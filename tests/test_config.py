# tests/test_config.py
import os
import pytest
from symphony.config import WorkflowConfig, TrackerConfig, ClaudeConfig
from pydantic import ValidationError


def test_tracker_config_with_literal_api_key():
    config = TrackerConfig(
        kind="gitea",
        endpoint="http://localhost:3000/api/v1",
        api_key="secret_token",
        owner="myuser",
        repo="myrepo",
    )
    assert config.kind == "gitea"
    assert config.api_key == "secret_token"


def test_tracker_config_with_env_var():
    os.environ["GITEA_TOKEN"] = "from_env"
    config = TrackerConfig(
        kind="gitea",
        endpoint="http://localhost:3000/api/v1",
        api_key="$GITEA_TOKEN",
        owner="myuser",
        repo="myrepo",
    )
    assert config.api_key == "from_env"
    del os.environ["GITEA_TOKEN"]


def test_tracker_config_invalid_kind():
    with pytest.raises(ValidationError):
        TrackerConfig(
            kind="github",
            endpoint="http://localhost:3000/api/v1",
            api_key="token",
            owner="myuser",
            repo="myrepo",
        )


def test_claude_config_defaults():
    config = ClaudeConfig()
    assert config.command == "claude"
    assert config.dangerous_mode is True
    assert config.turn_timeout_ms == 3600000
