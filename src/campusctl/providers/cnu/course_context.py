"""Authenticated row-to-menu navigation for CNU course sections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded

COURSE_MENU_SELECTOR = 'a[href="/std/course"]'
COURSE_MENU_TIMEOUT_MS = 7000
SECTION_RESPONSE_TIMEOUT_MS = 15000
_SECTION_SELECTORS = {
    "course": COURSE_MENU_SELECTOR,
    "task": 'a[href="/std/task"]',
    "archive": 'a[href="/std/archive"]',
}


@dataclass(frozen=True, slots=True)
class CourseSelection:
    """One validated UI selection; identities are in-memory request/document ordinals."""

    course_id: str
    ordinal: int
    epoch: int
    response_identity: int
    document_identity: int

    @classmethod
    def from_response(
        cls,
        *,
        course_id: str,
        roster_course_id: str,
        topbar_course_id: str,
        ordinal: int,
        epoch: int,
        response_identity: int,
        document_identity: int,
        response_status: int,
        response_body: Any,
        response_count: int,
        document_committed: bool,
        pending: bool = False,
    ) -> CourseSelection:
        """Accept precisely one completed selection before its committed entry document."""
        header = response_body.get("header") if isinstance(response_body, dict) else None
        body = response_body.get("body") if isinstance(response_body, dict) else None
        data = body.get("data") if isinstance(body, dict) else None
        if (
            not course_id
            or not isinstance(course_id, str)
            or course_id != roster_course_id
            or course_id != topbar_course_id
            or response_status != 200
            or not isinstance(header, dict)
            or header.get("code") != 200
            or not isinstance(body, dict)
            or body.get("result") != "Y"
            or not isinstance(data, dict)
            or data.get("course_id") != course_id
            or response_count != 1
            or pending
            or not document_committed
            or any(
                type(value) is not int or value < 1 for value in (ordinal, epoch, response_identity, document_identity)
            )
            or response_identity >= document_identity
        ):
            raise ValueError("Course selection is not bound to its committed course entry")
        return cls(course_id, ordinal, epoch, response_identity, document_identity)


async def prepare_course_section(
    page: Any,
    config: dict[str, Any],
    course_id: str,
    section: Literal["course", "task", "archive"],
) -> None:
    """Enter the course and wait for its menu before arming a section response."""
    if not isinstance(course_id, str) or not course_id:
        raise ValueError("course_id must be a non-empty string")
    if section not in _SECTION_SELECTORS:
        raise ValueError("section must be course, task, or archive")

    course_selector = f'[data-act="moveLecture"][data-courseid={_css_string(course_id)}]'
    await bounded(page.click(course_selector), PROTOCOL_TIMEOUT_SECONDS, "opening a CNU course")
    await bounded(
        page.wait_for_selector(COURSE_MENU_SELECTOR, timeout=COURSE_MENU_TIMEOUT_MS),
        COURSE_MENU_TIMEOUT_MS / 1000 + PROTOCOL_TIMEOUT_SECONDS,
        "waiting for the CNU course menu",
    )


async def open_course_section(page: Any, section: Literal["course", "task", "archive"]) -> None:
    """Click the selected section while its response listener is armed."""
    if section not in _SECTION_SELECTORS:
        raise ValueError("section must be course, task, or archive")
    await bounded(
        page.click(_SECTION_SELECTORS[section]),
        PROTOCOL_TIMEOUT_SECONDS,
        "opening the CNU course section",
    )


def _css_string(value: str) -> str:
    """Quote a value as a CSS string without allowing selector injection."""
    escaped: list[str] = []
    for char in value:
        codepoint = ord(char)
        if char in {'"', "\\"}:
            escaped.append(f"\\{char}")
        elif codepoint < 0x20 or codepoint == 0x7F:
            escaped.append(f"\\{codepoint:x} ")
        else:
            escaped.append(char)
    return f'"{"".join(escaped)}"'
