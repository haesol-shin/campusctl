from __future__ import annotations

import asyncio
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest

from campusctl import browser
from campusctl.providers.cnu.ui_policy import UiRequestDiagnostics, UiRequestPolicy, install_ui_request_interceptor

LMS = "https://lms.invalid"
PROVIDER = "https://provider.invalid"
ROSTER = f"{LMS}/std/myLecture"
SSO = f"{LMS}/SSOServiceLogin"


async def _browser_context():
    from playwright.async_api import async_playwright

    manager = await async_playwright().start()
    if not Path(manager.chromium.executable_path).is_file():
        await manager.stop()
        pytest.skip("Playwright Chromium is not installed")
    instance = await manager.chromium.launch(headless=True)
    context = await instance.new_context()
    return manager, instance, context


def test_popup_settles_before_guard_and_roster_links_render(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        manager, instance, context = await _browser_context()
        monkeypatch.setattr(browser, "_sso_page_url", lambda url: url.startswith(SSO))
        monkeypatch.setattr(browser, "_roster_page_url", lambda url: url.startswith(ROSTER))
        posts: list[str] = []

        async def fixture(route):
            request = route.request
            if request.url == ROSTER:
                await route.fulfill(
                    content_type="text/html",
                    body="""<!doctype html><script>
                    if (sessionStorage.getItem('ready')) {
                        document.write('<a data-act="moveLecture" href="/std/course">Course</a>');
                    } else if (!sessionStorage.getItem('started')) {
                        sessionStorage.setItem('started', '1');
                        window.addEventListener('message', () => sessionStorage.setItem('ready', '1'));
                        window.open('/SSOServiceLogin');
                    }
                </script>""",
                )
            elif request.url == SSO:
                await route.fulfill(
                    content_type="text/html",
                    body="""<!doctype html><script>
                    setTimeout(() => {
                        const form = document.createElement('form');
                        form.method = 'POST';
                        form.action = 'https://provider.invalid/Panopto/Pages/Auth/Login.aspx';
                        document.body.append(form);
                        form.submit();
                    }, 400);
                </script>""",
                )
            elif request.url == f"{PROVIDER}/Panopto/Pages/Auth/Login.aspx":
                posts.append(request.method)
                await route.fulfill(
                    content_type="text/html",
                    body="""<!doctype html><script>
                    opener.postMessage('ready', '*');
                    setTimeout(() => window.close(), 2600);
                </script>""",
                )
            else:
                await route.abort()

        await context.route("**/*", fixture)
        popups, pending, remove = browser._track_sso_popups(context)
        try:
            page = await context.new_page()
            async with page.expect_popup():
                await page.goto(ROSTER)
            session = browser.BrowserSession(page, context, "local", popups, pending)
            started = perf_counter()
            await browser.settle_sso_popups(session)
            elapsed = perf_counter() - started
            assert 2.5 <= elapsed < 8.0
            assert posts == ["POST"]
            policy = UiRequestPolicy(
                approved=True,
                origins=frozenset({LMS}),
                routes=(
                    SimpleNamespace(
                        origin=LMS,
                        path="/std/myLecture",
                        operation="assignments.sync",
                        methods=frozenset({"GET"}),
                        query=(),
                        logging_token_reviewed=False,
                        resource_type="",
                    ),
                ),
            )
            await page.goto(ROSTER)
            diagnostics = UiRequestDiagnostics()
            guard = await install_ui_request_interceptor(
                context, policy, operation="assignments.sync", diagnostics=diagnostics
            )
            try:
                await page.wait_for_selector('[data-act="moveLecture"]')
                guard.raise_if_denied()
                assert diagnostics.suppressed_count == 0
            finally:
                await guard.close()
            before = perf_counter()
            await browser.settle_sso_popups(session)
            idle_elapsed = perf_counter() - before
            assert idle_elapsed < 0.1
            print(f"fixture sso-settle active={elapsed:.3f}s idle={idle_elapsed:.6f}s")
        finally:
            remove()
            await context.close()
            await instance.close()
            await manager.stop()

    asyncio.run(scenario())


def test_no_popup_timeout_and_listener_cleanup() -> None:
    async def scenario() -> None:
        manager, instance, context = await _browser_context()
        popups, pending, remove = browser._track_sso_popups(context)
        try:
            page = await context.new_page()
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
