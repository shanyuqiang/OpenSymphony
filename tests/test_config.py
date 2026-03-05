"""config.py 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.config import (
    AgentConfig,
    HooksConfig,
    NotifierConfig,
    PollingConfig,
    SymphonyConfig,
    TrackerConfig,
    WorkspaceConfig,
    load_config,
    reload_config,
)

SAMPLE_YAML = """\
tracker:
  kind: github
  repo: qjc-office/qjc-webapp
  trigger_label: "symphony:ready"
  active_labels:
    - "symphony:in-progress"
  terminal_labels:
    - "symphony:done"
    - "symphony:failed"

polling:
  interval_s: 30

workspace:
  root: ~/symphony-workspaces

agent:
  max_concurrent: 2
  max_retries: 3
  retry_delay_s: 60
  max_budget_usd: 5
  model: opus
  allowed_tools: "Bash(*),Read(*),Write(*),Edit(*),Glob(*),Grep(*)"

hooks:
  after_create: "git checkout -b feat/issue-1"
  before_run: "git pull origin main --rebase"
  after_run: "echo done"
"""


class TestLoadConfig:
    def test_전체_로드(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")

        config = load_config(cfg_file)

        assert isinstance(config, SymphonyConfig)
        assert config.tracker.kind == "github"
        assert config.tracker.repo == "qjc-office/qjc-webapp"
        assert config.polling.interval_s == 30
        assert config.workspace.root == "~/symphony-workspaces"
        assert config.agent.max_concurrent == 2
        assert config.agent.max_retries == 3
        assert config.agent.model == "opus"
        assert config.hooks.after_create == "git checkout -b feat/issue-1"

    def test_기본값_사용(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("{}", encoding="utf-8")

        config = load_config(cfg_file)
        assert config.agent.max_concurrent == 2
        assert config.polling.interval_s == 30
        assert config.tracker.kind == "github"

    def test_빈_파일(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("", encoding="utf-8")

        config = load_config(cfg_file)
        assert isinstance(config, SymphonyConfig)

    def test_존재하지_않는_파일(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")


class TestEnvOverride:
    def test_agent_max_concurrent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
        monkeypatch.setenv("SYMPHONY_AGENT_MAX_CONCURRENT", "4")

        config = load_config(cfg_file)
        assert config.agent.max_concurrent == 4

    def test_polling_interval(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
        monkeypatch.setenv("SYMPHONY_POLLING_INTERVAL_S", "60")

        config = load_config(cfg_file)
        assert config.polling.interval_s == 60

    def test_workspace_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
        monkeypatch.setenv("SYMPHONY_WORKSPACE_ROOT", "/custom/path")

        config = load_config(cfg_file)
        assert config.workspace.root == "/custom/path"

    def test_tracker_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
        monkeypatch.setenv("SYMPHONY_TRACKER_REPO", "other/repo")

        config = load_config(cfg_file)
        assert config.tracker.repo == "other/repo"

    def test_budget_float(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
        monkeypatch.setenv("SYMPHONY_AGENT_MAX_BUDGET_USD", "10.5")

        config = load_config(cfg_file)
        assert config.agent.max_budget_usd == 10.5


class TestReloadConfig:
    def test_리로드_새_인스턴스(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")

        config1 = load_config(cfg_file)

        # 파일 수정
        cfg_file.write_text(
            SAMPLE_YAML.replace("max_concurrent: 2", "max_concurrent: 4"),
            encoding="utf-8",
        )

        config2 = reload_config(cfg_file)

        assert config1.agent.max_concurrent == 2
        assert config2.agent.max_concurrent == 4
        assert config1 is not config2


class TestNotifierConfig:
    def test_기본값(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("{}", encoding="utf-8")

        config = load_config(cfg_file)
        assert config.notifier.github_comment is True
        assert config.notifier.slack_webhook_url == ""
        assert config.notifier.events == ["succeeded", "failed", "escalated"]

    def test_yaml에서_로드(self, tmp_path: Path) -> None:
        yaml_content = """\
notifier:
  github_comment: false
  slack_webhook_url: "https://hooks.slack.com/test"
  events:
    - succeeded
"""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        config = load_config(cfg_file)
        assert config.notifier.github_comment is False
        assert config.notifier.slack_webhook_url == "https://hooks.slack.com/test"
        assert config.notifier.events == ["succeeded"]

    def test_환경변수_slack_webhook(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("SYMPHONY_NOTIFIER_SLACK_WEBHOOK", "https://hooks.slack.com/env")

        config = load_config(cfg_file)
        assert config.notifier.slack_webhook_url == "https://hooks.slack.com/env"


class TestAgentConfigDefaults:
    def test_default_mode_and_max_iterations(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("{}", encoding="utf-8")

        config = load_config(cfg_file)
        assert config.agent.default_mode == "feature"
        assert config.agent.default_max_iterations == 10

    def test_yaml에서_오버라이드(self, tmp_path: Path) -> None:
        yaml_content = """\
agent:
  default_mode: bugfix
  default_max_iterations: 20
"""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        config = load_config(cfg_file)
        assert config.agent.default_mode == "bugfix"
        assert config.agent.default_max_iterations == 20


class TestImmutability:
    def test_frozen(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")

        config = load_config(cfg_file)
        with pytest.raises(AttributeError):
            config.agent = AgentConfig()  # type: ignore[misc]
