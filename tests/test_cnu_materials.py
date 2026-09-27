"""Synthetic archive metadata and catalog sync tests (no LMS session)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Error as PlaywrightError

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


class _DocumentHandle:
    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


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
        self.document_generation = 1
        self.state_observations = 0
        self.handles: list[_DocumentHandle] = []
        self.mutation = None
        self.backdrop = None
        self.body_modal_open = False

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
            self.mutation = None
            self.backdrop = None
            self.body_modal_open = False
            self.view = "archive"
            self.in_detail = False
            self.modal_open = False
            self.page_number = 1
            self._replace_document()
            self._archive_request(document=True)

    def _replace_document(self) -> None:
        self.document_generation += 1
        for handle in self.handles:
            handle.disposed = True

    def _apply_after_close(self) -> None:
        mutation = self.case.get("after_close_by_post", {}).get(
            self.post["board_item_id"], self.case.get("after_close")
        )
        if not mutation:
            return
        self.mutation = mutation
        if mutation in {"replace-document", "navigate-away-back"}:
            self._replace_document()
        if mutation == "wrong-path":
            self.main_frame.url = "https://lms.example.invalid/std/other"
        if mutation == "navigate-away-back":
            self._request("/std/other", "GET", document=True)
            self._request("/std/archive", "GET", document=True)
        if mutation == "changed-page":
            self.page_number += 1
        if mutation == "visible-modal":
            self.modal_open = True
        if mutation == "hidden-backdrop":
            self.backdrop = "hidden"
        if mutation == "visible-backdrop":
            self.backdrop = "visible"
        if mutation == "body-modal-open":
            self.body_modal_open = True
        if mutation == "pending-navigation":
            self._emit("request", FakeRequest(self, "/std/other", "GET", document=True))
        if mutation == "archive-list-refresh":
            self._request(materials._ARCHIVE_LIST, "POST")
        if mutation == "telemetry":
            self._request("/api/v1/telemetry", "POST")
        if mutation == "request-runtime":
            request = FakeRequest(self, "/std/other", "GET", document=True)

            def broken_navigation() -> bool:
                raise RuntimeError("request inspection failed")

            request.is_navigation_request = broken_navigation
            self._emit("request", request)
        if mutation == "child-frame":
            child = SimpleNamespace(url=self.main_frame.url)
            navigation = FakeRequest(self, "/std/other", "GET", document=True)
            navigation.frame = child
            listing = FakeRequest(self, materials._ARCHIVE_LIST, "POST")
            listing.frame = child
            self._emit("request", navigation)
            self._emit("request", listing)
            self._emit("requestfinished", listing)

    async def evaluate_handle(self, script: str, arg=None):
        if self.case.get("missing_handle"):
            raise PlaywrightError("missing document handle")
        if self.case.get("capture_destroyed"):
            raise PlaywrightError("Execution context was destroyed")
        if self.case.get("capture_timeout"):
            raise CampusError("browser-timeout", "Timed out while retaining archive document.", "Retry.", "error")
        if self.case.get("capture_runtime"):
            raise RuntimeError("unrelated capture failure")
        handle = _DocumentHandle(self.document_generation)
        self.handles.append(handle)
        if before_click := self.case.get("before_click"):
            self.mutation = before_click
        return handle

    def _restoring(self) -> bool:
        clicks = self.events.count(materials._ARCHIVE_MENU)
        return clicks > getattr(self, "_menu_before_restore", clicks)

    def _observed_posts(self) -> list[dict[str, str]]:
        pages = self.case.get("pages", [self.case["posts"]])
        index = self.page_number - 1
        if index < 0 or index >= len(pages):
            return []
        posts = [{"board_item_id": post["board_item_id"], "title": post["title"]} for post in pages[index]]
        if self.mutation == "reordered-ids":
            return list(reversed(posts))
        if self.mutation == "replaced-ids" and posts:
            return [{"board_item_id": "board-replaced", "title": posts[0]["title"]}, *posts[1:]]
        if self.mutation == "title-only" and posts:
            return [{**posts[0], "title": f"{posts[0]['title']} changed"}, *posts[1:]]
        if self._restoring() and self.case.get("changed_restore_posts"):
            return [{"board_item_id": "board-changed", "title": "Changed title"}]
        return posts

    def _observed_state(self, arg):
        self.state_observations += 1
        if getattr(arg, "disposed", False) and hasattr(arg, "generation"):
            raise PlaywrightError("JSHandle is disposed")
        if self.mutation == "destroyed-context":
            raise PlaywrightError("Execution context was destroyed")
        if self.mutation == "proof-timeout":
            raise CampusError("browser-timeout", "Timed out while observing archive table.", "Retry.", "error")
        if arg is not None and self.case.get("pre_observation_error"):
            raise CampusError("course-sync-failed", "Observation failed.", "Retry.", "error")
        clicks = self.events.count(materials._ARCHIVE_MENU)
        if not hasattr(self, "_menu_before_restore"):
            self._menu_before_restore = clicks
        posts = self._observed_posts()
        if self.case.get("restore_fails") and self._restoring():
            return {"completed": False, "posts": [], "modal_clear": False}
        selected = self.case["course"]["course_id"]
        if self.mutation in {"missing-course", "ambiguous-course"}:
            selected = None
        elif self.mutation == "wrong-course" or (self.case.get("wrong_restore_course") and self._restoring()):
            selected = "other-course"
        total = sum(len(items) for items in self.case.get("pages", [self.case["posts"]]))
        if self.mutation == "changed-total":
            total += 1
        page_size = self.case.get("page_size", 10)
        if self.mutation == "changed-page-size":
            page_size += 1
        row_count = len(posts)
        if self.mutation == "changed-row-count":
            row_count = 0 if row_count else 1
        modal_clear = not self.modal_open and self.backdrop is None and not self.body_modal_open
        if self._restoring() and self.case.get("residual_overlay"):
            modal_clear = False
        state = {
            "completed": (
                False
                if self.mutation == "incomplete-table"
                or (arg is None and self.state_observations <= self.case.get("initial_incomplete_observations", 0))
                else self.case.get("table_ready", True)
            ),
            "posts": posts,
            "total_count": total,
            "row_count": row_count,
            "page_size": page_size,
            "current_page": self.page_number,
            "selected_course_id": selected,
            "same_document": arg is not None
            and getattr(arg, "generation", None) == self.document_generation
            and not getattr(arg, "disposed", False),
            "archive_path": urlsplit(self.main_frame.url).path == "/std/archive",
            "modal_clear": modal_clear,
        }
        if self.mutation and self.mutation.startswith("missing-"):
            state.pop(self.mutation.removeprefix("missing-").replace("-", "_"), None)
        return state

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
            return self._observed_state(arg)
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
            self._apply_after_close()
            return None
        raise AssertionError("unexpected script")


def fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_archive_fixture_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materials, "_WAIT_MS", 100)
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
                assert [(row["file_id"], row["filename"], row["archive_entry"]["board_item_id"]) for row in rows] == [
                    ("file-first", "first.pdf", "board-first"),
                    ("file-next", "next.pdf", "board-next"),
                ]
                assert page.events.count(materials._ARCHIVE_MENU) == 0
                assert not any(response.request.url.endswith(materials._ARCHIVE_LIST) for response in page.responses)
                assert not page.modal_open
                assert page.backdrop is None
            if case["name"] == "completed-empty":
                assert page.events.count(materials._ARCHIVE_MENU) == 0
            if case["name"] == "inline-after-modal-timeout":
                assert rows[0]["archive_entry"]["board_item_id"] == "board-inline"
                assert rows[0]["file_id"] == "file-inline"
                assert page.events.count(materials._ARCHIVE_MENU) == 0
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
    if wrong_restore_course:
        case = {**case, "after_close": "replace-document"}
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
def test_material_collector_fails_entire_course_without_detail_fallback(
    case_name: str, error_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(materials, "_WAIT_MS", 100)
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


def _sample_posts(*board_ids: str) -> list[dict]:
    return [
        {
            "board_item_id": board_id,
            "title": f"Post {board_id}",
            "inline": [{"data_id": f"file-{board_id}", "text": f"{board_id}.pdf", "official": True}],
        }
        for board_id in board_ids
    ]


def _associated(rows: list[dict]) -> list[tuple[str, str, str]]:
    return [(row["archive_entry"]["board_item_id"], row["file_id"], row["filename"]) for row in rows]


def _phase_count(profile: dict, phase: str) -> int:
    return sum(span["count"] for span in profile["spans"] if span["phase"] == phase)


def _profiled(page: FakePage, course: dict) -> tuple[list[dict], dict]:
    import io

    from campusctl.browser import profile_context
    from campusctl.profiling import SpanRecorder

    recorder = SpanRecorder(enabled=True, scope=("materials",))
    with profile_context(recorder):
        rows = asyncio.run(materials.enumerate_archive(page, course))
    profile = recorder.finish(stderr=io.StringIO())
    assert profile is not None
    return rows, profile


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-course",
        "missing-course",
        "ambiguous-course",
        "changed-page",
        "changed-total",
        "changed-page-size",
        "changed-row-count",
        "missing-current-page",
        "missing-total-count",
        "missing-page-size",
        "missing-row-count",
        "missing-posts",
        "missing-modal-clear",
        "missing-same-document",
        "missing-archive-path",
        "reordered-ids",
        "replaced-ids",
        "title-only",
        "incomplete-table",
        "visible-modal",
        "hidden-backdrop",
        "visible-backdrop",
        "body-modal-open",
        "wrong-path",
        "destroyed-context",
        "proof-timeout",
        "replace-document",
        "navigate-away-back",
        "pending-navigation",
        "archive-list-refresh",
    ],
)
def test_each_proof_failure_reloads_the_existing_archive(mutation: str) -> None:
    course = fixture()["course"]
    posts = _sample_posts("board-a", "board-b")
    page = FakePage({"course": course, "posts": posts, "after_close": mutation}, [])
    rows, profile = _profiled(page, course)
    assert _associated(rows) == [
        ("board-a", "file-board-a", "board-a.pdf"),
        ("board-b", "file-board-b", "board-b.pdf"),
    ]
    assert page.events.count(materials._ARCHIVE_MENU) == 2
    assert _phase_count(profile, "archive-restore") == 2
    assert _phase_count(profile, "document-commit") == 2
    assert not page.modal_open
    assert page.backdrop is None
    assert not page.body_modal_open
    assert all(handle.disposed for handle in page.handles)
    assert page.listeners["request"] == []


@pytest.mark.parametrize("flag", ["missing_handle", "capture_destroyed", "capture_timeout"])
def test_unavailable_document_evidence_uses_fallback(flag: str) -> None:
    course = fixture()["course"]
    page = FakePage({"course": course, "posts": _sample_posts("board-a"), flag: True}, [])
    rows = asyncio.run(materials.enumerate_archive(page, course))
    assert _associated(rows) == [("board-a", "file-board-a", "board-a.pdf")]
    assert page.events.count(materials._ARCHIVE_MENU) == 1
    assert page.listeners["request"] == []


def test_unrelated_capture_error_propagates_and_releases_listeners() -> None:
    course = fixture()["course"]
    page = FakePage({"course": course, "posts": _sample_posts("board-a"), "capture_runtime": True}, [])
    with pytest.raises(RuntimeError, match="unrelated capture failure"):
        asyncio.run(materials.enumerate_archive(page, course))
    assert page.listeners["request"] == []
    assert page.events.count(materials._ARCHIVE_MENU) == 0


def test_child_frame_activity_does_not_reload_the_archive() -> None:
    course = fixture()["course"]
    page = FakePage({"course": course, "posts": _sample_posts("board-a", "board-b"), "after_close": "child-frame"}, [])
    rows, profile = _profiled(page, course)
    assert _associated(rows) == [
        ("board-a", "file-board-a", "board-a.pdf"),
        ("board-b", "file-board-b", "board-b.pdf"),
    ]
    assert page.events.count(materials._ARCHIVE_MENU) == 0
    assert _phase_count(profile, "archive-restore") == 2
    assert _phase_count(profile, "document-commit") == 0


def test_later_page_inspection_does_not_reset_to_the_first_page() -> None:
    course = fixture()["course"]
    posts = _sample_posts("board-a", "board-b")
    page = FakePage({"course": course, "posts": posts, "pages": [[posts[0]], [posts[1]]], "page_size": 1}, [])
    rows, profile = _profiled(page, course)
    assert _associated(rows) == [
        ("board-a", "file-board-a", "board-a.pdf"),
        ("board-b", "file-board-b", "board-b.pdf"),
    ]
    assert page.page_number == 2
    assert page.events.count("evaluate:archiveMetadataPage") == 1
    assert page.events.count(materials._ARCHIVE_MENU) == 0
    assert _phase_count(profile, "archive-restore") == 2
    assert _phase_count(profile, "document-commit") == 0


def test_inline_controls_skip_reload_only_with_complete_proof() -> None:
    course = fixture()["course"]
    posts = _sample_posts("board-inline")
    retained = FakePage({"course": course, "posts": posts}, [])
    retained_rows = asyncio.run(materials.enumerate_archive(retained, course))
    assert _associated(retained_rows) == [("board-inline", "file-board-inline", "board-inline.pdf")]
    assert retained.events.count(materials._ARCHIVE_MENU) == 0
    reloaded = FakePage({"course": course, "posts": posts, "after_close": "title-only"}, [])
    reloaded_rows = asyncio.run(materials.enumerate_archive(reloaded, course))
    assert _associated(reloaded_rows) == [("board-inline", "file-board-inline", "board-inline.pdf")]
    assert reloaded.events.count(materials._ARCHIVE_MENU) == 1


@pytest.mark.parametrize(
    ("case_update", "error_type"),
    [
        ({"invalid_response": True}, ValueError),
        ({"wrong_restore_course": True}, ValueError),
        ({"changed_restore_posts": True}, CampusError),
        ({"residual_overlay": True}, CampusError),
    ],
)
def test_failed_fallback_fails_collection_and_releases_observers(case_update: dict, error_type: type) -> None:
    course = fixture()["course"]
    page = FakePage(
        {"course": course, "posts": _sample_posts("board-a"), "after_close": "replace-document", **case_update},
        [],
    )
    with pytest.raises(error_type):
        asyncio.run(materials.enumerate_archive(page, course))
    assert page.events.count(materials._ARCHIVE_MENU) == 1
    assert all(handle.disposed for handle in page.handles)
    assert page.listeners["request"] == []
    assert page.listeners["response"] == []


def test_extraction_failure_propagates_after_successful_retention() -> None:
    data = fixture()
    case = next(item for item in data["cases"] if item["name"] == "duplicate-id")
    page = FakePage({**case, "course": data["course"]}, [])
    with pytest.raises(CampusError, match="enumerate") as exc:
        asyncio.run(materials.enumerate_archive(page, data["course"]))
    assert exc.value.code == "item-identity-missing"
    assert all(handle.disposed for handle in page.handles)
    assert page.listeners["request"] == []


def test_completed_empty_archive_does_not_restore() -> None:
    data = fixture()
    case = next(item for item in data["cases"] if item["name"] == "completed-empty")
    page = FakePage({**case, "course": data["course"]}, [])
    rows, profile = _profiled(page, data["course"])
    assert rows == []
    assert page.events.count(materials._ARCHIVE_MENU) == 0
    assert _phase_count(profile, "archive-restore") == 0
    assert _phase_count(profile, "document-commit") == 0


def test_pre_click_mutation_is_not_retained() -> None:
    course = fixture()["course"]
    page = FakePage(
        {"course": course, "posts": _sample_posts("board-a"), "before_click": "title-only"},
        [],
    )
    rows = asyncio.run(materials.enumerate_archive(page, course))
    assert _associated(rows) == [("board-a", "file-board-a", "board-a.pdf")]
    assert page.events.count(materials._ARCHIVE_MENU) == 1
    assert all(handle.disposed for handle in page.handles)


def test_unrelated_requests_do_not_disqualify_retention() -> None:
    course = fixture()["course"]
    page = FakePage({"course": course, "posts": _sample_posts("board-a"), "after_close": "telemetry"}, [])
    rows = asyncio.run(materials.enumerate_archive(page, course))
    assert _associated(rows) == [("board-a", "file-board-a", "board-a.pdf")]
    assert page.events.count(materials._ARCHIVE_MENU) == 0


def test_observation_failure_does_not_leak_document_handle() -> None:
    course = fixture()["course"]
    page = FakePage({"course": course, "posts": _sample_posts("board-a"), "pre_observation_error": True}, [])
    with pytest.raises(CampusError) as exc:
        asyncio.run(materials.enumerate_archive(page, course))
    assert exc.value.code == "course-sync-failed"
    assert all(handle.disposed for handle in page.handles)
    assert page.listeners["request"] == []


def test_profile_distinguishes_retained_and_reloaded_restores() -> None:
    course = fixture()["course"]
    page = FakePage(
        {
            "course": course,
            "posts": _sample_posts("board-a", "board-b"),
            "after_close_by_post": {"board-a": "replace-document"},
        },
        [],
    )
    rows, profile = _profiled(page, course)
    assert _associated(rows) == [
        ("board-a", "file-board-a", "board-a.pdf"),
        ("board-b", "file-board-b", "board-b.pdf"),
    ]
    assert page.events.count(materials._ARCHIVE_MENU) == 1
    assert _phase_count(profile, "archive-restore") == 2
    assert _phase_count(profile, "document-commit") == 1
    assert all(handle.disposed for handle in page.handles)


def test_initial_archive_state_waits_for_completed_rows() -> None:
    course = fixture()["course"]
    page = FakePage(
        {"course": course, "posts": _sample_posts("board-a"), "initial_incomplete_observations": 2},
        [],
    )
    rows = asyncio.run(materials.enumerate_archive(page, course))
    assert _associated(rows) == [("board-a", "file-board-a", "board-a.pdf")]
    assert page.state_observations >= 3
    assert page.events.count(materials._ARCHIVE_MENU) == 0


def test_unrelated_request_inspection_error_propagates() -> None:
    course = fixture()["course"]
    page = FakePage({"course": course, "posts": _sample_posts("board-a"), "after_close": "request-runtime"}, [])
    with pytest.raises(RuntimeError, match="request inspection failed"):
        asyncio.run(materials.enumerate_archive(page, course))
    assert page.listeners["request"] == []
    assert all(handle.disposed for handle in page.handles)
