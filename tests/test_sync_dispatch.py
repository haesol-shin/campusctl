"""Synthetic CLI dispatch tests; browser and LMS never run."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from campusctl import cli
from campusctl import sync as sync_module
from campusctl.envelope import CampusError


def _call(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def test_sync_rejects_unknown_domains_before_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    for bad in ("", "all", "lectures,", "unknown", "notices,,materials"):
        code, response = _call(["sync", "--only", bad, "--json"], capsys)
        assert code == 2 and response["errors"][0]["code"] == "unsupported-domain"


def test_global_mode_refuses_before_sync_and_legacy_flag_is_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {"browser": {"cdp_endpoint": "http://browser.invalid:9222"}})
    monkeypatch.setattr(sync_module, "sync_all", lambda *args, **kwargs: pytest.fail("sync dispatched"))
    for argv, code, expected in (
        (["--headless", "sync", "--json"], 2, "headless-unavailable"),
        (["sync", "--headless", "--json"], 2, "usage-error"),
        (["--headless", "--headed", "sync", "--json"], 2, "usage-error"),
    ):
        result, response = _call(argv, capsys)
        assert result == code and response["errors"][0]["code"] == expected


def test_profile_emits_single_safe_stderr_line_without_polluting_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})

    async def collect(*args: object, **kwargs: object):
        return {"assignments": sync_module.DomainOutcome(result={"courses": 1})}

    monkeypatch.setattr(sync_module, "sync_all", collect)
    code = cli.main(["--profile", "sync", "--only", "assignments", "--json"])
    captured = capsys.readouterr()
    assert code == 0 and json.loads(captured.out)["result"] == {"courses": 1}
    lines = captured.err.splitlines()
    assert len(lines) == 1 and lines[0].startswith("campusctl-profile: ")
    record = json.loads(lines[0].removeprefix("campusctl-profile: "))
    assert record["scope"] == ["assignments"] and record["outcome"] == "ok"
    code, response = _call(["--profile", "status", "--json"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "usage-error"


def test_fresh_cache_sync_accepts_live_full_id_but_rejects_name_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})

    async def collect(config: dict, root: Path, domains: tuple[str, ...], course_id: str | None, **kwargs: object):
        if course_id != "course-a":
            raise CampusError("course-not-found", "Not enrolled.", status="user-action")
        return {"assignments": sync_module.DomainOutcome(result={"courses": 1})}

    from campusctl.course_selection import resolve_course

    def discover(selector: str, config: dict, root: Path, domain: str, *, ids_only: bool, mode: bool) -> str:
        assert domain == "assignments" and root == tmp_path and not mode
        return resolve_course(selector, [{"course_id": "course-a", "label": "Alpha"}], ids_only=ids_only)

    monkeypatch.setattr(cli, "_discover_live_course", discover)

    monkeypatch.setattr(sync_module, "sync_all", collect)
    code, response = _call(["sync", "--only", "assignments", "--course", "course-a", "--json"], capsys)
    assert code == 0 and response["result"]["courses"] == 1
    code, response = _call(["sync", "--only", "assignments", "--course", "course", "--json"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "course-id-required"
    code, response = _call(["sync", "--only", "assignments", "--course", "1", "--json"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "course-index-unavailable"


def test_json_cached_name_and_index_refused_before_multi_domain_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl.catalog import write_catalog

    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-09-25T12:00:00Z",
            "courses": [{"course_id": "course-a", "label": "Alpha", "class_no": None}],
            "lectures": [],
        },
        tmp_path / "catalog" / "lectures.json",
    )
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "_discover_live_course", lambda *args, **kwargs: pytest.fail("must not discover"))
    for selector, expected in (("Alph", "course-id-required"), ("1", "course-index-unavailable")):
        code, response = _call(["sync", "--course", selector, "--json"], capsys)
        assert code == 2 and response["errors"][0]["code"] == expected


def test_fresh_live_discovery_uses_selected_operation_before_domain_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from contextlib import asynccontextmanager

    from campusctl import browser
    from campusctl.providers.cnu import courses

    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    operations: list[str] = []

    @asynccontextmanager
    async def session(config: dict, *, data_dir: Path, headless: bool, operation: str):
        assert data_dir == tmp_path and not headless
        operations.append(operation)
        yield SimpleNamespace(page=object())

    async def discover(page: object, config: dict):
        return [{"course_id": "course-a", "label": "Alpha", "class_no": None}]

    async def collect(config: dict, root: Path, domains: tuple[str, ...], course_id: str | None, **kwargs: object):
        assert course_id == "course-a"
        return {"assignments": sync_module.DomainOutcome(result={"courses": 1})}

    monkeypatch.setattr(browser, "open_session", session)
    monkeypatch.setattr(courses, "discover_courses", discover)
    monkeypatch.setattr(sync_module, "sync_all", collect)
    code, response = _call(["sync", "--only", "assignments", "--course", "course-a", "--json"], capsys)
    assert code == 0 and response["result"]["courses"] == 1
    assert operations == ["assignments.sync"]


def test_uncached_numeric_full_id_wins_over_json_index_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl.course_selection import resolve_course

    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    calls: list[str | None] = []

    def discover(selector: str, config: dict, root: Path, domain: str, *, ids_only: bool, mode: bool):
        return resolve_course(selector, [{"course_id": "123", "label": "Numbers"}], ids_only=ids_only)

    async def collect(config: dict, root: Path, domains: tuple[str, ...], course_id: str | None, **kwargs: object):
        calls.append(course_id)
        return {"assignments": sync_module.DomainOutcome(result={"courses": 1})}

    monkeypatch.setattr(cli, "_discover_live_course", discover)
    monkeypatch.setattr(sync_module, "sync_all", collect)
    code, response = _call(["sync", "--only", "assignments", "--course", "123", "--json"], capsys)
    assert code == 0 and response["result"]["courses"] == 1 and calls == ["123"]
