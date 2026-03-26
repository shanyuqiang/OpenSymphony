# src/symphony/config.py
import os
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class TrackerConfig(BaseModel):
    kind: Literal["gitea"]
    endpoint: str = "http://localhost:3000/api/v1"
    api_key: str
    owner: str
    repo: str
    active_states: list[str] = Field(default_factory=lambda: ["open"])
    terminal_states: list[str] = Field(default_factory=lambda: ["closed"])

    @field_validator("api_key")
    @classmethod
    def resolve_env_var(cls, v: str) -> str:
        if v.startswith("$"):
            env_var = v[1:]
            env_value = os.environ.get(env_var)
            if not env_value:
                raise ValueError(f"Environment variable {env_var} not set")
            return env_value
        return v


class PollingConfig(BaseModel):
    interval_ms: int = 30000


class WorkspaceConfig(BaseModel):
    root: str = "~/symphony_workspaces"

    @field_validator("root")
    @classmethod
    def expand_path(cls, v: str) -> str:
        return os.path.expanduser(v)


class HooksConfig(BaseModel):
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = 60000


class AgentConfig(BaseModel):
    max_concurrent_agents: int = 10
    max_turns: int = 20
    max_retry_backoff_ms: int = 300000
    max_concurrent_agents_by_state: dict[str, int] = Field(default_factory=dict)


class ClaudeConfig(BaseModel):
    command: str = "claude"
    allowed_tools: list[str] | None = None
    dangerous_mode: bool = True
    turn_timeout_ms: int = 3600000
    read_timeout_ms: int = 5000
    stall_timeout_ms: int = 300000


class ServerConfig(BaseModel):
    port: int = 8080


class WorkflowConfig(BaseModel):
    tracker: TrackerConfig
    polling: PollingConfig = Field(default_factory=PollingConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    server: ServerConfig | None = None
