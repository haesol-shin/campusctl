from __future__ import annotations

import asyncio
import json
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from virtual_clock import VirtualClock, settle

from campusctl import browser
from campusctl.envelope import CampusError
from campusctl.lock import exclusive_lock
from campusctl.profiling import SpanRecorder
from campusctl.wait_clock import current_clock, use_clock


class FakeCdpSession:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def send(self, command: str, params: dict[str, Any]) -> None:
        self.commands.append((command, params))


class FakePage:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = 0
        self.cdp_session = FakeCdpSession()
        self.url = "about:blank"
        self.main_frame = object()
        self.listeners: dict[str, list[Any]] = {}

    async def opener(self) -> None:
        return None

    def on(self, event: str, callback: Any) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event: str, callback: Any) -> None:
        self.listeners[event].remove(callback)

    async def close(self) -> None:
        self.closed += 1


class FakeContext:
    def __init__(
        self, *, pages: list[FakePage] | None = None, close_wait: bool = False, close_cancel: bool = False
    ) -> None:
        self.pages = pages or []
        self.closed = 0
        self.close_wait = close_wait
        self.close_cancel = close_cancel
        self.new_pages: list[FakePage] = []
        self.listeners: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event: str, callback: Any) -> None:
        self.listeners[event].remove(callback)

    async def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        self.new_pages.append(page)
        return page

    async def new_cdp_session(self, page: FakePage) -> FakeCdpSession:
        return page.cdp_session

    async def close(self) -> None:
        self.closed += 1
        if self.close_wait:
            await asyncio.Event().wait()
        if self.close_cancel:
            raise asyncio.CancelledError("cleanup cancelled")


class FakeChromium:
    def __init__(self, context: FakeContext | None = None, *, connect_wait: bool = False) -> None:
        self.executable_path = None
        self.context = context or FakeContext()
        self.connect_wait = connect_wait
        self.launch_args: dict[str, Any] | None = None
        self.connect_args: list[tuple[str, dict[str, Any]]] = []
        self.browser = type("FakeBrowser", (), {"contexts": [self.context]})()

    async def launch_persistent_context(self, **kwargs: Any) -> FakeContext:
        self.launch_args = kwargs
        return self.context

    async def connect_over_cdp(self, ws_url: str, **kwargs: Any) -> Any:
        self.connect_args.append((ws_url, kwargs))
        if self.connect_wait:
            await asyncio.Event().wait()
        return self.browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = 0

    async def stop(self) -> None:
        self.stopped += 1


class FakePlaywrightManager:
    def __init__(
        self,
        playwright: FakePlaywright,
        *,
        start_wait: bool = False,
        start_error: Exception | None = None,
    ) -> None:
        self.playwright = playwright
        self.started = 0
        self.exited = 0
        self.start_wait = start_wait
        self.start_error = start_error
        self.start_started = asyncio.Event()

    async def start(self) -> FakePlaywright:
        self.started += 1
        self.start_started.set()
        if self.start_error is not None:
            raise self.start_error
        if self.start_wait:
            await asyncio.Event().wait()
        return self.playwright

    async def __aexit__(self, *_args: Any) -> None:
        self.exited += 1
        await self.playwright.stop()


def install_fake_playwright(monkeypatch: pytest.MonkeyPatch, chromium: FakeChromium) -> FakePlaywright:
    playwright = FakePlaywright(chromium)
    manager = FakePlaywrightManager(playwright)
    monkeypatch.setattr(browser, "PLAYWRIGHT_FACTORY", lambda: manager)
    return playwright


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _cdp_config(endpoint: str) -> dict[str, Any]:
    return {"browser": {"cdp_endpoint": endpoint}}


def test_bounded_timeout_is_safe_and_names_operation() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def wait_for_release() -> None:
            started.set()
            await asyncio.Event().wait()

        operation = asyncio.create_task(browser.bounded(wait_for_release(), 1, "connecting to a test browser"))
        await started.wait()
        with pytest.raises(CampusError) as caught:
            await operation
        assert caught.value.code == "browser-timeout"
        assert caught.value.status == "error"
        assert "connecting to a test browser" in caught.value.message

    _run(scenario())


@pytest.mark.parametrize("outcome", ["success", "pending-success", "cancel", "timeout"])
def test_finish_response_does_not_orphan_target_close_tasks(outcome: str) -> None:
    from types import SimpleNamespace

    from playwright._impl._network import Response as ImplResponse

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        errors: list[dict[str, Any]] = []
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        closed = loop.create_future()
        impl = object.__new__(ImplResponse)
        impl._finished_future = loop.create_future()
        impl._request = SimpleNamespace(_target_closed_future=lambda: closed)
        response = SimpleNamespace(_impl_obj=impl, finished=impl.finished)
        baseline = asyncio.all_tasks()
        try:
            if outcome == "success":
                impl._finished_future.set_result(True)
                assert await browser.finish_response(response) is None
            elif outcome == "pending-success":
                waiter = asyncio.create_task(browser.finish_response(response))
                await settle()
                assert not waiter.done()
                impl._finished_future.set_result(True)
                assert await waiter is None
            elif outcome == "cancel":
                waiter = asyncio.create_task(browser.finish_response(response))
                await settle()
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
            else:
                with pytest.raises(CampusError, match="finishing a test response"):
                    await browser.bounded(browser.finish_response(response), 0.01, "finishing a test response")
            await settle()
            orphaned = asyncio.all_tasks() - baseline
            closed.set_result(None)
            await settle()
            # Retain and retrieve leaked tasks only after recording the failure,
            # so the regression itself does not pollute other tests' stderr.
            leaked_errors = await asyncio.gather(*orphaned, return_exceptions=True)
            assert not orphaned, leaked_errors
            assert not errors
            assert not impl._finished_future.cancelled()
            assert not closed.cancelled()
        finally:
            loop.set_exception_handler(None)

    _run(scenario())


@pytest.mark.parametrize("outcome", ["closed", "closed-and-finished", "completion-error"])
def test_finish_response_preserves_errors(outcome: str) -> None:
    from types import SimpleNamespace

    from playwright._impl._network import Response as ImplResponse
    from playwright.async_api import Error

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        closed = loop.create_future()
        impl = object.__new__(ImplResponse)
        impl._finished_future = loop.create_future()
        impl._request = SimpleNamespace(_target_closed_future=lambda: closed)
        response = SimpleNamespace(_impl_obj=impl, finished=impl.finished)
        if outcome == "closed-and-finished":
            impl._finished_future.set_result(True)
            closed.set_result(None)
        waiter = asyncio.create_task(browser.finish_response(response))
        await settle()
        if outcome == "closed":
            assert not waiter.done()
            closed.set_result(None)
        elif outcome == "completion-error":
            assert not waiter.done()
            impl._finished_future.set_exception(Error("Response failed"))
        message = "Response failed" if outcome == "completion-error" else "Target closed"
        with pytest.raises(Error, match=message):
            await waiter
        assert not closed.cancelled()
        assert not impl._finished_future.cancelled()

    _run(scenario())


def test_bounded_virtual_timeout_cancels_pending_operation_at_exact_deadline() -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with use_clock(clock):
            operation = asyncio.create_task(browser.bounded(pending(), 1.5, "connecting to a test browser"))
            await started.wait()
            await settle()
            clock.advance(1.49)
            await settle()
            assert not operation.done()
            assert not cancelled.is_set()
            clock.advance(0.01)
            with pytest.raises(CampusError) as caught:
                await operation
            assert caught.value.code == "browser-timeout"
            assert caught.value.status == "error"
            assert "connecting to a test browser" in caught.value.message
            assert cancelled.is_set()
            assert clock.now() == pytest.approx(1.5)
            assert not clock.sleepers

    _run(scenario())


def test_bounded_virtual_completion_and_task_local_clock_reset() -> None:
    async def scenario() -> None:
        real = current_clock()
        loop = asyncio.get_running_loop()
        assert abs(real.now() - loop.time()) < 1
        assert abs(real.monotonic() - loop.time()) < 1
        clock = VirtualClock()
        sibling = asyncio.create_task(asyncio.sleep(0, result=current_clock()))

        async def work() -> str:
            await current_clock().sleep(0.25)
            return "complete"

        with use_clock(clock):
            assert current_clock() is clock
            operation = asyncio.create_task(browser.bounded(work(), 1.5, "fetching synthetic data"))
            await settle()
            clock.advance(0.24)
            await settle()
            assert not operation.done()
            clock.sleep_sync(0.01)
            assert await operation == "complete"
            assert clock.monotonic() == pytest.approx(0.25)
            assert not clock.sleepers
        assert current_clock() is real
        assert await sibling is real
        with pytest.raises(RuntimeError), use_clock(clock):
            raise RuntimeError("reset on failure")
        assert current_clock() is real

    _run(scenario())


def test_bounded_virtual_external_cancellation_propagates_to_operation() -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        wrapped: asyncio.Task[None] | None = None

        async def pending() -> None:
            nonlocal wrapped
            wrapped = asyncio.current_task()
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with use_clock(clock):
            operation = asyncio.create_task(browser.bounded(pending(), 4, "connecting"))
            await started.wait()
            await settle()
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert cancelled.is_set()
            assert wrapped is not None and wrapped.done() and wrapped.cancelled()
            assert not clock.sleepers

    _run(scenario())


def test_bounded_virtual_nonpositive_timeout_matches_real_wait_for() -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        loop = asyncio.get_running_loop()
        completed: asyncio.Future[str] = loop.create_future()
        completed.set_result("ready")

        with use_clock(clock):
            assert await browser.bounded(completed, 0, "reading completed data") == "ready"
            assert await browser.bounded(completed, -1, "reading completed data") == "ready"
            started = False

            async def pending() -> None:
                nonlocal started
                started = True
                await asyncio.Event().wait()

            for seconds in (0, -1):
                request = asyncio.create_task(pending())
                with pytest.raises(CampusError) as caught:
                    await browser.bounded(request, seconds, "reading pending data")
                assert caught.value.code == "browser-timeout"
                assert request.cancelled()
                assert not started
            assert clock.now() == 0
            assert not clock.sleepers

    _run(scenario())


def test_lock_busy_is_reported_before_browser_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    lock_path = tmp_path / "shared.lock"
    config = {"browser": {"lock_path": str(lock_path)}}

    with exclusive_lock(lock_path), pytest.raises(CampusError) as caught:
        _run(browser.open_session(config, data_dir=tmp_path / "data").__aenter__())

    assert caught.value.status == "busy"
    assert caught.value.code == "session-busy"


def test_linux_local_mode_requires_a_display_before_loading_playwright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(browser, "PLAYWRIGHT_FACTORY", lambda: pytest.fail("Playwright must remain lazy"))

    with pytest.raises(CampusError) as caught:
        _run(browser.open_session({}, data_dir=tmp_path).__aenter__())

    assert caught.value.code == "display-unavailable"
    assert "xvfb-run" in (caught.value.remediation or "")


@pytest.mark.parametrize("interruption", ["timeout", "cancellation", "exception"])
def test_interrupted_playwright_start_still_exits_its_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser, "PROTOCOL_TIMEOUT_SECONDS", 1)
    playwright = FakePlaywright(FakeChromium())
    manager = FakePlaywrightManager(
        playwright,
        start_wait=interruption != "exception",
        start_error=RuntimeError("start failed") if interruption == "exception" else None,
    )
    monkeypatch.setattr(browser, "PLAYWRIGHT_FACTORY", lambda: manager)

    async def scenario() -> None:
        entry = browser.open_session({}, data_dir=tmp_path).__aenter__()
        if interruption == "timeout":
            with pytest.raises(CampusError) as caught:
                await entry
            assert caught.value.code == "browser-timeout"
        elif interruption == "cancellation":
            start_task = asyncio.create_task(entry)
            await manager.start_started.wait()
            start_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await start_task
        else:
            with pytest.raises(RuntimeError, match="start failed"):
                await entry

    _run(scenario())
    assert manager.started == 1
    assert manager.exited == 1
    assert playwright.stopped == 1


def test_missing_playwright_chromium_reports_install_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    chromium = FakeChromium()
    chromium.executable_path = "/missing/chromium"
    install_fake_playwright(monkeypatch, chromium)

    with pytest.raises(CampusError) as caught:
        _run(browser.open_session({}, data_dir=tmp_path).__aenter__())

    assert caught.value.code == "browser-not-installed"
    assert caught.value.status == "user-action"
    assert caught.value.remediation == "Run 'campusctl setup' to install the browser."


def test_unreachable_cdp_endpoint_is_actionable_and_does_not_echo_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_playwright(monkeypatch, FakeChromium())
    monkeypatch.setattr(browser, "PROTOCOL_TIMEOUT_SECONDS", 1)

    # Reserve the port without listening: connections fail, and another worker cannot claim it.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{reserved.getsockname()[1]}/json/version"
        with pytest.raises(CampusError) as caught:
            _run(browser.open_session(_cdp_config(endpoint), data_dir=tmp_path).__aenter__())

    assert caught.value.code == "browser-endpoint-unreachable"
    assert endpoint not in caught.value.message
    assert endpoint not in (caught.value.remediation or "")


def test_cdp_connect_hang_is_bounded_and_reported_without_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = "http://127.0.0.1:9223/json/version"
    chromium = FakeChromium(connect_wait=True)
    install_fake_playwright(monkeypatch, chromium)
    monkeypatch.setattr(browser, "PROTOCOL_TIMEOUT_SECONDS", 1)

    async def fake_resolver(_endpoint: str) -> str:
        return "ws://127.0.0.1:9223/devtools/browser/id"

    monkeypatch.setattr(browser, "resolve_cdp_ws_url", fake_resolver)

    with pytest.raises(CampusError) as caught:
        _run(browser.open_session(_cdp_config(endpoint), data_dir=tmp_path).__aenter__())

    assert caught.value.code == "browser-endpoint-unreachable"
    assert endpoint not in caught.value.message
    assert chromium.connect_args[0][1]["timeout"] == 1000


def test_cdp_reuses_external_context_and_closes_only_its_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = "http://127.0.0.1:9223/json/version"
    context = FakeContext()
    chromium = FakeChromium(context)
    playwright = install_fake_playwright(monkeypatch, chromium)

    async def fake_resolver(_endpoint: str) -> str:
        return "ws://127.0.0.1:9223/devtools/browser/id"

    monkeypatch.setattr(browser, "resolve_cdp_ws_url", fake_resolver)

    async def scenario() -> None:
        async with browser.open_session(_cdp_config(endpoint), data_dir=tmp_path) as session:
            assert session.mode == "cdp"
            assert session.context is context
            assert session.page is context.new_pages[0]
        assert context.closed == 0
        assert context.new_pages[0].closed == 1
        assert playwright.stopped == 1

    _run(scenario())


@pytest.mark.parametrize("require_owned_page", [False, True])
def test_cdp_owned_page_preserves_parked_tabs_and_default_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_owned_page: bool
) -> None:
    endpoint = "http://browser.invalid:9223/json/version"
    context = FakeContext()
    existing = FakePage(context)
    context.pages.append(existing)
    playwright = install_fake_playwright(monkeypatch, FakeChromium(context))

    async def fake_resolver(_endpoint: str) -> str:
        return "ws://browser.invalid:9223/devtools/browser/synthetic"

    monkeypatch.setattr(browser, "resolve_cdp_ws_url", fake_resolver)

    async def scenario() -> None:
        async with browser.open_session(
            _cdp_config(endpoint),
            data_dir=tmp_path,
            operation="lectures.sync",
            require_owned_page=require_owned_page,
        ) as session:
            assert session.page is (context.new_pages[0] if require_owned_page else existing)
        assert existing.closed == 0
        assert context.closed == 0 and playwright.stopped == 1
        if require_owned_page:
            assert len(context.new_pages) == 1 and context.new_pages[0].closed == 1
            assert existing.cdp_session.commands == []
            assert context.new_pages[0].cdp_session.commands == [
                ("Network.setUserAgentOverride", {"userAgent": browser.NORMAL_CHROME_USER_AGENT})
            ]
        else:
            assert context.new_pages == []
            assert existing.cdp_session.commands == [
                ("Network.setUserAgentOverride", {"userAgent": browser.NORMAL_CHROME_USER_AGENT})
            ]

    _run(scenario())


def test_owned_cdp_sync_closes_its_only_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = "http://browser.invalid:9223/json/version"
    context = FakeContext()
    playwright = install_fake_playwright(monkeypatch, FakeChromium(context))

    async def fake_resolver(_endpoint: str) -> str:
        return "ws://browser.invalid:9223/devtools/browser/synthetic"

    monkeypatch.setattr(browser, "resolve_cdp_ws_url", fake_resolver)

    async def scenario() -> None:
        async with browser.open_session(
            _cdp_config(endpoint), data_dir=tmp_path, operation="lectures.sync", require_owned_page=True
        ) as session:
            assert session.page is context.new_pages[0]
            assert len(context.listeners["page"]) == 1
        assert context.new_pages[0].closed == 1
        assert context.listeners["page"] == []
        assert context.closed == 0 and playwright.stopped == 1

    _run(scenario())


def test_local_mode_uses_headed_persistent_profile_and_normal_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    executable = tmp_path / "chromium"
    executable.touch()
    context = FakeContext()
    chromium = FakeChromium(context)
    install_fake_playwright(monkeypatch, chromium)

    async def scenario() -> None:
        async with browser.open_session(
            {"provider": "cnu", "browser": {"executable_path": str(executable)}}, data_dir=tmp_path / "data"
        ) as session:
            assert session.mode == "local"
            assert session.context is context
        args = chromium.launch_args
        assert args is not None
        assert args["headless"] is False
        assert Path(args["user_data_dir"]) == tmp_path / "data" / "profile" / "cnu"
        assert args["executable_path"] == str(executable)
        assert context.pages[0].cdp_session.commands == [
            ("Network.setUserAgentOverride", {"userAgent": browser.NORMAL_CHROME_USER_AGENT})
        ]
        assert context.closed == 1

    _run(scenario())


@pytest.mark.parametrize("operation", ["assignments.sync", "lectures.sync", "assignments.fetch", "notices.fetch"])
def test_guarded_session_removes_popup_listener_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    executable = tmp_path / "chromium"
    executable.touch()
    context = FakeContext()
    install_fake_playwright(monkeypatch, FakeChromium(context))

    async def scenario() -> None:
        async with browser.open_session(
            {"browser": {"executable_path": str(executable)}},
            data_dir=tmp_path,
            operation=operation,
        ) as session:
            assert len(context.listeners["page"]) == 1
            await browser.settle_sso_popups(session)
        assert context.listeners["page"] == []

    _run(scenario())


def test_existing_sso_tab_is_not_treated_as_a_session_popup() -> None:
    async def scenario() -> None:
        context = FakeContext()
        page = FakePage(context)
        page.url = "https://lms.invalid/SSOServiceLogin"
        context.pages.append(page)
        popups, pending, remove = browser._track_sso_popups(context)
        assert not popups and not pending
        await browser.settle_sso_popups(browser.BrowserSession(page, context, "cdp", popups, pending))
        remove()
        assert context.listeners["page"] == []

    _run(scenario())


def test_profiled_session_orders_bounded_browser_phases_and_keeps_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    executable = tmp_path / "chromium"
    executable.touch()
    install_fake_playwright(monkeypatch, FakeChromium())
    recorder = SpanRecorder(enabled=True, scope=("lectures",))

    async def scenario() -> None:
        with browser.profile_context(recorder):
            async with browser.open_session(
                {"browser": {"executable_path": str(executable)}}, data_dir=tmp_path, headless=True
            ):
                pass

    _run(scenario())
    report = recorder.finish()
    assert report is not None
    assert [span["phase"] for span in report["spans"]] == [
        "lock",
        "playwright",
        "launch-connect",
        "user-agent",
        "teardown",
        "wait",
    ]
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("campusctl-profile: ")
    assert str(tmp_path) not in output.err


def test_config_headless_does_not_opt_in_legacy_session_without_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    chromium = FakeChromium()
    install_fake_playwright(monkeypatch, chromium)

    async def scenario() -> None:
        async with browser.open_session({"browser": {"headless": True}}, data_dir=tmp_path):
            pass

    _run(scenario())
    assert chromium.launch_args is not None
    assert chromium.launch_args["headless"] is False
    assert chromium.context.closed == 1


def test_local_headless_mode_is_opt_in_and_skips_display_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "chromium"
    executable.touch()
    context = FakeContext()
    chromium = FakeChromium(context)
    install_fake_playwright(monkeypatch, chromium)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(browser, "_check_display", lambda: pytest.fail("headless mode checked for a display"))

    async def scenario() -> None:
        async with browser.open_session(
            {"provider": "cnu", "browser": {"executable_path": str(executable)}},
            data_dir=tmp_path / "data",
            headless=True,
        ) as session:
            assert session.mode == "local"
        assert chromium.launch_args is not None
        assert chromium.launch_args["headless"] is True
        assert context.closed == 1

    _run(scenario())


def test_cdp_rejects_headless_before_creating_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    async def scenario() -> None:
        with pytest.raises(CampusError) as caught:
            async with browser.open_session(
                _cdp_config("http://127.0.0.1:9223/json/version"), data_dir=data_dir, headless=True
            ):
                pytest.fail("CDP headless session should be rejected")
        assert caught.value.code == "headless-unavailable"

    _run(scenario())
    assert not data_dir.exists()


def test_cleanup_hang_does_not_mask_primary_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(browser, "CLEANUP_TIMEOUT_SECONDS", 1)
    context = FakeContext(close_wait=True)
    install_fake_playwright(monkeypatch, FakeChromium(context))

    async def scenario() -> None:
        with pytest.raises(ValueError, match="primary failure"):
            async with browser.open_session({}, data_dir=tmp_path):
                raise ValueError("primary failure")

    _run(scenario())


def test_cleanup_cancellation_does_not_mask_primary_or_skip_driver_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":99")
    context = FakeContext(close_cancel=True)
    playwright = install_fake_playwright(monkeypatch, FakeChromium(context))

    async def scenario() -> None:
        with pytest.raises(ValueError, match="primary failure"):
            async with browser.open_session({}, data_dir=tmp_path):
                raise ValueError("primary failure")

    _run(scenario())
    assert context.closed == 1
    assert playwright.stopped == 1


def test_cdp_websocket_port_is_reused_for_localhost_endpoint() -> None:
    async def scenario() -> None:
        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            body = json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/id"}).encode()
            chunk = f"{len(body):X}\r\n".encode() + body + b"\r\n0\r\n\r\n"
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + chunk)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            ws_url = await browser.resolve_cdp_ws_url(f"http://127.0.0.1:{port}/json/version")
            assert ws_url == f"ws://127.0.0.1:{port}/devtools/browser/id"
        finally:
            server.close()
            await server.wait_closed()

    _run(scenario())


def test_cdp_resolver_total_timeout_closes_slow_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "PROTOCOL_TIMEOUT_SECONDS", 2)

    async def scenario() -> None:
        response_sent = asyncio.Event()
        client_disconnected = asyncio.Event()

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\nConnection: close\r\n\r\n")
            await writer.drain()
            response_sent.set()
            try:
                if await reader.read() == b"":
                    client_disconnected.set()
            except ConnectionError:
                client_disconnected.set()
            finally:
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            request = asyncio.create_task(browser.resolve_cdp_ws_url(f"http://127.0.0.1:{port}/json/version"))
            await asyncio.wait_for(response_sent.wait(), timeout=10)
            with pytest.raises(TimeoutError):
                await request
            await asyncio.wait_for(client_disconnected.wait(), timeout=5)
        finally:
            server.close()
            await server.wait_closed()

    _run(scenario())


def test_cdp_response_size_is_limited_and_https_endpoints_are_rejected() -> None:
    async def scenario() -> None:
        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Length: {browser.MAX_CDP_RESPONSE_BYTES + 1}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(ValueError, match="too large"):
                await browser.resolve_cdp_ws_url(f"http://127.0.0.1:{port}/json/version")
            with pytest.raises(ValueError, match="HTTP"):
                await browser.resolve_cdp_ws_url(f"https://127.0.0.1:{port}/json/version")
        finally:
            server.close()
            await server.wait_closed()

    _run(scenario())


@pytest.mark.parametrize("mode", ["local", "cdp"])
def test_owned_lock_retains_exclusion_through_cleanup_and_publication(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = FakeContext()
    existing = FakePage(context)
    context.pages.append(existing)
    playwright = install_fake_playwright(monkeypatch, FakeChromium(context))
    config: dict[str, Any] = {"browser": {"lock_path": str(tmp_path / "shared.lock")}}
    if mode == "cdp":
        config["browser"]["cdp_endpoint"] = "http://127.0.0.1:9223/json/version"

        async def resolver(_endpoint: str) -> str:
            return "ws://127.0.0.1:9223/devtools/browser/synthetic"

        monkeypatch.setattr(browser, "resolve_cdp_ws_url", resolver)
    else:
        executable = tmp_path / "chromium"
        executable.touch()
        config["browser"]["executable_path"] = str(executable)

    async def scenario() -> None:
        published: list[bool] = []
        with browser.session_lock(config, data_dir=tmp_path):
            async with browser.open_session(config, data_dir=tmp_path, headless=mode != "cdp"):
                with pytest.raises(RuntimeError, match="already active"):
                    async with browser.open_session(config, data_dir=tmp_path, headless=mode != "cdp"):
                        pass
            assert playwright.stopped == 1
            assert context.closed == (1 if mode == "local" else 0)
            assert existing.closed == 0
            with pytest.raises(CampusError) as caught, exclusive_lock(tmp_path / "shared.lock"):
                pass
            assert caught.value.code == "session-busy"
            published.append(True)
        with exclusive_lock(tmp_path / "shared.lock"):
            assert published == [True]

    _run(scenario())


def test_inherited_lock_lease_expires_and_child_cannot_bypass_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"browser": {"lock_path": str(tmp_path / "shared.lock")}}
    monkeypatch.setattr(browser, "PLAYWRIGHT_FACTORY", lambda: pytest.fail("Busy lock must precede browser setup"))

    async def scenario() -> None:
        released = asyncio.Event()

        async def inherited_child() -> str:
            await released.wait()
            with pytest.raises(CampusError) as caught:
                async with browser.open_session(config, data_dir=tmp_path, headless=True):
                    pytest.fail("Child must not borrow an expired lock lease")
            return caught.value.code

        with browser.session_lock(config, data_dir=tmp_path):
            child = asyncio.create_task(inherited_child())
            with pytest.raises(CampusError) as caught:
                await asyncio.create_task(browser.open_session(config, data_dir=tmp_path, headless=True).__aenter__())
            assert caught.value.code == "session-busy"
        with exclusive_lock(tmp_path / "shared.lock"):
            released.set()
            assert await child == "session-busy"

    _run(scenario())


def test_sibling_tasks_cannot_share_an_owned_session_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "chromium"
    executable.touch()
    install_fake_playwright(monkeypatch, FakeChromium())
    config = {"browser": {"lock_path": str(tmp_path / "shared.lock"), "executable_path": str(executable)}}

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def first() -> None:
            async with browser.open_session(config, data_dir=tmp_path, headless=True):
                entered.set()
                await release.wait()

        task = asyncio.create_task(first())
        await entered.wait()
        with pytest.raises(CampusError) as caught:
            async with browser.open_session(config, data_dir=tmp_path, headless=True):
                pytest.fail("Second task must not bypass the first task's lock")
        assert caught.value.code == "session-busy"
        release.set()
        await task

    _run(scenario())


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as manager:
        return Path(manager.chromium.executable_path).is_file()


@pytest.mark.chromium
def test_persistent_profile_reopens_synthetic_cookie_until_server_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not _chromium_available():
        pytest.skip("local Playwright Chromium not installed")
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = "synthetic-session"
    valid = {token}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            cookie = self.headers.get("Cookie", "")
            has_cookie = f"session={token}" in cookie
            if has_cookie and token in valid:
                body = b"<a data-act='moveLecture'>Synthetic course</a>"
                extra = None
            else:
                body = b"<form><input name='user_id'></form>"
                extra = None if has_cookie else f"session={token}; Path=/; Max-Age=3600"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if extra is not None:
                self.send_header("Set-Cookie", extra)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    monkeypatch.setattr(
        "campusctl.providers.cnu.login.get_credentials",
        lambda _config: (_ for _ in ()).throw(AssertionError("credentials must not be read")),
    )

    async def scenario() -> None:
        from campusctl.providers.cnu.login import ensure_logged_in

        config = {"browser": {"headless": True}}
        try:
            async with browser.open_session(config, data_dir=tmp_path, operation="materials.download") as session:
                await session.page.goto(url)
                await session.page.reload()
                assert await session.page.locator("[data-act='moveLecture']").count() == 1
            async with browser.open_session(config, data_dir=tmp_path, operation="materials.download") as session:
                await ensure_logged_in(
                    session.page,
                    {},
                    target_url=url,
                    expected_selector="[data-act='moveLecture']",
                    timeout_ms=5000,
                )
                assert await session.page.locator("input[name='user_id']").count() == 0
            valid.clear()
            async with browser.open_session(config, data_dir=tmp_path, operation="materials.download") as session:
                await session.page.goto(url)
                assert await session.page.locator("input[name='user_id']").count() == 1
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    _run(scenario())


@pytest.mark.chromium
def test_cdp_attachment_preserves_unrelated_tabs(tmp_path: Path) -> None:
    if not _chromium_available():
        pytest.skip("local Playwright Chromium not installed")
    profile = tmp_path / "external-profile"
    port_file = profile / "DevToolsActivePort"

    async def scenario() -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            external = await playwright.chromium.launch_persistent_context(
                str(profile), headless=True, args=["--remote-debugging-port=0"]
            )
            try:
                deadline = asyncio.get_running_loop().time() + 5
                while not port_file.exists():
                    if asyncio.get_running_loop().time() >= deadline:
                        pytest.fail("external CDP endpoint did not start")
                    await asyncio.sleep(0.1)
                port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
                await browser.resolve_cdp_ws_url(f"http://127.0.0.1:{port}/json/version")
                unrelated = await external.new_page()
                await unrelated.goto("data:text/html,<title>Unrelated</title><p>Keep</p>")
                config = {"browser": {"cdp_endpoint": f"http://127.0.0.1:{port}/json/version"}}
                async with browser.open_session(
                    config, data_dir=tmp_path, operation="assignments.fetch", require_owned_page=True
                ) as session:
                    assert session.mode == "cdp"
                    await session.page.goto("data:text/html,<title>Owned</title>")
                assert unrelated.is_closed() is False
                assert await unrelated.title() == "Unrelated"
            finally:
                await external.close()

    _run(scenario())
