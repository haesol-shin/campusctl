from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from campusctl import cli
from campusctl.catalog import catalog_path, read_catalog, write_catalog
from campusctl.envelope import CampusError
from campusctl.lock import exclusive_lock
from campusctl.providers.cnu import sync as sync_module

COURSES = [
    {"course_id": "course-a", "label": "Course A", "class_no": None},
    {"course_id": "course-b", "label": "Course B", "class_no": "002"},
]


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


def _lecture(course_id: str, row_id: str) -> dict[str, Any]:
    return {
        "entity_id": f"cnu_lecture:{course_id}:{row_id}",
        "course": {"id": course_id, "label": course_id},
        "kind": "lecture",
        "title": f"Old {row_id}",
        "week": None,
        "sequence": None,
        "progress_text": None,
        "duration_minutes": None,
        "available_from": None,
        "due_date": None,
        "late_until": None,
        "media": "video",
        "attendance_counted": None,
        "open": None,
        "completion": "incomplete",
        "provider_state": None,
    }


class FakePage:
    def __init__(
        self,
        rows_by_course: dict[str, list[dict[str, Any]]],
        *,
        empty_courses: set[str] | None = None,
        failed_courses: set[str] | None = None,
        stalled_selectors: dict[str, str] | None = None,
    ) -> None:
        self.rows_by_course = rows_by_course
        self.empty_courses = empty_courses or set()
        self.failed_courses = failed_courses or set()
        self.stalled_selectors = stalled_selectors or {}
        self.stalled: list[tuple[str, str]] = []
        self.current_course: str | None = None
        self.in_course_room = False
        self.course_clicks: list[str] = []

    async def click(self, selector: str) -> None:
        prefix = '[data-act="moveLecture"][data-courseid="'
        if selector.startswith(prefix):
            course_id = selector[len(prefix) : -2]
            self.course_clicks.append(course_id)
            self.current_course = course_id
            self.in_course_room = False
            if course_id in self.failed_courses:
                raise RuntimeError("sentinel raw LMS exception")
            return
        assert selector == sync_module.COURSE_ROOM_URL_ANCHOR
        self.in_course_room = True

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        assert self.current_course is not None
        if self.stalled_selectors.get(self.current_course) == selector:
            self.stalled.append((self.current_course, selector))
            await asyncio.Event().wait()
        if selector == sync_module.COURSE_ROOM_URL_ANCHOR:
            assert kwargs == {"timeout": sync_module.COURSE_ROOM_TIMEOUT_MS}
            return
        assert selector == sync_module.LEARNING_ROW_SELECTOR
        assert kwargs == {"state": "attached", "timeout": sync_module.COURSE_ROOM_TIMEOUT_MS}
        assert self.in_course_room
        if self.current_course in self.empty_courses:
            raise PlaywrightTimeoutError("no lecture rows")

    async def evaluate(self, script: str) -> list[dict[str, Any]]:
        assert script == sync_module.EXTRACT_LEARNING_ROWS_JS
        assert self.current_course is not None
        return self.rows_by_course[self.current_course]


def _install_fake_sync(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
    *,
    courses: list[dict[str, Any]] = COURSES,
    rows_by_course: dict[str, list[dict[str, Any]]] | None = None,
    empty_courses: set[str] | None = None,
    failed_courses: set[str] | None = None,
    stalled_selectors: dict[str, str] | None = None,
) -> FakePage:
    page = FakePage(
        rows_by_course or {},
        empty_courses=empty_courses,
        failed_courses=failed_courses,
        stalled_selectors=stalled_selectors,
    )

    @asynccontextmanager
    async def fake_open_session(config: dict[str, Any], *, data_dir: Path, headless: bool = False, operation: str):
        del config
        assert data_dir == data_dir_arg
        assert operation == "lectures.sync"
        assert headless in (True, False)
        yield SimpleNamespace(page=page)

    data_dir_arg = data_dir

    async def fake_login(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_discover(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return courses

    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(data_dir))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(sync_module, "open_session", fake_open_session)
    monkeypatch.setattr(sync_module, "ensure_logged_in", fake_login)
    monkeypatch.setattr(sync_module, "discover_courses", fake_discover)
    return page


@pytest.mark.parametrize("headless", [False, True])
def test_provider_modes_preserve_filtered_rows_and_failed_course(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headless: bool
) -> None:
    page = _install_fake_sync(
        monkeypatch,
        tmp_path,
        rows_by_course={"course-a": [_row("new-a")]},
        failed_courses={"course-b"},
    )
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "courses": COURSES,
            "lectures": [_lecture("course-b", "old-b")],
        },
        catalog_path(tmp_path),
    )
    original_open = sync_module.open_session

    @asynccontextmanager
    async def checked_open(*args: Any, **kwargs: Any):
        assert kwargs["headless"] is headless
        async with original_open(*args, **kwargs) as session:
            yield session

    monkeypatch.setattr(sync_module, "open_session", checked_open)
    result, errors = asyncio.run(sync_module.sync_lectures({}, tmp_path, headless=headless))
    assert page.course_clicks == ["course-a", "course-b"]
    assert result["failed_courses"] == [{"course_id": "course-b", "label": "Course B"}]
    assert [error.code for error in errors] == ["course-sync-failed"]
    stored = read_catalog(catalog_path(tmp_path))
    assert {row["entity_id"] for row in stored["lectures"]} == {
        "cnu_lecture:course-a:new-a",
        "cnu_lecture:course-b:old-b",
    }
    assert stored["failed_courses"][0]["reason"] == "course-sync-failed"


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main([*argv, "--json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    return exit_code, json.loads(captured.out)


def test_full_sync_writes_catalog_and_drops_unenrolled_courses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = _install_fake_sync(
        monkeypatch,
        tmp_path,
        rows_by_course={"course-a": [_row("a-1"), _row("other", moduletype="AS")]},
        empty_courses={"course-b"},
    )
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "courses": [{"course_id": "course-gone", "label": "Former course", "class_no": None}],
            "lectures": [_lecture("course-gone", "old")],
        },
        catalog_path(tmp_path),
    )

    code, envelope = _invoke(["sync"], capsys)

    assert code == 0
    assert envelope["status"] == "ok"
    assert envelope["result"]["courses"] == 2
    assert envelope["result"]["lectures"] == 1
    assert envelope["result"]["incomplete"] == 1
    assert envelope["result"]["failed_courses"] == []
    assert envelope["result"]["catalog"]["generated_at"]
    catalog = read_catalog(catalog_path(tmp_path))
    assert [course["course_id"] for course in catalog["courses"]] == ["course-a", "course-b"]
    assert [lecture["entity_id"] for lecture in catalog["lectures"]] == ["cnu_lecture:course-a:a-1"]
    assert catalog["enrollment_state"] == "known"
    assert catalog["failed_courses"] == []
    assert page.course_clicks == ["course-a", "course-b"]


def test_sync_count_excludes_fully_watched_not_counted_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    youtube = _row("youtube")
    youtube["badge_text"] = "유튜브"
    youtube["progress_text"] = "20분/20분"
    youtube["row_text"] = "Lecture youtube 20분/20분 출석 미반영"
    _install_fake_sync(
        monkeypatch,
        tmp_path,
        courses=COURSES[:1],
        rows_by_course={"course-a": [youtube, _row("incomplete")]},
    )

    code, envelope = _invoke(["sync"], capsys)

    assert code == 0
    assert envelope["result"]["lectures"] == 2
    assert envelope["result"]["incomplete"] == 1
    records = read_catalog(catalog_path(tmp_path))["lectures"]
    assert [(record["completion"], record["provider_state"]) for record in records] == [
        ("recorded", "N"),
        ("incomplete", "N"),
    ]


def test_partial_sync_keeps_failed_and_unenrolled_previous_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(
        monkeypatch,
        tmp_path,
        rows_by_course={"course-a": [_row("a-new")]},
        failed_courses={"course-b"},
    )
    old_courses = [*COURSES, {"course_id": "course-gone", "label": "Former course", "class_no": None}]
    old_lectures = [_lecture("course-a", "a-old"), _lecture("course-b", "b-old"), _lecture("course-gone", "gone")]
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "courses": old_courses,
            "lectures": old_lectures,
        },
        catalog_path(tmp_path),
    )

    code, envelope = _invoke(["sync"], capsys)

    assert code == 1
    assert envelope["status"] == "partial"
    assert envelope["errors"][0]["code"] == "course-sync-failed"
    assert "course-b" in envelope["errors"][0]["message"]
    assert "Course B" in envelope["errors"][0]["message"]
    assert "sentinel raw LMS exception" not in envelope["errors"][0]["message"]
    assert envelope["result"]["courses"] == 1
    assert envelope["result"]["failed_courses"] == [{"course_id": "course-b", "label": "Course B"}]
    catalog = read_catalog(catalog_path(tmp_path))
    assert {course["course_id"] for course in catalog["courses"]} == {"course-a", "course-b", "course-gone"}
    assert {lecture["entity_id"] for lecture in catalog["lectures"]} == {
        "cnu_lecture:course-a:a-new",
        "cnu_lecture:course-b:b-old",
        "cnu_lecture:course-gone:gone",
    }
    assert catalog["enrollment_state"] == "known"
    assert catalog["failed_courses"] == [
        {"course_id": "course-b", "label": "Course B", "reason": "course-sync-failed"},
        {"course_id": "course-gone", "label": "Former course", "reason": "removal-deferred"},
    ]


def test_course_filter_replaces_only_selected_course_and_unknown_course_leaves_catalog_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(monkeypatch, tmp_path, rows_by_course={"course-b": [_row("b-new")]})
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "courses": COURSES,
            "lectures": [_lecture("course-a", "a-old"), _lecture("course-b", "b-old")],
        },
        catalog_path(tmp_path),
    )

    code, envelope = _invoke(["sync", "--course", "course-b"], capsys)

    assert code == 0
    assert envelope["result"]["courses"] == 1
    assert read_catalog(catalog_path(tmp_path))["lectures"] == [
        _lecture("course-a", "a-old"),
        {
            "entity_id": "cnu_lecture:course-b:b-new",
            "course": {"id": "course-b", "label": "Course B"},
            "kind": "lecture",
            "title": "Lecture b-new",
            "week": "1",
            "sequence": "1",
            "progress_text": "0분/20분",
            "duration_minutes": 20,
            "available_from": None,
            "due_date": None,
            "late_until": None,
            "media": "video",
            "attendance_counted": None,
            "open": True,
            "completion": "incomplete",
            "provider_state": "N",
        },
    ]
    catalog = read_catalog(catalog_path(tmp_path))
    assert catalog["enrollment_state"] == "unknown"
    assert catalog["failed_courses"] == []

    before = catalog_path(tmp_path).read_bytes()
    code, envelope = _invoke(["sync", "--course", "not-enrolled"], capsys)

    assert code == 2
    assert envelope["status"] == "user-action"
    assert envelope["errors"][0]["code"] == "course-not-found"
    assert catalog_path(tmp_path).read_bytes() == before


def test_first_scoped_sync_cannot_claim_full_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(monkeypatch, tmp_path, courses=COURSES[:1], empty_courses={"course-a"})
    code, _ = _invoke(["sync", "--course", "course-a"], capsys)
    assert code == 0
    scoped = read_catalog(catalog_path(tmp_path))
    assert scoped["courses"] == COURSES[:1]
    assert scoped["enrollment_state"] == "unknown"
    assert scoped["failed_courses"] == []

    code, _ = _invoke(["sync"], capsys)
    assert code == 0
    full = read_catalog(catalog_path(tmp_path))
    assert full["enrollment_state"] == "known"
    assert full["failed_courses"] == []


def test_zero_row_course_is_a_successful_empty_course(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(monkeypatch, tmp_path, courses=COURSES[:1], empty_courses={"course-a"})

    code, envelope = _invoke(["sync"], capsys)

    assert code == 0
    assert envelope["result"]["courses"] == 1
    assert envelope["result"]["lectures"] == 0
    assert envelope["result"]["incomplete"] == 0
    assert read_catalog(catalog_path(tmp_path))["courses"] == COURSES[:1]
    assert read_catalog(catalog_path(tmp_path))["lectures"] == []
    catalog = read_catalog(catalog_path(tmp_path))
    assert catalog["enrollment_state"] == "known"
    assert catalog["failed_courses"] == []


def test_scoped_failure_and_success_keep_untouched_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(monkeypatch, tmp_path, failed_courses={"course-b"})
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "enrollment_state": "unknown",
            "courses": COURSES,
            "lectures": [_lecture("course-a", "a-old"), _lecture("course-b", "b-old")],
            "failed_courses": [{"course_id": "course-a", "label": "Course A", "reason": "course-sync-failed"}],
        },
        catalog_path(tmp_path),
    )
    code, _ = _invoke(["sync", "--course", "course-b"], capsys)
    assert code == 1
    catalog = read_catalog(catalog_path(tmp_path))
    assert catalog["enrollment_state"] == "unknown"
    assert {row["entity_id"] for row in catalog["lectures"]} == {
        "cnu_lecture:course-a:a-old",
        "cnu_lecture:course-b:b-old",
    }
    assert {item["course_id"] for item in catalog["failed_courses"]} == {"course-a", "course-b"}
    assert {item["reason"] for item in catalog["failed_courses"]} == {"course-sync-failed"}

    _install_fake_sync(monkeypatch, tmp_path, rows_by_course={"course-b": []})
    code, _ = _invoke(["sync", "--course", "course-b"], capsys)
    assert code == 0
    catalog = read_catalog(catalog_path(tmp_path))
    assert catalog["enrollment_state"] == "unknown"
    assert catalog["failed_courses"] == [{"course_id": "course-a", "label": "Course A", "reason": "course-sync-failed"}]
    assert [row["entity_id"] for row in catalog["lectures"]] == ["cnu_lecture:course-a:a-old"]


def test_discovery_failure_marks_full_enrollment_unknown_but_leaves_scoped_catalog_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(monkeypatch, tmp_path)
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": COURSES[:1],
            "lectures": [_lecture("course-a", "old")],
            "failed_courses": [],
        },
        catalog_path(tmp_path),
    )

    async def broken_discovery(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("private error contents")

    monkeypatch.setattr(sync_module, "discover_courses", broken_discovery)
    before = catalog_path(tmp_path).read_bytes()
    code, envelope = _invoke(["sync", "--course", "course-a"], capsys)
    assert code == 1
    assert envelope["errors"][0]["code"] == "course-discovery-failed"
    assert catalog_path(tmp_path).read_bytes() == before
    code, envelope = _invoke(["sync"], capsys)
    assert code == 1
    assert envelope["errors"][0]["code"] == "course-discovery-failed"
    assert "private error contents" not in json.dumps(envelope)
    catalog = read_catalog(catalog_path(tmp_path))
    assert catalog["generated_at"] == "2026-01-01T00:00:00Z"
    assert catalog["enrollment_state"] == "unknown"
    assert catalog["courses"] == COURSES[:1]
    assert catalog["lectures"] == [_lecture("course-a", "old")]


def test_full_success_resolves_legacy_unknown_and_removes_old_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(monkeypatch, tmp_path, courses=COURSES[:1], empty_courses={"course-a"})
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "courses": COURSES,
            "lectures": [_lecture("course-a", "old"), _lecture("course-b", "removed")],
            "failed_courses": [{"course_id": "course-a", "label": "Course A", "reason": "course-sync-failed"}],
        },
        catalog_path(tmp_path),
    )
    code, _ = _invoke(["sync"], capsys)
    assert code == 0
    catalog = read_catalog(catalog_path(tmp_path))
    assert catalog["enrollment_state"] == "known"
    assert catalog["failed_courses"] == []
    assert catalog["courses"] == COURSES[:1]
    assert catalog["lectures"] == []


@pytest.mark.parametrize(
    "stalled_selector",
    [sync_module.COURSE_ROOM_URL_ANCHOR, sync_module.LEARNING_ROW_SELECTOR],
)
def test_stalled_course_selector_is_bounded_and_sync_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stalled_selector: str
) -> None:
    page = _install_fake_sync(
        monkeypatch,
        tmp_path,
        rows_by_course={"course-b": [_row("b-new")]},
        stalled_selectors={"course-a": stalled_selector},
    )
    monkeypatch.setattr(sync_module, "COURSE_ROOM_TIMEOUT_MS", 1000)
    monkeypatch.setattr(sync_module, "PROTOCOL_TIMEOUT_SECONDS", 1)
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "courses": COURSES,
            "lectures": [_lecture("course-a", "a-old"), _lecture("course-b", "b-old")],
        },
        catalog_path(tmp_path),
    )

    async def guarded_sync() -> tuple[dict[str, Any], list[CampusError]]:
        return await asyncio.wait_for(sync_module.sync_lectures({}, tmp_path), timeout=10)

    result, errors = asyncio.run(guarded_sync())

    assert len(errors) == 1
    assert errors[0].code == "course-sync-failed"
    assert result["failed_courses"] == [{"course_id": "course-a", "label": "Course A"}]
    assert page.stalled == [("course-a", stalled_selector)]
    assert page.course_clicks == ["course-a", "course-b"]
    catalog = read_catalog(catalog_path(tmp_path))
    assert {lecture["entity_id"] for lecture in catalog["lectures"]} == {
        "cnu_lecture:course-a:a-old",
        "cnu_lecture:course-b:b-new",
    }


def test_login_failure_aborts_without_reading_or_writing_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_sync(monkeypatch, tmp_path)
    target = catalog_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("preserve this exact catalog", encoding="utf-8")

    async def failed_discovery(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise CampusError("login-failed", "The LMS rejected the configured credentials.")

    def no_catalog_read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("catalog read after login failure")

    def no_catalog_write(*_args: Any, **_kwargs: Any) -> Path:
        raise AssertionError("catalog write after login failure")

    monkeypatch.setattr(sync_module, "discover_courses", failed_discovery)
    monkeypatch.setattr(sync_module, "read_catalog", no_catalog_read)
    monkeypatch.setattr(sync_module, "write_catalog", no_catalog_write)

    code, envelope = _invoke(["sync"], capsys)

    assert code == 2
    assert envelope["errors"][0]["code"] == "login-failed"
    assert target.read_text(encoding="utf-8") == "preserve this exact catalog"


def test_unsupported_domain_is_rejected_before_loading_config(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("unsupported domain must be validated first"))

    code, envelope = _invoke(["sync", "--only", "no-such-domain"], capsys)

    assert code == 2
    assert envelope["errors"][0]["code"] == "unsupported-domain"


def test_busy_session_lock_returns_exit_75(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_path = tmp_path / "session.lock"
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {"browser": {"lock_path": str(lock_path)}})

    with exclusive_lock(lock_path):
        code, envelope = _invoke(["sync"], capsys)

    assert code == 75
    assert envelope["status"] == "busy"
    assert envelope["errors"][0]["code"] == "session-busy"
