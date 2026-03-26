# Orchestrator Completeness — Design Spec

**Date:** 2026-03-23
**Status:** Approved
**Scope:** `src/symphony/orchestrator.py`, `src/symphony/workflow.py`
**Reference:** [SPEC.md §6.2, §7.3, §8.1–8.5](https://github.com/openai/symphony/blob/main/SPEC.md), [cc-symphony Rust implementation](https://github.com/hawkymisc/cc-symphony)

---

## Background

Four features from the Symphony spec were identified as missing after reviewing the spec and cross-referencing the cc-symphony Rust reference implementation:

| Feature | Spec § | Impact |
|---------|--------|--------|
| Reconciliation Part B (terminate terminal workers) | §8.5 | Running agents never stopped when issues close |
| Continuation retry (1 s re-check after clean exit) | §7.3, §8.4 | max_turns-exhausted runs never continue |
| Dispatch sorting (priority → created_at → identifier) | §8.2 | Issues dispatched in arbitrary order |
| WORKFLOW.md hot reload | §6.2 | Config changes require restart |

---

## Feature 1: Reconciliation Part B

### Problem

When an issue is closed or moved to a terminal state while its agent is running, the current orchestrator never cancels the agent. It continues running until it finishes or times out.

### Design

**Approach:** Reuse the candidate list from the current tick (no extra API call). After fetching candidates, build a `candidate_ids` set and compare against `_running`. Any running issue absent from candidates has gone terminal → cancel its asyncio Task.

**Task tracking:** `self._tasks: dict[str, asyncio.Task]` is already declared but never populated. Store the task handle from `create_task` in this dict when dispatching.

**Cancellation flow:**
1. `_reconcile(candidate_ids: set[str])` called at the start of each `_tick()`
2. For each `issue_id` in `_running`: if not in `candidate_ids` → `self._tasks[issue_id].cancel()`
3. `_dispatch` wraps agent run in `try/except asyncio.CancelledError`: on cancellation, remove symphony-doing label and release `_claimed`
4. `run_claude` in `claude_cli.py` receives `CancelledError` propagation naturally (asyncio task cancellation propagates through `await`)

**Stall detection note:** Stall detection is already handled inside `run_claude` via `stall_timeout_ms`. No changes needed in reconciliation for stalls — the subprocess itself times out and returns a failed `ClaudeResult`, which triggers the existing retry path.

### Data flow

```
_tick()
  └─ _reconcile(candidate_ids)    ← new, from candidate fetch
       └─ for running not in candidates:
            task.cancel()
            _dispatch catches CancelledError
            removes symphony-doing
            _claimed.discard(issue_id)
  └─ _poll_and_dispatch(candidates)
```

### Changes

- `_poll_and_dispatch` → returns sorted `Issue` list; `_tick` passes `candidate_ids` to `_reconcile`
- New `_reconcile(candidate_ids: set[str])` method in `Orchestrator`
- `create_task(...)` result stored in `self._tasks[issue.id]`
- `_dispatch` removes `self._tasks[issue.id]` on both normal exit and `CancelledError` (prevents stale-key accumulation in graceful shutdown gather)

---

## Feature 2: Continuation Retry

### Problem

When the Claude CLI exits cleanly (max turns reached, agent decided it was done, etc.) but the issue has **not** been marked `symphony-done`, the current code treats it as a non-retryable success and releases the claim. The agent never gets another chance to continue.

### Design

**Approach:** Mirror the Rust reference implementation's `NORMAL_EXIT_ATTEMPT = 0` pattern.

**Rules (post-run decision):**

| Agent exit | Issue has symphony-done | Action |
|------------|------------------------|--------|
| Success | Yes | Remove symphony-doing, release claim — done |
| Success | No | Schedule 1 s continuation retry (keep claim) |
| Failure | — | Existing exponential backoff retry |
| Cancelled | — | Remove symphony-doing, release claim |

**Retry entry for continuation:** Uses `attempt = 0` (distinct from failure retries which use `attempt >= 1`). The `_process_retries` / `_schedule_retry` code needs a `is_continuation: bool` flag (or check `attempt == 0`) to skip the exponential formula and use a fixed 1000 ms delay.

**`_get_attempt` returns 0** for continuation entries, so the prompt renders without `attempt` context (first run semantics).

### Changes

- `_dispatch`: after clean exit without symphony-done, call `_schedule_retry(issue, attempt=0, error=None)` instead of releasing claim
- `_schedule_retry`: when `attempt == 0`, use fixed `backoff_ms = 1000` instead of the exponential formula
- Update `RetryEntry` to carry `error: str | None = None` (already present)
- Update `test_orchestrator.py` to test the continuation retry path

---

## Feature 3: Dispatch Sorting

### Problem

Issues are dispatched in the order they happen to arrive from the API response, which is undefined. The spec requires: priority ascending (None last) → `created_at` ascending → `identifier` lexicographic.

### Design

Add a sort step in `_poll_and_dispatch` before the dispatch loop.

```python
def _sort_candidates(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (
        i.priority is None,   # False (0) < True (1) → None sorts last
        i.priority or 0,
        i.created_at or datetime.min,
        i.identifier,
    ))
```

This function is pure and independently testable.

### Changes

- New `_sort_candidates(issues)` pure function in `orchestrator.py`
- Called in `_poll_and_dispatch` before the dispatch loop
- Tests: sort by priority, created_at tie-break, identifier tie-break, None priority last

---

## Feature 4: WORKFLOW.md Hot Reload

### Problem

Config changes (polling interval, concurrency, prompt template) require a full restart. The spec (§6.2) requires dynamic reload without restart.

### Design

**Implementation:** Use `watchdog` (already in dependencies) to watch `WORKFLOW.md`. On file-changed event, re-parse the file and atomically update the live config.

**What gets updated live:**
- `self.config.polling.interval_ms` → affects `_run_loop` on next sleep
- `self.config.agent.max_concurrent_agents` → `self._semaphore` is re-created
- `self.workflow.prompt_template` → affects future `render_prompt` calls
- `self.config.hooks` → affects future hook runs
- `self.config.claude` → affects future agent runs

**What does NOT change live:**
- `self.config.tracker` (endpoint/credentials) — requires restart; changing mid-run risks authentication inconsistencies
- `self._semaphore` — replaced atomically: if new limit is lower, existing acquired slots drain naturally; new limit is applied to next `create_task`

**Error handling:** If reload fails (YAML parse error, validation error), log a warning, keep existing config, and continue. Do not crash.

**Watchdog thread → asyncio bridge:** `watchdog` runs in a thread pool. Use `asyncio.get_event_loop().call_soon_threadsafe()` to schedule the reload coroutine on the event loop.

### New component: `WorkflowWatcher`

```python
class WorkflowWatcher:
    def __init__(self, path: Path, on_reload: Callable[[Workflow], None]) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

Lives in `workflow.py` alongside `WorkflowLoader`. The orchestrator calls `watcher.start()` after startup and `watcher.stop()` on shutdown.

### Changes

- New `WorkflowWatcher` class in `workflow.py`
- `Orchestrator._apply_reload(new_workflow: Workflow)` method: updates config fields and semaphore
- `Orchestrator.start()` starts watcher; `stop()` stops it
- Tests: mock watchdog event → verify config fields update; verify parse error does not crash

---

## Architecture Summary

All four features are confined to `orchestrator.py` and `workflow.py`. No changes to the tracker, workspace, agent, or CLI layers.

```
_tick()  (updated)
  ├─ _reconcile(candidate_ids)           ← NEW: cancels terminal workers
  └─ _poll_and_dispatch()
       ├─ fetch candidates
       ├─ _sort_candidates(issues)        ← NEW: priority/created_at order
       └─ dispatch loop (unchanged)

_dispatch()  (updated)
  ├─ [existing] run agent
  ├─ if success + symphony-done → done
  └─ if success + NOT done → _schedule_retry(attempt=0)   ← NEW: continuation

_schedule_retry()  (updated)
  ├─ attempt == 0 → backoff = 1000 ms   ← NEW: continuation path
  └─ attempt >= 1 → existing exponential formula

WorkflowWatcher  (new, in workflow.py)
  └─ watchdog observer → calls orchestrator._apply_reload()  ← NEW: hot reload
```

---

## Testing

| Test | What it verifies |
|------|-----------------|
| `test_reconcile_cancels_terminal_issue` | Running issue absent from candidates gets task.cancel() |
| `test_reconcile_keeps_active_issue` | Running issue present in candidates is not cancelled |
| `test_continuation_retry_on_clean_exit` | Clean exit without symphony-done → 1 s retry scheduled |
| `test_sort_candidates_priority` | Priority 1 dispatched before priority 3 |
| `test_sort_candidates_none_priority_last` | None priority after numeric priorities |
| `test_sort_candidates_created_at_tiebreak` | Same priority: older issue first |
| `test_workflow_watcher_reloads_config` | File change triggers config update |
| `test_workflow_watcher_bad_yaml_no_crash` | Invalid reload keeps old config |
