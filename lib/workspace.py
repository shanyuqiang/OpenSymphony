"""Git worktree-based workspace isolation manager.

Manages isolated workspace directories for each issue using git worktree.
Like an apartment manager: assigns independent rooms (worktrees) for each
issue and cleans up after work is done.
"""

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    """Workspace-related errors."""


class WorkspaceManager:
    """Manages isolated workspace directories for each issue using git worktree."""

    def __init__(self, workspace_root: Path, repo_path: Path, tracker_repo: str) -> None:
        self.workspace_root = workspace_root
        self.repo_path = repo_path
        self.tracker_repo = tracker_repo

    def _worktree_path(self, issue_number: int) -> Path:
        """Generate worktree path from issue number."""
        # Extract repo name from tracker_repo (owner/repo -> repo)
        repo_name = self.tracker_repo.split("/")[-1]
        return self.workspace_root / repo_name / f"issue-{issue_number}"

    async def _run_git(self, *args: str, cwd: Optional[Path] = None) -> str:
        """Run git command and return stdout."""
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
                f"git {' '.join(args)} failed (exit {proc.returncode}): {error_msg}"
            )
        return stdout.decode().strip()

    async def create_worktree(self, issue_number: int, branch_name: str) -> Path:
        """Create worktree for issue. Returns existing path if already exists."""
        existing = await self.get_worktree(issue_number)
        if existing is not None:
            logger.info("Reusing worktree: issue #%d -> %s", issue_number, existing)
            return existing

        wt_path = self._worktree_path(issue_number)
        wt_path.parent.mkdir(parents=True, exist_ok=True)

        await self._run_git("worktree", "add", "-b", branch_name, str(wt_path))
        logger.info("Created worktree: issue #%d -> %s", issue_number, wt_path)

        # Set worktree's origin to tracker_repo
        remote_url = f"https://github.com/{self.tracker_repo}.git"
        # Worktree inherits parent's remote, so use set-url to override
        await self._run_git("remote", "set-url", "origin", remote_url, cwd=wt_path)
        logger.info("Set worktree remote: %s -> %s", wt_path, remote_url)

        # Copy .claude/skills to worktree for SDK auto-discovery
        await self._copy_skills_to_worktree(wt_path)

        return wt_path

    # Skills required by workflow - Agent uses these via Skill tool
    WORKFLOW_SKILLS = ["commit", "pull", "push", "land"]

    async def _copy_skills_to_worktree(self, wt_path: Path) -> None:
        """Copy required skills from main repo to worktree for SDK auto-discovery."""
        source_skills = self.repo_path / ".claude" / "skills"
        if not source_skills.exists():
            logger.warning("Skills directory not found: %s", source_skills)
            return

        target_skills = wt_path / ".claude" / "skills"
        target_skills.parent.mkdir(parents=True, exist_ok=True)

        # Only copy skills required by workflow
        for skill_name in self.WORKFLOW_SKILLS:
            source_skill = source_skills / skill_name
            target_skill = target_skills / skill_name

            if source_skill.exists():
                if target_skill.exists():
                    shutil.rmtree(target_skill)
                shutil.copytree(source_skill, target_skill)
                logger.info("Copied skill '%s' to worktree", skill_name)
            else:
                logger.warning("Required skill not found: %s", source_skill)

    async def get_worktree(self, issue_number: int) -> Optional[Path]:
        """Return existing worktree path. Returns None if not found."""
        wt_path = self._worktree_path(issue_number)
        if not wt_path.exists():
            return None

        # Verify actual registration via git worktree list
        # Use resolve() for comparison: prevents /var -> /private/var symlink mismatch on macOS
        worktrees = await self.list_worktrees()
        resolved = wt_path.resolve()
        for wt in worktrees:
            if Path(wt["path"]).resolve() == resolved:
                return wt_path
        return None

    async def cleanup_worktree(self, issue_number: int) -> None:
        """Remove worktree and prune."""
        wt_path = self._worktree_path(issue_number)

        try:
            await self._run_git("worktree", "remove", str(wt_path), "--force")
        except WorkspaceError:
            # Ignore if already deleted
            logger.warning("worktree remove failed (already deleted?): issue #%d", issue_number)

        await self._run_git("worktree", "prune")
        logger.info("Worktree cleanup done: issue #%d", issue_number)

    async def cleanup_orphans(self) -> list[Path]:
        """Clean up residual worktree directories not registered in git."""
        # First prune to remove stale references
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
                # Safety check: only delete issue-N pattern
                if re.match(r"^issue-\d+$", child.name):
                    shutil.rmtree(child)
                    cleaned.append(child)
                    logger.info("Deleted orphan worktree: %s", child)

        return cleaned

    async def list_worktrees(self) -> list[dict]:
        """Return list of currently registered worktrees."""
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
