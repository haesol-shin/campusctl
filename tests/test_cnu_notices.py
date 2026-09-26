from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.commands.notices import CAPABILITY
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError
from campusctl.providers.cnu import notices
from campusctl.providers.cnu.ui_policy import (
    UiRequestDenied,
    UiRequestDiagnostics,
    UiRequestPolicy,
    guard_ui_request,
    install_ui_request_interceptor,
)

L = "https://lms.example.invalid"
COURSES = [
    {"course_id": "course-a", "label": "Example Course", "class_no": None},
    {"course_id": "course-b", "label": "Other Course", "class_no": None},
]


def native_board_id(suffix: int) -> str:
    return f"TB_L_BOARDITEM{suffix}"


def policy() -> dict[str, Any]:
    configured = deepcopy(CAPABILITY["policy"])
    configured["origins"] = [L, "https://dcs-learning.cnu.ac.kr"]
    configured["static_asset_origins"] = [L]
    for collection in ("routes", "suppress"):
        for entry in configured[collection]:
            if entry.get("logging_token_reviewed"):
                continue
            if entry["origin"] == "https://dcs-learning.cnu.ac.kr":
                entry["origin"] = L
    return configured


def row(number: str, native: str, *, title: str = "Board notice", date: str = "2026-09-01") -> dict[str, Any]:
    return {
        "number": number,
        "native_id": native,
        "title": title,
        "date": date,
        "attachment_text": "",
        "attachment_marked": False,
        "author": "Example Author",
        "view_count": "42",
    }


def response_item(board_row: dict[str, Any], course_id: str) -> dict[str, Any]:
    number = int(board_row["number"])
    date = board_row["date"]
    return {
        "row_idx": number,
        "rseq": number,
        "real_rseq": number,
        "real_seq": number,
        "boarditem_orders": number,
        "boarditem_viewcnt": int(board_row["view_count"]) if board_row["view_count"] else None,
        "boarditem_depth": 0,
        "recommand_cnt": 0,
        "cmt_cnt": 0,
        "a_cnt": 0,
        "read_cnt": 0,
        "file_yn": int(board_row["attachment_marked"]),
        "a_cnt2": 0,
        "boarditem_no": board_row["native_id"],
        "board_no": "BOARD_SYNTHETIC",
        "writeruserno": "USER_SYNTHETIC",
        "ref_user_no": "USER_SYNTHETIC",
        "boarditem_title": board_row["title"],
        "course_id": course_id,
        "class_no": "CLASS_SYNTHETIC",
        "boarditem_ref": "BOARD_REF_SYNTHETIC",
        "board_nm": "Notice",
        "insert_dt": date[:10],
        "insert_dt_addtime": date if len(date) > 10 else date + " 08:00",
        "writeruser_name": board_row["author"],
        "writeruser_phone": "000-0000",
        "course_nm": "Example Course",
        "new_yn": "N",
        "delete_yn": "N",
        "complete_delete_yn": "N",
        "temp_save_yn": "N",
        "week_no": None,
        "seq_no": None,
        "study_time": None,
        "study_open_yn": None,
        "upper_boarditem_no": None,
        "category_id": None,
        "board_gubun_nm": None,
        "category_nm": None,
        "open_yn": None,
        "boarditem_share_yn": None,
        "attach_file_list": [],
    }


class FakeRoute:
    def __init__(self, request: Any, page: FakePage) -> None:
        self.request = request
        self.page = page
        self.action = ""

    async def abort(self) -> None:
        self.action = "abort"

    async def continue_(self) -> None:
        self.action = "continue"

    async def fetch(self, *, max_redirects: int) -> Any:
        assert max_redirects == 0
        assert self.request.url.endswith("/api/v1/course/addSessionCourseInfo")

        async def json() -> dict[str, Any]:
            return self.page.session_response()

        return SimpleNamespace(status=self.page.session_status, json=json)

    async def fulfill(self, *, response: Any) -> None:
        assert response.status == self.page.session_status
        self.action = "continue"


class FakePage:
    def __init__(self) -> None:
        self.roster = COURSES
        self.boards: dict[str, list[dict[str, Any]]] = {
            "course-a": [row("1", native_board_id(100))],
            "course-b": [row("2", native_board_id(200))],
        }
        self.todo: list[dict[str, Any]] = []
        self.paginated: set[str] = set()
        self.response_total: dict[str, int] = {}
        self.response_overrides: dict[tuple[str, str], dict[str, Any]] = {}
        self.current = "course-a"
        self.main_frame = SimpleNamespace(url=L + "/std/myLecture")
        self.listeners: dict[str, Any] = {}
        self.handler: Any = None
        self.request_log: list[str] = []
        self.duplicate = False
        self.render_duplicate_top = False
        self.rendered_ids: dict[str, list[str]] = {}
        self.stale = False
        self.bad_referer = False
        self.wrong_context = False
        self.context_id: str | None = None
        self.context_name: str | None = None
        self.context_name_only = False
        self.discovery_error = False
        self.todo_pending = False
        self.missing_list = False
        self.session_course_id: str | None = None
        self.session_result = "Y"
        self.session_body_read_before_navigation = False
        self.session_code = 200
        self.session_status = 200

    def session_response(self) -> dict[str, Any]:
        if self.main_frame.url.endswith(("/std/lecture", "/std/notice")):
            raise ValueError("No resource with given identifier")
        self.session_body_read_before_navigation = True
        return {
            "header": {"msg": "OK", "code": self.session_code},
            "body": {
                "result": self.session_result,
                "data": {
                    "course_id": self.session_course_id or self.current,
                    "course_nm": next(c["label"] for c in self.roster if c["course_id"] == self.current),
                    "term_cd": "1",
                    "term_year": "2026",
                    "subject_cd": "synthetic",
                    "class_no": "01",
                },
            },
        }

    def on(self, event: str, callback: Any) -> None:
        self.listeners[event] = callback

    def remove_listener(self, event: str, callback: Any) -> None:
        self.listeners.pop(event, None)

    async def route(self, pattern: str, handler: Any) -> None:
        self.handler = handler

    async def unroute(self, pattern: str, handler: Any) -> None:
        self.handler = None

    async def request(self, path: str, method: str = "GET", *, referer: str | None = None, body: Any = None) -> None:
        url = L + path
        request = SimpleNamespace(
            url=url,
            method=method,
            resource_type="document" if path.startswith("/std/") else "xhr",
            frame=self.main_frame,
            redirected_from=None,
            post_data_json=body,
        )

        async def headers() -> dict[str, str]:
            return {"referer": referer or L + self.main_frame.url.split(L)[-1]}

        request.all_headers = headers
        self.listeners.get("request", lambda _: None)(request)
        route = FakeRoute(request, self)
        await self.handler(route)
        self.request_log.append(path)
        if route.action != "continue":
            raise RuntimeError("request denied")
        if method == "POST":

            async def finished() -> None:
                return None

            async def json() -> dict[str, Any]:
                if path == "/api/v1/course/addSessionCourseInfo":
                    return self.session_response()
                if path not in {"/api/v1/board/notice/list/top", "/api/v1/board/notice/list"}:
                    return {"items": []}
                if (self.current, path) in self.response_overrides:
                    return self.response_overrides[self.current, path]
                items = [response_item(item, self.current) for item in self.boards[self.current]]
                if path.endswith("/top"):
                    return {"header": {"msg": "OK", "code": 200}, "body": {"list": []}}
                return {
                    "header": {"msg": "OK", "code": 200},
                    "body": {"total": self.response_total.get(self.current, len(items)), "list": items},
                }

            self.listeners.get("response", lambda _: None)(
                SimpleNamespace(
                    request=request,
                    status=self.session_status if path.endswith("/addSessionCourseInfo") else 200,
                    finished=finished,
                    json=json,
                )
            )

    async def goto(self, url: str, **kwargs: Any) -> None:
        path = url.split(L)[-1]
        await self.request(path)
        self.main_frame.url = url
        self.listeners.get("framenavigated", lambda _: None)(self.main_frame)
        if path == "/std/todo":
            await self.request("/api/v1/board/std/notice/list", "POST")

    async def click(self, selector: str) -> None:
        if "moveLecture" in selector:
            self.current = next(course["course_id"] for course in self.roster if course["course_id"] in selector)
            await self.request(
                "/api/v1/course/addSessionCourseInfo",
                "POST",
                referer=L + "/std/myLecture",
                body={"e": "opaque-encrypted-selection"},
            )
            await self.goto(L + "/std/lecture")
        elif selector == 'a[href="/std/notice"]':
            if self.stale:
                await self.request("/api/v1/board/notice/list", "POST", referer=L + "/std/lecture")
            await self.goto(L + "/std/notice")
            await self.request("/api/v1/board/notice/list/top", "POST", referer=L + "/std/notice")
            if not self.missing_list:
                await self.request(
                    "/api/v1/board/notice/list",
                    "POST",
                    referer=L + ("/std/lecture" if self.bad_referer else "/std/notice"),
                )
                if self.duplicate:
                    await self.request("/api/v1/board/notice/list", "POST", referer=L + "/std/notice")
        else:
            raise AssertionError(f"unexpected click {selector}")

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        if self.todo_pending and self.main_frame.url.endswith("/std/todo"):
            raise TimeoutError("To-do grid did not settle")

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        if self.discovery_error:
            raise RuntimeError("Roster unavailable")

    async def wait_for_function(self, expression: str) -> None:
        pass

    async def evaluate(self, expression: str) -> Any:
        if expression == notices.EXTRACT_COURSES_JS:
            return self.roster
        if expression == notices._EXTRACT_GRID_JS:
            return {"rendered": True, "empty": not self.todo, "rows": self.todo, "next": None}
        if expression == notices._EXTRACT_BOARD_JS:
            row_ids = self.rendered_ids.get(self.current) or [item["native_id"] for item in self.boards[self.current]]
            if self.render_duplicate_top and row_ids:
                row_ids.insert(0, row_ids[0])
            return {
                "row_count": len(row_ids),
                "row_ids": row_ids,
                "page_size": 10,
                "pages": [1, 2] if self.current in self.paginated else [1],
                "next_enabled": self.current in self.paginated,
            }
        return {
            "id": None
            if self.context_name_only
            else self.context_id or ("wrong" if self.wrong_context else self.current),
            "name": self.context_name
            or (
                "wrong"
                if self.wrong_context
                else next(course["label"] for course in self.roster if course["course_id"] == self.current)
            ),
        }


def run_sync(
    monkeypatch: pytest.MonkeyPatch,
    page: FakePage,
    root: Path,
    course_id: str | None = None,
    *,
    headless: bool = False,
) -> tuple[Any, Any]:
    @asynccontextmanager
    async def session(config: Any, *, data_dir: Any, headless: bool, operation: str):
        assert data_dir == root and operation == "notices.sync"
        page.open_headless = headless
        yield SimpleNamespace(page=page)

    async def login(page: Any, config: Any) -> None:
        pass

    monkeypatch.setattr(notices, "open_session", session)
    monkeypatch.setattr(notices, "ensure_logged_in", login)
    monkeypatch.setattr(notices, "_ORIGIN", L)
    monkeypatch.setattr(notices, "_TODO_URL", L + "/std/todo")
    monkeypatch.setattr(notices, "MY_LECTURE_URL", L + "/std/myLecture")
    return asyncio.run(notices.sync_notices({}, root, course_id, headless=headless, reviewed_policy=policy()))


def catalog(root: Path) -> dict[str, Any]:
    return read_domain_catalog("notices", domain_catalog_path("notices", root))


def test_legacy_parser_golden_fixture() -> None:
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/lms_sources/notices_legacy.json").read_text(encoding="utf-8")
    )
    for case in fixture["cases"]:
        parsed, failures = notices.parse_legacy_notice_text(case["text"], fixture["courses"])
        assert not failures
        (item,) = parsed[case["course_id"]]
        assert (item["legacy_key"], item["entity_id"], item["title"], item["is_unread"]) == (
            case["legacy_key"],
            case["entity_id"],
            case["title"],
            case["is_unread"],
        )


@pytest.mark.parametrize("headless", [False, True])
def test_modes_keep_filtered_records_and_stale_board_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headless: bool
) -> None:
    page = FakePage()
    result, errors = run_sync(monkeypatch, page, tmp_path, headless=headless)
    assert page.open_headless is headless
    assert result["courses"] == 2 and not errors
    old_b = [row for row in catalog(tmp_path)["notices"] if row["course"]["id"] == "course-b"]
    assert len(old_b) == 1
    page.paginated.add("course-b")
    page.boards["course-b"] = [row("3", native_board_id(300))]
    result, errors = run_sync(monkeypatch, page, tmp_path, headless=headless)
    assert result["courses"] == 1 and [error.code for error in errors] == ["notice-board-paginated"]
    stored = catalog(tmp_path)
    assert [row for row in stored["notices"] if row["course"]["id"] == "course-b"] == old_b
    assert stored["failed_courses"][0]["reason"] == "notice-board-paginated"
    page.paginated.clear()
    result, errors = run_sync(monkeypatch, page, tmp_path, "course-a", headless=headless)
    assert result["courses"] == 1 and result["notices"] == 1 and not errors
    assert [row for row in catalog(tmp_path)["notices"] if row["course"]["id"] == "course-b"] == old_b


def test_two_course_board_rows_without_todo_and_no_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert not errors and result["notices"] == 2
    assert page.session_body_read_before_navigation
    items = catalog(tmp_path)["notices"]
    assert {item["course"]["id"] for item in items} == {"course-a", "course-b"}
    assert all(
        item["is_unread"] is None and item["author"] == "Example Author" and item["view_count"] == 42 for item in items
    )
    assert all(item["has_attachments"] is False for item in items)
    assert all(
        not any(field in item for field in ("writeruser_phone", "writeruserno", "ref_user_no")) for item in items
    )
    assert not any("noticeDetail" in path or "/notice/info" in path for path in page.request_log)


def test_board_attachment_and_nullable_views(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.boards["course-b"] = [{**row("2", native_board_id(200)), "attachment_marked": True, "view_count": ""}]
    run_sync(monkeypatch, page, tmp_path)
    second = next(item for item in catalog(tmp_path)["notices"] if item["course"]["id"] == "course-b")
    assert second["has_attachments"] is True and second["view_count"] is None


def test_verified_empty_requires_course_board_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    page.boards["course-a"] = []
    run_sync(monkeypatch, page, tmp_path)
    assert [item["course"]["id"] for item in catalog(tmp_path)["notices"]] == ["course-b"]
    page.boards["course-a"] = [row("9", native_board_id(900))]
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.boards["course-a"] = []
    page.missing_list = True
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert errors and result["courses"] == 0
    assert catalog(tmp_path)["notices"] == old


def test_paginated_board_preserves_stale_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    before = [item for item in catalog(tmp_path)["notices"] if item["course"]["id"] == "course-b"]
    page.paginated.add("course-b")
    page.boards["course-b"] = [row("3", native_board_id(300))]
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["notices"] == 1 and [error.code for error in errors] == ["notice-board-paginated"]
    assert catalog(tmp_path)["failed_courses"][0]["reason"] == "notice-board-paginated"
    assert [item for item in catalog(tmp_path)["notices"] if item["course"]["id"] == "course-b"] == before


def test_response_total_above_page_size_preserves_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.response_total["course-b"] = 11
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["courses"] == 1 and [error.code for error in errors] == ["notice-board-paginated"]
    assert [item for item in catalog(tmp_path)["notices"] if item["course"]["id"] == "course-b"] == [
        item for item in old if item["course"]["id"] == "course-b"
    ]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("response-has-item", "course-sync-failed"),
        ("response-shape-unknown", "course-sync-failed"),
        ("top-has-item", "course-sync-failed"),
        ("error-header", "course-sync-failed"),
        ("list-shorter-than-total", "notice-board-paginated"),
        ("wrong-course", "course-sync-failed"),
        ("deleted-item", "course-sync-failed"),
    ],
)
def test_board_response_must_confirm_rendered_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, expected: str
) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.boards["course-a"] = []
    path = "/api/v1/board/notice/list"
    item = response_item(row("1", native_board_id(100)), "course-a")
    if case == "top-has-item":
        page.response_overrides["course-a", path + "/top"] = {
            "header": {"msg": "OK", "code": 200},
            "body": {"list": [item]},
        }
    elif case == "response-shape-unknown":
        page.response_overrides["course-a", path] = {"header": {"msg": "OK", "code": 200}, "body": {}}
    else:
        code = 500 if case == "error-header" else 200
        if case == "wrong-course":
            item["course_id"] = "course-b"
        if case == "deleted-item":
            item["delete_yn"] = "Y"
        page.response_overrides["course-a", path] = {
            "header": {"msg": "OK", "code": code},
            "body": {"total": 2 if case == "list-shorter-than-total" else 1, "list": [item]},
        }
    result, errors = run_sync(monkeypatch, page, tmp_path, course_id="course-a")
    assert result["courses"] == 0 and [error.code for error in errors] == [expected]
    assert catalog(tmp_path)["notices"] == old


def test_board_top_items_join_rendered_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    item = response_item(page.boards["course-a"][0], "course-a")
    page.response_overrides["course-a", "/api/v1/board/notice/list/top"] = {
        "header": {"msg": "OK", "code": 200},
        "body": {"list": [item]},
    }
    page.response_overrides["course-a", "/api/v1/board/notice/list"] = {
        "header": {"msg": "OK", "code": 200},
        "body": {"total": 0, "list": []},
    }
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert not errors and result["notices"] == 2
    assert any(item["course"]["id"] == "course-a" for item in catalog(tmp_path)["notices"])


@pytest.mark.parametrize("same_day", [True, False])
def test_duplicate_board_date_precision_preserves_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_day: bool
) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    previous = catalog(tmp_path)["notices"]
    page.render_duplicate_top = True
    top = response_item(page.boards["course-a"][0], "course-a")
    top["insert_dt_addtime"] = None
    if not same_day:
        top["insert_dt"] = "2026-09-02"
    page.response_overrides["course-a", "/api/v1/board/notice/list/top"] = {
        "header": {"msg": "OK", "code": 200},
        "body": {"list": [top]},
    }
    result, errors = run_sync(monkeypatch, page, tmp_path, "course-a")
    if same_day:
        assert not errors and result["courses"] == 1
        assert next(row for row in catalog(tmp_path)["notices"] if row["course"]["id"] == "course-a")[
            "legacy_key"
        ].endswith("2026-09-01 08:00_1")
    else:
        assert result["courses"] == 0 and len(errors) == 1
        assert catalog(tmp_path)["notices"] == previous


def test_rendered_duplicate_must_match_response_multiplicity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    first = row("1", native_board_id(100))
    second = row("2", native_board_id(101))
    page.boards["course-a"] = [first, second]
    run_sync(monkeypatch, page, tmp_path)
    previous = catalog(tmp_path)["notices"]
    page.response_overrides["course-a", "/api/v1/board/notice/list/top"] = {
        "header": {"msg": "OK", "code": 200},
        "body": {"list": [response_item(first, "course-a")]},
    }
    page.rendered_ids["course-a"] = [native_board_id(100), native_board_id(101), native_board_id(101)]
    result, errors = run_sync(monkeypatch, page, tmp_path, "course-a")
    assert result["courses"] == 0 and len(errors) == 1
    assert catalog(tmp_path)["notices"] == previous
    page.rendered_ids["course-a"] = [native_board_id(100), native_board_id(100), native_board_id(101)]
    result, errors = run_sync(monkeypatch, page, tmp_path, "course-a")
    assert not errors and result["courses"] == 1


def test_nonempty_dom_with_zero_response_keeps_stale_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.response_overrides["course-a", "/api/v1/board/notice/list"] = {
        "header": {"msg": "OK", "code": 200},
        "body": {"total": 0, "list": []},
    }
    result, errors = run_sync(monkeypatch, page, tmp_path, course_id="course-a")
    assert result["courses"] == 0 and len(errors) == 1
    assert catalog(tmp_path)["notices"] == old


def test_extract_board_js_ignores_empty_placeholder_row() -> None:
    assert "td[colspan]" in notices._EXTRACT_BOARD_JS


def test_todo_unread_merges_only_matching_native_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.todo = [
        {
            "number": "7",
            "course_label": "Example Course",
            "title": "Board notice",
            "date": "2026-09-01 12:00",
            "read_yn": "읽지않음",
            "native_id": native_board_id(100),
        }
    ]
    run_sync(monkeypatch, page, tmp_path)
    first, second = catalog(tmp_path)["notices"]
    assert first["is_unread"] is True and first["legacy_key"] == "Example Course_2026-09-01 12:00_7"
    assert second["is_unread"] is None and second["legacy_key"] == "Other Course_2026-09-01 08:00_2"


@pytest.mark.parametrize("failure", ["duplicate", "stale", "bad_referer", "wrong_context", "missing_list"])
def test_bad_response_binding_keeps_old_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    setattr(page, failure, True)
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert errors and result["courses"] < 2
    assert catalog(tmp_path)["notices"] == old


@pytest.mark.parametrize(
    ("field", "value"),
    [("session_course_id", "course-b"), ("session_result", "N"), ("session_code", 500), ("session_status", 503)],
)
def test_encrypted_selection_requires_matching_success_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    previous = catalog(tmp_path)["notices"]
    setattr(page, field, value)
    result, errors = run_sync(monkeypatch, page, tmp_path, "course-a")
    assert result["courses"] == 0 and len(errors) == 1
    assert catalog(tmp_path)["notices"] == previous


def test_todo_without_native_id_keeps_historical_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.todo = [
        {
            "number": "17",
            "course_label": "Example Course",
            "title": "Board notice",
            "date": "2026-09-01 12:30",
            "read_yn": "읽음",
        }
    ]
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["courses"] == 2 and not errors
    first = next(item for item in catalog(tmp_path)["notices"] if item["course"]["id"] == "course-a")
    assert first["legacy_key"] == "Example Course_2026-09-01 12:30_17"
    assert first["entity_id"] == "cnu_notice:course-a:2026-09-01 12%3A30:17"
    assert first["is_unread"] is False


def test_ambiguous_todo_match_preserves_old_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.todo = [
        {
            "number": n,
            "course_label": "Example Course",
            "title": "Board notice",
            "date": f"2026-09-01 {hour}:00",
            "read_yn": "읽음",
        }
        for n, hour in (("7", "12"), ("8", "13"))
    ]
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["courses"] == 1 and [error.code for error in errors] == ["notice-identity-ambiguous"]
    assert catalog(tmp_path)["notices"] == old
    assert catalog(tmp_path)["failed_courses"][0]["reason"] == "notice-identity-ambiguous"


def test_duplicate_board_candidate_is_not_a_legacy_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.boards["course-a"] = [row("1", native_board_id(100)), row("2", native_board_id(101))]
    page.todo = [
        {
            "number": "7",
            "course_label": "Example Course",
            "title": "Board notice",
            "date": "2026-09-01 12:00",
            "read_yn": "읽음",
        }
    ]
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["courses"] == 1 and [error.code for error in errors] == ["notice-identity-ambiguous"]
    assert catalog(tmp_path)["notices"] == old


def test_unmatched_board_notice_uses_observed_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.todo = [
        {
            "number": "7",
            "course_label": "Example Course",
            "title": "Different notice",
            "date": "2026-09-01 12:30",
            "read_yn": "읽지않음",
        }
    ]
    run_sync(monkeypatch, page, tmp_path)
    first = next(item for item in catalog(tmp_path)["notices"] if item["course"]["id"] == "course-a")
    assert first["legacy_key"] == "Example Course_2026-09-01 08:00_1"
    assert first["is_unread"] is None


def test_conflicting_id_cannot_pass_by_matching_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.context_id = "course-b"
    page.context_name = "Example Course"
    result, errors = run_sync(monkeypatch, page, tmp_path, course_id="course-a")
    assert result["courses"] == 0 and len(errors) == 1
    assert catalog(tmp_path)["notices"] == old


def test_name_only_context_requires_unique_roster_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    page.context_name_only = True
    result, errors = run_sync(monkeypatch, page, tmp_path, course_id="course-a")
    assert result["courses"] == 1 and not errors
    page.roster = [COURSES[0], {**COURSES[1], "label": "Example Course"}]
    result, errors = run_sync(monkeypatch, page, tmp_path, course_id="course-a")
    assert result["courses"] == 0 and len(errors) == 1
    assert catalog(tmp_path)["failed_courses"][0]["course_id"] == "course-a"


def test_suppressed_side_requests_do_not_become_denials() -> None:
    async def exercise() -> None:
        page = FakePage()
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            page,
            UiRequestPolicy.from_reviewed_config(policy()),
            operation="notices.sync",
            diagnostics=diagnostics,
        )
        try:
            for url, method, resource in [
                (L + "/js/common/panoptoSaml-abcdef.js", "GET", "script"),
                ("http://0.0.0.0:3000/v1/events", "POST", "fetch"),
                ("https://third-party.invalid/css2", "GET", "stylesheet"),
                (L + "/assets/images/favicon-abcdef.ico", "GET", "other"),
            ]:
                request = SimpleNamespace(url=url, method=method, resource_type=resource, redirected_from=None)

                async def headers() -> dict[str, str]:
                    return {}

                request.all_headers = headers
                route = FakeRoute(request, page)
                await page.handler(route)
                assert route.action == "abort"
            interceptor.raise_if_denied()
            request = SimpleNamespace(
                url="https://dcs-learning.cnu.ac.kr/api/v1/week/getStdActivityStatus",
                method="POST",
                resource_type="xhr",
                redirected_from=None,
                all_headers=headers,
            )
            route = FakeRoute(request, page)
            await page.handler(route)
            assert route.action == "continue"
            assert diagnostics.suppressed_count == 4
        finally:
            await interceptor.close()

    asyncio.run(exercise())


def test_policy_rejects_unknown_data_redirect_and_range() -> None:
    reviewed = UiRequestPolicy.from_reviewed_config(policy())
    assert reviewed.approved
    for url, method, resource, headers in [
        (L + "/std/noticeDetail?no=" + native_board_id(1), "GET", "document", {}),
        (L + "/api/v1/board/notice/info", "POST", "xhr", {}),
        ("https://other.invalid/v1/events", "POST", "fetch", {}),
        (L + "/std/notice", "GET", "document", {"Range": "bytes=0-10"}),
    ]:
        with pytest.raises(UiRequestDenied):
            guard_ui_request(reviewed, url, method, headers, operation="notices.sync", resource_type=resource)
    with pytest.raises(UiRequestDenied):
        guard_ui_request(
            reviewed,
            L + "/std/notice",
            "GET",
            {},
            operation="notices.sync",
            resource_type="document",
            redirected_from=L + "/std/myLecture",
        )
    malformed = deepcopy(policy())
    malformed["routes"].append(
        {
            "origin": "https://unreviewed.invalid",
            "path": "/api/v1/board/notice/info",
            "operation": "notices.sync",
            "methods": ["POST"],
        }
    )
    assert not UiRequestPolicy.from_reviewed_config(malformed).approved


def test_discovery_failure_preserves_catalog_by_sync_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    path = domain_catalog_path("notices", tmp_path)
    original = path.read_bytes()
    page.discovery_error = True
    with pytest.raises(CampusError) as filtered:
        run_sync(monkeypatch, page, tmp_path, course_id="course-a")
    assert filtered.value.code == "course-discovery-failed"

    assert path.read_bytes() == original
    with pytest.raises(CampusError) as full:
        run_sync(monkeypatch, page, tmp_path)
    assert full.value.code == "course-discovery-failed"
    assert catalog(tmp_path)["enrollment_state"] == "unknown"
    assert catalog(tmp_path)["notices"] == json.loads(original)["notices"]
    assert catalog(tmp_path)["generated_at"] == json.loads(original)["generated_at"]


def test_unknown_selected_course_preserves_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    path = domain_catalog_path("notices", tmp_path)
    old = path.read_bytes()
    with pytest.raises(CampusError) as exc:
        run_sync(monkeypatch, page, tmp_path, course_id="missing")
    assert exc.value.code == "course-not-found"
    assert path.read_bytes() == old


def test_stale_global_grid_never_confirms_empty_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.todo_pending = True
    page.boards["course-a"] = []
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["courses"] == 0 and errors
    assert catalog(tmp_path)["notices"] == old


def test_malformed_todo_identity_fails_only_affected_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    old = catalog(tmp_path)["notices"]
    page.todo = [
        {
            "number": "",
            "course_label": "Example Course",
            "title": "Board notice",
            "date": "2026-09-01 12:00",
            "read_yn": "읽음",
        }
    ]
    page.boards["course-a"] = [row("3", native_board_id(300))]
    page.boards["course-b"] = [row("4", native_board_id(400))]
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["courses"] == 1 and len(errors) == 1
    current = catalog(tmp_path)
    assert current["failed_courses"][0]["course_id"] == "course-a"
    assert next(item for item in old if item["course"]["id"] == "course-a") in current["notices"]
    assert next(item for item in current["notices"] if item["course"]["id"] == "course-b")["legacy_key"].endswith("_4")


def test_filtered_and_full_merge_transitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    run_sync(monkeypatch, page, tmp_path)
    original_b = next(item for item in catalog(tmp_path)["notices"] if item["course"]["id"] == "course-b")
    page.paginated.add("course-b")
    page.boards["course-a"] = [row("9", native_board_id(900))]
    result, errors = run_sync(monkeypatch, page, tmp_path)
    assert result["courses"] == 1 and [error.code for error in errors] == ["notice-board-paginated"]
    assert original_b in catalog(tmp_path)["notices"]
    page.paginated.clear()
    page.boards["course-a"] = [row("10", native_board_id(910))]
    run_sync(monkeypatch, page, tmp_path, course_id="course-a")
    assert catalog(tmp_path)["failed_courses"][0]["course_id"] == "course-b"
    page.roster = [COURSES[0]]
    page.paginated.add("course-a")
    run_sync(monkeypatch, page, tmp_path)
    assert {entry["reason"] for entry in catalog(tmp_path)["failed_courses"]} == {
        "notice-board-paginated",
        "removal-deferred",
    }
    page.paginated.clear()
    run_sync(monkeypatch, page, tmp_path)
    assert catalog(tmp_path)["failed_courses"] == []
    assert {item["course"]["id"] for item in catalog(tmp_path)["notices"]} == {"course-a"}
