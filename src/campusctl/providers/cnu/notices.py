"""Read-only per-course notice boards with global to-do read-state enrichment."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, open_session, profile_count, profile_span
from campusctl.domain_catalog import (
    domain_catalog_path,
    mark_enrollment_unknown,
    merge_domain_catalog,
    read_domain_catalog,
    write_domain_catalog,
)
from campusctl.envelope import CampusError
from campusctl.identity import notice_entity_id

from .course_context import SECTION_RESPONSE_TIMEOUT_MS, _css_string
from .courses import COURSE_LINK_SELECTOR, EXTRACT_COURSES_JS, parse_courses
from .login import MY_LECTURE_URL, ensure_logged_in
from .ui_policy import UiRequestDiagnostics, UiRequestPolicy, install_ui_request_interceptor

_ORIGIN = "https://dcs-learning.cnu.ac.kr"
_TODO_URL = f"{_ORIGIN}/std/todo"
_NOTICE_LIST_PATH = "/api/v1/board/std/notice/list"
# TB_L_BOARDITEM followed by digits is an observed native post ID, not a legacy identity.
_NATIVE_POST_ID = re.compile(r"TB_L_BOARDITEM[0-9]+\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?\Z")
_NUMBER = re.compile(r"[0-9]+\Z")
_ROLE = frozenset({"교수", "교수자", "조교", "관리자", "Instructor", "Teaching Assistant", "Administrator"})
_EXTRACT_GRID_JS = """() => {
    const grid = document.querySelector('#noticeList');
    if (!grid) return {rendered: false, empty: false, rows: [], next: null};
    const rows = [...grid.querySelectorAll('.tabulator-row')].map(row => {
        const cell = field => row.querySelector(`.tabulator-cell[tabulator-field="${field}"]`)?.innerText;
        const link = row.querySelector('[data-boarditem_no], a[href*="noticeDetail?no="]');
        return {number: cell('no'), course_label: cell('course_nm'), title: cell('title'),
            date: cell('date'), read_yn: cell('read_yn'), text: row.innerText,
            native_id: link?.getAttribute('data-boarditem_no') ||
                new URL(link?.getAttribute('href') || '', location.href).searchParams.get('no')};
    });
    const empty = grid.querySelector('#noticeNoData');
    const next = grid.querySelector('.tabulator-page[data-page="next"]');
    const more = grid.querySelector('[data-act="loadMore"], .load-more');
    const active = element => element && !element.disabled &&
        !element.classList.contains('disabled') && getComputedStyle(element).display !== 'none';
    return {rendered: !!grid.querySelector('.tabulator') || rows.length > 0,
        empty: !!empty && getComputedStyle(empty).display !== 'none', rows,
        next: active(next) ? '.tabulator-page[data-page="next"]' :
            active(more) ? (more.matches('[data-act="loadMore"]') ? '[data-act="loadMore"]' : '.load-more') : null};
}"""

_EXTRACT_BOARD_JS = """() => {
    const table = document.querySelector('table.table.mb-0:has(tbody#table-body)');
    if (!table) return null;
    const pagination = table.closest('.card, .container, main')?.querySelector('.pagination') ||
        document.querySelector('tbody#table-body')?.closest('table')?.parentElement?.querySelector('.pagination');
    const pages = [...(pagination?.querySelectorAll('[data-page]') || [])]
        .map(node => Number(node.getAttribute('data-page'))).filter(Number.isInteger);
    const next = pagination?.querySelector('[aria-label="Next"]');
    const nextEnabled = !!next && !next.closest('.page-item')?.classList.contains('disabled') &&
        !next.hasAttribute('disabled');
    const allRows = [...table.querySelectorAll('tbody#table-body > tr')];
    const emptyRow = allRows.length === 1 && !!allRows[0].querySelector('td[colspan]');
    const rows = emptyRow ? [] : allRows;
    const rowIds = rows.map(row => {
        const ids = [...row.querySelectorAll('a[href]')].map(link => {
            const url = new URL(link.getAttribute('href'), location.href);
            return url.origin === location.origin ? url.searchParams.get('no') : null;
        }).filter(Boolean);
        const distinct = [...new Set(ids)];
        return distinct.length === 1 ? distinct[0] : null;
    });
    return {row_count: rows.length, row_ids: rowIds,
        page_size: Number(table.dataset.pageSize), pages, next_enabled: nextEnabled};
}"""


def parse_board_rows(
    raw_rows: list[dict[str, Any]], course: dict[str, Any], todo: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Use a native ID or a unique course/title/day match to retain legacy identity."""
    matching = [item for item in todo if item["course"]["id"] == course["course_id"]]
    by_native: dict[str, list[dict[str, Any]]] = {}
    without_native: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in matching:
        if item.get("native_id"):
            by_native.setdefault(item["native_id"], []).append(item)
        else:
            without_native.setdefault((item["title"], item["date"][:10]), []).append(item)
    board_keys = Counter(
        (raw.get("title", "").strip().replace("\n", " "), raw.get("date", "")[:10])
        for raw in raw_rows
        if isinstance(raw, dict) and isinstance(raw.get("title"), str) and isinstance(raw.get("date"), str)
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ValueError("Invalid board row")
        native = raw.get("native_id")
        if not isinstance(native, str) or not _NATIVE_POST_ID.fullmatch(native):
            raise ValueError("Missing board item identity")
        matches = by_native.get(native, [])
        if not matches:
            key = (str(raw.get("title") or "").strip().replace("\n", " "), str(raw.get("date") or "")[:10])
            matches = without_native.get(key, [])
            if matches and (len(matches) != 1 or board_keys[key] != 1):
                raise CampusError(
                    "notice-identity-ambiguous",
                    "A notice cannot be matched uniquely to its existing to-do identity.",
                    "Review the board and retry.",
                    "error",
                )
        if len(matches) > 1:
            raise CampusError(
                "notice-identity-ambiguous", "A notice has conflicting to-do identities.", "Review the board.", "error"
            )
        todo_item = matches[0] if matches else None
        number = todo_item["legacy_key"].rsplit("_", 1)[-1] if todo_item else raw.get("number")
        date = todo_item["date"] if todo_item else raw.get("date")
        title = raw.get("title")
        if not all(isinstance(value, str) and value.strip() for value in (number, date, title)):
            raise ValueError("Missing board row fields")
        number, date, title = number.strip(), date.strip(), title.strip().replace("\n", " ")
        if not _NUMBER.fullmatch(number) or not _DATE.fullmatch(date):
            raise ValueError("Invalid board identity")
        entity_id = notice_entity_id(course["course_id"], date, number)
        if entity_id in seen:
            raise ValueError("Duplicate board identity")
        seen.add(entity_id)
        views = raw.get("view_count")
        if views not in (None, "") and (not isinstance(views, str) or not views.replace(",", "").isdigit()):
            raise ValueError("Invalid board view count")
        attachment = raw.get("has_attachments")
        if type(attachment) is not bool:
            attachment = True if raw.get("attachment_marked") is True or raw.get("attachment_text") else None
        result.append(
            {
                "entity_id": entity_id,
                "legacy_key": f"{course['label']}_{date}_{number}",
                "course": {"id": course["course_id"], "label": course["label"]},
                "kind": "notice",
                "title": title,
                "date": date,
                "status": todo_item["status"] if todo_item else None,
                "is_unread": todo_item["is_unread"] if todo_item else None,
                "posted_date": None,
                "author_role": None,
                "author": raw.get("author") or None,
                "view_count": int(views.replace(",", "")) if views else None,
                "has_attachments": attachment,
            }
        )
    return result


def _failure(course: dict[str, Any], reason: str) -> dict[str, str]:
    return {"course_id": course["course_id"], "label": course["label"], "reason": reason}


def _error(reason: str) -> CampusError:
    if reason == "notice-board-paginated":
        return CampusError(reason, "A notice board has more than one page.", "Review the board and retry.", "error")
    if reason == "notice-identity-ambiguous":
        return CampusError(
            reason, "A notice identity cannot be matched uniquely.", "Review the board and retry.", "error"
        )
    if reason == "item-identity-missing":
        return CampusError(
            reason, "A notice could not be identified unambiguously.", "Review the LMS notice board and retry.", "error"
        )
    return CampusError(
        "course-sync-failed", "A course notice list could not be completed.", "Check the LMS and retry.", "error"
    )


def _read_state(value: object) -> tuple[str | None, bool | None]:
    if value == "읽지않음":
        return "읽지않음", True
    if value == "읽음":
        return "읽음", False
    return None, None


def parse_notice_rows(
    raw_rows: list[dict[str, Any]], courses: list[dict[str, Any]], *, selected_course_id: str | None = None
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Map every global row to exactly one enrolled course; reject entire ambiguous courses.

    Return course-indexed rows and failed course IDs. An unassignable row poisons
    the full roster, or just the selected course for a filtered scan.
    """
    labels: dict[str, list[dict[str, Any]]] = {}
    result = {course["course_id"]: [] for course in courses}
    failures: set[str] = set()
    for course in courses:
        labels.setdefault(course["label"], []).append(course)
    for raw in raw_rows:
        if not isinstance(raw, dict):
            failures.update(result if selected_course_id is None else [selected_course_id])
            continue
        if isinstance(raw.get("text"), str) and not all(
            isinstance(raw.get(key), str) and raw[key].strip() for key in ("number", "course_label", "title", "date")
        ):
            labels_sorted = sorted(labels, key=len, reverse=True)
            expression = re.compile(
                rf"(\d+)\s*({'|'.join(re.escape(label) for label in labels_sorted)})\s*([\s\S]+?)\s*"
                r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})\s*(읽지않음|읽음)"
            )
            matches = expression.findall(raw["text"])
            if len(matches) != 1:
                failures.update(result if selected_course_id is None else [selected_course_id])
                continue
            number, label_text, title_text, date_text, state = matches[0]
            raw = {
                **raw,
                "number": number,
                "course_label": label_text.strip(),
                "title": title_text,
                "date": date_text,
                "read_yn": state,
            }
        label = raw.get("course_label")
        label = label.strip() if isinstance(label, str) else None
        matched = labels.get(label, []) if label is not None else []
        if len(matched) != 1:
            failures.update(
                (course["course_id"] for course in matched)
                if matched
                else (result if selected_course_id is None else [selected_course_id])
            )
            continue
        course = matched[0]
        cid = course["course_id"]
        if selected_course_id is not None and cid != selected_course_id:
            continue
        number = raw.get("number")
        date = raw.get("date")
        title = raw.get("title")
        if not all(isinstance(value, str) and value.strip() for value in (number, date, title)):
            failures.add(cid)
            continue
        number, date, title = number.strip(), date.strip(), title.strip().replace("\n", " ")
        if not _NUMBER.fullmatch(number) or not _DATE.fullmatch(date):
            failures.add(cid)
            continue
        try:
            entity_id = notice_entity_id(cid, date, number)
        except ValueError:
            failures.add(cid)
            continue
        status, unread = _read_state(raw.get("read_yn"))
        posted = raw.get("posted_date")
        role = raw.get("author_role")
        attachment = raw.get("has_attachments")
        item = {
            "entity_id": entity_id,
            "legacy_key": f"{label.strip()}_{date}_{number}",
            "course": {"id": cid, "label": label},
            "kind": "notice",
            "title": title,
            "date": date,
            "status": status,
            "is_unread": unread,
            "posted_date": posted.strip() if isinstance(posted, str) and posted.strip() else None,
            "author_role": role if role in _ROLE else None,
            "native_id": raw.get("native_id") if isinstance(raw.get("native_id"), str) else None,
            "has_attachments": attachment if type(attachment) is bool else None,
        }
        result[cid].append(item)
    for cid, rows in result.items():
        counts = Counter(row["entity_id"] for row in rows)
        if any(count != 1 for count in counts.values()):
            failures.add(cid)
    return result, failures


def parse_legacy_notice_text(
    text: str, courses: list[dict[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Port the legacy text capture while matching longest course labels first."""
    labels = sorted({course["label"] for course in courses}, key=len, reverse=True)
    if not labels:
        return {}, set()
    expression = re.compile(
        rf"(\d+)\s*({'|'.join(re.escape(label) for label in labels)})\s*([\s\S]+?)\s*"
        r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})\s*(읽지않음|읽음)"
    )
    raw = [
        {"number": num, "course_label": label.strip(), "title": title, "date": date, "read_yn": status}
        for num, label, title, date, status in expression.findall(text)
    ]
    return parse_notice_rows(raw, courses)


async def _grid_snapshot(page: Any, interceptor: Any) -> list[dict[str, Any]]:
    """Read the guarded, settled notice grid; per-course coverage is a live release gate."""
    sequence = 0
    document_sequence: int | None = None
    committed_sequence: int | None = None
    requests: list[tuple[int, Any]] = []
    responses: list[tuple[Any, Any]] = []
    stale_response = False

    def is_notice(request: Any) -> bool:
        return request.url.split("?", 1)[0] == f"{_ORIGIN}{_NOTICE_LIST_PATH}"

    def on_request(request: Any) -> None:
        nonlocal sequence, document_sequence
        sequence += 1
        if (
            request.url == _TODO_URL
            and request.method == "GET"
            and request.resource_type == "document"
            and request.frame == page.main_frame
        ):
            document_sequence = sequence
        if is_notice(request):
            requests.append((sequence, request))

    def on_frame_navigated(frame: Any) -> None:
        nonlocal committed_sequence
        if frame == page.main_frame and frame.url == _TODO_URL and document_sequence is not None:
            committed_sequence = sequence

    def on_response(response: Any) -> None:
        nonlocal stale_response
        if not is_notice(response.request):
            return
        if not any(request is response.request for _, request in requests):
            stale_response = True
        else:
            responses.append((response.request, response))

    async def settle(start: int, *, required: bool) -> None:
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling page requests")
        interceptor.raise_if_denied()
        window = [request for order, request in requests if order > start]
        if stale_response or len(window) > 1 or (required and len(window) != 1):
            raise ValueError("Notice request was stale, absent or duplicated")
        if not window:
            return  # UI-only pagination; there was no new request in the settle window.
        request = window[0]
        if request.method != "POST" or request.frame != page.main_frame:
            raise ValueError("Notice request did not originate in the main frame")
        headers = await request.all_headers()
        referer = next((value for name, value in headers.items() if name.casefold() == "referer"), "")
        parsed = urlsplit(referer)
        if f"{parsed.scheme}://{parsed.netloc}" != _ORIGIN or parsed.path != "/std/todo":
            raise ValueError("Notice request was not issued from the to-do document")
        matching = [response for source, response in responses if source is request]
        if len(matching) != 1 or matching[0].status != 200:
            raise ValueError("Notice request did not complete successfully")
        response = matching[0]
        await bounded(response.finished(), PROTOCOL_TIMEOUT_SECONDS, "settling the notice list")
        interceptor.raise_if_denied()
        await bounded(response.json(), PROTOCOL_TIMEOUT_SECONDS, "validating the notice list response")
        interceptor.raise_if_denied()

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("framenavigated", on_frame_navigated)
    try:
        async with asyncio.timeout(120):
            await bounded(
                page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling prior page requests"
            )
            interceptor.raise_if_denied()
            try:
                await bounded(
                    page.goto(_TODO_URL, wait_until="domcontentloaded"), PROTOCOL_TIMEOUT_SECONDS, "opening notices"
                )
            finally:
                interceptor.raise_if_denied()
            if document_sequence is None or committed_sequence is None or committed_sequence < document_sequence:
                raise ValueError("To-do document navigation did not commit")
            if any(order <= committed_sequence for order, _ in requests):
                raise ValueError("A notice request started before the to-do document committed")
            window_start = committed_sequence
            rows: list[dict[str, Any]] = []
            seen_pages: set[tuple[str, ...]] = set()
            first_page = True
            while True:
                await settle(window_start, required=first_page)
                first_page = False
                await bounded(
                    page.wait_for_function(
                        """() => {
                            const grid = document.querySelector('#noticeList');
                            if (!grid) return false;
                            const empty = grid.querySelector('#noticeNoData');
                            return !!grid.querySelector('.tabulator-row') ||
                                (!!empty && getComputedStyle(empty).display !== 'none');
                        }"""
                    ),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "waiting for notice rendering",
                )
                interceptor.raise_if_denied()
                snapshot = await bounded(
                    page.evaluate(_EXTRACT_GRID_JS), PROTOCOL_TIMEOUT_SECONDS, "extracting notice grid"
                )
                interceptor.raise_if_denied()
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get("rows"), list):
                    raise ValueError("Notice grid was not rendered")
                page_rows = snapshot["rows"]
                if not page_rows and snapshot.get("empty") is not True:
                    raise ValueError("Notice grid was not completed")
                if any(not isinstance(item, dict) for item in page_rows):
                    raise ValueError("Invalid notice grid row")
                fingerprint = tuple(str(item) for item in page_rows)
                if fingerprint in seen_pages:
                    raise ValueError("Notice pagination did not advance")
                seen_pages.add(fingerprint)
                rows.extend(page_rows)
                next_page = snapshot.get("next")
                if next_page is None:
                    return rows
                if len(seen_pages) >= 100:
                    raise ValueError("Notice pagination exceeded 100 pages")
                if next_page not in {'.tabulator-page[data-page="next"]', '[data-act="loadMore"]', ".load-more"}:
                    raise ValueError("Unknown notice pagination control")
                window_start = sequence
                try:
                    await bounded(page.click(next_page), PROTOCOL_TIMEOUT_SECONDS, "opening next notice page")
                finally:
                    interceptor.raise_if_denied()
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
        page.remove_listener("framenavigated", on_frame_navigated)


def _board_response_items(body: Any, *, ordinary: bool) -> tuple[list[dict[str, Any]], int | None]:
    """Decode the observed HTTP 200 board envelope, without treating missing counts as zero."""
    if not isinstance(body, dict) or not isinstance(body.get("header"), dict) or not isinstance(body.get("body"), dict):
        raise ValueError("Invalid board response envelope")
    if type(body["header"].get("code")) is not int or body["header"]["code"] != 200:
        raise ValueError("Board response failed")
    data = body["body"]
    items = data.get("list")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Board response has no item list")
    total = data.get("total") if ordinary else None
    if ordinary and (type(total) is not int or total < 0):
        raise ValueError("Board list has no verified total")
    return items, total


def _board_item(item: dict[str, Any], course_id: str) -> dict[str, Any] | None:
    """Retain only reviewed metadata; never persist IDs or contact details of the author."""
    if item.get("course_id") != course_id:
        raise ValueError("Board item belongs to another course")
    if item.get("delete_yn") == "Y":
        return None
    if item.get("delete_yn") != "N":
        raise ValueError("Board item deletion state unknown")
    native = item.get("boarditem_no")
    title = item.get("boarditem_title")
    number = item.get("row_idx")
    published = item.get("insert_dt_addtime")
    displayed = item.get("insert_dt")
    date = published if isinstance(published, str) and len(published) > 10 and _DATE.fullmatch(published) else displayed
    views = item.get("boarditem_viewcnt")
    file_flag = item.get("file_yn")
    if (
        not isinstance(native, str)
        or not _NATIVE_POST_ID.fullmatch(native)
        or not isinstance(title, str)
        or type(number) is not int
        or number < 1
        or not isinstance(date, str)
        or not _DATE.fullmatch(date)
        or (views is not None and (type(views) is not int or views < 0))
        or (file_flag is not None and (type(file_flag) is not int or file_flag not in (0, 1)))
    ):
        raise ValueError("Board item metadata invalid")
    return {
        "native_id": native,
        "title": title,
        "number": str(number),
        "date": date,
        "author": item.get("writeruser_name") if isinstance(item.get("writeruser_name"), str) else None,
        "view_count": str(views) if views is not None else None,
        "has_attachments": bool(file_flag) if file_flag is not None else None,
    }


async def _board_snapshot(
    page: Any, interceptor: Any, course: dict[str, Any], courses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Accept only the selected course's complete, single-page, post-commit board."""
    paths = {
        "/api/v1/course/addSessionCourseInfo",
        "/api/v1/board/notice/list/top",
        "/api/v1/board/notice/list",
    }
    sequence = 0
    commit: int | None = None
    notice_document: int | None = None
    requests: list[tuple[int, Any]] = []
    responses: list[Any] = []
    session_seen: asyncio.Future[tuple[Any, int, Any]] = asyncio.get_running_loop().create_future()
    stale = False

    async def capture_session(request: Any, response: Any) -> None:
        nonlocal stale
        if session_seen.done():
            stale = True
            return
        try:
            body = await response.json() if response.status == 200 else None
        except Exception:
            body = None
        session_seen.set_result((request, response.status, body))

    def on_request(request: Any) -> None:
        nonlocal sequence, notice_document
        sequence += 1
        path = urlsplit(request.url).path
        if path == "/std/notice" and request.resource_type == "document" and request.frame is page.main_frame:
            notice_document = sequence
        if path in paths:
            requests.append((sequence, request))

    def on_navigate(frame: Any) -> None:
        nonlocal commit
        if frame is page.main_frame and urlsplit(frame.url).path == "/std/notice" and notice_document is not None:
            commit = sequence

    def on_response(response: Any) -> None:
        nonlocal stale
        path = urlsplit(response.request.url).path
        if path not in paths:
            return
        if not any(request is response.request for _, request in requests):
            stale = True
        responses.append(response)
        if (
            path == "/api/v1/course/addSessionCourseInfo"
            and len([seen for seen in responses if urlsplit(seen.request.url).path == path]) > 1
        ):
            stale = True

    interceptor.capture_response("/api/v1/course/addSessionCourseInfo", capture_session)
    page.on("request", on_request)
    page.on("response", on_response)
    page.on("framenavigated", on_navigate)
    try:
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling previous course")
        interceptor.raise_if_denied()
        await bounded(
            page.goto(MY_LECTURE_URL, wait_until="domcontentloaded"), PROTOCOL_TIMEOUT_SECONDS, "opening course roster"
        )
        interceptor.raise_if_denied()
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling course roster")
        interceptor.raise_if_denied()
        with profile_span("course-selection", domain="notices"):
            await bounded(
                page.click(f'[data-act="moveLecture"][data-courseid={_css_string(course["course_id"])}]'),
                PROTOCOL_TIMEOUT_SECONDS,
                "selecting course",
            )
        profile_count("course_selections")
        interceptor.raise_if_denied()
        session_request, session_status, session_payload = await bounded(
            asyncio.wait_for(session_seen, SECTION_RESPONSE_TIMEOUT_MS / 1000),
            SECTION_RESPONSE_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
            "waiting for course selection response",
        )
        await bounded(
            page.wait_for_selector('a[href="/std/notice"]'), PROTOCOL_TIMEOUT_SECONDS, "waiting for notice menu"
        )
        with profile_span("document-commit", domain="notices"):
            await bounded(page.click('a[href="/std/notice"]'), PROTOCOL_TIMEOUT_SECONDS, "opening course notice board")
        interceptor.raise_if_denied()
        await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling notice board")
        interceptor.raise_if_denied()
        if stale or commit is None or notice_document is None or commit < notice_document:
            raise ValueError("Notice navigation did not commit cleanly")
        selected = [request for _, request in requests if urlsplit(request.url).path in paths]
        session = [request for request in selected if urlsplit(request.url).path.endswith("/addSessionCourseInfo")]
        if len(session) != 1 or any(order >= notice_document for order, request in requests if request is session[0]):
            raise ValueError("Course selection missing or duplicated")
        matching_session = [response for response in responses if response.request is session[0]]
        if (
            len(matching_session) != 1
            or matching_session[0].status != 200
            or session_status != 200
            or session_request is not session[0]
        ):
            raise ValueError("Course selection response missing or duplicated")
        data = session_payload.get("body") if isinstance(session_payload, dict) else None
        if (
            not isinstance(session_payload, dict)
            or not isinstance(session_payload.get("header"), dict)
            or session_payload["header"].get("code") != 200
            or not isinstance(data, dict)
            or data.get("result") != "Y"
            or not isinstance(data.get("data"), dict)
            or data["data"].get("course_id") != course["course_id"]
        ):
            raise ValueError("Course selection belongs to another course")
        board_sequences: list[int] = []
        top_items: list[dict[str, Any]] = []
        list_items: list[dict[str, Any]] = []
        list_total: int | None = None
        for path in ("/api/v1/board/notice/list/top", "/api/v1/board/notice/list"):
            bound = [(order, request) for order, request in requests if urlsplit(request.url).path == path]
            if len(bound) != 1 or bound[0][0] <= commit:
                raise ValueError("Board list request stale, missing or duplicated")
            board_sequences.append(bound[0][0])
            request = bound[0][1]
            if request.method != "POST" or request.frame is not page.main_frame:
                raise ValueError("Board list request not from main frame")
            headers = await request.all_headers()
            referer = next((value for name, value in headers.items() if name.casefold() == "referer"), "")
            parsed = urlsplit(referer)
            if f"{parsed.scheme}://{parsed.netloc}" != _ORIGIN or parsed.path != "/std/notice":
                raise ValueError("Board list Referer does not match its page")
            matching = [response for response in responses if response.request is request]
            if len(matching) != 1 or matching[0].status != 200:
                raise ValueError("Board list response missing or duplicated")
            response = matching[0]
            with profile_span("response-completion", domain="notices"):
                if await bounded(response.finished(), PROTOCOL_TIMEOUT_SECONDS, "finishing board response") is not None:
                    raise ValueError("Board response incomplete")
            response_body = await bounded(response.json(), PROTOCOL_TIMEOUT_SECONDS, "checking board response")
            items, total = _board_response_items(response_body, ordinary=path == "/api/v1/board/notice/list")
            if path == "/api/v1/board/notice/list/top":
                top_items = items
            else:
                list_items, list_total = items, total
            interceptor.raise_if_denied()
        if board_sequences != sorted(board_sequences) or board_sequences[0] == board_sequences[1]:
            raise ValueError("Board responses arrived out of request order")
        context = await bounded(
            page.evaluate("""() => {
                const current = document.querySelector('#topbarCurrentLecture');
                const name = current?.textContent?.replace(/\\s+/g, '').trim();
                const matches = [...document.querySelectorAll('#topbarLectureDropdown a[data-act="changeLecture"][data-courseid]')]
                    .filter(link => link.textContent.replace(/\\s+/g, '').trim() === name);
                return {id: matches.length === 1 ? matches[0].getAttribute('data-courseid') : null,
                    name: current?.textContent?.trim()};
            }"""),
            PROTOCOL_TIMEOUT_SECONDS,
            "checking board course",
        )
        if not isinstance(context, dict):
            raise ValueError("Notice board course identity unavailable")
        page_id = context.get("id")
        if page_id is not None:
            if page_id != course["course_id"]:
                raise ValueError("Notice board belongs to another course")
        elif context.get("name") != course["label"] or sum(item["label"] == course["label"] for item in courses) != 1:
            raise ValueError("Notice board course name is not unique")
        with profile_span("extract", domain="notices"):
            snapshot = await bounded(page.evaluate(_EXTRACT_BOARD_JS), PROTOCOL_TIMEOUT_SECONDS, "reading notice board")
        interceptor.raise_if_denied()
        if not isinstance(snapshot, dict) or type(snapshot.get("row_count")) is not int:
            raise ValueError("Notice board did not render")
        if (
            snapshot.get("page_size") != 10
            or snapshot.get("next_enabled")
            or any(page_number > 1 for page_number in snapshot.get("pages", []))
            or snapshot["row_count"] > 10
            or list_total is None
            or list_total > 10
            or len(list_items) < list_total
        ):
            raise CampusError("notice-board-paginated", "Notice board spans multiple pages.", None, "error")
        if len(list_items) > list_total:
            raise ValueError("Board response item count exceeds total")
        rows_by_id: dict[str, dict[str, Any]] = {}
        response_ids: Counter[str] = Counter()
        for item in (*top_items, *list_items):
            row = _board_item(item, course["course_id"])
            if row is None:
                continue
            native_id = row["native_id"]
            response_ids[native_id] += 1
            previous = rows_by_id.get(native_id)
            if previous is not None and previous != row:
                same_fields = all(previous[key] == value for key, value in row.items() if key != "date")
                previous_date = previous["date"]
                current_date = row["date"]
                if (
                    not same_fields
                    or previous_date[:10] != current_date[:10]
                    or len(previous_date) == len(current_date)
                ):
                    raise ValueError("Board item has conflicting duplicate entries")
                if len(current_date) > len(previous_date):
                    rows_by_id[native_id] = row
            else:
                rows_by_id[native_id] = row
        row_ids = snapshot.get("row_ids")
        if (
            not isinstance(row_ids, list)
            or len(row_ids) != snapshot["row_count"]
            or any(not isinstance(native, str) or not _NATIVE_POST_ID.fullmatch(native) for native in row_ids)
            or Counter(row_ids) != response_ids
        ):
            raise ValueError("Board responses and rendered rows disagree")
        if snapshot["row_count"] == 0 and (list_total != 0 or top_items or list_items):
            raise ValueError("Empty board response is unverified")
        return list(rows_by_id.values())
    finally:
        interceptor.stop_capture()
        if not session_seen.done():
            session_seen.cancel()
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
        page.remove_listener("framenavigated", on_navigate)


async def sync_notices(
    config: dict[str, Any],
    root: Path,
    course_id: str | None = None,
    *,
    headless: bool = False,
    reviewed_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], list[CampusError]]:
    """Authenticate once, guard every later request and commit only complete courses."""
    policy = UiRequestPolicy.from_reviewed_config(reviewed_policy)
    if not policy.approved:
        raise CampusError("policy-blocked", "The reviewed notice policy is unavailable.", None, "error")
    async with open_session(config, data_dir=root, headless=headless, operation="notices.sync") as session:
        page = session.page
        with profile_span("auth", domain="notices"):
            await ensure_logged_in(page, config)
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            page, policy, operation="notices.sync", diagnostics=diagnostics, selected_file=None
        )
        try:
            try:
                with profile_span("roster", domain="notices"):
                    with profile_span("document-commit", domain="notices"):
                        await bounded(
                            page.goto(MY_LECTURE_URL, wait_until="domcontentloaded"),
                            PROTOCOL_TIMEOUT_SECONDS,
                            "opening enrolled courses",
                        )
                    interceptor.raise_if_denied()
                    with profile_span("dom-ready", domain="notices"):
                        await bounded(
                            page.wait_for_selector(COURSE_LINK_SELECTOR),
                            PROTOCOL_TIMEOUT_SECONDS,
                            "waiting for enrolled courses",
                        )
                    interceptor.raise_if_denied()
                    with profile_span("extract", domain="notices"):
                        raw_roster = await bounded(
                            page.evaluate(EXTRACT_COURSES_JS), PROTOCOL_TIMEOUT_SECONDS, "reading enrolled courses"
                        )
                interceptor.raise_if_denied()
                if not isinstance(raw_roster, list) or any(
                    not isinstance(course, dict)
                    or not isinstance(course.get("course_id"), str)
                    or not course["course_id"].strip()
                    or not isinstance(course.get("label"), str)
                    or not course["label"].strip()
                    for course in raw_roster
                ):
                    raise ValueError("Invalid roster")
                courses = parse_courses(raw_roster)
                if len(courses) != len(raw_roster):
                    raise ValueError("Ambiguous roster")
            except Exception:
                interceptor.raise_if_denied()
                if course_id is None:
                    mark_enrollment_unknown("notices", root)
                raise CampusError(
                    "course-discovery-failed", "Enrolled courses could not be discovered.", "Retry the sync.", "error"
                ) from None
            if course_id is not None and not any(course["course_id"] == course_id for course in courses):
                raise CampusError(
                    "course-not-found",
                    "The requested course ID was not found among enrolled courses.",
                    "Check the course ID and retry.",
                    "user-action",
                )
            selected = [course for course in courses if course_id is None or course["course_id"] == course_id]
            try:
                with profile_span("todo", domain="notices"):
                    todo_rows = await _grid_snapshot(page, interceptor)
                    todo_by_course, todo_failures = parse_notice_rows(todo_rows, courses, selected_course_id=course_id)
            except Exception:
                interceptor.raise_if_denied()
                todo_by_course = {}
                todo_failures = {course["course_id"] for course in selected}
            parsed: dict[str, list[dict[str, Any]]] = {}
            failed: list[dict[str, str]] = [
                _failure(course, "course-sync-failed") for course in selected if course["course_id"] in todo_failures
            ]
            for ordinal, course in enumerate(selected, 1):
                if course["course_id"] in todo_failures:
                    continue
                try:
                    with profile_span("extract", domain="notices", course=ordinal):
                        board_rows = await _board_snapshot(page, interceptor, course, courses)
                        parsed[course["course_id"]] = parse_board_rows(
                            board_rows, course, todo_by_course.get(course["course_id"], [])
                        )
                except Exception as exc:
                    interceptor.raise_if_denied()
                    reason = (
                        exc.code
                        if isinstance(exc, CampusError)
                        and exc.code in {"notice-board-paginated", "notice-identity-ambiguous", "item-identity-missing"}
                        else "course-sync-failed"
                    )
                    failed.append(_failure(course, reason))
            interceptor.raise_if_denied()
            successful = {course["course_id"] for course in selected} - {entry["course_id"] for entry in failed}
            incoming = [
                row
                for course in selected
                if course["course_id"] in successful
                for row in parsed.get(course["course_id"], [])
            ]
            path = domain_catalog_path("notices", root)
            try:
                previous = read_domain_catalog("notices", path)
            except CampusError as exc:
                if exc.code != "catalog-missing":
                    raise
                previous = None
            with profile_span("merge", domain="notices"):
                merged = merge_domain_catalog(
                    "notices",
                    previous,
                    courses,
                    incoming,
                    successful_course_ids=successful,
                    failed_courses=failed,
                    selected_course_id=course_id,
                )
            with profile_span("serialize-write", domain="notices"):
                write_domain_catalog("notices", merged, path)
            return {
                "courses": len(successful),
                "notices": len(incoming),
                "failed_courses": merged["failed_courses"],
                "catalog": {"generated_at": merged["generated_at"], "enrollment_state": merged["enrollment_state"]},
            }, [_error(entry["reason"]) for entry in failed]
        finally:
            await interceptor.close()
