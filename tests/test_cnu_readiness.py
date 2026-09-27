from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu import login, readiness
from virtual_clock import VirtualClock, drive

ORIGIN = "https://lms.example.invalid"


class Page:
    def __init__(self, path: str) -> None:
        self.main_frame = SimpleNamespace(url=ORIGIN + path)
        self.identity: str | None = "course-a"
        self.surface = True
        self.selectors: list[tuple[str, str, int]] = []

    async def evaluate(self, expression: str) -> Any:
        if expression == readiness._TODO_READY_JS:
            return self.surface
        return self.identity

    async def wait_for_selector(self, selector: str, *, state: str, timeout: int) -> None:
        self.selectors.append((selector, state, timeout))


@pytest.fixture(autouse=True)
def origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login, "MY_LECTURE_URL", ORIGIN + "/std/myLecture")
    monkeypatch.setattr(readiness, "READINESS_TIMEOUT_S", 0.3)


def test_route_and_identity_arrive_before_virtual_deadline() -> None:
    clock = VirtualClock()
    page = Page("/user/login")
    page.identity = None
    clock.call_at(0.1, lambda: setattr(page.main_frame, "url", ORIGIN + "/std/task"))
    clock.call_at(0.2, lambda: setattr(page, "identity", "course-a"))
    asyncio.run(drive(readiness.wait_page_ready(page, "assignments", expected_course_id="course-a"), clock))
    assert len(page.selectors) == 1
    assert page.selectors[0][:2] == ("#table_list tbody#tbody", "attached")
    assert 0.2 <= clock.now() < 0.3


def test_identity_mismatch_fails_without_waiting_for_deadline() -> None:
    clock = VirtualClock()
    page = Page("/std/task")
    page.identity = "course-b"
    with pytest.raises(ValueError, match="selected course"):
        asyncio.run(drive(readiness.wait_page_ready(page, "assignments", expected_course_id="course-a"), clock))
    assert clock.now() == 0


def test_unready_surface_reaches_virtual_timeout() -> None:
    clock = VirtualClock()
    page = Page("/std/todo")
    page.surface = False
    with pytest.raises(CampusError) as caught:
        asyncio.run(drive(readiness.wait_page_ready(page, "todo"), clock))
    assert caught.value.code == "browser-timeout"
    assert clock.now() == pytest.approx(0.3)
