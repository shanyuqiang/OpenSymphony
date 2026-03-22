# tests/test_labels.py
from symphony.labels import LabelLifecycleManager, SYMPHONY_DOING_LABEL, SYMPHONY_DONE_LABEL
from symphony.models import Issue
from datetime import datetime


def test_should_dispatch_no_labels():
    issue = Issue(
        id="1",
        identifier="owner/repo#1",
        number=1,
        title="Test",
        state="open",
        labels=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )
    manager = LabelLifecycleManager(None)
    assert manager.should_dispatch(issue) is True


def test_should_dispatch_with_doing_label():
    issue = Issue(
        id="1",
        identifier="owner/repo#1",
        number=1,
        title="Test",
        state="open",
        labels=[SYMPHONY_DOING_LABEL],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )
    manager = LabelLifecycleManager(None)
    assert manager.should_dispatch(issue) is False


def test_should_dispatch_with_done_label():
    issue = Issue(
        id="1",
        identifier="owner/repo#1",
        number=1,
        title="Test",
        state="open",
        labels=[SYMPHONY_DONE_LABEL],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )
    manager = LabelLifecycleManager(None)
    assert manager.should_dispatch(issue) is False


def test_is_completed():
    issue = Issue(
        id="1",
        identifier="owner/repo#1",
        number=1,
        title="Test",
        state="open",
        labels=[SYMPHONY_DONE_LABEL],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        owner="owner",
        repo="repo",
    )
    manager = LabelLifecycleManager(None)
    assert manager.is_completed(issue) is True
