# Orchestrator Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement four missing spec features: dispatch sorting, reconciliation Part B (terminate terminal workers), continuation retry, and WORKFLOW.md hot reload.

**Architecture:** All changes are confined to `src/symphony/orchestrator.py` and `src/symphony/workflow.py`. No changes to tracker, workspace, agent, or CLI layers. Features are independent and can be implemented in order: sorting (no dependencies) → reconciliation (needs `_tasks` population) → continuation retry (needs `_dispatch` result path) → hot reload (new class in `workflow.py`).

**Tech Stack:** Python 3.11+, asyncio, watchdog (already in dependencies), pytest-asyncio

**Spec reference:** `docs/superpowers/specs/2026-03-23-orchestrator-completeness-design.md`

---

## File Map

| File | Change |
|------|--------|
| `src/symphony/orchestrator.py` | Add `_sort_candidates()`, `_reconcile()`, `_apply_reload()`; modify `_dispatch()`, `_tick()`, `_poll_and_dispatch()`, `_schedule_retry()`, `start()`, `stop()` |
| `src/symphony/workflow.py` | Add `WorkflowWatcher` class |
| `tests/test_orchestrator.py` | Add 6 new tests |
| `tests/test_workflow.py` | Add 2 new tests (watcher) |

---

## Task 1: Dispatch Sorting

**Files:**
- Modify: `src/symphony/orchestrator.py`
- Test: `tests/test_orchestrator.py`

### 1.1 — Write failing tests for `_sort_candidates`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_orchestrator.py`:

```python
from datetime import datetime, UTC, timedelta
from symphony.orchestrator import _sort_candidates


def _make_issue_for_sort(
    id: str,
    priority: int | None = None,
    created_at: datetime | None = None,
    identifier: str | None = None,
) -> "Issue":
    from symphony.models import Issue
    return Issue(
        id=id,
        identifier=identifier or f"owner/repo#{id}",
        number=int(id),
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/shanyuqiang/github/OpenSymphony
python -m pytest tests/test_orchestrator.py::test_sort_candidates_by_priority -v 2>&1 | tail -5
```

Expected: `ImportError` or `FAILED` — `_sort_candidates` does not exist yet.

### 1.2 — Implement `_sort_candidates`

- [ ] **Step 3: Add `_sort_candidates` to `orchestrator.py`**

Add as a module-level function (before the `Orchestrator` class), after the imports:

```python
def _sort_candidates(issues: list[Issue]) -> list[Issue]:
    """Sort issues per spec §8.2: priority asc (None last) → created_at asc → identifier."""
    return sorted(
        issues,
        key=lambda i: (
            i.priority is None,          # False(0) < True(1) → None sorts last
            i.priority if i.priority is not None else 0,
            i.created_at or datetime.min,
            i.identifier,
        ),
    )
```

- [ ] **Step 4: Call `_sort_candidates` in `_poll_and_dispatch`**

In `_poll_and_dispatch`, after the issue list is parsed, add the sort call. The current code loops over `raw_issues` and parses each one inline. Refactor the loop to first collect parsed issues, then sort:

Replace the loop body in `_poll_and_dispatch`:

```python
async def _poll_and_dispatch(self) -> None:
    try:
        raw_issues = await self.tracker.fetch_candidate_issues()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch issues: %s", exc)
        return

    # Parse and sort candidates per spec §8.2
    candidates: list[Issue] = []
    for raw in raw_issues:
        issue = self._parse_issue(raw)
        if issue is not None:
            candidates.append(issue)
    candidates = _sort_candidates(candidates)

    candidate_ids = {i.id for i in candidates}

    for issue in candidates:
        if issue.id in self._claimed:
            continue
        if not self.label_mgr.should_dispatch(issue):
            continue
        if issue.blocked_by:
            logger.debug("Issue %s is blocked, skipping", issue.identifier)
            continue

        self._claimed.add(issue.id)
        task = asyncio.create_task(self._dispatch(issue), name=f"agent-{issue.id}")
        self._tasks[issue.id] = task

    return candidate_ids
```

Note: `_poll_and_dispatch` now **returns** `candidate_ids` — this will be used by `_reconcile` in Task 2.

- [ ] **Step 5: Run sort tests**

```bash
python -m pytest tests/test_orchestrator.py::test_sort_candidates_by_priority tests/test_orchestrator.py::test_sort_candidates_none_priority_last tests/test_orchestrator.py::test_sort_candidates_created_at_tiebreak tests/test_orchestrator.py::test_sort_candidates_identifier_tiebreak -v
```

Expected: 4 PASSED.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
python -m pytest tests/test_orchestrator.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/symphony/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add dispatch sorting per spec §8.2

Sort candidates by priority asc (None last), created_at asc,
identifier asc before dispatch loop."
```

---

## Task 2: Reconciliation Part B

**Files:**
- Modify: `src/symphony/orchestrator.py`
- Test: `tests/test_orchestrator.py`

### 2.1 — Write failing tests for `_reconcile`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_reconcile_cancels_terminal_issue(tmp_path: Path):
    """Running issue absent from candidates gets its task cancelled."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    from symphony.models import Issue as I
    issue = I(**_make_raw_issue(1))

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
async def test_reconcile_keeps_active_issue(tmp_path: Path):
    """Running issue present in candidates is NOT cancelled."""
    config = _make_config(tmp_path)
    workflow = _make_workflow_mock()
    orch = Orchestrator(config, workflow)

    from symphony.models import Issue as I
    issue = I(**_make_raw_issue(1))

    done = asyncio.Event()
    task = asyncio.create_task(done.wait())
    orch._tasks[issue.id] = task
    orch._running[issue.id] = MagicMock()

    # candidate_ids includes issue 1 → still active
    await orch._reconcile(candidate_ids={issue.id})

    assert not task.cancelled()
    done.set()
    await task
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_orchestrator.py::test_reconcile_cancels_terminal_issue tests/test_orchestrator.py::test_reconcile_keeps_active_issue -v 2>&1 | tail -5
```

Expected: `AttributeError` — `_reconcile` does not exist.

### 2.2 — Implement `_reconcile` and wire it up

- [ ] **Step 3: Add `_reconcile` method to `Orchestrator`**

Add after `_poll_and_dispatch` in `orchestrator.py`:

```python
async def _reconcile(self, candidate_ids: set[str]) -> None:
    """Cancel tasks for running issues that are no longer candidates (gone terminal)."""
    for issue_id in list(self._running):
        if issue_id not in candidate_ids and issue_id in self._tasks:
            logger.info("Reconciling terminal issue %s — cancelling task", issue_id)
            self._tasks[issue_id].cancel()
```

- [ ] **Step 4: Update `_tick` to call `_reconcile` with candidate_ids**

`_poll_and_dispatch` now returns `candidate_ids`. Update `_tick`:

```python
async def _tick(self) -> None:
    """One iteration: process retries + poll + dispatch."""
    await self._process_retries()
    candidate_ids = await self._poll_and_dispatch()
    if candidate_ids is not None:
        await self._reconcile(candidate_ids)
```

- [ ] **Step 5: Add `CancelledError` handling to `_dispatch`**

In `_dispatch`, wrap the agent run section to catch `CancelledError`. Find the `try: result = await self.agent_runner.run(...)` block and add:

```python
            try:
                result = await self.agent_runner.run(issue, workspace, attempt=attempt)
            except asyncio.CancelledError:
                logger.info("Agent task cancelled for %s", issue.identifier)
                await self.label_mgr.on_completion_detected(issue)
                self._claimed.discard(issue.id)
                self._tasks.pop(issue.id, None)
                del self._running[issue.id]
                self._notify()
                raise
            except Exception as exc:  # noqa: BLE001
                ...
```

Also add `self._tasks.pop(issue.id, None)` at both the normal-exit cleanup points (success and failure paths). Find the existing cleanup at the end of `_dispatch` and add it alongside `self._claimed.discard(issue.id)`:

In the success path (after `on_completion_detected`):
```python
                self._tasks.pop(issue.id, None)
                self._claimed.discard(issue.id)
```

In the failure path (after `_schedule_retry` or final `discard`):
```python
                self._tasks.pop(issue.id, None)
                # (claimed retained for retry, or discarded if agent succeeded without done)
```

**Full updated `_dispatch` for reference** (complete replacement — copy this exactly):

```python
async def _dispatch(self, issue: Issue) -> None:
    """Acquire semaphore, create workspace, run agent, handle result."""
    async with self._semaphore:
        if self._stop_event.is_set():
            self._claimed.discard(issue.id)
            self._tasks.pop(issue.id, None)
            return

        # Mark as doing
        added = await self.label_mgr.on_dispatch(issue)
        if not added:
            logger.warning(
                "Failed to add symphony-doing to %s, skipping",
                issue.identifier,
            )
            self._claimed.discard(issue.id)
            self._tasks.pop(issue.id, None)
            return

        try:
            workspace = await self.workspace_mgr.create_for_issue(issue)
        except Exception as exc:  # noqa: BLE001
            logger.error("Workspace creation failed for %s: %s", issue.identifier, exc)
            await self.label_mgr.on_completion_detected(issue)
            self._claimed.discard(issue.id)
            self._tasks.pop(issue.id, None)
            return

        attempt = self._get_attempt(issue.id)
        entry = RunningEntry(
            issue=issue,
            workspace_path=workspace.path,
            started_at=datetime.now(UTC),
            retry_attempt=attempt,
        )
        self._running[issue.id] = entry
        self._notify()

        logger.info(
            "Dispatching agent",
            extra={
                "identifier": issue.identifier,
                "attempt": attempt,
                "workspace": str(workspace.path),
            },
        )

        try:
            result = await self.agent_runner.run(issue, workspace, attempt=attempt)
        except asyncio.CancelledError:
            logger.info("Agent task cancelled for %s", issue.identifier)
            # Note: on_completion_detected is a network call; a second cancellation
            # during it would propagate naturally. This is acceptable — the worst
            # outcome is that symphony-doing is not removed, and startup cleanup
            # will handle it on next restart.
            await self.label_mgr.on_completion_detected(issue)
            self._claimed.discard(issue.id)
            self._tasks.pop(issue.id, None)
            del self._running[issue.id]
            self._notify()
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Agent run raised exception for %s: %s", issue.identifier, exc
            )
            result_success = False
            result_error = str(exc)
            result_tokens = TokenCounts()
        else:
            result_success = result.success
            result_error = result.error
            result_tokens = result.token_usage

        del self._running[issue.id]
        self._notify()

        # Refresh issue to check labels
        refreshed = await self._refresh_issue(issue)
        if refreshed and self.label_mgr.is_completed(refreshed):
            await self.label_mgr.on_completion_detected(refreshed)
            logger.info(
                "Issue %s completed by agent (tokens: %s)",
                issue.identifier,
                result_tokens.total_tokens,
            )
            self._tasks.pop(issue.id, None)
            self._claimed.discard(issue.id)
            return

        # Not marked done
        await self.label_mgr.on_completion_detected(issue)

        if not result_success:
            error_msg = result_error or "Agent did not mark issue as done"
            logger.warning(
                "Agent run failed for %s (attempt %d): %s",
                issue.identifier,
                attempt,
                error_msg,
            )
            self._tasks.pop(issue.id, None)
            self._schedule_retry(issue, attempt + 1, error_msg)
        else:
            # Agent succeeded but did not mark done — schedule continuation retry
            self._tasks.pop(issue.id, None)
            self._schedule_retry(issue, attempt=0, error=None)
```

Note: the last `else` branch is the **continuation retry** from Task 3 — implement it here already.

- [ ] **Step 6: Update `_process_retries` to store task handle**

Find `asyncio.create_task(self._dispatch(issue), name=f"agent-retry-{issue.id}")` and update it:

```python
            task = asyncio.create_task(
                self._dispatch(issue), name=f"agent-retry-{issue.id}"
            )
            self._tasks[issue.id] = task
```

- [ ] **Step 7: Run reconciliation tests**

```bash
python -m pytest tests/test_orchestrator.py::test_reconcile_cancels_terminal_issue tests/test_orchestrator.py::test_reconcile_keeps_active_issue -v
```

Expected: 2 PASSED.

- [ ] **Step 8: Run full orchestrator test suite**

```bash
python -m pytest tests/test_orchestrator.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/symphony/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add reconciliation Part B per spec §8.5

Cancels asyncio tasks for running issues that disappear from
candidate list (gone terminal). Stores task handles in _tasks.
Handles CancelledError in _dispatch for clean label cleanup."
```

---

## Task 3: Continuation Retry

**Files:**
- Modify: `src/symphony/orchestrator.py`
- Test: `tests/test_orchestrator.py`

> **Note:** The `_dispatch` change (success-without-done → `_schedule_retry(attempt=0)`) was already included in Task 2's full `_dispatch` replacement. This task only needs to update `_schedule_retry` and add the test.

### 3.1 — Write failing test for continuation retry

- [ ] **Step 1: Write failing test**

Add to `tests/test_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_continuation_retry_on_clean_exit(tmp_path: Path):
    """Clean exit without symphony-done schedules a 1s continuation retry (attempt=0)."""
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

    # Agent exits cleanly but issue has NO symphony-done label
    raw_refreshed = _make_raw_issue(1, labels=["symphony-doing"])
    refreshed_issue = I(**raw_refreshed)
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
    # Backoff must be ~1000ms (not exponential)
    assert orch._retry_queue[0].due_at_ms <= time.monotonic() * 1000 + 1100
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_orchestrator.py::test_continuation_retry_on_clean_exit -v 2>&1 | tail -10
```

Expected: FAILED — currently the `else` branch calls `_claimed.discard` without scheduling a retry.

### 3.2 — Update `_schedule_retry` for continuation path

- [ ] **Step 3: Update `_schedule_retry` for attempt=0**

Find the `_schedule_retry` method. Add the continuation path at the top:

```python
def _schedule_retry(self, issue: Issue, attempt: int, error: str | None) -> None:
    if attempt == 0:
        # Continuation retry: fixed 1s delay (spec §7.3, §8.4)
        backoff_ms = 1000
    else:
        # §8.4: delay = min(10000 * 2^(attempt-1), max_retry_backoff_ms)
        backoff_ms = min(
            10000 * (2 ** (attempt - 1)),
            self.config.agent.max_retry_backoff_ms,
        )
    due_at_ms = time.monotonic() * 1000 + backoff_ms
    logger.info(
        "Scheduling retry for %s in %.0f s (attempt %d)",
        issue.identifier,
        backoff_ms / 1000,
        attempt,
    )
    self._retry_queue = [e for e in self._retry_queue if e.issue_id != issue.id]
    self._retry_queue.append(
        RetryEntry(
            issue_id=issue.id,
            identifier=issue.identifier,
            attempt=attempt,
            due_at_ms=due_at_ms,
            error=error,
        )
    )
    self._retry_queue.sort(key=lambda e: e.due_at_ms)
    self._notify()
```

Also update the method signature to `error: str | None` (was `error: str`).

- [ ] **Step 4: Fix callers of `_schedule_retry`**

In `_dispatch` (already done in Task 2 full replacement — verify):
- Failure path: `self._schedule_retry(issue, attempt + 1, error_msg)` ✓
- Success-without-done path: `self._schedule_retry(issue, attempt=0, error=None)` ✓

In `test_orchestrator.py`, `test_retry_scheduling` calls `orch._schedule_retry(issue, attempt=1, error="timeout")` — this still works as-is.

- [ ] **Step 5: Run continuation retry test**

```bash
python -m pytest tests/test_orchestrator.py::test_continuation_retry_on_clean_exit -v
```

Expected: PASSED.

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/symphony/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add continuation retry per spec §7.3

Clean exit without symphony-done schedules 1s retry (attempt=0)
instead of silently releasing the claim. Failure retries unchanged."
```

---

## Task 4: WORKFLOW.md Hot Reload

**Files:**
- Modify: `src/symphony/workflow.py` — add `WorkflowWatcher` class
- Modify: `src/symphony/orchestrator.py` — add `_apply_reload()`, update `start()` / `stop()`
- Test: `tests/test_workflow.py` (new file)

### 4.1 — Write failing tests for `WorkflowWatcher`

- [ ] **Step 1: Create `tests/test_workflow.py` with failing tests**

```python
"""Tests for WorkflowWatcher hot reload."""
from __future__ import annotations

import asyncio
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from symphony.workflow import WorkflowLoader, WorkflowWatcher


def _make_workflow_file(tmp_path: Path, interval_ms: int = 5000) -> Path:
    content = textwrap.dedent(f"""\
        ---
        tracker:
          kind: gitea
          endpoint: http://localhost:3000/api/v1
          api_key: token
          owner: owner
          repo: repo
        polling:
          interval_ms: {interval_ms}
        ---
        Working on {{{{ issue.identifier }}}}
    """)
    wf_file = tmp_path / "WORKFLOW.md"
    wf_file.write_text(content)
    return wf_file


def test_workflow_watcher_reloads_config(tmp_path: Path):
    """File change triggers the on_reload callback with a new Workflow."""
    wf_file = _make_workflow_file(tmp_path, interval_ms=5000)

    reloaded = []
    loader = WorkflowLoader()
    watcher = WorkflowWatcher(path=wf_file, on_reload=reloaded.append, loader=loader)
    watcher.start()

    try:
        # Write new content with different interval
        new_content = textwrap.dedent("""\
            ---
            tracker:
              kind: gitea
              endpoint: http://localhost:3000/api/v1
              api_key: token
              owner: owner
              repo: repo
            polling:
              interval_ms: 9999
            ---
            Updated prompt {{ issue.identifier }}
        """)
        wf_file.write_text(new_content)

        # Wait for watchdog to fire (up to 3s)
        deadline = time.monotonic() + 3.0
        while not reloaded and time.monotonic() < deadline:
            time.sleep(0.1)

        assert len(reloaded) >= 1
        assert reloaded[-1].config.polling.interval_ms == 9999
    finally:
        watcher.stop()


def test_workflow_watcher_bad_yaml_no_crash(tmp_path: Path):
    """Invalid YAML reload does not call on_reload and does not crash."""
    wf_file = _make_workflow_file(tmp_path)

    reloaded = []
    loader = WorkflowLoader()
    watcher = WorkflowWatcher(path=wf_file, on_reload=reloaded.append, loader=loader)
    watcher.start()

    try:
        # Write invalid YAML
        wf_file.write_text("---\n: broken: yaml:\n---\nPrompt")

        # Wait briefly — no reload should fire
        time.sleep(0.5)
        assert len(reloaded) == 0
    finally:
        watcher.stop()
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_workflow.py -v 2>&1 | tail -5
```

Expected: `ImportError` — `WorkflowWatcher` does not exist.

### 4.2 — Implement `WorkflowWatcher`

- [ ] **Step 3: Add `WorkflowWatcher` to `workflow.py`**

Add at the end of `workflow.py`:

```python
import logging
import threading
from collections.abc import Callable

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

_watcher_logger = logging.getLogger(__name__)


class _ReloadHandler(FileSystemEventHandler):
    """Watchdog handler that reloads WORKFLOW.md on modification."""

    def __init__(self, path: Path, on_reload: Callable[["Workflow"], None], loader: "WorkflowLoader") -> None:
        super().__init__()
        self._path = path.resolve()
        self._on_reload = on_reload
        self._loader = loader

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if Path(event.src_path).resolve() != self._path:
            return
        try:
            workflow = self._loader.load(self._path)
            self._on_reload(workflow)
            _watcher_logger.info("WORKFLOW.md reloaded from %s", self._path)
        except Exception as exc:  # noqa: BLE001
            _watcher_logger.warning("WORKFLOW.md reload failed (keeping old config): %s", exc)


class WorkflowWatcher:
    """Watches WORKFLOW.md for changes and calls on_reload with the new Workflow."""

    def __init__(
        self,
        path: Path,
        on_reload: Callable["Workflow", None],
        loader: WorkflowLoader | None = None,
    ) -> None:
        self._path = path.resolve()
        self._on_reload = on_reload
        self._loader = loader or WorkflowLoader()
        self._observer: Observer | None = None

    def start(self) -> None:
        handler = _ReloadHandler(self._path, self._on_reload, self._loader)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._path.parent), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
```

- [ ] **Step 4: Run watcher tests**

```bash
python -m pytest tests/test_workflow.py -v
```

Expected: 2 PASSED (may be slightly slow due to watchdog polling interval).

### 4.3 — Wire hot reload into `Orchestrator`

- [ ] **Step 5: Write failing orchestrator test for hot reload**

Add to `tests/test_orchestrator.py`:

```python
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
```

- [ ] **Step 6: Run to verify it fails**

```bash
python -m pytest tests/test_orchestrator.py::test_apply_reload_updates_config -v 2>&1 | tail -5
```

Expected: `AttributeError` — `_apply_reload` does not exist.

- [ ] **Step 7: Add `_apply_reload` to `Orchestrator`**

Add after the `_notify` method:

```python
def _apply_reload(self, new_workflow: "Workflow") -> None:
    """Atomically apply a reloaded WORKFLOW.md. Called from watchdog thread via call_soon_threadsafe."""
    old_concurrency = self.config.agent.max_concurrent_agents
    new_concurrency = new_workflow.config.agent.max_concurrent_agents

    # Update live config fields (tracker credentials excluded — require restart)
    self.config.polling.interval_ms = new_workflow.config.polling.interval_ms
    self.config.agent.max_concurrent_agents = new_concurrency
    self.config.hooks = new_workflow.config.hooks
    self.config.claude = new_workflow.config.claude

    # Update prompt template
    self.workflow = new_workflow

    # Replace semaphore if concurrency changed
    if new_concurrency != old_concurrency:
        self._semaphore = asyncio.Semaphore(new_concurrency)

    logger.info(
        "WORKFLOW.md hot-reloaded: interval=%dms concurrency=%d",
        self.config.polling.interval_ms,
        new_concurrency,
    )
```

- [ ] **Step 8: Add `WorkflowWatcher` to `Orchestrator.__init__`, `start`, and `stop`**

In `orchestrator.py`, add import at top:

```python
from symphony.workflow import WorkflowWatcher
```

In `__init__`, add after `self._stop_event`:

```python
        self._watcher: WorkflowWatcher | None = None
        if workflow.path:
            loop = None  # will be resolved at start() time
            self._workflow_path = workflow.path
        else:
            self._workflow_path = None
```

Update `start()`:

```python
    async def start(self) -> None:
        """Start the orchestrator.  Blocks until stop() is called."""
        logger.info(
            "Symphony orchestrator starting",
            extra={
                "owner": self.config.tracker.owner,
                "repo": self.config.tracker.repo,
                "max_concurrent": self.config.agent.max_concurrent_agents,
            },
        )
        # Start hot reload watcher
        if self._workflow_path:
            loop = asyncio.get_event_loop()

            def _threadsafe_reload(new_workflow: "Workflow") -> None:
                loop.call_soon_threadsafe(self._apply_reload, new_workflow)

            self._watcher = WorkflowWatcher(
                path=self._workflow_path,
                on_reload=_threadsafe_reload,
            )
            self._watcher.start()

        await self._startup_cleanup()
        await self._run_loop()
```

Update `stop()`:

```python
    def stop(self) -> None:
        """Signal the orchestrator to stop."""
        logger.info("Stop requested")
        self._stop_event.set()
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
```

- [ ] **Step 9: Run hot reload test**

```bash
python -m pytest tests/test_orchestrator.py::test_apply_reload_updates_config -v
```

Expected: PASSED.

- [ ] **Step 10: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git add src/symphony/orchestrator.py src/symphony/workflow.py tests/test_orchestrator.py tests/test_workflow.py
git commit -m "feat: add WORKFLOW.md hot reload per spec §6.2

WorkflowWatcher uses watchdog to detect file changes and atomically
updates polling interval, concurrency, prompt template, and hooks.
Tracker credentials require restart. Parse errors log warning only."
```

---

## Final Verification

- [ ] **Run full test suite one last time**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: all tests pass, no regressions.

- [ ] **Check test count**

Current: ~18 tests. After this plan: 18 + 4 (sort) + 2 (reconcile) + 1 (continuation) + 2 (watcher) + 1 (apply_reload) = **28 tests**.
