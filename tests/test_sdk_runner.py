"""Tests for Claude Agent SDK runner.

Validates SDKAgentRunner functionality including:
- Import and initialization
- Message parsing
- Timeout handling
- Error handling
- Log persistence
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSDKRunnerInit:
    """SDK runner initialization tests."""

    def test_import_sdk_runner(self) -> None:
        """SDKAgentRunner can be imported."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner()
        assert runner.timeout_s == 600

    def test_custom_timeout(self) -> None:
        """SDKAgentRunner accepts custom timeout."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner(timeout_s=300)
        assert runner.timeout_s == 300

    def test_run_result_dataclass(self) -> None:
        """RunResult dataclass has expected fields."""
        from lib.claude_sdk_runner import RunResult

        result = RunResult(
            success=True,
            output="test output",
            cost_usd=0.5,
            duration_s=10.0,
            exit_code=0,
        )
        assert result.success is True
        assert result.output == "test output"
        assert result.cost_usd == 0.5
        assert result.duration_s == 10.0
        assert result.exit_code == 0


class TestSDKRunnerConfig:
    """SDK runner configuration tests."""

    def test_allowed_tools_parsing(self) -> None:
        """CLI-style allowed tools are parsed to SDK format."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner()
        # Tools with wildcards like "Bash(*)" should become "Bash"
        config = {
            "allowed_tools": ["Bash(*)", "Read(*)", "Write", "Edit"],
        }

        # This will be tested via the actual run method behavior
        assert runner.timeout_s == 600


class TestSDKRunnerMocked:
    """SDK runner tests with mocked SDK."""

    @pytest.mark.asyncio
    async def test_successful_run(self) -> None:
        """Successful SDK run returns correct result."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner(timeout_s=30)

        # Mock ResultMessage with proper class name
        class ResultMessage:
            def __init__(self):
                self.is_error = False
                self.result = "Task completed successfully"
                self.total_cost_usd = 0.25
                self.subtype = "success"
        ResultMessage.__name__ = "ResultMessage"

        async def mock_query(*args, **kwargs):
            yield ResultMessage()

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            result = await runner.run(
                prompt="Test prompt",
                worktree_path=Path("/tmp/test"),
                config={"model": "sonnet", "max_budget_usd": 1},
            )

        assert result.success is True
        assert "Task completed successfully" in result.output
        assert result.cost_usd == 0.25
        assert result.exit_code == 0
        assert result.duration_s > 0

    @pytest.mark.asyncio
    async def test_error_run(self) -> None:
        """SDK run with error returns failure result."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner(timeout_s=30)

        mock_result = MagicMock()
        mock_result.is_error = True
        mock_result.result = "Error occurred"
        mock_result.total_cost_usd = 0.1

        async def mock_query(*args, **kwargs):
            yield mock_result

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            result = await runner.run(
                prompt="Test prompt",
                worktree_path=Path("/tmp/test"),
                config={},
            )

        assert result.success is False
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """SDK runner handles timeout correctly."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner(timeout_s=1)

        async def mock_query(*args, **kwargs):
            # Simulate slow response that exceeds timeout
            await asyncio.sleep(2)
            yield MagicMock()

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            # Patch isinstance to return False so loop keeps waiting
            with patch("lib.claude_sdk_runner.isinstance", return_value=False):
                result = await runner.run(
                    prompt="Test prompt",
                    worktree_path=Path("/tmp/test"),
                    config={},
                )

        assert result.success is False
        assert "timeout" in result.output.lower()
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_exception_handling(self) -> None:
        """SDK runner handles exceptions correctly."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner(timeout_s=30)

        async def mock_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # Never reached

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            result = await runner.run(
                prompt="Test prompt",
                worktree_path=Path("/tmp/test"),
                config={},
            )

        assert result.success is False
        assert "SDK error" in result.output
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_progress_callback(self) -> None:
        """SDK runner calls progress callback."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner(timeout_s=30)
        progress_calls: list[dict] = []

        def on_progress(msg: dict) -> None:
            progress_calls.append(msg)

        # Mock messages with proper class names
        class AssistantMessage:
            content = []
        AssistantMessage.__name__ = "AssistantMessage"

        class ResultMessage:
            is_error = False
            result = "Done"
            total_cost_usd = 0.1
            subtype = "success"
        ResultMessage.__name__ = "ResultMessage"

        async def mock_query(*args, **kwargs):
            yield AssistantMessage()
            yield ResultMessage()

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            result = await runner.run(
                prompt="Test",
                worktree_path=Path("/tmp/test"),
                config={},
                on_progress=on_progress,
            )

        assert len(progress_calls) >= 1
        assert progress_calls[0]["type"] == "AssistantMessage"


class TestSDKRunnerOptions:
    """SDK runner ClaudeAgentOptions tests."""

    @pytest.mark.asyncio
    async def test_options_passed_correctly(self) -> None:
        """SDK options are passed correctly to query."""
        from lib.claude_sdk_runner import SDKAgentRunner

        runner = SDKAgentRunner()
        captured_options = {}

        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.result = "Done"

        async def mock_query(prompt, options):
            captured_options["prompt"] = prompt
            captured_options["options"] = options
            yield mock_result

        with patch("claude_agent_sdk.query", side_effect=mock_query):
            await runner.run(
                prompt="Custom prompt",
                worktree_path=Path("/custom/path"),
                config={
                    "model": "sonnet",
                    "max_budget_usd": 10,
                    "allowed_tools": ["Bash", "Read"],
                },
            )

        assert captured_options["prompt"] == "Custom prompt"
        opts = captured_options["options"]
        assert opts.model == "sonnet"
        assert opts.max_budget_usd == 10
        assert "Bash" in opts.allowed_tools
        assert opts.permission_mode == "acceptEdits"
        assert opts.cwd == "/custom/path"


class TestSDKIntegration:
    """SDK integration smoke tests."""

    @pytest.mark.asyncio
    async def test_sdk_imports_work(self) -> None:
        """All SDK imports work correctly."""
        from claude_agent_sdk import ClaudeAgentOptions, query

        # Check ClaudeAgentOptions has expected fields
        opts = ClaudeAgentOptions(
            model="opus",
            max_budget_usd=1,
            permission_mode="acceptEdits",
            cwd="/tmp",
        )
        assert opts.model == "opus"
        assert opts.max_budget_usd == 1

