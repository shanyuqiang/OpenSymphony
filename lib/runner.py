"""Claude CLI subprocess 러너.

비유: 레스토랑의 주방장(Claude CLI)에게 주문서(프롬프트)를 전달하고,
요리 과정(stream-json)을 실시간으로 모니터링하며,
완성된 요리(결과)를 받아오는 웨이터 역할.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# stream-json 출력 최대 크기 (10MB)
MAX_OUTPUT_BYTES = 10 * 1024 * 1024


@dataclass
class RunResult:
    """에이전트 실행 결과."""
    success: bool
    output: str
    cost_usd: float
    duration_s: float
    exit_code: int


class RunnerError(Exception):
    """러너 관련 에러."""


def _build_prompt(description: str, mode: str, max_iterations: int) -> str:
    """이슈 mode에 따라 적절한 프롬프트를 조립한다.

    - feature(단순, max_iterations=1) -> /auto {description}
    - feature(복합, max_iterations>1) -> /auto-loop {description} --max-iterations N
    - bugfix -> /auto --mode bugfix {description}
    - refactor -> /auto --mode refactor {description}
    """
    if mode == "feature" and max_iterations > 1:
        return f"/auto-loop {description} --max-iterations {max_iterations}"
    elif mode in ("bugfix", "refactor"):
        return f"/auto --mode {mode} {description}"
    else:
        # 기본: feature(단순) 또는 알 수 없는 mode
        return f"/auto {description}"


def _build_command(
    prompt: str,
    worktree_path: Path,
    config: dict,
) -> list[str]:
    """claude CLI 명령어를 조립한다."""
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
    """stream-json 출력을 파싱하여 최종 텍스트와 비용을 추출한다.

    각 줄은 독립된 JSON 객체. 10MB 안전 한도 적용.
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
            logger.warning("stream-json 출력이 10MB를 초과, 파싱 중단")
            break

        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # JSON이 아닌 줄은 무시
            continue

        if on_progress is not None:
            on_progress(data)

        # stream-json 형식에서 텍스트 추출
        msg_type = data.get("type", "")
        if msg_type == "assistant" and "content" in data:
            for block in data["content"]:
                if block.get("type") == "text":
                    output_parts.append(block["text"])
        elif msg_type == "result":
            # 최종 결과에서 비용 추출
            cost_usd = data.get("cost_usd", 0.0)
            if "result" in data:
                output_parts.append(data["result"])

    return "\n".join(output_parts), cost_usd


class AgentRunner:
    """Claude CLI를 subprocess로 실행하고 결과를 수집한다."""

    def __init__(self, timeout_s: int = 600) -> None:
        self.timeout_s = timeout_s

    async def run(
        self,
        prompt: str,
        worktree_path: Path,
        config: dict,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> RunResult:
        """claude --print를 실행하고 결과를 반환한다."""
        cmd = _build_command(prompt, worktree_path, config)
        logger.info("에이전트 실행: %s (cwd=%s)", cmd[0:4], worktree_path)

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
            # stdout 파싱 완료 후 프로세스 종료 대기
            await proc.wait()
        except asyncio.TimeoutError:
            logger.warning("에이전트 타임아웃 (%ds), 프로세스 종료", self.timeout_s)
            proc.kill()
            await proc.wait()
            duration = time.monotonic() - start
            return RunResult(
                success=False,
                output="타임아웃으로 인한 강제 종료",
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
