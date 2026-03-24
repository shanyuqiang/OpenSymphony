"""Claude Agent SDK Runner.

Runs Claude Code agent using the Claude Agent SDK async query() API
instead of CLI subprocess.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from claude_agent_sdk import ResultMessage

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Agent execution result."""

    success: bool
    output: str
    cost_usd: float
    duration_s: float
    exit_code: int


class RunnerError(Exception):
    """Runner error."""


class SDKAgentRunner:
    """Run agent using Claude Agent SDK."""

    def __init__(self, timeout_s: int = 600) -> None:
        self.timeout_s = timeout_s

    async def run(
        self,
        prompt: str,
        worktree_path: Path,
        config: dict,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> RunResult:
        """Run agent using Claude Agent SDK and return result."""
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        model = config.get("model", "opus")
        max_budget = config.get("max_budget_usd", 5)
        allowed_tools = config.get(
            "allowed_tools",
            ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        )

        # Convert CLI's --allowedTools "Bash(*)" format to SDK list
        tools: list[str] = []
        for t in allowed_tools:
            if "(" in t:
                tools.append(t.split("(")[0])
            else:
                tools.append(t)

        logger.info(
            "SDK agent run: model=%s, budget=$%s, cwd=%s",
            model,
            max_budget,
            worktree_path,
        )

        start = time.monotonic()
        output_parts: list[str] = []
        cost_usd = 0.0
        success = False

        options = ClaudeAgentOptions(
            model=model,
            max_budget_usd=max_budget,
            allowed_tools=tools,
            permission_mode="acceptEdits",
            cwd=str(worktree_path),
            include_partial_messages=True,
        )

        async def _run_with_timeout() -> None:
            """Run query iteration with timeout wrapper."""
            async for message in query(prompt=prompt, options=options):
                # Handle result message
                if isinstance(message, ResultMessage):
                    nonlocal success, cost_usd
                    success = message.is_error is not True
                    if hasattr(message, "result") and message.result:
                        output_parts.append(str(message.result))
                    if hasattr(message, "total_cost_usd") and message.total_cost_usd:
                        cost_usd = message.total_cost_usd
                    break

                # Progress callback
                if on_progress is not None and hasattr(message, "type"):
                    on_progress({"type": getattr(message, "type", "unknown")})

        try:
            await asyncio.wait_for(_run_with_timeout(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            logger.warning("SDK agent timeout (%ds)", self.timeout_s)
            duration = time.monotonic() - start
            return RunResult(
                success=False,
                output="Forced termination due to timeout",
                cost_usd=cost_usd,
                duration_s=duration,
                exit_code=-1,
            )
        except Exception as e:
            logger.error("SDK agent run error: %s", e)
            duration = time.monotonic() - start
            return RunResult(
                success=False,
                output=str(e),
                cost_usd=cost_usd,
                duration_s=duration,
                exit_code=-1,
            )

        duration = time.monotonic() - start

        return RunResult(
            success=success,
            output="\n".join(output_parts),
            cost_usd=cost_usd,
            duration_s=duration,
            exit_code=0 if success else 1,
        )
