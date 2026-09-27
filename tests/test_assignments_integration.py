"""Fixture-backed public CLI and foundation hook integration for assignments."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_assignments_provider import old_catalog
from test_sync_all import IDS, _install_fixture, fixture_server

from campusctl import cli
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.lock import exclusive_lock


def _call(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = cli.main([*argv, "--json"])
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def _install(monkeypatch: pytest.MonkeyPatch, root: Path, server: Any) -> dict[str, Any]:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(root))
    config = _install_fixture(monkeypatch, server)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    return config


def test_assignment_discovery_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    old_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    code, response = _call(["assignments", "list", "--course", "course-a"], capsys)
    assert code == 0
    assert response["result"]["assignments"][0]["task_id"] == "TB_L_REPORT1"
    code, response = _call(["assignments", "list", "--course", "unknown"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "course-id-required"


@pytest.mark.chromium
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
    with fixture_server() as server:
        _install(monkeypatch, tmp_path, server)
        assert cli.main(["--headless", "sync", "--only", "assignments", "--course", IDS[0]]) == 0
        assert "Synced 1 course, 1 assignment." in capsys.readouterr().out


@pytest.mark.chromium
def test_browser_cli_sync_list_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old_catalog(tmp_path)
    with fixture_server() as server:
        _install(monkeypatch, tmp_path, server)
        code, response = _call(["--headless", "sync", "--only", "assignments"], capsys)
        assert code == 0 and response["status"] == "ok"
        assert response["result"]["courses"] == 7 and response["result"]["assignments"] == 7
        code, listed = _call(["assignments", "list"], capsys)
        assert code == 0
        assert len(listed["result"]["assignments"]) == 7  # Complete roster removes unenrolled cached rows.
        assert listed["result"]["cache"]["failed_courses"] == []
        previous = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        if os.name != "nt":
            with exclusive_lock(tmp_path / "session.lock"):
                code, response = _call(["--headless", "sync", "--only", "assignments", "--course", IDS[0]], capsys)
            assert code == 75 and response["status"] == "busy"
            assert response["errors"][0]["code"] == "session-busy"
        monkeypatch.setattr(
            cli, "load_config", lambda: {"provider": "cnu", "browser": {"cdp_endpoint": "http://browser.invalid:9222"}}
        )
        code, response = _call(["--headless", "sync", "--only", "assignments"], capsys)
        assert code == 2 and response["errors"][0]["code"] == "headless-unavailable"
        assert read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path)) == previous
