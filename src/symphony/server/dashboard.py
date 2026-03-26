"""HTTP Dashboard for Symphony orchestrator status."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, UTC
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from symphony.orchestrator import Orchestrator

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Symphony Dashboard</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body {{ font-family: monospace; margin: 2em; background: #0d1117; color: #c9d1d9; }}
    h1 {{ color: #58a6ff; }}
    h2 {{ color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 2em; }}
    th, td {{ border: 1px solid #30363d; padding: 6px 12px; text-align: left; }}
    th {{ background: #161b22; color: #58a6ff; }}
    tr:nth-child(even) {{ background: #161b22; }}
    .badge {{ padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }}
    .running {{ background: #1f6feb; }}
    .retry {{ background: #da3633; }}
    .stat {{ display: inline-block; margin-right: 2em; }}
    .stat-value {{ font-size: 1.4em; color: #58a6ff; }}
    .empty {{ color: #8b949e; font-style: italic; }}
  </style>
</head>
<body>
  <h1>&#9835; Symphony Dashboard</h1>
  <p>
    <span class="stat"><span class="stat-value">{running_count}</span> running</span>
    <span class="stat"><span class="stat-value">{retry_count}</span> retry queue</span>
    <span class="stat"><span class="stat-value">{uptime}</span> uptime</span>
    <span class="stat">refreshes every 5s</span>
  </p>

  <h2>Running Agents ({running_count})</h2>
  {running_table}

  <h2>Retry Queue ({retry_count})</h2>
  {retry_table}

  <p style="color:#8b949e; font-size:0.8em">Last updated: {timestamp} &mdash; <a href="/api/status" style="color:#58a6ff">JSON API</a></p>
</body>
</html>
"""


def _format_uptime(started_at: float) -> str:
    elapsed = int(time.monotonic() - started_at)
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _running_table_html(running: dict) -> str:
    if not running:
        return '<p class="empty">No agents currently running.</p>'
    rows = ""
    for entry in running.values():
        issue = entry.issue
        elapsed = ""
        try:
            started = entry.started_at
            if isinstance(started, str):
                started = datetime.fromisoformat(started)
            delta = int((datetime.now(UTC) - started).total_seconds())
            m, s = divmod(delta, 60)
            elapsed = f"{m}m {s}s" if m else f"{s}s"
        except Exception:  # noqa: BLE001
            pass
        rows += (
            f"<tr>"
            f"<td>{issue.identifier}</td>"
            f"<td>{issue.title[:60]}</td>"
            f"<td>{elapsed}</td>"
            f"<td>{entry.retry_attempt}</td>"
            f"<td>{entry.tokens.total_tokens if hasattr(entry, 'tokens') else 0}</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Issue</th><th>Title</th><th>Elapsed</th><th>Attempt</th><th>Tokens</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _retry_table_html(retry_queue: list) -> str:
    if not retry_queue:
        return '<p class="empty">Retry queue is empty.</p>'
    rows = ""
    now_ms = time.monotonic() * 1000
    for entry in retry_queue:
        wait_s = max(0, int((entry.due_at_ms - now_ms) / 1000))
        rows += (
            f"<tr>"
            f"<td>{entry.identifier}</td>"
            f"<td>{entry.attempt}</td>"
            f"<td>{wait_s}s</td>"
            f"<td>{(entry.error or '')[:80]}</td>"
            f"</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Issue</th><th>Attempt</th><th>Retry in</th><th>Error</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


class DashboardServer:
    """Starlette-based HTTP dashboard."""

    def __init__(self, orchestrator: "Orchestrator", port: int = 8080) -> None:
        self.orchestrator = orchestrator
        self.port = port
        self._started_at = time.monotonic()
        self._app = Starlette(
            routes=[
                Route("/", self._index),
                Route("/api/status", self._api_status),
            ]
        )
        self._server_task: asyncio.Task | None = None

    async def _index(self, request: Request) -> HTMLResponse:
        running = self.orchestrator.get_running()
        retry = self.orchestrator.get_retry_queue()
        html = _HTML_TEMPLATE.format(
            running_count=len(running),
            retry_count=len(retry),
            uptime=_format_uptime(self._started_at),
            running_table=_running_table_html(running),
            retry_table=_retry_table_html(retry),
            timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
        return HTMLResponse(html)

    async def _api_status(self, request: Request) -> JSONResponse:
        running = self.orchestrator.get_running()
        retry = self.orchestrator.get_retry_queue()
        now_ms = time.monotonic() * 1000

        data = {
            "uptime_s": int(time.monotonic() - self._started_at),
            "running": [
                {
                    "issue_id": entry.issue.id,
                    "identifier": entry.issue.identifier,
                    "title": entry.issue.title,
                    "started_at": entry.started_at.isoformat()
                    if hasattr(entry.started_at, "isoformat")
                    else str(entry.started_at),
                    "retry_attempt": entry.retry_attempt,
                    "tokens": {
                        "input": entry.tokens.input_tokens
                        if hasattr(entry, "tokens")
                        else 0,
                        "output": entry.tokens.output_tokens
                        if hasattr(entry, "tokens")
                        else 0,
                    },
                }
                for entry in running.values()
            ],
            "retry_queue": [
                {
                    "issue_id": entry.issue_id,
                    "identifier": entry.identifier,
                    "attempt": entry.attempt,
                    "retry_in_s": max(0, (entry.due_at_ms - now_ms) / 1000),
                    "error": entry.error,
                }
                for entry in retry
            ],
        }
        return JSONResponse(data)

    async def start(self) -> None:
        """Start the uvicorn server in the background."""
        import uvicorn

        config = uvicorn.Config(
            self._app,
            host="0.0.0.0",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(server.serve())

    async def stop(self) -> None:
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
