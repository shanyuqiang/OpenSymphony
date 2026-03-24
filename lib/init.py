"""symphonyctl init - 프로젝트 초기 설정.

비유: 신규 입사자의 첫날처럼, 필요한 도구(config)와
명찰(label)과 서류(template)를 자동으로 준비해준다.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import yaml


# 기본 설정 템플릿
_DEFAULT_CONFIG: dict = {
    "tracker": {
        "kind": "github",
        "repo": "",
        "trigger_label": "symphony:ready",
        "active_labels": ["symphony:in-progress"],
        "terminal_labels": ["symphony:done", "symphony:failed"],
    },
    "polling": {"interval_s": 30},
    "workspace": {"root": "~/symphony-workspaces"},
    "agent": {
        "max_concurrent": 2,
        "max_retries": 3,
        "retry_delay_s": 60,
        "max_budget_usd": 5,
        "model": "opus",
        "allowed_tools": "Bash(*),Read(*),Write(*),Edit(*),Glob(*),Grep(*)",
    },
    "notifier": {
        "github_comment": True,
        "slack_webhook_url": "",
        "events": ["succeeded", "failed", "escalated"],
    },
}

# symphony 라벨 목록
_LABELS = [
    ("symphony:ready", "0E8A16", "Symphony-CC 자동 처리 대기"),
    ("symphony:in-progress", "FBCA04", "Symphony-CC 처리 중"),
    ("symphony:done", "0075CA", "Symphony-CC 처리 완료"),
    ("symphony:failed", "D93F0B", "Symphony-CC 처리 실패"),
]


def _detect_repo() -> str:
    """gh CLI로 현재 리포지토리를 자동 감지한다."""
    try:
        result = subprocess.run(
            [
                "gh", "repo", "view",
                "--json", "nameWithOwner",
                "-q", ".nameWithOwner",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _prompt_value(prompt_text: str, default: str) -> str:
    """대화형 프롬프트. 기본값 제시 + Enter로 수락."""
    display = f"{prompt_text} [{default}]: " if default else f"{prompt_text}: "
    value = input(display).strip()
    return value if value else default


def _deep_copy_config() -> dict:
    """_DEFAULT_CONFIG의 깊은 복사본을 반환한다."""
    return yaml.safe_load(yaml.dump(_DEFAULT_CONFIG))


async def init_project(
    repo: str | None = None,
    budget: float | None = None,
    workspace_root: str | None = None,
    interactive: bool = True,
) -> Path:
    """프로젝트를 Symphony-CC용으로 초기화한다.

    Returns:
        생성된 config.yaml 경로.
    """
    config = _deep_copy_config()

    # 1. 리포지토리 감지/입력
    detected_repo = _detect_repo()
    if repo:
        config["tracker"]["repo"] = repo
    elif interactive:
        config["tracker"]["repo"] = _prompt_value(
            "GitHub 리포지토리 (owner/repo)", detected_repo,
        )
    else:
        config["tracker"]["repo"] = detected_repo

    if not config["tracker"]["repo"]:
        raise ValueError(
            "리포지토리를 지정해야 합니다 (--repo 또는 gh CLI 로그인 필요)"
        )

    # 2. 예산 설정
    if budget is not None:
        config["agent"]["max_budget_usd"] = budget
    elif interactive:
        budget_str = _prompt_value(
            "이슈당 최대 예산 (USD)",
            str(config["agent"]["max_budget_usd"]),
        )
        config["agent"]["max_budget_usd"] = float(budget_str)

    # 3. 워크스페이스 루트
    if workspace_root:
        config["workspace"]["root"] = workspace_root
    elif interactive:
        config["workspace"]["root"] = _prompt_value(
            "워크스페이스 루트 경로", config["workspace"]["root"],
        )

    # 4. config.yaml 생성
    config_path = Path.cwd() / "config.yaml"
    config_path.write_text(
        yaml.dump(
            config,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    print(f"config.yaml 생성: {config_path}")

    # 5. GitHub 라벨 생성
    await _create_labels(config["tracker"]["repo"])

    # 6. 이슈 템플릿 설치
    _install_issue_template()

    # 7. 슬래시 명령어 설치
    _install_slash_command()

    print(
        "\n초기화 완료! 이제 GitHub 이슈를 생성하고 "
        "'symphonyctl start -f'로 시작하세요."
    )
    return config_path


async def _create_labels(repo: str) -> None:
    """GitHub 라벨을 생성한다. 이미 존재하면 skip."""
    for name, color, description in _LABELS:
        proc = await asyncio.create_subprocess_exec(
            "gh", "label", "create", name,
            "--repo", repo,
            "--color", color,
            "--description", description,
            "--force",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            print(f"라벨 생성: {name}")
        else:
            err = stderr.decode().strip()
            if "already exists" in err.lower():
                print(f"  라벨 이미 존재: {name}")
            else:
                print(f"  라벨 생성 실패: {name} ({err})")


def _install_issue_template() -> None:
    """GitHub 이슈 폼 템플릿을 설치한다."""
    template_src = Path(__file__).parent.parent / "templates" / "symphony-task.yml"
    if not template_src.exists():
        print("  이슈 템플릿 소스 없음 (건너뜀)")
        return

    target_dir = Path.cwd() / ".github" / "ISSUE_TEMPLATE"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "symphony-task.yml"
    shutil.copy2(template_src, target)
    print(f"이슈 템플릿 설치: {target}")


def _install_slash_command() -> None:
    """Claude Code 슬래시 명령어를 설치한다."""
    cmd_src = Path(__file__).parent.parent / "templates" / "symphony-slash-command.md"
    if not cmd_src.exists():
        # 템플릿 없으면 인라인 생성
        cmd_dir = Path.home() / ".claude" / "commands"
        cmd_dir.mkdir(parents=True, exist_ok=True)
        cmd_path = cmd_dir / "symphony.md"
        if not cmd_path.exists():
            cmd_path.write_text(
                '---\ndescription: "Symphony-CC 오케스트레이션 제어"\n---\n'
                "사용자의 요청에 따라 symphonyctl CLI를 실행합니다.\n"
                "사용 가능한 명령어: status, start -f, dispatch <N>, "
                "logs --issue <N>, stop\n\n"
                "사용자 요청: $ARGUMENTS\n",
                encoding="utf-8",
            )
            print(f"슬래시 명령어 설치: {cmd_path}")
        else:
            print(f"  슬래시 명령어 이미 존재: {cmd_path}")
