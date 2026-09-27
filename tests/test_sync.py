"""Lecture collector, normalized record, and CLI boundary regressions."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from campusctl import cli
from campusctl.envelope import CampusError
from campusctl.lock import exclusive_lock
from campusctl.providers.cnu import sync as sync_module
from campusctl.providers.cnu.course_context import _TOPBAR_COURSE_JS

COURSES = [
    {"course_id": "course-a", "label": "Course A", "class_no": None},
    {"course_id": "course-b", "label": "Course B", "class_no": "002"},
]


@pytest.fixture(autouse=True)
def provider_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl.providers.cnu import login

    monkeypatch.setattr(login, "MY_LECTURE_URL", "https://lms.example.invalid/std/myLecture")


def _row(row_id: str, *, state: str = "N", moduletype: str = "LV") -> dict[str, Any]:
    return {
        "id": row_id,
        "moduletype": moduletype,
        "state": state,
        "openyn": "Y",
        "weekno": "1",
        "seqno": "1",
        "title": f"Lecture {row_id}",
        "badge_text": "동영상",
        "progress_text": "0분/20분",
        "period_text": None,
        "late_until_text": None,
        "start_date": None,
        "row_text": f"Lecture {row_id} 0분/20분",
    }


class FakePage:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        empty: bool = False,
        topbar_id: str | None = "course-a",
        redirect_on_empty: str | None = None,
    ) -> None:
        self.rows = rows
        self.empty = empty
        self.topbar_id = topbar_id
        self.redirect_on_empty = redirect_on_empty
        self.main_frame = SimpleNamespace(url="https://lms.example.invalid/std/course")
        self.course_clicks: list[str] = []

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        assert selector == sync_module.LEARNING_ROW_SELECTOR
        assert kwargs == {"state": "attached", "timeout": sync_module.COURSE_ROOM_TIMEOUT_MS}
        if self.empty:
            if self.redirect_on_empty is not None:
                self.main_frame.url = self.redirect_on_empty
            raise PlaywrightTimeoutError("no lecture rows")

    async def evaluate(self, script: str) -> Any:
        if script == _TOPBAR_COURSE_JS:
            return self.topbar_id
        assert script == sync_module.EXTRACT_LEARNING_ROWS_JS
        return self.rows


def test_unsupported_domain_is_rejected_before_loading_config(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("unsupported domain must be validated first"))
    code = cli.main(["sync", "--only", "no-such-domain", "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 2
    assert envelope["errors"][0]["code"] == "unsupported-domain"


def test_busy_session_lock_returns_exit_75(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_path = tmp_path / "session.lock"
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {"browser": {"lock_path": str(lock_path)}})
    with exclusive_lock(lock_path):
        code = cli.main(["sync", "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 75
    assert envelope["status"] == "busy"
    assert envelope["errors"][0]["code"] == "session-busy"


def test_lecture_collector_uses_committed_section_without_another_selection() -> None:
    page = FakePage([_row("new-a"), _row("other", moduletype="AS")])
    rows = asyncio.run(sync_module.collect_lectures_rows(page, COURSES[0]))
    assert [row["entity_id"] for row in rows] == ["cnu_lecture:course-a:new-a"]
    assert page.course_clicks == []


def test_lecture_collector_empty() -> None:
    page = FakePage([], empty=True)
    assert asyncio.run(sync_module.collect_lectures_rows(page, COURSES[0])) == []


def test_empty_lecture_timeout_keeps_failed_span_and_successful_profile() -> None:
    from campusctl.browser import profile_context
    from campusctl.profiling import SpanRecorder

    recorder = SpanRecorder(enabled=True, scope=("lectures",))
    page = FakePage([], empty=True)

    async def scenario() -> list[dict[str, Any]]:
        with profile_context(recorder):
            return await sync_module.collect_lectures_rows(page, COURSES[0], ordinal=1)

    assert asyncio.run(scenario()) == []
    profile = recorder.finish(outcome="ok", stderr=io.StringIO())
    readiness = next(span for span in profile["spans"] if span["phase"] == "page-readiness")
    assert (readiness["page_kind"], readiness["wait_kind"], readiness["domain"], readiness["course"]) == (
        "lecture",
        "readiness",
        "lectures",
        1,
    )
    assert profile["outcome"] == "ok"
    wait = next(span for span in profile["spans"] if span["phase"] == "dom-ready")
    assert wait["failed"] is True
    assert wait["wait_kind"] == "selector"
    assert wait["domain"] == "lectures"
    assert wait["course"] == 1


def test_missing_lecture_topbar_fails_after_bounded_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl.providers.cnu import readiness

    monkeypatch.setattr(readiness, "READINESS_TIMEOUT_S", 0.025)
    page = FakePage([_row("new-a")], topbar_id=None)
    with pytest.raises(CampusError) as caught:
        asyncio.run(sync_module.collect_lectures_rows(page, COURSES[0]))
    assert caught.value.code == "browser-timeout"


def test_rowless_lecture_redirect_does_not_publish_empty_course() -> None:
    page = FakePage([], empty=True, redirect_on_empty="https://elsewhere.invalid/std/course")
    with pytest.raises(ValueError, match="document changed"):
        asyncio.run(sync_module.collect_lectures_rows(page, COURSES[0]))


def test_lecture_collector_keeps_non_counted_recorded_rows_out_of_incomplete_count() -> None:
    youtube = _row("non-counted")
    youtube["badge_text"] = "유튜브"
    youtube["progress_text"] = "20분/20분"
    youtube["row_text"] = "Lecture non-counted 20분/20분 출석 미반영"
    page = FakePage([youtube, _row("incomplete")])
    rows = asyncio.run(sync_module.collect_lectures_rows(page, COURSES[0]))
    assert [(record["completion"], record["provider_state"]) for record in rows] == [
        ("recorded", "N"),
        ("incomplete", "N"),
    ]
    assert sum(record["completion"] == "incomplete" for record in rows) == 1
