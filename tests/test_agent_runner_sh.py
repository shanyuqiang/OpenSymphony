"""agent-runner.sh 테스트.

bash 스크립트의 동시 실행 제어, PID 관리, cleanup 등을 검증한다.
claude CLI가 없는 환경에서도 테스트할 수 있도록 mock을 사용한다.
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "agent-runner.sh"
PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def env_setup(tmp_path: Path):
    """테스트용 worktree 디렉토리와 환경을 준비한다."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    # mock claude 스크립트: 단순히 echo하고 종료
    mock_claude = tmp_path / "claude"
    mock_claude.write_text(
        '#!/bin/bash\necho \'{"type":"result","result":"mock","cost_usd":0.1}\'\n'
    )
    mock_claude.chmod(0o755)

    # PATH에 mock claude를 우선 배치
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + ":" + env.get("PATH", "")

    return worktree, env


def test_script_exists_and_is_executable():
    """스크립트가 존재하고 실행 가능한지 확인."""
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK)


def test_missing_arguments():
    """인자 부족 시 에러 코드를 반환한다."""
    result = subprocess.run(
        [str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "사용법" in result.stderr


def test_invalid_worktree_path(env_setup):
    """존재하지 않는 worktree 경로에서 에러를 반환한다."""
    _, env = env_setup
    result = subprocess.run(
        [str(SCRIPT), "999", "/nonexistent/path", "test prompt"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode != 0
    assert "존재하지 않습니다" in result.stderr


def test_successful_run(env_setup):
    """정상 실행 시 PID 파일 생성 후 종료 시 정리된다."""
    worktree, env = env_setup

    result = subprocess.run(
        [str(SCRIPT), "42", str(worktree), "test prompt"],
        capture_output=True, text=True, env=env,
        timeout=30,
    )

    assert result.returncode == 0

    # PID 파일이 cleanup으로 삭제됨
    pid_file = PROJECT_ROOT / "state" / "active" / "issue-42.pid"
    assert not pid_file.exists()

    # lock 디렉토리도 정리됨
    lock_dir = PROJECT_ROOT / "state" / "active" / "issue-42.lock"
    assert not lock_dir.exists()

    # 로그 파일 생성됨
    log_file = PROJECT_ROOT / "logs" / "issue-42" / "agent.log"
    assert log_file.exists()
    log_content = log_file.read_text()
    assert "에이전트 시작" in log_content
    assert "에이전트 종료" in log_content


def test_concurrent_execution_blocked(env_setup):
    """동일 이슈의 동시 실행이 차단된다."""
    worktree, env = env_setup

    # 느린 mock claude: 5초 sleep
    slow_claude = Path(env["PATH"].split(":")[0]) / "claude"
    slow_claude.write_text(
        '#!/bin/bash\nsleep 5\necho \'{"type":"result","result":"done","cost_usd":0.1}\'\n'
    )
    slow_claude.chmod(0o755)

    # 첫 번째 실행 (백그라운드)
    proc1 = subprocess.Popen(
        [str(SCRIPT), "100", str(worktree), "first run"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )

    # 잠금이 걸릴 때까지 잠깐 대기
    import time
    time.sleep(1)

    # 두 번째 실행 (동일 이슈)
    result2 = subprocess.run(
        [str(SCRIPT), "100", str(worktree), "second run"],
        capture_output=True, text=True, env=env,
        timeout=10,
    )

    # 두 번째 실행은 차단됨
    assert result2.returncode != 0
    assert "이미 실행 중" in result2.stderr

    # 첫 번째 프로세스 정리
    proc1.terminate()
    proc1.wait(timeout=10)


def test_log_directory_created(env_setup):
    """이슈별 로그 디렉토리가 자동 생성된다."""
    worktree, env = env_setup

    subprocess.run(
        [str(SCRIPT), "77", str(worktree), "log test"],
        capture_output=True, text=True, env=env,
        timeout=30,
    )

    log_dir = PROJECT_ROOT / "logs" / "issue-77"
    assert log_dir.exists()


def test_custom_options(env_setup):
    """커스텀 옵션이 전달된다."""
    worktree, env = env_setup

    # mock claude가 인자를 로그 파일에 기록
    mock_claude = Path(env["PATH"].split(":")[0]) / "claude"
    mock_claude.write_text(
        '#!/bin/bash\necho "$@" > /tmp/claude_args_test.txt\n'
        'echo \'{"type":"result","result":"ok","cost_usd":0.1}\'\n'
    )
    mock_claude.chmod(0o755)

    result = subprocess.run(
        [
            str(SCRIPT), "55", str(worktree), "custom test",
            "--model", "sonnet",
            "--max-budget", "10",
        ],
        capture_output=True, text=True, env=env,
        timeout=30,
    )

    assert result.returncode == 0

    # claude에 전달된 인자 확인
    args_file = Path("/tmp/claude_args_test.txt")
    if args_file.exists():
        args_content = args_file.read_text()
        assert "sonnet" in args_content
        assert "10" in args_content
        args_file.unlink()


def test_unknown_option(env_setup):
    """알 수 없는 옵션 시 에러."""
    worktree, env = env_setup

    result = subprocess.run(
        [str(SCRIPT), "1", str(worktree), "test", "--unknown-opt", "val"],
        capture_output=True, text=True, env=env,
        timeout=10,
    )

    assert result.returncode != 0
    assert "알 수 없는 옵션" in result.stderr
