"""구조화 로깅 시스템.

방송국처럼 하나의 메시지를 여러 채널(콘솔, 전체 로그, 이슈별 로그)에
동시에 내보내는 로깅 모듈.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# 프로젝트 루트 기준 로그 디렉토리
_DEFAULT_LOG_DIR = Path(__file__).parent.parent / "logs"


class JsonFormatter(logging.Formatter):
    """JSON 구조화 로그 포맷터.

    일기장을 자유 형식이 아닌 정해진 양식에 맞춰 쓰는 것처럼,
    모든 로그를 동일한 JSON 스키마로 출력한다.
    """

    def __init__(self, session_id: str, issue_id: int | None = None) -> None:
        super().__init__()
        self.session_id = session_id
        self.issue_id = issue_id

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "session_id": self.session_id,
            "issue_id": getattr(record, "issue_id", self.issue_id),
            "message": record.getMessage(),
            "extra": getattr(record, "extra_data", {}),
        }
        return json.dumps(entry, ensure_ascii=False)


class RichConsoleFormatter(logging.Formatter):
    """콘솔용 사람이 읽기 쉬운 포맷터.

    신문 헤드라인처럼 핵심 정보만 한 줄에 보여준다.
    """

    _LEVEL_COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._LEVEL_COLORS.get(record.levelname, "")
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        issue_id = getattr(record, "issue_id", None)
        issue_tag = f" [#{issue_id}]" if issue_id else ""
        return (
            f"{color}{record.levelname:<7}{self._RESET} "
            f"{ts}{issue_tag} {record.getMessage()}"
        )


def _ensure_dir(path: Path) -> Path:
    """디렉토리가 없으면 생성한다."""
    path.mkdir(parents=True, exist_ok=True)
    return path


class StructuredLogger:
    """구조화 로거.

    편의점 CCTV처럼 전체 매장(daemon.log)과 특정 구역(issue별 로그)을
    동시에 녹화하는 로거.
    """

    def __init__(
        self,
        name: str,
        session_id: str,
        issue_id: int | None = None,
        log_dir: Path | None = None,
        console: bool = True,
    ) -> None:
        self.name = name
        self.session_id = session_id
        self.issue_id = issue_id
        self._log_dir = log_dir or _DEFAULT_LOG_DIR

        self._logger = logging.getLogger(f"symphony.{name}")
        self._logger.setLevel(logging.DEBUG)
        # 중복 핸들러 방지
        self._logger.handlers.clear()
        self._logger.propagate = False

        self._setup_handlers(console)

    def _setup_handlers(self, console: bool) -> None:
        """핸들러 설정: 콘솔 + 전체 로그 + 이슈별 로그."""
        json_fmt = JsonFormatter(self.session_id, self.issue_id)

        # 1) 전체 로그 파일 (daemon.log)
        daemon_path = _ensure_dir(self._log_dir) / "daemon.log"
        file_handler = logging.FileHandler(daemon_path, encoding="utf-8")
        file_handler.setFormatter(json_fmt)
        file_handler.setLevel(logging.DEBUG)
        self._logger.addHandler(file_handler)

        # 2) 이슈별 로그 파일
        if self.issue_id is not None:
            issue_dir = _ensure_dir(self._log_dir / f"issue-{self.issue_id}")
            issue_handler = logging.FileHandler(
                issue_dir / "agent.log", encoding="utf-8"
            )
            issue_handler.setFormatter(json_fmt)
            issue_handler.setLevel(logging.DEBUG)
            self._logger.addHandler(issue_handler)

        # 3) 콘솔 출력
        if console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(RichConsoleFormatter())
            console_handler.setLevel(logging.INFO)
            self._logger.addHandler(console_handler)

    def _log(
        self,
        level: int,
        message: str,
        issue_id: int | None = None,
        **extra: Any,
    ) -> None:
        """로그 기록. issue_id와 extra를 LogRecord에 주입한다."""
        record = self._logger.makeRecord(
            name=self._logger.name,
            level=level,
            fn="",
            lno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        record.issue_id = issue_id or self.issue_id  # type: ignore[attr-defined]
        record.extra_data = extra  # type: ignore[attr-defined]
        self._logger.handle(record)

    def debug(self, message: str, **extra: Any) -> None:
        self._log(logging.DEBUG, message, **extra)

    def info(self, message: str, **extra: Any) -> None:
        self._log(logging.INFO, message, **extra)

    def warning(self, message: str, **extra: Any) -> None:
        self._log(logging.WARNING, message, **extra)

    def error(self, message: str, **extra: Any) -> None:
        self._log(logging.ERROR, message, **extra)


def get_logger(
    name: str,
    session_id: str = "default",
    issue_id: int | None = None,
    log_dir: Path | None = None,
    console: bool = True,
) -> StructuredLogger:
    """로거 팩토리 함수.

    레스토랑 입구에서 좌석 번호(issue_id)를 받고
    해당 테이블 전용 주문표를 발급하는 것과 같다.
    """
    return StructuredLogger(
        name=name,
        session_id=session_id,
        issue_id=issue_id,
        log_dir=log_dir,
        console=console,
    )
