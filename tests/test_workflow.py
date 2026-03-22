# tests/test_workflow.py
import tempfile
from pathlib import Path
import pytest
from symphony.workflow import WorkflowLoader, Workflow
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
