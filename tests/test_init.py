"""lib/init.py 테스트.

비유: 신입사원 온보딩 키트가 제대로 준비되는지 확인하는 체크리스트.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from lib.init import _detect_repo, init_project


class TestDetectRepo:
    """_detect_repo() 테스트."""

    def test_성공시_리포명_반환(self) -> None:
        """gh CLI가 성공하면 owner/repo를 반환한다."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "qjc-office/symphony-cc\n"

        with patch("lib.init.subprocess.run", return_value=mock_result):
            assert _detect_repo() == "qjc-office/symphony-cc"

    def test_실패시_빈문자열_반환(self) -> None:
        """gh CLI가 실패하면 빈 문자열을 반환한다."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("lib.init.subprocess.run", return_value=mock_result):
            assert _detect_repo() == ""

    def test_gh없으면_빈문자열_반환(self) -> None:
        """gh CLI가 설치되지 않으면 빈 문자열을 반환한다."""
        with patch("lib.init.subprocess.run", side_effect=FileNotFoundError):
            assert _detect_repo() == ""

    def test_타임아웃시_빈문자열_반환(self) -> None:
        """gh CLI가 타임아웃되면 빈 문자열을 반환한다."""
        with patch(
            "lib.init.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=10),
        ):
            assert _detect_repo() == ""


class TestInitProject:
    """init_project() 테스트."""

    @pytest.fixture
    def work_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """임시 작업 디렉토리로 이동한다."""
        monkeypatch.chdir(tmp_path)
        return tmp_path

    async def test_config_yaml_생성(self, work_dir: Path) -> None:
        """non-interactive 모드에서 config.yaml이 정상 생성된다."""
        with patch("lib.init._create_labels", new_callable=AsyncMock), \
             patch("lib.init._install_issue_template"), \
             patch("lib.init._install_slash_command"), \
             patch("lib.init._detect_repo", return_value="owner/repo"):
            config_path = await init_project(
                repo="test-owner/test-repo",
                budget=3.0,
                workspace_root="/tmp/ws",
                interactive=False,
            )

        assert config_path.exists()
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config["tracker"]["repo"] == "test-owner/test-repo"
        assert config["agent"]["max_budget_usd"] == 3.0
        assert config["workspace"]["root"] == "/tmp/ws"

    async def test_리포_미지정시_에러(self, work_dir: Path) -> None:
        """리포지토리를 감지할 수 없고 지정도 안 했으면 ValueError."""
        with patch("lib.init._detect_repo", return_value=""), \
             patch("lib.init._create_labels", new_callable=AsyncMock), \
             patch("lib.init._install_issue_template"), \
             patch("lib.init._install_slash_command"):
            with pytest.raises(ValueError, match="리포지토리를 지정"):
                await init_project(interactive=False)

    async def test_기본값_적용(self, work_dir: Path) -> None:
        """budget/workspace 미지정 시 기본값이 적용된다."""
        with patch("lib.init._create_labels", new_callable=AsyncMock), \
             patch("lib.init._install_issue_template"), \
             patch("lib.init._install_slash_command"), \
             patch("lib.init._detect_repo", return_value="org/repo"):
            config_path = await init_project(
                repo="org/repo",
                interactive=False,
            )

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config["agent"]["max_budget_usd"] == 5
        assert config["workspace"]["root"] == "~/symphony-workspaces"


class TestCreateLabels:
    """_create_labels() 테스트."""

    async def test_라벨_4개_생성(self) -> None:
        """4개의 symphony 라벨을 gh CLI로 생성한다."""
        from lib.init import _create_labels

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await _create_labels("owner/repo")

        # 4개 라벨 생성 호출 확인
        assert mock_exec.call_count == 4
