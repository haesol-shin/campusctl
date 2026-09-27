from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter

import pytest

from campusctl import browser

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


@pytest.mark.chromium
def test_popup_settles_and_roster_links_render(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[str] = []
    server, thread = _fixture_server(posts)
    lms = f"http://lms.invalid:{server.server_port}"
    roster = f"{lms}{ROSTER_PATH}"
    sso = f"{lms}{SSO_PATH}"
    monkeypatch.setattr(browser, "_roster_page_url", lambda url: url == roster)

    async def scenario() -> None:
        manager, instance, context = await _browser_context(fixture_port=server.server_port)
        try:
            popups, pending, remove = browser._track_sso_popups(context)
            try:
                page = await context.new_page()
                async with page.expect_popup() as popup_info:
                    await page.goto(roster)
                popup = await popup_info.value
                await popup.wait_for_url(sso)
                session = browser.BrowserSession(page, context, "local", popups, pending)
                await browser.settle_sso_popups(session, domain="assignments")
                assert posts == [f"provider.invalid:{server.server_port}"]
                await page.goto(roster)
                await page.wait_for_selector('[data-act="moveLecture"]', timeout=1000)
                before = perf_counter()
                await browser.settle_sso_popups(session, domain="assignments")
                assert perf_counter() - before < 0.1
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


@pytest.mark.chromium
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
