from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu import notices

L = "https://lms.example.invalid"
COURSES = [
    {"course_id": "course-a", "label": "Example Course", "class_no": None},
    {"course_id": "course-b", "label": "Other Course", "class_no": None},
]


def native_board_id(suffix: int) -> str:
    return f"TB_L_BOARDITEM{suffix}"


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

        return SimpleNamespace(status=200, json=json)

    async def fulfill(self, *, response: Any) -> None:
        assert response.status == 200
        self.action = "continue"


class FakePage:
    def __init__(self) -> None:
        self.roster = COURSES
        self.boards: dict[str, list[dict[str, Any]]] = {
            "course-a": [row("1", native_board_id(100))],
            "course-b": [row("2", native_board_id(200))],
        }
        self.todo: list[dict[str, Any]] = []
        self.response_overrides: dict[tuple[str, str], dict[str, Any]] = {}
        self.response_total: dict[str, int] = {}
        self.rendered_ids: list[str] | None = None
        self.paginated = False
        self.missing_list = False
        self.duplicate_list = False
        self.stale_list = False
        self.bad_referer = False
        self.context_id: str | None = "course-a"
        self.context_name = "Example Course"
        self.todo_pending = False
        self.current = "course-a"
        self.main_frame = SimpleNamespace(url=L + "/std/myLecture")
        self.listeners: dict[str, Any] = {}
        self.handler: Any = None

    def session_response(self) -> dict[str, Any]:
        if self.main_frame.url.endswith(("/std/lecture", "/std/notice")):
            raise ValueError("No resource with given identifier")
        return {
            "header": {"code": 200},
            "body": {"result": "Y", "data": {"course_id": self.current}},
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
                    status=200,
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
        assert selector == 'a[href="/std/notice"]'
        if self.stale_list:
            await self.request("/api/v1/board/notice/list", "POST", referer=L + "/std/lecture")
        await self.goto(L + "/std/notice")
        await self.request("/api/v1/board/notice/list/top", "POST", referer=L + "/std/notice")
        if not self.missing_list:
            await self.request(
                "/api/v1/board/notice/list",
                "POST",
                referer=L + ("/std/lecture" if self.bad_referer else "/std/notice"),
            )
            if self.duplicate_list:
                await self.request("/api/v1/board/notice/list", "POST", referer=L + "/std/notice")

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        if self.todo_pending and self.main_frame.url.endswith("/std/todo"):
            raise TimeoutError("To-do grid did not settle")

    async def wait_for_function(self, expression: str) -> None:
        pass

    async def evaluate(self, expression: str) -> Any:
        if expression == notices._EXTRACT_GRID_JS:
            return {"rendered": True, "empty": not self.todo, "rows": self.todo, "next": None}
        if expression == notices._EXTRACT_BOARD_JS:
            row_ids = (
                self.rendered_ids
                if self.rendered_ids is not None
                else [item["native_id"] for item in self.boards[self.current]]
            )
            return {
                "row_count": len(row_ids),
                "row_ids": row_ids,
                "page_size": 10,
                "pages": [1, 2] if self.paginated else [1],
                "next_enabled": self.paginated,
            }
        return {"id": self.context_id, "name": self.context_name}


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


def test_board_rows_preserve_native_todo_identity_and_nullable_metadata() -> None:
    native = native_board_id(100)
    todo, failures = notices.parse_notice_rows(
        [
            {
                "number": "7",
                "course_label": "Example Course",
                "title": "Board notice",
                "date": "2026-09-01 12:00",
                "read_yn": "읽지않음",
                "native_id": native,
            }
        ],
        COURSES,
    )
    assert not failures
    first = notices.parse_board_rows([row("1", native)], COURSES[0], todo["course-a"])
    assert first == [
        {
            "entity_id": "cnu_notice:course-a:2026-09-01 12%3A00:7",
            "native_id": native,
            "legacy_key": "Example Course_2026-09-01 12:00_7",
            "course": {"id": "course-a", "label": "Example Course"},
            "kind": "notice",
            "title": "Board notice",
            "date": "2026-09-01 12:00",
            "status": "읽지않음",
            "is_unread": True,
            "posted_date": None,
            "author_role": None,
            "author": "Example Author",
            "view_count": 42,
            "has_attachments": None,
        }
    ]
    second = notices.parse_board_rows(
        [{**row("2", native_board_id(200)), "attachment_marked": True, "view_count": ""}],
        COURSES[1],
        todo["course-b"],
    )
    assert second[0]["is_unread"] is None
    assert second[0]["view_count"] is None and second[0]["has_attachments"] is True


def test_unique_legacy_todo_join_and_ambiguous_identity() -> None:
    todo, failures = notices.parse_notice_rows(
        [
            {
                "number": "17",
                "course_label": "Example Course",
                "title": "Board notice",
                "date": "2026-09-01 12:30",
                "read_yn": "읽음",
            }
        ],
        COURSES,
    )
    assert not failures
    parsed = notices.parse_board_rows([row("1", native_board_id(100))], COURSES[0], todo["course-a"])
    assert parsed[0]["legacy_key"] == "Example Course_2026-09-01 12:30_17"
    assert parsed[0]["entity_id"] == "cnu_notice:course-a:2026-09-01 12%3A30:17"
    assert parsed[0]["is_unread"] is False
    with pytest.raises(CampusError) as error:
        notices.parse_board_rows(
            [row("1", native_board_id(100)), row("2", native_board_id(101))], COURSES[0], todo["course-a"]
        )
    assert error.value.code == "notice-identity-ambiguous"


def test_malformed_todo_identity_fails_only_affected_course() -> None:
    rows, failures = notices.parse_notice_rows(
        [
            {
                "number": "",
                "course_label": "Example Course",
                "title": "Board notice",
                "date": "2026-09-01 12:00",
                "read_yn": "읽음",
            },
            {
                "number": "4",
                "course_label": "Other Course",
                "title": "Board notice",
                "date": "2026-09-01 08:00",
                "read_yn": "읽지않음",
            },
        ],
        COURSES,
    )
    assert failures == {"course-a"}
    assert rows["course-b"][0]["legacy_key"] == "Other Course_2026-09-01 08:00_4"
    assert rows["course-b"][0]["is_unread"] is True


def collect_board(
    page: FakePage, monkeypatch: pytest.MonkeyPatch, *, courses: list[dict[str, Any]] = COURSES
) -> list[dict[str, Any]]:

    monkeypatch.setattr(notices, "_ORIGIN", L)
    page.handler = lambda route: route.continue_()

    async def exercise() -> list[dict[str, Any]]:
        capture = notices.arm_notice_capture(page)
        await page.click('a[href="/std/notice"]')
        return await notices.collect_notice_rows(page, COURSES[0], capture=capture, courses=courses, todo_rows=[])

    return asyncio.run(exercise())


def test_verified_empty_board_requires_complete_matching_list(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.boards["course-a"] = []
    assert collect_board(page, monkeypatch) == []
    page = FakePage()
    page.boards["course-a"] = []
    page.missing_list = True
    with pytest.raises(ValueError, match="missing or duplicated"):
        collect_board(page, monkeypatch)


@pytest.mark.parametrize("pagination", ["controls", "response-total", "short-list"])
def test_board_pagination_rejects_incomplete_course(monkeypatch: pytest.MonkeyPatch, pagination: str) -> None:
    page = FakePage()
    if pagination == "controls":
        page.paginated = True
    elif pagination == "response-total":
        page.response_total["course-a"] = 11
    else:
        page.response_total["course-a"] = 2
    with pytest.raises(CampusError) as error:
        collect_board(page, monkeypatch)
    assert error.value.code == "notice-board-paginated"


@pytest.mark.parametrize(
    "case",
    ["response-has-item", "top-has-item", "unknown-shape", "error-header", "wrong-course", "deleted-item"],
)
def test_rendered_empty_must_agree_with_board_responses(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    page = FakePage()
    page.boards["course-a"] = []
    item = response_item(row("1", native_board_id(100)), "course-a")
    listing = "/api/v1/board/notice/list"
    if case == "top-has-item":
        page.response_overrides["course-a", listing + "/top"] = {
            "header": {"code": 200},
            "body": {"list": [item]},
        }
    else:
        if case == "wrong-course":
            item["course_id"] = "course-b"
        if case == "deleted-item":
            item["delete_yn"] = "Y"
        page.response_overrides["course-a", listing] = (
            {"header": {"code": 200}, "body": {}}
            if case == "unknown-shape"
            else {
                "header": {"code": 500 if case == "error-header" else 200},
                "body": {"total": 1, "list": [item]},
            }
        )
    with pytest.raises(ValueError):
        collect_board(page, monkeypatch)


def test_nonempty_rendered_board_cannot_accept_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.response_overrides["course-a", "/api/v1/board/notice/list"] = {
        "header": {"code": 200},
        "body": {"total": 0, "list": []},
    }
    with pytest.raises(ValueError, match="rendered rows disagree"):
        collect_board(page, monkeypatch)


def test_top_board_row_and_same_day_date_precision_are_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    item = response_item(page.boards["course-a"][0], "course-a")
    short_date = {**item, "insert_dt_addtime": None}
    top_path = "/api/v1/board/notice/list/top"
    page.response_overrides["course-a", top_path] = {"header": {"code": 200}, "body": {"list": [short_date]}}
    page.rendered_ids = [native_board_id(100), native_board_id(100)]
    rows = collect_board(page, monkeypatch)
    assert len(rows) == 1 and rows[0]["date"] == "2026-09-01 08:00"
    page = FakePage()
    page.response_overrides["course-a", top_path] = {
        "header": {"code": 200},
        "body": {"list": [{**short_date, "insert_dt": "2026-09-02"}]},
    }
    page.rendered_ids = [native_board_id(100), native_board_id(100)]
    with pytest.raises(ValueError, match="conflicting duplicate"):
        collect_board(page, monkeypatch)


def test_top_only_board_item_and_rendered_multiplicity(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    item = response_item(page.boards["course-a"][0], "course-a")
    page.response_overrides["course-a", "/api/v1/board/notice/list/top"] = {
        "header": {"code": 200},
        "body": {"list": [item]},
    }
    page.response_overrides["course-a", "/api/v1/board/notice/list"] = {
        "header": {"code": 200},
        "body": {"total": 0, "list": []},
    }
    assert collect_board(page, monkeypatch)[0]["title"] == "Board notice"
    page.rendered_ids = [native_board_id(100), native_board_id(100)]
    with pytest.raises(ValueError, match="rendered rows disagree"):
        collect_board(page, monkeypatch)


@pytest.mark.parametrize("fault", ["stale_list", "missing_list", "duplicate_list", "bad_referer"])
def test_board_rejects_unbound_or_incomplete_responses(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    page = FakePage()
    setattr(page, fault, True)
    with pytest.raises(ValueError):
        collect_board(page, monkeypatch)


def test_board_rejects_conflicting_context_and_ambiguous_name(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()

    page.context_id = "course-b"
    with pytest.raises(ValueError, match="another course"):
        collect_board(page, monkeypatch)
    page = FakePage()
    page.context_id = None
    assert len(collect_board(page, monkeypatch)) == 1
    with pytest.raises(ValueError, match="not unique"):
        collect_board(page, monkeypatch, courses=[COURSES[0], {**COURSES[1], "label": "Example Course"}])


def test_unsettled_global_todo_cannot_confirm_board_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.handler = lambda route: route.continue_()
    monkeypatch.setattr(notices, "_ORIGIN", L)
    monkeypatch.setattr(notices, "_TODO_URL", L + "/std/todo")

    async def exercise() -> None:
        capture = await notices.open_notice_todo(page)
        page.todo_pending = True
        with pytest.raises(CampusError) as error:
            await notices.collect_notice_todo(page, COURSES, capture=capture)
        assert error.value.code == "browser-timeout"
        assert not page.listeners

    asyncio.run(exercise())


def test_multiple_legacy_todo_candidates_cannot_claim_one_board_notice() -> None:
    todo, failures = notices.parse_notice_rows(
        [
            {
                "number": number,
                "course_label": "Example Course",
                "title": "Board notice",
                "date": f"2026-09-01 {hour}:00",
                "read_yn": "읽음",
            }
            for number, hour in (("7", "12"), ("8", "13"))
        ],
        COURSES,
    )
    assert not failures
    with pytest.raises(CampusError) as error:
        notices.parse_board_rows([row("1", native_board_id(100))], COURSES[0], todo["course-a"])
    assert error.value.code == "notice-identity-ambiguous"


def test_unmatched_todo_keeps_board_timestamp() -> None:
    todo, failures = notices.parse_notice_rows(
        [
            {
                "number": "7",
                "course_label": "Example Course",
                "title": "Different notice",
                "date": "2026-09-01 12:30",
                "read_yn": "읽지않음",
            }
        ],
        COURSES,
    )
    assert not failures
    parsed = notices.parse_board_rows(
        [row("1", native_board_id(100), date="2026-09-01 08:00")], COURSES[0], todo["course-a"]
    )
    assert parsed[0]["legacy_key"] == "Example Course_2026-09-01 08:00_1"
    assert parsed[0]["is_unread"] is None


def test_collector_uses_prearmed_board_and_independent_expected_row(monkeypatch: pytest.MonkeyPatch) -> None:

    page = FakePage()
    monkeypatch.setattr(notices, "_ORIGIN", L)
    page.handler = lambda route: route.continue_()

    async def exercise() -> None:
        capture = notices.arm_notice_capture(page)
        await page.click('a[href="/std/notice"]')
        actual = await notices.collect_notice_rows(page, COURSES[0], capture=capture, courses=COURSES, todo_rows=[])
        assert actual == [
            {
                "entity_id": "cnu_notice:course-a:2026-09-01 08%3A00:1",
                "native_id": "TB_L_BOARDITEM100",
                "legacy_key": "Example Course_2026-09-01 08:00_1",
                "course": {"id": "course-a", "label": "Example Course"},
                "kind": "notice",
                "title": "Board notice",
                "date": "2026-09-01 08:00",
                "status": None,
                "is_unread": None,
                "posted_date": None,
                "author_role": None,
                "author": "Example Author",
                "view_count": 42,
                "has_attachments": False,
            }
        ]
        assert not page.listeners

    asyncio.run(exercise())


def test_collector_refuses_stale_course_switch_during_board(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    monkeypatch.setattr(notices, "_ORIGIN", L)
    page.handler = lambda route: route.continue_()

    async def exercise() -> None:
        capture = notices.arm_notice_capture(page)
        await page.click('a[href="/std/notice"]')
        await page.request("/api/v1/course/addSessionCourseInfo", "POST")
        with pytest.raises(ValueError, match="cleanly"):
            await notices.collect_notice_rows(
                page,
                COURSES[0],
                capture=capture,
                courses=COURSES,
                todo_rows=[],
            )
        capture = notices.arm_notice_capture(page)
        await page.click('a[href="/std/notice"]')
        evaluate = page.evaluate

        async def late_response(expression: str) -> Any:
            value = await evaluate(expression)
            if expression == notices._EXTRACT_BOARD_JS:
                await page.request("/api/v1/course/addSessionCourseInfo", "POST")
            return value

        page.evaluate = late_response
        with pytest.raises(ValueError, match="changed during extraction"):
            await notices.collect_notice_rows(
                page,
                COURSES[0],
                capture=capture,
                courses=COURSES,
                todo_rows=[],
            )

    asyncio.run(exercise())


def test_failed_notice_navigation_releases_capture() -> None:

    page = FakePage()
    page.main_frame.url = L + "/std/lecture"

    async def fail() -> None:
        raise RuntimeError("synthetic notice navigation failed")

    async def scenario() -> None:
        capture = notices.arm_notice_capture(page)
        with pytest.raises(RuntimeError, match="synthetic notice navigation failed"):
            await notices.open_notice_section(page, fail, capture=capture)
        assert not page.listeners

    asyncio.run(scenario())
