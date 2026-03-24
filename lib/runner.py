"""Claude CLI subprocess runner.

Like a waiter that delivers orders to the chef (Claude CLI),
monitors the cooking process (stream-json) in real-time,
and brings the finished dish (result) back.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# stream-json output max size (10MB)
MAX_OUTPUT_BYTES = 10 * 1024 * 1024


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


def _build_prompt(description: str, mode: str, max_iterations: int) -> str:
    """Build appropriate prompt based on issue mode.

    - feature(simple, max_iterations=1) -> /auto {description}
    - feature(complex, max_iterations>1) -> /auto-loop {description} --max-iterations N
    - bugfix -> /auto --mode bugfix {description}
    - refactor -> /auto --mode refactor {description}
    """
    if mode == "feature" and max_iterations > 1:
        return f"/auto-loop {description} --max-iterations {max_iterations}"
    elif mode in ("bugfix", "refactor"):
        return f"/auto --mode {mode} {description}"
    else:
        # Default: feature(simple) or unknown mode
        return f"/auto {description}"


def _build_command(
    prompt: str,
    worktree_path: Path,
    config: dict,
) -> list[str]:
    """Build claude CLI command."""
    model = config.get("model", "opus")
    max_budget = config.get("max_budget_usd", 5)
    allowed_tools = config.get(
        "allowed_tools",
        "Bash(*),Read(*),Write(*),Edit(*),Glob(*),Grep(*)",
    )

    cmd = [
        "claude",
        "--print",
        "--model", str(model),
        "--max-budget-usd", str(max_budget),
        "--output-format", "stream-json",
        "--allowedTools", allowed_tools,
        "-p", prompt,
    ]
    return cmd


async def _parse_stream_json(
    stream: asyncio.StreamReader,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> tuple[str, float]:
    """Parse stream-json output to extract final text and cost.

    Each line is an independent JSON object. 10MB safety limit applied.
    """
    output_parts: list[str] = []
    total_bytes = 0
    cost_usd = 0.0

    while True:
        line = await stream.readline()
        if not line:
            break

        total_bytes += len(line)
        if total_bytes > MAX_OUTPUT_BYTES:
            logger.warning("stream-json output exceeded 10MB, stopping parse")
            break

        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Skip non-JSON lines
            continue

        if on_progress is not None:
            on_progress(data)

        # Extract text from stream-json format
        msg_type = data.get("type", "")
        if msg_type == "assistant" and "content" in data:
            for block in data["content"]:
                if block.get("type") == "text":
                    output_parts.append(block["text"])
        elif msg_type == "result":
            # Extract cost from final result
            cost_usd = data.get("cost_usd", 0.0)
            if "result" in data:
                output_parts.append(data["result"])

    return "\n".join(output_parts), cost_usd


class AgentRunner:
    """Execute Claude CLI as subprocess and collect results."""

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
        """Execute claude --print and return result."""
        cmd = _build_command(prompt, worktree_path, config)
        logger.info("Agent run: %s (cwd=%s)", cmd[0:4], worktree_path)

        start = time.monotonic()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            output, cost_usd = await asyncio.wait_for(
                _parse_stream_json(proc.stdout, on_progress),
                timeout=self.timeout_s,
            )
            # Wait for process exit after stdout parsing
            await proc.wait()
        except asyncio.TimeoutError:
            logger.warning("Agent timeout (%ds), killing process", self.timeout_s)
            proc.kill()
            await proc.wait()
            duration = time.monotonic() - start
            return RunResult(
                success=False,
                output="Forced termination due to timeout",
                cost_usd=0.0,
                duration_s=duration,
                exit_code=-1,
            )

        duration = time.monotonic() - start
        exit_code = proc.returncode or 0

        return RunResult(
            success=exit_code == 0,
            output=output,
            cost_usd=cost_usd,
            duration_s=duration,
            exit_code=exit_code,
        )
