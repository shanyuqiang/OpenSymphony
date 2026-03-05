"""GitHub Issues 어댑터 테스트.

gh CLI 호출을 모킹하여 네트워크 없이 테스트한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from lib.tracker import (
    GitHubTracker,
    Issue,
    IssueMeta,
    parse_issue_meta,
)


# --- parse_issue_meta 단위 테스트 ---


class TestParseIssueMeta:
    """frontmatter 파싱 테스트."""

    def test_빈_본문이면_기본값_반환(self) -> None:
        meta, warnings = parse_issue_meta("")
        assert meta == IssueMeta()
        assert warnings == []

    def test_frontmatter_없으면_기본값_반환(self) -> None:
        meta, warnings = parse_issue_meta("그냥 이슈 본문입니다.")
        assert meta == IssueMeta()
        assert warnings == []

    def test_모든_필드_파싱(self) -> None:
        body = """---
mode: bugfix
priority: high
max_iterations: 20
max_budget_usd: 10.5
---

이슈 내용입니다.
"""
        meta, warnings = parse_issue_meta(body)
        assert meta.mode == "bugfix"
        assert meta.priority == "high"
        assert meta.max_iterations == 20
        assert meta.max_budget_usd == 10.5
        assert warnings == []

    def test_하이픈_키_지원(self) -> None:
        body = """---
mode: refactor
max-iterations: 15
max-budget-usd: 3.0
---
"""
        meta, warnings = parse_issue_meta(body)
        assert meta.mode == "refactor"
        assert meta.max_iterations == 15
        assert meta.max_budget_usd == 3.0

    def test_일부_필드만_있으면_나머지_기본값(self) -> None:
        body = """---
mode: bugfix
---
"""
        meta, warnings = parse_issue_meta(body)
        assert meta.mode == "bugfix"
        assert meta.priority == "normal"
        assert meta.max_iterations == 10
        assert meta.max_budget_usd == 5.0

    def test_잘못된_값은_무시(self) -> None:
        body = """---
max_iterations: not_a_number
mode: feature
---
"""
        meta, warnings = parse_issue_meta(body)
        assert meta.mode == "feature"
        assert meta.max_iterations == 10  # 기본값 유지
        assert any("잘못된 값" in w for w in warnings)

    def test_잘못된_mode_경고(self) -> None:
        body = """---
mode: unknown
---
"""
        meta, warnings = parse_issue_meta(body)
        assert meta.mode == "feature"  # 기본값 유지
        assert any("알 수 없는 mode" in w for w in warnings)

    def test_defaults_적용(self) -> None:
        defaults = IssueMeta(mode="bugfix", priority="high", max_iterations=20, max_budget_usd=10.0)
        meta, warnings = parse_issue_meta("본문만 있고 frontmatter 없음", defaults=defaults)
        assert meta == defaults
        assert warnings == []

    def test_defaults_위에_파싱값_병합(self) -> None:
        defaults = IssueMeta(mode="bugfix", priority="high", max_iterations=20, max_budget_usd=10.0)
        body = """---
mode: refactor
---
"""
        meta, warnings = parse_issue_meta(body, defaults=defaults)
        assert meta.mode == "refactor"  # 파싱값 우선
        assert meta.priority == "high"  # defaults 유지
        assert meta.max_iterations == 20  # defaults 유지
        assert meta.max_budget_usd == 10.0  # defaults 유지


# --- _run_gh 모킹 헬퍼 ---


def _mock_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    """asyncio.create_subprocess_exec의 모킹 반환값을 만든다."""
    proc = AsyncMock()
    proc.communicate.return_value = (
        stdout.encode(),
        stderr.encode(),
    )
    proc.returncode = returncode
    return proc


# --- GitHubTracker 테스트 ---


class TestGitHubTracker:
    """GitHubTracker 클래스 테스트."""

    @pytest.fixture
    def tracker(self) -> GitHubTracker:
        return GitHubTracker(repo="owner/repo")

    @pytest.mark.asyncio
    async def test_poll_ready_issues_정상(self, tracker: GitHubTracker) -> None:
        gh_response = json.dumps([
            {
                "number": 1,
                "title": "기능 추가",
                "body": "---\nmode: feature\npriority: high\n---\n본문",
                "labels": [{"name": "symphony:ready"}, {"name": "enhancement"}],
            },
            {
                "number": 2,
                "title": "버그 수정",
                "body": "버그 설명",
                "labels": [{"name": "symphony:ready"}],
            },
        ])

        with patch(
            "lib.tracker.asyncio.create_subprocess_exec",
            return_value=_mock_process(stdout=gh_response),
        ):
            issues = await tracker.poll_ready_issues("symphony:ready")

        assert len(issues) == 2

        # 첫 번째 이슈 검증
        assert issues[0].number == 1
        assert issues[0].title == "기능 추가"
        assert "symphony:ready" in issues[0].labels
        assert issues[0].meta.mode == "feature"
        assert issues[0].meta.priority == "high"

        # 두 번째 이슈 (frontmatter 없음) 검증
        assert issues[1].number == 2
        assert issues[1].meta == IssueMeta()

    @pytest.mark.asyncio
    async def test_poll_ready_issues_빈_결과(self, tracker: GitHubTracker) -> None:
        with patch(
            "lib.tracker.asyncio.create_subprocess_exec",
            return_value=_mock_process(stdout=""),
        ):
            issues = await tracker.poll_ready_issues("symphony:ready")

        assert issues == []

    @pytest.mark.asyncio
    async def test_update_labels_추가_및_제거(self, tracker: GitHubTracker) -> None:
        mock_proc = _mock_process()

        with patch(
            "lib.tracker.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            await tracker.update_labels(
                issue_number=1,
                add_labels=["symphony:running"],
                remove_labels=["symphony:ready"],
            )

        # gh issue edit 호출 확인
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "gh" == call_args[0]
        assert "issue" == call_args[1]
        assert "edit" == call_args[2]
        assert "1" == call_args[3]
        assert "--add-label" in call_args
        assert "--remove-label" in call_args

    @pytest.mark.asyncio
    async def test_update_labels_아무것도_없으면_호출_안함(
        self, tracker: GitHubTracker
    ) -> None:
        with patch(
            "lib.tracker.asyncio.create_subprocess_exec",
            return_value=_mock_process(),
        ) as mock_exec:
            await tracker.update_labels(issue_number=1)

        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_comment(self, tracker: GitHubTracker) -> None:
        with patch(
            "lib.tracker.asyncio.create_subprocess_exec",
            return_value=_mock_process(),
        ) as mock_exec:
            await tracker.add_comment(1, "작업을 시작합니다.")

        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert "comment" in call_args
        assert "--body" in call_args

    @pytest.mark.asyncio
    async def test_create_pr(self, tracker: GitHubTracker) -> None:
        pr_url = "https://github.com/owner/repo/pull/10"
        with patch(
            "lib.tracker.asyncio.create_subprocess_exec",
            return_value=_mock_process(stdout=pr_url),
        ) as mock_exec:
            result = await tracker.create_pr(
                issue_number=1,
                branch="feature/issue-1",
                title="feat: 기능 추가",
                body="변경 사항 설명",
            )

        assert result == pr_url
        call_args = mock_exec.call_args[0]
        assert "pr" in call_args
        assert "create" in call_args
        assert "--head" in call_args

    @pytest.mark.asyncio
    async def test_gh_명령_실패_시_RuntimeError(self, tracker: GitHubTracker) -> None:
        with patch(
            "lib.tracker.asyncio.create_subprocess_exec",
            return_value=_mock_process(
                stderr="not found", returncode=1
            ),
        ):
            with pytest.raises(RuntimeError, match="gh 명령 실패"):
                await tracker.poll_ready_issues("symphony:ready")


class TestIssueDataclass:
    """Issue / IssueMeta 데이터 클래스 테스트."""

    def test_issue_frozen(self) -> None:
        issue = Issue(number=1, title="테스트", body="본문")
        with pytest.raises(AttributeError):
            issue.number = 2  # type: ignore[misc]

    def test_issue_meta_frozen(self) -> None:
        meta = IssueMeta()
        with pytest.raises(AttributeError):
            meta.mode = "bugfix"  # type: ignore[misc]

    def test_issue_기본값(self) -> None:
        issue = Issue(number=1, title="t", body="b")
        assert issue.labels == []
        assert issue.meta == IssueMeta()
