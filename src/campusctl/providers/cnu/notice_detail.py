"""Fixture-backed, selected per-course notice detail reader (no mark-read action)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.envelope import CampusError
from campusctl.source_package import DetailSnapshot, ResourceReference

from .course_context import _css_string
from .login import MY_LECTURE_URL
from .notices import _board_item, parse_board_rows

_ORIGIN = "https://dcs-learning.cnu.ac.kr"
_NATIVE_ID = re.compile(r"TB_L_BOARDITEM[0-9]+\Z")
_LIST_PATHS = ("/api/v1/board/notice/list/top", "/api/v1/board/notice/list")
_DETAIL_PATHS = ("/api/v1/board/notice/info", "/api/v1/board/cmt/list")

# The board link and native ID are independently checked against the POST list.
_BOARD_JS = """() => [...document.querySelectorAll('tbody#table-body > tr')].map(row => ({
    links: [...row.querySelectorAll('a[href*="noticeDetail?no="]')].map(a => a.getAttribute('href'))
}))"""

# Fixture selectors identify content, not an official file control. The latter
# remains a separate live-release gate and must not be invented from an image/link.
_DETAIL_JS = r"""() => {
    const article = document.querySelector('#noticeDetail');
    const body = article?.querySelector('#noticeContent');
    if (!article || !body) return null;
    const parts = [];
    const walk = node => {
        if (node.nodeType === Node.TEXT_NODE) {
            if (node.textContent.trim()) parts.push({kind: 'text', text: node.textContent});
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE ||
            ['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME'].includes(node.tagName)) return;
        if (getComputedStyle(node).display === 'none') return;
        if (node.tagName === 'IMG') {
            parts.push({kind: 'image', url: node.src, label: node.alt || 'image', name: node.getAttribute('src')?.split(/[?#]/)[0].split('/').pop() || null});
            return;
        }
        if (node.tagName === 'VIDEO' || node.tagName === 'AUDIO') {
            const src = node.getAttribute('src') || node.querySelector('source')?.getAttribute('src') || '';
            const label = (node.getAttribute('title') || node.getAttribute('aria-label') || node.tagName.toLowerCase()).trim();
            const name = src ? src.split(/[?#]/)[0].split('/').pop() || null : null;
            const fullUrl = src ? new URL(src, document.URL).href : document.URL;
            parts.push({kind: node.tagName.toLowerCase(), url: fullUrl, label: label || node.tagName.toLowerCase(), name});
            return;
        }
        if (node.tagName === 'A') {
            const label = (node.textContent || '').trim();
            const isOfficialControl = node.getAttribute('data-act') === 'downloadFile';
            const fileId = node.getAttribute('data-id') || node.getAttribute('data-file_no') || null;
            if (isOfficialControl && fileId) {
                const name = node.getAttribute('data-name') || label || 'attachment';
                const href = node.getAttribute('href');
                parts.push({
                    kind: 'attachment',
                    file_id: fileId,
                    label: label || 'attachment',
                    name,
                    url: href && /^https?:\/\//i.test(href) ? href : null,
                });
            } else {
                parts.push({kind: 'link', text: label});
            }
            return;
        }
        if (['P', 'DIV', 'LI', 'H1', 'H2', 'H3', 'H4', 'BR'].includes(node.tagName))
            parts.push({kind: 'text', text: '\\n'});
        for (const child of node.childNodes) walk(child);
        if (['P', 'DIV', 'LI', 'H1', 'H2', 'H3', 'H4'].includes(node.tagName))
            parts.push({kind: 'text', text: '\\n'});
    };
    walk(body);
    return {native_id: article.getAttribute('data-boarditem-no'),
        title: article.querySelector('h4')?.textContent?.trim(), parts};
}"""


def _failed(message: str) -> CampusError:
    return CampusError(
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


def _extract_parts(raw: Any, source_url: str) -> tuple[str | ResourceReference, ...]:
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
        elif kind == "attachment":
            file_id = item.get("file_id")
            name = item.get("name")
            label = item.get("label", "attachment")
            parts.append(
                ResourceReference(
                    kind="attachment",
                    source_url=item.get("url") or source_url,
                    original_name=name,
                    media_type_hint=None,
                    label=label,
                    provider_file_id=file_id,
                    official_target=None,
                )
            )
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

    def on_response(response: Any) -> None:
        path = urlsplit(response.request.url).path
        if path in captured:
            captured[path].append(response)

    async def response_payload(path: str) -> Any:
        matches = captured[path]
        if len(matches) != 1 or matches[0].status != 200 or matches[0].request.method != "POST":
            raise _failed(f"{path.rsplit('/', 1)[-1]} response is missing, duplicated, or unsuccessful.")
        result = matches[0]
        await bounded(result.finished(), PROTOCOL_TIMEOUT_SECONDS, "finishing notice response")
        return await bounded(result.json(), PROTOCOL_TIMEOUT_SECONDS, "reading notice response")

    page.on("response", on_response)
    try:
        await bounded(
            page.goto(MY_LECTURE_URL, wait_until="domcontentloaded"),
            PROTOCOL_TIMEOUT_SECONDS,
            "opening notice course roster",
        )
        await bounded(
            page.click(f'[data-act="moveLecture"][data-courseid={_css_string(course_id)}]'),
            PROTOCOL_TIMEOUT_SECONDS,
            "opening selected notice course",
        )
        await bounded(
            page.wait_for_selector('a[href="/std/notice"]'), PROTOCOL_TIMEOUT_SECONDS, "waiting for notice menu"
        )
        await bounded(page.click('a[href="/std/notice"]'), PROTOCOL_TIMEOUT_SECONDS, "opening notice board")
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling notice board")
        rows = [*_items(await response_payload(_LIST_PATHS[0])), *_items(await response_payload(_LIST_PATHS[1]))]
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
        board_url = page.url
        rendered = await bounded(page.evaluate(_BOARD_JS), PROTOCOL_TIMEOUT_SECONDS, "reading board links")
        if not isinstance(rendered, list):
            raise _failed("Selected notice board did not render.")
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
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling notice detail")
        source_url = page.url
        if _native_from_href(source_url, board_url) != native:
            raise _failed("Opened notice does not match the selected board item.")
        payload = await response_payload(_DETAIL_PATHS[0])
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("body"), dict)
            or payload.get("header", {}).get("code") != 200
        ):
            raise _failed("detail response is invalid.")
        body = payload["body"]
        info = body.get("data", body)
        if (
            not isinstance(info, dict)
            or info.get("boarditem_no") != native
            or ("course_id" in info and info["course_id"] != course_id)
        ):
            raise _failed("Opened notice response belongs to another board item.")
        comments = await response_payload(_DETAIL_PATHS[1])
        if (
            not isinstance(comments, dict)
            or not isinstance(comments.get("header"), dict)
            or comments["header"].get("code") != 200
        ):
            raise _failed("comments response is invalid.")
        detail = await bounded(page.evaluate(_DETAIL_JS), PROTOCOL_TIMEOUT_SECONDS, "reading notice detail")
        if (
            not isinstance(detail, dict)
            or detail.get("native_id") != native
            or not isinstance(detail.get("title"), str)
            or not detail["title"].strip()
        ):
            raise _failed("rendered detail identity cannot be verified.")
        extracted = _extract_parts(detail.get("parts"), source_url=source_url)
        has_attachment_ref = any(isinstance(p, ResourceReference) and p.kind == "attachment" for p in extracted)
        notice_has_attachments = bool(matched_item.get("has_attachments") or selected_row.get("has_attachments"))
        if notice_has_attachments and not has_attachment_ref:
            extracted = (
                *extracted,
                "\n\n",
                ResourceReference(
                    kind="attachment",
                    source_url=source_url,
                    original_name=None,
                    media_type_hint=None,
                    label="Notice attachment",
                    provider_file_id=None,
                    official_target=None,
                ),
            )
        parts = ("# " + _escape_markdown(detail["title"]) + "\n\n", *extracted)
        return DetailSnapshot(source_url=source_url, provider_native_id=None, parts=parts)
    finally:
        page.remove_listener("response", on_response)
