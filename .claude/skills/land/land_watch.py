#!/usr/bin/env python3
"""
Land watch helper for GitHub PRs.

Monitors CI checks, review status, and merge conflicts for a PR.
Exit codes:
  0: Ready to merge (CI green, no blocking reviews)
  2: Human review feedback detected (blocking)
  3: CI checks failed
  4: PR head updated (force-push detected)
  5: Merge conflicts detected
"""

import asyncio
import json
import random
from dataclasses import dataclass
from datetime import datetime

POLL_SECONDS = 10
CHECKS_APPEAR_TIMEOUT_SECONDS = 120
MAX_GH_RETRIES = 5
BASE_GH_BACKOFF_SECONDS = 2


@dataclass
class PrInfo:
    number: int
    url: str
    head_sha: str
    mergeable: str | None
    merge_state: str | None


class RateLimitError(RuntimeError):
    pass


def is_rate_limit_error(error: str) -> bool:
    return "HTTP 429" in error or "rate limit" in error.lower()


async def run_gh(*args: str) -> str:
    max_delay = BASE_GH_BACKOFF_SECONDS * (2 ** (MAX_GH_RETRIES - 1))
    delay_seconds = BASE_GH_BACKOFF_SECONDS
    last_error = "gh command failed"
    for attempt in range(1, MAX_GH_RETRIES + 1):
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode()
        error = stderr.decode().strip() or "gh command failed"
        if not is_rate_limit_error(error):
            raise RuntimeError(error)
        last_error = error
        if attempt >= MAX_GH_RETRIES:
            break
        jitter = random.uniform(0, delay_seconds)
        await asyncio.sleep(min(delay_seconds + jitter, max_delay))
        delay_seconds = min(delay_seconds * 2, max_delay)
    raise RateLimitError(last_error)


async def get_pr_info() -> PrInfo:
    data = await run_gh(
        "pr",
        "view",
        "--json",
        "number,url,headRefOid,mergeable,mergeStateStatus",
    )
    parsed = json.loads(data)
    return PrInfo(
        number=parsed["number"],
        url=parsed["url"],
        head_sha=parsed["headRefOid"],
        mergeable=parsed.get("mergeable"),
        merge_state=parsed.get("mergeStateStatus"),
    )


async def get_paginated_list(endpoint: str) -> list[dict]:
    page = 1
    items: list[dict] = []
    while True:
        data = await run_gh(
            "api",
            "--method",
            "GET",
            endpoint,
            "-f",
            "per_page=100",
            "-f",
            f"page={page}",
        )
        batch = json.loads(data)
        if not batch:
            break
        items.extend(batch)
        page += 1
    return items


async def get_issue_comments(pr_number: int) -> list[dict]:
    return await get_paginated_list(
        f"repos/{{owner}}/{{repo}}/issues/{pr_number}/comments",
    )


async def get_review_comments(pr_number: int) -> list[dict]:
    return await get_paginated_list(
        f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
    )


async def get_reviews(pr_number: int) -> list[dict]:
    return await get_paginated_list(
        f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
    )


async def get_check_runs(head_sha: str) -> list[dict]:
    page = 1
    check_runs: list[dict] = []
    while True:
        data = await run_gh(
            "api",
            "--method",
            "GET",
            f"repos/{{owner}}/{{repo}}/commits/{head_sha}/check-runs",
            "-f",
            "per_page=100",
            "-f",
            f"page={page}",
        )
        payload = json.loads(data)
        batch = payload.get("check_runs", [])
        if not batch:
            break
        check_runs.extend(batch)
        total_count = payload.get("total_count")
        if total_count is not None and len(check_runs) >= total_count:
            break
        page += 1
    return check_runs


def parse_time(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def check_timestamp(check: dict) -> datetime | None:
    for key in ("completed_at", "started_at", "run_started_at", "created_at"):
        value = check.get(key)
        if value:
            return parse_time(value)
    return None


def dedupe_check_runs(check_runs: list[dict]) -> list[dict]:
    latest_by_name: dict[str, dict] = {}
    for check in check_runs:
        name = check.get("name", "unknown")
        timestamp = check_timestamp(check)
        if name not in latest_by_name:
            latest_by_name[name] = check
            continue
        existing = latest_by_name[name]
        existing_timestamp = check_timestamp(existing)
        if timestamp is None:
            continue
        if existing_timestamp is None or timestamp > existing_timestamp:
            latest_by_name[name] = check
    return list(latest_by_name.values())


def summarize_checks(check_runs: list[dict]) -> tuple[bool, bool, list[str]]:
    if not check_runs:
        return True, False, ["no checks reported"]
    check_runs = dedupe_check_runs(check_runs)
    pending = False
    failed = False
    failures: list[str] = []
    for check in check_runs:
        status = check.get("status")
        conclusion = check.get("conclusion")
        name = check.get("name", "unknown")
        if status != "completed":
            pending = True
            continue
        if conclusion not in ("success", "skipped", "neutral"):
            failed = True
            failures.append(f"{name}: {conclusion}")
    return pending, failed, failures


def is_bot_user(user: dict) -> bool:
    """Check if user is a bot."""
    login = user.get("login") or ""
    if user.get("type") == "Bot":
        return True
    return login.endswith("[bot]")


def is_blocking_review(review: dict) -> bool:
    """Check if a review is blocking merge.

    Blocking: CHANGES_REQUESTED state
    Non-blocking: APPROVED, DISMISSED, COMMENTED (with no body)
    """
    state = review.get("state")
    # APPROVED and DISMISSED are never blocking
    if state in ("APPROVED", "DISMISSED"):
        return False
    # CHANGES_REQUESTED is blocking
    if state == "CHANGES_REQUESTED":
        return True
    # For other states (COMMENTED, PENDING), check if human and has content
    if state in ("COMMENTED", "PENDING"):
        body = (review.get("body") or "").strip()
        user = review.get("user", {})
        # If it's a human with a comment, it might be blocking
        if body and not is_bot_user(user):
            return True
    return False


def get_latest_review_per_user(reviews: list[dict]) -> list[dict]:
    """Get latest review per user (dedupe)."""
    latest_by_user: dict[str, dict] = {}
    for review in reviews:
        user_login = review.get("user", {}).get("login")
        if not user_login:
            continue
        created_at = review.get("submitted_at") or review.get("created_at")
        if not created_at:
            continue
        created_time = parse_time(created_at)
        existing = latest_by_user.get(user_login)
        if existing:
            existing_time = parse_time(
                existing.get("submitted_at") or existing.get("created_at") or ""
            )
            if existing_time and created_time > existing_time:
                latest_by_user[user_login] = review
        else:
            latest_by_user[user_login] = review
    return list(latest_by_user.values())


def has_blocking_reviews(reviews: list[dict]) -> tuple[bool, list[str]]:
    """Check if there are blocking reviews. Returns (has_blocking, list of reviewers)."""
    latest = get_latest_review_per_user(reviews)
    blocking: list[str] = []
    for review in latest:
        if is_blocking_review(review):
            user = review.get("user", {}).get("login", "unknown")
            blocking.append(user)
    return bool(blocking), blocking


def has_blocking_issue_comments(comments: list[dict]) -> tuple[bool, int]:
    """Check if there are blocking human issue comments.

    Human comments without bot replies since the last review request are blocking.
    """
    # For simplicity, any human comment that isn't from a bot is potentially blocking
    # In a real scenario, you'd want to track which comments have been addressed
    human_comments = [
        c for c in comments
        if not is_bot_user(c.get("user", {}))
    ]
    return len(human_comments) > 0, len(human_comments)


def is_merge_conflicting(pr: PrInfo) -> bool:
    return pr.mergeable == "CONFLICTING" or pr.merge_state == "DIRTY"


async def wait_for_checks(checks_done: asyncio.Event, head_sha: str) -> None:
    """Wait for CI checks to complete. Exits with error if no CI is configured."""
    print("Waiting for CI checks...", flush=True)
    empty_seconds = 0
    while True:
        check_runs = await get_check_runs(head_sha)
        if not check_runs:
            empty_seconds += POLL_SECONDS
            if empty_seconds >= CHECKS_APPEAR_TIMEOUT_SECONDS:
                print(
                    "No checks detected after 120s; check CI configuration",
                )
                raise SystemExit(3)
            await asyncio.sleep(POLL_SECONDS)
            continue
        empty_seconds = 0
        pending, failed, failures = summarize_checks(check_runs)
        if failed:
            print("Checks failed:")
            for failure in failures:
                print(f"- {failure}")
            raise SystemExit(3)
        if not pending:
            print("Checks passed")
            checks_done.set()
            return
        await asyncio.sleep(POLL_SECONDS)


async def wait_for_review(
    checks_done: asyncio.Event,
    pr_number: int,
) -> None:
    """Wait for human review approval."""
    print("Waiting for human review...", flush=True)
    while True:
        reviews = await get_reviews(pr_number)
        has_block, reviewers = has_blocking_reviews(reviews)
        if has_block:
            print(f"Blocking review from: {', '.join(reviewers)}")
            raise SystemExit(2)

        # Also check issue comments for human feedback
        issue_comments = await get_issue_comments(pr_number)
        has_comments, count = has_blocking_issue_comments(issue_comments)
        if has_comments:
            print(f"Human issue comments detected ({count})")
            raise SystemExit(2)

        if checks_done.is_set():
            # CI is green but no approval yet - keep waiting
            pass
        await asyncio.sleep(POLL_SECONDS)


async def watch_pr() -> None:
    """Main watch loop."""
    pr = await get_pr_info()
    if is_merge_conflicting(pr):
        print(
            "PR has merge conflicts. Resolve/rebase against main and push before "
            "running land_watch again.",
        )
        raise SystemExit(5)
    head_sha = pr.head_sha
    checks_done = asyncio.Event()

    checks_task = asyncio.create_task(wait_for_checks(checks_done, head_sha))
    review_task = asyncio.create_task(wait_for_review(checks_done, pr.number))

    async def head_monitor() -> None:
        while True:
            current = await get_pr_info()
            if is_merge_conflicting(current):
                print(
                    "PR has merge conflicts. Resolve/rebase against main and push "
                    "before running land_watch again.",
                )
                raise SystemExit(5)
            if current.head_sha != head_sha:
                print("PR head updated; pull/amend/force-push to retrigger CI")
                raise SystemExit(4)
            await asyncio.sleep(POLL_SECONDS)

    monitor_task = asyncio.create_task(head_monitor())

    # Wait for either checks or review to finish first
    done, pending = await asyncio.wait(
        [monitor_task, checks_task, review_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()

    for task in done:
        exc = task.exception()
        if exc:
            raise exc


if __name__ == "__main__":
    try:
        asyncio.run(watch_pr())
    except SystemExit as exc:
        raise SystemExit(exc.code) from None
