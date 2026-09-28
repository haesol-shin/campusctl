"""Keep browser CI shards complete and reject silently skipped Chromium tests."""

import os
from pathlib import Path

import pytest

# Measured call seconds for the real-browser sync cases (2026-09-28, local Chromium).
# Longest-processing-time assignment keeps the 9–17s cases on separate runners.
_SYNC_SECONDS = {
    "test_first_course_section_redirect_fails_only_that_course": 17.26,
    "test_failed_task_response_retains_only_that_courses_previous_assignment": 15.53,
    "test_incomplete_archive_list_retains_only_failed_courses_previous_row": 15.47,
    "test_seven_course_sync_loads_external_assets_and_survives_dead_loopback_telemetry": 13.03,
    "test_external_active_request_is_not_intercepted[POST-fetch]": 12.13,
    "test_external_active_request_is_not_intercepted[GET-xhr]": 11.54,
    "test_external_active_request_is_not_intercepted[GET-fetch]": 11.45,
    "test_single_course_board_failure_retains_prior_row_and_other_courses_complete": 11.22,
    "test_seven_courses_one_session_and_full_normalized_catalogs": 11.19,
    "test_first_entry_topbar_mismatch_retains_old_row_and_continues_other_courses": 10.27,
    "test_lecture_section_requires_its_own_committed_course_page[skip_course_navigation]": 9.37,
    "test_first_course_todo_failure_preserves_notice_and_continues_archive": 8.43,
    "test_lecture_extraction_failure_retains_only_failed_courses_prior_row": 6.18,
    "test_filtered_selection_and_unknown_course_do_not_claim_full_enrollment": 5.86,
    "test_partial_cli_exit_reflects_paginated_notice_and_committed_material": 5.76,
    "test_second_domain_atomic_write_failure_reports_only_committed_truth": 4.73,
    "test_document_commit_spans_exclude_all_four_collector_phases": 4.71,
    "test_delayed_entry_and_section_topbars_still_bind_the_selected_course": 4.50,
    "test_later_invalid_catalog_blocks_all_publication_and_names_later_domain": 4.21,
    "test_standalone_provider_wrapper_preserves_selection_and_rows[notices]": 4.16,
    "test_cli_profile_stderr_carries_failed_check_without_identifiers": 3.41,
    "test_standalone_provider_wrapper_preserves_selection_and_rows[materials]": 3.25,
    "test_assignment_sync_ignores_outstanding_unrelated_request": 3.10,
    "test_failed_todo_navigation_restores_roster_before_archive_menu": 2.55,
    "test_catalogs_are_published_only_after_browser_session_closes": 2.53,
    "test_lecture_section_requires_its_own_committed_course_page[wrong_course_topbar]": 2.39,
    "test_standalone_provider_wrapper_preserves_selection_and_rows[lectures]": 2.30,
    "test_standalone_provider_wrapper_preserves_selection_and_rows[assignments]": 2.27,
    "test_discovery_failure_preserves_rows_and_marks_every_catalog_unknown": 1.93,
}


def _weight(item: pytest.Item) -> float:
    if item.nodeid.startswith("tests/test_sync_all.py::"):
        return _SYNC_SECONDS.get(item.nodeid.split("::", 1)[1], 5.0)
    return 2.0 if item.get_closest_marker("chromium") else 0.2


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard-id", type=int, help="One-based browser CI shard number")
    parser.addoption("--num-shards", type=int, help="Total browser CI shard count")


def _shard_for(item: pytest.Item, loads: list[float]) -> int:
    shard = min(range(len(loads)), key=lambda index: (loads[index], index))
    loads[shard] += _weight(item)
    return shard


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard_id = config.getoption("--shard-id")
    num_shards = config.getoption("--num-shards")
    if shard_id is None and num_shards is None:
        return
    if shard_id is None or num_shards is None or num_shards < 1 or not 1 <= shard_id <= num_shards:
        raise pytest.UsageError("--shard-id and --num-shards require 1 <= shard-id <= num-shards")
    loads = [0.0] * num_shards
    # Longest first, with nodeid tie breaks independent of discovery order.
    assignment = {
        item.nodeid: _shard_for(item, loads)
        for item in sorted(items, key=lambda item: (-_weight(item), item.nodeid))
    }
    selected = [item for item in items if assignment[item.nodeid] == shard_id - 1]
    deselected = [item for item in items if assignment[item.nodeid] != shard_id - 1]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


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
