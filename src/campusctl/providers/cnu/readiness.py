"""Bounded element readiness for CNU pages.

Proves route, a rendered surface, and unique topbar identity. Collectors still
prove response completeness and list cardinality.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import bounded, profile_check_start, profile_diagnostic, profile_span
from campusctl.envelope import CampusError

from .course_context import _TOPBAR_COURSE_JS, COURSE_MENU_TIMEOUT_MS
from .courses import COURSE_LINK_SELECTOR

READINESS_TIMEOUT_S = COURSE_MENU_TIMEOUT_MS / 1000

_ROUTES = {
    "roster": "/std/myLecture",
    "course-entry": "/std/lecture",
    "lecture": "/std/course",
    "assignments": "/std/task",
    "notices": "/std/notice",
    "archive": "/std/archive",
    "todo": "/std/todo",
    "assignment-detail": "/std/taskView",
}
_COURSE_KINDS = frozenset({"course-entry", "lecture", "assignments", "notices", "archive"})
_ATTACHED = {
    "roster": (COURSE_LINK_SELECTOR,),
    "course-entry": ('a[href="/std/course"]',),
    "assignments": ("#table_list tbody#tbody",),
    "notices": ("tbody#table-body",),
    "archive": ("#table_list #listBody", "#totalCnt strong"),
}
_TODO_READY_JS = """() => {
    const grid = document.querySelector('#noticeList');
    if (!grid) return false;
    const empty = grid.querySelector('#noticeNoData');
    const visible = !!empty && getComputedStyle(empty).display !== 'none' && getComputedStyle(empty).visibility !== 'hidden';
    return !!grid.querySelector('.tabulator-row') || visible;
}"""
_DETAIL_TITLE = ".card-body h4"


def provider_origin() -> tuple[str, str]:
    """Scheme and host from the configured roster URL, read when the page is checked."""
    from . import login

    parts = urlsplit(login.MY_LECTURE_URL)
    return parts.scheme, parts.netloc


def _page_url(page: Any) -> str:
    frame = getattr(page, "main_frame", None)
    url = getattr(frame, "url", None) if frame is not None else None
    if not isinstance(url, str) or not url:
        url = getattr(page, "url", "")
    return url if isinstance(url, str) else ""


def _route_matches(url: str, path: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc) == provider_origin() and parts.path == path


async def _identity(page: Any) -> str | None:
    value = await page.evaluate(_TOPBAR_COURSE_JS)
    return value if isinstance(value, str) and value else None


def _remaining_ms(deadline: float) -> int:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return max(1, int(remaining * 1000))


async def _await_surface(page: Any, page_kind: str, deadline: float) -> None:
    if page_kind == "lecture":
        return
    if page_kind == "todo":
        while True:
            if (await page.evaluate(_TODO_READY_JS)) is True:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError
            await asyncio.sleep(0.05)
    if page_kind == "assignment-detail":
        await page.wait_for_selector(_DETAIL_TITLE, state="visible", timeout=_remaining_ms(deadline))
        return
    for selector in _ATTACHED[page_kind]:
        await page.wait_for_selector(selector, state="attached", timeout=_remaining_ms(deadline))


async def wait_page_ready(
    page: Any,
    page_kind: str,
    *,
    expected_course_id: str | None = None,
    domain: str | None = None,
    ordinal: int | None = None,
) -> None:
    """Wait until the committed page is the one the next collector will read."""
    if page_kind not in _ROUTES:
        raise ValueError("unsupported page kind")
    if page_kind in _COURSE_KINDS and (not isinstance(expected_course_id, str) or not expected_course_id):
        raise ValueError("expected_course_id is required")

    path = _ROUTES[page_kind]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + READINESS_TIMEOUT_S
    labels: dict[str, Any] = {"page_kind": page_kind, "wait_kind": "readiness"}
    if domain is not None:
        labels["domain"] = domain
    if ordinal is not None:
        labels["course"] = ordinal
    started = profile_check_start()
    check = "page-readiness"
    route_match: bool | None = None
    ids_match: bool | None = None

    def diagnose() -> None:
        if started is None:
            return
        states: dict[str, bool] = {}
        if route_match is not None:
            states["route_match"] = route_match
        if ids_match is not None:
            states["ids_match"] = ids_match
        profile_diagnostic(
            check,
            started=started,
            bound_ns=int(READINESS_TIMEOUT_S * 1_000_000_000),
            states=states,
            **{key: value for key, value in labels.items() if key != "wait_kind"},
        )


    async def ready() -> None:
        nonlocal check, route_match, ids_match
        entered_route = False
        while True:
            if loop.time() >= deadline:
                raise TimeoutError
            url = _page_url(page)
            current = urlsplit(url)
            if url and (current.scheme, current.netloc) != provider_origin():
                route_match = False
                raise ValueError("page origin does not match the provider")
            if not _route_matches(url, path):
                if entered_route:
                    route_match = False
                    raise ValueError("page route changed before readiness")
                await asyncio.sleep(0.05)
                continue
            entered_route = True
            route_match = True
            if page_kind in _COURSE_KINDS:
                check = "course-identity"
                course_id = await _identity(page)
                ids_match = course_id == expected_course_id if course_id is not None else None
                if course_id is not None and course_id != expected_course_id:
                    raise ValueError("page course does not match the selected course")
                if course_id != expected_course_id:
                    await asyncio.sleep(0.05)
                    continue
            check = "page-readiness"
            break
        try:
            await _await_surface(page, page_kind, deadline)
        except Exception as error:
            if type(error).__name__ == "TimeoutError":
                raise TimeoutError from error
            raise
        if not _route_matches(_page_url(page), path):
            route_match = False
            raise ValueError("page route changed before readiness")
        if page_kind in _COURSE_KINDS:
            check = "course-identity"
            ids_match = await _identity(page) == expected_course_id
            if not ids_match:
                raise ValueError("page course does not match the selected course")

    with profile_span("page-readiness", **labels):
        try:
            await bounded(ready(), READINESS_TIMEOUT_S, "waiting for page readiness")
        except CampusError:
            diagnose()
            raise
        except Exception as error:
            diagnose()
            if type(error).__name__ != "TimeoutError":
                raise
            raise CampusError(
                "browser-timeout",
                "Timed out while waiting for page readiness.",
                "Check the browser and try again.",
                "error",
            ) from None
