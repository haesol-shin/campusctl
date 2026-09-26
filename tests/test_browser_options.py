from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from campusctl.browser import open_session
from campusctl.browser_options import (
    HEADLESS_SUPPORT,
    operation_headless_supported,
    preflight_browser_mode,
    resolve_headless,
)
from campusctl.config import load_config, validate_config
from campusctl.envelope import CampusError


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, [], {}])
def test_config_rejects_non_boolean_headless(tmp_path: Path, value: object) -> None:
    with pytest.raises(CampusError) as caught:
        validate_config({"browser": {"headless": value}}, path=tmp_path / "config.toml")
    assert caught.value.code == "config-invalid"
    assert "browser.headless" in caught.value.message


def test_toml_true_false_and_absent_default(tmp_path: Path) -> None:
    for contents, expected in (
        ("", False),
        ("[browser]\nheadless = true\n", True),
        ("[browser]\nheadless = false\n", False),
    ):
        path = tmp_path / "config.toml"
        path.write_text(contents, encoding="utf-8")
        assert resolve_headless(load_config(path)) is expected
    assert resolve_headless({"browser": {"headless": True}}, override=False) is False
    assert resolve_headless({"browser": {"headless": False}}, override=True) is True


def test_pending_operations_refuse_before_lock_or_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import browser

    assert not any(HEADLESS_SUPPORT.values())
    monkeypatch.setattr(browser, "_playwright_manager", lambda: pytest.fail("browser started"))
    data_dir = tmp_path / "data"
    for operation in (*HEADLESS_SUPPORT, "unregistered.operation"):
        assert not operation_headless_supported(operation)
        with pytest.raises(CampusError) as caught:
            preflight_browser_mode({"browser": {"headless": True}}, operation)
        assert caught.value.code == "headless-unavailable"
        assert caught.value.status == "user-action"
        with pytest.raises(CampusError) as caught:
            asyncio.run(open_session({}, data_dir=data_dir, headless=True, operation=operation).__aenter__())
        assert caught.value.code == "headless-unavailable"
        assert not data_dir.exists()
    assert preflight_browser_mode({"browser": {"headless": True}}, "lectures.sync", override=False) is False


def test_cdp_headless_refused_before_browser_or_lock(tmp_path: Path) -> None:
    config = {"browser": {"cdp_endpoint": "http://browser.invalid:9222", "headless": True}}
    # A future approved operation still cannot run headless over CDP.
    from campusctl import browser_options

    original = browser_options.HEADLESS_SUPPORT["lectures.sync"]
    browser_options.HEADLESS_SUPPORT["lectures.sync"] = True
    try:
        with pytest.raises(CampusError) as caught:
            preflight_browser_mode(config, "lectures.sync")
        assert caught.value.code == "headless-unavailable"
        assert "CDP" in caught.value.message
        assert not (tmp_path / "data").exists()
    finally:
        browser_options.HEADLESS_SUPPORT["lectures.sync"] = original


@pytest.mark.skipif(os.name == "nt", reason="no-display assertion is POSIX-only")
def test_actual_local_headless_chromium_without_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.async_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as manager:
        if not Path(manager.chromium.executable_path).is_file():
            pytest.skip("local Playwright Chromium not installed")

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    data_dir = tmp_path / "data"

    async def scenario() -> None:
        async with open_session({}, data_dir=data_dir, headless=True) as session:
            await session.page.goto("data:text/html,<title>Synthetic</title><main>Local fixture</main>")
            assert await session.page.title() == "Synthetic"
            assert await session.page.locator("main").inner_text() == "Local fixture"
            with pytest.raises(CampusError) as caught:
                async with open_session({}, data_dir=data_dir, headless=True):
                    pass
            assert caught.value.code == "session-busy"
        async with open_session({}, data_dir=data_dir, headless=True) as session:
            assert await session.page.evaluate("2 + 3") == 5

    asyncio.run(scenario())
