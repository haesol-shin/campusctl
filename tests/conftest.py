"""Ensure the browser CI job cannot silently skip real Chromium coverage."""

import os
from pathlib import Path

import pytest

_REQUIRED = os.environ.get("CAMPUSCTL_REQUIRE_CHROMIUM") == "1"

_SKIPPED_CHROMIUM: list[str] = []


def pytest_collection_finish(session: pytest.Session) -> None:
    if not _REQUIRED:
        return
    marked = [item for item in session.items if item.get_closest_marker("chromium")]
    if not marked:
        raise pytest.UsageError("CAMPUSCTL_REQUIRE_CHROMIUM: no chromium-marked tests collected")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            if not executable.is_file():
                raise pytest.UsageError("CAMPUSCTL_REQUIRE_CHROMIUM: Playwright Chromium executable is missing")
    except ImportError as exc:
        raise pytest.UsageError("CAMPUSCTL_REQUIRE_CHROMIUM: Playwright is missing") from exc


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _REQUIRED and report.skipped and "chromium" in report.keywords:
        _SKIPPED_CHROMIUM.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _REQUIRED and _SKIPPED_CHROMIUM:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    for nodeid in _SKIPPED_CHROMIUM:
        terminalreporter.write_line(f"CAMPUSCTL_REQUIRE_CHROMIUM: marked test skipped: {nodeid}", red=True)
