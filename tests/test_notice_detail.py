from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    def __init__(
        self,
        fixture: Fixture,
        *,
        wrong_info: bool = False,
        row_addtime: str = "2026-09-01 09:00",
        row_insert_dt: str = "2026-09-01",
        board_number: int = 7,
    ) -> None:
        self.fixture = fixture
        self.wrong_info = wrong_info
        self.row_addtime = row_addtime
        self.row_insert_dt = row_insert_dt
        self.board_number = board_number
        self.url = ORIGIN + "/std/myLecture"
        self.listeners: dict[str, object] = {}
        self.actions: list[str] = []
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

        async def fake_get(url: str, **_kwargs: Any) -> Any:
            class ImgResponse:
                status = 200
                headers = {"content-type": "image/png", "content-length": str(len(png_bytes))}

                def __init__(self, u: str) -> None:
                    self.url = u

                async def body(self) -> bytes:
                    return png_bytes

                async def dispose(self) -> None:
                    pass

            return ImgResponse(url)

        self.context = SimpleNamespace(request=SimpleNamespace(get=fake_get))

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
                "/api/v1/board/notice/list",
                {
                    "list": [
                        self._row(self.board_number, NATIVE, addtime=self.row_addtime, insert_dt=self.row_insert_dt),
                        self._row(8, "TB_L_BOARDITEM7002", title="Another notice"),
                    ]
                },
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
    def _row(
        number: int,
        native: str,
        *,
        addtime: str = "2026-09-01 09:00",
        insert_dt: str = "2026-09-01",
        title: str = "Example notice",
    ) -> dict[str, object]:
        return {
            "boarditem_no": native,
            "row_idx": number,
            "insert_dt": insert_dt,
            "insert_dt_addtime": addtime,
            "boarditem_title": title,
            "delete_yn": "N",
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


def fixture_page(
    *,
    wrong_info: bool = False,
    addtime: str = "2026-09-01 09:00",
    insert_dt: str = "2026-09-01",
    board_number: int = 7,
) -> FakePage:
    fixture = Fixture()
    fixture.feed(FIXTURE.read_text(encoding="utf-8"))
    assert fixture.detail_native == NATIVE
    assert fixture.view_counts[0] + 1 == fixture.view_counts[-1]
    assert not fixture.read_state_claim and not fixture.file_claim
    return FakePage(
        fixture, wrong_info=wrong_info, row_addtime=addtime, row_insert_dt=insert_dt, board_number=board_number
    )


def selected(number: int = 7, *, title: str = "Example notice", date: str = "2026-09-01 09:00") -> dict[str, object]:
    return {
        "entity_id": notice_entity_id("course-example", date, str(number)),
        "legacy_key": f"Example Course_{date}_{number}",
        "title": title,
        "date": date,
        "status": None,
        "is_unread": None,
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


def test_todo_number_different_from_board_row_index_opens_selected_notice() -> None:
    page = fixture_page(board_number=1)
    snapshot = asyncio.run(capture_notice_detail(page, selected(6), interceptor=Interceptor()))
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"
    assert page.actions[-1].startswith('tbody#table-body a[href="/std/noticeDetail?no=TB_L_BOARDITEM7001')


def test_wrong_selected_notice_fails_before_transfer() -> None:
    page = fixture_page()
    wrong = selected(9, title="Different notice")
    with pytest.raises(CampusError, match="Selected notice no longer matches"):
        asyncio.run(capture_notice_detail(page, wrong, interceptor=Interceptor()))
    assert not any(action.startswith("tbody#table-body a[") for action in page.actions)
    assert not page.listeners


def test_mismatched_detail_response_rejected() -> None:
    page = fixture_page(wrong_info=True)
    with pytest.raises(CampusError, match="another board item"):
        asyncio.run(capture_notice_detail(page, selected(), interceptor=Interceptor()))
    assert not page.listeners


def test_malformed_addtime_falls_back_to_insert_dt() -> None:
    page = fixture_page(addtime="bad-time", insert_dt="2026-09-01")
    target = selected(date="2026-09-01")
    snapshot = asyncio.run(capture_notice_detail(page, target, interceptor=Interceptor()))
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"


def test_ordinary_link_named_download_stays_link_label_and_complete(tmp_path: Path) -> None:
    page = fixture_page()
    orig_evaluate = page.evaluate

    async def evaluate_with_download_link(script: str) -> object:
        if "#noticeDetail" in script:
            return {
                "native_id": page.fixture.detail_native,
                "title": "Notice with download link",
                "parts": [
                    {"kind": "text", "text": "Click here to "},
                    {"kind": "link", "text": "download syllabus"},
                    {"kind": "text", "text": "."},
                ],
            }
        return await orig_evaluate(script)

    page.evaluate = evaluate_with_download_link
    target = {**selected(), "has_attachments": False}
    snapshot = asyncio.run(capture_notice_detail(page, target, interceptor=Interceptor()))
    attachment_refs = [p for p in snapshot.parts if isinstance(p, ResourceReference) and p.kind == "attachment"]
    assert len(attachment_refs) == 0
    assert "download syllabus [link URL omitted]" in snapshot.parts

    from campusctl.commands.notices import FETCH_POLICY
    from campusctl.providers.cnu.request_policy import RequestPolicy
    from campusctl.providers.cnu.ui_policy import UiRequestPolicy
    from campusctl.source_package import build_source_package

    ui_policy = UiRequestPolicy.from_reviewed_config(FETCH_POLICY)
    policy = RequestPolicy(ui_policy, "notice")
    pkg = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id=target["entity_id"],
            kind="notice",
            course_id=target["course"]["id"],
            course_label=target["course"]["label"],
            root=tmp_path / "data",
            policy=policy,
        )
    )
    assert pkg["completeness"] == "complete"
    assert len(pkg["omitted_resources"]) == 0


def test_board_attachment_flag_without_verified_control_adds_unknown_control_omission(tmp_path: Path) -> None:
    page = fixture_page()
    target = {**selected(), "has_attachments": True}
    snapshot = asyncio.run(capture_notice_detail(page, target, interceptor=Interceptor()))
    attachment_refs = [p for p in snapshot.parts if isinstance(p, ResourceReference) and p.kind == "attachment"]
    assert len(attachment_refs) == 1
    assert attachment_refs[0].label == "Notice attachment"
    assert attachment_refs[0].official_target is None

    from campusctl.commands.notices import FETCH_POLICY
    from campusctl.providers.cnu.request_policy import RequestPolicy
    from campusctl.providers.cnu.ui_policy import UiRequestPolicy
    from campusctl.source_package import build_source_package

    ui_policy = UiRequestPolicy.from_reviewed_config(FETCH_POLICY)
    policy = RequestPolicy(ui_policy, "notice")
    pkg = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id=target["entity_id"],
            kind="notice",
            course_id=target["course"]["id"],
            course_label=target["course"]["label"],
            root=tmp_path / "data",
            policy=policy,
        )
    )
    assert pkg["completeness"] == "policy-filtered"
    assert len(pkg["omitted_resources"]) == 1
    assert pkg["omitted_resources"][0]["reason"] == "unapproved-file-route"
