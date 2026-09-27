"""Read-only CNU assignment metadata synchronization."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_span
from campusctl.envelope import CampusError
from campusctl.identity import assignment_entity_id

from .course_context import SECTION_RESPONSE_TIMEOUT_MS

TASK_TABLE_SELECTOR = "#table_list tbody#tbody"
TASK_RESPONSE_PATH = "/api/v1/task/stdList"
COURSE_WAIT_MS = 7000
_TASK_ID = re.compile(r"TB_L_REPORT[0-9]+\Z")
EXTRACT_ASSIGNMENT_ROWS_JS = r"""() => {
    const table = document.querySelector('#table_list tbody#tbody');
    if (!table) return null;
    const text = (node) => (node?.innerText || node?.textContent || '').trim();
    const rows = Array.from(table.rows).flatMap((tr) => {
        const link = tr.querySelector('a[data-act="detail"]');
        if (!link) return [];
        const cell = link.closest('td');
        const cellText = text(cell);
        const at = cellText.lastIndexOf(' ~ ');
        const statusCells = Array.from(tr.querySelectorAll('td')).filter((td) => td !== cell);
        const status = statusCells.find((td) => /^(완료|미완료)/.test(text(td)));
        return [{task_id: link.getAttribute('data-id'),
            title: text(link.querySelector('strong') || link),
            due_date: at < 0 ? null : cellText.slice(at + 3).trim(),
            is_submitted: statusCells.some((td) => !!td.querySelector('span.text_badge.complete'))
                || text(status).startsWith('완료')}];
    });
    return {row_count: table.rows.length, rows};
}"""
EXTRACT_COURSE_CONTEXT_JS = r"""() => {
    const current = document.querySelector('#topbarCurrentLecture');
    if (!current) return null;
    const normalize = (node) => (node.textContent || '').replace(/\s+/g, '');
    const name = normalize(current);
    const matches = Array.from(
        document.querySelectorAll('#topbarLectureDropdown a[data-act="changeLecture"][data-courseid]')
    ).filter((link) => normalize(link) === name);
    return matches.length === 1 ? matches[0].getAttribute('data-courseid') : null;
}"""


def _check_response_course(body: Any, course_id: str) -> None:
    """Reject every explicit native course identifier that differs from the selected course."""
    pending = [body]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and key.casefold().replace("_", "") in {"courseid", "crscd"}:
                    if item != course_id:
                        raise ValueError("Task response belongs to another course")
                elif isinstance(item, (dict, list)):
                    pending.append(item)
        elif isinstance(value, list):
            pending.extend(item for item in value if isinstance(item, (dict, list)))


def parse_assignment_rows(raw_rows: list[dict[str, Any]], course: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize all candidate rows or reject the entire unaddressable course."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw_rows:
        native = row.get("task_id")
        task_id = native.strip() if isinstance(native, str) else ""
        if not _TASK_ID.fullmatch(task_id) or task_id in seen:
            raise CampusError(
                "item-identity-missing",
                "An assignment in this course has no unique valid task ID.",
                "Retry assignment sync after the LMS task list is available.",
                "error",
            )
        seen.add(task_id)
        due = row.get("due_date")
        result.append(
            {
                "entity_id": assignment_entity_id(course["course_id"], task_id),
                "task_id": task_id,
                "course": {"id": course["course_id"], "label": course["label"]},
                "kind": "assignment",
                "title": str(row.get("title") or "").strip(),
                "due_date": due.strip() or None if isinstance(due, str) else None,
                "is_submitted": row.get("is_submitted") is True,
            }
        )
    return result


def _response_count(body: Any) -> int:
    """Use an available list/count without equating an unknown shape to zero."""
    if isinstance(body, list):
        return len(body)
    if not isinstance(body, dict):
        raise ValueError("Invalid task response")
    header = body.get("header")
    if isinstance(header, dict) and str(header.get("code")) not in {"200", "0"}:
        raise ValueError("Unsuccessful task response")
    for key in ("list", "rows", "items", "contents", "taskList", "stdList"):
        if key in body:
            value = body[key]
            if not isinstance(value, list):
                raise ValueError("Invalid task row list")
            return len(value)
    for key in ("totalCount", "total_count", "total", "count"):
        if key in body:
            value = body[key]
            if isinstance(value, bool) or not str(value).isdigit():
                raise ValueError("Invalid task row count")
            return int(value)
    for key in ("data", "body", "result"):
        if key in body:
            return _response_count(body[key])
    raise ValueError("Task response has no row count")


class _PageActivity:
    """Observe all page requests until the course task page is idle."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self.pending: set[int] = set()
        self.idle = asyncio.Event()
        self.idle.set()
        self.requests: list[Any] = []
        self.task_document_seq: int | None = None
        self.task_commit_seq: int | None = None
        self.std_before_document = 0
        self.std_after_document: list[tuple[int, Any]] = []
        self.document_count = 0
        self.responses: list[Any] = []
        self.response_seen = asyncio.Event()
        self.closed = False

    def start(self) -> None:
        self.page.on("request", self._started)
        self.page.on("requestfinished", self._finished)
        self.page.on("requestfailed", self._finished)
        self.page.on("framenavigated", self._navigated)
        self.page.on("response", self._responded)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.page.remove_listener("request", self._started)
        self.page.remove_listener("requestfinished", self._finished)
        self.page.remove_listener("requestfailed", self._finished)
        self.page.remove_listener("framenavigated", self._navigated)
        self.page.remove_listener("response", self._responded)

    def _started(self, request: Any) -> None:
        self.requests.append(request)
        sequence = len(self.requests)
        if (
            self._main_frame(request)
            and request.is_navigation_request()
            and request.resource_type == "document"
            and self._path(request) == "/std/task"
        ):
            self.document_count += 1
            if self.task_document_seq is None:
                self.task_document_seq = sequence
        elif (
            self._main_frame(request) and request.method.upper() == "POST" and self._path(request) == TASK_RESPONSE_PATH
        ):
            if self.task_commit_seq is None or sequence <= self.task_commit_seq:
                self.std_before_document += 1
            else:
                self.std_after_document.append((sequence, request))
        self.pending.add(id(request))
        self.idle.clear()

    def _navigated(self, frame: Any) -> None:
        if (
            frame is self.page.main_frame
            and urlsplit(frame.url).path == "/std/task"
            and self.task_document_seq is not None
            and self.task_commit_seq is None
        ):
            self.task_commit_seq = len(self.requests)

    def _responded(self, response: Any) -> None:
        if self._path(response.request) == TASK_RESPONSE_PATH:
            self.responses.append(response)
            self.response_seen.set()

    def _finished(self, request: Any) -> None:
        self.pending.discard(id(request))
        if not self.pending:
            self.idle.set()

    def _main_frame(self, request: Any) -> bool:
        try:
            return request.frame is self.page.main_frame
        except Exception:
            return False

    @staticmethod
    def _path(request: Any) -> str:
        return urlsplit(request.url).path

    def own_task_response(self, response: Any) -> bool:
        request = response.request
        return bool(self.std_after_document) and request is self.std_after_document[0][1]


def arm_assignment_capture(page: Any) -> _PageActivity:
    """Observe the committed task document and candidate list requests before navigation."""
    activity = _PageActivity(page)
    activity.start()
    return activity


async def open_assignment_section(
    page: Any,
    action: Callable[[], Any],
    *,
    capture: _PageActivity | None = None,
) -> None:
    """Open the task document while its response capture is armed."""
    try:
        await bounded(action(), PROTOCOL_TIMEOUT_SECONDS, "opening the CNU task section")
    except BaseException:
        if capture is not None:
            capture.close()
        raise


async def collect_assignment_rows(
    page: Any,
    course: dict[str, Any],
    *,
    capture: _PageActivity,
) -> list[dict[str, Any]]:
    """Validate the already-entered task section without selecting or publishing."""
    activity = capture
    try:
        with profile_span("response-completion", wait_kind="response", domain="assignments"):
            await bounded(
                asyncio.wait_for(activity.response_seen.wait(), SECTION_RESPONSE_TIMEOUT_MS / 1000),
                SECTION_RESPONSE_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
                "waiting for the CNU task response",
            )
        response = activity.responses[0]
        return await _collect_assignment_rows(page, course, activity, response)
    finally:
        activity.close()


async def _collect_assignment_rows(
    page: Any, course: dict[str, Any], activity: _PageActivity, response: Any
) -> list[dict[str, Any]]:
    """Validate first post-commit task XHR, task Referer, DOM, and response count."""
    if not activity.own_task_response(response):
        raise ValueError("Task response did not follow the committed task document")
    with profile_span("response-completion", wait_kind="response", domain="assignments"):
        completion_error = await bounded(
            response.finished(), PROTOCOL_TIMEOUT_SECONDS, "finishing the CNU task response"
        )
    if completion_error is not None or response.status != 200:
        raise ValueError("Task response failed")
    headers = await bounded(
        response.request.all_headers(), PROTOCOL_TIMEOUT_SECONDS, "checking the task request context"
    )
    referer = next((value for key, value in headers.items() if key.casefold() == "referer"), "")
    reference = urlsplit(referer)
    request_origin = urlsplit(response.request.url)
    if activity.task_commit_seq is None or (reference.scheme, reference.netloc, reference.path) != (
        request_origin.scheme,
        request_origin.netloc,
        "/std/task",
    ):
        raise ValueError("Task response did not originate in the selected task page")
    try:
        body = await bounded(response.json(), PROTOCOL_TIMEOUT_SECONDS, "reading CNU task metadata")
    except (ValueError, TypeError):
        count = None  # Body inaccessible; successful response plus idle DOM is still valid.
    else:
        _check_response_course(body, course["course_id"])
        count = _response_count(body)
    with profile_span("wait", wait_kind="timeout", domain="assignments"):
        await bounded(activity.idle.wait(), PROTOCOL_TIMEOUT_SECONDS, "waiting for the CNU task page to be idle")
    with profile_span("idle", wait_kind="load", domain="assignments"):
        await bounded(
            page.wait_for_load_state("networkidle", timeout=COURSE_WAIT_MS),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for all CNU task page requests to settle",
        )
    with profile_span("dom-ready", wait_kind="selector", domain="assignments"):
        await bounded(
            page.wait_for_selector(TASK_TABLE_SELECTOR, state="attached", timeout=COURSE_WAIT_MS),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for the CNU task table",
        )
    page_course_id = await bounded(
        page.evaluate(EXTRACT_COURSE_CONTEXT_JS), PROTOCOL_TIMEOUT_SECONDS, "checking the active CNU task course"
    )
    if page_course_id != course["course_id"]:
        raise ValueError("Task page belongs to another course")
    with profile_span("extract", domain="assignments"):
        extracted = await bounded(
            page.evaluate(EXTRACT_ASSIGNMENT_ROWS_JS), PROTOCOL_TIMEOUT_SECONDS, "extracting CNU tasks"
        )
    if (
        not isinstance(extracted, dict)
        or type(extracted.get("row_count")) is not int
        or not isinstance(extracted.get("rows"), list)
        or activity.pending
        or activity.std_before_document
        or len(activity.std_after_document) != 1
        or activity.document_count != 1
        or activity.task_commit_seq is None
        or (activity.responses and (len(activity.responses) != 1 or activity.responses[0] is not response))
    ):
        raise ValueError("Task page not fully rendered")
    raw = extracted["rows"]
    row_count = extracted["row_count"]
    if not raw and (row_count != 0 or count not in (None, 0)):
        raise ValueError("Task page is not an observed empty course")
    if count is not None and count != len(raw):
        raise ValueError("Task response and task page disagree")
    return parse_assignment_rows(raw, course)


async def sync_assignments(
    config: dict[str, Any],
    root: Path,
    course_id: str | None = None,
    *,
    headless: bool = False,
) -> tuple[dict[str, Any], list[CampusError]]:
    """Sync assignment rows through the shared course traversal."""
    from .sync_all import sync_one

    return await sync_one(config, root, "assignments", course_id, headless=headless)
