"""Resolve full course IDs, printed human indices, and human labels."""

from __future__ import annotations

from typing import Any

from campusctl.envelope import CampusError


class CourseAmbiguous(CampusError):
    """An ambiguous human selector with full candidate identities for the caller's result."""

    def __init__(self, candidates: list[dict[str, str]]) -> None:
        super().__init__(
            "course-ambiguous",
            "More than one course matches the selector.",
            "Use a full course ID from 'campusctl courses list'.",
        )
        self.candidates = candidates


def resolve_course(
    selector: str | None,
    courses: list[dict[str, Any]] | dict[str, Any],
    *,
    ids_only: bool,
    printed_roster: dict[str, Any] | None = None,
) -> str | None:
    """Select only current exact IDs; numbers require a matching printed generation."""
    if selector is None:
        return None
    rows = courses["courses"] if isinstance(courses, dict) else courses
    if not isinstance(selector, str):
        raise CampusError("selection-invalid", "The course selector is invalid.", "Run 'campusctl courses list' again.")
    for course in rows:
        if selector == course["course_id"]:
            return course["course_id"]
    numeric = selector.isdecimal()
    if ids_only:
        code = "course-index-unavailable" if numeric else "course-id-required"
        raise CampusError(
            code,
            "JSON course selection requires an exact full course ID.",
            "Use a full course ID from 'campusctl courses list --json'.",
        )
    normalized = "".join(selector.split()).casefold()
    if not normalized:
        raise CampusError("selection-invalid", "The course selector is blank.", "Run 'campusctl courses list' again.")
    if numeric:
        if printed_roster is None:
            raise CampusError(
                "selection-missing", "No printed course list is available.", "Run 'campusctl courses list' again."
            )
        if not isinstance(courses, dict) or printed_roster.get("roster_generation") != courses.get("roster_generation"):
            raise CampusError(
                "selection-stale",
                "The printed course list is no longer current.",
                "Run 'campusctl courses list' again.",
            )
        printed = printed_roster.get("courses")
        index = int(selector)
        if not isinstance(printed, list) or not 1 <= index <= len(printed):
            raise CampusError(
                "selection-invalid", "The printed course number is out of range.", "Run 'campusctl courses list' again."
            )
        course_id = printed[index - 1].get("course_id") if isinstance(printed[index - 1], dict) else None
        if not isinstance(course_id, str) or not any(row["course_id"] == course_id for row in rows):
            raise CampusError(
                "selection-stale",
                "The printed course list is no longer current.",
                "Run 'campusctl courses list' again.",
            )
        return course_id
    candidates = [course for course in rows if normalized in "".join(course.get("label", "").split()).casefold()]
    if len(candidates) == 1:
        return candidates[0]["course_id"]
    if candidates:
        raise CourseAmbiguous(
            [{"course_id": row["course_id"], "label": row.get("label", row["course_id"])} for row in candidates]
        )
    raise CampusError(
        "course-not-found", "No course matches the selector.", "Run 'campusctl courses list' and use a full course ID."
    )
