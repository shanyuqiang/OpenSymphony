"""Rich TUI Dashboard.

Real-time monitoring of active issues, queue, and completed/failed states
like an air traffic control tower.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


_DEFAULT_STATE_DIR = Path(__file__).parent.parent / "state"


@dataclass
class IssueState:
    """Issue state data."""

    issue_number: int
    title: str = ""
    status: str = "QUEUED"
    started_at: str | None = None
    completed_at: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    attempt: int = 0
    max_retries: int = 3


def _load_json(path: Path) -> Any:
    """Load JSON file safely. Returns None on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _elapsed_str(started_at: str | None) -> str:
    """Format elapsed time from start time in human-readable form."""
    if not started_at:
        return "-"
    try:
        start = datetime.fromisoformat(started_at)
        now = datetime.now(timezone.utc)
        delta = now - start
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"
    except (ValueError, TypeError):
        return "-"


class StateReader:
    """Read issue states from state/ directory.

    Like a clerk organizing files from folders to a status board.
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or _DEFAULT_STATE_DIR

    def read_queue(self) -> list[IssueState]:
        """Read queued issues."""
        data = _load_json(self.state_dir / "queue.json")
        if not isinstance(data, list):
            return []
        return [
            IssueState(
                issue_number=item.get("issue_number", 0),
                title=item.get("issue_title", item.get("title", "")),
                status=item.get("status", "QUEUED"),
            )
            for item in data
        ]

    def read_active(self) -> list[IssueState]:
        """Read active issues."""
        active_dir = self.state_dir / "active"
        if not active_dir.is_dir():
            return []

        results: list[IssueState] = []
        for path in sorted(active_dir.glob("issue-*.json")):
            data = _load_json(path)
            if not isinstance(data, dict):
                continue
            results.append(
                IssueState(
                    issue_number=data.get("issue_number", 0),
                    title=data.get("issue_title", data.get("title", "")),
                    status=data.get("state", data.get("status", "RUNNING")),
                    started_at=data.get("started_at"),
                    cost_usd=data.get("cost_usd", 0.0),
                    attempt=data.get("attempt", 0),
                    max_retries=data.get("max_retries", 3),
                )
            )
        return results

    def read_completed(self) -> list[IssueState]:
        """Read completed/failed issues."""
        completed_dir = self.state_dir / "completed"
        if not completed_dir.is_dir():
            return []

        results: list[IssueState] = []
        for path in sorted(completed_dir.glob("issue-*.json")):
            data = _load_json(path)
            if not isinstance(data, dict):
                continue
            results.append(
                IssueState(
                    issue_number=data.get("issue_number", 0),
                    title=data.get("issue_title", data.get("title", "")),
                    status=data.get("state", data.get("status", "SUCCEEDED")),
                    started_at=data.get("started_at"),
                    completed_at=data.get("completed_at"),
                    cost_usd=data.get("cost_usd", 0.0),
                    error=data.get("error"),
                )
            )
        return results

    def read_all(self) -> dict[str, list[IssueState]]:
        """Read all states at once."""
        return {
            "queue": self.read_queue(),
            "active": self.read_active(),
            "completed": self.read_completed(),
        }


def build_summary_panel(
    active: list[IssueState],
    queue: list[IssueState],
    completed: list[IssueState],
) -> Panel:
    """Build top summary panel.

    Shows key metrics like a scoreboard at the top of the dashboard.
    """
    succeeded = [c for c in completed if c.status == "SUCCEEDED"]
    failed = [c for c in completed if c.status == "FAILED"]

    text = Text()
    text.append(f"  Active: {len(active)}  ", style="bold cyan")
    text.append("|  ", style="dim")
    text.append(f"Queue: {len(queue)}  ", style="bold yellow")
    text.append("|  ", style="dim")
    text.append(f"Done: {len(succeeded)}  ", style="bold green")
    text.append("|  ", style="dim")
    text.append(f"Failed: {len(failed)}  ", style="bold red")

    return Panel(text, title="Symphony-CC", border_style="blue")


def build_active_table(active: list[IssueState]) -> Table:
    """Build active issues table."""
    table = Table(title="Active Issues", expand=True)
    table.add_column("#", style="cyan", width=6)
    table.add_column("Title", style="white", ratio=3)
    table.add_column("Status", style="yellow", width=12)
    table.add_column("Attempts", style="blue", width=8)
    table.add_column("Elapsed", style="green", width=10)
    table.add_column("Cost($)", style="magenta", width=10)

    for issue in active:
        attempt_str = f"{issue.attempt}/{issue.max_retries}" if issue.attempt > 0 else "-"
        table.add_row(
            str(issue.issue_number),
            issue.title[:50],
            issue.status,
            attempt_str,
            _elapsed_str(issue.started_at),
            f"${issue.cost_usd:.2f}",
        )

    if not active:
        table.add_row("-", "No active tasks", "-", "-", "-", "-")

    return table


def build_queue_table(queue: list[IssueState]) -> Table:
    """Build queue table."""
    table = Table(title="Queue", expand=True)
    table.add_column("#", style="cyan", width=6)
    table.add_column("Title", style="white", ratio=3)
    table.add_column("Status", style="dim", width=12)

    for issue in queue:
        table.add_row(
            str(issue.issue_number),
            issue.title[:50],
            issue.status,
        )

    if not queue:
        table.add_row("-", "No queued issues", "-")

    return table


def build_completed_table(completed: list[IssueState], limit: int = 10) -> Table:
    """Build recent completed/failed issues table."""
    table = Table(title="Recent Completed/Failed", expand=True)
    table.add_column("#", style="cyan", width=6)
    table.add_column("Title", style="white", ratio=3)
    table.add_column("Result", width=10)
    table.add_column("Cost($)", style="magenta", width=10)

    # Show newest first
    recent = completed[-limit:][::-1]
    for issue in recent:
        status_style = "green" if issue.status == "SUCCEEDED" else "red"
        table.add_row(
            str(issue.issue_number),
            issue.title[:50],
            Text(issue.status, style=status_style),
            f"${issue.cost_usd:.2f}",
        )

    if not completed:
        table.add_row("-", "No completed tasks", "-", "-")

    return table


def build_stats_panel(completed: list[IssueState]) -> Panel:
    """Build daily stats panel."""
    total = len(completed)
    succeeded = sum(1 for c in completed if c.status == "SUCCEEDED")
    total_cost = sum(c.cost_usd for c in completed)
    success_rate = (succeeded / total * 100) if total > 0 else 0.0

    text = Text()
    text.append(f"  Total: {total}  ", style="bold")
    text.append("|  ", style="dim")
    text.append(f"Success: {success_rate:.0f}%  ", style="bold green")
    text.append("|  ", style="dim")
    text.append(f"Total Cost: ${total_cost:.2f}", style="bold magenta")

    return Panel(text, title="Stats", border_style="dim")


def build_layout(state: dict[str, list[IssueState]]) -> Layout:
    """Assemble the full dashboard layout."""
    layout = Layout()
    layout.split_column(
        Layout(name="summary", size=3),
        Layout(name="active", ratio=2),
        Layout(name="queue", ratio=1),
        Layout(name="completed", ratio=2),
        Layout(name="stats", size=3),
    )

    active = state["active"]
    queue = state["queue"]
    completed = state["completed"]

    layout["summary"].update(build_summary_panel(active, queue, completed))
    layout["active"].update(build_active_table(active))
    layout["queue"].update(build_queue_table(queue))
    layout["completed"].update(build_completed_table(completed))
    layout["stats"].update(build_stats_panel(completed))

    return layout


class DashboardApp:
    """TUI dashboard application.

    Refreshes screen every second to show real-time status like a control tower.
    """

    def __init__(
        self,
        state_dir: Path | None = None,
        refresh_interval: float = 1.0,
    ) -> None:
        self.reader = StateReader(state_dir)
        self.refresh_interval = refresh_interval
        self._running = False

    def render_once(self) -> Layout:
        """Render screen once (for testing)."""
        state = self.reader.read_all()
        return build_layout(state)

    def run(self) -> None:
        """Run dashboard. Press Ctrl+C to exit."""
        console = Console()
        self._running = True

        try:
            with Live(
                self.render_once(),
                console=console,
                refresh_per_second=1,
                screen=True,
            ) as live:
                while self._running:
                    time.sleep(self.refresh_interval)
                    live.update(self.render_once())
        except KeyboardInterrupt:
            self._running = False

    def stop(self) -> None:
        """Stop the dashboard."""
        self._running = False
