"""Synthetic archive metadata and catalog sync tests (no LMS session)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu import materials, readiness

_FIXTURE = Path(__file__).parent / "fixtures/lms_sources/materials_archive.json"


@pytest.fixture(autouse=True)
def short_render_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materials, "_WAIT_MS", 50)


class FakeRequest:
    def __init__(self, page, path: str, method: str, document: bool = False) -> None:
        self.frame = page.main_frame
        self.url = "https://dcs-learning.cnu.ac.kr" + path
        self.method = method
        self.resource_type = "document" if document else "xhr"
        self.document = document
        self.headers = {"referer": page.main_frame.url}

    def is_navigation_request(self):
        return self.document


class FakeResponse:
    def __init__(self, case: dict, request: FakeRequest, body=None) -> None:
        self.status = case.get("response_status", 200)
        self.case = case
        self.request = request
        self.url = request.url
        self.body = body
        self.done = asyncio.Event()

    async def json(self):
        if self.case.get("invalid_response"):
            raise ValueError("invalid JSON")
        return self.body if self.body is not None else self.case.get("response_body", {"records": []})

    async def finished(self):
        await self.done.wait()
        return None


class FakePage:
    def __init__(self, case: dict, events: list[str]) -> None:
        self.case = case
        self.events = events
        self.post = None
        self.view = "archive"
        self.modal_open = False
        self.main_frame = SimpleNamespace(url="https://dcs-learning.cnu.ac.kr/std/archive")
        self.listeners = {}
        self.responses = []
        self.page_number = 1
        self.elapsed_ms = 0
        self.state_reads = 0
        self.partial_states = 0

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        self.listeners[event].remove(callback)

    def _emit(self, event, value):
        for callback in self.listeners.get(event, []):
            callback(value)

    def _request(self, path, method, *, document=False, body=None):
        request = FakeRequest(self, path, method, document)
        self._emit("request", request)
        response = FakeResponse(self.case, request, body)
        self._emit("response", response)
        self.responses.append(response)
        if path == materials._ATTACH_LIST and self.case.get("delayed_attachment"):

            async def complete() -> None:
                await asyncio.sleep(0.02)
                response.done.set()
                self._emit("requestfinished", request)

            asyncio.create_task(complete())
        else:
            response.done.set()
            self._emit("requestfinished", request)
        if document:
            if self.case.get("outgoing_after_document"):
                self._request("/api/v1/archive/list", "POST")
            self.main_frame.url = request.url
            self._emit("framenavigated", self.main_frame)

    def _archive_request(self, *, document):
        stale = None
        if document and self.case.get("stale_late"):
            stale = FakeRequest(self, "/api/v1/archive/list", "POST")
            self._emit("request", stale)
        if document:
            self._request("/std/archive", "GET", document=True)
        if stale is not None:
            response = FakeResponse(self.case, stale)
            self._emit("response", response)
            self.responses.append(response)
            self._emit("requestfinished", stale)
        self._request("/api/v1/archive/list", "POST")
        self.state_reads = 0

    @asynccontextmanager
    async def expect_response(self, predicate, **_kwargs):
        start = len(self.responses)
        armed_ms = self.elapsed_ms
        pending = SimpleNamespace(value=None)
        yield pending
        if self.elapsed_ms - armed_ms > _kwargs["timeout"]:
            raise TimeoutError("archive response arrived after expectation expired")
        matching = [response for response in self.responses[start:] if predicate(response)]
        if not matching:
            raise TimeoutError("missing matching response")
        self.events.append("archive-response")
        pending.value = self._completed_response(matching[0])

    async def _completed_response(self, response):
        return response

    async def wait_for_load_state(self, state, **_kwargs):
        assert state == "networkidle"
        if self.case.get("pending"):
            raise TimeoutError("pending archive request")
        self.events.append("network-idle")

    async def click(self, selector: str) -> None:
        self.events.append(selector)
        if selector == materials._ARCHIVE_MENU:
            self.view = "archive"
            self.in_detail = False
            self.modal_open = False
            self.page_number = 1
            self._archive_request(document=True)

    async def wait_for_selector(self, selector: str, **_kwargs):
        if selector in {"#table_list #listBody", "#totalCnt strong"}:
            return object()
        if selector == materials._MODAL and not (self.post and self.post.get("modal")):
            raise TimeoutError("modal absent")
        return object()

    async def evaluate(self, script: str, arg=None):
        self.events.append("evaluate:" + script.split("*/")[0].split("/*")[-1].strip())
        if script == readiness._TOPBAR_COURSE_JS:
            return (
                "other-course"
                if self.case.get("wrong_restore_course") and self.events.count(materials._ARCHIVE_MENU) > 1
                else self.case["course"]["course_id"]
            )
        if "archiveMetadataState" in script:
            pages = self.case.get("pages", [self.case["posts"]])
            posts = [{"board_item_id": p["board_item_id"], "title": p["title"]} for p in pages[self.page_number - 1]]
            self.state_reads += 1
            if self.case.get("progressive_rows") and posts and self.state_reads == 1:
                posts = posts[:-1]
                self.partial_states += 1
            if self.case.get("restore_fails") and self.events.count(materials._ARCHIVE_MENU) > 0:
                return {"completed": False, "posts": []}
            return {
                "completed": self.case.get("table_ready", True),
                "posts": posts,
                "total_count": sum(len(items) for items in pages),
                "row_count": len(posts),
                "page_size": self.case.get("page_size", 10),
                "current_page": self.page_number,
                "selected_course_id": (
                    "other-course"
                    if self.case.get("wrong_restore_course") and self.events.count(materials._ARCHIVE_MENU) > 1
                    else self.case["course"]["course_id"]
                ),
            }
        if "archiveMetadataClickIcon" in script:
            assert self.view == "archive"
            self.post = next(p for p in self.case["posts"] if p["board_item_id"] == arg)
            self.modal_open = bool(self.post.get("modal"))
            if "attachment_list" in self.post:
                self._request(
                    materials._ATTACH_LIST,
                    "GET",
                    body={
                        "header": {"msg": "OK", "code": 200},
                        "body": {"attachFileList": self.post["attachment_list"]},
                    },
                )
            return None
        if "archiveMetadataTargets" in script:
            if isinstance(arg, dict):
                if arg["modalOnly"]:
                    return self.post.get("modal", []) if self.modal_open else []
                matches = [
                    post
                    for post in self.case["posts"]
                    if post["board_item_id"] == arg["boardItemId"]
                    and post["board_item_id"] not in self.case.get("missing_inline_rows", [])
                ]
                return matches[0].get("inline", []) if len(matches) == 1 else []
            if arg:
                return self.post.get("modal", []) if self.modal_open else []
            return [target for post in self.case["posts"] for target in post.get("inline", [])]
        if "archiveMetadataPage" in script:
            self.page_number = arg
            self._archive_request(document=False)
            return None
        if "archiveMetadataCloseModal" in script:
            self.modal_open = False
            return None
        raise AssertionError("unexpected script")


def fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_archive_fixture_cases() -> None:
    data = fixture()
    assert data["course"]["course_id"] == "course-synthetic-a"
    assert {c["name"] for c in data["cases"]} == {
        "modal-multiple",
        "official-list-filename",
        "unnamed-official",
        "inline-after-modal-timeout",
        "unresolved-after-inline",
        "multiple-posts",
        "completed-empty",
        "table-incomplete",
        "empty-post",
        "duplicate-id",
        "missing-id",
        "metadata-only",
        "failed-restore",
    }
    for case in data["cases"]:
        page = FakePage({**case, "course": data["course"]}, [])
        if case.get("fails"):
            with pytest.raises((CampusError, ValueError)):
                asyncio.run(materials.enumerate_archive(page, data["course"]))
        else:
            rows = asyncio.run(materials.enumerate_archive(page, data["course"]))
            assert len(rows) == sum(len(p.get("modal") or p.get("inline") or []) for p in case["posts"])
            assert len({row["entity_id"] for row in rows}) == len(rows)
            assert all(row["archive_entry"]["board_item_id"] for row in rows)
            if case["name"] == "modal-multiple":
                assert rows[0]["display_name"] == "example.pdf 바로보기"
                assert rows[0]["filename"] == "example.pdf"
                assert rows[0]["downloadable"]
            if case["name"] == "official-list-filename":
                assert rows[0]["display_name"] == "original.pdf 바로보기 "
                assert rows[0]["filename"] == "original.pdf"
                assert rows[0]["downloadable"]
            if case["name"] == "unnamed-official":
                assert not rows[0]["downloadable"]
                assert rows[0]["unavailable_reason"] == "official-name-unavailable"
            if case["name"] == "metadata-only":
                assert not any(row["downloadable"] for row in rows)
            if case["name"] == "multiple-posts":
                assert page.view == "archive"
                assert page.events.count(materials._ARCHIVE_MENU) == 2
        assert not any("downloadFile" in event for event in page.events)


def test_archive_waits_for_attachment_request_completion() -> None:
    data = fixture()
    case = next(case for case in data["cases"] if case["name"] == "official-list-filename")
    page = FakePage({**case, "course": data["course"], "delayed_attachment": True}, [])
    rows = asyncio.run(materials.enumerate_archive(page, data["course"]))
    assert rows[0]["filename"] == "original.pdf"


def test_inline_controls_belong_to_selected_post_before_duplicate_check() -> None:
    course = fixture()["course"]
    posts = [
        {
            "board_item_id": "board-a",
            "title": "First",
            "inline": [{"data_id": "file-a", "text": "a.pdf", "official": True}],
        },
        {
            "board_item_id": "board-b",
            "title": "Second",
            "inline": [{"data_id": "file-b", "text": "b.pdf", "official": True}],
        },
    ]
    page = FakePage({"course": course, "posts": posts}, [])
    rows = asyncio.run(materials.enumerate_archive(page, course))
    assert [(row["archive_entry"]["board_item_id"], row["file_id"]) for row in rows] == [
        ("board-a", "file-a"),
        ("board-b", "file-b"),
    ]


def test_unresolved_inline_row_does_not_borrow_other_post_controls() -> None:
    course = fixture()["course"]
    posts = [
        {"board_item_id": "board-missing", "title": "Missing row"},
        {
            "board_item_id": "board-other",
            "title": "Other",
            "inline": [{"data_id": "file-other", "text": "other.pdf", "official": True}],
        },
    ]
    page = FakePage({"course": course, "posts": posts, "missing_inline_rows": ["board-missing"]}, [])
    with pytest.raises(CampusError) as exc:
        asyncio.run(materials.enumerate_archive(page, course))
    assert exc.value.code == "course-sync-failed"


def test_modal_inline_detail_and_course_failure() -> None:
    data = fixture()
    case = next(c for c in data["cases"] if c["name"] == "duplicate-id")
    page = FakePage({**case, "course": data["course"]}, [])
    with pytest.raises(CampusError, match="enumerate") as exc:
        asyncio.run(materials.enumerate_archive(page, data["course"]))
    assert exc.value.code == "item-identity-missing"


@pytest.mark.parametrize("wrong_restore_course", [False, True])
def test_material_collector_validates_entry_response_and_preserves_full_records(wrong_restore_course: bool) -> None:
    data = fixture()
    case = next(case for case in data["cases"] if case["name"] == "multiple-posts")
    case = {**case, "pages": [[case["posts"][0]], [case["posts"][1]]], "page_size": 1}
    page = FakePage({**case, "course": data["course"], "wrong_restore_course": wrong_restore_course}, [])

    async def collect():
        capture = await materials.arm_materials_capture(page)
        await materials.open_materials_section(page, lambda: page.click(materials._ARCHIVE_MENU), capture=capture)
        return await materials.collect_materials_rows(page, data["course"], capture=capture)

    if wrong_restore_course:
        with pytest.raises(ValueError, match="page course"):
            asyncio.run(collect())
    else:
        rows = asyncio.run(collect())
        assert [(row["archive_entry"]["board_item_id"], row["file_id"], row["filename"]) for row in rows] == [
            ("board-first", "file-first", "first.pdf"),
            ("board-next", "file-next", "next.pdf"),
        ]
        assert page.page_number == 2
    assert page.listeners["request"] == []


def test_archive_waits_for_progressive_rows_across_pages_and_restoration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materials, "_WAIT_MS", 200)
    data = fixture()
    case = next(case for case in data["cases"] if case["name"] == "multiple-posts")
    posts = case["posts"]
    page = FakePage(
        {**case, "course": data["course"], "pages": [[posts[0]], [posts[1]]], "page_size": 1, "progressive_rows": True},
        [],
    )

    rows = asyncio.run(materials.enumerate_archive(page, data["course"]))
    assert {row["file_id"] for row in rows} == {"file-first", "file-next"}
    assert page.partial_states >= 3


def test_material_collector_rejects_duplicate_entry_list() -> None:
    data = fixture()
    case = next(case for case in data["cases"] if case["name"] == "modal-multiple")
    page = FakePage({**case, "course": data["course"]}, [])

    async def collect():
        capture = await materials.arm_materials_capture(page)
        await materials.open_materials_section(page, lambda: page.click(materials._ARCHIVE_MENU), capture=capture)
        page._request(materials._ARCHIVE_LIST, "POST")
        return await materials.collect_materials_rows(page, data["course"], capture=capture)

    with pytest.raises(ValueError, match="duplicate archive response"):
        asyncio.run(collect())


@pytest.mark.parametrize(
    ("case_name", "error_code"),
    [
        ("duplicate-id", "item-identity-missing"),
        ("unresolved-after-inline", "course-sync-failed"),
        ("failed-restore", "course-sync-failed"),
    ],
)
def test_material_collector_fails_entire_course_without_detail_fallback(case_name: str, error_code: str) -> None:
    data = fixture()
    case = next(case for case in data["cases"] if case["name"] == case_name)
    events: list[str] = []
    page = FakePage({**case, "course": data["course"]}, events)

    async def collect() -> None:
        capture = await materials.arm_materials_capture(page)
        await materials.open_materials_section(page, lambda: page.click(materials._ARCHIVE_MENU), capture=capture)
        await materials.collect_materials_rows(page, data["course"], capture=capture)

    with pytest.raises((CampusError, ValueError)) as failure:
        asyncio.run(collect())
    if isinstance(failure.value, CampusError):
        assert failure.value.code == error_code
    assert not any("archiveMetadataOpenDetail" in event for event in events)
    assert page.listeners["request"] == []
    assert page.listeners["response"] == []


def test_failed_archive_section_open_closes_armed_capture() -> None:
    data = fixture()
    page = FakePage({"course": data["course"], "posts": []}, [])

    async def fail() -> None:
        capture = await materials.arm_materials_capture(page)

        async def broken_click() -> None:
            raise RuntimeError("archive menu unavailable")

        await materials.open_materials_section(page, broken_click, capture=capture)

    with pytest.raises(RuntimeError, match="menu unavailable"):
        asyncio.run(fail())
    assert all(not listeners for listeners in page.listeners.values())
