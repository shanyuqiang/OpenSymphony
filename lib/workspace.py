"""git worktree 기반 워크스페이스 격리 매니저.

비유: 아파트 관리인처럼, 각 이슈에 독립된 작업 공간(방)을 배정하고
작업이 끝나면 깨끗이 정리하는 역할.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    """워크스페이스 관련 에러."""


class WorkspaceManager:
    """git worktree를 사용해 이슈별 격리된 작업 디렉토리를 관리한다."""

    def __init__(self, workspace_root: Path, repo_path: Path, tracker_repo: str) -> None:
        self.workspace_root = workspace_root
        self.repo_path = repo_path
        self.tracker_repo = tracker_repo

    def _worktree_path(self, issue_number: int) -> Path:
        """이슈 번호로 worktree 경로를 생성한다."""
        # tracker_repo에서 repo 이름 추출 (owner/repo -> repo)
        repo_name = self.tracker_repo.split("/")[-1]
        return self.workspace_root / repo_name / f"issue-{issue_number}"

    async def _run_git(self, *args: str, cwd: Optional[Path] = None) -> str:
        """git 명령어를 실행하고 stdout을 반환한다."""
        cmd_cwd = cwd or self.repo_path
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=cmd_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            raise WorkspaceError(
                f"git {' '.join(args)} 실패 (exit {proc.returncode}): {error_msg}"
            )
        return stdout.decode().strip()

    async def create_worktree(self, issue_number: int, branch_name: str) -> Path:
        """이슈용 worktree를 생성한다. 이미 존재하면 기존 경로를 반환한다."""
        existing = await self.get_worktree(issue_number)
        if existing is not None:
            logger.info("worktree 재사용: issue #%d -> %s", issue_number, existing)
            return existing

        wt_path = self._worktree_path(issue_number)
        wt_path.parent.mkdir(parents=True, exist_ok=True)

        await self._run_git("worktree", "add", "-b", branch_name, str(wt_path))
        logger.info("worktree 생성: issue #%d -> %s", issue_number, wt_path)

        # worktree의 origin을 tracker_repo로 설정
        remote_url = f"https://github.com/{self.tracker_repo}.git"
        # worktree는 부모 repo의 remote를 상속하므로 set-url로 덮어쓰기
        await self._run_git("remote", "set-url", "origin", remote_url, cwd=wt_path)
        logger.info("worktree remote 설정: %s -> %s", wt_path, remote_url)

        # Copy .claude/skills to worktree for SDK auto-discovery
        await self._copy_skills_to_worktree(wt_path)

        return wt_path

    async def _copy_skills_to_worktree(self, wt_path: Path) -> None:
        """Copy .claude/skills from main repo to worktree for SDK auto-discovery."""
        source_skills = self.repo_path / ".claude" / "skills"
        if not source_skills.exists():
            logger.warning("Skills directory not found: %s", source_skills)
            return

        target_skills = wt_path / ".claude" / "skills"
        target_skills.parent.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            "cp", "-r", str(source_skills), str(target_skills),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("Skills copied to worktree: %s", target_skills)
        else:
            logger.warning("Failed to copy skills: %s", stderr.decode().strip())

    async def get_worktree(self, issue_number: int) -> Optional[Path]:
        """기존 worktree 경로를 반환한다. 없으면 None."""
        wt_path = self._worktree_path(issue_number)
        if not wt_path.exists():
            return None

        # git worktree list로 실제 등록 여부 확인
        # resolve()로 비교: macOS에서 /var → /private/var symlink 불일치 방지
        worktrees = await self.list_worktrees()
        resolved = wt_path.resolve()
        for wt in worktrees:
            if Path(wt["path"]).resolve() == resolved:
                return wt_path
        return None

    async def cleanup_worktree(self, issue_number: int) -> None:
        """worktree를 제거하고 prune한다."""
        wt_path = self._worktree_path(issue_number)

        try:
            await self._run_git("worktree", "remove", str(wt_path), "--force")
        except WorkspaceError:
            # 이미 삭제된 경우 무시
            logger.warning("worktree remove 실패 (이미 삭제?): issue #%d", issue_number)

        await self._run_git("worktree", "prune")
        logger.info("worktree 정리 완료: issue #%d", issue_number)

    async def cleanup_orphans(self) -> list[Path]:
        """git에 등록되지 않은 잔여 worktree 디렉토리를 정리한다."""
        # 먼저 prune으로 stale 참조 제거
        await self._run_git("worktree", "prune")

        registered = await self.list_worktrees()
        registered_paths = {Path(wt["path"]) for wt in registered}

        repo_name = self.repo_path.name
        workspace_dir = self.workspace_root / repo_name
        cleaned: list[Path] = []

        if not workspace_dir.exists():
            return cleaned

        for child in workspace_dir.iterdir():
            if child.is_dir() and child not in registered_paths:
                # 안전 확인: issue-N 패턴인 경우만 삭제
                if re.match(r"^issue-\d+$", child.name):
                    import shutil
                    shutil.rmtree(child)
                    cleaned.append(child)
                    logger.info("고아 worktree 삭제: %s", child)

        return cleaned

    async def list_worktrees(self) -> list[dict]:
        """현재 등록된 worktree 목록을 반환한다."""
        output = await self._run_git("worktree", "list", "--porcelain")
        if not output:
            return []

        worktrees: list[dict] = []
        current: dict = {}

        for line in output.split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[len("worktree "):]}
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True

        if current:
            worktrees.append(current)

        return worktrees
