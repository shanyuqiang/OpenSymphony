"""알림 시스템 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lib.config import NotifierConfig
from lib.notifier import Notifier, TaskResult


@pytest.fixture
def sample_result() -> TaskResult:
    return TaskResult(
        issue_number=1,
        issue_title="기능 추가",
        state="SUCCEEDED",
        attempt=1,
        max_retries=3,
        cost_usd=1.23,
        duration_s=120.0,
        pr_url="https://github.com/owner/repo/pull/10",
    )


@pytest.fixture
def failed_result() -> TaskResult:
    return TaskResult(
        issue_number=2,
        issue_title="버그 수정",
        state="ESCALATED",
        attempt=3,
        max_retries=3,
        cost_usd=5.0,
        duration_s=300.0,
        error="테스트 실패: assert False",
    )


class TestNotifyGitHubComment:
    @pytest.mark.asyncio
    async def test_notify_github_comment(self, sample_result: TaskResult) -> None:
        config = NotifierConfig(github_comment=True, slack_webhook_url="")
        tracker = AsyncMock()
        notifier = Notifier(config, tracker)

        await notifier.notify("succeeded", sample_result)

        tracker.add_comment.assert_called_once()
        call_args = tracker.add_comment.call_args
        assert call_args[0][0] == 1  # issue_number
        assert "SUCCEEDED" in call_args[0][1]


class TestNotifySlack:
    @pytest.mark.asyncio
    async def test_notify_slack_webhook(self, sample_result: TaskResult) -> None:
        config = NotifierConfig(
            github_comment=False,
            slack_webhook_url="https://hooks.slack.com/test",
        )
        tracker = AsyncMock()
        notifier = Notifier(config, tracker)

        with patch("lib.notifier.urllib.request.urlopen") as mock_urlopen:
            await notifier.notify("succeeded", sample_result)

        mock_urlopen.assert_called_once()


class TestEventFiltering:
    @pytest.mark.asyncio
    async def test_이벤트_미포함시_skip(self) -> None:
        config = NotifierConfig(
            github_comment=True,
            events=["succeeded"],
        )
        tracker = AsyncMock()
        notifier = Notifier(config, tracker)

        result = TaskResult(
            issue_number=1,
            issue_title="test",
            state="FAILED",
            attempt=1,
            max_retries=3,
            cost_usd=0.0,
            duration_s=0.0,
        )

        await notifier.notify("failed", result)

        tracker.add_comment.assert_not_called()


class TestFormatGitHubComment:
    def test_성공_포맷(self, sample_result: TaskResult) -> None:
        config = NotifierConfig()
        tracker = AsyncMock()
        notifier = Notifier(config, tracker)

        comment = notifier._format_github_comment("succeeded", sample_result)
        assert "SUCCEEDED" in comment
        assert "PR" in comment
        assert "$1.23" in comment

    def test_실패_에러포함(self, failed_result: TaskResult) -> None:
        config = NotifierConfig()
        tracker = AsyncMock()
        notifier = Notifier(config, tracker)

        comment = notifier._format_github_comment("escalated", failed_result)
        assert "ESCALATED" in comment
        assert "테스트 실패" in comment
        assert "3/3" in comment
