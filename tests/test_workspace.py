"""WorkspaceManager 테스트.

실제 git 명령어를 사용하는 통합 테스트.
임시 디렉토리에 git repo를 만들어 worktree 기능을 검증한다.
"""

import asyncio
from pathlib import Path

import pytest

from lib.workspace import WorkspaceError, WorkspaceManager


@pytest.fixture
async def git_env(tmp_path: Path):
    """테스트용 git repo + workspace root를 준비한다."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    # git repo 초기화 + 최초 커밋 (worktree 생성에 필요)
    proc = await asyncio.create_subprocess_exec(
        "git", "init", cwd=repo,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "git", "config", "user.email", "test@test.com", cwd=repo,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "git", "config", "user.name", "Test", cwd=repo,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # 빈 커밋 생성
    readme = repo / "README.md"
    readme.write_text("test")
    proc = await asyncio.create_subprocess_exec(
        "git", "add", ".", cwd=repo,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", "init", cwd=repo,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    manager = WorkspaceManager(workspace_root=workspace_root, repo_path=repo)
    return manager, repo, workspace_root


async def test_create_worktree(git_env):
    """worktree 생성 후 디렉토리가 존재하고 git repo인지 확인."""
    manager, repo, _ = git_env

    wt_path = await manager.create_worktree(42, "feat/issue-42")

    assert wt_path.exists()
    assert (wt_path / ".git").exists()  # worktree는 .git 파일을 가짐
    assert "issue-42" in str(wt_path)


async def test_create_worktree_reuses_existing(git_env):
    """이미 존재하는 worktree는 재사용한다."""
    manager, _, _ = git_env

    path1 = await manager.create_worktree(42, "feat/issue-42")
    path2 = await manager.create_worktree(42, "feat/issue-42")

    assert path1 == path2


async def test_get_worktree_returns_none_when_missing(git_env):
    """존재하지 않는 worktree는 None을 반환한다."""
    manager, _, _ = git_env

    result = await manager.get_worktree(999)

    assert result is None


async def test_get_worktree_returns_path_when_exists(git_env):
    """존재하는 worktree는 경로를 반환한다."""
    manager, _, _ = git_env

    created = await manager.create_worktree(10, "feat/issue-10")
    found = await manager.get_worktree(10)

    assert found == created


async def test_cleanup_worktree(git_env):
    """cleanup 후 worktree가 제거된다."""
    manager, _, _ = git_env

    wt_path = await manager.create_worktree(7, "feat/issue-7")
    assert wt_path.exists()

    await manager.cleanup_worktree(7)

    assert not wt_path.exists()


async def test_cleanup_worktree_idempotent(git_env):
    """이미 삭제된 worktree를 다시 cleanup해도 에러 없음."""
    manager, _, _ = git_env

    await manager.create_worktree(7, "feat/issue-7")
    await manager.cleanup_worktree(7)
    # 두 번째 호출도 에러 없이 완료
    await manager.cleanup_worktree(7)


async def test_list_worktrees(git_env):
    """list_worktrees가 메인 + 생성한 worktree를 반환한다."""
    manager, _, _ = git_env

    await manager.create_worktree(1, "feat/issue-1")
    await manager.create_worktree(2, "feat/issue-2")

    worktrees = await manager.list_worktrees()

    # 메인 repo + 2개 worktree = 최소 3개
    assert len(worktrees) >= 3
    paths = [wt["path"] for wt in worktrees]
    assert any("issue-1" in p for p in paths)
    assert any("issue-2" in p for p in paths)


async def test_cleanup_orphans(git_env):
    """등록되지 않은 issue 디렉토리를 정리한다."""
    manager, repo, workspace_root = git_env

    # 고아 디렉토리 직접 생성 (git worktree에 등록되지 않음)
    orphan = workspace_root / repo.name / "issue-999"
    orphan.mkdir(parents=True)
    (orphan / "dummy.txt").write_text("orphan")

    cleaned = await manager.cleanup_orphans()

    assert orphan in cleaned
    assert not orphan.exists()


async def test_cleanup_orphans_ignores_non_issue_dirs(git_env):
    """issue-N 패턴이 아닌 디렉토리는 건드리지 않는다."""
    manager, repo, workspace_root = git_env

    # issue 패턴이 아닌 디렉토리
    other = workspace_root / repo.name / "some-other-dir"
    other.mkdir(parents=True)

    cleaned = await manager.cleanup_orphans()

    assert len(cleaned) == 0
    assert other.exists()


async def test_worktree_path_structure(git_env):
    """worktree 경로가 {workspace_root}/{repo_name}/issue-{N} 형태인지 확인."""
    manager, repo, workspace_root = git_env

    wt_path = await manager.create_worktree(123, "feat/issue-123")

    expected = workspace_root / repo.name / "issue-123"
    assert wt_path == expected


async def test_run_git_error_raises(git_env):
    """잘못된 git 명령은 WorkspaceError를 발생시킨다."""
    manager, _, _ = git_env

    with pytest.raises(WorkspaceError, match="실패"):
        await manager._run_git("worktree", "add", "/nonexistent/path")
