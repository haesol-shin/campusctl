"""Lecture catalog synchronization for the CNU LMS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, open_session, profile_count, profile_span
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
    with profile_span("auth", domain="lectures"):
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


async def sync_lectures(
    config: dict[str, Any], root: Path, course_id: str | None = None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    """Scrape enrolled-course lecture rows and merge them into the local catalog."""
    async with open_session(config, data_dir=root, headless=headless, operation="lectures.sync") as session:
        page = session.page
        try:
            discovered_courses = await discover_courses(page, config)
        except Exception as error:
            if isinstance(error, CampusError) and error.code in {*_LOGIN_ERRORS, "policy-blocked"}:
                raise
            if course_id is None:
                _mark_enrollment_unknown(root)
            raise CampusError(
                "course-discovery-failed",
                "Enrolled courses could not be discovered.",
                "Retry the lecture sync after the LMS course list loads.",
                "error",
            ) from None
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

        for ordinal, course in enumerate(courses, 1):
            # Login and course-list failures abort before touching the catalog.
            await _open_course_list(page, config)
            current_course_id = str(course.get("course_id", ""))
            try:
                with profile_span("course-selection", domain="lectures", course=ordinal):
                    await bounded(
                        page.click(f'[data-act="moveLecture"][data-courseid="{current_course_id}"]'),
                        PROTOCOL_TIMEOUT_SECONDS,
                        "opening a CNU course",
                    )
                profile_count("course_selections")
                await bounded(
                    page.wait_for_selector(COURSE_ROOM_URL_ANCHOR, timeout=COURSE_ROOM_TIMEOUT_MS),
                    COURSE_ROOM_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
                    "waiting for the CNU course lecture menu",
                )
                with profile_span("document-commit", domain="lectures", course=ordinal):
                    await bounded(
                        page.click(COURSE_ROOM_URL_ANCHOR),
                        PROTOCOL_TIMEOUT_SECONDS,
                        "opening the CNU course lecture page",
                    )
                profile_count("documents")
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
                    with profile_span("extract", domain="lectures", course=ordinal):
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
        with profile_span("merge", domain="lectures"):
            merged = merge_catalog(
                previous,
                successful_courses,
                lectures,
                failed_course_ids=failed_course_ids,
            )
        _merge_health(merged, previous, discovered_courses, failed_courses, course_id)
        with profile_span("serialize-write", domain="lectures"):
            write_catalog(merged, target)

    result = {
        "courses": len(successful_courses),
        "lectures": len(lectures),
        "incomplete": sum(lecture["completion"] == "incomplete" for lecture in lectures),
        "failed_courses": failed_courses,
        "catalog": {"generated_at": merged["generated_at"], "enrollment_state": merged["enrollment_state"]},
    }
    return result, errors
