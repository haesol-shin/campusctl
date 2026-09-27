"""Lecture catalog synchronization for the CNU LMS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_span
from campusctl.catalog import catalog_path, read_catalog, write_catalog
from campusctl.envelope import CampusError

from .course_context import _TOPBAR_COURSE_JS
from .lectures import EXTRACT_LEARNING_ROWS_JS, LEARNING_ROW_SELECTOR, parse_learning_rows
from .readiness import _route_matches, wait_page_ready

COURSE_ROOM_URL_ANCHOR = 'a[href="/std/course"]'
COURSE_ROOM_TIMEOUT_MS = 7000


def _is_timeout(error: Exception) -> bool:
    """Recognize Playwright and asyncio timeout exceptions without importing Playwright."""
    return isinstance(error, TimeoutError) or type(error).__name__ == "TimeoutError"


def _mark_enrollment_unknown(root: Path) -> None:
    target = catalog_path(root)
    if not target.exists():
        return
    previous = read_catalog(target)
    if previous.get("enrollment_state") != "unknown":
        write_catalog({**previous, "enrollment_state": "unknown"}, target)


def _merge_health(
    merged: dict[str, Any],
    previous: dict[str, Any] | None,
    roster: list[dict[str, Any]],
    failures: list[dict[str, str]],
    selected_course_id: str | None,
) -> None:
    """Keep retained rows visibly stale until their course succeeds or enrollment is resolved."""
    old_courses = {course["course_id"]: course for course in previous["courses"]} if previous else {}
    old_failures = {failure["course_id"]: failure for failure in previous.get("failed_courses", [])} if previous else {}
    current_failures = {failure["course_id"]: {**failure, "reason": "course-sync-failed"} for failure in failures}
    if selected_course_id is not None:
        old_failures.pop(selected_course_id, None)
        old_failures.update(current_failures)
        merged["enrollment_state"] = previous.get("enrollment_state", "unknown") if previous else "unknown"
        merged["failed_courses"] = list(old_failures.values())
        return

    roster_ids = {course["course_id"] for course in roster}
    if current_failures:
        for course_id, course in old_courses.items():
            if course_id not in roster_ids:
                current_failures[course_id] = {
                    "course_id": course_id,
                    "label": course["label"],
                    "reason": "removal-deferred",
                }
    # A successful full discovery resolves old failures and removed enrollment.
    merged["enrollment_state"] = "known"
    merged["failed_courses"] = list(current_failures.values())


async def collect_lectures_rows(
    page: Any, course: dict[str, Any], *, ordinal: int | None = None
) -> list[dict[str, Any]]:
    """Read LV rows on the committed course section without selecting again."""
    await wait_page_ready(page, "lecture", expected_course_id=course["course_id"], domain="lectures", ordinal=ordinal)
    try:
        with profile_span("dom-ready", wait_kind="selector", domain="lectures", course=ordinal):
            await bounded(
                page.wait_for_selector(LEARNING_ROW_SELECTOR, state="attached", timeout=COURSE_ROOM_TIMEOUT_MS),
                COURSE_ROOM_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
                "waiting for CNU lecture rows",
            )
    except Exception as error:
        if not _is_timeout(error):
            raise
        if not _route_matches(page.main_frame.url, "/std/course"):
            raise ValueError("Lecture section document changed before an empty course was accepted") from error
        confirmed = await page.evaluate(_TOPBAR_COURSE_JS)
        if confirmed != course["course_id"]:
            raise ValueError("Lecture section belongs to another course") from error
        raw_rows = []
    else:
        with profile_span("extract", domain="lectures", course=ordinal):
            raw_rows = await bounded(
                page.evaluate(EXTRACT_LEARNING_ROWS_JS),
                PROTOCOL_TIMEOUT_SECONDS,
                "extracting CNU lecture rows",
            )
        if not _route_matches(page.main_frame.url, "/std/course"):
            raise ValueError("Lecture section document changed during extraction")
        if await page.evaluate(_TOPBAR_COURSE_JS) != course["course_id"]:
            raise ValueError("Lecture section belongs to another course")
    return parse_learning_rows(raw_rows, course)


async def sync_lectures(
    config: dict[str, Any], root: Path, course_id: str | None = None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    """Publish lecture rows via the shared course traversal."""
    from .sync_all import sync_one

    return await sync_one(config, root, "lectures", course_id, headless=headless)
