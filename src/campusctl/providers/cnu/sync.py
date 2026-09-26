"""Lecture catalog synchronization for the CNU LMS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_span
from campusctl.catalog import catalog_path, read_catalog, write_catalog
from campusctl.envelope import CampusError

from .lectures import EXTRACT_LEARNING_ROWS_JS, LEARNING_ROW_SELECTOR, parse_learning_rows

COURSE_ROOM_URL_ANCHOR = 'a[href="/std/course"]'
COURSE_ROOM_TIMEOUT_MS = 7000


def _is_timeout(error: Exception) -> bool:
    """Recognize Playwright and asyncio timeout exceptions without importing Playwright."""
    return isinstance(error, TimeoutError) or type(error).__name__ == "TimeoutError"


def _course_failure(course: dict[str, Any]) -> CampusError:
    course_id = str(course.get("course_id", ""))
    label = str(course.get("label", ""))
    return CampusError(
        "course-sync-failed",
        f"Could not sync course '{label}' ({course_id}).",
        "Check the course in the LMS, then retry the sync.",
        "error",
    )


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
    page: Any, course: dict[str, Any], section_guard: Any, *, ordinal: int | None = None
) -> list[dict[str, Any]]:
    """Read LV rows on the committed course section without selecting again."""
    if section_guard is not None:
        section_guard.raise_if_denied()
    try:
        await bounded(
            page.wait_for_selector(LEARNING_ROW_SELECTOR, state="attached", timeout=COURSE_ROOM_TIMEOUT_MS),
            COURSE_ROOM_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
            "waiting for CNU lecture rows",
        )
    except Exception as error:
        if not _is_timeout(error):
            raise
        raw_rows = []
    else:
        with profile_span("extract", domain="lectures", course=ordinal):
            raw_rows = await bounded(
                page.evaluate(EXTRACT_LEARNING_ROWS_JS),
                PROTOCOL_TIMEOUT_SECONDS,
                "extracting CNU lecture rows",
            )
    if section_guard is not None:
        section_guard.raise_if_denied()
    return parse_learning_rows(raw_rows, course)


async def sync_lectures(
    config: dict[str, Any], root: Path, course_id: str | None = None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    """Publish lecture rows via the shared guarded course traversal."""
    from .sync_all import sync_one

    return await sync_one(config, root, "lectures", course_id, headless=headless)
