"""Tests for the Orchestrator."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, UTC
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
from symphony.orchestrator import Orchestrator


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
