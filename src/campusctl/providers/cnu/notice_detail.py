"""Selected per-course notice detail reader (no mark-read action)."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_span
from campusctl.envelope import CampusError
from campusctl.source_package import DetailSnapshot, ResourceReference

from .course_context import COURSE_MENU_TIMEOUT_MS, SECTION_RESPONSE_TIMEOUT_MS, _css_string
from .login import MY_LECTURE_URL
from .notices import _board_item, parse_board_rows
from .readiness import wait_page_ready

_ORIGIN = "https://dcs-learning.cnu.ac.kr"
_NATIVE_ID = re.compile(r"TB_L_BOARDITEM[0-9]+\Z")
_LIST_PATHS = ("/api/v1/board/notice/list/top", "/api/v1/board/notice/list")
_DETAIL_PATHS = ("/api/v1/board/notice/info", "/api/v1/board/cmt/list")

# The board link and native ID are independently checked against the POST list.
_BOARD_JS = """() => [...document.querySelectorAll('tbody#table-body > tr')].map(row => ({
    links: [...row.querySelectorAll('a[href*="noticeDetail?no="]')].map(a => a.getAttribute('href'))
}))"""


class NoticeDetailError(CampusError):
    """A provider-generated notice check with a static, identity-free message."""


def _failed(message: str) -> NoticeDetailError:
    return NoticeDetailError(
        "entity-unknown", f"Notice detail: {message}", "Sync the notice catalog and retry.", "user-action"
    )


def _items(payload: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("header"), dict)
        or payload["header"].get("code") != 200
    ):
        raise _failed("board list response is invalid.")
    body = payload.get("body")
    if not isinstance(body, dict) or not isinstance(body.get("list"), list):
        raise _failed("board list payload is invalid.")
    items = body["list"]
    if any(not isinstance(item, dict) for item in items):
        raise _failed("board list item is invalid.")
    return items


def _native_from_href(href: str, board_url: str) -> str | None:
    parsed = urlsplit(urljoin(board_url, href))
    if f"{parsed.scheme}://{parsed.netloc}" != _ORIGIN or parsed.path != "/std/noticeDetail" or parsed.fragment:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"no", "curPage"} or query["curPage"] != ["1"]:
        return None
    native = query["no"]
    return native[0] if len(native) == 1 and _NATIVE_ID.fullmatch(native[0]) else None


def _escape_markdown(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", text)


class _NoticeHTML(HTMLParser):
    """Read content as data, never as executable page markup."""

    _BLOCK = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4"})
    _SKIP = frozenset({"script", "style", "noscript", "iframe"})
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.parts: list[dict[str, str | None]] = []
        self._skip_stack: list[str] = []
        self._link: list[str] | None = None
        self._media_index: int | None = None

    def _resource_url(self, value: str | None) -> str:
        url = urlsplit(urljoin(self.source_url, value or ""))
        page = urlsplit(self.source_url)
        if url.scheme != page.scheme or url.netloc != page.netloc:
            return self.source_url  # An unverified resource is omitted, not fetched.
        return url.geturl()

    def _break(self) -> None:
        if self.parts and self.parts[-1].get("text") != "\n":
            self.parts.append({"kind": "text", "text": "\n"})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_stack or tag in self._SKIP:
            if tag not in self._VOID:
                self._skip_stack.append(tag)
            return
        fields = dict(attrs)
        if tag == "a":
            self._link = []
        elif tag == "img":
            src = fields.get("src")
            url = self._resource_url(src)
            self.parts.append(
                {
                    "kind": "image",
                    "url": url,
                    "label": fields.get("alt") or "image",
                    "name": urlsplit(url).path.rsplit("/", 1)[-1] or None,
                }
            )
        elif tag in {"video", "audio"}:
            url = self._resource_url(fields.get("src"))
            self._media_index = len(self.parts)
            self.parts.append(
                {
                    "kind": tag,
                    "url": url,
                    "label": fields.get("title") or fields.get("aria-label") or tag,
                    "name": urlsplit(url).path.rsplit("/", 1)[-1] or None,
                }
            )
        elif tag == "source" and self._media_index is not None and fields.get("src"):
            url = self._resource_url(fields["src"])
            self.parts[self._media_index]["url"] = url
            self.parts[self._media_index]["name"] = urlsplit(url).path.rsplit("/", 1)[-1] or None
        elif tag in self._BLOCK or tag == "br":
            self._break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_stack or tag in self._SKIP:
            return
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack:
            if tag in self._skip_stack:
                while self._skip_stack.pop() != tag:
                    pass
            return
        if tag == "a" and self._link is not None:
            self.parts.append({"kind": "link", "text": "".join(self._link).strip()})
            self._link = None
        elif tag in {"video", "audio"}:
            self._media_index = None
        elif tag in self._BLOCK:
            self._break()

    def handle_data(self, data: str) -> None:
        if self._skip_stack or not data.strip():
            return
        if self._link is not None:
            self._link.append(data)
        else:
            self.parts.append({"kind": "text", "text": data})

    def close(self) -> None:
        super().close()
        if self._skip_stack:
            raise _failed("notice info HTML skipped element is unclosed.")


def _extract_parts(raw: list[dict[str, str | None]]) -> tuple[str | ResourceReference, ...]:
    if not isinstance(raw, list) or not raw:
        raise _failed("detail has no readable content.")
    parts: list[str | ResourceReference] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _failed("detail content part is invalid.")
        kind = item.get("kind")
        if kind == "text" and isinstance(item.get("text"), str):
            parts.append(_escape_markdown(item["text"]))
        elif kind == "link" and isinstance(item.get("text"), str):
            parts.append(_escape_markdown(item["text"]) + " [link URL omitted]")
        elif kind == "image" and isinstance(item.get("url"), str) and isinstance(item.get("label"), str):
            parts.append(
                ResourceReference(
                    kind="image",
                    source_url=item["url"],
                    original_name=item.get("name"),
                    media_type_hint=None,
                    label=item["label"],
                    provider_file_id=None,
                    official_target=None,
                )
            )
        elif kind in {"video", "audio"}:
            url, label = item.get("url"), item.get("label")
            if not isinstance(url, str) or not isinstance(label, str):
                raise _failed("detail media part is invalid.")
            parts.append(ResourceReference(kind, url, item.get("name"), f"{kind}/*", label, None, None))
        else:
            raise _failed("detail content part is invalid.")
    return tuple(parts)


async def capture_notice_detail(page: Any, selected_row: dict[str, Any]) -> DetailSnapshot:
    """Open exactly the selected course row through the notice menu; caller owns login.

    The selected catalog ID never encodes the native board-item ID. A wrong or
    stale composite ID is rejected before visiting any detail (or transferring bytes).
    """
    course = selected_row.get("course")
    if (
        not isinstance(course, dict)
        or not isinstance(course.get("id"), str)
        or not course["id"].strip()
        or not isinstance(course.get("label"), str)
        or not course["label"].strip()
        or not isinstance(selected_row.get("entity_id"), str)
        or not selected_row["entity_id"].strip()
        or not isinstance(selected_row.get("legacy_key"), str)
        or not selected_row["legacy_key"].strip()
        or not isinstance(selected_row.get("title"), str)
        or not selected_row["title"].strip()
        or not isinstance(selected_row.get("date"), str)
        or not selected_row["date"].strip()
    ):
        raise _failed("Selected notice identity is incomplete.")
    selected_native = selected_row.get("native_id")
    if selected_native is not None and (
        not isinstance(selected_native, str) or not _NATIVE_ID.fullmatch(selected_native)
    ):
        raise _failed("Selected notice native identity is invalid.")
    course_id = course["id"]
    captured: dict[str, list[Any]] = {path: [] for path in (*_LIST_PATHS, *_DETAIL_PATHS)}
    requests: dict[str, list[tuple[int, Any]]] = {path: [] for path in captured}
    commits: dict[str, int] = {}
    documents: dict[str, int] = {}
    sequence = 0
    changed = asyncio.Event()
    frame = page.main_frame

    def on_request(request: Any) -> None:
        nonlocal sequence
        sequence += 1
        path = urlsplit(request.url).path
        if (
            path in {"/std/notice", "/std/noticeDetail"}
            and request.resource_type == "document"
            and request.frame is frame
        ):
            documents[path] = sequence
        if path in requests:
            requests[path].append((sequence, request))
            changed.set()

    def on_navigate(committed_frame: Any) -> None:
        if committed_frame is frame:
            path = urlsplit(frame.url).path
            if path in documents:
                commits[path] = sequence

    def on_response(response: Any) -> None:
        path = urlsplit(response.request.url).path
        if path in captured:
            captured[path].append(response)
            changed.set()

    async def response_payload(path: str, route: str) -> Any:
        async def received() -> Any:
            while True:
                matches = captured[path]
                if len(matches) > 1 or len(requests[path]) > 1:
                    raise _failed("Notice response is duplicated.")
                if matches:
                    return matches[0]
                changed.clear()
                await changed.wait()

        with profile_span("response-completion", wait_kind="response", domain="notices"):
            result = await bounded(received(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "waiting for notice response")
            bound = requests[path]
            if (
                len(bound) != 1
                or bound[0][1] is not result.request
                or route not in commits
                or route not in documents
                or bound[0][0] <= commits[route]
                or commits[route] < documents[route]
                or result.status != 200
                or result.request.method.upper() != "POST"
            ):
                raise _failed("Notice response is missing, stale, or unsuccessful.")
            if (
                await bounded(result.finished(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "finishing notice response")
                is not None
            ):
                raise _failed("Notice response did not complete.")
        payload = await bounded(result.json(), PROTOCOL_TIMEOUT_SECONDS, "reading notice response")
        if len(captured[path]) != 1 or len(requests[path]) != 1:
            raise _failed("Notice response changed during validation.")
        return payload

    page.on("request", on_request)
    page.on("framenavigated", on_navigate)
    page.on("response", on_response)
    try:
        await bounded(
            page.goto(MY_LECTURE_URL, wait_until="commit"),
            PROTOCOL_TIMEOUT_SECONDS,
            "opening notice course roster",
        )
        try:
            await wait_page_ready(page, "roster", domain="notices")
            await bounded(
                page.click(f'[data-act="moveLecture"][data-courseid={_css_string(course_id)}]'),
                PROTOCOL_TIMEOUT_SECONDS,
                "opening selected notice course",
            )
            await wait_page_ready(page, "course-entry", expected_course_id=course_id, domain="notices")
        except ValueError:
            raise _failed("Selected notice course identity changed.") from None
        await bounded(
            page.wait_for_selector('a[href="/std/notice"]', state="attached"),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for notice menu",
        )
        await bounded(page.click('a[href="/std/notice"]'), PROTOCOL_TIMEOUT_SECONDS, "opening notice board")
        rows = [
            *_items(await response_payload(_LIST_PATHS[0], "/std/notice")),
            *_items(await response_payload(_LIST_PATHS[1], "/std/notice")),
        ]
        board: list[dict[str, Any]] = []
        for item in rows:
            try:
                parsed = _board_item(item, course_id)
            except ValueError as exc:
                raise _failed("Notice board item metadata invalid.") from exc
            if parsed is not None:
                board.append(parsed)
        candidates = [
            raw
            for raw in board
            if raw["title"].strip().replace("\n", " ") == selected_row["title"]
            and raw["date"][:10] == selected_row["date"][:10]
            and (selected_native is None or raw["native_id"] == selected_native)
        ]
        if len(candidates) != 1:
            raise _failed("Selected notice no longer matches one board row.")
        try:
            resolved = parse_board_rows(board, {"course_id": course_id, "label": course["label"]}, [selected_row])
        except ValueError as exc:
            raise _failed("Notice board identity is invalid.") from exc
        selected = [
            raw
            for raw, bound in zip(board, resolved, strict=True)
            if raw["native_id"] == candidates[0]["native_id"] and bound["entity_id"] == selected_row["entity_id"]
        ]
        if len(selected) != 1:
            raise _failed("Selected notice no longer matches one board row.")
        matched_item = selected[0]
        native = matched_item["native_id"]
        try:
            await wait_page_ready(page, "notices", expected_course_id=course_id, domain="notices")
        except ValueError:
            raise _failed("Selected notice board identity changed.") from None
        board_url = page.url
        expected = Counter(item["native_id"] for item in board)

        async def matching_board() -> list[dict[str, Any]]:
            while True:
                rendered = await page.evaluate(_BOARD_JS)
                if isinstance(rendered, list):
                    ids = [
                        _native_from_href(href, board_url)
                        for row in rendered
                        if isinstance(row, dict) and isinstance(row.get("links"), list)
                        for href in row["links"]
                        if isinstance(href, str)
                    ]
                    if None not in ids and Counter(ids) == expected:
                        return rendered
                await asyncio.sleep(0.05)

        with profile_span("page-readiness", wait_kind="readiness", domain="notices", page_kind="notices"):
            rendered = await bounded(matching_board(), COURSE_MENU_TIMEOUT_MS / 1000, "waiting for notice board rows")
        links = [
            href
            for row in rendered
            if isinstance(row, dict) and isinstance(row.get("links"), list)
            for href in row["links"]
            if isinstance(href, str) and _native_from_href(href, board_url) == native
        ]
        if len(links) != 1:
            raise _failed("Selected notice board link is missing or ambiguous.")
        selector = f"tbody#table-body a[href={_css_string(links[0])}]"
        await bounded(page.click(selector), PROTOCOL_TIMEOUT_SECONDS, "opening selected notice")
        with profile_span("page-readiness", wait_kind="readiness", domain="notices", page_kind="notice-detail"):
            source_url = page.url
            if _native_from_href(source_url, board_url) != native:
                raise _failed("opened detail URL native ID does not match the selected board item.")
            payload = await response_payload(_DETAIL_PATHS[0], "/std/noticeDetail")
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("body"), dict)
                or payload.get("header", {}).get("code") != 200
            ):
                raise _failed("detail response is invalid.")
            body = payload["body"]
            if body.get("boarditem_no") != native:
                raise _failed("notice info boarditem_no does not match the selected board item.")
            if body.get("course_id") != course_id:
                raise _failed("notice info course_id does not match the selected course.")
            comments = await response_payload(_DETAIL_PATHS[1], "/std/noticeDetail")
            if (
                not isinstance(comments, dict)
                or not isinstance(comments.get("header"), dict)
                or comments["header"].get("code") != 200
            ):
                raise _failed("comments response is invalid.")
        title = body.get("boarditem_title")
        content = body.get("boarditem_content")
        attachments = body.get("attach_file_list")
        if not isinstance(title, str) or not title.strip():
            raise _failed("notice info boarditem_title is missing.")
        if not isinstance(content, str):
            raise _failed("notice info boarditem_content is invalid.")
        if not isinstance(attachments, list):
            raise _failed("notice info attach_file_list is invalid.")
        html = _NoticeHTML(source_url)
        html.feed(content)
        html.close()
        if not content.strip() and not attachments:
            raise _failed("notice info has no readable content.")
        extracted = _extract_parts(html.parts) if html.parts else ()
        attachment_parts: list[ResourceReference] = []
        for entry in attachments:
            if not isinstance(entry, dict):
                raise _failed("notice info attachment entry is invalid.")
            name = next(
                (
                    value
                    for key in ("file_name", "pdf_attach_file_nm")
                    if isinstance(value := entry.get(key), str) and value.strip()
                ),
                None,
            )
            file_id = entry.get("boarditem_attach_file_no")
            attachment_parts.append(
                ResourceReference(
                    kind="attachment",
                    source_url=source_url,
                    original_name=name,
                    media_type_hint=None,
                    label=name or "Notice attachment",
                    provider_file_id=file_id if isinstance(file_id, str) and file_id else None,
                    official_target=None,
                )
            )
        if not attachment_parts and (matched_item.get("has_attachments") or selected_row.get("has_attachments")):
            attachment_parts.append(
                ResourceReference("attachment", source_url, None, None, "Notice attachment", None, None)
            )
        parts = ("# " + _escape_markdown(title) + "\n\n", *extracted, *attachment_parts)
        return DetailSnapshot(source_url=source_url, provider_native_id=None, parts=parts)
    finally:
        page.remove_listener("response", on_response)
        page.remove_listener("request", on_request)
        page.remove_listener("framenavigated", on_navigate)
