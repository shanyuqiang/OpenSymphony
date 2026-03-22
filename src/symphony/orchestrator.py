"""Symphony Orchestrator.

Polls Gitea for issues, dispatches them to Claude agents in isolated workspaces,
and manages the full task lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from symphony.agent.runner import AgentRunner
from symphony.config import WorkflowConfig
from symphony.labels import LabelLifecycleManager
from symphony.models import Issue, RetryEntry, RunningEntry, TokenCounts
from symphony.tracker.gitea import GiteaTracker
from symphony.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """Main orchestration loop."""

    def __init__(self, config: WorkflowConfig, workflow: Any) -> None:
        self.config = config
        self.workflow = workflow

        self.tracker = GiteaTracker(config.tracker)
        self.label_mgr = LabelLifecycleManager(self.tracker)
        self.workspace_mgr = WorkspaceManager(config.workspace, config.hooks)
        self.agent_runner = AgentRunner(
            workflow=workflow,
            agent_config=config.agent,
            claude_config=config.claude,
            hooks=config.hooks,
        )

        # State
        self._running: dict[str, RunningEntry] = {}
        self._claimed: set[str] = set()  # issue IDs reserved or running (prevents double dispatch)
        self._retry_queue: list[RetryEntry] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(config.agent.max_concurrent_agents)
        self._stop_event = asyncio.Event()

        # Optional dashboard state callback
        self._on_state_change: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state_change_callback(self, cb: Callable[[], None]) -> None:
        self._on_state_change = cb

    def get_running(self) -> dict[str, RunningEntry]:
        return dict(self._running)

    def get_retry_queue(self) -> list[RetryEntry]:
        return list(self._retry_queue)

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
        await self._startup_cleanup()
        await self._run_loop()

    def stop(self) -> None:
        """Signal the orchestrator to stop."""
        logger.info("Stop requested")
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def _startup_cleanup(self) -> None:
        """On startup, remove symphony-doing from any stale issues."""
        try:
            stale = await self.tracker.fetch_issues_by_states(
                self.config.tracker.active_states
            )
            for raw in stale:
                issue = self._parse_issue(raw)
                if issue and "symphony-doing" in issue.labels:
                    logger.info(
                        "Cleaning up stale symphony-doing label on startup",
                        extra={"identifier": issue.identifier},
                    )
                    await self.tracker.remove_label(issue.number, "symphony-doing")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Startup cleanup failed: %s", exc)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        interval_s = self.config.polling.interval_ms / 1000
        while not self._stop_event.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=interval_s,
                )
            except TimeoutError:
                pass

        # Graceful shutdown: wait for running tasks
        if self._tasks:
            logger.info(
                "Waiting for %d running agents to finish", len(self._tasks)
            )
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _tick(self) -> None:
        """One iteration: process retries + poll + dispatch."""
        await self._process_retries()
        await self._poll_and_dispatch()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_and_dispatch(self) -> None:
        try:
            raw_issues = await self.tracker.fetch_candidate_issues()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch issues: %s", exc)
            return

        for raw in raw_issues:
            issue = self._parse_issue(raw)
            if issue is None:
                continue
            if issue.id in self._claimed:
                continue
            if not self.label_mgr.should_dispatch(issue):
                continue
            if issue.blocked_by:
                logger.debug(
                    "Issue %s is blocked, skipping", issue.identifier
                )
                continue

            # Claim immediately before create_task to close the double-dispatch window
            self._claimed.add(issue.id)
            asyncio.create_task(self._dispatch(issue), name=f"agent-{issue.id}")

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, issue: Issue) -> None:
        """Acquire semaphore, create workspace, run agent, handle result."""
        async with self._semaphore:
            if self._stop_event.is_set():
                self._claimed.discard(issue.id)
                return

            # Mark as doing
            added = await self.label_mgr.on_dispatch(issue)
            if not added:
                logger.warning(
                    "Failed to add symphony-doing to %s, skipping",
                    issue.identifier,
                )
                self._claimed.discard(issue.id)
                return

            try:
                workspace = await self.workspace_mgr.create_for_issue(issue)
            except Exception as exc:  # noqa: BLE001
                logger.error("Workspace creation failed for %s: %s", issue.identifier, exc)
                await self.label_mgr.on_completion_detected(issue)
                self._claimed.discard(issue.id)
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
                # Agent added symphony-done — remove symphony-doing, done!
                await self.label_mgr.on_completion_detected(refreshed)
                logger.info(
                    "Issue %s completed by agent (tokens: %s)",
                    issue.identifier,
                    result_tokens.total_tokens,
                )
                self._claimed.discard(issue.id)
                return

            # Not marked done — treat as failure
            await self.label_mgr.on_completion_detected(issue)

            if not result_success:
                error_msg = result_error or "Agent did not mark issue as done"
                logger.warning(
                    "Agent run failed for %s (attempt %d): %s",
                    issue.identifier,
                    attempt,
                    error_msg,
                )
                self._schedule_retry(issue, attempt + 1, error_msg)
                # _claimed is retained while issue sits in retry queue
            else:
                # Agent succeeded but did not mark done — release
                self._claimed.discard(issue.id)

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    def _get_attempt(self, issue_id: str) -> int:
        for entry in self._retry_queue:
            if entry.issue_id == issue_id:
                return entry.attempt
        return 0

    def _schedule_retry(self, issue: Issue, attempt: int, error: str) -> None:
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
        # Remove existing retry entry for this issue (if any)
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

    async def _process_retries(self) -> None:
        now_ms = time.monotonic() * 1000
        due = [e for e in self._retry_queue if e.due_at_ms <= now_ms]
        if not due:
            return

        try:
            raw_issues = await self.tracker.fetch_candidate_issues()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch issues for retry processing: %s", exc)
            return

        issues_by_id = {
            raw.get("id", ""): self._parse_issue(raw)
            for raw in raw_issues
            if self._parse_issue(raw)
        }

        for entry in due:
            if entry.issue_id in self._running:
                continue
            issue = issues_by_id.get(entry.issue_id)
            if issue is None:
                # Issue no longer active — release claim
                self._retry_queue = [
                    e for e in self._retry_queue if e.issue_id != entry.issue_id
                ]
                self._claimed.discard(entry.issue_id)
                continue
            if self.label_mgr.is_completed(issue):
                self._retry_queue = [
                    e for e in self._retry_queue if e.issue_id != entry.issue_id
                ]
                self._claimed.discard(entry.issue_id)
                continue
            # Remove from retry queue and re-dispatch (_claimed stays set)
            self._retry_queue = [
                e for e in self._retry_queue if e.issue_id != entry.issue_id
            ]
            asyncio.create_task(
                self._dispatch(issue), name=f"agent-retry-{issue.id}"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_issue(self, raw: dict) -> Issue | None:
        try:
            return Issue(**raw)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to parse issue: %s — %s", raw.get("id"), exc)
            return None

    async def _refresh_issue(self, issue: Issue) -> Issue | None:
        try:
            results = await self.tracker.fetch_issue_states_by_ids([issue.id])
            if results:
                return self._parse_issue(results[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to refresh issue %s: %s", issue.identifier, exc)
        return None

    def _notify(self) -> None:
        if self._on_state_change:
            try:
                self._on_state_change()
            except Exception:  # noqa: BLE001
                pass
