"""orchestrator.py tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.config import AgentConfig, HooksConfig, PollingConfig, SymphonyConfig, TrackerConfig, WorkspaceConfig
from lib.orchestrator import (
    Orchestrator,
    StateStore,
    TaskRecord,
    TaskState,
)
from lib.claude_sdk_runner import RunResult
from lib.tracker import Issue
from lib.workflow import WorkflowConfig


# --- TaskRecord ---


class TestTaskRecord:
    def test_initial_state(self) -> None:
        record = TaskRecord(issue_number=1, issue_title="test")
        assert record.state == TaskState.QUEUED
        assert record.attempt == 0

    def test_valid_transition(self) -> None:
        record = TaskRecord(issue_number=1, issue_title="test")
        record.transition(TaskState.PREPARING)
        assert record.state == TaskState.PREPARING

        record.transition(TaskState.RUNNING)
        assert record.state == TaskState.RUNNING

        record.transition(TaskState.SUCCEEDED)
        assert record.state == TaskState.SUCCEEDED

    def test_invalid_transition(self) -> None:
        record = TaskRecord(issue_number=1, issue_title="test")
        with pytest.raises(ValueError, match="Invalid state transition"):
            record.transition(TaskState.RUNNING)  # QUEUED -> RUNNING direct transition not allowed

    def test_retry_transition(self) -> None:
        record = TaskRecord(issue_number=1, issue_title="test")
        record.transition(TaskState.PREPARING)
        record.transition(TaskState.RUNNING)
        record.transition(TaskState.FAILED)
        record.transition(TaskState.RETRYING)
        record.transition(TaskState.PREPARING)  # RETRYING -> PREPARING allowed

    def test_escalation(self) -> None:
        record = TaskRecord(issue_number=1, issue_title="test")
        record.transition(TaskState.PREPARING)
        record.transition(TaskState.RUNNING)
        record.transition(TaskState.FAILED)
        record.transition(TaskState.ESCALATED)
        assert record.state == TaskState.ESCALATED

    def test_serialization_deserialization(self) -> None:
        record = TaskRecord(
            issue_number=42,
            issue_title="bug fix",
            state=TaskState.RUNNING,
            attempt=2,
        )
        data = record.to_dict()
        assert data["state"] == "RUNNING"
        assert data["issue_number"] == 42

        restored = TaskRecord.from_dict(data)
        assert restored.state == TaskState.RUNNING
        assert restored.issue_number == 42
        assert restored.attempt == 2


# --- StateStore ---


class TestStateStore:
    def test_queue_save_load(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path)
        records = [
            TaskRecord(issue_number=1, issue_title="first"),
            TaskRecord(issue_number=2, issue_title="second"),
        ]
        store.save_queue(records)
        loaded = store.load_queue()
        assert len(loaded) == 2
        assert loaded[0].issue_number == 1
        assert loaded[1].issue_title == "second"

    def test_load_empty_queue(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path)
        assert store.load_queue() == []

    def test_active_save_load(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path)
        record = TaskRecord(issue_number=7, issue_title="test")
        store.save_active(record)

        loaded = store.load_active(7)
        assert loaded is not None
        assert loaded.issue_number == 7

    def test_active_missing_issue(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path)
        assert store.load_active(999) is None

    def test_move_to_completed(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path)
        record = TaskRecord(issue_number=3, issue_title="completed test")
        store.save_active(record)
        store.move_to_completed(record)

        assert store.load_active(3) is None
        completed_path = tmp_path / "completed" / "issue-3.json"
        assert completed_path.exists()

    def test_remove_from_queue(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path)
        records = [
            TaskRecord(issue_number=1, issue_title="a"),
            TaskRecord(issue_number=2, issue_title="b"),
            TaskRecord(issue_number=3, issue_title="c"),
        ]
        store.save_queue(records)
        store.remove_from_queue(2)

        loaded = store.load_queue()
        assert len(loaded) == 2
        assert all(r.issue_number != 2 for r in loaded)

    def test_list_active(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path)
        store.save_active(TaskRecord(issue_number=1, issue_title="a"))
        store.save_active(TaskRecord(issue_number=5, issue_title="b"))

        active = store.list_active()
        assert len(active) == 2
        numbers = {r.issue_number for r in active}
        assert numbers == {1, 5}


# --- Orchestrator ---


def _make_config(**overrides: Any) -> SymphonyConfig:
    """Create test SymphonyConfig."""
    agent_kwargs = {
        "max_concurrent": 2,
        "max_retries": 3,
        "retry_delay_s": 0,  # no wait in tests
        **overrides,
    }
    return SymphonyConfig(agent=AgentConfig(**agent_kwargs))


def _make_workflow() -> WorkflowConfig:
    """Create test WorkflowConfig."""
    return WorkflowConfig(
        body_template="Issue #{{issue.number}}: {{issue.title}}\n",
        hooks={},
    )


def _make_issue(number: int = 1, title: str = "test issue") -> Issue:
    return Issue(number=number, title=title, body="test body")


class TestOrchestrator:
    def test_enqueue(self, tmp_path: Path) -> None:
        config = _make_config()
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=MagicMock(),
            runner=MagicMock(),
            state_dir=tmp_path,
        )
        record = orch.enqueue(_make_issue(1))
        assert record.state == TaskState.QUEUED
        assert record.issue_number == 1

    def test_duplicate_enqueue_error(self, tmp_path: Path) -> None:
        config = _make_config()
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=MagicMock(),
            runner=MagicMock(),
            state_dir=tmp_path,
        )
        orch.enqueue(_make_issue(1))
        with pytest.raises(ValueError, match="already"):
            orch.enqueue(_make_issue(1))

    @pytest.mark.asyncio
    async def test_successful_dispatch(self, tmp_path: Path) -> None:
        workspace = AsyncMock()
        workspace.create_worktree.return_value = tmp_path / "worktree"

        runner = AsyncMock()
        runner.run.return_value = RunResult(
            success=True, output="done", cost_usd=1.5, duration_s=30.0, exit_code=0
        )

        config = _make_config()
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=workspace,
            runner=runner,
            state_dir=tmp_path,
        )

        issue = _make_issue(1)
        record = await orch.dispatch_one(issue)

        assert record.state == TaskState.SUCCEEDED
        assert record.cost_usd == 1.5
        assert record.attempt == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self, tmp_path: Path) -> None:
        workspace = AsyncMock()
        workspace.create_worktree.return_value = tmp_path / "worktree"

        runner = AsyncMock()
        # first fails, second succeeds
        runner.run.side_effect = [
            RunResult(success=False, output="error", cost_usd=0.5, duration_s=10.0, exit_code=1),
            RunResult(success=True, output="success", cost_usd=1.0, duration_s=20.0, exit_code=0),
        ]

        config = _make_config(max_retries=3)
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=workspace,
            runner=runner,
            state_dir=tmp_path,
        )

        record = await orch.dispatch_one(_make_issue(1))
        assert record.state == TaskState.SUCCEEDED
        assert record.attempt == 2
        assert record.cost_usd == 1.5  # 0.5 + 1.0

    @pytest.mark.asyncio
    async def test_max_retries_escalation(self, tmp_path: Path) -> None:
        workspace = AsyncMock()
        workspace.create_worktree.return_value = tmp_path / "worktree"

        runner = AsyncMock()
        runner.run.return_value = RunResult(
            success=False, output="error", cost_usd=0.5, duration_s=5.0, exit_code=1
        )

        config = _make_config(max_retries=2)
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=workspace,
            runner=runner,
            state_dir=tmp_path,
        )

        record = await orch.dispatch_one(_make_issue(1))
        assert record.state == TaskState.ESCALATED
        assert record.attempt == 2

    @pytest.mark.asyncio
    async def test_workspace_failure(self, tmp_path: Path) -> None:
        workspace = AsyncMock()
        workspace.create_worktree.side_effect = RuntimeError("git error")

        config = _make_config(max_retries=1)
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=workspace,
            runner=AsyncMock(),
            state_dir=tmp_path,
        )

        record = await orch.dispatch_one(_make_issue(1))
        assert record.state == TaskState.ESCALATED
        assert "Workspace creation failed" in record.error

    def test_get_status(self, tmp_path: Path) -> None:
        config = _make_config()
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=MagicMock(),
            runner=MagicMock(),
            state_dir=tmp_path,
        )
        orch.enqueue(_make_issue(1))
        orch.enqueue(_make_issue(2))

        status = orch.get_status()
        assert status["queued"] == 2
        assert status["active"] == 0

    @pytest.mark.asyncio
    async def test_semaphore_concurrency_limit(self, tmp_path: Path) -> None:
        """Verify that max_concurrent=1 limits concurrent execution to 1."""
        workspace = AsyncMock()
        workspace.create_worktree.return_value = tmp_path / "worktree"

        concurrent_count = 0
        max_concurrent_seen = 0

        async def slow_run(prompt, worktree_path, config, on_progress=None):
            nonlocal concurrent_count, max_concurrent_seen
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return RunResult(success=True, output="ok", cost_usd=0.1, duration_s=1.0, exit_code=0)

        runner = AsyncMock()
        runner.run.side_effect = slow_run

        config = _make_config(max_concurrent=1)
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=workspace,
            runner=runner,
            state_dir=tmp_path,
        )

        # dispatch 2 issues concurrently
        results = await asyncio.gather(
            orch.dispatch_one(_make_issue(1)),
            orch.dispatch_one(_make_issue(2)),
        )

        assert max_concurrent_seen == 1
        assert all(r.state == TaskState.SUCCEEDED for r in results)

    def test_stop(self, tmp_path: Path) -> None:
        config = _make_config()
        orch = Orchestrator(
            config=config,
            workflow=_make_workflow(),
            workspace=MagicMock(),
            runner=MagicMock(),
            state_dir=tmp_path,
        )
        assert not orch.is_stopped
        orch.stop()
        assert orch.is_stopped
