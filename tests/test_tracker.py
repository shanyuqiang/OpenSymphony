# tests/test_tracker.py
import pytest
import respx
from httpx import Response
from symphony.tracker.gitea import GiteaTracker
from symphony.config import TrackerConfig


@pytest.fixture
def tracker_config():
    return TrackerConfig(
        kind="gitea",
        endpoint="http://localhost:3000/api/v1",
        api_key="test_token",
        owner="testuser",
        repo="testrepo",
    )


@pytest.fixture
def tracker(tracker_config):
    return GiteaTracker(tracker_config)


@respx.mock
def test_fetch_candidate_issues(tracker):
    route = respx.get("http://localhost:3000/api/v1/repos/testuser/testrepo/issues").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 123,
                    "number": 42,
                    "title": "Test Issue",
                    "body": "Test description",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "html_url": "http://localhost:3000/testuser/testrepo/issues/42",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ],
        )
    )

    import asyncio
    issues = asyncio.run(tracker.fetch_candidate_issues())

    assert len(issues) == 1
    assert issues[0].id == "123"
    assert issues[0].number == 42
    assert issues[0].title == "Test Issue"
    assert route.called


@respx.mock
def test_add_label(tracker):
    route = respx.post(
        "http://localhost:3000/api/v1/repos/testuser/testrepo/issues/42/labels"
    ).mock(return_value=Response(200, json={"labels": ["symphony-doing"]}))

    import asyncio
    result = asyncio.run(tracker.add_label(42, "symphony-doing"))

    assert result is True
    assert route.called
