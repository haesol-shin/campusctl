"""Fixture-backed public CLI and foundation hook integration for assignments."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from test_assignments_provider import fixture_rows, old_catalog, setup

from campusctl import cli
from campusctl.commands import assignments, discover_domain_modules
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.lock import exclusive_lock
from campusctl.providers.cnu import assignments as provider


def _call(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = cli.main([*argv, "--json"])
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def _install(monkeypatch: pytest.MonkeyPatch, root: Path, scenarios: dict[str, Any]) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(root))
    monkeypatch.setattr(cli, "load_config", lambda: {"provider": "cnu"})
    import asyncio

    asyncio.run(setup(monkeypatch, root, scenarios))
    fake_session = provider.open_session

    @asynccontextmanager
    async def locked_session(*args: Any, **kwargs: Any):
        with exclusive_lock(root / "session.lock"):
            async with fake_session(*args, **kwargs) as session:
                yield session

    monkeypatch.setattr(provider, "open_session", locked_session)


def test_discovery_gate_and_assignment_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl import commands

    assert discover_domain_modules()["assignments"] is assignments
    assert cli.CAPABILITIES["assignments"] == ["list"]
    assert "policy" not in cli.CAPABILITIES
    assert cli.build_parser().parse_args(["assignments", "list"]).command == "assignments"
    # An explicitly unapproved complete module is ignored even when it is importable.
    rejected = tmp_path / "unreviewed.py"
    rejected.write_text(
        "CAPABILITY = {'commands': ['list'], 'policy': {'approved': False, "
        "'read_only_evidence': None, 'origins': [], 'routes': [], "
        "'allowed_media': [], 'max_bytes': None}}\n"
        "def register(subparsers): pass\n"
        "def dispatch(args): pass\n"
        "def render(command, result, width): pass\n"
        "async def sync(config, root, course_id, *, headless=False): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "__path__", [*commands.__path__, str(tmp_path)])
    assert "unreviewed" not in discover_domain_modules()
    old_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    code, response = _call(["assignments", "list", "--course", "course-a"], capsys)
    assert code == 0
    assert response["result"]["assignments"][0]["task_id"] == "TB_L_REPORT1"
    code, response = _call(["assignments", "list", "--course", "unknown"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "course-id-required"


def test_human_assignment_renderer_has_full_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl import presentation

    old_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr(presentation.shutil, "get_terminal_size", lambda **_kw: os.terminal_size((12, 24)))
    assert cli.main(["assignments", "list", "--course", "course-a"]) == 0
    output = capsys.readouterr().out
    assert "Old A — Not submitted — Due: —" in output
    assert "cnu_assignment:course-a:TB_L_REPORT1\n" in output
    monkeypatch.setattr(
        cli, "load_config", lambda: {"provider": "cnu", "browser": {"cdp_endpoint": "http://browser.invalid:9222"}}
    )
    assert cli.main(["--headless", "sync", "--only", "assignments"]) == 2
    assert "Headless" in capsys.readouterr().out
    _install(monkeypatch, tmp_path, {"course-a": {"rows": fixture_rows()}})
    assert cli.main(["sync", "--only", "assignments", "--course", "course-a"]) == 0
    assert "Synced 1 course, 5 assignments." in capsys.readouterr().out


def test_published_assignment_cli_contract() -> None:
    text = (Path(__file__).parents[1] / "docs/contracts/assignments.md").read_text(encoding="utf-8")
    for required in (
        "campusctl sync --only assignments",
        "campusctl assignments list",
        "schema_version",
        "entity_id",
        "failed_courses",
        "headless-unavailable",
        "policy-blocked",
        "item-identity-missing",
        "Warning: assignment cache is stale",
        "cnu_assignment:",
    ):
        assert required in text
    assert "/api/v1/task/stdList" not in text  # Internal policy pins are not a public route inventory.


def test_fixture_cli_sync_list_partial_rollback_policy_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old_catalog(tmp_path)
    _install(monkeypatch, tmp_path, {"course-a": {"rows": fixture_rows()}, "course-b": {"rows": [], "failed": True}})
    code, response = _call(["sync", "--only", "assignments"], capsys)
    assert code == 1 and response["status"] == "partial"
    assert response["result"]["courses"] == 1 and response["result"]["assignments"] == 5
    assert response["errors"][0]["code"] == "course-sync-failed"
    code, listed = _call(["assignments", "list"], capsys)
    assert code == 0
    assert len(listed["result"]["assignments"]) == 7  # Five new rows, two retained stale courses.
    assert {failure["reason"] for failure in listed["result"]["cache"]["failed_courses"]} == {
        "course-sync-failed",
        "removal-deferred",
    }
    from campusctl import presentation

    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr(presentation.shutil, "get_terminal_size", lambda **_kw: os.terminal_size((12, 24)))
    assert cli.main(["assignments", "list", "--course", "course-a"]) == 0
    human = capsys.readouterr().out
    assert "cnu_assignment:course-a:TB_L_REPORT101\n" in human
    assert "Warning:" in human and "Course B" in human
    previous = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    bad_rows = [*fixture_rows(), {"task_id": None, "title": "Unaddressable"}]
    _install(monkeypatch, tmp_path, {"course-a": {"rows": bad_rows}})
    code, response = _call(["sync", "--only", "assignments", "--course", "course-a"], capsys)
    assert code == 1 and response["errors"][0]["code"] == "item-identity-missing"
    assert (
        read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))["assignments"]
        == previous["assignments"]
    )
    previous = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
    _install(
        monkeypatch, tmp_path, {"course-a": {"rows": [], "unexpected": "/std/task", "headers": {"rAnGe": "bytes=0-1"}}}
    )
    code, response = _call(["sync", "--only", "assignments", "--course", "course-a"], capsys)
    assert code == 1 and response["errors"][0]["code"] == "policy-blocked"
    assert read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path)) == previous
    if os.name != "nt":
        with exclusive_lock(tmp_path / "session.lock"):
            code, response = _call(["sync", "--only", "assignments", "--course", "course-a"], capsys)
        assert code == 75 and response["status"] == "busy"
        assert response["errors"][0]["code"] == "session-busy"
    monkeypatch.setattr(
        cli, "load_config", lambda: {"provider": "cnu", "browser": {"cdp_endpoint": "http://browser.invalid:9222"}}
    )
    code, response = _call(["--headless", "sync", "--only", "assignments"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "headless-unavailable"
    assert read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path)) == previous
