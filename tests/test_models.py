# tests/test_models.py
from datetime import datetime
from symphony.models import Issue, Blocker, Workspace, TokenCounts


def test_issue_creation():
    issue = Issue(
        id="123",
        identifier="owner/repo#42",
        number=42,
        title="Test Issue",
        description="Test description",
        state="open",
        labels=["bug", "priority/high"],
        priority=2,
        url="http://localhost:3000/owner/repo/issues/42",
        blocked_by=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )
    assert issue.id == "123"
    assert issue.identifier == "owner/repo#42"
    assert issue.number == 42
    assert issue.priority == 2


def test_issue_labels_normalized():
    issue = Issue(
        id="123",
        identifier="owner/repo#1",
        number=1,
        title="Test",
        state="open",
        labels=["BUG", "Priority/High"],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )
    assert issue.labels == ["bug", "priority/high"]


def test_workspace_creation():
    from pathlib import Path
    workspace = Workspace(
        path=Path("/tmp/symphony/owner_repo_1"),
        workspace_key="owner_repo_1",
        created_now=True,
    )
    assert workspace.workspace_key == "owner_repo_1"
    assert workspace.created_now is True
