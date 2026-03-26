"""GitHub Issues 어댑터.

우체국 창구처럼 이슈를 접수하고, 라벨로 상태를 관리하고,
코멘트로 진행 상황을 알려주는 모듈.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IssueMeta:
    """이슈 본문의 frontmatter에서 추출한 메타데이터."""

    mode: str = "feature"  # feature / bugfix / refactor
    priority: str = "normal"  # normal / high / low
    max_iterations: int = 10
    max_budget_usd: float = 5.0


@dataclass(frozen=True)
class Issue:
    """GitHub 이슈 데이터."""

    number: int
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    meta: IssueMeta = field(default_factory=IssueMeta)


# frontmatter 패턴: --- 로 감싼 YAML 블록
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n?",
    re.DOTALL,
)

# frontmatter 내 key: value 패턴
_KV_RE = re.compile(r"^(\w[\w-]*)\s*:\s*(.+)$", re.MULTILINE)

# IssueMeta 허용 필드 및 타입 변환
_META_FIELDS: dict[str, type] = {
    "mode": str,
    "priority": str,
    "max_iterations": int,
    "max_budget_usd": float,
    "max-iterations": int,  # 하이픈 표기도 허용
    "max-budget-usd": float,
}


def parse_issue_meta(body: str, defaults: IssueMeta | None = None) -> tuple[IssueMeta, list[str]]:
    """이슈 본문에서 frontmatter 메타데이터를 추출한다.

    편지 봉투의 수신자 정보를 읽는 것처럼,
    이슈 본문 상단의 YAML frontmatter에서 설정값을 파싱한다.
    frontmatter 없으면 defaults 반환. warnings로 잘못된 값 보고.
    """
    warnings: list[str] = []

    match = _FRONTMATTER_RE.match(body)
    if not match:
        return (defaults or IssueMeta(), warnings)

    raw = match.group(1)
    kwargs: dict[str, Any] = {}
    for kv_match in _KV_RE.finditer(raw):
        key = kv_match.group(1).strip()
        value = kv_match.group(2).strip()
        normalized_key = key.replace("-", "_")

        if key in _META_FIELDS:
            converter = _META_FIELDS[key]
            try:
                converted = converter(value)
                if normalized_key == "mode" and converted not in ("feature", "bugfix", "refactor"):
                    warnings.append(f"알 수 없는 mode: {converted} (기본값 사용)")
                    continue
                kwargs[normalized_key] = converted
            except (ValueError, TypeError):
                warnings.append(f"잘못된 값: {key}={value}")

    base = defaults or IssueMeta()
    merged = IssueMeta(
        mode=kwargs.get("mode", base.mode),
        priority=kwargs.get("priority", base.priority),
        max_iterations=kwargs.get("max_iterations", base.max_iterations),
        max_budget_usd=kwargs.get("max_budget_usd", base.max_budget_usd),
    )
    return (merged, warnings)


async def _run_gh(*args: str) -> str:
    """gh CLI 명령어를 실행하고 stdout을 반환한다.

    전화기로 GitHub에 전화를 거는 것과 같다 — 명령을 전달하고 응답을 받는다.
    """
    proc = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(
            f"gh 명령 실패 (exit {proc.returncode}): {error_msg}"
        )

    return stdout.decode().strip()


class GitHubTracker:
    """GitHub Issues 기반 작업 추적기.

    게시판 관리자처럼 이슈를 조회하고, 라벨을 바꾸고,
    코멘트를 달고, PR을 생성한다.
    """

    def __init__(self, repo: str) -> None:
        """repo: 'owner/repo' 형식의 저장소 경로."""
        self.repo = repo

    async def poll_ready_issues(self, label: str) -> list[Issue]:
        """특정 라벨이 붙은 이슈 목록을 조회한다.

        우편함에서 특정 색 봉투만 골라내는 것과 같다.
        """
        raw = await _run_gh(
            "issue", "list",
            "--repo", self.repo,
            "--label", label,
            "--json", "number,title,body,labels",
            "--limit", "100",
        )

        if not raw:
            return []

        items = json.loads(raw)
        issues: list[Issue] = []
        for item in items:
            label_names = [lb["name"] for lb in item.get("labels", [])]
            meta, _warnings = parse_issue_meta(item.get("body", "") or "")
            issues.append(
                Issue(
                    number=item["number"],
                    title=item["title"],
                    body=item.get("body", "") or "",
                    labels=label_names,
                    meta=meta,
                )
            )
        return issues

    async def update_labels(
        self,
        issue_number: int,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """이슈 라벨을 추가/제거한다.

        파일 폴더에 색인 태그를 붙이고 떼는 것과 같다.
        """
        args: list[str] = [
            "issue", "edit",
            str(issue_number),
            "--repo", self.repo,
        ]

        if add_labels:
            args.extend(["--add-label", ",".join(add_labels)])
        if remove_labels:
            args.extend(["--remove-label", ",".join(remove_labels)])

        if add_labels or remove_labels:
            await _run_gh(*args)

    async def add_comment(self, issue_number: int, body: str) -> None:
        """이슈에 코멘트를 추가한다.

        게시판 글에 답글을 다는 것과 같다.
        """
        await _run_gh(
            "issue", "comment",
            str(issue_number),
            "--repo", self.repo,
            "--body", body,
        )

    async def create_pr(
        self,
        issue_number: int,
        branch: str,
        title: str,
        body: str,
    ) -> str:
        """PR을 생성하고 URL을 반환한다.

        작업 완료 보고서를 제출하는 것과 같다.
        """
        pr_body = f"Closes #{issue_number}\n\n{body}"
        result = await _run_gh(
            "pr", "create",
            "--repo", self.repo,
            "--head", branch,
            "--title", title,
            "--body", pr_body,
        )
        return result
