"""WORKFLOW.md 파서 + Liquid-style 템플릿 렌더링.

YAML frontmatter와 markdown body를 분리하고,
Liquid-style 변수/조건문을 렌더링한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class WorkflowConfig:
    """WORKFLOW.md 파싱 결과를 담는 불변 데이터 클래스."""

    # frontmatter에서 추출한 설정
    tracker: dict[str, Any] = field(default_factory=dict)
    polling: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, str] = field(default_factory=dict)

    # frontmatter 원본 (추가 키 포함)
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)

    # markdown body (템플릿 상태, 아직 렌더링 안 됨)
    body_template: str = ""


# --- YAML Frontmatter 파서 ---

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)---\s*\n(.*)\Z",
    re.DOTALL,
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """YAML frontmatter와 markdown body를 분리한다.

    Args:
        text: `---`로 감싼 frontmatter + body 전체 텍스트.

    Returns:
        (frontmatter_dict, body_string) 튜플.

    Raises:
        ValueError: frontmatter 구분자가 없을 때.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("유효한 YAML frontmatter를 찾을 수 없습니다 (--- 구분자 필요)")

    yaml_str, body = match.group(1), match.group(2)
    frontmatter = yaml.safe_load(yaml_str) or {}
    return frontmatter, body


def parse_workflow(text: str) -> WorkflowConfig:
    """WORKFLOW.md 텍스트를 파싱하여 WorkflowConfig를 반환한다."""
    frontmatter, body = parse_frontmatter(text)

    known_keys = ("tracker", "polling", "workspace", "agent", "hooks")
    kwargs: dict[str, Any] = {}
    for key in known_keys:
        if key in frontmatter:
            kwargs[key] = frontmatter[key]

    return WorkflowConfig(
        **kwargs,
        raw_frontmatter=frontmatter,
        body_template=body,
    )


def load_workflow(path: str | Path) -> WorkflowConfig:
    """파일 경로에서 WORKFLOW.md를 읽어 파싱한다."""
    return parse_workflow(Path(path).read_text(encoding="utf-8"))


# --- Liquid-style 템플릿 렌더링 ---

# {{variable.path}} 패턴
_VAR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")

# {% if var %}...{% endif %} 패턴 (중첩 미지원)
_IF_BLOCK_RE = re.compile(
    r"\{%\s*if\s+([\w.]+)\s*%\}(.*?)\{%\s*endif\s*%\}",
    re.DOTALL,
)


def _resolve_var(name: str, context: dict[str, Any]) -> Any:
    """점(.) 구분 경로로 context에서 값을 가져온다.

    예: "issue.title" → context["issue"]["title"]
    """
    parts = name.split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _is_truthy(value: Any) -> bool:
    """Liquid 스타일 truthy 판정. None, 빈 문자열, 0, False는 falsy."""
    if value is None:
        return False
    if isinstance(value, str) and value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value == 0:
        return False
    return True


def render_template(template: str, context: dict[str, Any]) -> str:
    """Liquid-style 템플릿을 렌더링한다.

    지원하는 문법:
    - {{variable}} 또는 {{object.key}} — 변수 치환
    - {% if variable %}...{% endif %} — 조건부 블록

    Args:
        template: 템플릿 문자열.
        context: 변수 딕셔너리 (예: {"issue": {"title": "...", "number": 42}}).

    Returns:
        렌더링된 문자열.
    """
    # 1단계: {% if %}...{% endif %} 처리
    def replace_if_block(match: re.Match[str]) -> str:
        var_name = match.group(1)
        block_content = match.group(2)
        value = _resolve_var(var_name, context)
        if _is_truthy(value):
            # 블록 내부의 변수도 치환
            return _replace_vars(block_content, context)
        return ""

    result = _IF_BLOCK_RE.sub(replace_if_block, template)

    # 2단계: {{variable}} 치환
    result = _replace_vars(result, context)

    return result


def _replace_vars(text: str, context: dict[str, Any]) -> str:
    """{{variable}} 패턴을 context 값으로 치환한다."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = _resolve_var(var_name, context)
        if value is None:
            return match.group(0)  # 미해결 변수는 원본 유지
        return str(value)

    return _VAR_RE.sub(replacer, text)


def render_workflow(
    config: WorkflowConfig,
    context: dict[str, Any],
) -> str:
    """WorkflowConfig의 body_template을 context로 렌더링한다."""
    return render_template(config.body_template, context)


def render_hooks(
    hooks: dict[str, str],
    context: dict[str, Any],
) -> dict[str, str]:
    """hooks 딕셔너리의 모든 값을 템플릿 렌더링한다."""
    return {key: render_template(value, context) for key, value in hooks.items()}
