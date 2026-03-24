"""AgentRunner 테스트.

실제 claude CLI 대신 mock subprocess를 사용하여
명령어 조립, stream-json 파싱, 타임아웃 등을 검증한다.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lib.runner import (
    AgentRunner,
    RunResult,
    _build_command,
    _build_prompt,
    _parse_stream_json,
    MAX_OUTPUT_BYTES,
)


# --- _build_prompt 테스트 ---

def test_build_prompt_simple_feature():
    """단순 feature는 /auto를 사용한다."""
    result = _build_prompt("로그인 구현", "feature", 1)
    assert result == "/auto 로그인 구현"


def test_build_prompt_complex_feature():
    """복합 feature(max_iterations>1)는 /auto-loop를 사용한다."""
    result = _build_prompt("결제 시스템 구현", "feature", 5)
    assert result == "/auto-loop 결제 시스템 구현 --max-iterations 5"


def test_build_prompt_bugfix():
    """bugfix mode는 /auto --mode bugfix를 사용한다."""
    result = _build_prompt("NullPointer 수정", "bugfix", 1)
    assert result == "/auto --mode bugfix NullPointer 수정"


def test_build_prompt_refactor():
    """refactor mode는 /auto --mode refactor를 사용한다."""
    result = _build_prompt("utils 분리", "refactor", 1)
    assert result == "/auto --mode refactor utils 분리"


def test_build_prompt_unknown_mode_defaults_to_auto():
    """알 수 없는 mode는 /auto로 기본 처리한다."""
    result = _build_prompt("뭔가 작업", "unknown", 1)
    assert result == "/auto 뭔가 작업"


# --- _build_command 테스트 ---

def test_build_command_default_config():
    """기본 config로 올바른 CLI 명령을 조립한다."""
    cmd = _build_command("/auto 테스트", Path("/tmp/wt"), {})

    assert cmd[0] == "claude"
    assert "--print" in cmd
    assert "--model" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "-p" in cmd
    assert "/auto 테스트" in cmd


def test_build_command_custom_config():
    """커스텀 config 값이 반영된다."""
    config = {
        "model": "sonnet",
        "max_budget_usd": 10,
        "allowed_tools": "Bash(*),Read(*)",
    }
    cmd = _build_command("/auto 작업", Path("/tmp/wt"), config)

    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "sonnet"

    budget_idx = cmd.index("--max-budget-usd")
    assert cmd[budget_idx + 1] == "10"

    tools_idx = cmd.index("--allowedTools")
    assert cmd[tools_idx + 1] == "Bash(*),Read(*)"


# --- _parse_stream_json 테스트 ---

async def _make_stream(lines: list[str]) -> asyncio.StreamReader:
    """테스트용 StreamReader를 생성한다."""
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data((line + "\n").encode())
    reader.feed_eof()
    return reader


async def test_parse_stream_json_extracts_text():
    """assistant 메시지에서 텍스트를 추출한다."""
    data = {
        "type": "assistant",
        "content": [{"type": "text", "text": "작업 완료했습니다."}],
    }
    stream = await _make_stream([json.dumps(data)])

    output, cost = await _parse_stream_json(stream)

    assert "작업 완료했습니다." in output
    assert cost == 0.0


async def test_parse_stream_json_extracts_cost():
    """result 메시지에서 비용을 추출한다."""
    data = {"type": "result", "result": "최종 결과", "cost_usd": 1.23}
    stream = await _make_stream([json.dumps(data)])

    output, cost = await _parse_stream_json(stream)

    assert "최종 결과" in output
    assert cost == 1.23


async def test_parse_stream_json_ignores_invalid_json():
    """JSON이 아닌 줄은 무시한다."""
    stream = await _make_stream([
        "이건 JSON이 아님",
        json.dumps({"type": "result", "result": "OK", "cost_usd": 0.5}),
    ])

    output, cost = await _parse_stream_json(stream)

    assert "OK" in output


async def test_parse_stream_json_progress_callback():
    """진행 상태 콜백이 호출된다."""
    data = {"type": "result", "result": "done", "cost_usd": 0.1}
    stream = await _make_stream([json.dumps(data)])

    received = []
    await _parse_stream_json(stream, on_progress=lambda d: received.append(d))

    assert len(received) == 1
    assert received[0]["type"] == "result"


async def test_parse_stream_json_respects_size_limit():
    """10MB 초과 시 파싱을 중단한다."""
    # 큰 데이터를 여러 줄로 생성
    big_text = "x" * 1000
    big_data = json.dumps({"type": "assistant", "content": [{"type": "text", "text": big_text}]})
    # MAX_OUTPUT_BYTES / len(big_data) + 여유분만큼 반복
    line_count = (MAX_OUTPUT_BYTES // len(big_data)) + 100
    stream = await _make_stream([big_data] * line_count)

    output, _ = await _parse_stream_json(stream)

    # 일부만 파싱됨 (전부 읽지 않음)
    assert len(output) < len(big_text) * line_count


# --- AgentRunner.run 테스트 ---

async def test_runner_success():
    """정상 실행 시 RunResult를 반환한다."""
    result_json = json.dumps({
        "type": "result",
        "result": "구현 완료",
        "cost_usd": 2.50,
    })

    runner = AgentRunner(timeout_s=30)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.stdout = await _make_stream([result_json])
    mock_proc.stderr = asyncio.StreamReader()
    mock_proc.stderr.feed_eof()
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("lib.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await runner.run("/auto 테스트", Path("/tmp/wt"), {})

    assert result.success is True
    assert "구현 완료" in result.output
    assert result.cost_usd == 2.50
    assert result.exit_code == 0
    assert result.duration_s > 0


async def test_runner_failure():
    """비정상 종료 시 success=False."""
    runner = AgentRunner(timeout_s=30)

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.stdout = await _make_stream([])
    mock_proc.stderr = asyncio.StreamReader()
    mock_proc.stderr.feed_eof()
    mock_proc.wait = AsyncMock(return_value=1)

    with patch("lib.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await runner.run("/auto 실패", Path("/tmp/wt"), {})

    assert result.success is False
    assert result.exit_code == 1


async def test_runner_timeout():
    """타임아웃 시 강제 종료하고 적절한 결과를 반환한다."""
    runner = AgentRunner(timeout_s=1)

    mock_proc = AsyncMock()
    mock_proc.returncode = -9

    # stdout readline이 영원히 블록되도록 시뮬레이션
    never_ending = asyncio.StreamReader()
    # EOF를 보내지 않아서 readline이 계속 대기
    mock_proc.stdout = never_ending
    mock_proc.stderr = asyncio.StreamReader()
    mock_proc.stderr.feed_eof()
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock(return_value=-9)

    with patch("lib.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await runner.run("/auto 느린작업", Path("/tmp/wt"), {})

    assert result.success is False
    assert result.exit_code == -1
    assert "타임아웃" in result.output
    mock_proc.kill.assert_called_once()


async def test_runner_with_progress_callback():
    """진행 콜백이 runner를 통해 전달된다."""
    data = json.dumps({"type": "result", "result": "done", "cost_usd": 0.1})
    runner = AgentRunner(timeout_s=30)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.stdout = await _make_stream([data])
    mock_proc.stderr = asyncio.StreamReader()
    mock_proc.stderr.feed_eof()
    mock_proc.wait = AsyncMock(return_value=0)

    progress_events = []

    with patch("lib.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await runner.run(
            "/auto 콜백테스트",
            Path("/tmp/wt"),
            {},
            on_progress=lambda d: progress_events.append(d),
        )

    assert len(progress_events) == 1
    assert result.success is True
