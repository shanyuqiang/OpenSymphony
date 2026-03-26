"""symphonyctl CLI.

Control panel for the orchestrator - start/stop and check status.
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
from lib.claude_sdk_runner import SDKAgentRunner
from lib.tracker import GitHubTracker, Issue
from lib.workflow import load_workflow
from lib.workspace import WorkspaceManager


def _find_project_root() -> Path:
    """Find project root containing config.yaml.

    Search upward from current directory to find config.yaml.
    """
    # 1. Based on current script (dev environment)
    script_root = Path(__file__).parent.parent
    if (script_root / "config.yaml").exists():
        return script_root

    # 2. Search upward from current working directory
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "config.yaml").exists():
            return parent
        if parent == parent.parent:
            break

    # 3. Home directory config
    home_config = Path.home() / ".config" / "symphony-cc"
    if home_config.exists():
        return home_config

    # 4. Fallback: script based
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
    """Build argparse parser."""
    parser = argparse.ArgumentParser(
        prog="symphonyctl",
        description="Symphony-CC orchestration CLI",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Config file path (default: config.yaml)",
    )
    parser.add_argument(
        "--workflow", type=Path, default=None,
        help="Workflow template path (default: templates/WORKFLOW.md)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # start
    start_p = sub.add_parser("start", help="Start polling + orchestrator")
    start_p.add_argument(
        "--foreground", "-f", action="store_true",
        help="Run in foreground (disable daemon mode)",
    )

    # stop
    sub.add_parser("stop", help="Stop running daemon")

    # status
    sub.add_parser("status", help="Show queue/active/completed status")

    # dispatch
    dispatch_p = sub.add_parser("dispatch", help="Manually dispatch an issue")
    dispatch_p.add_argument("issue_number", type=int, help="Issue number")

    # retry
    retry_p = sub.add_parser("retry", help="Retry a failed issue")
    retry_p.add_argument("issue_number", type=int, help="Issue number")

    # logs
    logs_p = sub.add_parser("logs", help="Show logs")
    logs_p.add_argument(
        "--issue", type=int, default=None,
        help="Show logs for specific issue only",
    )
    logs_p.add_argument(
        "--tail", "-n", type=int, default=50,
        help="Last N lines (default: 50)",
    )

    # dashboard
    sub.add_parser("dashboard", help="Real-time dashboard")

    # init
    init_p = sub.add_parser("init", help="Initialize project")
    init_p.add_argument(
        "--repo", type=str, default=None,
        help="GitHub repository (owner/repo)",
    )
    init_p.add_argument(
        "--budget", type=float, default=None,
        help="Max budget per issue (USD)",
    )
    init_p.add_argument(
        "--workspace-root", type=str, default=None,
        help="Workspace root path",
    )
    init_p.add_argument(
        "--non-interactive", action="store_true",
        help="Disable interactive prompts",
    )

    return parser


def _write_pid() -> None:
    """Write current process PID to file."""
    pid_file = _get_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def _read_pid() -> int | None:
    """Read PID from file."""
    pid_file = _get_pid_file()
    if not pid_file.exists():
        return None
    text = pid_file.read_text(encoding="utf-8").strip()
    return int(text) if text.isdigit() else None


def _remove_pid() -> None:
    """Remove PID file."""
    pid_file = _get_pid_file()
    if pid_file.exists():
        pid_file.unlink()


def _make_orchestrator(
    config_path: Path,
    workflow_path: Path,
) -> tuple[Orchestrator, GitHubTracker]:
    """Load config and create Orchestrator + Tracker."""
    config = load_config(config_path)
    workflow = load_workflow(workflow_path)

    repo_path = Path(config.workspace.root).expanduser()
    workspace = WorkspaceManager(
        workspace_root=repo_path,
        repo_path=Path.cwd(),
        tracker_repo=config.tracker.repo,
    )

    runner = SDKAgentRunner()

    tracker = GitHubTracker(repo=config.tracker.repo)

    orch = Orchestrator(
        config=config,
        workflow=workflow,
        workspace=workspace,
        runner=runner,
        state_dir=_get_state_dir(),
        tracker=tracker,
    )
    return orch, tracker


# --- Subcommand handlers ---


async def _cmd_init(args: argparse.Namespace) -> int:
    """Initialize project for Symphony-CC."""
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
        print(f"Initialization failed: {e}", file=sys.stderr)
        return 1


async def _cmd_start(args: argparse.Namespace) -> int:
    """Start polling loop."""
    orch, tracker = _make_orchestrator(args.config, args.workflow)
    config = load_config(args.config)

    # Clean up orphaned active records (e.g., from crash/restart during post-processing)
    def _log_cleanup(issue_number: int, msg: str) -> None:
        print(f"  [cleanup] {msg}")

    orphaned = orch.store.cleanup_orphaned(_log_cleanup)
    if orphaned:
        print(f"  Cleaned up {len(orphaned)} orphaned record(s): {', '.join(f'#{n}' for n in orphaned)}")

    _write_pid()
    print(f"Symphony-CC started (PID: {os.getpid()}, repo: {config.tracker.repo})")
    print(f"Polling interval: {config.polling.interval_s}s, concurrent: {config.agent.max_concurrent}")

    # SIGTERM/SIGINT handler
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
                        print(f"Queued: #{issue.number} - {issue.title}")
                    except ValueError:
                        pass  # Already in queue

                # Dispatch one by one from queue
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
                print(f"Polling error: {e}", file=sys.stderr)

            # Wait for polling interval (checking stop signal)
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.sleep(config.polling.interval_s)),
                    timeout=config.polling.interval_s,
                )
            except asyncio.TimeoutError:
                pass
    finally:
        _remove_pid()
        print("Symphony-CC stopped")

    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    """Stop running daemon."""
    pid = _read_pid()
    if pid is None:
        print("No running daemon")
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Termination signal sent: PID {pid}")
        _remove_pid()
        return 0
    except ProcessLookupError:
        print(f"Process {pid} already terminated")
        _remove_pid()
        return 1


def _cmd_status(args: argparse.Namespace) -> int:
    """Show current status."""
    state_dir = _get_state_dir()
    store = StateStore(state_dir)

    queue = store.load_queue()
    active = store.list_active()

    # Check completed directory
    completed_dir = state_dir / "completed"
    completed_count = len(list(completed_dir.glob("issue-*.json"))) if completed_dir.exists() else 0

    print(f"Queued: {len(queue)} | Active: {len(active)} | Completed: {completed_count}")

    if queue:
        print("\n[Queue]")
        for r in queue:
            print(f"  #{r.issue_number} - {r.issue_title}")

    if active:
        print("\n[Active]")
        for r in active:
            print(f"  #{r.issue_number} - {r.issue_title} ({r.state.value}, attempt {r.attempt})")

    pid = _read_pid()
    if pid:
        print(f"\nDaemon PID: {pid}")
    else:
        print("\nDaemon: not running")

    return 0


async def _cmd_dispatch(args: argparse.Namespace) -> int:
    """Manually dispatch a specific issue."""
    orch, tracker = _make_orchestrator(args.config, args.workflow)
    config = load_config(args.config)

    # Get issue info from gh
    print(f"Dispatching issue #{args.issue_number}...")
    issues = await tracker.poll_ready_issues(config.tracker.trigger_label)
    issue = next(
        (i for i in issues if i.number == args.issue_number),
        None,
    )
    if issue is None:
        # Allow dispatch without label by creating directly
        issue = Issue(
            number=args.issue_number,
            title=f"Issue #{args.issue_number}",
            body="",
        )

    record = await orch.dispatch_one(issue)
    print(f"Result: {record.state.value} (attempt {record.attempt}, cost ${record.cost_usd:.2f})")
    return 0 if record.state.value in ("SUCCEEDED", "PR_CREATED") else 1


async def _cmd_retry(args: argparse.Namespace) -> int:
    """Retry a failed issue."""
    state_dir = _get_state_dir()
    store = StateStore(state_dir)
    completed_path = state_dir / "completed" / f"issue-{args.issue_number}.json"

    if not completed_path.exists():
        print(f"No completed record for issue #{args.issue_number}")
        return 1

    # Remove from completed and dispatch again
    completed_path.unlink()
    print(f"Retrying issue #{args.issue_number}")

    orch, _ = _make_orchestrator(args.config, args.workflow)
    issue = Issue(
        number=args.issue_number,
        title=f"Issue #{args.issue_number} (retry)",
        body="",
    )
    record = await orch.dispatch_one(issue)
    print(f"Result: {record.state.value}")
    return 0 if record.state.value in ("SUCCEEDED", "PR_CREATED") else 1


def _cmd_logs(args: argparse.Namespace) -> int:
    """Show logs."""
    log_dir = _get_log_dir()
    if args.issue:
        log_file = log_dir / f"issue-{args.issue}" / "agent.log"
    else:
        log_file = log_dir / "daemon.log"

    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        return 1

    lines = log_file.read_text(encoding="utf-8").splitlines()
    for line in lines[-args.tail:]:
        print(line)
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Show real-time dashboard."""
    from lib.dashboard import DashboardApp

    app = DashboardApp(state_dir=_get_state_dir())
    app.run()
    return 0


# --- Main entry point ---


def main(argv: list[str] | None = None) -> int:
    """CLI main function."""
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
