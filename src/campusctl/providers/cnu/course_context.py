"""Authenticated row-to-menu navigation for CNU course sections."""

from __future__ import annotations

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
_TOPBAR_COURSE_JS = """() => {
    const current = document.querySelector('#topbarCurrentLecture');
    const name = current?.textContent?.replace(/\\s+/g, '').trim();
    const matches = [...document.querySelectorAll('#topbarLectureDropdown a[data-act="changeLecture"][data-courseid]')]
        .filter(link => link.textContent.replace(/\\s+/g, '').trim() === name);
    return matches.length === 1 ? matches[0].getAttribute('data-courseid') : null;
}"""


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
