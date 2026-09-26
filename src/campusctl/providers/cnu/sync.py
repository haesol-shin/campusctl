"""Lecture catalog synchronization for the CNU LMS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, open_session
from campusctl.catalog import catalog_path, merge_catalog, read_catalog, write_catalog
from campusctl.envelope import CampusError

from .courses import discover_courses
from .lectures import EXTRACT_LEARNING_ROWS_JS, LEARNING_ROW_SELECTOR, parse_learning_rows
from .login import COURSE_LINK_SELECTOR, MY_LECTURE_URL, ensure_logged_in

COURSE_ROOM_URL_ANCHOR = 'a[href="/std/course"]'
COURSE_ROOM_TIMEOUT_MS = 7000
_LOGIN_ERRORS = {
    "login-failed",
    "login-action-required",
    "lms-unavailable",
    "credentials-username-missing",
    "credentials-not-configured",
    "credential-backend-insecure",
    "credential-backend-unavailable",
    "credential-helper-invalid",
    "credential-helper-failed",
}


async def _open_course_list(page: Any, config: dict[str, Any]) -> None:
    """Return to the authenticated course list before entering another room."""
    await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)


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


async def sync_lectures(
    config: dict[str, Any], root: Path, course_id: str | None = None
) -> tuple[dict[str, Any], list[CampusError]]:
    """Scrape enrolled-course lecture rows and merge them into the local catalog."""
    async with open_session(config, data_dir=root) as session:
        page = session.page
        discovered_courses = await discover_courses(page, config)
        if course_id is None:
            courses = discovered_courses
        else:
            courses = [course for course in discovered_courses if course.get("course_id") == course_id]
            if not courses:
                raise CampusError(
                    "course-not-found",
                    "The requested course ID was not found among enrolled courses.",
                    "Check the course ID and retry.",
                    "user-action",
                )

        successful_courses: list[dict[str, Any]] = []
        lectures: list[dict[str, Any]] = []
        failed_courses: list[dict[str, str]] = []
        failed_course_ids: set[str] = set()
        errors: list[CampusError] = []

        for course in courses:
            # Login and course-list failures abort before touching the catalog.
            await _open_course_list(page, config)
            current_course_id = str(course.get("course_id", ""))
            try:
                await bounded(
                    page.click(f'[data-act="moveLecture"][data-courseid="{current_course_id}"]'),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "opening a CNU course",
                )
                await bounded(
                    page.wait_for_selector(COURSE_ROOM_URL_ANCHOR, timeout=COURSE_ROOM_TIMEOUT_MS),
                    COURSE_ROOM_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
                    "waiting for the CNU course lecture menu",
                )
                await bounded(
                    page.click(COURSE_ROOM_URL_ANCHOR),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "opening the CNU course lecture page",
                )
                try:
                    await bounded(
                        page.wait_for_selector(
                            LEARNING_ROW_SELECTOR,
                            state="attached",
                            timeout=COURSE_ROOM_TIMEOUT_MS,
                        ),
                        COURSE_ROOM_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
                        "waiting for CNU lecture rows",
                    )
                except Exception as error:
                    if not _is_timeout(error):
                        raise
                    raw_rows = []
                else:
                    raw_rows = await bounded(
                        page.evaluate(EXTRACT_LEARNING_ROWS_JS),
                        PROTOCOL_TIMEOUT_SECONDS,
                        "extracting CNU lecture rows",
                    )
                course_lectures = parse_learning_rows(raw_rows, course)
            except CampusError as error:
                if error.code in _LOGIN_ERRORS:
                    raise
                failed_course_ids.add(current_course_id)
                failed_courses.append({"course_id": current_course_id, "label": str(course.get("label", ""))})
                errors.append(_course_failure(course))
            except Exception:
                failed_course_ids.add(current_course_id)
                failed_courses.append({"course_id": current_course_id, "label": str(course.get("label", ""))})
                errors.append(_course_failure(course))
            else:
                successful_courses.append(course)
                lectures.extend(course_lectures)

        target = catalog_path(root)
        previous = read_catalog(target) if target.exists() else None
        if course_id is None and not failed_course_ids and previous is not None:
            enrolled_ids = {str(course.get("course_id")) for course in discovered_courses}
            previous = {
                **previous,
                "courses": [course for course in previous["courses"] if str(course.get("course_id")) in enrolled_ids],
                "lectures": [
                    lecture
                    for lecture in previous["lectures"]
                    if str(lecture.get("course", {}).get("id")) in enrolled_ids
                ],
            }
        merged = merge_catalog(
            previous,
            successful_courses,
            lectures,
            failed_course_ids=failed_course_ids,
        )
        write_catalog(merged, target)

    result = {
        "courses": len(successful_courses),
        "lectures": len(lectures),
        "incomplete": sum(lecture["completion"] == "incomplete" for lecture in lectures),
        "failed_courses": failed_courses,
        "catalog": {"generated_at": merged["generated_at"]},
    }
    return result, errors
