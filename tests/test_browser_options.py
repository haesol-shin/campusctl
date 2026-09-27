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

    assert HEADLESS_SUPPORT["materials.download"] is True
    monkeypatch.setattr(browser, "_playwright_manager", lambda: pytest.fail("browser started"))
    data_dir = tmp_path / "data"
    for operation in (
        "lectures.sync",
        "assignments.sync",
        "notices.sync",
        "materials.sync",
        "materials.download",
        "assignments.fetch",
        "notices.fetch",
    ):
        assert operation_headless_supported(operation)
        assert preflight_browser_mode({"browser": {"headless": True}}, operation) is True
    pending = ("lectures.play",)
    for operation in (*pending, "unregistered.operation"):
        assert not operation_headless_supported(operation)
        with pytest.raises(CampusError) as caught:
            preflight_browser_mode({"browser": {"headless": True}}, operation)
        assert caught.value.code == "headless-unavailable"
        assert caught.value.status == "user-action"
        with pytest.raises(CampusError) as caught:
            asyncio.run(open_session({}, data_dir=data_dir, headless=True, operation=operation).__aenter__())
        assert caught.value.code == "headless-unavailable"
        with pytest.raises(CampusError) as caught:
            asyncio.run(
                open_session({"browser": {"headless": True}}, data_dir=data_dir, operation=operation).__aenter__()
            )
        assert caught.value.code == "headless-unavailable"
        assert not data_dir.exists()
    assert preflight_browser_mode({"browser": {"headless": True}}, "lectures.play", override=False) is False


def test_approved_download_mode_still_rejects_cdp_before_lock(tmp_path: Path) -> None:
    config = {"browser": {"cdp_endpoint": "http://browser.invalid:9222", "headless": True}}
    assert operation_headless_supported("materials.download")
    with pytest.raises(CampusError) as caught:
        asyncio.run(open_session(config, data_dir=tmp_path / "data", operation="materials.download").__aenter__())
    assert caught.value.code == "headless-unavailable"
    assert "CDP" in caught.value.message
    assert not (tmp_path / "data").exists()


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
        async with open_session(
            {"browser": {"headless": True}}, data_dir=data_dir, operation="materials.download"
        ) as session:
            await session.page.goto("data:text/html,<title>Synthetic</title><main>Local fixture</main>")
            assert await session.page.title() == "Synthetic"
            assert await session.page.locator("main").inner_text() == "Local fixture"
            with pytest.raises(CampusError) as caught:
                async with open_session({}, data_dir=data_dir, headless=True):
                    pass
            assert caught.value.code == "session-busy"
        async with open_session(
            {"browser": {"headless": True}}, data_dir=data_dir, operation="materials.download"
        ) as session:
            assert await session.page.evaluate("2 + 3") == 5

    asyncio.run(scenario())


def test_doctor_exposes_fetch_headless_candidate_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path / "data"))
    from campusctl.cli import doctor_result

    result, _error = doctor_result()
    support = result["browser"]["headless_support"]
    assert support["assignments.fetch"] is True
    assert support["notices.fetch"] is True
    assert support["lectures.play"] is False


@pytest.mark.skipif(os.name == "nt", reason="no-display assertion is POSIX-only")
def test_headless_fetch_publishes_synthetic_details_without_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("playwright.async_api")
    from playwright.sync_api import sync_playwright

    from campusctl.source_package import DetailSnapshot, build_source_package

    with sync_playwright() as manager:
        if not Path(manager.chromium.executable_path).is_file():
            pytest.skip("local Playwright Chromium not installed")

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    async def scenario() -> None:
        cases = (
            ("assignments.fetch", "assignment", "cnu_assignment:course-1:task-101", "task-101"),
            ("notices.fetch", "notice", "cnu_notice:course-1:2026-09-25:1", "notice-1"),
        )
        for operation, kind, entity_id, marker in cases:
            root = tmp_path / kind
            async with open_session({"browser": {"headless": True}}, data_dir=root, operation=operation) as session:
                html = (
                    f"data:text/html,<title>Synthetic</title><main data-selected-id='{marker}'>Selected {marker}</main>"
                )
                await session.page.goto(html)
                selected = await session.page.locator("main").get_attribute("data-selected-id")
                text = await session.page.locator("main").inner_text()
                assert selected == marker
                assert marker in text
                package = await build_source_package(
                    session.page,
                    DetailSnapshot(
                        source_url=f"https://lms.invalid/std/{kind}",
                        provider_native_id=marker,
                        parts=(f"{text}\n",),
                    ),
                    entity_id=entity_id,
                    kind=kind,
                    course_id="course-1",
                    course_label="Synthetic Course",
                    root=root,
                )
            assert package["entity_id"] == entity_id
            assert marker in Path(package["content_path"]).read_text(encoding="utf-8")
            manifest = __import__("json").loads(Path(package["manifest_path"]).read_text(encoding="utf-8"))
            assert manifest["entity_id"] == entity_id
            assert manifest["kind"] == kind

    asyncio.run(scenario())
