"""Claude CLI subprocess wrapper.

Spawns the `claude` binary, streams NDJSON events, and returns a ClaudeResult.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from symphony.config import ClaudeConfig
from symphony.models import ClaudeResult, TokenCounts


class ClaudeCLIError(Exception):
    """Raised when the claude subprocess fails unexpectedly."""


async def _read_line_with_timeout(
    stream: asyncio.StreamReader,
    timeout_s: float,
) -> bytes | None:
    """Read one line; return None on EOF, raise asyncio.TimeoutError on timeout."""
    try:
        return await asyncio.wait_for(stream.readline(), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise


def _build_command(config: ClaudeConfig, prompt: str, max_turns: int) -> list[str]:
    cmd: list[str] = [config.command]
    cmd += ["--output-format", "stream-json"]
    cmd += ["--max-turns", str(max_turns)]
    if config.dangerous_mode:
        cmd += ["--dangerously-skip-permissions"]
    if config.allowed_tools:
        cmd += ["--allowedTools", ",".join(config.allowed_tools)]
    cmd += ["-p", prompt]
    return cmd


async def run_claude(
    prompt: str,
    workspace: Path,
    config: ClaudeConfig,
    max_turns: int,
) -> ClaudeResult:
    """Run the claude CLI in *workspace* and return a ClaudeResult."""
    cmd = _build_command(config, prompt, max_turns)
    turn_timeout_s = config.turn_timeout_ms / 1000
    stall_timeout_s = config.stall_timeout_ms / 1000

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return ClaudeResult(
            success=False,
            error=f"claude binary not found: {config.command!r}",
        )
    except Exception as exc:  # noqa: BLE001
        return ClaudeResult(success=False, error=f"Failed to start claude: {exc}")

    events: list[dict] = []
    token_usage = TokenCounts()
    final_result: dict = {}
    succeeded = False
    stderr_lines: list[str] = []

    async def _collect_stderr() -> None:
        assert proc.stderr is not None
        async for line in proc.stderr:
            stderr_lines.append(line.decode(errors="replace").rstrip())

    stderr_task = asyncio.create_task(_collect_stderr())

    assert proc.stdout is not None

    try:
        async with asyncio.timeout(turn_timeout_s):
            while True:
                try:
                    raw = await _read_line_with_timeout(proc.stdout, stall_timeout_s)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    await stderr_task
                    return ClaudeResult(
                        success=False,
                        events=events,
                        error="Stall timeout: no output from claude",
                        stderr="\n".join(stderr_lines) or None,
                    )

                if not raw:
                    break  # EOF

                line = raw.decode(errors="replace").strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                events.append(event)
                event_type = event.get("type")

                if event_type == "result":
                    final_result = event
                    subtype = event.get("subtype", "")
                    succeeded = subtype == "success"
                    usage = event.get("usage") or {}
                    token_usage = TokenCounts(
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        total_tokens=usage.get("input_tokens", 0)
                        + usage.get("output_tokens", 0),
                    )

    except TimeoutError:
        proc.kill()
        await proc.wait()
        await stderr_task
        return ClaudeResult(
            success=False,
            events=events,
            error=f"Turn timeout ({config.turn_timeout_ms} ms) exceeded",
            stderr="\n".join(stderr_lines) or None,
        )

    await proc.wait()
    await stderr_task

    if not succeeded and not events:
        stderr_text = "\n".join(stderr_lines) or None
        return ClaudeResult(
            success=False,
            error=f"claude exited with code {proc.returncode} and no events",
            stderr=stderr_text,
        )

    return ClaudeResult(
        success=succeeded,
        events=events,
        final_result=final_result,
        token_usage=token_usage,
        stderr="\n".join(stderr_lines) or None,
    )
