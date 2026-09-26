from __future__ import annotations

import asyncio
import json
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from campusctl import browser
from campusctl.envelope import CampusError
from campusctl.lock import exclusive_lock
from campusctl.profiling import SpanRecorder


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


def _closed_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


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
    endpoint = f"http://127.0.0.1:{_closed_port()}/json/version"
    install_fake_playwright(monkeypatch, FakeChromium())
    monkeypatch.setattr(browser, "PROTOCOL_TIMEOUT_SECONDS", 1)

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
