# src/symphony/workflow.py
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from symphony.config import WorkflowConfig

logger = logging.getLogger(__name__)


class Workflow:
    def __init__(self, config: WorkflowConfig, prompt_template: str, path: Path):
        self.config = config
        self.prompt_template = prompt_template
        self.path = path
        self._jinja_env = Environment(undefined=StrictUndefined)
        self._template = self._jinja_env.from_string(prompt_template)

    def render_prompt(self, issue: dict[str, Any], attempt: int | None = None) -> str:
        context = {"issue": issue}
        if attempt is not None:
            context["attempt"] = attempt
        return self._template.render(**context)


class WorkflowLoader:
    FRONT_MATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

    def load(self, path: Path) -> Workflow:
        if not path.exists():
            raise FileNotFoundError(f"Workflow file not found: {path}")

        content = path.read_text(encoding="utf-8")

        match = self.FRONT_MATTER_PATTERN.match(content)
        if match:
            yaml_content = match.group(1)
            prompt_template = match.group(2).strip()
            try:
                config_dict = yaml.safe_load(yaml_content)
                if not isinstance(config_dict, dict):
                    raise ValueError("YAML front matter must be a dictionary")
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML in front matter: {e}")
        else:
            config_dict = {}
            prompt_template = content.strip()

        config = WorkflowConfig(**config_dict)
        return Workflow(config=config, prompt_template=prompt_template, path=path)


class _ReloadHandler(FileSystemEventHandler):
    """Watchdog handler — reloads WORKFLOW.md on modification."""

    def __init__(
        self,
        path: Path,
        on_reload: Callable[["Workflow"], None],
        loader: "WorkflowLoader",
    ) -> None:
        super().__init__()
        self._path = path.resolve()
        self._on_reload = on_reload
        self._loader = loader

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if Path(event.src_path).resolve() != self._path:
            return
        try:
            workflow = self._loader.load(self._path)
            self._on_reload(workflow)
            logger.info("WORKFLOW.md reloaded from %s", self._path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "WORKFLOW.md reload failed (keeping old config): %s", exc
            )


class WorkflowWatcher:
    """Watches WORKFLOW.md for changes and calls on_reload with the new Workflow."""

    def __init__(
        self,
        path: Path,
        on_reload: Callable[["Workflow"], None],
        loader: WorkflowLoader | None = None,
    ) -> None:
        self._path = path.resolve()
        self._on_reload = on_reload
        self._loader = loader or WorkflowLoader()
        self._observer: Observer | None = None

    def start(self) -> None:
        handler = _ReloadHandler(self._path, self._on_reload, self._loader)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._path.parent), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
