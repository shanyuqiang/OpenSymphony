"""Symphony CLI entry point."""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(
    name="symphony",
    help="Symphony — Issue tracker to coding agent orchestrator.",
    add_completion=False,
)

_VERSION = "0.1.0"


@app.command()
def run(
    workflow_file: Annotated[
        Path,
        typer.Argument(
            help="Path to WORKFLOW.md",
            exists=True,
            file_okay=True,
            dir_okay=False,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate config only, do not start orchestrator."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
    dashboard: Annotated[
        Optional[int],
        typer.Option("--dashboard", help="Enable HTTP dashboard on this port."),
    ] = None,
) -> None:
    """Run the Symphony orchestrator."""
    from symphony.logging_config import configure_logging

    configure_logging(verbose=verbose)

    from symphony.workflow import WorkflowLoader

    loader = WorkflowLoader()
    try:
        workflow = loader.load(workflow_file)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(1)

    if dry_run:
        typer.echo("Config valid.")
        typer.echo(f"  Tracker: {workflow.config.tracker.kind} → "
                   f"{workflow.config.tracker.owner}/{workflow.config.tracker.repo}")
        typer.echo(f"  Polling: {workflow.config.polling.interval_ms} ms")
        typer.echo(f"  Max concurrent: {workflow.config.agent.max_concurrent_agents}")
        typer.echo(f"  Max turns: {workflow.config.agent.max_turns}")
        raise typer.Exit(0)

    asyncio.run(_run_async(workflow, dashboard_port=dashboard or _dashboard_port(workflow)))


def _dashboard_port(workflow) -> int | None:
    if workflow.config.server:
        return workflow.config.server.port
    return None


async def _run_async(workflow, dashboard_port: int | None) -> None:
    import logging

    from symphony.orchestrator import Orchestrator

    logger = logging.getLogger("symphony.cli")

    orch = Orchestrator(workflow.config, workflow)

    dashboard_server = None
    if dashboard_port is not None:
        from symphony.server.dashboard import DashboardServer

        dashboard_server = DashboardServer(orch, port=dashboard_port)
        await dashboard_server.start()
        logger.info("Dashboard running on http://0.0.0.0:%d", dashboard_port)

    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        orch.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows fallback
            pass

    try:
        await orch.start()
    finally:
        if dashboard_server:
            await dashboard_server.stop()


@app.command()
def validate(
    workflow_file: Annotated[
        Path,
        typer.Argument(help="Path to WORKFLOW.md"),
    ],
) -> None:
    """Validate a WORKFLOW.md file without starting the orchestrator."""
    from symphony.workflow import WorkflowLoader

    loader = WorkflowLoader()
    try:
        workflow = loader.load(workflow_file)
        typer.echo("✓ Config valid")
        typer.echo(f"  Tracker: {workflow.config.tracker.kind} → "
                   f"{workflow.config.tracker.owner}/{workflow.config.tracker.repo}")
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print Symphony version."""
    typer.echo(f"symphony {_VERSION}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
