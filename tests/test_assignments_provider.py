"""Synthetic LMS metadata paths; no browser or account is contacted."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
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
ORIGIN = "https://dcs-learning.cnu.ac.kr"
POLICY = {
    "approved": True,
    "read_only_evidence": "Synthetic approved task metadata fixture",
    "origins": [ORIGIN],
    "routes": [
        {"origin": ORIGIN, "path": path, "operation": "assignments.sync", "methods": [method]}
        for path, method in [("/std/myLecture", "GET"), ("/std/task", "GET"), ("/api/v1/task/stdList", "POST")]
    ],
    "allowed_media": [],
    "max_bytes": None,
}


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


class FakePage:
    def __init__(self, courses: list[dict[str, Any]], scenarios: dict[str, dict[str, Any]]) -> None:
        self.courses = courses
        self.scenarios = scenarios
        self.current = ""
        self.listeners: dict[str, list[Any]] = defaultdict(list)
        self.response_future: asyncio.Future[Any] | None = None
        self.logins = 0
        self.context = None
        self.main_frame = SimpleNamespace(url=provider.MY_LECTURE_URL)
        self.elapsed_ms = 0

    def on(self, event: str, callback: Any) -> None:
        self.listeners[event].append(callback)

    def remove_listener(self, event: str, callback: Any) -> None:
        self.listeners[event].remove(callback)

    def fire(self, event: str, request: Any) -> None:
        for callback in tuple(self.listeners[event]):
            callback(request)

    async def goto(self, url: str) -> None:
        assert url == provider.MY_LECTURE_URL
        if self.scenarios.get("roster_error"):
            raise RuntimeError("roster failed")
        self.main_frame.url = url

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        if selector == provider.TASK_TABLE_SELECTOR and self.scenarios[self.current].get("missing_table"):
            raise TimeoutError("table missing")

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        assert state == "networkidle" and kwargs["timeout"] == provider.COURSE_WAIT_MS

    async def evaluate(self, script: str) -> Any:
        if script == provider.EXTRACT_COURSES_JS:
            return self.courses
        if script == provider.EXTRACT_COURSE_CONTEXT_JS:
            return self.scenarios[self.current].get("page_course_id", self.current)
        assert script == provider.EXTRACT_ASSIGNMENT_ROWS_JS
        scenario = self.scenarios[self.current]
        rows = scenario.get("rows", [])
        return {"row_count": scenario.get("row_count", len(rows)), "rows": rows}

    def expect_response(self, predicate: Any, *, timeout: int) -> Any:
        page = self

        class Expectation:
            async def __aenter__(self) -> Any:
                page.response_armed_ms = page.elapsed_ms
                page.response_future = asyncio.get_running_loop().create_future()
                page.response_predicate = predicate
                return SimpleNamespace(value=page.response_future)

            async def __aexit__(self, *_args: Any) -> None:
                if page.elapsed_ms - page.response_armed_ms > timeout:
                    raise TimeoutError("response arrived after expectation expired")
                return None

        return Expectation()


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.handler: Any = None
        self.sent: list[str] = []
        self.aborted: list[str] = []

    async def route(self, pattern: str, handler: Any) -> None:
        self.handler = handler

    async def unroute(self, pattern: str, handler: Any) -> None:
        assert self.handler == handler
        self.handler = None

    async def request(
        self, path: str, method: str = "GET", headers: dict[str, str] | None = None, resource_type: str | None = None
    ) -> Any:
        request_headers = headers
        if request_headers is None:
            request_headers = {"Referer": ORIGIN + "/std/task"} if path == "/api/v1/task/stdList" else {}
        request = SimpleNamespace(
            url=ORIGIN + path,
            method=method,
            resource_type=resource_type or ("document" if method == "GET" else "xhr"),
            redirected_from=None,
            all_headers=lambda: asyncio.sleep(0, result=request_headers),
            frame=self.page.main_frame,
            is_navigation_request=lambda: (resource_type or ("document" if method == "GET" else "xhr")) == "document",
        )
        self.page.fire("request", request)

        async def continue_() -> None:
            self.sent.append(path)

        async def abort() -> None:
            self.aborted.append(path)

        route = SimpleNamespace(request=request, continue_=continue_, abort=abort)
        await self.handler(route)
        self.page.fire("requestfinished", request)
        return request


async def setup(
    monkeypatch: pytest.MonkeyPatch, root: Path, scenarios: dict[str, Any], courses: list[dict[str, Any]] = COURSES
) -> tuple[FakePage, FakeContext]:
    page = FakePage(courses, scenarios)
    context = FakeContext(page)
    page.context = context
    page.logins = 0

    @asynccontextmanager
    async def open_fake(*_args: Any, **kwargs: Any):
        assert kwargs["operation"] == "assignments.sync"
        assert kwargs["data_dir"] == root
        assert kwargs["headless"] in (False, True)
        page.open_headless = kwargs["headless"]
        yield SimpleNamespace(page=page, context=context)

    async def login(*_args: Any, **_kwargs: Any) -> None:
        page.logins += 1

    async def prepare(_page: Any, _config: Any, course_id: str, *, section: str) -> None:
        assert section == "task" and _page is page
        page.elapsed_ms += 8500  # Guarded row navigation precedes the section's response window.
        page.current = course_id
        scenario = scenarios[course_id]
        if scenario.get("stale_response"):
            scenario["_stale_request"] = await context.request("/api/v1/task/stdList", "POST")

    async def enter(_page: Any, section: str) -> None:
        assert section == "task" and _page is page
        scenario = scenarios[page.current]
        page.elapsed_ms += 2900  # Section document commit and list XHR complete within 15 seconds.
        await context.request("/std/task")
        if scenario.get("outgoing_before_commit"):
            outgoing_request = await context.request(
                "/api/v1/task/stdList", "POST", headers={"Referer": ORIGIN + "/std/myLecture"}
            )
        page.main_frame.url = ORIGIN + "/std/task"
        page.fire("framenavigated", page.main_frame)
        request = (
            await context.request(
                "/api/v1/task/stdList",
                "POST",
                headers={"Referer": ORIGIN + "/std/myLecture"} if scenario.get("wrong_referer") else None,
            )
            if not scenario.get("missing_own")
            else None
        )
        if scenario.get("unexpected"):
            await context.request(scenario["unexpected"], headers=scenario.get("headers"))
        for path, method, kind in scenario.get("side_requests", []):
            await context.request(path, method, resource_type=kind)
        if scenario.get("pending"):
            return
        if scenario.get("stale_response"):
            stale = SimpleNamespace(request=scenario["_stale_request"], status=200)
            assert not page.response_predicate(stale)
        if scenario.get("outgoing_before_commit"):
            assert not page.response_predicate(SimpleNamespace(request=outgoing_request, status=200))
        if request is not None:

            async def json() -> Any:
                if scenario.get("body_unavailable"):
                    raise ValueError("Synthetic body cannot be decoded")
                return scenario.get("body", {"data": {"list": scenario.get("rows", [])}})

            response = SimpleNamespace(
                request=request,
                status=503 if scenario.get("failed") else 200,
                finished=lambda: asyncio.sleep(0, result="connection ended" if scenario.get("interrupted") else None),
                json=json,
            )
            assert page.response_predicate(response)
            page.response_future.set_result(response)
        if scenario.get("busy"):
            page.fire("request", object())

    monkeypatch.setattr(provider, "open_session", open_fake)
    monkeypatch.setattr(provider, "ensure_logged_in", login)
    monkeypatch.setattr(provider, "prepare_course_section", prepare)
    monkeypatch.setattr(provider, "open_course_section", enter)
    return page, context


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


def run(
    root: Path, *, course_id: str | None = None, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    return asyncio.run(provider.sync_assignments({}, root, course_id, headless=headless, reviewed_policy=POLICY))


@pytest.mark.parametrize("headless", [False, True])
def test_modes_keep_records_failures_and_filtered_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headless: bool
) -> None:
    old_catalog(tmp_path)
    scenarios = {"course-a": {"rows": fixture_rows()}, "course-b": {"rows": [], "failed": True}}
    page, _ = asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    result, errors = run(tmp_path, headless=headless)
    assert page.open_headless is headless
    assert result["courses"] == 1 and result["assignments"] == 5
    assert [error.code for error in errors] == ["course-sync-failed"]
    stored = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert [row["title"] for row in stored["assignments"] if row["course"]["id"] == "course-b"] == ["Old B"]
    assert len([row for row in stored["assignments"] if row["course"]["id"] == "course-a"]) == 5
    assert {entry["reason"] for entry in stored["failed_courses"]} == {"course-sync-failed", "removal-deferred"}
    scenarios["course-b"] = {"rows": []}
    page, _ = asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    result, errors = run(tmp_path, course_id="course-b", headless=headless)
    assert page.open_headless is headless
    assert result["courses"] == 1 and result["assignments"] == 0 and not errors
    stored = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert {row["course"]["id"] for row in stored["assignments"]} == {"course-a", "course-old"}


def test_full_partial_filtered_and_unknown_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_catalog(tmp_path)
    scenarios = {"course-a": {"rows": fixture_rows()}, "course-b": {"rows": [], "failed": True}}
    page, _ = asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    result, errors = run(tmp_path)
    assert result["courses"] == 1 and result["assignments"] == 5 and len(errors) == 1
    assert page.logins == 1
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert [row["title"] for row in catalog["assignments"] if row["course"]["id"] == "course-b"] == ["Old B"]
    assert {failure["reason"] for failure in catalog["failed_courses"]} == {"course-sync-failed", "removal-deferred"}
    scenarios["course-b"] = {"rows": [], "body": {"data": {"list": []}}}
    asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    result, errors = run(tmp_path, course_id="course-b")
    assert not errors and result["assignments"] == 0
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert {row["course"]["id"] for row in catalog["assignments"]} == {"course-a", "course-old"}
    assert catalog["failed_courses"][0]["reason"] == "removal-deferred"
    scenarios["roster_error"] = True
    asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    stamp = catalog["generated_at"]
    with pytest.raises(CampusError) as failure:
        run(tmp_path)
    assert failure.value.code == "course-discovery-failed"
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert catalog["generated_at"] == stamp and catalog["enrollment_state"] == "unknown"
    del scenarios["roster_error"]
    asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    result, _ = run(tmp_path)
    assert result["courses"] == 2
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert catalog["failed_courses"] == []
    assert {row["course"]["id"] for row in catalog["assignments"]} == {"course-a"}


@pytest.mark.parametrize(
    "state",
    ["pending", "failed", "busy", "missing_table", "nonzero", "malformed", "placeholder", "duplicate", "interrupted"],
)
def test_pending_empty_tbody_waits_for_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    old_catalog(tmp_path)
    scenario = {"rows": []}
    scenario[state] = True
    if state == "nonzero":
        scenario["body"] = {"data": {"list": [{"task_id": "TB_L_REPORT111"}]}}
    if state == "malformed":
        scenario["body"] = {}
    if state == "placeholder":
        scenario["row_count"] = 1
    if state == "duplicate":
        scenario["side_requests"] = [("/api/v1/task/stdList", "POST", "xhr")]
    asyncio.run(setup(monkeypatch, tmp_path, {"course-a": scenario}))
    monkeypatch.setattr(provider, "PROTOCOL_TIMEOUT_SECONDS", 0.02)
    result, errors = run(tmp_path, course_id="course-a")
    assert result["courses"] == 0 and errors
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert [row["title"] for row in catalog["assignments"] if row["course"]["id"] == "course-a"] == ["Old A"]


def test_unavailable_body_count_accepts_idle_empty_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_catalog(tmp_path)
    asyncio.run(setup(monkeypatch, tmp_path, {"course-a": {"rows": [], "body_unavailable": True}}))
    result, errors = run(tmp_path, course_id="course-a")
    assert not errors and result["courses"] == 1 and result["assignments"] == 0
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert all(row["course"]["id"] != "course-a" for row in catalog["assignments"])


@pytest.mark.parametrize(
    "scenario",
    [
        {"body": {"data": {"courseId": "course-b", "list": []}}},
        {"body": {"data": {"rows": [], "context": {"course_id": "course-b"}}}},
        {"page_course_id": "course-b"},
    ],
)
def test_wrong_course_context_keeps_cached_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: dict[str, Any]
) -> None:
    old_catalog(tmp_path)
    asyncio.run(setup(monkeypatch, tmp_path, {"course-a": {"rows": [], **scenario}}))
    result, errors = run(tmp_path, course_id="course-a")
    assert result["courses"] == 0 and errors[0].code == "course-sync-failed"
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert [row["title"] for row in catalog["assignments"] if row["course"]["id"] == "course-a"] == ["Old A"]


@pytest.mark.parametrize(
    "scenario",
    [
        {"stale_response": True},
        {"missing_own": True},
        {"stale_response": True, "missing_own": True},
        {"outgoing_before_commit": True},
        {"outgoing_before_commit": True, "missing_own": True},
        {"wrong_referer": True},
    ],
)
def test_stale_or_missing_course_response_never_clears_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: dict[str, bool]
) -> None:
    old_catalog(tmp_path)
    asyncio.run(setup(monkeypatch, tmp_path, {"course-a": {"rows": [], **scenario}}))
    monkeypatch.setattr(provider, "PROTOCOL_TIMEOUT_SECONDS", 0.02)
    result, errors = run(tmp_path, course_id="course-a")
    assert result["courses"] == 0 and errors[0].code == "course-sync-failed"
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert [row["title"] for row in catalog["assignments"] if row["course"]["id"] == "course-a"] == ["Old A"]


@pytest.mark.parametrize("headless", [False, True])
def test_range_header_rejected_on_allowed_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headless: bool) -> None:
    old_catalog(tmp_path)
    scenarios = {"course-a": {"rows": [], "unexpected": "/std/task", "headers": {"rAnGe": "bytes=0-1023"}}}
    asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    with pytest.raises(CampusError) as failure:
        run(tmp_path, course_id="course-a", headless=headless)
    assert failure.value.code == "policy-blocked"
    assert (
        read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))["generated_at"]
        == "2026-01-01T00:00:00Z"
    )


@pytest.mark.parametrize("headless", [False, True])
def test_suppressed_panopto_and_unpinned_request_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headless: bool
) -> None:
    old_catalog(tmp_path)
    scenarios = {"course-a": {"rows": [], "unexpected": "/api/v1/task/unknown"}}
    asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    with pytest.raises(CampusError) as failure:
        run(tmp_path, course_id="course-a", headless=headless)
    assert failure.value.code == "policy-blocked"
    assert (
        read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))["generated_at"]
        == "2026-01-01T00:00:00Z"
    )


def test_unaddressable_course_retains_all_prior_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_catalog(tmp_path)
    scenarios = {"course-a": {"rows": [*fixture_rows(), {"task_id": None, "title": "Missing identity"}]}}
    asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    result, errors = run(tmp_path, course_id="course-a")
    assert result["courses"] == 0 and errors[0].code == "item-identity-missing"
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    assert [row["title"] for row in catalog["assignments"] if row["course"]["id"] == "course-a"] == ["Old A"]


def test_named_panopto_requests_abort_without_failing_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_catalog(tmp_path)
    requests = [
        ("/js/common/panopto-abc123.js", "GET", "script"),
        ("/api/v1/panopto/addInternetDisconnectionLog", "POST", "xhr"),
        ("/api/v1/panopto/checkInternetConnection", "GET", "xhr"),
    ]
    scenarios = {"course-a": {"rows": [], "side_requests": requests}}
    _, context = asyncio.run(setup(monkeypatch, tmp_path, scenarios))
    reviewed = {
        **POLICY,
        "suppress": [
            {
                "name": name,
                "origin": ORIGIN,
                "path_template": path,
                "operation": "assignments.sync",
                "methods": [method],
                "reason": reason,
            }
            for (name, reason), (path, method, _) in zip(
                [
                    ("panopto-script", "media-integration"),
                    ("panopto-disconnection-log", "logging"),
                    ("panopto-connectivity-check", "logging"),
                ],
                [(requests[0][0].replace("abc123", "{hash}"), "GET", "script"), *requests[1:]],
                strict=True,
            )
        ],
    }
    result, errors = asyncio.run(provider.sync_assignments({}, tmp_path, "course-a", reviewed_policy=reviewed))
    assert not errors and result["courses"] == 1 and result["assignments"] == 0
    assert result["suppressed_count"] == 3
    assert result["suppressed_reasons"] == {"media-integration": 1, "logging": 2}
    assert context.aborted == [entry[0] for entry in requests]
    assert all(path not in context.sent for path in context.aborted)
