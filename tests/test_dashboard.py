"""Rich TUI 대시보드 테스트.

state/ 디렉토리에서 데이터를 읽고 UI 컴포넌트를 빌드하는 로직을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.dashboard import (
    DashboardApp,
    IssueState,
    StateReader,
    build_active_table,
    build_completed_table,
    build_layout,
    build_queue_table,
    build_stats_panel,
    build_summary_panel,
    _elapsed_str,
)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """테스트용 state 디렉토리 생성."""
    sd = tmp_path / "state"
    (sd / "active").mkdir(parents=True)
    (sd / "completed").mkdir(parents=True)
    return sd


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# --- StateReader 테스트 ---


class TestStateReader:
    """state/ 디렉토리 읽기 테스트."""

    def test_빈_디렉토리(self, state_dir: Path) -> None:
        reader = StateReader(state_dir)
        result = reader.read_all()
        assert result["queue"] == []
        assert result["active"] == []
        assert result["completed"] == []

    def test_queue_읽기(self, state_dir: Path) -> None:
        _write_json(state_dir / "queue.json", [
            {"issue_number": 1, "issue_title": "기능 A", "status": "QUEUED"},
            {"issue_number": 2, "issue_title": "기능 B", "status": "QUEUED"},
        ])

        reader = StateReader(state_dir)
        queue = reader.read_queue()
        assert len(queue) == 2
        assert queue[0].issue_number == 1
        assert queue[0].title == "기능 A"

    def test_active_읽기(self, state_dir: Path) -> None:
        _write_json(state_dir / "active" / "issue-5.json", {
            "issue_number": 5,
            "issue_title": "버그 수정",
            "state": "RUNNING",
            "started_at": "2026-03-05T10:00:00+00:00",
            "cost_usd": 1.23,
            "attempt": 2,
            "max_retries": 3,
        })

        reader = StateReader(state_dir)
        active = reader.read_active()
        assert len(active) == 1
        assert active[0].issue_number == 5
        assert active[0].title == "버그 수정"
        assert active[0].status == "RUNNING"
        assert active[0].cost_usd == 1.23
        assert active[0].attempt == 2
        assert active[0].max_retries == 3

    def test_completed_읽기(self, state_dir: Path) -> None:
        _write_json(state_dir / "completed" / "issue-3.json", {
            "issue_number": 3,
            "issue_title": "리팩토링",
            "state": "SUCCEEDED",
            "started_at": "2026-03-05T09:00:00+00:00",
            "completed_at": "2026-03-05T09:30:00+00:00",
            "cost_usd": 0.5,
        })
        _write_json(state_dir / "completed" / "issue-4.json", {
            "issue_number": 4,
            "issue_title": "실패 건",
            "state": "FAILED",
            "cost_usd": 0.1,
            "error": "timeout",
        })

        reader = StateReader(state_dir)
        completed = reader.read_completed()
        assert len(completed) == 2
        assert completed[0].status == "SUCCEEDED"
        assert completed[1].status == "FAILED"
        assert completed[1].error == "timeout"

    def test_잘못된_JSON_무시(self, state_dir: Path) -> None:
        (state_dir / "queue.json").write_text("not json", encoding="utf-8")
        (state_dir / "active" / "issue-1.json").write_text("{bad", encoding="utf-8")

        reader = StateReader(state_dir)
        assert reader.read_queue() == []
        assert reader.read_active() == []

    def test_존재하지_않는_디렉토리(self, tmp_path: Path) -> None:
        reader = StateReader(tmp_path / "nonexistent")
        assert reader.read_active() == []
        assert reader.read_completed() == []

    def test_하위_호환_title_키(self, state_dir: Path) -> None:
        """기존 title/status 키로도 읽기 가능한지 확인."""
        _write_json(state_dir / "queue.json", [
            {"issue_number": 10, "title": "레거시 큐", "status": "QUEUED"},
        ])
        _write_json(state_dir / "active" / "issue-11.json", {
            "issue_number": 11,
            "title": "레거시 활성",
            "status": "RUNNING",
        })
        _write_json(state_dir / "completed" / "issue-12.json", {
            "issue_number": 12,
            "title": "레거시 완료",
            "status": "SUCCEEDED",
        })

        reader = StateReader(state_dir)
        assert reader.read_queue()[0].title == "레거시 큐"
        assert reader.read_active()[0].title == "레거시 활성"
        assert reader.read_active()[0].status == "RUNNING"
        assert reader.read_completed()[0].title == "레거시 완료"
        assert reader.read_completed()[0].status == "SUCCEEDED"

    def test_attempt_기본값(self, state_dir: Path) -> None:
        """attempt/max_retries 미지정 시 기본값 확인."""
        _write_json(state_dir / "active" / "issue-20.json", {
            "issue_number": 20,
            "issue_title": "기본값 테스트",
            "state": "RUNNING",
        })

        reader = StateReader(state_dir)
        active = reader.read_active()
        assert active[0].attempt == 0
        assert active[0].max_retries == 3


# --- UI 빌더 테스트 ---


class TestUIBuilders:
    """UI 컴포넌트 빌드 테스트. Rich 객체가 에러 없이 생성되는지 확인."""

    def _sample_active(self) -> list[IssueState]:
        return [
            IssueState(issue_number=1, title="기능 A", status="RUNNING",
                       started_at="2026-03-05T10:00:00+00:00", cost_usd=1.0),
        ]

    def _sample_queue(self) -> list[IssueState]:
        return [IssueState(issue_number=2, title="대기", status="QUEUED")]

    def _sample_completed(self) -> list[IssueState]:
        return [
            IssueState(issue_number=3, title="완료", status="SUCCEEDED", cost_usd=0.5),
            IssueState(issue_number=4, title="실패", status="FAILED", cost_usd=0.1),
        ]

    def test_summary_panel_생성(self) -> None:
        panel = build_summary_panel(
            self._sample_active(),
            self._sample_queue(),
            self._sample_completed(),
        )
        assert panel is not None
        assert panel.title == "Symphony-CC"

    def test_active_table_생성(self) -> None:
        table = build_active_table(self._sample_active())
        assert table is not None
        assert table.row_count == 1

    def test_active_table_빈_목록(self) -> None:
        table = build_active_table([])
        assert table.row_count == 1  # "대기 중인 작업 없음" 행

    def test_completed_table_생성(self) -> None:
        table = build_completed_table(self._sample_completed())
        assert table.row_count == 2

    def test_completed_table_limit(self) -> None:
        many = [
            IssueState(issue_number=i, title=f"이슈 {i}", status="SUCCEEDED")
            for i in range(20)
        ]
        table = build_completed_table(many, limit=5)
        assert table.row_count == 5

    def test_stats_panel_생성(self) -> None:
        panel = build_stats_panel(self._sample_completed())
        assert panel is not None

    def test_stats_panel_빈_목록(self) -> None:
        panel = build_stats_panel([])
        assert panel is not None

    def test_active_table_attempt_표시(self) -> None:
        active = [
            IssueState(issue_number=1, title="재시도 중", status="RUNNING",
                       attempt=2, max_retries=3),
            IssueState(issue_number=2, title="첫 시도", status="RUNNING",
                       attempt=0, max_retries=3),
        ]
        table = build_active_table(active)
        assert table.row_count == 2

    def test_queue_table_생성(self) -> None:
        queue = [IssueState(issue_number=1, title="대기", status="QUEUED")]
        table = build_queue_table(queue)
        assert table.row_count == 1

    def test_queue_table_빈_목록(self) -> None:
        table = build_queue_table([])
        assert table.row_count == 1  # "대기 중인 이슈 없음" 행

    def test_build_layout_통합(self) -> None:
        state = {
            "active": self._sample_active(),
            "queue": self._sample_queue(),
            "completed": self._sample_completed(),
        }
        layout = build_layout(state)
        assert layout is not None


# --- DashboardApp 테스트 ---


class TestDashboardApp:
    """DashboardApp 기본 동작 테스트."""

    def test_render_once(self, state_dir: Path) -> None:
        _write_json(state_dir / "queue.json", [
            {"issue_number": 1, "title": "테스트", "status": "QUEUED"},
        ])

        app = DashboardApp(state_dir=state_dir)
        layout = app.render_once()
        assert layout is not None

    def test_stop(self, state_dir: Path) -> None:
        app = DashboardApp(state_dir=state_dir)
        assert app._running is False
        app._running = True
        app.stop()
        assert app._running is False


# --- 유틸리티 테스트 ---


class TestElapsedStr:
    """경과 시간 표시 유틸리티 테스트."""

    def test_None이면_대시(self) -> None:
        assert _elapsed_str(None) == "-"

    def test_잘못된_형식이면_대시(self) -> None:
        assert _elapsed_str("not-a-date") == "-"

    def test_정상_경과_시간(self) -> None:
        # 현재보다 충분히 과거 시간 설정
        result = _elapsed_str("2020-01-01T00:00:00+00:00")
        assert "h" in result or "m" in result  # 시간 또는 분 단위 포함
