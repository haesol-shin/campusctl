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


def test_sync_subset_canonical_order_and_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    calls: list[str] = []

    def module(domain: str) -> SimpleNamespace:
        async def run(config: dict, root: Path, course_id: str | None, *, headless: bool) -> tuple[dict, list]:
            calls.append(domain)
            assert course_id is None and not headless and root == tmp_path
            return {"courses": 1, domain: 2}, []

        return SimpleNamespace(sync=run)

    monkeypatch.setattr(
        sync_module,
        "discover_domain_modules",
        lambda: {domain: module(domain) for domain in ("assignments", "notices", "materials")},
    )
    code, response = _call(["sync", "--only", "notices, assignments,notices", "--json"], capsys)
    assert code == 0 and calls == ["assignments", "notices"]
    assert list(response["result"]["domains"]) == calls
    assert [entry["status"] for entry in response["result"]["domains"].values()] == ["ok", "ok"]
    for bad in ("", "all", "lectures,", "unknown", "notices,,materials"):
        code, response = _call(["sync", "--only", bad, "--json"], capsys)
        assert code == 2 and response["errors"][0]["code"] == "unsupported-domain"
    assert calls == ["assignments", "notices"]


def test_sync_partial_retains_successful_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})

    async def okay(*args: object, **kwargs: object) -> tuple[dict, list]:
        return {"courses": 1}, []

    async def failure(*args: object, **kwargs: object) -> tuple[dict, list]:
        return {}, [CampusError("course-sync-failed", "A course failed.", status="error")]

    monkeypatch.setattr(
        sync_module,
        "discover_domain_modules",
        lambda: {"assignments": SimpleNamespace(sync=okay), "notices": SimpleNamespace(sync=failure)},
    )
    code, response = _call(["sync", "--only", "assignments,notices", "--json"], capsys)
    assert code == 1 and response["status"] == "partial"
    assert response["result"]["domains"]["assignments"]["result"] == {"courses": 1}
    assert response["result"]["domains"]["notices"]["errors"][0]["code"] == "course-sync-failed"
    assert response["errors"][0]["code"] == "course-sync-failed"


def test_global_mode_refuses_before_sync_and_legacy_flag_is_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
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

    async def provider(config: dict, root: Path, course_id: str | None, *, headless: bool):
        return {"courses": 1}, []

    monkeypatch.setattr(sync_module, "discover_domain_modules", lambda: {"assignments": SimpleNamespace(sync=provider)})
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
    ids = ["course-a"]

    async def provider(config: dict, root: Path, course_id: str | None, *, headless: bool):
        if course_id not in ids:
            raise CampusError("course-not-found", "Not enrolled.", status="user-action")
        return {"courses": 1}, []

    monkeypatch.setattr(sync_module, "discover_domain_modules", lambda: {"assignments": SimpleNamespace(sync=provider)})
    code, response = _call(["sync", "--only", "assignments", "--course", "course-a", "--json"], capsys)
    assert code == 0 and response["result"]["courses"] == 1
    code, response = _call(["sync", "--only", "assignments", "--course", "course", "--json"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "course-id-required"
    code, response = _call(["sync", "--only", "assignments", "--course", "1", "--json"], capsys)
    assert code == 2 and response["errors"][0]["code"] == "course-index-unavailable"
