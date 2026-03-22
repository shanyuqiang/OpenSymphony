"""AgentRunner: orchestrates a single issue run (hooks + claude invocation)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from symphony.agent.claude_cli import run_claude
from symphony.config import AgentConfig, ClaudeConfig, HooksConfig
from symphony.models import ClaudeResult, Issue, Workspace
from symphony.workflow import Workflow

logger = logging.getLogger(__name__)


async def _run_hook(
    hook_script: str,
    workspace_path: Path,
    timeout_ms: int,
    *,
    fatal: bool,
) -> bool:
    """Execute a shell hook script in *workspace_path*."""
    try:
        proc = await asyncio.create_subprocess_shell(
            hook_script,
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000
        )
        if proc.returncode != 0:
            msg = f"Hook failed (exit {proc.returncode}): {stderr.decode(errors='replace')}"
            if fatal:
                raise RuntimeError(msg)
            logger.warning(msg)
            return False
        return True
    except asyncio.TimeoutError:
        msg = f"Hook timed out after {timeout_ms} ms"
        if fatal:
            raise RuntimeError(msg)
        logger.warning(msg)
        return False


class AgentRunner:
    """Runs one issue through the claude agent."""

    def __init__(
        self,
        workflow: Workflow,
        agent_config: AgentConfig,
        claude_config: ClaudeConfig,
        hooks: HooksConfig,
    ) -> None:
        self.workflow = workflow
        self.agent_config = agent_config
        self.claude_config = claude_config
        self.hooks = hooks

    async def run(
        self,
        issue: Issue,
        workspace: Workspace,
        attempt: int = 0,
    ) -> ClaudeResult:
        """Run the agent for *issue* in *workspace*. Returns ClaudeResult."""
        prompt = self.workflow.render_prompt(
            issue=issue.model_dump(mode="json"),
            attempt=attempt if attempt > 0 else None,
        )

        if self.hooks.before_run:
            try:
                await _run_hook(
                    self.hooks.before_run,
                    workspace.path,
                    self.hooks.timeout_ms,
                    fatal=True,  # §5.3.4: before_run failure aborts the current attempt
                )
            except RuntimeError as exc:
                logger.warning("before_run hook aborted attempt for %s: %s", issue.identifier, exc)
                return ClaudeResult(success=False, error=f"before_run hook failed: {exc}")

        result = await run_claude(
            prompt=prompt,
            workspace=workspace.path,
            config=self.claude_config,
            max_turns=self.agent_config.max_turns,
        )

        if self.hooks.after_run:
            try:
                await _run_hook(
                    self.hooks.after_run,
                    workspace.path,
                    self.hooks.timeout_ms,
                    fatal=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("after_run hook error: %s", exc)

        return result
