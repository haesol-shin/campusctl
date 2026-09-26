"""Synthetic selected-task replay; no LMS or submission control is contacted."""

from __future__ import annotations

import asyncio
import json
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu import assignment_detail as adapter
from campusctl.source_package import ResourceReference

FIXTURE = Path(__file__).parent / "fixtures" / "lms_sources" / "assignment_detail_synthetic.html"
RESPONSE_FIXTURE = Path(__file__).parent / "fixtures" / "lms_sources" / "assignment_detail_responses_synthetic.json"
RESPONSES = json.loads(RESPONSE_FIXTURE.read_text(encoding="utf-8"))
SELECTED = {
    "entity_id": "cnu_assignment:course-a:TB_L_REPORT101",
    "task_id": "TB_L_REPORT101",
    "course": {"id": "course-a", "label": "Sample course"},
}


class BriefFixture(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.parts: list[dict[str, Any]] = []
        self.badge = ""
        self.upload_controls: list[str] = []
        self.task_id: str | None = None
        self._link: dict[str, Any] | None = None
        self._heading = False
        self._badge = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if attr.get("id") == "selected-task":
            self.task_id = attr.get("data-id")
            self.depth = 1
        elif self.depth and tag not in {"img", "input", "br", "meta", "link"}:
            self.depth += 1
        if attr.get("id") in {"uploadFile", "fileUploadModal"}:
            self.upload_controls.append(attr["id"])
        if self.depth <= 1:
            if "text_badge" in (attr.get("class") or ""):
                self._badge = True
            return
        if tag == "h4":
            self.parts.append({"kind": "prefix", "value": "\n#### "})
            self._heading = True
        elif tag == "p":
            self.parts.append({"kind": "prefix", "value": "\n"})
        elif tag == "li":
            self.parts.append({"kind": "prefix", "value": "\n- "})
        elif tag == "img":
            self.parts.append(
                {
                    "kind": "image",
                    "url": "https://dcs-learning.cnu.ac.kr" + attr["src"],
                    "label": attr["alt"],
                    "name": "diagram.png",
                }
            )
        elif tag == "a":
            self._link = {
                "kind": "attachment" if attr.get("data-act") == "downloadFile" else "ordinary-link",
                "label": "",
                "file_id": attr.get("data-id"),
                "name": attr.get("data-name"),
                "url": None,
            }

    def handle_data(self, data: str) -> None:
        if self._badge:
            self.badge += data
        if self.depth < 2:
            return
        if self._link is not None:
            self._link["label"] += data
        elif data.strip():
            self.parts.append({"kind": "text", "value": data})

    def handle_endtag(self, tag: str) -> None:
        if self.depth >= 2:
            if tag == "a" and self._link is not None:
                self.parts.append(self._link)
                self._link = None
            if tag in {"h4", "p", "li", "ul"}:
                self.parts.append({"kind": "prefix", "value": "\n"})
        if tag == "span":
            self._badge = False
        if self.depth:
            self.depth -= 1


class Response:
    def __init__(self, path: str, native: str | None, course_id: str) -> None:
        self.url = "https://dcs-learning.cnu.ac.kr" + path
        self.request = SimpleNamespace(method="POST")
        self.status = 200
        self.native = native
        self.course_id = course_id

    async def finished(self) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        fixture = RESPONSES["stdDetail" if self.url.endswith("/stdDetail") else "detail"]
        body = dict(fixture["body"])
        body["course_id"] = self.course_id
        if self.native is None:
            body.pop("report_no")
        else:
            body["report_no"] = self.native
            if "contents_id" in body:
                body["contents_id"] = self.native
        return {"header": fixture["header"], "body": body}


class Expectation:
    def __init__(self, page: Page, predicate: Any) -> None:
        self.page = page
        self.predicate = predicate
        self.value: asyncio.Future[Response] = asyncio.get_running_loop().create_future()

    async def __aenter__(self) -> Expectation:
        self.page.waiters.append(self)
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.page.waiters.remove(self)
        if not self.value.done():
            raise AssertionError("A required detail request was not observed")


class SelectedLink:
    def __init__(self, page: Page, selector: str) -> None:
        self.page = page
        self.selector = selector

    async def count(self) -> int:
        return int("TB_L_REPORT101" in self.selector)

    async def get_attribute(self, name: str) -> str:
        assert name == "data-id"
        return "TB_L_REPORT101"

    async def click(self) -> None:
        self.page.actions.append(self.selector)
        self.page.image_requests += 1
        for path in adapter._DETAIL_PATHS:
            native = self.page.std_id if path.endswith("/stdDetail") and self.page.std_id else self.page.observed_id
            if self.page.missing_report and path.endswith("/detail"):
                native = None
            response = Response(path, native, self.page.observed_course)
            for waiter in self.page.waiters:
                if waiter.predicate(response):
                    waiter.value.set_result(response)
        self.page.url = "https://dcs-learning.cnu.ac.kr/std/taskView?curPage=undefined"


class Page:
    def __init__(
        self,
        fixture: BriefFixture,
        observed_id: str,
        *,
        observed_course: str = "course-a",
        std_id: str | None = None,
        missing_report: bool = False,
    ) -> None:
        self.fixture = fixture
        self.observed_id = observed_id
        self.observed_course = observed_course
        self.std_id = std_id
        self.missing_report = missing_report
        self.url = "https://dcs-learning.cnu.ac.kr/std/myLecture"
        self.actions: list[str] = []
        self.waiters: list[Expectation] = []
        self.image_requests = 0
        self.extracted = False

    async def wait_for_load_state(self, state: str) -> None:
        assert state == "networkidle"

    async def wait_for_selector(self, selector: str, **_: Any) -> None:
        assert selector in {'a[data-act="detail"][data-id]', ".card-body h4"}

    async def evaluate(self, expression: str) -> Any:
        if expression == adapter.EXTRACT_COURSE_CONTEXT_JS:
            return "course-a"
        assert expression == adapter.EXTRACT_ASSIGNMENT_DETAIL_JS
        self.extracted = True
        return {"parts": self.fixture.parts, "page_task_id": self.fixture.task_id}

    def locator(self, selector: str) -> SelectedLink:
        return SelectedLink(self, selector)

    def expect_response(self, predicate: Any, **_: Any) -> Expectation:
        return Expectation(self, predicate)


@pytest.fixture
def brief() -> BriefFixture:
    parser = BriefFixture()
    parser.feed(FIXTURE.read_text(encoding="utf-8"))
    return parser


async def _capture(monkeypatch: pytest.MonkeyPatch, page: Page) -> Any:
    async def enter(_: Any, __: Any, course_id: str, section: str) -> None:
        assert (course_id, section) == ("course-a", "task")
        page.actions.append("course-a:task")

    async def open_section(_: Any, section: str) -> None:
        assert section == "task"
        page.actions.append("task")

    monkeypatch.setattr(adapter, "prepare_course_section", enter)
    monkeypatch.setattr(adapter, "open_course_section", open_section)
    return await adapter.capture_assignment_detail(page, {}, SELECTED)


def test_selected_assignment_detail_capture_readonly(monkeypatch: pytest.MonkeyPatch, brief: BriefFixture) -> None:
    page = Page(brief, "TB_L_REPORT101")
    original_badge = brief.badge
    snapshot = asyncio.run(_capture(monkeypatch, page))
    assert snapshot.provider_native_id == SELECTED["task_id"] == brief.task_id
    assert snapshot.source_url.endswith("/std/taskView?curPage=undefined")
    readable = "".join(part for part in snapshot.parts if isinstance(part, str))
    assert "Sample assignment brief" in readable
    assert "Read the instructions" in readable and "만점 10점" in readable
    assert "reference guide [link URL omitted]" in readable
    assert "session=placeholder" not in readable
    refs = [part for part in snapshot.parts if isinstance(part, ResourceReference)]
    assert [part.kind for part in refs] == ["image", "attachment"]
    assert snapshot.parts.index(refs[0]) < snapshot.parts.index(refs[1])
    assert refs[1].official_target.parent_id == SELECTED["task_id"]
    assert refs[1].official_target.file_id == "TB_L_FILE101"
    assert refs[1].official_target.control_locator == 'a[data-act="downloadFile"][data-id="TB_L_FILE101"]'
    assert brief.badge == original_badge == "미완료"
    assert brief.upload_controls == ["uploadFile", "fileUploadModal"]
    assert len(page.actions) == 3
    assert all("uploadFile" not in action and "modal" not in action.lower() for action in page.actions)
    assert page.image_requests == 1
    assert page.extracted


def test_wrong_selected_task_fails_before_transfer(monkeypatch: pytest.MonkeyPatch, brief: BriefFixture) -> None:
    page = Page(brief, "TB_L_REPORT102")
    with pytest.raises(CampusError) as failure:
        asyncio.run(_capture(monkeypatch, page))
    assert failure.value.code == "entity-unknown"
    assert "Assignment detail selection" in failure.value.message
    assert "TB_L_REPORT101" not in failure.value.message
    assert page.actions == ["course-a:task", "task", 'a[data-act="detail"][data-id="TB_L_REPORT101"]']
    assert not any(action.startswith("download") for action in page.actions)
    assert page.fixture.badge == "미완료"
    assert page.image_requests == 1
    assert not page.extracted


@pytest.mark.parametrize(
    "options",
    [
        {"observed_course": "other-course"},
        {"std_id": "TB_L_REPORT102"},
        {"missing_report": True},
    ],
)
def test_assignment_response_identity_rejects_before_extraction(
    monkeypatch: pytest.MonkeyPatch, brief: BriefFixture, options: dict[str, Any]
) -> None:
    page = Page(brief, "TB_L_REPORT101", **options)
    with pytest.raises(CampusError) as failure:
        asyncio.run(_capture(monkeypatch, page))
    assert failure.value.code == "entity-unknown"
    assert not page.extracted
    assert page.image_requests == 1


def test_assignment_detail_extracts_media_omission_references(
    monkeypatch: pytest.MonkeyPatch, brief: BriefFixture
) -> None:
    brief.parts.append(
        {
            "kind": "video",
            "url": "https://dcs-learning.cnu.ac.kr/lecture.mp4",
            "label": "Sample video",
            "name": "lecture.mp4",
        }
    )
    page = Page(brief, "TB_L_REPORT101")
    snapshot = asyncio.run(_capture(monkeypatch, page))
    media_refs = [p for p in snapshot.parts if isinstance(p, ResourceReference) and p.kind == "video"]
    assert len(media_refs) == 1
    assert media_refs[0].label == "Sample video"
    assert media_refs[0].source_url == "https://dcs-learning.cnu.ac.kr/lecture.mp4"
