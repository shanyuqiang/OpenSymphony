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
                # Use type().__name__ to get class name (e.g., "AssistantMessage")
                msg_type = type(message).__name__
                subtype = getattr(message, "subtype", None)

                # Log all messages at debug level
                if msg_type == "ResultMessage":
                    nonlocal success, cost_usd
                    success = message.is_error is not True
                    if hasattr(message, "result") and message.result:
                        result_text = str(message.result)[:500]
                        output_parts.append(str(message.result))
                        slog.info(f"[msg#{msg_count}] ResultMessage({subtype}): {result_text}")
                    if hasattr(message, "total_cost_usd") and message.total_cost_usd:
                        cost_usd = message.total_cost_usd
                    break

                elif msg_type == "SystemMessage":
                    slog.debug(f"[msg#{msg_count}] SystemMessage({subtype})")

                elif msg_type == "AssistantMessage":
                    # Log text content from assistant
                    content = getattr(message, "content", [])
                    if isinstance(content, list):
                        for item in content[:3]:
                            item_type = getattr(item, "type", "")
                            if item_type == "text":
                                text = getattr(item, "text", "")[:200]
                                if text:
                                    slog.info(f"[msg#{msg_count}] Assistant: {text}")
                            elif item_type == "thinking":
                                thinking = getattr(item, "thinking", "")[:100]
                                if thinking:
                                    slog.debug(f"[msg#{msg_count}] Thinking: {thinking}")
                            elif item_type == "tool_use":
                                tool_name = getattr(item, "name", "unknown")
                                tool_input = getattr(item, "input", {})
                                slog.info(f"[msg#{msg_count}] Tool use: {tool_name}({str(tool_input)[:200]})")
                            elif item_type == "tool_result":
                                tool_content = getattr(item, "content", "")
                                slog.debug(f"[msg#{msg_count}] Tool result: {str(tool_content)[:200]}")
                    else:
                        slog.debug(f"[msg#{msg_count}] Assistant content: {str(content)[:200]}")

                elif msg_type == "UserMessage":
                    slog.debug(f"[msg#{msg_count}] UserMessage")

                elif msg_type == "StreamEvent":
                    # StreamEvent contains raw API events
                    event_type = getattr(message.event, "type", None) if hasattr(message, "event") else None
                    if event_type == "content_block_delta":
                        # Don't log every delta, too noisy
                        pass
                    elif event_type:
                        slog.debug(f"[msg#{msg_count}] StreamEvent({event_type})")

                else:
                    slog.debug(f"[msg#{msg_count}] {msg_type}({subtype})")

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
