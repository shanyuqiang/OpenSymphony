"""workflow.py 테스트."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lib.workflow import (
    WorkflowConfig,
    load_workflow,
    parse_frontmatter,
    parse_workflow,
    render_hooks,
    render_template,
    render_workflow,
)


# --- parse_frontmatter ---


class TestParseFrontmatter:
    def test_기본_분리(self) -> None:
        text = textwrap.dedent("""\
            ---
            key: value
            num: 42
            ---
            본문 내용입니다.
        """)
        fm, body = parse_frontmatter(text)
        assert fm == {"key": "value", "num": 42}
        assert "본문 내용입니다." in body

    def test_빈_frontmatter(self) -> None:
        text = "---\n---\n본문"
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body.strip() == "본문"

    def test_구분자_없으면_에러(self) -> None:
        with pytest.raises(ValueError, match="frontmatter"):
            parse_frontmatter("그냥 텍스트")

    def test_하나의_구분자만_있으면_에러(self) -> None:
        with pytest.raises(ValueError, match="frontmatter"):
            parse_frontmatter("---\nkey: value\n본문")

    def test_중첩_yaml(self) -> None:
        text = textwrap.dedent("""\
            ---
            tracker:
              kind: github
              repo: org/repo
            agent:
              max_retries: 3
            ---
            body
        """)
        fm, _ = parse_frontmatter(text)
        assert fm["tracker"]["kind"] == "github"
        assert fm["agent"]["max_retries"] == 3


# --- parse_workflow ---


class TestParseWorkflow:
    def test_known_keys_추출(self) -> None:
        text = textwrap.dedent("""\
            ---
            tracker:
              kind: github
            polling:
              interval_s: 30
            workspace:
              root: /tmp
            agent:
              max_concurrent: 2
            hooks:
              after_create: "echo hi"
            ---
            body
        """)
        config = parse_workflow(text)
        assert config.tracker == {"kind": "github"}
        assert config.polling == {"interval_s": 30}
        assert config.workspace == {"root": "/tmp"}
        assert config.agent == {"max_concurrent": 2}
        assert config.hooks == {"after_create": "echo hi"}
        assert "body" in config.body_template

    def test_알_수_없는_키는_raw에만(self) -> None:
        text = "---\ncustom_key: value\n---\nbody\n"
        config = parse_workflow(text)
        assert config.raw_frontmatter["custom_key"] == "value"

    def test_불변성(self) -> None:
        text = "---\nkey: val\n---\nbody\n"
        config = parse_workflow(text)
        with pytest.raises(AttributeError):
            config.body_template = "modified"  # type: ignore[misc]


# --- render_template ---


class TestRenderTemplate:
    def test_변수_치환(self) -> None:
        template = "제목: {{issue.title}}, 번호: {{issue.number}}"
        ctx = {"issue": {"title": "버그 수정", "number": 42}}
        result = render_template(template, ctx)
        assert result == "제목: 버그 수정, 번호: 42"

    def test_미해결_변수는_원본_유지(self) -> None:
        template = "{{unknown.var}} 테스트"
        result = render_template(template, {})
        assert result == "{{unknown.var}} 테스트"

    def test_if_블록_truthy(self) -> None:
        template = "앞{% if attempt %}재시도 #{{attempt}}{% endif %}뒤"
        result = render_template(template, {"attempt": 2})
        assert result == "앞재시도 #2뒤"

    def test_if_블록_falsy_none(self) -> None:
        template = "앞{% if attempt %}재시도{% endif %}뒤"
        result = render_template(template, {})
        assert result == "앞뒤"

    def test_if_블록_falsy_빈문자열(self) -> None:
        template = "{% if name %}이름: {{name}}{% endif %}"
        result = render_template(template, {"name": ""})
        assert result == ""

    def test_if_블록_falsy_0(self) -> None:
        template = "{% if count %}횟수: {{count}}{% endif %}"
        result = render_template(template, {"count": 0})
        assert result == ""

    def test_단순_변수(self) -> None:
        template = "값: {{value}}"
        result = render_template(template, {"value": "hello"})
        assert result == "값: hello"

    def test_여러_if_블록(self) -> None:
        template = "{% if a %}A{% endif %}-{% if b %}B{% endif %}"
        result = render_template(template, {"a": True, "b": False})
        assert result == "A-"

    def test_공백_포함_변수(self) -> None:
        template = "{{ issue.title }}"
        result = render_template(template, {"issue": {"title": "테스트"}})
        assert result == "테스트"


# --- render_workflow ---


class TestRenderWorkflow:
    def test_전체_렌더링(self) -> None:
        text = textwrap.dedent("""\
            ---
            agent:
              model: opus
            ---

            이슈 #{{issue.number}}: {{issue.title}}

            {% if attempt %}
            재시도 #{{attempt}}
            {% endif %}
        """)
        config = parse_workflow(text)
        ctx = {"issue": {"number": 7, "title": "버그"}, "attempt": 2}
        result = render_workflow(config, ctx)
        assert "이슈 #7: 버그" in result
        assert "재시도 #2" in result

    def test_attempt_없으면_조건블록_제거(self) -> None:
        text = textwrap.dedent("""\
            ---
            agent:
              model: opus
            ---

            본문
            {% if attempt %}
            재시도 #{{attempt}}
            {% endif %}
        """)
        config = parse_workflow(text)
        ctx = {"issue": {"number": 1, "title": "t"}}
        result = render_workflow(config, ctx)
        assert "본문" in result
        assert "재시도" not in result


# --- render_hooks ---


class TestRenderHooks:
    def test_hooks_렌더링(self) -> None:
        hooks = {
            "after_create": "git checkout -b feat/issue-{{issue.number}}",
            "after_run": "echo #{{issue.number}} done",
        }
        ctx = {"issue": {"number": 42}}
        result = render_hooks(hooks, ctx)
        assert result["after_create"] == "git checkout -b feat/issue-42"
        assert result["after_run"] == "echo #42 done"


# --- load_workflow (파일 I/O) ---


class TestLoadWorkflow:
    def test_파일_로드(self, tmp_path: Path) -> None:
        workflow_file = tmp_path / "WORKFLOW.md"
        workflow_file.write_text(
            "---\nagent:\n  model: opus\n---\n본문 {{issue.number}}\n",
            encoding="utf-8",
        )
        config = load_workflow(workflow_file)
        assert config.agent == {"model": "opus"}
        assert "{{issue.number}}" in config.body_template

    def test_존재하지_않는_파일(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_workflow("/nonexistent/path/WORKFLOW.md")
