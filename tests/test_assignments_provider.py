"""Synthetic LMS metadata paths; no browser or account is contacted."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog, write_domain_catalog
from campusctl.envelope import CampusError
from campusctl.providers.cnu import assignments as provider

COURSES = [
    {"course_id": "course-a", "label": "Course A", "class_no": "01"},
    {"course_id": "course-b", "label": "Course B", "class_no": "02"},
]
FIXTURE = Path(__file__).parent / "fixtures" / "assignments" / "rows.html"


class FixtureRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self.in_table = False
        self.in_template = False
        self.in_link = False
        self.title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "template":
            self.in_template = True
        elif tag == "table" and attributes.get("id") == "table_list" and not self.in_template:
            self.in_table = True
        elif self.in_table and tag == "tr" and not self.in_template:
            self.rows.append({"task_id": None, "title": "", "due_date": None, "is_submitted": False})
        elif self.in_table and tag == "a" and attributes.get("data-act") == "detail" and not self.in_template:
            self.in_link = True
            self.rows[-1]["task_id"] = attributes.get("data-id")
        elif self.in_table and tag == "span" and "complete" in attributes.get("class", ""):
            self.rows[-1]["is_submitted"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_link:
            self.rows[-1]["title"] = "".join(self.title).strip()
            self.title = []
            self.in_link = False
        elif tag == "table":
            self.in_table = False
        elif tag == "template":
            self.in_template = False

    def handle_data(self, data: str) -> None:
        if self.in_link:
            self.title.append(data)


def fixture_rows() -> list[dict[str, Any]]:
    parser = FixtureRows()
    parser.feed(FIXTURE.read_text(encoding="utf-8"))
    parser.rows[0]["due_date"] = "2026-10-02 23:59"
    parser.rows[1]["is_submitted"] = True
    return parser.rows


def test_absent_data_id_fails_whole_course() -> None:
    rows = fixture_rows()
    assert len(rows) == 5
    rows.append({"task_id": None, "title": "Unaddressable"})
    with pytest.raises(CampusError, match="unique valid task ID") as failure:
        provider.parse_assignment_rows(rows, COURSES[0])
    assert failure.value.code == "item-identity-missing"


@pytest.mark.parametrize("native", [" ", "REPORT105", "TB_L_REPORT101", None])
def test_assignment_rows_and_identity_failure(native: str | None) -> None:
    rows = fixture_rows()
    normalized = provider.parse_assignment_rows(rows, COURSES[0])
    assert [row["task_id"] for row in normalized] == [f"TB_L_REPORT{n}" for n in range(101, 106)]
    assert normalized[1]["is_submitted"] and normalized[1]["due_date"] is None
    assert normalized[2]["due_date"] is None and not normalized[2]["is_submitted"]
    assert normalized[4]["title"] == "완료 보고서" and not normalized[4]["is_submitted"]
    assert normalized[0]["entity_id"] == "cnu_assignment:course-a:TB_L_REPORT101"
    with pytest.raises(CampusError) as failure:
        provider.parse_assignment_rows([*rows, {"task_id": native, "title": "Invalid"}], COURSES[0])
    assert failure.value.code == "item-identity-missing"


def _browser_fixture(monkeypatch: pytest.MonkeyPatch, server: Any) -> dict[str, Any]:
    from test_sync_all import _install_fixture

    return _install_fixture(monkeypatch, server)


def old_catalog(root: Path) -> None:
    write_domain_catalog(
        "assignments",
        {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [*COURSES, {"course_id": "course-old", "label": "Old course", "class_no": None}],
            "failed_courses": [],
            "assignments": [
                *provider.parse_assignment_rows([{"task_id": "TB_L_REPORT1", "title": "Old A"}], COURSES[0]),
                *provider.parse_assignment_rows([{"task_id": "TB_L_REPORT2", "title": "Old B"}], COURSES[1]),
                *provider.parse_assignment_rows(
                    [{"task_id": "TB_L_REPORT3", "title": "Old detached"}],
                    {"course_id": "course-old", "label": "Old course"},
                ),
            ],
        },
        domain_catalog_path("assignments", root),
    )


def test_standalone_rejects_unapproved_reviewed_policy_before_opening_browser(tmp_path: Path) -> None:
    with pytest.raises(CampusError) as failure:
        asyncio.run(provider.sync_assignments({}, tmp_path, reviewed_policy={"approved": False}))
    error = failure.value
    assert (error.code, error.message, error.remediation, error.status) == (
        "policy-unapproved",
        "Assignment request policy is not approved.",
        None,
        "user-action",
    )
    assert not (tmp_path / "catalog" / "assignments.json").exists()


def test_standalone_sync_normalizes_all_browser_courses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_sync_all import COURSES as BROWSER_COURSES, IDS, _expected_row, fixture_server
    from campusctl.commands.assignments import CAPABILITY

    with fixture_server() as server:
        config = _browser_fixture(monkeypatch, server)
        result, errors = asyncio.run(
            provider.sync_assignments(config, tmp_path, headless=True, reviewed_policy=CAPABILITY["policy"])
        )
        assert errors == []
        assert result["courses"] == 7 and result["assignments"] == 7
        catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        assert catalog["courses"] == BROWSER_COURSES
        assert catalog["assignments"] == [_expected_row("assignments", cid, n) for n, cid in enumerate(IDS, 1)]
        assert catalog["failed_courses"] == []
        assert config["_fixture_login_calls"] == ["login"]


def test_standalone_filtered_sync_preserves_cached_other_courses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_sync_all import IDS, _expected_row, fixture_server

    old_catalog(tmp_path)
    with fixture_server() as server:
        config = _browser_fixture(monkeypatch, server)
        result, errors = asyncio.run(provider.sync_assignments(config, tmp_path, IDS[2], headless=True))
        assert errors == []
        assert result["courses"] == 1 and result["assignments"] == 1
        catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        assert _expected_row("assignments", IDS[2], 3) in catalog["assignments"]
        assert {row["course"]["id"] for row in catalog["assignments"]} >= {"course-a", "course-b", "course-old", IDS[2]}
        assert catalog["enrollment_state"] == "known"
        with pytest.raises(CampusError) as failure:
            asyncio.run(provider.sync_assignments(config, tmp_path, "missing.invalid", headless=True))
        assert failure.value.code == "course-not-found"
        assert read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path)) == catalog


def test_standalone_guard_denial_never_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_sync_all import fixture_server

    old_catalog(tmp_path)
    before = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    with fixture_server() as server:
        server.unreviewed = True
        config = _browser_fixture(monkeypatch, server)
        with pytest.raises(CampusError) as failure:
            asyncio.run(provider.sync_assignments(config, tmp_path, headless=True))
        assert failure.value.code == "policy-blocked"
        assert read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path)) == before


@pytest.mark.parametrize(
    ("rows", "body", "failure"),
    [
        ([], {"body": {"list": []}}, None),
        ([{"task_id": "TB_L_REPORT901", "title": "Fallback task"}], None, None),
        ([], {"body": {"list": [{"task_id": "TB_L_REPORT901"}]}}, "not an observed empty course"),
    ],
)
def test_collector_accepts_proven_empty_or_inaccessible_body_but_rejects_count_mismatch(
    rows: list[dict[str, Any]], body: dict[str, Any] | None, failure: str | None
) -> None:
    origin = "https://lms.example.invalid"

    async def evaluate(script: str) -> Any:
        if script == provider.EXTRACT_COURSE_CONTEXT_JS:
            return "course-a"
        assert script == provider.EXTRACT_ASSIGNMENT_ROWS_JS
        return {"row_count": len(rows), "rows": rows}

    async def settle(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def decode() -> Any:
        if body is None:
            raise ValueError("Response body inaccessible")
        return body

    async def headers() -> dict[str, str]:
        return {"Referer": origin + "/std/task"}

    request = SimpleNamespace(url=origin + provider.TASK_RESPONSE_PATH, all_headers=headers)
    response = SimpleNamespace(request=request, status=200, finished=settle, json=decode)
    page = SimpleNamespace(wait_for_load_state=settle, wait_for_selector=settle, evaluate=evaluate)
    activity = provider._PageActivity(page)
    activity.task_commit_seq = 1
    activity.document_count = 1
    activity.std_after_document.append((2, request))
    activity.responses.append(response)
    guard = SimpleNamespace(raise_if_denied=lambda: None)

    async def exercise() -> None:
        if failure is not None:
            with pytest.raises(ValueError, match=failure):
                await provider._collect_assignment_rows(page, COURSES[0], activity, response, guard)
        else:
            result = await provider._collect_assignment_rows(page, COURSES[0], activity, response, guard)
            assert [row["title"] for row in result] == [row["title"] for row in rows]
            assert all(row["course"]["id"] == "course-a" for row in result)

    asyncio.run(exercise())


def test_prearmed_collector_rejects_other_selection_epoch() -> None:
    from campusctl.providers.cnu.course_context import CourseSelection

    listeners: dict[str, list[Any]] = defaultdict(list)
    page = SimpleNamespace(
        on=lambda event, callback: listeners[event].append(callback),
        remove_listener=lambda event, callback: listeners[event].remove(callback),
    )
    guard = SimpleNamespace(
        epoch=SimpleNamespace(
            number=9,
            selection_epoch=4,
            course_id="course-a",
            phase="bound",
            document_url="https://lms.example.invalid/std/task",
        ),
        raise_if_denied=lambda: None,
    )

    async def exercise() -> None:
        capture = provider.arm_assignment_capture(page, guard)
        with pytest.raises(ValueError, match="another course"):
            await provider.collect_assignment_rows(
                page, COURSES[0], CourseSelection("course-a", 1, 3, 2, 4), guard, capture=capture
            )
        assert all(not callbacks for callbacks in listeners.values())

    asyncio.run(exercise())


def test_failed_task_navigation_closes_armed_capture() -> None:
    from campusctl.providers.cnu.course_context import CourseSelection

    listeners: dict[str, list[Any]] = defaultdict(list)
    frame = SimpleNamespace(url="https://lms.example.invalid/std/lecture")
    page = SimpleNamespace(
        main_frame=frame,
        on=lambda event, callback: listeners[event].append(callback),
        remove_listener=lambda event, callback: listeners[event].remove(callback),
    )
    selected = CourseSelection("course-a", 1, 1, 2, 3)
    guard = SimpleNamespace(
        epoch=SimpleNamespace(
            number=2,
            phase="navigation",
            navigation_path="/std/task",
            frame=frame,
            course_id=selected.course_id,
            selection_epoch=selected.epoch,
            document_url=frame.url,
        ),
        raise_if_denied=lambda: None,
    )

    async def fail() -> None:
        raise RuntimeError("synthetic task navigation failed")

    async def exercise() -> None:
        capture = provider.arm_assignment_capture(page, guard)
        with pytest.raises(RuntimeError, match="synthetic task navigation failed"):
            await provider.open_assignment_section(page, guard, fail, capture=capture, selection=selected)
        assert all(not callbacks for callbacks in listeners.values())

    asyncio.run(exercise())
