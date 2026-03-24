"""설정 스키마 + YAML 로드 + 환경변수 오버라이드.

config.yaml을 읽어 SymphonyConfig dataclass로 변환하고,
SYMPHONY_* 환경변수로 값을 오버라이드할 수 있다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# --- 섹션별 설정 dataclass ---


@dataclass(frozen=True)
class TrackerConfig:
    """이슈 트래커 설정."""

    kind: str = "github"
    repo: str = ""
    trigger_label: str = "symphony:ready"
    active_labels: list[str] = field(default_factory=lambda: ["symphony:in-progress"])
    terminal_labels: list[str] = field(
        default_factory=lambda: ["symphony:done", "symphony:failed"]
    )


@dataclass(frozen=True)
class PollingConfig:
    """폴링 주기 설정."""

    interval_s: int = 30


@dataclass(frozen=True)
class WorkspaceConfig:
    """워크스페이스 경로 설정."""

    root: str = "~/symphony-workspaces"


@dataclass(frozen=True)
class AgentConfig:
    """에이전트 실행 설정."""

    max_concurrent: int = 2
    max_retries: int = 3
    retry_delay_s: int = 60
    max_budget_usd: float = 5.0
    model: str = "opus"
    allowed_tools: str = "Bash(*),Read(*),Write(*),Edit(*),Glob(*),Grep(*)"
    default_mode: str = "feature"
    default_max_iterations: int = 10


@dataclass(frozen=True)
class HooksConfig:
    """훅 스크립트 설정 (템플릿 문자열)."""

    after_create: str = ""
    before_run: str = ""
    after_run: str = ""


@dataclass(frozen=True)
class NotifierConfig:
    """알림 설정. 편지 배달부의 배달 규칙과 같다."""

    github_comment: bool = True
    slack_webhook_url: str = ""
    events: list[str] = field(default_factory=lambda: ["succeeded", "failed", "escalated"])


# --- 최상위 설정 ---


@dataclass(frozen=True)
class SymphonyConfig:
    """전체 설정을 담는 최상위 dataclass."""

    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)


# --- 환경변수 오버라이드 매핑 ---

# SYMPHONY_AGENT_MAX_CONCURRENT → agent.max_concurrent
_ENV_OVERRIDES: dict[str, tuple[str, str, type]] = {
    "SYMPHONY_AGENT_MAX_CONCURRENT": ("agent", "max_concurrent", int),
    "SYMPHONY_AGENT_MAX_RETRIES": ("agent", "max_retries", int),
    "SYMPHONY_AGENT_RETRY_DELAY_S": ("agent", "retry_delay_s", int),
    "SYMPHONY_AGENT_MAX_BUDGET_USD": ("agent", "max_budget_usd", float),
    "SYMPHONY_AGENT_MODEL": ("agent", "model", str),
    "SYMPHONY_POLLING_INTERVAL_S": ("polling", "interval_s", int),
    "SYMPHONY_WORKSPACE_ROOT": ("workspace", "root", str),
    "SYMPHONY_TRACKER_REPO": ("tracker", "repo", str),
    "SYMPHONY_NOTIFIER_SLACK_WEBHOOK": ("notifier", "slack_webhook_url", str),
}


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """환경변수 SYMPHONY_*로 설정값을 오버라이드한다."""
    for env_key, (section, field_name, cast) in _ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if value is not None:
            raw.setdefault(section, {})[field_name] = cast(value)
    return raw


def _build_config(raw: dict[str, Any]) -> SymphonyConfig:
    """raw 딕셔너리에서 SymphonyConfig를 생성한다."""
    tracker_data = raw.get("tracker", {}) or {}
    polling_data = raw.get("polling", {}) or {}
    workspace_data = raw.get("workspace", {}) or {}
    agent_data = raw.get("agent", {}) or {}
    hooks_data = raw.get("hooks", {}) or {}
    notifier_data = raw.get("notifier", {}) or {}

    return SymphonyConfig(
        tracker=TrackerConfig(**tracker_data),
        polling=PollingConfig(**polling_data),
        workspace=WorkspaceConfig(**workspace_data),
        agent=AgentConfig(**agent_data),
        hooks=HooksConfig(**hooks_data),
        notifier=NotifierConfig(**notifier_data),
    )


def load_config(path: str | Path) -> SymphonyConfig:
    """config.yaml 파일을 읽어 SymphonyConfig를 반환한다.

    Args:
        path: config.yaml 파일 경로.

    Returns:
        SymphonyConfig 인스턴스.

    Raises:
        FileNotFoundError: 파일이 없을 때.
        yaml.YAMLError: YAML 파싱 실패 시.
    """
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    raw = _apply_env_overrides(raw)
    return _build_config(raw)


def reload_config(path: str | Path) -> SymphonyConfig:
    """설정 파일을 다시 읽어 새 SymphonyConfig를 반환한다.

    frozen dataclass이므로 기존 인스턴스를 수정하지 않고
    새 인스턴스를 반환한다 (호출자가 교체해야 함).
    """
    return load_config(path)
