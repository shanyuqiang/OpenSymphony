# tests/test_workflow.py
"""Tests for WorkflowLoader and WorkflowWatcher hot reload."""
from __future__ import annotations

import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from symphony.workflow import WorkflowLoader, Workflow, WorkflowWatcher
from symphony.config import WorkflowConfig, TrackerConfig


def test_load_workflow_with_front_matter():
    content = """---
tracker:
  kind: gitea
  endpoint: http://localhost:3000/api/v1
  api_key: test_token
  owner: myuser
  repo: myrepo
---

You are working on issue {{ issue.identifier }}.
Title: {{ issue.title }}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        f.flush()
        path = Path(f.name)

    try:
        loader = WorkflowLoader()
        workflow = loader.load(path)

        assert workflow.config.tracker.kind == "gitea"
        assert workflow.config.tracker.owner == "myuser"
        assert "{{ issue.identifier }}" in workflow.prompt_template
    finally:
        path.unlink()


def test_load_workflow_missing_file():
    loader = WorkflowLoader()
    with pytest.raises(FileNotFoundError):
        loader.load(Path("/nonexistent/WORKFLOW.md"))


def test_load_workflow_invalid_yaml():
    content = """---
tracker: [
invalid yaml
---

Prompt here.
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        f.flush()
        path = Path(f.name)

    try:
        loader = WorkflowLoader()
        with pytest.raises(ValueError):
            loader.load(path)
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# WorkflowWatcher tests
# ---------------------------------------------------------------------------


def _make_workflow_file(tmp_path: Path, interval_ms: int = 5000) -> Path:
    content = textwrap.dedent(f"""\
        ---
        tracker:
          kind: gitea
          endpoint: http://localhost:3000/api/v1
          api_key: token
          owner: owner
          repo: repo
        polling:
          interval_ms: {interval_ms}
        ---
        Working on {{{{ issue.identifier }}}}
    """)
    wf_file = tmp_path / "WORKFLOW.md"
    wf_file.write_text(content)
    return wf_file


def test_workflow_watcher_reloads_config(tmp_path: Path):
    """File change triggers the on_reload callback with a new Workflow."""
    wf_file = _make_workflow_file(tmp_path, interval_ms=5000)

    reloaded = []
    loader = WorkflowLoader()
    watcher = WorkflowWatcher(path=wf_file, on_reload=reloaded.append, loader=loader)
    watcher.start()

    try:
        # Write new content with different interval
        new_content = textwrap.dedent("""\
            ---
            tracker:
              kind: gitea
              endpoint: http://localhost:3000/api/v1
              api_key: token
              owner: owner
              repo: repo
            polling:
              interval_ms: 9999
            ---
            Updated prompt {{ issue.identifier }}
        """)
        wf_file.write_text(new_content)

        # Wait for watchdog to fire (up to 3s)
        deadline = time.monotonic() + 3.0
        while not reloaded and time.monotonic() < deadline:
            time.sleep(0.1)

        assert len(reloaded) >= 1
        assert reloaded[-1].config.polling.interval_ms == 9999
    finally:
        watcher.stop()


def test_workflow_watcher_bad_yaml_no_crash(tmp_path: Path):
    """Invalid YAML reload does not call on_reload and does not crash."""
    wf_file = _make_workflow_file(tmp_path)

    reloaded = []
    loader = WorkflowLoader()
    watcher = WorkflowWatcher(path=wf_file, on_reload=reloaded.append, loader=loader)
    watcher.start()

    try:
        # Write invalid YAML
        wf_file.write_text("---\n: broken: yaml:\n---\nPrompt")

        # Wait 1.5s — no reload should fire
        time.sleep(1.5)
        assert len(reloaded) == 0
    finally:
        watcher.stop()
