from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest

from campusctl.browser import profile_context
from campusctl.envelope import CampusError
from campusctl.profiling import SpanRecorder
from campusctl.providers.cnu import course_context

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lms_sources" / "course_navigation.json"


class FakePage:
    def __init__(self, *, expected_row: str, section_link: str, course_id: str, empty_section: bool = False) -> None:
        self.expected_row = expected_row
        self.section_link = section_link
        self.course_id = course_id
        self.empty_section = empty_section
        self.events: list[str] = []
        self.main_frame = type("Frame", (), {"url": "https://lms.example.invalid/std/myLecture"})()

    async def click(self, selector: str) -> None:
        if selector.startswith('[data-act="moveLecture"]'):
            if selector != self.expected_row:
                raise TimeoutError("course row was not found")
            self.events.append("click_course_row")
            self.main_frame.url = "https://lms.example.invalid/std/lecture"
            return
        if selector == self.section_link:
            self.events.append("click_section_link")
            return
        raise AssertionError(f"unexpected click selector: {selector}")

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        assert selector == course_context.COURSE_MENU_SELECTOR
        assert kwargs["state"] == "attached"
        assert 0 < kwargs["timeout"] <= course_context.COURSE_MENU_TIMEOUT_MS
        self.events.append("wait_for_course_menu")

    async def evaluate(self, script: str) -> str:
        assert "topbarCurrentLecture" in script
        return self.course_id


@pytest.fixture(autouse=True)
def provider_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl.providers.cnu import login

    monkeypatch.setattr(login, "MY_LECTURE_URL", "https://lms.example.invalid/std/myLecture")


@pytest.fixture
def navigation_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _page(fixture: dict[str, Any], *, empty_section: bool = False) -> FakePage:
    return FakePage(
        expected_row=fixture["selectors"]["course_row"],
        section_link=fixture["selectors"]["section_link"],
        course_id=fixture["course_id"],
        empty_section=empty_section,
    )


async def _navigate(page: FakePage, fixture: dict[str, Any]) -> None:
    await course_context.prepare_course_section(page, {}, fixture["course_id"], fixture["section"])
    assert page.events == ["click_course_row", "wait_for_course_menu"]
    await course_context.open_course_section(page, fixture["section"])


def test_enters_section_after_course_row(navigation_fixture: dict[str, Any]) -> None:
    page = _page(navigation_fixture)
    recorder = SpanRecorder(enabled=True)
    with profile_context(recorder):
        asyncio.run(_navigate(page, navigation_fixture))
    assert page.events == navigation_fixture["expected_events"]
    assert recorder.finish(stderr=io.StringIO())["diagnostics"] == []


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


def test_readiness_origin_failure_records_sanitized_route_state(navigation_fixture: dict[str, Any]) -> None:
    class WrongOriginPage(FakePage):
        async def click(self, selector: str) -> None:
            await super().click(selector)
            self.main_frame.url = "https://other.invalid/std/lecture"

    page = WrongOriginPage(
        expected_row=navigation_fixture["selectors"]["course_row"],
        section_link=navigation_fixture["selectors"]["section_link"],
        course_id=navigation_fixture["course_id"],
    )
    recorder = SpanRecorder(enabled=True)
    with profile_context(recorder), pytest.raises(ValueError, match="origin"):
        asyncio.run(
            course_context.prepare_course_section(
                page, {}, navigation_fixture["course_id"], navigation_fixture["section"]
            )
        )
    profile = recorder.finish(stderr=io.StringIO())
    assert profile["diagnostics"][0]["check"] == "page-readiness"
    assert profile["diagnostics"][0]["page_kind"] == "course-entry"
    assert profile["diagnostics"][0]["states"] == {"route_match": False}
    assert profile["diagnostics"][0]["counts"] == {}
    assert profile["diagnostics"][0]["elapsed_ns"] >= 0
    assert profile["diagnostics"][0]["bound_ns"] > 0
