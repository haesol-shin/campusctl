from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest

from campusctl import browser
from campusctl.providers.cnu.ui_policy import (
    UiRequestDiagnostics,
    UiRequestInterceptor,
    UiRequestPolicy,
    install_ui_request_interceptor,
)

ROSTER_PATH = "/std/myLecture"
SSO_PATH = "/SSOServiceLogin"
PROVIDER_PATH = "/Panopto/Pages/Auth/Login.aspx"


async def _browser_context(*, fixture_port: int | None = None):
    from playwright.async_api import async_playwright

    manager = await async_playwright().start()
    if not Path(manager.chromium.executable_path).is_file():
        await manager.stop()
        pytest.skip("Playwright Chromium is not installed")
    instance = await manager.chromium.launch(
        headless=True,
        args=(
            ["--no-proxy-server", "--host-resolver-rules=MAP lms.invalid 127.0.0.1,MAP provider.invalid 127.0.0.1"]
            if fixture_port is not None
            else []
        ),
    )
    context = await instance.new_context()
    return manager, instance, context


def _fixture_server(posts: list[str]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == ROSTER_PATH:
                body = """<!doctype html><script>
                    const link = '<a data-act="moveLecture" href="/std/course">Course</a>';
                    if (sessionStorage.getItem('ready')) {
                        document.write(link);
                    } else if (!sessionStorage.getItem('started')) {
                        sessionStorage.setItem('started', '1');
                        window.addEventListener('message', () => {
                            sessionStorage.setItem('ready', '1');
                            document.body.innerHTML = link;
                        });
                        window.open('/SSOServiceLogin');
                    }
                </script>"""
            elif self.path == SSO_PATH:
                body = f"""<!doctype html><script>
                    setTimeout(() => {{
                        const form = document.createElement('form');
                        form.method = 'POST';
                        form.action = 'http://provider.invalid:{self.server.server_port}{PROVIDER_PATH}';
                        document.body.append(form);
                        form.submit();
                    }}, 400);
                </script>"""
            else:
                self.send_error(404)
                return
            self._reply(body)

        def do_POST(self) -> None:
            if self.path != PROVIDER_PATH:
                self.send_error(404)
                return
            posts.append(self.headers["Host"])
            self._reply("""<!doctype html><script>
                opener.postMessage('ready', '*');
                setTimeout(() => window.close(), 2600);
            </script>""")

        def _reply(self, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_popup_settles_before_guard_and_roster_links_render(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[str] = []
    server, thread = _fixture_server(posts)
    lms = f"http://lms.invalid:{server.server_port}"
    provider = f"http://provider.invalid:{server.server_port}"
    roster = f"{lms}{ROSTER_PATH}"
    sso = f"{lms}{SSO_PATH}"
    monkeypatch.setattr(browser, "_roster_page_url", lambda url: url == roster)

    async def fixture_popup(request):
        frame = request.frame
        opener = await frame.page.opener()
        return frame.url == sso and opener is not None and opener.url == roster, frame.page

    monkeypatch.setattr(UiRequestInterceptor, "_sso_popup", lambda self, request: fixture_popup(request))
    policy = UiRequestPolicy(
        approved=True,
        origins=frozenset({lms}),
        routes=(
            SimpleNamespace(
                origin=lms,
                path=ROSTER_PATH,
                operation="assignments.sync",
                methods=frozenset({"GET"}),
                query=(),
                logging_token_reviewed=False,
                resource_type="",
            ),
        ),
        suppress=(
            SimpleNamespace(
                origin=provider,
                path_template=PROVIDER_PATH,
                operation="assignments.sync",
                methods=frozenset({"POST"}),
                name="panopto-sso-popup",
                reason="panopto-sso-popup",
            ),
        ),
    )

    async def scenario() -> None:
        manager, instance, context = await _browser_context(fixture_port=server.server_port)
        try:
            for settle in (False, True):
                if settle:
                    context = await instance.new_context()
                popups, pending, remove = browser._track_sso_popups(context)
                try:
                    page = await context.new_page()
                    async with page.expect_popup() as popup_info:
                        await page.goto(roster)
                    popup = await popup_info.value
                    await popup.wait_for_url(sso)
                    session = browser.BrowserSession(page, context, "local", popups, pending)
                    diagnostics = UiRequestDiagnostics()
                    if settle:
                        started = perf_counter()
                        await browser.settle_sso_popups(session, domain="assignments")
                        elapsed = perf_counter() - started
                        assert 2.5 <= elapsed < 8.0
                        assert posts == [f"provider.invalid:{server.server_port}"]
                    guard = await install_ui_request_interceptor(
                        context, policy, operation="assignments.sync", diagnostics=diagnostics
                    )
                    try:
                        if settle:
                            await page.goto(roster)
                            await page.wait_for_selector('[data-act="moveLecture"]', timeout=1000)
                            assert diagnostics.suppressed_count == 0
                            before = perf_counter()
                            await browser.settle_sso_popups(session, domain="assignments")
                            idle_elapsed = perf_counter() - before
                            assert idle_elapsed < 0.1
                            print(f"fixture sso-settle active={elapsed:.3f}s idle={idle_elapsed:.6f}s")
                        else:
                            await page.wait_for_timeout(800)
                            assert diagnostics.suppressed_reasons["panopto-sso-popup"] == 1
                            await page.goto(roster)
                            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

                            with pytest.raises(PlaywrightTimeoutError):
                                await page.wait_for_selector('[data-act="moveLecture"]', timeout=250)
                            assert len(posts) == 0
                        guard.raise_if_denied()
                    finally:
                        await guard.close()
                finally:
                    remove()
                    await context.close()
        finally:
            await instance.close()
            await manager.stop()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_no_popup_timeout_and_listener_cleanup() -> None:
    async def scenario() -> None:
        manager, instance, context = await _browser_context()
        sso = "https://lms.invalid/SSOServiceLogin"
        await context.route(sso, lambda route: route.fulfill(content_type="text/html", body="<body>unrelated</body>"))
        unrelated = await context.new_page()
        await unrelated.goto(sso)
        popups, pending, remove = browser._track_sso_popups(context)
        try:
            page = await context.new_page()
            await page.goto(sso)
            session = browser.BrowserSession(page, context, "local", popups, pending)
            before = perf_counter()
            await browser.settle_sso_popups(session)
            assert perf_counter() - before < 0.1
            popup = await context.new_page()
            popups.add(popup)
            before = perf_counter()
            await browser.settle_sso_popups(session, timeout=0.15)
            assert 0.12 <= perf_counter() - before < 0.5
            assert not popup.is_closed()
            remove()
            assert not context._impl_obj.listeners("page")
        finally:
            await context.close()
            await instance.close()
            await manager.stop()

    asyncio.run(scenario())
