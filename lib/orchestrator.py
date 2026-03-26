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
from typing import Any, Callable

from lib.config import AgentConfig, SymphonyConfig
from lib.notifier import Notifier, TaskResult
from lib.claude_sdk_runner import SDKAgentRunner as AgentRunner, RunResult
from lib.tracker import GitHubTracker, Issue
from lib.workflow import WorkflowConfig, render_hooks, render_workflow
from lib.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


# --- 状态定义 ---


class TaskState(str, enum.Enum):
    """Issue 处理状态。如同机场塔台的状态显示屏。"""

    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PR_CREATED = "PR_CREATED"
    LANDING = "LANDING"  # PR created, monitoring CI + reviews
    LAND_FAILED = "LAND_FAILED"  # CI failed or review blocked
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    ESCALATED = "ESCALATED"


# 允许的状态转换（当前状态 -> 可能的下一个状态）
_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {TaskState.PREPARING},
    TaskState.PREPARING: {TaskState.RUNNING, TaskState.FAILED},
    TaskState.RUNNING: {TaskState.SUCCEEDED, TaskState.FAILED},
    TaskState.SUCCEEDED: {TaskState.PR_CREATED},
    TaskState.PR_CREATED: {TaskState.LANDING},
    TaskState.LANDING: {TaskState.PR_CREATED, TaskState.LAND_FAILED},  # back to PR_CREATED on conflict resolution
    TaskState.LAND_FAILED: {TaskState.LANDING},  # retry land
    TaskState.FAILED: {TaskState.RETRYING, TaskState.ESCALATED},
    TaskState.RETRYING: {TaskState.PREPARING},
    TaskState.ESCALATED: set(),
}


# --- 任务记录 ---


@dataclass
class TaskRecord:
    """跟踪 Issue 处理状态的记录。持久化为 JSON。"""

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
        """Transition state. Raises error for invalid transitions."""
        allowed = _TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid state transition: {self.state.value} -> {new_state.value}"
            )
        self.state = new_state
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """返回用于 JSON 序列化的字典。"""
        data = asdict(self)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        """从字典恢复 TaskRecord。"""
        data = {**data}
        data["state"] = TaskState(data["state"])
        return cls(**data)


# --- 状态持久化 ---


class StateStore:
    """state/ 目录下的 JSON 文件持久化任务状态。

    比喻：如同文件柜，进行中的文件放在 active 抽屉，
    已完成的文件放在 completed 抽屉。
    """

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.queue_file = state_dir / "queue.json"
        self.active_dir = state_dir / "active"
        self.completed_dir = state_dir / "completed"

        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.completed_dir.mkdir(parents=True, exist_ok=True)

    def save_queue(self, records: list[TaskRecord]) -> None:
        """保存队列到 queue.json。"""
        data = [r.to_dict() for r in records]
        self.queue_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_queue(self) -> list[TaskRecord]:
        """从 queue.json 加载队列。"""
        if not self.queue_file.exists():
            return []
        text = self.queue_file.read_text(encoding="utf-8")
        if not text.strip():
            return []
        return [TaskRecord.from_dict(d) for d in json.loads(text)]

    def save_active(self, record: TaskRecord) -> None:
        """将进行中的任务保存到 active/ 目录。"""
        path = self.active_dir / f"issue-{record.issue_number}.json"
        path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_active(self, issue_number: int) -> TaskRecord | None:
        """从 active/ 目录加载任务。"""
        path = self.active_dir / f"issue-{issue_number}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return TaskRecord.from_dict(data)

    def move_to_completed(self, record: TaskRecord) -> None:
        """从 active 移动到 completed。"""
        active_path = self.active_dir / f"issue-{record.issue_number}.json"
        completed_path = self.completed_dir / f"issue-{record.issue_number}.json"
        completed_path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if active_path.exists():
            active_path.unlink()

    def list_active(self) -> list[TaskRecord]:
        """加载 active/ 目录下的所有任务。"""
        records: list[TaskRecord] = []
        for path in sorted(self.active_dir.glob("issue-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append(TaskRecord.from_dict(data))
        return records

    def cleanup_orphaned(self, log_fn: Callable[[int, str], None] | None = None) -> list[int]:
        """Move orphaned active records (terminal states) to completed.

        Called on startup to recover from crashes where move_to_completed
        wasn't reached (e.g., SIGKILL during post-processing).
        """
        orphaned: list[int] = []
        terminal = {TaskState.SUCCEEDED, TaskState.PR_CREATED, TaskState.ESCALATED}
        for record in self.list_active():
            if record.state in terminal:
                msg = f"Cleaning up orphaned active record: #{record.issue_number} (state={record.state.value})"
                if log_fn:
                    log_fn(record.issue_number, msg)
                self.move_to_completed(record)
                orphaned.append(record.issue_number)
        return orphaned

    def remove_from_queue(self, issue_number: int) -> None:
        """从队列中移除指定 issue。"""
        queue = self.load_queue()
        queue = [r for r in queue if r.issue_number != issue_number]
        self.save_queue(queue)


# --- Orchestrator ---


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
        """添加 issue 到队列。"""
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
            raise ValueError(f"Issue #{issue.number} is already in queue or active")

        queue.append(record)
        self.store.save_queue(queue)
        return record

    async def dispatch_one(self, issue: Issue) -> TaskRecord:
        """处理单个 issue（从 QUEUED 到最终状态）。"""
        # 从队列查找记录或新建
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
        """管理单个 issue 的完整生命周期。"""
        async with self._semaphore:
            await self._run_with_retries(record, issue)

    async def _run_with_retries(self, record: TaskRecord, issue: Issue) -> None:
        """包含重试逻辑的执行。"""
        while True:
            record.attempt += 1

            # PREPARING
            record.transition(
                TaskState.PREPARING
                if record.state in (TaskState.QUEUED, TaskState.RETRYING)
                else TaskState.PREPARING
            )
            self.store.save_active(record)

            # 准备 workspace
            branch_name = f"feat/issue-{issue.number}"
            try:
                wt_path = await self.workspace.create_worktree(
                    issue.number, branch_name
                )
                record.worktree_path = str(wt_path)
            except Exception as e:
                record.error = f"Workspace creation failed: {e}"
                record.transition(TaskState.FAILED)
                self.store.save_active(record)
                self._handle_failure(record)
                return

            # 钩子：before_run
            context = self._build_context(issue, record.attempt)
            hooks = render_hooks(self.workflow.hooks, context)
            if hooks.get("before_run"):
                await self._run_hook(hooks["before_run"], wt_path)

            # RUNNING
            record.transition(TaskState.RUNNING)
            self.store.save_active(record)

            # 渲染 prompt + 执行 agent
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
                            # Update issue labels: symphony:merging
                            await self.tracker.update_labels(
                                issue.number,
                                add_labels=["symphony:merging"],
                                remove_labels=["symphony:in-progress", "symphony:ready"],
                            )
                            # Transition to LANDING and run land process
                            record.transition(TaskState.LANDING)
                            self.store.save_active(record)
                            await self._land_pr(record, issue, wt_path, branch_name)
                            return
                    except Exception as e:
                        logger.warning("Failed to create PR: %s", e)
                        record.error = f"PR creation failed: {e}"
                        record.transition(TaskState.LAND_FAILED)
                        self.store.save_active(record)

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

                # 重试等待
                delay = self.config.agent.retry_delay_s * record.attempt
                await asyncio.sleep(delay)

    def _handle_failure(self, record: TaskRecord) -> bool:
        """失败处理。可重试返回 True，需要 escalation 返回 False。"""
        if record.attempt < record.max_retries:
            record.transition(TaskState.RETRYING)
            self.store.save_active(record)
            return True
        else:
            record.transition(TaskState.ESCALATED)
            self.store.save_active(record)
            return False

    def _build_context(self, issue: Issue, attempt: int) -> dict[str, Any]:
        """生成用于模板渲染的 context。"""
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

    async def _land_pr(
        self,
        record: TaskRecord,
        issue: Issue,
        worktree_path: Path,
        branch_name: str,
    ) -> None:
        """Monitor CI + reviews and squash-merge PR when ready."""
        if not self.tracker:
            record.transition(TaskState.LAND_FAILED)
            record.error = "No tracker configured"
            self.store.save_active(record)
            return

        pr_number = await self._get_pr_number(issue.number)
        if not pr_number:
            logger.warning("Could not find PR number for issue #%d", issue.number)
            record.transition(TaskState.LAND_FAILED)
            record.error = "PR not found"
            self.store.save_active(record)
            return

        logger.info("Starting land process for PR #%d", pr_number)

        while not self.is_stopped:
            # Check if stopped
            if self.is_stopped:
                break

            # 1. Check merge conflicts
            pr_info = await self._get_pr_info(pr_number)
            if pr_info.get("mergeable") == "CONFLICTING":
                logger.warning("PR #%d has merge conflicts", pr_number)
                await self._resolve_conflicts(worktree_path, branch_name)
                await self._push_branch(worktree_path, branch_name)
                record.transition(TaskState.PR_CREATED)
                self.store.save_active(record)
                continue

            # 2. Check CI status
            checks = await self._get_check_runs(pr_number)
            pending, failed = self._summarize_checks(checks)
            if failed:
                logger.warning("CI checks failed for PR #%d: %s", pr_number, failed)
                record.transition(TaskState.LAND_FAILED)
                record.error = f"CI failed: {failed[0] if failed else 'unknown'}"
                self.store.save_active(record)
                await self._update_labels(issue.number, add_labels=["symphony:failed"])
                self.store.move_to_completed(record)
                return

            if pending:
                logger.debug("PR #%d: CI checks pending, waiting...", pr_number)
                await asyncio.sleep(30)
                continue

            # 3. Check review status
            reviews = await self._get_reviews(pr_number)
            has_blocking = self._has_blocking_review(reviews)
            if has_blocking:
                logger.warning("PR #%d has blocking review", pr_number)
                record.transition(TaskState.LAND_FAILED)
                record.error = "Blocking review"
                self.store.save_active(record)
                await self._update_labels(issue.number, add_labels=["symphony:failed"])
                self.store.move_to_completed(record)
                return

            # 4. All checks passed, no blocking reviews - squash-merge!
            logger.info("PR #%d is ready to merge (CI green, no blocking reviews)", pr_number)
            success = await self._squash_merge_pr(pr_number, issue.title, issue.body)
            if success:
                logger.info("PR #%d successfully merged", pr_number)
                await self._update_labels(
                    issue.number,
                    add_labels=["symphony:done"],
                    remove_labels=["symphony:merging"],
                )
                self.store.move_to_completed(record)
            else:
                logger.error("Failed to merge PR #%d", pr_number)
                record.transition(TaskState.LAND_FAILED)
                record.error = "Merge failed"
                self.store.save_active(record)
                await self._update_labels(issue.number, add_labels=["symphony:failed"])
                self.store.move_to_completed(record)
            return

        # Stopped
        logger.info("Land process stopped for PR #%d", pr_number)
        record.transition(TaskState.LAND_FAILED)
        record.error = "Stopped by signal"
        self.store.save_active(record)

    async def _get_pr_number(self, issue_number: int) -> int | None:
        """Get PR number for an issue."""
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "list",
            "--repo", self.tracker.repo,
            "--search", f"#{issue_number}",
            "--json", "number",
            "--limit", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        import json
        try:
            data = json.loads(stdout.decode())
            return data[0]["number"] if data else None
        except (json.JSONDecodeError, IndexError, KeyError):
            return None

    async def _get_pr_info(self, pr_number: int) -> dict:
        """Get PR info including mergeable status."""
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "view",
            str(pr_number),
            "--repo", self.tracker.repo,
            "--json", "number,mergeable,mergeStateStatus",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        import json
        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            return {}

    async def _get_check_runs(self, pr_number: int) -> list[dict]:
        """Get CI check runs for a PR."""
        proc = await asyncio.create_subprocess_exec(
            "gh", "api",
            f"repos/{self.tracker.repo}/commits",
            "--paginate",
            "--jq", f".[] | select(.number == {pr_number})",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Simplified: use gh pr checks
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "checks",
            str(pr_number),
            "--repo", self.tracker.repo,
            "--json", "status,conclusion,name",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        import json
        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            return []

    def _summarize_checks(self, checks: list[dict]) -> tuple[bool, list[str]]:
        """Summarize checks: returns (has_pending, failed_list)."""
        pending = False
        failed = []
        for check in checks:
            status = check.get("status")
            conclusion = check.get("conclusion")
            name = check.get("name", "unknown")
            if status != "completed":
                pending = True
                continue
            if conclusion not in ("success", "skipped", "neutral"):
                failed.append(f"{name}: {conclusion}")
        return pending, failed

    async def _get_reviews(self, pr_number: int) -> list[dict]:
        """Get reviews for a PR."""
        proc = await asyncio.create_subprocess_exec(
            "gh", "api",
            f"repos/{self.tracker.repo}/pulls/{pr_number}/reviews",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        import json
        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            return []

    def _has_blocking_review(self, reviews: list[dict]) -> bool:
        """Check if there are blocking reviews (CHANGES_REQUESTED)."""
        latest_by_user: dict[str, dict] = {}
        for review in reviews:
            user = review.get("user", {}).get("login")
            if not user:
                continue
            created = review.get("submitted_at", "")
            if user not in latest_by_user or created > latest_by_user[user].get("submitted_at", ""):
                latest_by_user[user] = review
        for review in latest_by_user.values():
            state = review.get("state")
            if state == "CHANGES_REQUESTED":
                return True
        return False

    async def _resolve_conflicts(
        self,
        worktree_path: Path,
        branch_name: str,
    ) -> None:
        """Resolve merge conflicts by rebasing on main."""
        logger.info("Resolving merge conflicts for %s", branch_name)
        # Pull main and rebase
        proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin", "main",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        proc = await asyncio.create_subprocess_exec(
            "git", "rebase", "origin/main",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("Rebase failed, aborting: %s", stderr.decode().strip())
            await asyncio.create_subprocess_exec(
                "git", "rebase", "--abort",
                cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    async def _push_branch(
        self,
        worktree_path: Path,
        branch_name: str,
    ) -> None:
        """Push branch to origin."""
        proc = await asyncio.create_subprocess_exec(
            "git", "push", "--force-with-lease", "origin", f"HEAD:{branch_name}",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _squash_merge_pr(
        self,
        pr_number: int,
        title: str,
        body: str,
    ) -> bool:
        """Squash-merge the PR."""
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "merge",
            str(pr_number),
            "--repo", self.tracker.repo,
            "--squash",
            "--subject", title[:72],
            "--body", f"Closes #{pr_number}\n\n{body[:500] if body else ''}",
            "--delete-branch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("Merge failed: %s", stderr.decode().strip())
            return False
        return True

    async def _update_labels(
        self,
        issue_number: int,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """Update issue labels."""
        if self.tracker:
            await self.tracker.update_labels(issue_number, add_labels, remove_labels)

    def get_status(self) -> dict[str, Any]:
        """返回当前状态摘要。"""
        queue = self.store.load_queue()
        active = self.store.list_active()
        return {
            "queued": len(queue),
            "active": len(active),
            "queue": [r.to_dict() for r in queue],
            "active_tasks": [r.to_dict() for r in active],
        }

    def stop(self) -> None:
        """Orchestrator 停止信号。"""
        self._stop_event.set()

    @property
    def is_stopped(self) -> bool:
        return self._stop_event.is_set()
