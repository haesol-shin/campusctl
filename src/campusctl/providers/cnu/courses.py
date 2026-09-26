"""Course discovery extraction and normalization for the CNU LMS."""

from __future__ import annotations

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_span

COURSE_LINK_SELECTOR = '[data-act="moveLecture"]'
EXTRACT_COURSES_JS = r"""() => {
    const courses = [];
    const seen = new Set();
    document.querySelectorAll('[data-act="moveLecture"]').forEach((el) => {
        const courseId = el.getAttribute('data-courseid');
        if (!courseId || seen.has(courseId)) return;
        seen.add(courseId);
        courses.push({
            course_id: courseId,
            label: el.getAttribute('data-coursenm'),
            class_no: el.getAttribute('data-classno')
        });
    });
    return courses;
}"""


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_courses(raw: list[dict]) -> list[dict]:
    """Return normalized course catalog records, preserving DOM order."""
    courses = []
    seen: set[str] = set()
    for row in raw:
        course_id = _text(row.get("course_id"))
        if not course_id or course_id in seen:
            continue
        seen.add(course_id)
        label = _text(row.get("label")) or course_id
        class_no = _text(row.get("class_no")) or None
        courses.append({"course_id": course_id, "label": label, "class_no": class_no})
    return courses


async def discover_courses(page: object, config: dict) -> list[dict]:
    from .login import ensure_logged_in

    with profile_span("auth", domain="lectures"):
        await ensure_logged_in(page, config)
    with profile_span("roster", domain="lectures"):
        with profile_span("dom-ready", domain="lectures"):
            await bounded(
                page.wait_for_selector(
                    COURSE_LINK_SELECTOR,
                    state="attached",
                    timeout=int(PROTOCOL_TIMEOUT_SECONDS * 1000),
                ),
                PROTOCOL_TIMEOUT_SECONDS,
                "waiting for CNU course links",
            )
        with profile_span("extract", domain="lectures"):
            raw = await bounded(
                page.evaluate(EXTRACT_COURSES_JS),
                PROTOCOL_TIMEOUT_SECONDS,
                "extracting CNU courses",
            )
    return parse_courses(raw)
