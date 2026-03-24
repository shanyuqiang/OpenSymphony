"""구조화 로깅 시스템 테스트.

로그가 올바른 JSON 형식으로, 올바른 파일에 기록되는지 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.logger import StructuredLogger, get_logger


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    """임시 로그 디렉토리."""
    return tmp_path / "logs"


class TestStructuredLogger:
    """StructuredLogger 핵심 동작 테스트."""

    def test_daemon_log에_JSON_기록(self, log_dir: Path) -> None:
        logger = StructuredLogger(
            name="test",
            session_id="sess-001",
            log_dir=log_dir,
            console=False,
        )
        logger.info("테스트 메시지")

        daemon_log = log_dir / "daemon.log"
        assert daemon_log.exists()

        line = daemon_log.read_text().strip()
        entry = json.loads(line)
        assert entry["level"] == "INFO"
        assert entry["session_id"] == "sess-001"
        assert entry["message"] == "테스트 메시지"
        assert entry["issue_id"] is None
        assert "timestamp" in entry
        assert "extra" in entry

    def test_이슈별_로그_파일_생성(self, log_dir: Path) -> None:
        logger = StructuredLogger(
            name="test",
            session_id="sess-002",
            issue_id=42,
            log_dir=log_dir,
            console=False,
        )
        logger.info("이슈 작업 시작")

        # daemon.log에도 기록
        daemon_log = log_dir / "daemon.log"
        assert daemon_log.exists()

        # 이슈별 로그에도 기록
        issue_log = log_dir / "issue-42" / "agent.log"
        assert issue_log.exists()

        entry = json.loads(issue_log.read_text().strip())
        assert entry["issue_id"] == 42
        assert entry["message"] == "이슈 작업 시작"

    def test_모든_로그_레벨(self, log_dir: Path) -> None:
        logger = StructuredLogger(
            name="test",
            session_id="sess-003",
            log_dir=log_dir,
            console=False,
        )
        logger.debug("디버그")
        logger.info("정보")
        logger.warning("경고")
        logger.error("에러")

        lines = (log_dir / "daemon.log").read_text().strip().split("\n")
        assert len(lines) == 4

        levels = [json.loads(line)["level"] for line in lines]
        assert levels == ["DEBUG", "INFO", "WARNING", "ERROR"]

    def test_extra_데이터_기록(self, log_dir: Path) -> None:
        logger = StructuredLogger(
            name="test",
            session_id="sess-004",
            log_dir=log_dir,
            console=False,
        )
        logger.info("비용 기록", cost_usd=1.5, tokens=1000)

        entry = json.loads((log_dir / "daemon.log").read_text().strip())
        assert entry["extra"]["cost_usd"] == 1.5
        assert entry["extra"]["tokens"] == 1000

    def test_핸들러_중복_방지(self, log_dir: Path) -> None:
        """같은 이름으로 여러 번 생성해도 핸들러가 중복되지 않는다."""
        logger1 = StructuredLogger(
            name="dup-test",
            session_id="s1",
            log_dir=log_dir,
            console=False,
        )
        logger2 = StructuredLogger(
            name="dup-test",
            session_id="s2",
            log_dir=log_dir,
            console=False,
        )
        logger2.info("한 번만 기록")

        lines = (log_dir / "daemon.log").read_text().strip().split("\n")
        # 핸들러가 중복되면 2줄 이상 기록됨
        assert len(lines) == 1


class TestGetLogger:
    """get_logger 팩토리 함수 테스트."""

    def test_기본값으로_로거_생성(self, log_dir: Path) -> None:
        logger = get_logger("factory-test", log_dir=log_dir, console=False)
        assert isinstance(logger, StructuredLogger)
        assert logger.session_id == "default"
        assert logger.issue_id is None

    def test_issue_id_지정(self, log_dir: Path) -> None:
        logger = get_logger(
            "factory-test",
            session_id="sess-f",
            issue_id=7,
            log_dir=log_dir,
            console=False,
        )
        assert logger.issue_id == 7
        logger.info("테스트")

        issue_log = log_dir / "issue-7" / "agent.log"
        assert issue_log.exists()


class TestJsonFormat:
    """JSON 로그 포맷 스키마 검증."""

    def test_필수_필드_존재(self, log_dir: Path) -> None:
        logger = get_logger(
            "schema-test",
            session_id="s-schema",
            issue_id=99,
            log_dir=log_dir,
            console=False,
        )
        logger.info("스키마 검증")

        entry = json.loads((log_dir / "daemon.log").read_text().strip())
        required_keys = {"timestamp", "level", "session_id", "issue_id", "message", "extra"}
        assert required_keys.issubset(entry.keys())

    def test_timestamp_ISO8601_형식(self, log_dir: Path) -> None:
        logger = get_logger("ts-test", log_dir=log_dir, console=False)
        logger.info("시간 테스트")

        entry = json.loads((log_dir / "daemon.log").read_text().strip())
        ts = entry["timestamp"]
        # ISO 8601 기본 검증: T 포함, + 또는 Z 포함
        assert "T" in ts
