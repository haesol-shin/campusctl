from __future__ import annotations

import asyncio
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

import pytest

from campusctl.envelope import CampusError
from campusctl.identity import notice_entity_id
from campusctl.providers.cnu.attachment_transfer import OfficialAttachmentTarget
from campusctl.providers.cnu.notice_detail import capture_notice_detail
from campusctl.source_package import ResourceReference

FIXTURE = Path(__file__).parent / "fixtures/lms_sources/notice_detail_synthetic.html"
ORIGIN = "https://dcs-learning.cnu.ac.kr"
NATIVE = "TB_L_BOARDITEM7001"


class Fixture(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.read_state_claim = False
        self.view_counts: list[int] = []
        self.file_claim = False
        self._views = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "a" and "noticeDetail?no=" in (fields.get("href") or ""):
            self.links.append(fields["href"] or "")
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
        wrong_course: bool = False,
        info_title: str = "Example notice",
        content: str | None = None,
        attachments: list[dict[str, object]] | None = None,
        row_addtime: str = "2026-09-01 09:00",
        row_insert_dt: str = "2026-09-01",
        board_number: int = 7,
        duplicate_title_day: bool = False,
    ) -> None:
        self.fixture = fixture
        self.wrong_info = wrong_info
        self.wrong_course = wrong_course
        self.info_title = info_title
        self.content = (
            content
            if content is not None
            else (
                '<p>First instruction.</p><p><img src="/assets/example-diagram.png" alt="Example diagram"></p>'
                '<p>Read <a href="https://external.example.invalid/info?token=synthetic">example guidance</a>.</p>'
                "<p>Second instruction.</p>"
            )
        )
        self.attachments = attachments if attachments is not None else []
        self.row_addtime = row_addtime
        self.row_insert_dt = row_insert_dt
        self.board_number = board_number
        self.duplicate_title_day = duplicate_title_day
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
                        self._row(
                            8,
                            "TB_L_BOARDITEM7002",
                            title="Example notice" if self.duplicate_title_day else "Another notice",
                        ),
                    ]
                },
            )
        elif selector.startswith("tbody#table-body a["):
            self.url = urljoin(self.url, self.fixture.links[0])
            info = {
                "boarditem_no": "TB_L_BOARDITEM7002" if self.wrong_info else NATIVE,
                "course_id": "another.invalid" if self.wrong_course else "course-example",
                "boarditem_title": self.info_title,
                "boarditem_content": self.content,
                "attach_file_list": self.attachments,
            }
            self._respond("/api/v1/board/notice/info", info)
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
        assert selector == 'a[href="/std/notice"]'
        self.actions.append("wait:notice-menu")

    async def wait_for_load_state(self, state: str) -> None:
        return None

    async def evaluate(self, script: str) -> object:
        assert "table-body > tr" in script, "Unobserved detail DOM must not be read"
        return [{"links": [url]} for url in self.fixture.links]


def fixture_page(
    *,
    wrong_info: bool = False,
    wrong_course: bool = False,
    info_title: str = "Example notice",
    content: str | None = None,
    attachments: list[dict[str, object]] | None = None,
    addtime: str = "2026-09-01 09:00",
    insert_dt: str = "2026-09-01",
    board_number: int = 7,
    duplicate_title_day: bool = False,
) -> FakePage:
    fixture = Fixture()
    fixture.feed(FIXTURE.read_text(encoding="utf-8"))
    assert fixture.view_counts[0] + 1 == fixture.view_counts[-1]
    assert not fixture.read_state_claim and not fixture.file_claim
    return FakePage(
        fixture,
        wrong_info=wrong_info,
        wrong_course=wrong_course,
        info_title=info_title,
        content=content,
        attachments=attachments,
        row_addtime=addtime,
        row_insert_dt=insert_dt,
        board_number=board_number,
        duplicate_title_day=duplicate_title_day,
    )


def selected(
    number: int = 7,
    *,
    title: str = "Example notice",
    date: str = "2026-09-01 09:00",
    native_id: str | None = NATIVE,
) -> dict[str, object]:
    row: dict[str, object] = {
        "entity_id": notice_entity_id("course-example", date, str(number)),
        "legacy_key": f"Example Course_{date}_{number}",
        "title": title,
        "date": date,
        "status": None,
        "is_unread": None,
        "course": {"id": "course-example", "label": "Example Course"},
    }
    if native_id is not None:
        row["native_id"] = native_id
    return row


def test_selected_notice_detail_capture_readonly() -> None:
    page = fixture_page()
    snapshot = asyncio.run(capture_notice_detail(page, selected()))
    assert snapshot.provider_native_id is None  # Live provider-native mapping remains unverified.
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"
    assert snapshot.parts[0] == "# Example notice\n\n"
    assert "First instruction." in snapshot.parts[1]
    images = [part for part in snapshot.parts if isinstance(part, ResourceReference) and part.kind == "image"]
    assert images == [
        ResourceReference(
            "image", ORIGIN + "/assets/example-diagram.png", "example-diagram.png", None, "Example diagram", None, None
        )
    ]
    assert "example guidance [link URL omitted]" in snapshot.parts
    assert any(isinstance(part, str) and "Second instruction." in part for part in snapshot.parts)
    assert page.actions[-1].startswith('tbody#table-body a[href="noticeDetail?no=TB_L_BOARDITEM7001')
    assert page.actions[:4] == [
        "roster",
        '[data-act="moveLecture"][data-courseid="course-example"]',
        "wait:notice-menu",
        'a[href="/std/notice"]',
    ]
    assert not any("read" in action.lower() or "upload" in action.lower() for action in page.actions)
    assert not page.listeners


def test_notice_title_comes_from_verified_info_not_catalog() -> None:
    page = fixture_page(info_title="Updated <notice>")
    snapshot = asyncio.run(capture_notice_detail(page, selected()))
    assert snapshot.parts[0] == "# Updated \\<notice\\>\n\n"


def test_html_content_omits_scripts_external_images_and_media(tmp_path: Path) -> None:
    from campusctl.source_package import build_source_package

    page = fixture_page(
        content=(
            "<div>Read &amp; learn<script>secret script</script>"
            '<a href="javascript:alert(1)">linked guidance</a>'
            '<img src="https://other.invalid/private.png" alt="external image">'
            '<video><source src="/media/clip.mp4"></video></div>'
        )
    )
    snapshot = asyncio.run(capture_notice_detail(page, selected()))
    assert "Read & learn" in snapshot.parts
    assert "linked guidance [link URL omitted]" in snapshot.parts
    assert "secret script" not in str(snapshot.parts)
    resources = [part for part in snapshot.parts if isinstance(part, ResourceReference)]
    assert [part.kind for part in resources] == ["image", "video"]
    assert resources[0].source_url == snapshot.source_url
    assert resources[1].source_url == ORIGIN + "/media/clip.mp4"

    async def no_untrusted_download(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Unverified HTML resources must not be downloaded")

    page.context.request.get = no_untrusted_download
    package = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id=selected()["entity_id"],
            kind="notice",
            course_id="course-example",
            course_label="Example Course",
            root=tmp_path,
        )
    )
    assert package["completeness"] == "partial"
    assert [item["reason"] for item in package["omitted_resources"]] == [
        "unsupported-media-type",
        "unsupported-media-type",
    ]


@pytest.mark.parametrize(
    "href",
    [
        "../noticeDetail?no=TB_L_BOARDITEM7001&curPage=1",
        "https://other.invalid/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1",
        "noticeDetail?no=TB_L_BOARDITEM7001&curPage=1&extra=1",
        "noticeDetail?no=TB_L_BOARDITEM7001&no=TB_L_BOARDITEM7001&curPage=1",
        "noticeDetail?no=TB_L_BOARDITEM7001&curPage=1&curPage=1",
    ],
)
def test_unreviewed_notice_href_rejected_before_click(href: str) -> None:
    page = fixture_page()
    page.fixture.links[0] = href
    with pytest.raises(CampusError, match="link is missing or ambiguous"):
        asyncio.run(capture_notice_detail(page, selected()))
    assert not any(action.startswith("tbody#table-body a[") for action in page.actions)
    assert not page.listeners


def test_todo_number_different_from_board_row_index_opens_selected_notice() -> None:
    page = fixture_page(board_number=1)
    snapshot = asyncio.run(capture_notice_detail(page, selected(6)))
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"
    assert page.actions[-1].startswith('tbody#table-body a[href="noticeDetail?no=TB_L_BOARDITEM7001')


def test_unique_title_day_without_native_identity_preserves_legacy_number() -> None:
    page = fixture_page(board_number=1)
    snapshot = asyncio.run(capture_notice_detail(page, selected(6, native_id=None)))
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"


def test_native_identity_disambiguates_same_title_day() -> None:
    page = fixture_page(duplicate_title_day=True)
    snapshot = asyncio.run(capture_notice_detail(page, selected()))
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"


def test_duplicate_title_day_without_native_identity_rejected_before_click() -> None:
    page = fixture_page(duplicate_title_day=True)
    with pytest.raises(CampusError) as failure:
        asyncio.run(capture_notice_detail(page, selected(6, native_id=None)))
    assert failure.value.code == "entity-unknown"
    assert not any(action.startswith("tbody#table-body a[") for action in page.actions)


def test_stale_native_identity_cannot_select_another_notice() -> None:
    page = fixture_page(board_number=6)
    stale = selected(6, native_id="TB_L_BOARDITEM7999")
    with pytest.raises(CampusError) as failure:
        asyncio.run(capture_notice_detail(page, stale))
    assert failure.value.code == "entity-unknown"
    assert "Notice detail" in failure.value.message
    assert "TB_L_BOARDITEM7999" not in failure.value.message
    assert not any(action.startswith("tbody#table-body a[") for action in page.actions)


def test_wrong_selected_notice_fails_before_transfer() -> None:
    page = fixture_page()
    wrong = selected(9, title="Different notice", native_id=None)
    with pytest.raises(CampusError, match="Selected notice no longer matches"):
        asyncio.run(capture_notice_detail(page, wrong))
    assert not any(action.startswith("tbody#table-body a[") for action in page.actions)
    assert not page.listeners


def test_mismatched_detail_course_rejected() -> None:
    page = fixture_page(wrong_course=True)
    with pytest.raises(CampusError, match="notice info course_id does not match"):
        asyncio.run(capture_notice_detail(page, selected()))
    assert not page.listeners


def test_mismatched_detail_response_rejected() -> None:
    page = fixture_page(wrong_info=True)
    with pytest.raises(CampusError, match="notice info boarditem_no does not match"):
        asyncio.run(capture_notice_detail(page, selected()))
    assert not page.listeners


def test_malformed_addtime_falls_back_to_insert_dt() -> None:
    page = fixture_page(addtime="bad-time", insert_dt="2026-09-01")
    target = selected(date="2026-09-01")
    snapshot = asyncio.run(capture_notice_detail(page, target))
    assert snapshot.source_url == ORIGIN + "/std/noticeDetail?no=TB_L_BOARDITEM7001&curPage=1"


def test_ordinary_link_named_download_stays_link_label_and_complete(tmp_path: Path) -> None:
    page = fixture_page(content='<p>Click here to <a href="https://other.invalid/private">download syllabus</a>.</p>')
    target = {**selected(), "has_attachments": False}
    snapshot = asyncio.run(capture_notice_detail(page, target))
    attachment_refs = [p for p in snapshot.parts if isinstance(p, ResourceReference) and p.kind == "attachment"]
    assert len(attachment_refs) == 0
    assert "download syllabus [link URL omitted]" in snapshot.parts

    from campusctl.source_package import build_source_package

    pkg = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id=target["entity_id"],
            kind="notice",
            course_id=target["course"]["id"],
            course_label=target["course"]["label"],
            root=tmp_path / "data",
        )
    )
    assert pkg["completeness"] == "complete"
    assert len(pkg["omitted_resources"]) == 0


def test_notice_attachment_with_unverified_url_remains_omitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = fixture_page(
        attachments=[
            {"boarditem_attach_file_no": "TB_L_FILE999", "file_name": "worksheet.pdf"},
            {"boarditem_attach_file_no": "TB_L_FILE1000"},
        ]
    )
    target = selected()
    snapshot = asyncio.run(capture_notice_detail(page, target))
    attachment_refs = [p for p in snapshot.parts if isinstance(p, ResourceReference) and p.kind == "attachment"]
    assert [ref.label for ref in attachment_refs] == ["worksheet.pdf", "Notice attachment"]
    assert all(ref.official_target is None for ref in attachment_refs)
    unverified = replace(
        attachment_refs[0],
        source_url="/unverified/file?id=TB_L_FILE999",
        original_name="worksheet.pdf",
        provider_file_id="TB_L_FILE999",
        official_target=OfficialAttachmentTarget(
            "TB_L_FILE999", "notice", NATIVE, 'a[data-act="downloadFile"]', "/unverified/file?id=TB_L_FILE999"
        ),
    )
    snapshot = replace(
        snapshot, parts=tuple(unverified if part is attachment_refs[0] else part for part in snapshot.parts)
    )

    from campusctl.source_package import build_source_package

    async def deny_transfer(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Unverified notice attachment must not be transferred")

    monkeypatch.setattr("campusctl.source_package.fetch_official_attachment", deny_transfer)

    pkg = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id=target["entity_id"],
            kind="notice",
            course_id=target["course"]["id"],
            course_label=target["course"]["label"],
            root=tmp_path / "data",
        )
    )
    assert pkg["completeness"] == "partial"
    assert len(pkg["omitted_resources"]) == 2
    assert pkg["omitted_resources"][0]["reason"] == "unverified-notice-attachment"
    assert pkg["omitted_resources"][0]["source_ref"] == {
        "origin": ORIGIN,
        "page_path": "/std/noticeDetail",
        "provider_native_id": "TB_L_FILE999",
    }
    assert "/unverified/file" not in str(pkg)
