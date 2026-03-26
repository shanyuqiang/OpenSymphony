"""알림 시스템.

비유: 택배 알림처럼, 작업이 완료/실패되면
관련 채널(GitHub 코멘트, Slack)로 소식을 전달한다.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass

from lib.config import NotifierConfig
from lib.tracker import GitHubTracker


@dataclass(frozen=True)
class TaskResult:
    """알림에 포함할 태스크 결과 정보."""

    issue_number: int
    issue_title: str
    state: str
    attempt: int
    max_retries: int
    cost_usd: float
    duration_s: float
    pr_url: str = ""
    error: str = ""


class Notifier:
    """작업 완료/실패 알림을 전송한다."""

    def __init__(self, config: NotifierConfig, tracker: GitHubTracker) -> None:
        self._config = config
        self._tracker = tracker

    async def notify(self, event: str, result: TaskResult) -> None:
        """이벤트에 따라 알림을 전송한다."""
        if event not in self._config.events:
            return

        tasks = []
        if self._config.github_comment:
            tasks.append(self._notify_github(event, result))
        if self._config.slack_webhook_url:
            tasks.append(self._notify_slack(event, result))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _notify_github(self, event: str, result: TaskResult) -> None:
        """GitHub 이슈에 코멘트를 추가한다."""
        body = self._format_github_comment(event, result)
        await self._tracker.add_comment(result.issue_number, body)

    async def _notify_slack(self, event: str, result: TaskResult) -> None:
        """Slack webhook으로 알림을 전송한다."""
        payload = {
            "text": self._format_slack_message(event, result),
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._config.slack_webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))

    def _format_github_comment(self, event: str, result: TaskResult) -> str:
        """GitHub 코멘트 포맷."""
        icon = {"succeeded": "✅", "failed": "❌", "escalated": "🚨"}.get(event, "ℹ️")
        duration_min = result.duration_s / 60

        lines = [
            f"## {icon} Symphony-CC: {event.upper()}",
            "",
            "| 항목 | 값 |",
            "|------|-----|",
            f"| 상태 | {result.state} |",
            f"| 시도 | {result.attempt}/{result.max_retries} |",
            f"| 소요 시간 | {duration_min:.1f}분 |",
            f"| 비용 | ${result.cost_usd:.2f} |",
        ]

        if result.pr_url:
            lines.append(f"| PR | {result.pr_url} |")
        if result.error:
            lines.extend(["", f"**에러**: `{result.error[:200]}`"])

        return "\n".join(lines)

    def _format_slack_message(self, event: str, result: TaskResult) -> str:
        """Slack 메시지 포맷."""
        icon = {"succeeded": ":white_check_mark:", "failed": ":x:", "escalated": ":rotating_light:"}.get(event, ":information_source:")
        duration_min = result.duration_s / 60
        msg = f"{icon} *#{result.issue_number} {result.issue_title}* — {event.upper()} (시도 {result.attempt}/{result.max_retries}, {duration_min:.1f}분, ${result.cost_usd:.2f})"
        if result.pr_url:
            msg += f"\nPR: {result.pr_url}"
        return msg
