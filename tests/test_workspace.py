# tests/test_workspace.py
from pathlib import Path
import pytest
from symphony.workspace import sanitize_identifier, ensure_safe_path, WorkspaceManager, WorkspaceError
from symphony.config import WorkspaceConfig, HooksConfig
from symphony.models import Issue
from datetime import datetime


def test_sanitize_identifier():
    assert sanitize_identifier("owner/repo#123") == "owner_repo_123"
    assert sanitize_identifier("my-issue") == "my-issue"
    assert sanitize_identifier("test.file") == "test.file"
    assert sanitize_identifier("a/b/c#1") == "a_b_c_1"
    # Test length limit
    long_name = "very" * 50 + "#1"
    result = sanitize_identifier(long_name)
    assert len(result) <= 100


def test_ensure_safe_path_within_root():
    root = Path("/tmp/symphony")
    target = Path("/tmp/symphony/owner_repo_123")
    assert ensure_safe_path(root, target) is True


def test_ensure_safe_path_outside_root():
    root = Path("/tmp/symphony")
    target = Path("/tmp/other/path")
    assert ensure_safe_path(root, target) is False


def test_ensure_safe_path_traversal():
    root = Path("/tmp/symphony")
    target = Path("/tmp/symphony/../../etc/passwd")
    assert ensure_safe_path(root, target) is False


@pytest.mark.asyncio
async def test_workspace_manager_create(tmp_path):
    config = WorkspaceConfig(root=str(tmp_path / "workspaces"))
    manager = WorkspaceManager(config)

    issue = Issue(
        id="123",
        identifier="owner/repo#1",
        number=1,
        title="Test Issue",
        state="open",
        labels=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )

    workspace = await manager.create_for_issue(issue)

    assert workspace.path.exists()
    assert workspace.workspace_key == "owner_repo_1"
    assert workspace.created_now is True


@pytest.mark.asyncio
async def test_workspace_manager_reuse(tmp_path):
    config = WorkspaceConfig(root=str(tmp_path / "workspaces"))
    manager = WorkspaceManager(config)

    issue = Issue(
        id="123",
        identifier="owner/repo#1",
        number=1,
        title="Test Issue",
        state="open",
        labels=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )

    # First creation
    workspace1 = await manager.create_for_issue(issue)
    assert workspace1.created_now is True

    # Second creation should reuse
    workspace2 = await manager.create_for_issue(issue)
    assert workspace2.created_now is False
    assert workspace1.path == workspace2.path
