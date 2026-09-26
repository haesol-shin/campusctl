"""Read-only, guarded CNU assignment metadata synchronization."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import (
    PROTOCOL_TIMEOUT_SECONDS,
    bounded,
    open_session,
    profile_count,
    profile_span,
    settle_sso_popups,
)
from campusctl.domain_catalog import (
    domain_catalog_path,
    mark_enrollment_unknown,
    merge_domain_catalog,
    read_domain_catalog,
    write_domain_catalog,
)
from campusctl.envelope import CampusError
from campusctl.identity import assignment_entity_id

from .course_context import SECTION_RESPONSE_TIMEOUT_MS, open_course_section, prepare_course_section
from .courses import COURSE_LINK_SELECTOR, EXTRACT_COURSES_JS, parse_courses
from .login import MY_LECTURE_URL, ensure_logged_in
from .roster_diagnostics import capture_roster_failure, start_roster_requests, stop_roster_requests
from .ui_policy import UiRequestDiagnostics, UiRequestPolicy, install_ui_request_interceptor

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

    def start(self) -> None:
        self.page.on("request", self._started)
        self.page.on("requestfinished", self._finished)
        self.page.on("requestfailed", self._finished)
        self.page.on("framenavigated", self._navigated)

    def close(self) -> None:
        self.page.remove_listener("request", self._started)
        self.page.remove_listener("requestfinished", self._finished)
        self.page.remove_listener("requestfailed", self._finished)
        self.page.remove_listener("framenavigated", self._navigated)

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


async def _course_rows(page: Any, config: dict[str, Any], course: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind the first post-commit task XHR by request order, task Referer, page course, and decoded ID."""
    activity = _PageActivity(page)
    activity.start()
    try:
        await bounded(
            page.wait_for_load_state("networkidle", timeout=COURSE_WAIT_MS),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for the previous CNU course requests to settle",
        )
        if activity.std_before_document:
            raise ValueError("A task response preceded this course navigation")
        with profile_span("course-selection", domain="assignments"):
            await prepare_course_section(page, config, course["course_id"], section="task")
        profile_count("course_selections")
        async with page.expect_response(
            activity.own_task_response, timeout=SECTION_RESPONSE_TIMEOUT_MS
        ) as response_info:
            with profile_span("document-commit", domain="assignments"):
                await open_course_section(page, "task")
        response = await bounded(response_info.value, PROTOCOL_TIMEOUT_SECONDS, "waiting for the CNU task response")
        with profile_span("response-completion", domain="assignments"):
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
        await bounded(activity.idle.wait(), PROTOCOL_TIMEOUT_SECONDS, "waiting for the CNU task page to be idle")
        await bounded(
            page.wait_for_load_state("networkidle", timeout=COURSE_WAIT_MS),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for all CNU task page requests to settle",
        )
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
        ):
            raise ValueError("Task page not fully rendered")
        raw = extracted["rows"]
        row_count = extracted["row_count"]
        if not raw and (row_count != 0 or count not in (None, 0)):
            raise ValueError("Task page is not an observed empty course")
        if count is not None and count != len(raw):
            raise ValueError("Task response and task page disagree")
        return parse_assignment_rows(raw, course)
    finally:
        activity.close()


def _course_failure(course: dict[str, Any], reason: str) -> CampusError:
    return CampusError(
        reason,
        f"Assignment sync could not complete for {course['label']} ({course['course_id']}).",
        "Retry assignment sync after the LMS task list is available.",
        "error",
    )


async def sync_assignments(
    config: dict[str, Any],
    root: Path,
    course_id: str | None = None,
    *,
    headless: bool = False,
    reviewed_policy: dict[str, Any],
) -> tuple[dict[str, Any], list[CampusError]]:
    """Sync only complete courses; an unpinned request prevents all publication."""
    policy = UiRequestPolicy.from_reviewed_config(reviewed_policy)
    if not policy.approved or not any(route.operation == "assignments.sync" for route in policy.routes):
        raise CampusError("policy-unapproved", "Assignment request policy is not approved.", None, "user-action")
    diagnostics = UiRequestDiagnostics()
    async with open_session(config, data_dir=root, headless=headless, operation="assignments.sync") as session:
        page = session.page
        with profile_span("auth", domain="assignments"):
            await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)
        await settle_sso_popups(session, domain="assignments")
        interceptor = await install_ui_request_interceptor(
            session.context, policy, operation="assignments.sync", diagnostics=diagnostics
        )
        trace = start_roster_requests(page, headless=headless)
        try:
            try:
                with profile_span("roster", domain="assignments"):
                    with profile_span("document-commit", domain="assignments"):
                        await bounded(
                            page.goto(MY_LECTURE_URL), PROTOCOL_TIMEOUT_SECONDS, "opening the CNU course roster"
                        )
                    trace.step = "wait"
                    with profile_span("dom-ready", domain="assignments"):
                        await bounded(
                            page.wait_for_selector(COURSE_LINK_SELECTOR, state="attached", timeout=COURSE_WAIT_MS),
                            PROTOCOL_TIMEOUT_SECONDS,
                            "waiting for the CNU course roster",
                        )
                    trace.step = "evaluate"
                    raw_roster = await bounded(
                        page.evaluate(EXTRACT_COURSES_JS), PROTOCOL_TIMEOUT_SECONDS, "extracting CNU courses"
                    )
                    trace.step = "parse"
                    roster = parse_courses(raw_roster)
                interceptor.raise_if_denied()
            except Exception as error:
                interceptor.raise_if_denied()
                if isinstance(error, CampusError) and error.code in {"login-action-required", "login-failed"}:
                    raise
                await capture_roster_failure(
                    page, operation="assignments.sync", step=trace.step, elapsed_s=trace.elapsed_s, root=root
                )
                if course_id is None:
                    mark_enrollment_unknown("assignments", root)
                raise CampusError(
                    "course-discovery-failed",
                    "Assignment course discovery did not complete.",
                    "Retry assignment sync after the LMS course list loads.",
                    "error",
                ) from None
            stop_roster_requests(page)
            courses = roster if course_id is None else [course for course in roster if course["course_id"] == course_id]
            if not courses and course_id is not None:
                raise CampusError(
                    "course-not-found",
                    "The requested course ID was not found among enrolled courses.",
                    "Check the course ID and retry.",
                    "user-action",
                )
            rows: list[dict[str, Any]] = []
            failures: list[dict[str, str]] = []
            errors: list[CampusError] = []
            successful: set[str] = set()
            for index, course in enumerate(courses):
                if index:
                    trace = start_roster_requests(page, headless=headless)
                    try:
                        await bounded(
                            page.goto(MY_LECTURE_URL), PROTOCOL_TIMEOUT_SECONDS, "returning to the CNU course roster"
                        )
                        interceptor.raise_if_denied()
                    except Exception:
                        interceptor.raise_if_denied()
                        await capture_roster_failure(
                            page, operation="assignments.sync", step="goto", elapsed_s=trace.elapsed_s, root=root
                        )
                        raise CampusError(
                            "course-discovery-failed", "The CNU course roster became unavailable.", None, "error"
                        ) from None
                    finally:
                        stop_roster_requests(page)
                try:
                    with profile_span("extract", domain="assignments", course=index + 1):
                        course_rows = await _course_rows(page, config, course)
                    interceptor.raise_if_denied()
                except Exception as error:
                    interceptor.raise_if_denied()
                    if isinstance(error, CampusError) and error.code in {"login-action-required", "login-failed"}:
                        raise
                    reason = (
                        "item-identity-missing"
                        if isinstance(error, CampusError) and error.code == "item-identity-missing"
                        else "course-sync-failed"
                    )
                    failures.append({"course_id": course["course_id"], "label": course["label"], "reason": reason})
                    errors.append(_course_failure(course, reason))
                else:
                    successful.add(course["course_id"])
                    rows.extend(course_rows)
            interceptor.raise_if_denied()
            target = domain_catalog_path("assignments", root)
            previous = read_domain_catalog("assignments", target) if target.exists() else None
            with profile_span("merge", domain="assignments"):
                merged = merge_domain_catalog(
                    "assignments",
                    previous,
                    roster,
                    rows,
                    successful_course_ids=successful,
                    failed_courses=failures,
                    selected_course_id=course_id,
                )
            interceptor.raise_if_denied()
            with profile_span("serialize-write", domain="assignments"):
                write_domain_catalog("assignments", merged, target)
            result = {
                "courses": len(successful),
                "assignments": len(rows),
                "failed_courses": failures,
                "catalog": {"generated_at": merged["generated_at"], "enrollment_state": merged["enrollment_state"]},
                "suppressed_count": diagnostics.suppressed_count,
                "suppressed_reasons": dict(diagnostics.suppressed_reasons),
            }
            return result, errors
        finally:
            stop_roster_requests(page)
            await interceptor.close()
