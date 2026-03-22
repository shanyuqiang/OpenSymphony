# src/symphony/workflow.py
import re
from pathlib import Path
from typing import Any
import yaml
from jinja2 import Environment, StrictUndefined
from symphony.config import WorkflowConfig


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
