"""Read-only per-course notice boards with global to-do read-state enrichment."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_span
from campusctl.envelope import CampusError
from campusctl.identity import notice_entity_id

from .course_context import _TOPBAR_COURSE_JS, COURSE_MENU_TIMEOUT_MS, SECTION_RESPONSE_TIMEOUT_MS
from .readiness import _route_matches, wait_page_ready

_ORIGIN = "https://dcs-learning.cnu.ac.kr"
_TODO_URL = f"{_ORIGIN}/std/todo"
_NOTICE_LIST_PATH = "/api/v1/board/std/notice/list"
_GRID_QUIET_SECONDS = 0.5
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
                "native_id": native,
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


class _TodoCapture:
    """Observe the todo document commit and all subsequent notice-list responses."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self.sequence = 0
        self.document_sequence: int | None = None
        self.committed_sequence: int | None = None
        self.requests: list[tuple[int, Any]] = []
        self.responses: list[tuple[Any, Any]] = []
        self.stale_response = False
        self.changed = asyncio.Event()
        self.closed = False
        page.on("request", self.on_request)
        page.on("response", self.on_response)
        page.on("framenavigated", self.on_navigate)

    def is_notice(self, request: Any) -> bool:
        return request.url.split("?", 1)[0] == f"{_ORIGIN}{_NOTICE_LIST_PATH}"

    def on_request(self, request: Any) -> None:
        self.sequence += 1
        if (
            request.url == _TODO_URL
            and request.method == "GET"
            and request.resource_type == "document"
            and request.frame == self.page.main_frame
        ):
            self.document_sequence = self.sequence
        if self.is_notice(request):
            self.requests.append((self.sequence, request))
            self.changed.set()

    def on_navigate(self, frame: Any) -> None:
        if frame == self.page.main_frame and frame.url == _TODO_URL and self.document_sequence is not None:
            self.committed_sequence = self.sequence

    def on_response(self, response: Any) -> None:
        if not self.is_notice(response.request):
            return
        if not any(request is response.request for _, request in self.requests):
            self.stale_response = True
        else:
            self.responses.append((response.request, response))
            self.changed.set()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.page.remove_listener("request", self.on_request)
            self.page.remove_listener("response", self.on_response)
            self.page.remove_listener("framenavigated", self.on_navigate)


async def open_notice_todo(page: Any) -> _TodoCapture:
    """Navigate to the to-do document and capture its first list XHR."""
    capture = _TodoCapture(page)

    async def navigate() -> None:
        with profile_span("document-commit", wait_kind="navigation", domain="notices", page_kind="todo"):
            await bounded(page.goto(_TODO_URL, wait_until="commit"), PROTOCOL_TIMEOUT_SECONDS, "opening notices")

    try:
        await navigate()
        if (
            capture.document_sequence is None
            or capture.committed_sequence is None
            or capture.committed_sequence < capture.document_sequence
            or any(order <= capture.committed_sequence for order, _ in capture.requests)
        ):
            raise ValueError("To-do document navigation did not commit cleanly")
        return capture
    except BaseException:
        capture.close()
        raise


def _todo_response_coverage(payload: Any) -> tuple[int, int] | None:
    """Count server-delivered items without equating them to rendered grid rows."""
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), dict):
        return None
    body = payload["body"]
    items, total = body.get("list"), body.get("tot_cnt")
    if not isinstance(items, list) or type(total) is not int or total < len(items):
        return None
    return len(items), total


async def _grid_snapshot(page: Any, capture: _TodoCapture) -> list[dict[str, Any]]:
    """Read each committed grid page after its own response and render gate."""

    async def completed(start: int, *, required: bool) -> tuple[bool, tuple[int, int] | None]:
        async def response_for(request: Any) -> Any:
            while True:
                matching = [response for source, response in capture.responses if source is request]
                if len(matching) > 1 or capture.stale_response:
                    raise ValueError("Notice response was stale or duplicated")
                if matching:
                    return matching[0]
                capture.changed.clear()
                await capture.changed.wait()

        if required and not any(order > start for order, _ in capture.requests):
            with profile_span("response-completion", wait_kind="response", domain="notices", page_kind="todo"):
                await bounded(capture.changed.wait(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "waiting for the notice list")
        window = [request for order, request in capture.requests if order > start]
        if capture.stale_response or len(window) > 1 or (required and len(window) != 1):
            raise ValueError("Notice request was stale, absent or duplicated")
        if not window:
            return False, None  # UI-only pagination is allowed only after DOM advancement.
        request = window[0]
        if request.method != "POST" or request.frame != page.main_frame:
            raise ValueError("Notice request did not originate in the main frame")
        headers = await bounded(request.all_headers(), PROTOCOL_TIMEOUT_SECONDS, "checking notice request context")
        referer = next((value for name, value in headers.items() if name.casefold() == "referer"), "")
        parsed = urlsplit(referer)
        if f"{parsed.scheme}://{parsed.netloc}" != _ORIGIN or parsed.path != "/std/todo":
            raise ValueError("Notice request was not issued from the to-do document")
        with profile_span("response-completion", wait_kind="response", domain="notices", page_kind="todo"):
            response = await bounded(
                response_for(request), SECTION_RESPONSE_TIMEOUT_MS / 1000, "waiting for the notice list"
            )
            if (
                response.status != 200
                or await bounded(response.finished(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "finishing the notice list")
                is not None
            ):
                raise ValueError("Notice request did not complete successfully")
        payload = await bounded(response.json(), PROTOCOL_TIMEOUT_SECONDS, "validating the notice list response")
        if capture.stale_response or len([order for order, _ in capture.requests if order > start]) != 1:
            raise ValueError("Notice list changed during completion")
        return True, _todo_response_coverage(payload)

    async def advanced(previous: tuple[str, ...]) -> None:
        async def changed() -> None:
            while True:
                snapshot = await page.evaluate(_EXTRACT_GRID_JS)
                if isinstance(snapshot, dict) and isinstance(snapshot.get("rows"), list):
                    fingerprint = tuple(str(item) for item in snapshot["rows"])
                    if fingerprint != previous and (fingerprint or snapshot.get("empty") is True):
                        return
                await asyncio.sleep(0.05)

        with profile_span("page-readiness", wait_kind="readiness", domain="notices", page_kind="todo"):
            await bounded(changed(), COURSE_MENU_TIMEOUT_MS / 1000, "waiting for the next notice page")

    async def stable_grid(*, require_next: bool) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        signature: tuple[Any, ...] | None = None
        unchanged_since = 0.0
        while True:
            snapshot = await page.evaluate(_EXTRACT_GRID_JS)
            if isinstance(snapshot, dict) and isinstance(snapshot.get("rows"), list):
                page_rows = snapshot["rows"]
                if page_rows or snapshot.get("empty") is True:
                    current = (
                        len(page_rows),
                        tuple(str(item) for item in page_rows),
                        snapshot.get("next"),
                        snapshot.get("empty") is True,
                    )
                    now = loop.time()
                    if current != signature:
                        signature, unchanged_since = current, now
                    elif now - unchanged_since >= _GRID_QUIET_SECONDS and (
                        not require_next or snapshot.get("next") is not None
                    ):
                        return snapshot
                else:
                    signature = None
            else:
                signature = None
            await asyncio.sleep(0.05)

    try:
        async with asyncio.timeout(120):
            if capture.committed_sequence is None:
                raise ValueError("To-do document navigation did not commit")
            window_start = capture.committed_sequence
            rows: list[dict[str, Any]] = []
            seen_pages: set[tuple[str, ...]] = set()
            previous: tuple[str, ...] | None = None
            response_items = 0
            response_total: int | None = None
            coverage_available = True
            while True:
                if previous is not None:
                    await advanced(previous)
                has_response, coverage = await completed(window_start, required=previous is None)
                if has_response and coverage_available:
                    if coverage is None:
                        # One missing total makes cumulative response coverage unknowable.
                        coverage_available = False
                        response_total = None
                    else:
                        count, response_total = coverage
                        response_items += count
                loop = asyncio.get_running_loop()
                deadline = loop.time() + COURSE_MENU_TIMEOUT_MS / 1000
                await wait_page_ready(page, "todo", domain="notices")
                with profile_span("page-readiness", wait_kind="readiness", domain="notices", page_kind="todo"):
                    snapshot = await bounded(
                        stable_grid(require_next=response_total is not None and response_items < response_total),
                        max(0, deadline - loop.time()),
                        "waiting for stable notice grid",
                    )
                if page.main_frame.url != _TODO_URL:
                    raise ValueError("To-do document changed during extraction")
                page_rows = snapshot["rows"]
                if not page_rows and snapshot.get("empty") is not True:
                    raise ValueError("Notice grid was not completed")
                if any(not isinstance(item, dict) for item in page_rows):
                    raise ValueError("Invalid notice grid row")
                fingerprint = tuple(str(item) for item in page_rows)
                if fingerprint in seen_pages or capture.stale_response:
                    raise ValueError("Notice pagination did not advance cleanly")
                if len([order for order, _ in capture.requests if order > window_start]) > 1:
                    raise ValueError("Notice pagination responses changed during extraction")
                seen_pages.add(fingerprint)
                rows.extend(page_rows)
                next_page = snapshot.get("next")
                if next_page is None:
                    return rows
                if len(seen_pages) >= 100:
                    raise ValueError("Notice pagination exceeded 100 pages")
                if next_page not in {'.tabulator-page[data-page="next"]', '[data-act="loadMore"]', ".load-more"}:
                    raise ValueError("Unknown notice pagination control")
                previous = fingerprint
                window_start = capture.sequence
                with profile_span("wait", wait_kind="action", domain="notices", page_kind="todo"):
                    await bounded(page.click(next_page), PROTOCOL_TIMEOUT_SECONDS, "opening next notice page")
    finally:
        capture.close()


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


class _NoticeCapture:
    """Keep the board's request identities and document boundary from before navigation."""

    def __init__(self, page: Any, *, armed: bool = True) -> None:
        self.page = page
        self.requests: list[tuple[int, Any]] = []
        self.responses: list[Any] = []
        self.sequence = 0
        self.notice_document: int | None = None
        self.commit: int | None = None
        self.stale = False
        self.changed = asyncio.Event()
        self.armed = armed
        if armed:
            page.on("request", self.on_request)
            page.on("response", self.on_response)
            page.on("framenavigated", self.on_navigate)

    def on_request(self, request: Any) -> None:
        self.sequence += 1
        path = urlsplit(request.url).path
        if path == "/std/notice" and request.resource_type == "document" and request.frame is self.page.main_frame:
            self.notice_document = self.sequence
        if path == "/api/v1/course/addSessionCourseInfo":
            self.stale = True
        if path in {"/api/v1/board/notice/list/top", "/api/v1/board/notice/list"}:
            self.requests.append((self.sequence, request))
            self.changed.set()

    def on_navigate(self, frame: Any) -> None:
        if (
            frame is self.page.main_frame
            and urlsplit(frame.url).path == "/std/notice"
            and self.notice_document is not None
        ):
            self.commit = self.sequence

    def on_response(self, response: Any) -> None:
        path = urlsplit(response.request.url).path
        if path == "/api/v1/course/addSessionCourseInfo":
            self.stale = True
            return
        if path not in {"/api/v1/board/notice/list/top", "/api/v1/board/notice/list"}:
            return
        if not any(request is response.request for _, request in self.requests):
            self.stale = True
        self.responses.append(response)
        self.changed.set()

    def close(self) -> None:
        if self.armed:
            self.armed = False
            self.page.remove_listener("request", self.on_request)
            self.page.remove_listener("response", self.on_response)
            self.page.remove_listener("framenavigated", self.on_navigate)

    async def collect(self, page: Any, course: dict[str, Any], courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async def responses_ready() -> None:
            while True:
                if self.stale:
                    raise ValueError("Notice board response was stale")
                if all(
                    any(urlsplit(response.request.url).path == path for response in self.responses)
                    for path in ("/api/v1/board/notice/list/top", "/api/v1/board/notice/list")
                ):
                    return
                self.changed.clear()
                await self.changed.wait()

        with profile_span("response-completion", wait_kind="response", domain="notices"):
            await bounded(responses_ready(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "waiting for notice board responses")
        requests, responses, commit, notice_document = (
            self.requests,
            self.responses,
            self.commit,
            self.notice_document,
        )
        if self.stale or commit is None or notice_document is None or commit < notice_document:
            raise ValueError("Notice navigation did not commit cleanly")
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
            with profile_span("response-completion", wait_kind="response", domain="notices"):
                if (
                    await bounded(response.finished(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "finishing board response")
                    is not None
                ):
                    raise ValueError("Board response incomplete")
            response_body = await bounded(response.json(), PROTOCOL_TIMEOUT_SECONDS, "checking board response")
            items, total = _board_response_items(response_body, ordinary=path == "/api/v1/board/notice/list")
            if path == "/api/v1/board/notice/list/top":
                top_items = items
            else:
                list_items, list_total = items, total
        if board_sequences != sorted(board_sequences) or board_sequences[0] == board_sequences[1]:
            raise ValueError("Board responses arrived out of request order")
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
        await wait_page_ready(page, "notices", expected_course_id=course["course_id"], domain="notices")

        async def matching_rows() -> dict[str, Any]:
            while True:
                snapshot = await page.evaluate(_EXTRACT_BOARD_JS)
                if (
                    isinstance(snapshot, dict)
                    and type(snapshot.get("row_count")) is int
                    and isinstance(snapshot.get("row_ids"), list)
                    and len(snapshot["row_ids"]) == snapshot["row_count"]
                    and Counter(snapshot["row_ids"]) == response_ids
                ):
                    return snapshot
                await asyncio.sleep(0.05)

        with profile_span("page-readiness", wait_kind="readiness", domain="notices", page_kind="notices"):
            snapshot = await bounded(matching_rows(), COURSE_MENU_TIMEOUT_MS / 1000, "waiting for notice board rows")
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
        if (
            not _route_matches(page.main_frame.url, "/std/notice")
            or await page.evaluate(_TOPBAR_COURSE_JS) != course["course_id"]
        ):
            raise ValueError("Board page changed during extraction")
        if self.stale or any(
            len([request for _, request in requests if urlsplit(request.url).path == path]) != 1
            for path in ("/api/v1/board/notice/list/top", "/api/v1/board/notice/list")
        ):
            raise ValueError("Board response changed during extraction")
        return list(rows_by_id.values())


async def collect_notice_todo(
    page: Any,
    courses: list[dict[str, Any]],
    *,
    capture: _TodoCapture,
    selected_course_id: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Read the committed global to-do once; failure cannot confirm an empty board."""
    with profile_span("todo", domain="notices"):
        rows = await _grid_snapshot(page, capture)
        return parse_notice_rows(rows, courses, selected_course_id=selected_course_id)


def arm_notice_capture(page: Any) -> _NoticeCapture:
    """Arm the board observer before its section document is opened."""
    return _NoticeCapture(page)


async def open_notice_section(
    page: Any,
    action: Callable[[], Any],
    *,
    capture: _NoticeCapture | None = None,
) -> None:
    """Open the notice board with its response capture armed."""
    try:
        await bounded(action(), PROTOCOL_TIMEOUT_SECONDS, "opening course notice board")
    except BaseException:
        if capture is not None:
            capture.close()
        raise


async def collect_notice_rows(
    page: Any,
    course: dict[str, Any],
    *,
    capture: _NoticeCapture,
    courses: list[dict[str, Any]],
    todo_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate the already-entered board and join its verified global to-do snapshot."""
    try:
        board = await capture.collect(page, course, courses)
        return parse_board_rows(board, course, todo_rows)
    finally:
        capture.close()


async def sync_notices(
    config: dict[str, Any],
    root: Path,
    course_id: str | None = None,
    *,
    headless: bool = False,
) -> tuple[dict[str, Any], list[CampusError]]:
    """Synchronize notices through the shared course traversal."""
    from .sync_all import sync_one

    return await sync_one(config, root, "notices", course_id, headless=headless)
