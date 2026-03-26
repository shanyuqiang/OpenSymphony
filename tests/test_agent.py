"""Tests for the agent module (claude_cli + runner)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from symphony.agent.claude_cli import _build_command, run_claude
from symphony.agent.runner import AgentRunner
from symphony.config import AgentConfig, ClaudeConfig, HooksConfig
from symphony.models import ClaudeResult, Issue, TokenCounts, Workspace
from symphony.workflow import Workflow, WorkflowLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_issue(number: int = 1) -> Issue:
    return Issue(
        id=str(number),
        identifier=f"owner/repo#{number}",
        number=number,
        title="Test issue",
        description="Do the thing",
        state="open",
        labels=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner="owner",
        repo="repo",
    )


def _make_workspace(tmp_path: Path) -> Workspace:
    return Workspace(path=tmp_path, workspace_key="owner_repo_1", created_now=True)


# ---------------------------------------------------------------------------
# _build_command
# ---------------------------------------------------------------------------

def test_build_command_defaults():
    config = ClaudeConfig()
    cmd = _build_command(config, "hello", max_turns=5)
    assert cmd[0] == "claude"
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--max-turns" in cmd
    assert "5" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "-p" in cmd
    assert "hello" in cmd


def test_build_command_no_dangerous():
    config = ClaudeConfig(dangerous_mode=False)
    cmd = _build_command(config, "hello", max_turns=5)
    assert "--dangerously-skip-permissions" not in cmd


def test_build_command_allowed_tools():
    config = ClaudeConfig(allowed_tools=["Edit", "Bash"])
    cmd = _build_command(config, "hello", max_turns=5)
    assert "--allowedTools" in cmd
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == "Edit,Bash"


# ---------------------------------------------------------------------------
# run_claude - mocked subprocess
# ---------------------------------------------------------------------------

def _make_stream_events(*events: dict) -> bytes:
    return b"".join(json.dumps(e).encode() + b"\n" for e in events)


class _FakeProcess:
    """Minimal asyncio.Process stand-in."""

    def __init__(self, stdout_data: bytes, stderr_data: bytes = b"", returncode: int = 0) -> None:
        self._stdout_data = stdout_data
        self._stderr_data = stderr_data
        self.returncode = returncode
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout_data)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr_data)
        self.stderr.feed_eof()

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        pass


@pytest.mark.asyncio
async def test_run_claude_success(tmp_path: Path):
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {}},
        {
            "type": "result",
            "subtype": "success",
            "result": "done",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    ]
    fake_proc = _FakeProcess(_make_stream_events(*events))
    config = ClaudeConfig()

    with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        result = await run_claude("do stuff", tmp_path, config, max_turns=5)

    assert result.success is True
    assert result.token_usage.input_tokens == 100
    assert result.token_usage.output_tokens == 50
    assert result.token_usage.total_tokens == 150


@pytest.mark.asyncio
async def test_run_claude_failure_subtype(tmp_path: Path):
    events = [
        {"type": "result", "subtype": "error_max_turns", "result": ""},
    ]
    fake_proc = _FakeProcess(_make_stream_events(*events))
    config = ClaudeConfig()

    with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        result = await run_claude("do stuff", tmp_path, config, max_turns=5)

    assert result.success is False


@pytest.mark.asyncio
async def test_run_claude_binary_not_found(tmp_path: Path):
    config = ClaudeConfig(command="/nonexistent/claude")
    result = await run_claude("do stuff", tmp_path, config, max_turns=5)
    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# AgentRunner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_runner_run(tmp_path: Path):
    import tempfile
    import textwrap

    workflow_content = textwrap.dedent("""\
        ---
        tracker:
          kind: gitea
          endpoint: http://localhost:3000/api/v1
          api_key: token
          owner: owner
          repo: repo
        ---
        Working on {{ issue.identifier }}: {{ issue.title }}
    """)
    wf_file = tmp_path / "WORKFLOW.md"
    wf_file.write_text(workflow_content)

    loader = WorkflowLoader()
    workflow = loader.load(wf_file)

    mock_result = ClaudeResult(
        success=True,
        token_usage=TokenCounts(input_tokens=10, output_tokens=5, total_tokens=15),
    )

    runner = AgentRunner(
        workflow=workflow,
        agent_config=AgentConfig(),
        claude_config=ClaudeConfig(),
        hooks=HooksConfig(),
    )

    issue = _make_issue(42)
    workspace = _make_workspace(tmp_path)

    with patch("symphony.agent.runner.run_claude", return_value=mock_result) as mock_run:
        result = await runner.run(issue, workspace, attempt=0)

    assert result.success is True
    call_kwargs = mock_run.call_args
    assert "owner/repo#42" in call_kwargs.kwargs["prompt"]
    assert "Test issue" in call_kwargs.kwargs["prompt"]


@pytest.mark.asyncio
async def test_before_run_hook_failure_aborts_attempt(tmp_path: Path):
    """§5.3.4: before_run hook failure must abort the attempt and return failure."""
    import textwrap

    workflow_content = textwrap.dedent("""\
        ---
        tracker:
          kind: gitea
          endpoint: http://localhost:3000/api/v1
          api_key: token
          owner: owner
          repo: repo
        ---
        Working on {{ issue.identifier }}
    """)
    wf_file = tmp_path / "WORKFLOW.md"
    wf_file.write_text(workflow_content)

    loader = WorkflowLoader()
    workflow = loader.load(wf_file)

    runner = AgentRunner(
        workflow=workflow,
        agent_config=AgentConfig(),
        claude_config=ClaudeConfig(),
        hooks=HooksConfig(before_run="exit 1"),  # will fail
    )

    issue = _make_issue(1)
    workspace = _make_workspace(tmp_path)

    with patch("symphony.agent.runner.run_claude") as mock_run:
        result = await runner.run(issue, workspace, attempt=0)

    # run_claude must NOT have been called
    mock_run.assert_not_called()
    assert result.success is False
    assert "before_run hook failed" in (result.error or "")
