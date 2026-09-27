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
from campusctl.wait_clock import current_clock

from .course_context import _TOPBAR_COURSE_JS, SECTION_RESPONSE_TIMEOUT_MS
from .readiness import _route_matches, wait_page_ready

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
    count: int | None = None
    for key in ("list", "rows", "items", "contents", "taskList", "stdList"):
        if key in body:
            value = body[key]
            if not isinstance(value, list):
                raise ValueError("Invalid task row list")
            count = len(value)
            break
    for key in ("totalCount", "total_count", "total", "count"):
        if key in body:
            value = body[key]
            if isinstance(value, bool) or not str(value).isdigit():
                raise ValueError("Invalid task row count")
            total = int(value)
            if count is not None and count != total:
                raise ValueError("Task response row count disagrees with total")
            return total
    if count is not None:
        return count
    for key in ("data", "body", "result"):
        if key in body:
            return _response_count(body[key])
    raise ValueError("Task response has no row count")


class _PageActivity:
    """Observe the ordered requests for the committed course task page."""

    def __init__(self, page: Any) -> None:
        self.page = page
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
        self.page.on("framenavigated", self._navigated)
        self.page.on("response", self._responded)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.page.remove_listener("request", self._started)
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
                activity.response_seen.wait(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "waiting for the CNU task response"
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
            response.finished(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "finishing the CNU task response"
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
    except (ValueError, TypeError) as error:
        raise ValueError("Task response has no verified row count") from error
    _check_response_course(body, course["course_id"])
    count = _response_count(body)
    await wait_page_ready(page, "assignments", expected_course_id=course["course_id"], domain="assignments")

    async def matching_rows() -> dict[str, Any]:
        while True:
            with profile_span("extract", domain="assignments"):
                extracted = await page.evaluate(EXTRACT_ASSIGNMENT_ROWS_JS)
            if (
                isinstance(extracted, dict)
                and type(extracted.get("row_count")) is int
                and isinstance(extracted.get("rows"), list)
                and extracted["row_count"] == count
                and len(extracted["rows"]) == count
            ):
                return extracted
            await current_clock().sleep(0.05)

    with profile_span("page-readiness", wait_kind="readiness", domain="assignments", page_kind="assignments"):
        extracted = await bounded(matching_rows(), COURSE_WAIT_MS / 1000, "waiting for CNU task rows")
    if (
        not _route_matches(page.main_frame.url, "/std/task")
        or await page.evaluate(_TOPBAR_COURSE_JS) != course["course_id"]
    ):
        raise ValueError("Task page changed before extraction completed")
    if (
        activity.std_before_document
        or len(activity.std_after_document) != 1
        or activity.document_count != 1
        or activity.task_commit_seq is None
        or not activity.own_task_response(response)
        or len(activity.responses) != 1
        or activity.responses[0] is not response
    ):
        raise ValueError("Task page not fully rendered")
    return parse_assignment_rows(extracted["rows"], course)


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
