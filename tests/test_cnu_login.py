from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu import courses, login

SENTINEL = "secret-sentinel-9462"
USERNAME = "authorized-user"


class FakeResponse:
    def __init__(self, data: dict[str, Any], *, body_delay: float = 0, body_error: Exception | None = None) -> None:
        self.data = data
        self.body_delay = body_delay
        self.body_error = body_error

    async def body(self) -> bytes:
        if self.body_delay:
            await asyncio.sleep(self.body_delay)
        if self.body_error is not None:
            raise self.body_error
        return json.dumps(self.data).encode()


class FakeRoute:
    def __init__(
        self,
        data: dict[str, Any],
        *,
        fetch_delay: float = 0,
        body_delay: float = 0,
        fetch_error: Exception | None = None,
        body_error: Exception | None = None,
        hang_fulfill: bool = False,
    ) -> None:
        self.response = FakeResponse(data, body_delay=body_delay, body_error=body_error)
        self.fetch_delay = fetch_delay
        self.fetch_error = fetch_error
        self.hang_fulfill = hang_fulfill
        self.fetch_count = 0
        self.fulfill_count = 0
        self.abort_count = 0

    async def fetch(self) -> FakeResponse:
        self.fetch_count += 1
        if self.fetch_delay:
            await asyncio.sleep(self.fetch_delay)
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.response

    async def fulfill(self, *, response: FakeResponse) -> None:
        assert response is self.response
        self.fulfill_count += 1
        if self.hang_fulfill:
            await asyncio.Event().wait()

    async def abort(self) -> None:
        self.abort_count += 1


class FakePage:
    def __init__(
        self,
        response_data: dict[str, Any] | None = None,
        *,
        landing_visible: bool = False,
        form_visible: bool = True,
        route: FakeRoute | None = None,
        fill_error: bool = False,
        never_land: bool = False,
    ) -> None:
        self.landing_visible = landing_visible
        self.form_visible = form_visible
        self.route_value = route or FakeRoute(response_data or {"header": {"code": 200}})
        self.fill_error = fill_error
        self.never_land = never_land
        self.route_task: asyncio.Task[Any] | None = None
        self.fills: list[tuple[str, str]] = []
        self.gotos: list[tuple[str, dict[str, Any]]] = []
        self.routes = 0
        self.unroutes = 0
        self.route_matcher = None
        self.route_handler = None
        self.clicked = False
        self.cnu_setup_evaluated = False

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.gotos.append((url, kwargs))

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        if selector == f"{login.COURSE_LINK_SELECTOR}, {login.LOGIN_FORM_SELECTOR}":
            if not self.landing_visible and not self.form_visible:
                raise TimeoutError
            return
        if selector == login.COURSE_LINK_SELECTOR and self.route_task is not None:
            await self.route_task
        if selector == login.COURSE_LINK_SELECTOR and self.landing_visible:
            return
        raise TimeoutError

    async def is_visible(self, selector: str) -> bool:
        if selector == login.COURSE_LINK_SELECTOR:
            return self.landing_visible
        if selector == login.LOGIN_FORM_SELECTOR:
            return self.form_visible
        return False

    async def evaluate(self, script: str, *args: Any) -> None:
        del args
        assert "univ_no" in script and "#cnu_auth" in script
        self.cnu_setup_evaluated = True

    async def fill(self, selector: str, value: str) -> None:
        if self.fill_error and selector == login.LOGIN_PASSWORD_SELECTOR:
            raise RuntimeError(f"fill failed for {value}")
        self.fills.append((selector, value))

    async def route(self, matcher: Any, handler: Any) -> None:
        self.routes += 1
        self.route_matcher = matcher
        self.route_handler = handler

    async def unroute(self, matcher: Any, handler: Any) -> None:
        assert matcher is self.route_matcher
        assert handler is self.route_handler
        self.unroutes += 1
        self.route_matcher = None
        self.route_handler = None

    async def click(self, selector: str) -> None:
        assert selector == login.LOGIN_BUTTON_SELECTOR
        self.clicked = True
        assert self.route_matcher("https://dcs-learning.cnu.ac.kr/apiCnuAuthLogin")
        route_handler = self.route_handler
        assert route_handler is not None

        async def handle_login() -> None:
            await route_handler(self.route_value)
            header = self.route_value.response.data.get("header", {})
            ret_code = str(self.route_value.response.data.get("ret_code"))
            if self.route_value.fulfill_count and header.get("code") in (200, "200") and ret_code not in {"0", "-2"}:
                if not self.never_land:
                    self.landing_visible = True
                self.form_visible = False

        self.route_task = asyncio.create_task(handle_login())


async def _ensure(page: FakePage, *, timeout_ms: int = 30000) -> None:
    await login.ensure_logged_in(page, {"credentials": {"provider": "keyring"}}, timeout_ms=timeout_ms)


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _assert_redacted(error: BaseException) -> None:
    assert SENTINEL not in str(error)
    assert SENTINEL not in repr(error)


def test_existing_landing_skips_credentials_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(landing_visible=True)

    def fail_if_read(_: dict[str, Any]) -> tuple[str, str]:
        raise AssertionError("credentials must not be read on the authenticated path")

    monkeypatch.setattr(login, "get_credentials", fail_if_read)
    _run(_ensure(page))

    assert page.gotos[0][0] == login.MY_LECTURE_URL
    assert page.routes == 0
    assert page.fills == []
    assert not page.clicked


def test_visible_login_form_is_filled_and_submitted_through_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    calls: list[dict[str, Any]] = []

    def credentials(config: dict[str, Any]) -> tuple[str, str]:
        calls.append(config)
        return USERNAME, SENTINEL

    monkeypatch.setattr(login, "get_credentials", credentials)
    _run(_ensure(page))

    assert calls == [{"credentials": {"provider": "keyring"}}]
    assert page.cnu_setup_evaluated
    assert page.fills == [(login.LOGIN_FORM_SELECTOR, USERNAME), (login.LOGIN_PASSWORD_SELECTOR, SENTINEL)]
    assert page.clicked
    assert page.routes == page.unroutes == 1
    assert page.route_value.fetch_count == page.route_value.fulfill_count == 1


def test_rejected_credentials_are_redacted_and_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage({"header": {"code": 401, "msg": SENTINEL}})
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    with pytest.raises(CampusError) as caught:
        _run(_ensure(page))

    assert caught.value.code == "login-failed"
    _assert_redacted(caught.value)
    assert page.unroutes == 1


def test_terms_agreement_is_reported_as_required_action(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage({"header": {"code": 200}, "ret_code": "0"})
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    with pytest.raises(CampusError) as caught:
        _run(_ensure(page))

    assert caught.value.code == "login-action-required"
    _assert_redacted(caught.value)
    assert page.unroutes == 1


def test_password_change_modal_requires_browser_action(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage({"header": {"code": 200}, "ret_code": "-2"})
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    with pytest.raises(CampusError) as caught:
        _run(_ensure(page))

    assert caught.value.code == "login-action-required"
    assert "password" in caught.value.message.lower()
    assert "normal browser" in caught.value.remediation.lower()
    assert "stored credentials" in caught.value.remediation.lower()
    _assert_redacted(caught.value)
    assert page.unroutes == 1


def test_landing_timeout_is_reported_as_lms_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(never_land=True)
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    with pytest.raises(CampusError) as caught:
        _run(_ensure(page))

    assert caught.value.code == "lms-unavailable"
    _assert_redacted(caught.value)
    assert page.unroutes == 1


def test_fetch_and_body_use_one_response_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    route = FakeRoute({"header": {"code": 200}}, fetch_delay=0.5, body_delay=0.5)
    page = FakePage(route=route)
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    _run(_ensure(page, timeout_ms=2000))
    assert route.fetch_count == route.fulfill_count == 1
    assert page.landing_visible


def test_fetch_and_body_exceed_shared_response_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    route = FakeRoute({"header": {"code": 200}}, fetch_delay=0.75, body_delay=0.75)
    page = FakePage(route=route)
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    async def exercise() -> CampusError:
        with pytest.raises(CampusError) as caught:
            await _ensure(page, timeout_ms=1000)
        assert page.route_task is not None
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(page.route_task, timeout=5)
        return caught.value

    error = _run(exercise())

    assert error.code == "lms-unavailable"
    _assert_redacted(error)
    assert route.fulfill_count == 0
    assert route.abort_count == 1
    assert page.unroutes == 1


def test_hanging_fulfill_is_bounded_and_route_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login, "PROTOCOL_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(login, "CLEANUP_TIMEOUT_SECONDS", 1)
    route = FakeRoute({"header": {"code": 200}}, hang_fulfill=True)
    page = FakePage(route=route)
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    with pytest.raises(CampusError) as caught:
        _run(_ensure(page, timeout_ms=500))

    assert caught.value.code == "lms-unavailable"
    _assert_redacted(caught.value)
    assert route.fulfill_count == route.abort_count == 1
    assert page.routes == page.unroutes == 1


def test_cancelled_login_drains_route_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    class BlockingFetchRoute(FakeRoute):
        def __init__(self) -> None:
            super().__init__({"header": {"code": 200}})
            self.fetch_started = asyncio.Event()

        async def fetch(self) -> FakeResponse:
            self.fetch_count += 1
            self.fetch_started.set()
            await asyncio.Event().wait()

    route = BlockingFetchRoute()
    page = FakePage(route=route)
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    async def exercise() -> None:
        ensure_task = asyncio.create_task(_ensure(page, timeout_ms=10000))
        await asyncio.wait_for(route.fetch_started.wait(), timeout=1)
        ensure_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ensure_task
        assert page.route_task is not None
        assert page.route_task.done()
        assert page.route_task.cancelled()

    _run(exercise())
    assert route.abort_count == 1
    assert page.unroutes == 1


def test_credential_provider_exception_is_propagated_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    expected = RuntimeError("credential provider is unavailable")

    def fail(_: dict[str, Any]) -> tuple[str, str]:
        raise expected

    monkeypatch.setattr(login, "get_credentials", fail)

    with pytest.raises(RuntimeError) as caught:
        _run(_ensure(page))

    assert caught.value is expected
    assert page.routes == 0


def test_login_form_failure_does_not_echo_password(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(fill_error=True)
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    with pytest.raises(CampusError) as caught:
        _run(_ensure(page))

    assert caught.value.code == "lms-unavailable"
    _assert_redacted(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    assert page.fills == [(login.LOGIN_FORM_SELECTOR, USERNAME)]
    assert page.routes == 0


def test_response_transport_exception_does_not_echo_password(monkeypatch: pytest.MonkeyPatch) -> None:
    route = FakeRoute({"header": {"code": 200}}, fetch_error=RuntimeError(f"transport {SENTINEL}"))
    page = FakePage(route=route)
    monkeypatch.setattr(login, "get_credentials", lambda _: (USERNAME, SENTINEL))

    with pytest.raises(CampusError) as caught:
        _run(_ensure(page))

    assert caught.value.code == "lms-unavailable"
    _assert_redacted(caught.value)
    assert page.unroutes == 1


def test_discovery_waits_for_attached_links_and_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class DiscoveryPage:
        def __init__(self) -> None:
            self.wait_calls: list[tuple[str, dict[str, Any]]] = []
            self.scripts: list[str] = []

        async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
            self.wait_calls.append((selector, kwargs))

        async def evaluate(self, script: str) -> list[dict[str, Any]]:
            self.scripts.append(script)
            return [
                {"course_id": " course-a ", "label": " Intro ", "class_no": " 001 "},
                {"course_id": "course-a", "label": "duplicate", "class_no": "002"},
                {"course_id": "course-b", "label": None, "class_no": ""},
            ]

    page = DiscoveryPage()
    calls: list[tuple[Any, dict[str, Any]]] = []

    async def ensure(page_arg: Any, config: dict[str, Any]) -> None:
        calls.append((page_arg, config))

    monkeypatch.setattr(login, "ensure_logged_in", ensure)
    config = {"provider": "cnu"}
    result = _run(courses.discover_courses(page, config))

    assert calls == [(page, config)]
    assert page.wait_calls[0][0] == courses.COURSE_LINK_SELECTOR
    assert page.wait_calls[0][1]["state"] == "attached"
    assert page.scripts == [courses.EXTRACT_COURSES_JS]
    assert result == [
        {"course_id": "course-a", "label": "Intro", "class_no": "001"},
        {"course_id": "course-b", "label": "course-b", "class_no": None},
    ]
