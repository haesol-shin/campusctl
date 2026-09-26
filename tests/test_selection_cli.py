"""Printed human course indices remain bound to the selected catalog generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campusctl import cli


def _catalog(root: Path, courses: list[tuple[str, str]]) -> None:
    path = root / "catalog" / "lectures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-09-25T12:00:00Z",
                "courses": [{"course_id": course_id, "label": label, "class_no": None} for course_id, label in courses],
                "lectures": [],
            }
        ),
        encoding="utf-8",
    )


def _call(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    result = cli.main(argv)
    output = capsys.readouterr().out
    return result, output


def test_printed_course_index_stale_on_same_second_catalog_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    _catalog(tmp_path, [("course-b", "Beta"), ("course-c", "Gamma")])
    code, output = _call(["courses", "list"], capsys)
    assert code == 0 and "1." in output and "course-b" in output
    code, output = _call(["lectures", "list", "--course", "1"], capsys)
    assert code == 0 and "Nothing unfinished" in output
    _catalog(tmp_path, [("course-a", "Alpha"), ("course-b", "Beta"), ("course-c", "Gamma")])
    code, output = _call(["lectures", "list", "--course", "1"], capsys)
    assert code == 2 and "selection-stale" in output
    code, output = _call(["lectures", "list", "--course", "course-b"], capsys)
    assert code == 0


def test_json_list_does_not_replace_printed_roster_and_json_refuses_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    _catalog(tmp_path, [("course-b", "Beta")])
    assert _call(["courses", "list"], capsys)[0] == 0
    snapshot = tmp_path / "selection" / "courses-last-list.json"
    original = snapshot.read_bytes()
    assert _call(["courses", "list", "--json"], capsys)[0] == 0
    assert snapshot.read_bytes() == original
    for selector, code in (("1", "course-index-unavailable"), ("Bet", "course-id-required")):
        exit_code, output = _call(["status", "--course", selector, "--json"], capsys)
        assert exit_code == 2 and json.loads(output)["errors"][0]["code"] == code
    assert snapshot.read_bytes() == original


def test_browser_preflight_is_inside_lock_and_precedes_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from contextlib import contextmanager

    from campusctl import browser
    from campusctl.envelope import CampusError

    events: list[str] = []

    @contextmanager
    def locked(path: Path):
        events.append("locked")
        try:
            yield
        finally:
            events.append("unlocked")

    def deny() -> None:
        events.append("selection-check")
        raise CampusError("selection-stale", "The printed catalog changed.", status="user-action")

    monkeypatch.setattr(browser, "exclusive_lock", locked)
    monkeypatch.setattr(browser, "_playwright_manager", lambda: events.append("launched"))

    async def run() -> None:
        with browser.pre_browser_check(deny):
            async with browser.open_session({}, data_dir=tmp_path, headless=True):
                raise AssertionError("must not reach the session")

    with pytest.raises(CampusError, match="printed catalog"):
        asyncio.run(run())
    assert events == ["locked", "selection-check", "unlocked"]


def test_refresh_runs_only_selected_domain_and_returns_updated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from campusctl import sync as sync_module
    from campusctl.domain_catalog import write_domain_catalog

    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    calls: list[str | None] = []

    async def refresh(config: dict, root: Path, course_id: str | None, *, headless: bool):
        calls.append(course_id)
        write_domain_catalog(
            "assignments",
            {
                "generated_at": "2026-09-25T10:00:00Z",
                "enrollment_state": "known",
                "courses": [{"course_id": "course-a", "label": "Alpha", "class_no": None}],
                "failed_courses": [],
                "assignments": [],
            },
            root / "catalog" / "assignments.json",
        )
        return {"courses": 1, "assignments": 0}, []

    monkeypatch.setattr(sync_module, "discover_domain_modules", lambda: {"assignments": SimpleNamespace(sync=refresh)})
    code, output = _call(["assignments", "list", "--refresh", "--json"], capsys)
    response = json.loads(output)
    assert code == 0 and calls == [None]
    assert response["result"]["refresh"]["status"] == "ok"
    assert response["result"]["assignments"] == []
    assert response["result"]["cache"]["generated_at"] == "2026-09-25T10:00:00Z"


def test_numeric_course_remains_frozen_across_own_domain_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from campusctl import sync as dispatcher

    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    _catalog(tmp_path, [("course-b", "Beta")])
    assert _call(["courses", "list"], capsys)[0] == 0
    monkeypatch.setattr(cli, "load_config", lambda: {})
    calls: list[str] = []

    def module(name: str):
        async def run(config: dict, root: Path, course_id: str | None, *, headless: bool):
            assert course_id == "course-b"
            calls.append(name)
            if name == "assignments":
                _catalog(root, [("course-b", "Beta"), ("course-c", "Gamma")])
            return {"courses": 1}, []

        return SimpleNamespace(sync=run)

    monkeypatch.setattr(
        dispatcher,
        "discover_domain_modules",
        lambda: {
            "assignments": module("assignments"),
            "notices": module("notices"),
        },
    )
    code, output = _call(["sync", "--only", "assignments,notices", "--course", "1", "--json"], capsys)
    assert code == 2 and json.loads(output)["errors"][0]["code"] == "course-index-unavailable"
    code, output = _call(["sync", "--only", "assignments,notices", "--course", "1"], capsys)
    assert code == 0 and calls == ["assignments", "notices"]


def test_filtered_status_preserves_invalid_source_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    _catalog(tmp_path, [("course-a", "Alpha")])
    broken = tmp_path / "catalog" / "materials.json"
    broken.write_text("{invalid", encoding="utf-8")
    code, output = _call(["status", "--course", "course-a", "--json"], capsys)
    status = json.loads(output)
    assert code == 1 and status["status"] == "partial"
    assert status["result"]["coverage"]["lectures"]["state"] == "available"
    assert status["result"]["coverage"]["materials"]["state"] == "invalid"
    assert status["errors"][0]["domain"] in {"assignments", "notices", "materials"}


def test_uncached_human_name_uses_live_roster_for_filtered_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from campusctl import sync as dispatcher
    from campusctl.course_selection import resolve_course
    from campusctl.domain_catalog import write_domain_catalog

    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    selectors: list[str | None] = []

    def discover(selector: str, config: dict, root: Path, domain: str, *, ids_only: bool, mode: bool):
        assert domain == "assignments" and not ids_only and not mode
        return resolve_course(selector, [{"course_id": "course-a", "label": "Alpha Study"}], ids_only=False)

    async def provider(config: dict, root: Path, course_id: str | None, *, headless: bool):
        selectors.append(course_id)
        write_domain_catalog(
            "assignments",
            {
                "generated_at": "2026-09-25T10:00:00Z",
                "enrollment_state": "known",
                "courses": [{"course_id": "course-a", "label": "Alpha Study", "class_no": None}],
                "failed_courses": [],
                "assignments": [],
            },
            root / "catalog" / "assignments.json",
        )
        return {"courses": 1, "assignments": 0}, []

    monkeypatch.setattr(cli, "_discover_live_course", discover)
    monkeypatch.setattr(
        dispatcher,
        "discover_domain_modules",
        lambda: {
            "assignments": SimpleNamespace(sync=provider),
        },
    )
    code, output = _call(["assignments", "list", "--course", "Alpha", "--refresh"], capsys)
    assert code == 0 and selectors == ["course-a"] and "0 assignments" in output
