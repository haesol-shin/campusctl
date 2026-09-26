"""Private roster failure evidence from a synthetic local browser page."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from campusctl.providers.cnu.roster_diagnostics import (
    _location,
    capture_roster_failure,
    start_roster_requests,
    stop_roster_requests,
)


def test_real_browser_roster_timeout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    secret = "fixture-private-label"

    async def scenario() -> Path:
        async with async_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Playwright Chromium is not installed")
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()

                async def fixture(route: object) -> None:
                    if "/api/v1/1234567890123456" in route.request.url:
                        await route.abort("failed")
                        return
                    if "/api/v1/list" in route.request.url:
                        await route.fulfill(status=503, body="unavailable")
                        return
                    await route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=f'<html><body><div data-act="loading">{secret}</div></body></html>',
                    )

                await page.route("**/*", fixture)
                trace = start_roster_requests(page, headless=True)
                try:
                    await page.goto("https://fixture.invalid/roster/12345/abcdef0123456789?token=credential")
                    await page.evaluate(
                        """() => Promise.all([
                            fetch('/api/v1/1234567890123456?token=credential').catch(() => null),
                            fetch('/api/v1/list').catch(() => null)
                        ])"""
                    )
                    trace.step = "wait"
                    with pytest.raises(PlaywrightTimeoutError):
                        await page.wait_for_selector('[data-act="moveLecture"]', timeout=80)
                    result = await capture_roster_failure(
                        page, operation="lectures.sync", step=trace.step, elapsed_s=trace.elapsed_s, root=tmp_path
                    )
                    assert result is not None
                    return result
                finally:
                    stop_roster_requests(page)
            finally:
                await browser.close()

    target = asyncio.run(scenario())
    record = json.loads(target.read_text())
    assert record["schema_version"] == 1
    assert record["timestamp"]
    assert record["operation"] == "lectures.sync"
    assert record["step"] == "wait"
    assert record["elapsed_s"] >= 0
    assert record["headless"] is True
    assert record["page"] == {"host_class": "other", "path": "/roster/:id/:id"}
    assert record["ready_state"] == "complete"
    assert record["password_inputs"] == 0
    assert record["course_links"] == 0
    assert record["total_nodes"] > 0
    assert record["data_acts"] == ["other"]
    assert record["iframes"] == 0
    assert record["visible_modals"] == 0
    assert record["other_pages"] == []
    assert record["in_flight"] == []
    assert record["failed_requests"][0]["path"] == "/api/v1/:id"
    assert record["error_responses"][0]["status"] == 503
    assert record["error_responses"][0]["path"] == "/api/v1/list"
    assert secret not in target.read_text()
    assert "credential" not in target.read_text()
    assert "12345" not in target.read_text()
    assert capsys.readouterr().out == ""
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
        assert target.parent.stat().st_mode & 0o777 == 0o700


def test_retention_and_closed_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import campusctl.providers.cnu.roster_diagnostics as diagnostics

    # Retention is under test here, not the write deadline; slow CI disks can exceed it.
    monkeypatch.setattr(diagnostics, "_WRITE_TIMEOUT_S", 30.0)

    class ClosedPage:
        url = "about:blank"

        async def evaluate(self, script: str) -> None:
            raise RuntimeError("page closed")

        @property
        def context(self) -> object:
            raise RuntimeError("page closed")

    async def scenario() -> None:
        page = ClosedPage()
        for _ in range(23):
            assert await capture_roster_failure(
                page, operation="lectures.sync", step="wait", elapsed_s=1, root=tmp_path
            )

    asyncio.run(scenario())
    assert len(list((tmp_path / "diagnostics").glob("roster-*.json"))) == 20
    assert capsys.readouterr().out == ""
    assert _location("https://fixture.invalid/a/1234567890123456/Zm9vYmFyMTIzNDU2Nzg5?token=hidden") == {
        "host_class": "other",
        "path": "/:id/:id/:id",
    }


def test_stalled_disk_write_returns_promptly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    import campusctl.providers.cnu.roster_diagnostics as diagnostics

    release = threading.Event()

    def stalled(*args: object) -> Path:
        release.wait(10)
        raise OSError("disk stalled")

    monkeypatch.setattr(diagnostics, "_write_record", stalled)

    class ClosedPage:
        url = "about:blank"

        async def evaluate(self, script: str) -> None:
            raise RuntimeError("page closed")

    started = time.monotonic()
    try:
        result = asyncio.run(
            capture_roster_failure(ClosedPage(), operation="notices.sync", step="wait", elapsed_s=1, root=tmp_path)
        )
    finally:
        release.set()
    assert result is None
    assert time.monotonic() - started < 3


def test_prune_failure_keeps_saved_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import campusctl.providers.cnu.roster_diagnostics as diagnostics

    directory = tmp_path / "diagnostics"
    directory.mkdir()
    for index in range(20):
        (directory / f"roster-20000101T0000{index:02d}000000Z-000000-lectures-sync.json").write_text("{}\n")
    real_unlink = Path.unlink

    def locked_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.parent == directory and self.name.startswith("roster-2000"):
            raise PermissionError("file in use")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    target = diagnostics._write_record({"schema_version": 1}, "lectures.sync", tmp_path)
    assert target.exists()
    assert len(list(directory.glob("roster-*.json"))) == 21
