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


def _get_structured_logger(issue_id: int | None = None):
    """Get structured logger for issue-specific logging."""
    from lib.logger import get_logger

    return get_logger(
        name="sdk_runner",
        session_id="sdk",
        issue_id=issue_id,
        console=True,
    )


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
        issue_id: Optional[int] = None,
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

        # Use structured logger for issue-specific file logging
        slog = _get_structured_logger(issue_id)
        slog.info(
            f"SDK agent run: model={model}, budget=${max_budget}, cwd={worktree_path}"
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
            msg_count = 0
            async for message in query(prompt=prompt, options=options):
                msg_count += 1
                msg_type = getattr(message, "type", "unknown")

                # Handle result message
                if isinstance(message, ResultMessage):
                    nonlocal success, cost_usd
                    success = message.is_error is not True
                    if hasattr(message, "result") and message.result:
                        result_text = str(message.result)[:500]
                        output_parts.append(str(message.result))
                        slog.info(f"[msg#{msg_count}] Result: {result_text}")
                    if hasattr(message, "total_cost_usd") and message.total_cost_usd:
                        cost_usd = message.total_cost_usd
                    break

                # Log each message with useful details
                if msg_type == "assistant":
                    # Log text content from assistant
                    if hasattr(message, "content"):
                        content = message.content
                        if isinstance(content, list):
                            for item in content[:3]:  # Limit to first 3 items
                                if hasattr(item, "type") and item.type == "text":
                                    text = getattr(item, "text", "")[:200]
                                    if text:
                                        slog.info(f"[msg#{msg_count}] Assistant: {text}")
                                elif hasattr(item, "type") and item.type == "thinking":
                                    thinking = getattr(item, "thinking", "")[:200]
                                    if thinking:
                                        slog.debug(f"[msg#{msg_count}] Thinking: {thinking}")
                    elif hasattr(message, "text"):
                        slog.info(f"[msg#{msg_count}] Assistant: {message.text[:200]}")

                elif msg_type == "user":
                    slog.debug(f"[msg#{msg_count}] User message")

                elif msg_type == "tool_use":
                    tool_name = getattr(message, "name", "unknown")
                    tool_input = getattr(message, "input", {})
                    if isinstance(tool_input, dict):
                        tool_input_str = str(tool_input)[:300]
                    else:
                        tool_input_str = str(tool_input)[:300]
                    slog.info(f"[msg#{msg_count}] Tool use: {tool_name}({tool_input_str})")

                elif msg_type == "tool_result":
                    content = getattr(message, "content", "")
                    if isinstance(content, list):
                        for item in content[:2]:
                            result = getattr(item, "content", str(item))[:200]
                            slog.debug(f"[msg#{msg_count}] Tool result: {result}")
                    else:
                        slog.debug(f"[msg#{msg_count}] Tool result: {str(content)[:200]}")

                elif msg_type == "result":
                    result_text = getattr(message, "result", "")[:500]
                    cost = getattr(message, "cost_usd", 0)
                    slog.info(f"[msg#{msg_count}] Result: {result_text[:300]} (cost: ${cost})")

                else:
                    slog.debug(f"[msg#{msg_count}] Message: {msg_type}")

                # Progress callback
                if on_progress is not None:
                    on_progress({"type": msg_type, "msg_count": msg_count})

        try:
            await asyncio.wait_for(_run_with_timeout(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            slog.warning(f"SDK agent timeout ({self.timeout_s}s)")
            duration = time.monotonic() - start
            return RunResult(
                success=False,
                output="Forced termination due to timeout",
                cost_usd=cost_usd,
                duration_s=duration,
                exit_code=-1,
            )
        except Exception as e:
            slog.error(f"SDK agent run error: {e}")
            duration = time.monotonic() - start
            return RunResult(
                success=False,
                output=str(e),
                cost_usd=cost_usd,
                duration_s=duration,
                exit_code=-1,
            )

        duration = time.monotonic() - start
        slog.info(
            f"SDK agent completed: success={success}, cost=${cost_usd:.2f}, "
            f"duration={duration:.1f}s"
        )

        return RunResult(
            success=success,
            output="\n".join(output_parts),
            cost_usd=cost_usd,
            duration_s=duration,
            exit_code=0 if success else 1,
        )
