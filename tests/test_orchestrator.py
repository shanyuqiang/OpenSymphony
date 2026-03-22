"""Tests for the Orchestrator."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, UTC, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from symphony.config import (
    AgentConfig,
    ClaudeConfig,
    HooksConfig,
    PollingConfig,
    TrackerConfig,
    WorkflowConfig,
    WorkspaceConfig,
)
from symphony.models import Issue, RetryEntry, RunningEntry, TokenCounts, Workspace, ClaudeResult
from symphony.orchestrator import Orchestrator, _sort_candidates


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> WorkflowConfig:
    return WorkflowConfig(
        tracker=TrackerConfig(
            kind="gitea",
            endpoint="http://localhost:3000/api/v1",
            api_key="token",
            owner="owner",
            repo="repo",
        ),
        polling=PollingConfig(interval_ms=100),
        workspace=WorkspaceConfig(root=str(tmp_path / "ws")),
        agent=AgentConfig(max_concurrent_agents=2, max_turns=3),
        claude=ClaudeConfig(),
    )


def _make_raw_issue(number: int = 1, labels: list[str] | None = None) -> dict:
    return {
        "id": str(number),
        "identifier": f"owner/repo#{number}",
        "number": number,
        "title": f"Issue {number}",
        "description": "desc",
        "state": "open",
        "labels": labels or [],
        "blocked_by": [],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "owner": "owner",
        "repo": "repo",
    }


def _make_workflow_mock():
    wf = MagicMock()
    wf.render_prompt.return_value = "Do the thing for issue #1"
    return wf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_start_stop(tmp_path: Path):
    """Orchestrator should stop cleanly when stop() is called."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    with (
        patch.object(orch.tracker, "fetch_candidate_issues", new_callable=AsyncMock, return_value=[]),
        patch.object(orch.tracker, "fetch_issues_by_states", new_callable=AsyncMock, return_value=[]),
    ):
        task = asyncio.create_task(orch.start())
        await asyncio.sleep(0.05)
        orch.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_label_lifecycle_should_dispatch(tmp_path: Path):
    """Issues with symphony-doing or symphony-done should not be dispatched."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    raw_doing = _make_raw_issue(1, labels=["symphony-doing"])
    raw_done = _make_raw_issue(2, labels=["symphony-done"])
    raw_ok = _make_raw_issue(3, labels=[])

    from symphony.models import Issue as I
    doing_issue = I(**raw_doing)
    done_issue = I(**raw_done)
    ok_issue = I(**raw_ok)

    assert orch.label_mgr.should_dispatch(doing_issue) is False
    assert orch.label_mgr.should_dispatch(done_issue) is False
    assert orch.label_mgr.should_dispatch(ok_issue) is True


@pytest.mark.asyncio
async def test_retry_scheduling(tmp_path: Path):
    """Failed agents should be added to the retry queue with backoff."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    from symphony.models import Issue as I
    issue = I(**_make_raw_issue(1))

    orch._schedule_retry(issue, attempt=1, error="timeout")

    assert len(orch._retry_queue) == 1
    entry = orch._retry_queue[0]
    assert entry.issue_id == "1"
    assert entry.attempt == 1
    assert entry.error == "timeout"
    # Backoff should be > 0 from now
    assert entry.due_at_ms > time.monotonic() * 1000


@pytest.mark.asyncio
async def test_dispatch_success_marks_done(tmp_path: Path):
    """When agent adds symphony-done, orchestrator removes symphony-doing."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    raw = _make_raw_issue(1, labels=[])
    from symphony.models import Issue as I
    issue = I(**raw)
    workspace = Workspace(
        path=tmp_path / "ws" / "owner_repo_1",
        workspace_key="owner_repo_1",
        created_now=True,
    )
    (tmp_path / "ws" / "owner_repo_1").mkdir(parents=True, exist_ok=True)

    # After run, issue has symphony-done label
    raw_refreshed = _make_raw_issue(1, labels=["symphony-doing", "symphony-done"])
    refreshed_issue = I(**raw_refreshed)

    mock_result = ClaudeResult(success=True)

    remove_mock = AsyncMock(return_value=True)
    with (
        patch.object(orch.tracker, "add_label", new_callable=AsyncMock, return_value=True),
        patch.object(orch.tracker, "remove_label", remove_mock),
        patch.object(orch.workspace_mgr, "create_for_issue", new_callable=AsyncMock, return_value=workspace),
        patch.object(orch.agent_runner, "run", new_callable=AsyncMock, return_value=mock_result),
        patch.object(orch, "_refresh_issue", new_callable=AsyncMock, return_value=refreshed_issue),
    ):
        await orch._dispatch(issue)

    # remove_label should have been called (on_completion_detected)
    remove_mock.assert_called_once_with(issue.number, "symphony-doing")
    # No retry should be scheduled
    assert len(orch._retry_queue) == 0


@pytest.mark.asyncio
async def test_dispatch_failure_schedules_retry(tmp_path: Path):
    """When agent fails, issue should be added to retry queue."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    raw = _make_raw_issue(1, labels=[])
    from symphony.models import Issue as I
    issue = I(**raw)
    workspace = Workspace(
        path=tmp_path / "ws" / "owner_repo_1",
        workspace_key="owner_repo_1",
        created_now=True,
    )
    (tmp_path / "ws" / "owner_repo_1").mkdir(parents=True, exist_ok=True)

    raw_refreshed = _make_raw_issue(1, labels=["symphony-doing"])
    refreshed_issue = I(**raw_refreshed)

    mock_result = ClaudeResult(success=False, error="stall timeout")

    with (
        patch.object(orch.tracker, "add_label", new_callable=AsyncMock, return_value=True),
        patch.object(orch.tracker, "remove_label", new_callable=AsyncMock, return_value=True),
        patch.object(orch.workspace_mgr, "create_for_issue", new_callable=AsyncMock, return_value=workspace),
        patch.object(orch.agent_runner, "run", new_callable=AsyncMock, return_value=mock_result),
        patch.object(orch, "_refresh_issue", new_callable=AsyncMock, return_value=refreshed_issue),
    ):
        await orch._dispatch(issue)

    assert len(orch._retry_queue) == 1
    assert orch._retry_queue[0].issue_id == "1"


@pytest.mark.asyncio
async def test_claimed_set_prevents_double_dispatch(tmp_path: Path):
    """After create_task, issue should be in _claimed and not re-dispatched."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    from symphony.models import Issue as I
    issue = I(**_make_raw_issue(1, labels=[]))

    # Manually claim the issue as if create_task was already called
    orch._claimed.add(issue.id)

    dispatched: list = []

    async def _fake_dispatch(issue):
        dispatched.append(issue.id)

    raw_issues = [_make_raw_issue(1, labels=[])]

    with patch.object(orch.tracker, "fetch_candidate_issues", new_callable=AsyncMock, return_value=raw_issues):
        with patch.object(orch, "_dispatch", side_effect=_fake_dispatch):
            await orch._poll_and_dispatch()

    # Issue is claimed, so _dispatch must NOT be called again
    assert dispatched == []


@pytest.mark.asyncio
async def test_claimed_released_on_success(tmp_path: Path):
    """After a successful run, _claimed must be cleared."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    from symphony.models import Issue as I
    issue = I(**_make_raw_issue(1, labels=[]))
    workspace = Workspace(
        path=tmp_path / "ws" / "owner_repo_1",
        workspace_key="owner_repo_1",
        created_now=True,
    )
    (tmp_path / "ws" / "owner_repo_1").mkdir(parents=True, exist_ok=True)

    raw_refreshed = _make_raw_issue(1, labels=["symphony-doing", "symphony-done"])
    refreshed_issue = I(**raw_refreshed)
    mock_result = ClaudeResult(success=True)

    orch._claimed.add(issue.id)  # simulates having been claimed before create_task

    with (
        patch.object(orch.tracker, "add_label", new_callable=AsyncMock, return_value=True),
        patch.object(orch.tracker, "remove_label", AsyncMock(return_value=True)),
        patch.object(orch.workspace_mgr, "create_for_issue", new_callable=AsyncMock, return_value=workspace),
        patch.object(orch.agent_runner, "run", new_callable=AsyncMock, return_value=mock_result),
        patch.object(orch, "_refresh_issue", new_callable=AsyncMock, return_value=refreshed_issue),
    ):
        await orch._dispatch(issue)

    # Claim must be released after successful completion
    assert issue.id not in orch._claimed


@pytest.mark.asyncio
async def test_claimed_retained_in_retry_queue(tmp_path: Path):
    """When a retry is scheduled, _claimed must stay set."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    from symphony.models import Issue as I
    issue = I(**_make_raw_issue(1, labels=[]))
    workspace = Workspace(
        path=tmp_path / "ws" / "owner_repo_1",
        workspace_key="owner_repo_1",
        created_now=True,
    )
    (tmp_path / "ws" / "owner_repo_1").mkdir(parents=True, exist_ok=True)

    raw_refreshed = _make_raw_issue(1, labels=["symphony-doing"])
    refreshed_issue = I(**raw_refreshed)
    mock_result = ClaudeResult(success=False, error="timeout")

    orch._claimed.add(issue.id)

    with (
        patch.object(orch.tracker, "add_label", new_callable=AsyncMock, return_value=True),
        patch.object(orch.tracker, "remove_label", AsyncMock(return_value=True)),
        patch.object(orch.workspace_mgr, "create_for_issue", new_callable=AsyncMock, return_value=workspace),
        patch.object(orch.agent_runner, "run", new_callable=AsyncMock, return_value=mock_result),
        patch.object(orch, "_refresh_issue", new_callable=AsyncMock, return_value=refreshed_issue),
    ):
        await orch._dispatch(issue)

    # Issue is queued for retry — claim must remain
    assert issue.id in orch._claimed
    assert len(orch._retry_queue) == 1


def test_retry_backoff_formula(tmp_path: Path):
    """§8.4: backoff = min(10000 * 2^(attempt-1), max_retry_backoff_ms)."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    from symphony.models import Issue as I
    issue = I(**_make_raw_issue(1))

    # attempt=1: 10000 * 2^0 = 10000 ms
    orch._schedule_retry(issue, attempt=1, error="e")
    assert orch._retry_queue[-1].due_at_ms > time.monotonic() * 1000 + 9000

    orch._retry_queue.clear()

    # attempt=2: 10000 * 2^1 = 20000 ms
    orch._schedule_retry(issue, attempt=2, error="e")
    assert orch._retry_queue[-1].due_at_ms > time.monotonic() * 1000 + 19000

    orch._retry_queue.clear()

    # attempt=100 (very large): capped at max_retry_backoff_ms (300000 ms default)
    orch._schedule_retry(issue, attempt=100, error="e")
    assert orch._retry_queue[-1].due_at_ms <= time.monotonic() * 1000 + 300001


# ---------------------------------------------------------------------------
# _sort_candidates tests
# ---------------------------------------------------------------------------


def _make_issue_for_sort(
    id: str,
    priority: int | None = None,
    created_at: datetime | None = None,
    identifier: str | None = None,
) -> Issue:
    return Issue(
        id=id,
        identifier=identifier or f"owner/repo#{id}",
        number=int(id) if id.isdigit() else 0,
        title=f"Issue {id}",
        description="desc",
        state="open",
        labels=[],
        priority=priority,
        created_at=created_at or datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner="owner",
        repo="repo",
    )


def test_sort_candidates_by_priority():
    """Lower priority number = higher urgency = dispatched first."""
    now = datetime.now(UTC)
    issues = [
        _make_issue_for_sort("3", priority=3, created_at=now),
        _make_issue_for_sort("1", priority=1, created_at=now),
        _make_issue_for_sort("2", priority=2, created_at=now),
    ]
    result = _sort_candidates(issues)
    assert [i.id for i in result] == ["1", "2", "3"]


def test_sort_candidates_none_priority_last():
    """None priority sorts after all numeric priorities."""
    now = datetime.now(UTC)
    issues = [
        _make_issue_for_sort("none", priority=None, created_at=now),
        _make_issue_for_sort("p1", priority=1, created_at=now),
    ]
    result = _sort_candidates(issues)
    assert result[0].id == "p1"
    assert result[1].id == "none"


def test_sort_candidates_created_at_tiebreak():
    """Same priority: older issue dispatched first."""
    now = datetime.now(UTC)
    issues = [
        _make_issue_for_sort("newer", priority=1, created_at=now),
        _make_issue_for_sort("older", priority=1, created_at=now - timedelta(hours=1)),
    ]
    result = _sort_candidates(issues)
    assert result[0].id == "older"


def test_sort_candidates_identifier_tiebreak():
    """Same priority + same created_at: lexicographic identifier order."""
    now = datetime.now(UTC)
    issues = [
        _make_issue_for_sort("2", priority=1, created_at=now, identifier="owner/repo#z"),
        _make_issue_for_sort("1", priority=1, created_at=now, identifier="owner/repo#a"),
    ]
    result = _sort_candidates(issues)
    assert result[0].identifier == "owner/repo#a"


# ---------------------------------------------------------------------------
# _reconcile tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_cancels_terminal_issue(tmp_path: Path):
    """Running issue absent from candidates gets its task cancelled."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    issue = Issue(**_make_raw_issue(1))

    # Simulate a running task
    cancelled = []

    async def _long_running():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(issue.id)
            raise

    task = asyncio.create_task(_long_running())
    orch._tasks[issue.id] = task
    orch._running[issue.id] = MagicMock()

    # candidate_ids does NOT include issue 1 → it went terminal
    await orch._reconcile(candidate_ids=set())

    # Give the event loop a tick to propagate cancellation
    await asyncio.sleep(0)

    assert issue.id in cancelled


@pytest.mark.asyncio
async def test_continuation_retry_on_clean_exit(tmp_path: Path):
    """Clean exit without symphony-done schedules a 1s continuation retry (attempt=0)."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    issue = Issue(**_make_raw_issue(1, labels=[]))
    workspace = Workspace(
        path=tmp_path / "ws" / "owner_repo_1",
        workspace_key="owner_repo_1",
        created_now=True,
    )
    (tmp_path / "ws" / "owner_repo_1").mkdir(parents=True, exist_ok=True)

    # Simulate having been claimed before _dispatch is called (as _poll_and_dispatch does)
    orch._claimed.add(issue.id)

    # Agent exits cleanly but issue has NO symphony-done label
    raw_refreshed = _make_raw_issue(1, labels=["symphony-doing"])
    refreshed_issue = Issue(**raw_refreshed)
    mock_result = ClaudeResult(success=True)

    with (
        patch.object(orch.tracker, "add_label", new_callable=AsyncMock, return_value=True),
        patch.object(orch.tracker, "remove_label", new_callable=AsyncMock, return_value=True),
        patch.object(orch.workspace_mgr, "create_for_issue", new_callable=AsyncMock, return_value=workspace),
        patch.object(orch.agent_runner, "run", new_callable=AsyncMock, return_value=mock_result),
        patch.object(orch, "_refresh_issue", new_callable=AsyncMock, return_value=refreshed_issue),
    ):
        await orch._dispatch(issue)

    # Claim must be retained (issue is in retry queue)
    assert issue.id in orch._claimed
    # Retry must be scheduled with attempt=0
    assert len(orch._retry_queue) == 1
    assert orch._retry_queue[0].attempt == 0
    # Backoff must be ~1000ms (not exponential) — verify both bounds
    now_ms = time.monotonic() * 1000
    assert orch._retry_queue[0].due_at_ms >= now_ms + 900
    assert orch._retry_queue[0].due_at_ms <= now_ms + 1100


@pytest.mark.asyncio
async def test_reconcile_keeps_active_issue(tmp_path: Path):
    """Running issue present in candidates is NOT cancelled."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    issue = Issue(**_make_raw_issue(1))

    done = asyncio.Event()
    task = asyncio.create_task(done.wait())
    orch._tasks[issue.id] = task
    orch._running[issue.id] = MagicMock()

    # candidate_ids includes issue 1 → still active
    await orch._reconcile(candidate_ids={issue.id})

    assert not task.cancelled()
    done.set()
    await task


@pytest.mark.asyncio
async def test_apply_reload_updates_config(tmp_path: Path):
    """_apply_reload updates polling interval, prompt_template, and semaphore."""
    import textwrap
    from symphony.workflow import WorkflowLoader

    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    original_semaphore = orch._semaphore

    # Build a new Workflow with different interval and concurrency
    wf_content = textwrap.dedent("""\
        ---
        tracker:
          kind: gitea
          endpoint: http://localhost:3000/api/v1
          api_key: token
          owner: owner
          repo: repo
        polling:
          interval_ms: 7777
        agent:
          max_concurrent_agents: 5
        ---
        New prompt {{ issue.identifier }}
    """)
    wf_file = tmp_path / "WORKFLOW.md"
    wf_file.write_text(wf_content)
    loader = WorkflowLoader()
    new_workflow = loader.load(wf_file)

    orch._apply_reload(new_workflow)

    assert orch.config.polling.interval_ms == 7777
    assert orch.config.agent.max_concurrent_agents == 5
    assert orch._semaphore is not original_semaphore
    assert orch.workflow.prompt_template == "New prompt {{ issue.identifier }}"
