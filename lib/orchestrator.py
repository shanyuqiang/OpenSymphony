"""Orchestrator state machine + asyncio dispatch.

Like an air traffic control tower, assigns incoming issues (planes) in order,
manages runways (concurrent slots), and handles retries on failure.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lib.config import AgentConfig, SymphonyConfig
from lib.notifier import Notifier, TaskResult
from lib.runner import AgentRunner, RunResult
from lib.tracker import GitHubTracker, Issue
from lib.workflow import WorkflowConfig, render_hooks, render_workflow
from lib.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


# --- 상태 정의 ---


class TaskState(str, enum.Enum):
    """이슈 처리 상태. 관제탑의 비행기 상태판과 같다."""

    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PR_CREATED = "PR_CREATED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    ESCALATED = "ESCALATED"


# 허용된 상태 전이 (현재 상태 → 가능한 다음 상태들)
_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {TaskState.PREPARING},
    TaskState.PREPARING: {TaskState.RUNNING, TaskState.FAILED},
    TaskState.RUNNING: {TaskState.SUCCEEDED, TaskState.FAILED},
    TaskState.SUCCEEDED: {TaskState.PR_CREATED},
    TaskState.PR_CREATED: set(),
    TaskState.FAILED: {TaskState.RETRYING, TaskState.ESCALATED},
    TaskState.RETRYING: {TaskState.PREPARING},
    TaskState.ESCALATED: set(),
}


# --- 태스크 레코드 ---


@dataclass
class TaskRecord:
    """이슈 처리 상태를 추적하는 레코드. JSON으로 영속화된다."""

    issue_number: int
    issue_title: str
    state: TaskState = TaskState.QUEUED
    attempt: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str = ""
    cost_usd: float = 0.0
    duration_s: float = 0.0
    pr_url: str = ""
    worktree_path: str = ""

    def transition(self, new_state: TaskState) -> None:
        """상태를 전이한다. 허용되지 않은 전이는 에러."""
        allowed = _TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"허용되지 않은 상태 전이: {self.state.value} → {new_state.value}"
            )
        self.state = new_state
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화용 딕셔너리를 반환한다."""
        data = asdict(self)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        """딕셔너리에서 TaskRecord를 복원한다."""
        data = {**data}
        data["state"] = TaskState(data["state"])
        return cls(**data)


# --- 상태 영속화 ---


class StateStore:
    """state/ 디렉토리에 JSON 파일로 태스크 상태를 영속화한다.

    비유: 서류 캐비닛처럼, 진행 중인 서류는 active 서랍에,
    완료된 서류는 completed 서랍에 보관한다.
    """

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.queue_file = state_dir / "queue.json"
        self.active_dir = state_dir / "active"
        self.completed_dir = state_dir / "completed"

        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.completed_dir.mkdir(parents=True, exist_ok=True)

    def save_queue(self, records: list[TaskRecord]) -> None:
        """대기열을 queue.json에 저장한다."""
        data = [r.to_dict() for r in records]
        self.queue_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_queue(self) -> list[TaskRecord]:
        """queue.json에서 대기열을 로드한다."""
        if not self.queue_file.exists():
            return []
        text = self.queue_file.read_text(encoding="utf-8")
        if not text.strip():
            return []
        return [TaskRecord.from_dict(d) for d in json.loads(text)]

    def save_active(self, record: TaskRecord) -> None:
        """진행 중인 태스크를 active/ 디렉토리에 저장한다."""
        path = self.active_dir / f"issue-{record.issue_number}.json"
        path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_active(self, issue_number: int) -> TaskRecord | None:
        """active/ 디렉토리에서 태스크를 로드한다."""
        path = self.active_dir / f"issue-{issue_number}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return TaskRecord.from_dict(data)

    def move_to_completed(self, record: TaskRecord) -> None:
        """active에서 completed로 이동한다."""
        active_path = self.active_dir / f"issue-{record.issue_number}.json"
        completed_path = self.completed_dir / f"issue-{record.issue_number}.json"
        completed_path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if active_path.exists():
            active_path.unlink()

    def list_active(self) -> list[TaskRecord]:
        """active/ 디렉토리의 모든 태스크를 로드한다."""
        records: list[TaskRecord] = []
        for path in sorted(self.active_dir.glob("issue-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append(TaskRecord.from_dict(data))
        return records

    def remove_from_queue(self, issue_number: int) -> None:
        """대기열에서 특정 이슈를 제거한다."""
        queue = self.load_queue()
        queue = [r for r in queue if r.issue_number != issue_number]
        self.save_queue(queue)


# --- 오케스트레이터 ---


class Orchestrator:
    """Issue processing orchestrator.

    Like an air traffic control tower, manages incoming issues (planes),
    puts them in queue, executes when slots are free, retries on failure
    or escalates.
    """

    def __init__(
        self,
        config: SymphonyConfig,
        workflow: WorkflowConfig,
        workspace: WorkspaceManager,
        runner: AgentRunner,
        state_dir: Path,
        notifier: Notifier | None = None,
        tracker: GitHubTracker | None = None,
    ) -> None:
        self.config = config
        self.workflow = workflow
        self.workspace = workspace
        self.runner = runner
        self.store = StateStore(state_dir)
        self.notifier = notifier
        self.tracker = tracker
        self._semaphore = asyncio.Semaphore(config.agent.max_concurrent)
        self._running: dict[int, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()

    def enqueue(self, issue: Issue) -> TaskRecord:
        """이슈를 대기열에 추가한다."""
        record = TaskRecord(
            issue_number=issue.number,
            issue_title=issue.title,
            max_retries=self.config.agent.max_retries,
        )
        queue = self.store.load_queue()

        # 중복 방지
        existing = {r.issue_number for r in queue}
        active_numbers = {r.issue_number for r in self.store.list_active()}
        if issue.number in existing or issue.number in active_numbers:
            raise ValueError(f"이슈 #{issue.number}는 이미 큐 또는 활성 상태입니다")

        queue.append(record)
        self.store.save_queue(queue)
        return record

    async def dispatch_one(self, issue: Issue) -> TaskRecord:
        """단일 이슈를 처리한다 (QUEUED → 최종 상태까지)."""
        # 큐에서 레코드 찾기 또는 새로 생성
        queue = self.store.load_queue()
        record = next(
            (r for r in queue if r.issue_number == issue.number),
            None,
        )
        if record is None:
            record = TaskRecord(
                issue_number=issue.number,
                issue_title=issue.title,
                max_retries=self.config.agent.max_retries,
            )

        self.store.remove_from_queue(issue.number)
        await self._process_issue(record, issue)
        return record

    async def _process_issue(self, record: TaskRecord, issue: Issue) -> None:
        """이슈 한 건의 전체 생애주기를 관리한다."""
        async with self._semaphore:
            await self._run_with_retries(record, issue)

    async def _run_with_retries(self, record: TaskRecord, issue: Issue) -> None:
        """재시도 로직을 포함한 실행."""
        while True:
            record.attempt += 1

            # PREPARING
            record.transition(
                TaskState.PREPARING
                if record.state in (TaskState.QUEUED, TaskState.RETRYING)
                else TaskState.PREPARING
            )
            self.store.save_active(record)

            # 워크스페이스 준비
            branch_name = f"feat/issue-{issue.number}"
            try:
                wt_path = await self.workspace.create_worktree(
                    issue.number, branch_name
                )
                record.worktree_path = str(wt_path)
            except Exception as e:
                record.error = f"워크스페이스 생성 실패: {e}"
                record.transition(TaskState.FAILED)
                self.store.save_active(record)
                self._handle_failure(record)
                return

            # 훅: before_run
            context = self._build_context(issue, record.attempt)
            hooks = render_hooks(self.workflow.hooks, context)
            if hooks.get("before_run"):
                await self._run_hook(hooks["before_run"], wt_path)

            # RUNNING
            record.transition(TaskState.RUNNING)
            self.store.save_active(record)

            # 프롬프트 렌더링 + 에이전트 실행
            prompt = render_workflow(self.workflow, context)
            agent_config = {
                "model": self.config.agent.model,
                "max_budget_usd": self.config.agent.max_budget_usd,
                "allowed_tools": self.config.agent.allowed_tools,
            }

            try:
                result: RunResult = await self.runner.run(
                    prompt=prompt,
                    worktree_path=wt_path,
                    config=agent_config,
                    issue_id=issue.number,
                )
            except Exception as e:
                result = RunResult(
                    success=False,
                    output=str(e),
                    cost_usd=0.0,
                    duration_s=0.0,
                    exit_code=-1,
                )

            record.cost_usd += result.cost_usd
            record.duration_s += result.duration_s

            if result.success:
                record.transition(TaskState.SUCCEEDED)
                self.store.save_active(record)

                if self.notifier:
                    task_result = TaskResult(
                        issue_number=record.issue_number,
                        issue_title=record.issue_title,
                        state=record.state.value,
                        attempt=record.attempt,
                        max_retries=record.max_retries,
                        cost_usd=record.cost_usd,
                        duration_s=record.duration_s,
                        pr_url=record.pr_url,
                    )
                    await self.notifier.notify("succeeded", task_result)

                # Push branch and create PR
                if self.tracker:
                    try:
                        branch_name = f"feat/issue-{issue.number}"
                        pr_url = await self._push_and_create_pr(
                            wt_path, branch_name, issue
                        )
                        if pr_url:
                            record.pr_url = pr_url
                            record.transition(TaskState.PR_CREATED)
                            self.store.save_active(record)
                            # Update issue labels
                            await self.tracker.update_labels(
                                issue.number,
                                add_labels=["symphony:done"],
                                remove_labels=["symphony:in-progress", "symphony:ready"],
                            )
                    except Exception as e:
                        logger.warning("Failed to create PR: %s", e)

                # Hook: after_run
                if hooks.get("after_run"):
                    await self._run_hook(hooks["after_run"], wt_path)

                self.store.move_to_completed(record)
                return
            else:
                record.error = result.output[:500]
                record.transition(TaskState.FAILED)
                self.store.save_active(record)
                should_retry = self._handle_failure(record)
                if not should_retry:
                    if self.notifier:
                        task_result = TaskResult(
                            issue_number=record.issue_number,
                            issue_title=record.issue_title,
                            state=record.state.value,
                            attempt=record.attempt,
                            max_retries=record.max_retries,
                            cost_usd=record.cost_usd,
                            duration_s=record.duration_s,
                            error=record.error,
                        )
                        await self.notifier.notify("escalated", task_result)
                    self.store.move_to_completed(record)
                    return

                if self.notifier:
                    task_result = TaskResult(
                        issue_number=record.issue_number,
                        issue_title=record.issue_title,
                        state=record.state.value,
                        attempt=record.attempt,
                        max_retries=record.max_retries,
                        cost_usd=record.cost_usd,
                        duration_s=record.duration_s,
                        error=record.error,
                    )
                    await self.notifier.notify("failed", task_result)

                # 재시도 대기
                delay = self.config.agent.retry_delay_s * record.attempt
                await asyncio.sleep(delay)

    def _handle_failure(self, record: TaskRecord) -> bool:
        """실패 처리. 재시도 가능하면 True, 에스컬레이션이면 False."""
        if record.attempt < record.max_retries:
            record.transition(TaskState.RETRYING)
            self.store.save_active(record)
            return True
        else:
            record.transition(TaskState.ESCALATED)
            self.store.save_active(record)
            return False

    def _build_context(self, issue: Issue, attempt: int) -> dict[str, Any]:
        """템플릿 렌더링용 context를 생성한다."""
        ctx: dict[str, Any] = {
            "issue": {
                "number": issue.number,
                "title": issue.title,
                "body": issue.body,
            },
        }
        if attempt > 1:
            ctx["attempt"] = attempt
        return ctx

    async def _run_hook(self, script: str, cwd: Path) -> None:
        """Run shell hook script."""
        proc = await asyncio.create_subprocess_shell(
            script,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _push_and_create_pr(
        self,
        worktree_path: Path,
        branch_name: str,
        issue: Issue,
    ) -> str | None:
        """Push branch to origin and create PR. Returns PR URL."""
        if not self.tracker:
            return None

        # Push to tracker repo using full URL (worktree may have wrong remote)
        remote_url = f"https://github.com/{self.tracker.repo}.git"
        proc = await asyncio.create_subprocess_exec(
            "git", "push", remote_url, f"HEAD:{branch_name}",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.warning("Git push failed: %s", error_msg)
            return None

        # Create PR using tracker
        try:
            pr_body = issue.body[:500] if issue.body else f"Resolves #{issue.number}"
            pr_url = await self.tracker.create_pr(
                issue_number=issue.number,
                branch=branch_name,
                title=issue.title,
                body=pr_body,
            )
            logger.info("Created PR: %s", pr_url)
            return pr_url
        except Exception as e:
            logger.warning("Failed to create PR: %s", e)
            return None

    def get_status(self) -> dict[str, Any]:
        """현재 상태 요약을 반환한다."""
        queue = self.store.load_queue()
        active = self.store.list_active()
        return {
            "queued": len(queue),
            "active": len(active),
            "queue": [r.to_dict() for r in queue],
            "active_tasks": [r.to_dict() for r in active],
        }

    def stop(self) -> None:
        """오케스트레이터 중지 신호."""
        self._stop_event.set()

    @property
    def is_stopped(self) -> bool:
        return self._stop_event.is_set()
