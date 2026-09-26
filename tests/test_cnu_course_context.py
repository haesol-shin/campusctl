from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu import course_context, login
from campusctl.providers.cnu.ui_policy import (
    UiRequestDenied,
    UiRequestDiagnostics,
    UiRequestPolicy,
    install_ui_request_interceptor,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lms_sources" / "course_navigation.json"


class FakePage:
    def __init__(self, *, expected_row: str, section_link: str, empty_section: bool = False) -> None:
        self.expected_row = expected_row
        self.section_link = section_link
        self.empty_section = empty_section
        self.events: list[str] = []

    async def click(self, selector: str) -> None:
        if selector.startswith('[data-act="moveLecture"]'):
            if selector != self.expected_row:
                raise TimeoutError("course row was not found")
            self.events.append("click_course_row")
            return
        if selector == self.section_link:
            self.events.append("click_section_link")
            return
        raise AssertionError(f"unexpected click selector: {selector}")

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        assert selector == course_context.COURSE_MENU_SELECTOR
        assert kwargs == {"timeout": course_context.COURSE_MENU_TIMEOUT_MS}
        self.events.append("wait_for_course_menu")
        if self.empty_section:
            return


@pytest.fixture
def navigation_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _page(fixture: dict[str, Any], *, empty_section: bool = False) -> FakePage:
    return FakePage(
        expected_row=fixture["selectors"]["course_row"],
        section_link=fixture["selectors"]["section_link"],
        empty_section=empty_section,
    )


async def _navigate(page: FakePage, fixture: dict[str, Any]) -> None:
    await course_context.prepare_course_section(page, {}, fixture["course_id"], fixture["section"])
    assert page.events == ["click_course_row", "wait_for_course_menu"]
    await course_context.open_course_section(page, fixture["section"])


def test_enters_section_after_course_row(navigation_fixture: dict[str, Any]) -> None:
    page = _page(navigation_fixture)
    asyncio.run(_navigate(page, navigation_fixture))
    assert page.events == navigation_fixture["expected_events"]


def test_empty_section_keeps_row_to_menu_context(navigation_fixture: dict[str, Any]) -> None:
    page = _page(navigation_fixture, empty_section=True)
    asyncio.run(_navigate(page, navigation_fixture))

    assert page.events == ["click_course_row", "wait_for_course_menu", "click_section_link"]


def test_wrong_course_id_never_clicks_another_course(navigation_fixture: dict[str, Any]) -> None:
    page = _page(navigation_fixture)

    with pytest.raises(CampusError) as caught:
        asyncio.run(
            course_context.prepare_course_section(page, {}, "course-not-enrolled", navigation_fixture["section"])
        )

    assert caught.value.code == "browser-timeout"

    assert page.events == []


def test_pre_auth_guard_roster_course_reentry_and_expiry(
    navigation_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = "https://lms.example.invalid"
    config = {
        "approved": True,
        "read_only_evidence": "synthetic",
        "allowed_media": [],
        "max_bytes": None,
        "origins": [origin],
        "routes": [
            {"origin": origin, "path": "/std/myLecture", "operation": "course.sync", "methods": ["GET"]},
            {"origin": origin, "path": "/std/task", "operation": "course.sync", "methods": ["GET"]},
            {
                "origin": origin,
                "path": "/api/v1/course/addSessionCourseInfo",
                "operation": "course.sync",
                "methods": ["POST"],
            },
        ],
    }
    policy = UiRequestPolicy.from_reviewed_config(config)
    page = _page(navigation_fixture)
    page.handler = None
    page.requests = []

    async def route(pattern: str, handler: Any) -> None:
        assert pattern == "**/*" and page.handler is None
        page.handler = handler

    async def unroute(pattern: str, handler: Any) -> None:
        assert pattern == "**/*" and page.handler == handler
        page.handler = None

    async def request(url: str, method: str = "GET", redirected_from: Any = None) -> None:
        class Request:
            resource_type = "document"

            async def all_headers(self) -> dict[str, str]:
                return {}

        class Route:
            def __init__(self) -> None:
                self.request = Request()
                self.request.url = url
                self.request.method = method
                self.request.redirected_from = redirected_from

            async def abort(self) -> None:
                page.requests.append(("abort", url))

            async def continue_(self) -> None:
                page.requests.append(("continue", url))

        assert page.handler is not None
        await page.handler(Route())

    page.route = route
    page.unroute = unroute
    authentications = []

    async def authenticate(supplied_page: Any, _config: dict[str, Any], **_kwargs: Any) -> None:
        assert supplied_page is page and page.handler is None
        authentications.append("once")

    monkeypatch.setattr(login, "ensure_logged_in", authenticate)
    original_click = page.click

    async def guarded_click(selector: str) -> None:
        await original_click(selector)
        if selector.startswith('[data-act="moveLecture"]'):
            await request(origin + "/api/v1/course/addSessionCourseInfo", "POST")
        else:
            await request(origin + "/std/task")

    page.click = guarded_click

    async def scenario() -> None:
        await login.ensure_logged_in(page, {}, target_url=origin + "/std/myLecture", expected_selector="row")
        interceptor = await install_ui_request_interceptor(
            page,
            policy,
            operation="course.sync",
            diagnostics=UiRequestDiagnostics(),
        )
        try:
            await request(origin + "/std/myLecture")  # guarded roster
            interceptor.raise_if_denied()
            await request(origin + "/std/myLecture")  # guarded course re-entry
            await _navigate(page, navigation_fixture)
            interceptor.raise_if_denied()
            assert authentications == ["once"]
            assert len(page.requests) == 4
            assert all(action == "continue" for action, _ in page.requests)
            await request(origin + "/login", redirected_from=type("Previous", (), {"url": origin + "/std/task"})())
            with pytest.raises(UiRequestDenied) as caught:
                interceptor.raise_if_denied()
            assert caught.value.reason_code == "redirect"
            assert authentications == ["once"]
            assert page.requests[-1] == ("abort", origin + "/login")
        finally:
            await interceptor.close()
        assert page.handler is None

    asyncio.run(scenario())
