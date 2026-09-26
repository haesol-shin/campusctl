from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from campusctl.envelope import CampusError
from campusctl.identity import notice_entity_id
from campusctl.providers.cnu.notice_detail import capture_notice_detail
from campusctl.source_package import ResourceReference

FIXTURE = Path(__file__).parent / "fixtures/lms_sources/notice_detail_synthetic.html"
ORIGIN = "https://dcs-learning.cnu.ac.kr"
NATIVE = "TB_L_BOARDITEM7001"


class Fixture(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.image: str | None = None
        self.view_counts: list[int] = []
        self.read_state_claim = False
        self.file_claim = False
        self.detail_native: str | None = None
        self._views = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "a" and fields.get("href", "").startswith("/std/noticeDetail"):
            self.links.append(fields["href"] or "")
        if tag == "img":
            self.image = fields.get("src")
        if tag == "article":
            self.detail_native = fields.get("data-boarditem-no")
        self._views = fields.get("data-field") == "views"
        self.read_state_claim |= "read_yn" in fields
        self.file_claim |= "data-file-id" in fields

    def handle_data(self, data: str) -> None:
        if self._views and data.strip().isdigit():
            self.view_counts.append(int(data.strip()))

    def handle_endtag(self, tag: str) -> None:
        self._views = False


class FakePage:
    def __init__(self, fixture: Fixture, *, wrong_info: bool = False) -> None:
        self.fixture = fixture
        self.wrong_info = wrong_info
        self.url = ORIGIN + "/std/myLecture"
        self.listeners: dict[str, object] = {}
        self.actions: list[str] = []

    def on(self, event: str, callback: object) -> None:
        self.listeners[event] = callback

    def remove_listener(self, event: str, callback: object) -> None:
        self.listeners.pop(event, None)

    async def goto(self, url: str, *, wait_until: str) -> None:
        self.actions.append("roster")
        self.url = url

    async def click(self, selector: str) -> None:
        self.actions.append(selector)
        if selector == 'a[href="/std/notice"]':
            self.url = ORIGIN + "/std/notice"
            self._respond("/api/v1/board/notice/list/top", {"list": []})
            self._respond(
                "/api/v1/board/notice/list", {"list": [self._row(7, NATIVE), self._row(8, "TB_L_BOARDITEM7002")]}
            )
        elif selector.startswith("tbody#table-body a["):
            self.url = ORIGIN + self.fixture.links[0]
            self._respond(
                "/api/v1/board/notice/info",
                {
                    "data": {
                        "boarditem_no": "TB_L_BOARDITEM7002" if self.wrong_info else NATIVE,
                        "course_id": "course-example",
                    }
                },
            )
            self._respond("/api/v1/board/cmt/list", {"list": []})

    @staticmethod
    def _row(number: int, native: str) -> dict[str, object]:
        return {
            "boarditem_no": native,
            "row_idx": number,
            "insert_dt_addtime": "2026-09-01 09:00",
            "course_id": "course-example",
        }

    def _respond(self, path: str, body: dict[str, object]) -> None:
        async def finished() -> None:
            return None

        async def json() -> dict[str, object]:
            return {"header": {"code": 200}, "body": body}

        response = SimpleNamespace(
            request=SimpleNamespace(url=ORIGIN + path, method="POST"), status=200, finished=finished, json=json
        )
        self.listeners["response"](response)

    async def wait_for_selector(self, selector: str) -> None:
        return None

    async def wait_for_load_state(self, state: str) -> None:
        return None

    async def evaluate(self, script: str) -> object:
        if "table-body > tr" in script:
            return [{"links": [url]} for url in self.fixture.links]
        assert "#noticeDetail" in script
        return {
            "native_id": self.fixture.detail_native,
            "title": "Example notice",
            "parts": [
                {"kind": "text", "text": "First instruction.\n"},
                {
                    "kind": "image",
                    "url": ORIGIN + (self.fixture.image or ""),
                    "label": "Example diagram",
                    "name": "example-diagram.png",
                },
                {"kind": "text", "text": "Read "},
                {"kind": "link", "text": "example guidance"},
                {"kind": "text", "text": ".\nSecond instruction."},
            ],
        }


class Interceptor:
    def raise_if_denied(self) -> None:
        return None


def fixture_page(*, wrong_info: bool = False) -> FakePage:
    fixture = Fixture()
    fixture.feed(FIXTURE.read_text(encoding="utf-8"))
    assert fixture.detail_native == NATIVE
    assert fixture.view_counts[0] + 1 == fixture.view_counts[-1]
    assert not fixture.read_state_claim and not fixture.file_claim
    return FakePage(fixture, wrong_info=wrong_info)


def selected(number: int = 7) -> dict[str, object]:
    return {
        "entity_id": notice_entity_id("course-example", "2026-09-01 09:00", str(number)),
        "course": {"id": "course-example", "label": "Example Course"},
    }


def test_selected_notice_detail_capture_readonly() -> None:
    page = fixture_page()
    snapshot = asyncio.run(capture_notice_detail(page, selected(), interceptor=Interceptor()))
    assert snapshot.provider_native_id is None  # Live provider-native mapping remains unverified.
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"
    assert snapshot.parts[0] == "# Example notice\n\n"
    assert "First instruction." in snapshot.parts[1]
    assert snapshot.parts[2] == ResourceReference(
        "image", ORIGIN + "/assets/example-diagram.png", "example-diagram.png", None, "Example diagram", None, None
    )
    assert "example guidance [link URL omitted]" in snapshot.parts
    assert "Second instruction." in snapshot.parts[-1]
    assert page.actions[-1].startswith('tbody#table-body a[href="/std/noticeDetail?no=TB_L_BOARDITEM7001')
    assert not any("read" in action.lower() or "upload" in action.lower() for action in page.actions)
    assert not page.listeners


def test_wrong_selected_notice_fails_before_transfer() -> None:
    page = fixture_page()
    wrong = selected(9)
    with pytest.raises(CampusError, match="Selected notice no longer matches"):
        asyncio.run(capture_notice_detail(page, wrong, interceptor=Interceptor()))
    assert not any(action.startswith("tbody#table-body a[") for action in page.actions)
    assert not page.listeners


def test_mismatched_detail_response_rejected() -> None:
    page = fixture_page(wrong_info=True)
    with pytest.raises(CampusError, match="another board item"):
        asyncio.run(capture_notice_detail(page, selected(), interceptor=Interceptor()))
    assert not page.listeners
