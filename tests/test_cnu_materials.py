"""Synthetic archive metadata and guarded catalog sync tests (no LMS session)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError
from campusctl.providers.cnu import materials

_FIXTURE = Path(__file__).parent / "fixtures/lms_sources/materials_archive.json"


class FakeGuard:
    def __init__(self) -> None:
        self.closed = False
        self.denied = False

    def raise_if_denied(self) -> None:
        if self.denied:
            raise CampusError("policy-blocked", "Blocked", None, "error")

    async def close(self) -> None:
        self.closed = True


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

    async def json(self):
        if self.case.get("invalid_response"):
            raise ValueError("invalid JSON")
        return self.body if self.body is not None else self.case.get("response_body", {"records": []})

    async def finished(self):
        return None


class FakePage:
    def __init__(self, case: dict, events: list[str]) -> None:
        self.case = case
        self.events = events
        self.post = None
        self.view = "archive"
        self.modal_open = False
        self.guard = FakeGuard()
        self.main_frame = SimpleNamespace(url="https://dcs-learning.cnu.ac.kr/std/archive")
        self.listeners = {}
        self.responses = []
        self.page_number = 1
        self.elapsed_ms = 0

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
                self._emit("requestfinished", request)

            asyncio.create_task(complete())
        else:
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

    async def goto(self, url: str) -> None:
        assert url == materials.MY_LECTURE_URL
        self.events.append("guarded-roster")
        self.main_frame.url = url
        self.view = "roster"

    async def click(self, selector: str) -> None:
        self.events.append(selector)
        if selector == materials._ARCHIVE_MENU:
            self.view = "archive"
            self.in_detail = False
            self.modal_open = False
            self.page_number = 1
            self._archive_request(document=True)

    async def wait_for_selector(self, selector: str, **_kwargs):
        if selector == materials._MODAL and not (self.post and self.post.get("modal")):
            raise TimeoutError("modal absent")
        return object()

    async def evaluate(self, script: str, arg=None):
        self.events.append("evaluate:" + script.split("*/")[0].split("/*")[-1].strip())
        if script == materials.EXTRACT_COURSES_JS:
            return self.case.get("roster", [self.case["course"]])
        if "archiveMetadataState" in script:
            pages = self.case.get("pages", [self.case["posts"]])
            posts = [{"board_item_id": p["board_item_id"], "title": p["title"]} for p in pages[self.page_number - 1]]
            if self.case.get("restore_fails") and self.events.count(materials._ARCHIVE_MENU) > 0:
                return {"completed": False, "posts": []}
            return {
                "completed": self.case.get("table_ready", True),
                "posts": posts,
                "total_count": sum(len(items) for items in pages),
                "row_count": len(posts),
                "page_size": self.case.get("page_size", 10),
                "current_page": self.page_number,
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
                asyncio.run(materials.enumerate_archive(page, data["course"], page.guard))
        else:
            rows = asyncio.run(materials.enumerate_archive(page, data["course"], page.guard))
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
    rows = asyncio.run(materials.enumerate_archive(page, data["course"], page.guard))
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
    rows = asyncio.run(materials.enumerate_archive(page, course, page.guard))
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
    with pytest.raises(CampusError, match="unresolved") as exc:
        asyncio.run(materials.enumerate_archive(page, course, page.guard))
    assert exc.value.code == "course-sync-failed"


def test_modal_inline_detail_and_course_failure() -> None:
    data = fixture()
    case = next(c for c in data["cases"] if c["name"] == "duplicate-id")
    page = FakePage({**case, "course": data["course"]}, [])
    with pytest.raises(CampusError, match="enumerate") as exc:
        asyncio.run(materials.enumerate_archive(page, data["course"], page.guard))
    assert exc.value.code == "item-identity-missing"


def _install_fake_session(monkeypatch, cases: list[dict], events: list[str]):
    page = FakePage(
        {
            **cases[0],
            "course": fixture()["course"],
            "roster": [dict(fixture()["course"], course_id=f"course-{i}") for i in range(len(cases))],
        },
        events,
    )

    @asynccontextmanager
    async def fake_session(*_args, **kwargs):
        assert kwargs["operation"] == "materials.sync"
        page.open_headless = kwargs["headless"]
        events.append("session")
        yield SimpleNamespace(page=page, context=page)

    async def login(*_args, **_kwargs):
        events.append("login")

    async def interceptor(*_args, **_kwargs):
        events.append("interceptor")
        return page.guard

    async def prepare(_page, _config, course_id, section):
        assert section == "archive"
        index = int(course_id.rsplit("-", 1)[-1])
        page.case = {**cases[index], "course": page.case["course"], "roster": page.case["roster"]}
        page.view = "archive"
        page.elapsed_ms += 8500  # Row navigation is deliberately slower than the old 7-second window.
        events.append(f"enter-{course_id}")

    async def open_section(_page, section):
        assert section == "archive"
        page.elapsed_ms += 2900
        page._archive_request(document=True)

    monkeypatch.setattr(materials, "open_session", fake_session)
    monkeypatch.setattr(materials, "ensure_logged_in", login)
    monkeypatch.setattr(materials, "install_ui_request_interceptor", interceptor)
    monkeypatch.setattr(materials, "prepare_course_section", prepare)
    monkeypatch.setattr(materials, "open_course_section", open_section)
    monkeypatch.setattr(
        materials.UiRequestPolicy, "from_reviewed_config", lambda _config: SimpleNamespace(approved=True)
    )
    return page


@pytest.mark.parametrize("headless", [False, True])
def test_modes_keep_records_failures_and_course_filter(monkeypatch, tmp_path: Path, headless: bool) -> None:
    cases = {case["name"]: case for case in fixture()["cases"]}
    page = _install_fake_session(monkeypatch, [cases["modal-multiple"], cases["inline-after-modal-timeout"]], [])
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, headless=headless, reviewed_policy={}))
    assert page.open_headless is headless
    assert result["courses"] == 2 and result["materials"] == 3 and not errors
    path = domain_catalog_path("materials", tmp_path)
    original = read_domain_catalog("materials", path)
    assert len([row for row in original["materials"] if row["course"]["id"] == "course-0"]) == 2
    page = _install_fake_session(monkeypatch, [cases["duplicate-id"], cases["completed-empty"]], [])
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, headless=headless, reviewed_policy={}))
    assert page.open_headless is headless
    assert result["courses"] == 1 and result["materials"] == 0
    assert [error.code for error in errors] == ["item-identity-missing"]
    stored = read_domain_catalog("materials", path)
    assert [row for row in stored["materials"] if row["course"]["id"] == "course-0"] == [
        row for row in original["materials"] if row["course"]["id"] == "course-0"
    ]
    assert stored["failed_courses"][0]["reason"] == "item-identity-missing"
    page = _install_fake_session(monkeypatch, [cases["completed-empty"], cases["completed-empty"]], [])
    result, errors = asyncio.run(
        materials.sync_materials({}, tmp_path, course_id="course-1", headless=headless, reviewed_policy={})
    )
    assert page.open_headless is headless
    assert result["courses"] == 1 and not errors
    assert [
        row for row in read_domain_catalog("materials", path)["materials"] if row["course"]["id"] == "course-0"
    ] == [row for row in original["materials"] if row["course"]["id"] == "course-0"]


@pytest.mark.parametrize("headless", [False, True])
def test_modes_preserve_policy_denial_before_catalog_publication(monkeypatch, tmp_path: Path, headless: bool) -> None:
    base = next(case for case in fixture()["cases"] if case["name"] == "modal-multiple")
    page = _install_fake_session(monkeypatch, [base], [])
    page.guard.denied = True
    with pytest.raises(CampusError) as failure:
        asyncio.run(materials.sync_materials({}, tmp_path, headless=headless, reviewed_policy={}))
    assert page.open_headless is headless
    assert failure.value.code == "policy-blocked"
    assert not domain_catalog_path("materials", tmp_path).exists()


def test_full_filtered_and_failed_course_merges(monkeypatch, tmp_path: Path) -> None:
    cases = {case["name"]: case for case in fixture()["cases"]}
    events: list[str] = []
    page = _install_fake_session(monkeypatch, [cases["modal-multiple"], cases["inline-after-modal-timeout"]], events)
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert not errors and (result["courses"], result["materials"]) == (2, 3)
    assert events.index("login") < events.index("interceptor") < events.index("guarded-roster")
    assert page.guard.closed
    original = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
    assert len(original["materials"]) == 3

    events.clear()
    _install_fake_session(monkeypatch, [cases["duplicate-id"], cases["completed-empty"]], events)
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert result["courses"] == 1 and result["materials"] == 0
    assert [error.code for error in errors] == ["item-identity-missing"]
    merged = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
    assert len([r for r in merged["materials"] if r["course"]["id"] == "course-0"]) == 2
    assert not any(r["course"]["id"] == "course-1" for r in merged["materials"])
    assert merged["failed_courses"][0]["reason"] == "item-identity-missing"

    events.clear()
    _install_fake_session(monkeypatch, [cases["completed-empty"], cases["completed-empty"]], events)
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, course_id="course-0", reviewed_policy={}))
    assert not errors and result["materials"] == 0
    filtered = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
    assert all(r["course"]["id"] != "course-0" for r in filtered["materials"])

    # A failed full roster retains timestamp and old rows while marking enrollment unknown.
    page = _install_fake_session(monkeypatch, [cases["completed-empty"]], [])
    page.case["roster"] = []
    before = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
    with pytest.raises(CampusError) as exc:
        asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert exc.value.code == "course-discovery-failed"
    after = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
    assert after["generated_at"] == before["generated_at"]
    assert after["materials"] == before["materials"]
    assert after["enrollment_state"] == "unknown"


@pytest.mark.parametrize(
    "failure", [{"response_status": 500}, {"invalid_response": True}, {"pending": True}, {"table_ready": False}]
)
def test_unfinished_archive_never_replaces_course(monkeypatch, tmp_path: Path, failure: dict) -> None:
    base = next(case for case in fixture()["cases"] if case["name"] == "modal-multiple")
    _install_fake_session(monkeypatch, [base], [])
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert not errors and result["materials"] == 2
    path = domain_catalog_path("materials", tmp_path)
    before = read_domain_catalog("materials", path)

    _install_fake_session(monkeypatch, [{**base, **failure}], [])
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert result["materials"] == 0
    assert [error.code for error in errors] == ["course-sync-failed"]
    after = read_domain_catalog("materials", path)
    assert after["materials"] == before["materials"]
    assert after["failed_courses"][0]["reason"] == "course-sync-failed"


@pytest.mark.parametrize("late", ["stale_late", "outgoing_after_document"])
def test_late_outgoing_archive_response_cannot_claim_next_course(monkeypatch, tmp_path: Path, late: str) -> None:
    base = next(case for case in fixture()["cases"] if case["name"] == "modal-multiple")
    _install_fake_session(monkeypatch, [base], [])
    asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    path = domain_catalog_path("materials", tmp_path)
    old_rows = read_domain_catalog("materials", path)["materials"]
    _install_fake_session(monkeypatch, [{**base, late: True}], [])
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert result["materials"] == 0
    assert [error.code for error in errors] == ["course-sync-failed"]
    assert read_domain_catalog("materials", path)["materials"] == old_rows


def test_paginated_archive_enumerates_all_posts(monkeypatch, tmp_path: Path) -> None:
    posts = [
        {
            "board_item_id": f"board-{number}",
            "title": f"Sample {number}",
            "modal": [
                {"data_id": f"file-{number}", "text": f"sample-{number}.pdf", "url": "javascript:;", "official": True}
            ],
        }
        for number in range(11)
    ]
    case = {"name": "paginated", "posts": posts, "pages": [posts[:10], posts[10:]], "page_size": 10}
    events: list[str] = []
    _install_fake_session(monkeypatch, [case], events)
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert not errors and result["materials"] == 11
    catalog = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
    assert len(catalog["materials"]) == 11
    assert "evaluate:archiveMetadataPage" in events


def test_unresolved_attachment_controls_keep_stale_course(monkeypatch, tmp_path: Path) -> None:
    cases = {case["name"]: case for case in fixture()["cases"]}
    _install_fake_session(monkeypatch, [cases["modal-multiple"]], [])
    asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    path = domain_catalog_path("materials", tmp_path)
    old_rows = read_domain_catalog("materials", path)["materials"]

    events: list[str] = []
    _install_fake_session(monkeypatch, [cases["unresolved-after-inline"]], events)
    result, errors = asyncio.run(materials.sync_materials({}, tmp_path, reviewed_policy={}))
    assert result["materials"] == 0
    assert [error.code for error in errors] == ["course-sync-failed"]
    assert "attachment controls were unresolved" in errors[0].message.lower()
    assert read_domain_catalog("materials", path)["materials"] == old_rows
    assert not any("archiveMetadataOpenDetail" in event for event in events)
