# src/symphony/models.py
from datetime import datetime
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field, field_validator


class Blocker(BaseModel):
    id: str
    identifier: str
    state: str


class Issue(BaseModel):
    id: str
    identifier: str
    number: int
    title: str
    description: str | None = None
    state: str
    labels: list[str] = []
    priority: int | None = None
    branch_name: str | None = None
    url: str | None = None
    blocked_by: list[Blocker] = []
    created_at: datetime
    updated_at: datetime
    owner: str
    repo: str

    @field_validator("labels", mode="before")
    @classmethod
    def normalize_labels(cls, v: list[str]) -> list[str]:
        return [label.lower() for label in v]


class Workspace(BaseModel):
    path: Path
    workspace_key: str
    created_now: bool


class TokenCounts(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class RunningEntry(BaseModel):
    issue: Issue
    workspace_path: Path
    started_at: datetime
    turn_count: int = 0
    last_event: str | None = None
    last_event_at: datetime | None = None
    tokens: TokenCounts = Field(default_factory=TokenCounts)
    retry_attempt: int = 0


class RetryEntry(BaseModel):
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: float
    error: str | None = None


class ClaudeResult(BaseModel):
    success: bool
    events: list[dict[str, Any]] = []
    final_result: dict[str, Any] = {}
    token_usage: TokenCounts = Field(default_factory=TokenCounts)
    error: str | None = None
    stderr: str | None = None
