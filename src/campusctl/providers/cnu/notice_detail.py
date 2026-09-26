"""Fixture-backed, selected per-course notice detail reader (no mark-read action)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.envelope import CampusError
from campusctl.identity import notice_entity_id
from campusctl.source_package import DetailSnapshot, ResourceReference

from .course_context import _css_string
from .login import MY_LECTURE_URL

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
_DETAIL_JS = """() => {
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
            parts.push({kind: 'image', url: node.src, label: node.alt || 'image', name: node.getAttribute('src')?.split('/').pop()});
            return;
        }
        if (node.tagName === 'A') {
            parts.push({kind: 'link', text: node.textContent.trim()});
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
    return CampusError("entity-unknown", message, "Sync the notice catalog and retry.", "user-action")


def _items(payload: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("header"), dict)
        or payload["header"].get("code") != 200
    ):
        raise ValueError("Invalid notice response")
    body = payload.get("body")
    if not isinstance(body, dict) or not isinstance(body.get("list"), list):
        raise ValueError("Invalid notice board list")
    items = body["list"]
    if any(not isinstance(item, dict) for item in items):
        raise ValueError("Invalid notice board item")
    return items


def _native_from_href(href: str) -> str | None:
    parsed = urlsplit(href)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.path != "/std/noticeDetail"
        or parsed.netloc not in ("", "dcs-learning.cnu.ac.kr")
        or (parsed.netloc and parsed.scheme != "https")
    ):
        return None
    if set(query) != {"no", "curPage"} or query["curPage"] != ["1"]:
        return None
    native = query["no"]
    return native[0] if len(native) == 1 and _NATIVE_ID.fullmatch(native[0]) else None


def _escape_markdown(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", text)


def _extract_parts(raw: Any) -> tuple[str | ResourceReference, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Notice has no readable detail")
    parts: list[str | ResourceReference] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid detail part")
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
        else:
            raise ValueError("Invalid detail part")
    return tuple(parts)


async def capture_notice_detail(page: Any, selected_row: dict[str, Any], *, interceptor: Any) -> DetailSnapshot:
    """Open exactly the selected course row; caller owns login and operation guard.

    The selected catalog ID never encodes the native board-item ID. A wrong or
    stale composite ID is rejected before visiting any detail (or transferring bytes).
    """
    course = selected_row.get("course")
    if (
        not isinstance(course, dict)
        or not isinstance(course.get("id"), str)
        or not isinstance(selected_row.get("entity_id"), str)
    ):
        raise _failed("Selected notice identity is incomplete.")
    course_id = course["id"]
    captured: dict[str, list[Any]] = {path: [] for path in (*_LIST_PATHS, *_DETAIL_PATHS)}

    def on_response(response: Any) -> None:
        path = urlsplit(response.request.url).path
        if path in captured:
            captured[path].append(response)

    async def response_payload(path: str) -> Any:
        matches = captured[path]
        if len(matches) != 1 or matches[0].status != 200 or matches[0].request.method != "POST":
            raise ValueError("Missing or duplicated notice response")
        result = matches[0]
        await bounded(result.finished(), PROTOCOL_TIMEOUT_SECONDS, "finishing notice response")
        interceptor.raise_if_denied()
        return await bounded(result.json(), PROTOCOL_TIMEOUT_SECONDS, "reading notice response")

    page.on("response", on_response)
    try:
        await bounded(
            page.goto(MY_LECTURE_URL, wait_until="domcontentloaded"), PROTOCOL_TIMEOUT_SECONDS, "opening course roster"
        )
        interceptor.raise_if_denied()
        await bounded(
            page.click(f'[data-act="moveLecture"][data-courseid={_css_string(course_id)}]'),
            PROTOCOL_TIMEOUT_SECONDS,
            "opening selected course",
        )
        interceptor.raise_if_denied()
        await bounded(
            page.wait_for_selector('a[href="/std/notice"]'), PROTOCOL_TIMEOUT_SECONDS, "waiting for notice menu"
        )
        await bounded(page.click('a[href="/std/notice"]'), PROTOCOL_TIMEOUT_SECONDS, "opening notice board")
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling notice board")
        interceptor.raise_if_denied()
        rows = [*_items(await response_payload(_LIST_PATHS[0])), *_items(await response_payload(_LIST_PATHS[1]))]
        selected: list[str] = []
        for item in rows:
            native = item.get("boarditem_no")
            number = item.get("row_idx")
            date = item.get("insert_dt_addtime") or item.get("insert_dt")
            if item.get("course_id") != course_id or not isinstance(native, str) or not _NATIVE_ID.fullmatch(native):
                raise _failed("Notice board identity could not be verified.")
            if type(number) is not int or not isinstance(date, str):
                raise _failed("Notice board row identity could not be verified.")
            if notice_entity_id(course_id, date, str(number)) == selected_row["entity_id"]:
                selected.append(native)
        if len(selected) != 1:
            raise _failed("Selected notice no longer matches one board row.")
        native = selected[0]
        rendered = await bounded(page.evaluate(_BOARD_JS), PROTOCOL_TIMEOUT_SECONDS, "reading board links")
        interceptor.raise_if_denied()
        if not isinstance(rendered, list):
            raise _failed("Selected notice board did not render.")
        links = [
            href
            for row in rendered
            if isinstance(row, dict) and isinstance(row.get("links"), list)
            for href in row["links"]
            if isinstance(href, str) and _native_from_href(href) == native
        ]
        if len(links) != 1:
            raise _failed("Selected notice board link is missing or ambiguous.")
        selector = f"tbody#table-body a[href={_css_string(links[0])}]"
        await bounded(page.click(selector), PROTOCOL_TIMEOUT_SECONDS, "opening selected notice")
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling notice detail")
        interceptor.raise_if_denied()
        source_url = page.url
        if _native_from_href(source_url) != native or urlsplit(source_url).scheme != "https":
            raise _failed("Opened notice does not match the selected board item.")
        payload = await response_payload(_DETAIL_PATHS[0])
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("body"), dict)
            or payload.get("header", {}).get("code") != 200
        ):
            raise ValueError("Notice detail response invalid")
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
            raise ValueError("Notice comments response invalid")
        detail = await bounded(page.evaluate(_DETAIL_JS), PROTOCOL_TIMEOUT_SECONDS, "reading notice detail")
        interceptor.raise_if_denied()
        if (
            not isinstance(detail, dict)
            or detail.get("native_id") != native
            or not isinstance(detail.get("title"), str)
            or not detail["title"].strip()
        ):
            raise _failed("Notice detail identity cannot be verified.")
        parts = ("# " + _escape_markdown(detail["title"]) + "\n\n", *_extract_parts(detail.get("parts")))
        return DetailSnapshot(source_url=source_url, provider_native_id=None, parts=parts)
    finally:
        page.remove_listener("response", on_response)
