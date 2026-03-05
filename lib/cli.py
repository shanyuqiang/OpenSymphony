"""symphonyctl CLI.

비유: 관제탑의 제어판처럼, 명령어 하나로
오케스트레이터를 시작/중지하고 상태를 확인한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from lib.config import load_config, reload_config
from lib.orchestrator import Orchestrator, StateStore
from lib.runner import AgentRunner
from lib.tracker import GitHubTracker, Issue
from lib.workflow import load_workflow
from lib.workspace import WorkspaceManager

def _find_project_root() -> Path:
    """config.yaml이 있는 프로젝트 루트를 탐색한다.

    비유: 집 열쇠를 찾듯이, 현재 위치에서 위로 올라가며
    config.yaml이 있는 디렉토리를 찾는다.
    """
    # 1. 현재 스크립트 기준 (개발 환경)
    script_root = Path(__file__).parent.parent
    if (script_root / "config.yaml").exists():
        return script_root

    # 2. 현재 작업 디렉토리에서 상위로 탐색
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "config.yaml").exists():
            return parent
        if parent == parent.parent:
            break

    # 3. 홈 디렉토리 설정
    home_config = Path.home() / ".config" / "symphony-cc"
    if home_config.exists():
        return home_config

    # 4. 폴백: 스크립트 기준
    return script_root


def _get_project_root() -> Path:
    return _find_project_root()


def _get_default_config() -> Path:
    return _find_project_root() / "config.yaml"


def _get_default_workflow() -> Path:
    return _find_project_root() / "templates" / "WORKFLOW.md"


def _get_state_dir() -> Path:
    return _find_project_root() / "state"


def _get_pid_file() -> Path:
    return _get_state_dir() / "symphony.pid"


def _get_log_dir() -> Path:
    return _find_project_root() / "logs"


def _build_parser() -> argparse.ArgumentParser:
    """argparse 파서를 생성한다."""
    parser = argparse.ArgumentParser(
        prog="symphonyctl",
        description="Symphony-CC 오케스트레이션 CLI",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="설정 파일 경로 (기본: config.yaml)",
    )
    parser.add_argument(
        "--workflow", type=Path, default=None,
        help="워크플로우 템플릿 경로 (기본: templates/WORKFLOW.md)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # start
    start_p = sub.add_parser("start", help="폴링 + 오케스트레이터 시작")
    start_p.add_argument(
        "--foreground", "-f", action="store_true",
        help="포그라운드에서 실행 (데몬 모드 비활성)",
    )

    # stop
    sub.add_parser("stop", help="실행 중인 데몬 중지")

    # status
    sub.add_parser("status", help="현재 큐/활성/완료 상태 표시")

    # dispatch
    dispatch_p = sub.add_parser("dispatch", help="특정 이슈를 수동 디스패치")
    dispatch_p.add_argument("issue_number", type=int, help="이슈 번호")

    # retry
    retry_p = sub.add_parser("retry", help="실패한 이슈를 재시도")
    retry_p.add_argument("issue_number", type=int, help="이슈 번호")

    # logs
    logs_p = sub.add_parser("logs", help="로그 출력")
    logs_p.add_argument(
        "--issue", type=int, default=None,
        help="특정 이슈의 로그만 표시",
    )
    logs_p.add_argument(
        "--tail", "-n", type=int, default=50,
        help="마지막 N줄 (기본: 50)",
    )

    # dashboard
    sub.add_parser("dashboard", help="실시간 대시보드")

    # init
    init_p = sub.add_parser("init", help="프로젝트 초기화")
    init_p.add_argument(
        "--repo", type=str, default=None,
        help="GitHub 리포지토리 (owner/repo)",
    )
    init_p.add_argument(
        "--budget", type=float, default=None,
        help="이슈당 최대 예산 (USD)",
    )
    init_p.add_argument(
        "--workspace-root", type=str, default=None,
        help="워크스페이스 루트 경로",
    )
    init_p.add_argument(
        "--non-interactive", action="store_true",
        help="대화형 프롬프트 비활성화",
    )

    return parser


def _write_pid() -> None:
    """현재 프로세스의 PID를 파일에 기록한다."""
    pid_file = _get_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def _read_pid() -> int | None:
    """PID 파일에서 PID를 읽는다."""
    pid_file = _get_pid_file()
    if not pid_file.exists():
        return None
    text = pid_file.read_text(encoding="utf-8").strip()
    return int(text) if text.isdigit() else None


def _remove_pid() -> None:
    """PID 파일을 삭제한다."""
    pid_file = _get_pid_file()
    if pid_file.exists():
        pid_file.unlink()


def _make_orchestrator(
    config_path: Path,
    workflow_path: Path,
) -> tuple[Orchestrator, GitHubTracker]:
    """설정을 로드하고 Orchestrator + Tracker를 생성한다."""
    config = load_config(config_path)
    workflow = load_workflow(workflow_path)

    repo_path = Path(config.workspace.root).expanduser()
    workspace = WorkspaceManager(
        workspace_root=repo_path,
        repo_path=Path.cwd(),
    )
    runner = AgentRunner()
    tracker = GitHubTracker(repo=config.tracker.repo)

    orch = Orchestrator(
        config=config,
        workflow=workflow,
        workspace=workspace,
        runner=runner,
        state_dir=_get_state_dir(),
    )
    return orch, tracker


# --- 서브커맨드 핸들러 ---


async def _cmd_init(args: argparse.Namespace) -> int:
    """프로젝트를 Symphony-CC용으로 초기화한다."""
    from lib.init import init_project

    try:
        await init_project(
            repo=args.repo,
            budget=args.budget,
            workspace_root=getattr(args, "workspace_root", None),
            interactive=not getattr(args, "non_interactive", False),
        )
        return 0
    except (ValueError, RuntimeError) as e:
        print(f"초기화 실패: {e}", file=sys.stderr)
        return 1


async def _cmd_start(args: argparse.Namespace) -> int:
    """폴링 루프를 시작한다."""
    orch, tracker = _make_orchestrator(args.config, args.workflow)
    config = load_config(args.config)

    _write_pid()
    print(f"Symphony-CC 시작 (PID: {os.getpid()}, repo: {config.tracker.repo})")
    print(f"폴링 간격: {config.polling.interval_s}초, 동시 실행: {config.agent.max_concurrent}개")

    # SIGTERM/SIGINT 핸들러
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, orch.stop)

    try:
        while not orch.is_stopped:
            try:
                issues = await tracker.poll_ready_issues(
                    config.tracker.trigger_label
                )
                for issue in issues:
                    try:
                        orch.enqueue(issue)
                        print(f"큐 추가: #{issue.number} - {issue.title}")
                    except ValueError:
                        pass  # 이미 큐에 있음

                # 큐에서 하나씩 디스패치
                queue = orch.store.load_queue()
                tasks = []
                for record in queue:
                    issue_match = next(
                        (i for i in issues if i.number == record.issue_number),
                        None,
                    )
                    if issue_match:
                        tasks.append(orch.dispatch_one(issue_match))

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                print(f"폴링 에러: {e}", file=sys.stderr)

            # 폴링 간격 대기 (중지 신호 확인하면서)
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.sleep(config.polling.interval_s)),
                    timeout=config.polling.interval_s,
                )
            except asyncio.TimeoutError:
                pass
    finally:
        _remove_pid()
        print("Symphony-CC 종료")

    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    """실행 중인 데몬을 중지한다."""
    pid = _read_pid()
    if pid is None:
        print("실행 중인 데몬이 없습니다")
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"종료 신호 전송: PID {pid}")
        _remove_pid()
        return 0
    except ProcessLookupError:
        print(f"프로세스 {pid}가 이미 종료됨")
        _remove_pid()
        return 1


def _cmd_status(args: argparse.Namespace) -> int:
    """현재 상태를 표시한다."""
    state_dir = _get_state_dir()
    store = StateStore(state_dir)

    queue = store.load_queue()
    active = store.list_active()

    # completed 디렉토리 확인
    completed_dir = state_dir / "completed"
    completed_count = len(list(completed_dir.glob("issue-*.json"))) if completed_dir.exists() else 0

    print(f"대기: {len(queue)}개 | 활성: {len(active)}개 | 완료: {completed_count}개")

    if queue:
        print("\n[대기열]")
        for r in queue:
            print(f"  #{r.issue_number} - {r.issue_title}")

    if active:
        print("\n[활성]")
        for r in active:
            print(f"  #{r.issue_number} - {r.issue_title} ({r.state.value}, 시도 {r.attempt})")

    pid = _read_pid()
    if pid:
        print(f"\n데몬 PID: {pid}")
    else:
        print("\n데몬: 미실행")

    return 0


async def _cmd_dispatch(args: argparse.Namespace) -> int:
    """특정 이슈를 수동으로 디스패치한다."""
    orch, tracker = _make_orchestrator(args.config, args.workflow)
    config = load_config(args.config)

    # gh에서 이슈 정보 가져오기
    print(f"이슈 #{args.issue_number} 디스패치 중...")
    issues = await tracker.poll_ready_issues(config.tracker.trigger_label)
    issue = next(
        (i for i in issues if i.number == args.issue_number),
        None,
    )
    if issue is None:
        # 라벨 없이도 디스패치 가능하도록 직접 생성
        issue = Issue(
            number=args.issue_number,
            title=f"Issue #{args.issue_number}",
            body="",
        )

    record = await orch.dispatch_one(issue)
    print(f"결과: {record.state.value} (시도 {record.attempt}, 비용 ${record.cost_usd:.2f})")
    return 0 if record.state.value in ("SUCCEEDED", "PR_CREATED") else 1


async def _cmd_retry(args: argparse.Namespace) -> int:
    """실패한 이슈를 재시도한다."""
    state_dir = _get_state_dir()
    store = StateStore(state_dir)
    completed_path = state_dir / "completed" / f"issue-{args.issue_number}.json"

    if not completed_path.exists():
        print(f"이슈 #{args.issue_number}의 완료 기록이 없습니다")
        return 1

    # completed에서 제거하고 다시 dispatch
    completed_path.unlink()
    print(f"이슈 #{args.issue_number} 재시도를 위해 다시 디스패치합니다")

    orch, _ = _make_orchestrator(args.config, args.workflow)
    issue = Issue(
        number=args.issue_number,
        title=f"Issue #{args.issue_number} (재시도)",
        body="",
    )
    record = await orch.dispatch_one(issue)
    print(f"결과: {record.state.value}")
    return 0 if record.state.value in ("SUCCEEDED", "PR_CREATED") else 1


def _cmd_logs(args: argparse.Namespace) -> int:
    """로그를 출력한다."""
    log_dir = _get_log_dir()
    if args.issue:
        log_file = log_dir / f"issue-{args.issue}" / "agent.log"
    else:
        log_file = log_dir / "daemon.log"

    if not log_file.exists():
        print(f"로그 파일 없음: {log_file}")
        return 1

    lines = log_file.read_text(encoding="utf-8").splitlines()
    for line in lines[-args.tail:]:
        print(line)
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """실시간 대시보드를 표시한다."""
    from lib.dashboard import DashboardApp
    app = DashboardApp(state_dir=_get_state_dir())
    app.run()
    return 0


# --- 메인 진입점 ---


def main(argv: list[str] | None = None) -> int:
    """CLI 메인 함수."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.config is None:
        args.config = _get_default_config()
    if args.workflow is None:
        args.workflow = _get_default_workflow()

    cmd = args.command

    if cmd == "stop":
        return _cmd_stop(args)
    elif cmd == "status":
        return _cmd_status(args)
    elif cmd == "logs":
        return _cmd_logs(args)
    elif cmd == "dashboard":
        return _cmd_dashboard(args)
    elif cmd == "start":
        return asyncio.run(_cmd_start(args))
    elif cmd == "dispatch":
        return asyncio.run(_cmd_dispatch(args))
    elif cmd == "retry":
        return asyncio.run(_cmd_retry(args))
    elif cmd == "init":
        return asyncio.run(_cmd_init(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
