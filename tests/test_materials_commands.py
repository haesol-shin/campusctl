from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl import cli
from campusctl.commands import discover_domain_modules, materials
from campusctl.domain_catalog import write_domain_catalog
from campusctl.providers.cnu import attachment_transfer
from campusctl.providers.cnu import materials as provider
from campusctl.providers.cnu.sync_all import DomainOutcome

ID = "cnu_lms_material:course-a:file-1"
ROW = {
    "entity_id": ID,
    "course": {"id": "course-a", "label": "Example Course"},
    "archive_entry": {"board_item_id": "board-1", "title": "Course archive"},
    "file_id": "file-1",
    "display_name": "example.pdf 바로보기",
    "filename": "example.pdf",
    "media_type": None,
    "size_bytes": None,
    "downloadable": True,
    "unavailable_reason": None,
}
BYTES = b"%PDF-1.7\nsynthetic fixture\n"


def _catalog(root: Path, *, stale: bool = False) -> None:
    write_domain_catalog(
        "materials",
        {
            "generated_at": "2026-09-25T10:00:00Z",
            "enrollment_state": "unknown" if stale else "known",
            "courses": [{"course_id": "course-a", "label": "Example Course", "class_no": None}],
            "failed_courses": [{"course_id": "course-a", "label": "Example Course", "reason": "course-sync-failed"}]
            if stale
            else [],
            "materials": [ROW],
        },
        root / "catalog" / "materials.json",
    )


def _cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main(argv)
    output = capsys.readouterr()
    assert output.err == ""
    return code, output.out


def test_sync_list_human_json_and_staleness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path, stale=True)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("list must not load configuration"))
    code, human = _cli(["materials", "list", "--course", "course-a"], capsys)
    assert code == 0 and ID in human and "Warning:" in human and "Course archive" in human
    code, raw = _cli(["materials", "list", "--json"], capsys)
    assert code == 0
    payload = json.loads(raw)
    assert payload["result"]["materials"] == [ROW]
    assert payload["result"]["cache"]["enrollment_state"] == "unknown"
    assert materials.render(
        "sync.materials",
        {"courses": 1, "materials": 2, "failed_courses": [], "catalog": {"enrollment_state": "known"}},
        50,
    ) == ["Synced 1 course, 2 materials."]


def test_discovery_exposes_materials_without_ui_request_policy() -> None:
    assert discover_domain_modules()["materials"] is materials
    assert cli.CAPABILITIES["materials"] == ["list", "download"]
    assert materials.CAPABILITY == {"commands": ["list", "download"]}


def _fake_network(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    parent_missing: bool = False,
    modal_matches: int = 1,
    inline_matches: int = 1,
) -> list[str]:
    calls: list[str] = []

    class Page:
        async def goto(self, url: str) -> None:
            calls.append("navigate")

        async def click(self, selector: str) -> None:
            calls.append("archive-list")

        async def evaluate(self, script: str, value: str) -> None:
            assert value == "board-1"
            calls.append("select-parent")

        async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
            calls.append("modal")

        def locator(self, selector: str) -> Any:
            async def count() -> int:
                if selector.startswith("#file_download "):
                    return modal_matches
                if ' [data-act="downloadFile"]' in selector:
                    return inline_matches
                return 1

            return SimpleNamespace(count=count)

    page = Page()
    page.context = SimpleNamespace()

    @asynccontextmanager
    async def session(config: Any, *, data_dir: Path, headless: bool, operation: str):
        calls.append("lock")
        assert data_dir == root
        if headless:
            calls.append("headless")
        yield SimpleNamespace(page=page, context=page.context)

    async def login(*args: Any, **kwargs: Any) -> None:
        calls.append("authenticate")

    async def navigate(page: Any, action: Any, **kwargs: Any) -> None:
        await action()

    async def step(action: Any, description: str) -> Any:
        return await action

    async def prepare(*args: Any) -> None:
        calls.append("enter-course")

    async def open_section(*args: Any) -> None:
        calls.append("open-section")

    async def enumerate_rows(*args: Any) -> list[dict[str, Any]]:
        calls.append("enumerate")
        return (
            [{**ROW, "archive_entry": {"board_item_id": "moved", "title": "Course archive"}}]
            if parent_missing
            else [ROW]
        )

    async def state(*args: Any) -> dict[str, Any]:
        return {"total_count": 1, "page_size": 1, "posts": [{"board_item_id": "board-1"}]}

    async def fetch(page: Any, target: Any, policy: Any, directory: Path, *, max_bytes: int) -> Any:
        calls.append("fetch")
        assert target.file_id == "file-1" and target.parent_kind == "archive" and target.parent_id == "board-1"
        expected_modal = '#file_download [data-act="downloadFile"][data-id="file-1"]'
        expected_inline = '#listBody tr:has([data-act="file"][data-boarditem_no="board-1"]) [data-act="downloadFile"][data-id="file-1"]'
        assert target.control_locator == (expected_modal if modal_matches else expected_inline)
        assert max_bytes == 200_000_000 and policy.filename == "example.pdf"
        temp = directory / ".attachment-synthetic"
        temp.write_bytes(BYTES)
        return attachment_transfer.FetchedAttachment(
            temp, hashlib.sha256(BYTES).hexdigest(), "application/x-pdf", len(BYTES)
        )

    monkeypatch.setattr("campusctl.material_files.default_download_dir", lambda: root / "Downloads" / "campusctl")
    monkeypatch.setattr("campusctl.browser.open_session", session)
    monkeypatch.setattr("campusctl.providers.cnu.login.ensure_logged_in", login)
    monkeypatch.setattr("campusctl.providers.cnu.course_context.prepare_course_section", prepare)
    monkeypatch.setattr("campusctl.providers.cnu.course_context.open_course_section", open_section)
    monkeypatch.setattr(provider, "_archive_navigation", navigate)
    monkeypatch.setattr(provider, "_archive_state", state)
    monkeypatch.setattr(provider, "_step", step)
    monkeypatch.setattr(provider, "enumerate_archive", enumerate_rows)
    monkeypatch.setattr(attachment_transfer, "fetch_official_attachment", fetch)
    return calls


def test_download_selection_policy_and_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    calls = _fake_network(monkeypatch, tmp_path)
    code, human = _cli(["materials", "download", ID], capsys)
    assert code == 0 and human.startswith("Saved: ")
    assert calls.index("lock") < calls.index("authenticate") < calls.index("fetch")
    path = tmp_path / "Downloads" / "campusctl" / "Example Course" / "example.pdf"
    assert path.read_bytes() == BYTES and str(path) in human
    assert not (path.parent / ".attachment-synthetic").exists()
    code, raw = _cli(["materials", "download", "absent", "--json"], capsys)
    assert code == 2 and json.loads(raw)["errors"][0]["code"] == "entity-unknown"
    code, raw = _cli(["materials", "download", ID, "--headless", "--json"], capsys)
    assert code == 2 and json.loads(raw)["errors"][0]["code"] == "usage-error"
    code, raw = _cli(["--headless", "materials", "download", ID, "--out", str(tmp_path / "headless"), "--json"], capsys)
    assert code == 0 and json.loads(raw)["result"]["material"]["outcome"] == "saved"
    assert calls.count("fetch") == 2 and "headless" in calls


@pytest.mark.parametrize(("modal_count", "inline_count", "expected_code"), [(0, 1, 0), (0, 0, 2), (2, 1, 2)])
def test_selected_modal_falls_back_only_to_unique_post_inline_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    modal_count: int,
    inline_count: int,
    expected_code: int,
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    calls = _fake_network(monkeypatch, tmp_path, modal_matches=modal_count, inline_matches=inline_count)
    code, raw = _cli(["materials", "download", ID, "--json"], capsys)
    assert code == expected_code
    if expected_code == 0:
        assert json.loads(raw)["result"]["material"]["outcome"] == "saved"
        assert calls.count("fetch") == 1
    else:
        assert json.loads(raw)["errors"][0]["code"] == "entity-unknown"
        assert "fetch" not in calls


def test_skip_existing_and_restart_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    calls = _fake_network(monkeypatch, tmp_path)
    destination = tmp_path / "chosen"
    code, raw = _cli(["materials", "download", ID, "--out", str(destination), "--json"], capsys)
    first = json.loads(raw)["result"]["material"]
    assert code == 0 and first["outcome"] == "saved"
    assert Path(first["path"]) == destination / "example.pdf"
    assert not (tmp_path / "Downloads" / "campusctl" / "Example Course").exists()
    calls.clear()
    code, raw = _cli(["materials", "download", ID, "--out", str(destination), "--json"], capsys)
    skipped = json.loads(raw)["result"]["material"]
    assert code == 0 and skipped["outcome"] == "skipped-existing" and skipped["path"] == first["path"]
    assert calls == ["lock"]  # no authentication, navigation, or LMS request
    Path(first["path"]).write_bytes(b"changed")
    calls.clear()
    code, raw = _cli(["materials", "download", ID, "--out", str(destination), "--json"], capsys)
    fresh = json.loads(raw)["result"]["material"]
    assert code == 0 and fresh["outcome"] == "saved" and calls.count("fetch") == 1
    assert Path(fresh["path"]).read_bytes() == BYTES


def test_overlong_windows_destination_fails_before_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl import material_files

    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    calls = _fake_network(monkeypatch, tmp_path)
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    long_downloads = tmp_path / ("x" * (247 - len(str(tmp_path))))
    monkeypatch.setattr(material_files, "default_download_dir", lambda: long_downloads)
    code, raw = _cli(["materials", "download", ID, "--json"], capsys)
    assert code == 2 and json.loads(raw)["errors"][0]["code"] == "output-path-too-long"
    assert calls == ["lock"]
    assert not long_downloads.exists()


def test_moved_parent_fails_before_selected_file_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    calls = _fake_network(monkeypatch, tmp_path, parent_missing=True)
    code, raw = _cli(["materials", "download", ID, "--json"], capsys)
    assert code == 2 and json.loads(raw)["errors"][0]["code"] == "entity-unknown"
    assert "fetch" not in calls
    assert not (tmp_path / "Downloads" / "campusctl" / "Example Course" / ROW["filename"]).exists()


def test_full_ids_stale_warning_and_outcome_paths() -> None:
    long_id = ID + "-" + "x" * 140
    row = {**ROW, "entity_id": long_id}
    lines = materials.render(
        "materials.list",
        {
            "materials": [row],
            "cache": {"generated_at": "2026-09-25T10:00:00Z", "enrollment_state": "unknown", "failed_courses": []},
        },
        34,
    )
    assert (
        "".join(
            line.strip().removeprefix("1. ")
            for line in lines
            if "cnu_lms_material" in line or line.strip().startswith("x")
        )
        == long_id
    )
    assert any("Warning:" in line for line in lines)
    for outcome, label in (
        ("saved", "Saved"),
        ("reused", "Reused"),
        ("adopted", "Adopted"),
        ("skipped-existing", "Skipped existing"),
    ):
        rendered = materials.render(
            "materials.download", {"material": {"outcome": outcome, "path": "downloads/example.pdf"}}, 10
        )
        assert rendered == [f"{label}: downloads/example.pdf"]


def test_end_to_end_fixture_cli_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    calls = _fake_network(monkeypatch, tmp_path)

    async def sync_fixture(
        config: Any, root: Path, domains: tuple[str, ...], course_id: Any, **kwargs: Any
    ) -> dict[str, DomainOutcome]:
        assert domains == ("materials",)
        _catalog(root)
        return {
            "materials": DomainOutcome(
                {
                    "courses": 1,
                    "materials": 1,
                    "failed_courses": [],
                    "catalog": {"generated_at": "2026-09-25T10:00:00Z", "enrollment_state": "known"},
                }
            )
        }

    monkeypatch.setattr("campusctl.sync.sync_all", sync_fixture)
    assert _cli(["sync", "--only", "materials"], capsys) == (0, "Synced 1 course, 1 material.\n")
    code, human_list = _cli(["materials", "list"], capsys)
    assert code == 0 and ID in human_list
    code, json_list = _cli(["materials", "list", "--json"], capsys)
    assert code == 0 and json.loads(json_list)["result"]["materials"][0]["entity_id"] == ID
    code, human_saved = _cli(["materials", "download", ID], capsys)
    expected_path = tmp_path / "Downloads" / "campusctl" / "Example Course" / "example.pdf"
    assert code == 0 and human_saved == f"Saved: {expected_path}\n"
    calls.clear()
    code, json_retry = _cli(["materials", "download", ID, "--json"], capsys)
    assert code == 0 and json.loads(json_retry)["result"]["material"]["outcome"] == "skipped-existing"
    assert calls == ["lock"]


def test_printed_material_number_is_bound_to_generation_and_json_cannot_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    calls = _fake_network(monkeypatch, tmp_path)
    code, output = _cli(["materials", "list", "--course", "course-a"], capsys)
    assert code == 0 and f"1. {ID}" in output
    code, output = _cli(["materials", "download", "1"], capsys)
    assert code == 0 and "Saved:" in output and calls.count("fetch") == 1
    _catalog(tmp_path)
    calls.clear()
    code, output = _cli(["materials", "download", "1"], capsys)
    assert code == 2 and "selection-stale" in output and not calls
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("JSON must not prompt"))
    code, output = _cli(["materials", "download", "--json"], capsys)
    assert code == 2 and json.loads(output)["errors"][0]["code"] == "selection-required"


def test_material_list_refuses_catalog_rewrite_before_snapshot_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    original = cli.render_human

    def rewrite(command: str, envelope: dict, stream: Any) -> None:
        original(command, envelope, stream)
        if command == "materials.list":
            _catalog(tmp_path)

    monkeypatch.setattr(cli, "render_human", rewrite)
    code, output = _cli(["materials", "list"], capsys)
    assert code == 2 and "selection-stale" in output
    assert not (tmp_path / "selection" / "materials-last-list.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows adoption fails closed without pinned reparse-point checks")
def test_configured_adoption_receipt_and_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    course = tmp_path / "School" / "Example Course"
    old = course / "archive"
    old.mkdir(parents=True)
    existing = old / "example.pdf"
    existing.write_bytes(BYTES)
    settings = {
        "materials": {"download_dir": str(tmp_path / "School" / "{course}" / "materials"), "adopt_existing": True}
    }
    monkeypatch.setattr("campusctl.config.load_config", lambda: settings)
    calls = _fake_network(monkeypatch, tmp_path)
    code, raw = _cli(["materials", "download", ID, "--json"], capsys)
    result = json.loads(raw)["result"]["material"]
    assert code == 0 and result["outcome"] == "adopted" and result["path"] == str(existing)
    assert calls.index("lock") < calls.index("authenticate") < calls.index("fetch")
    assert not (course / "materials" / "example.pdf").exists()
    calls.clear()
    code, raw = _cli(["materials", "download", ID, "--json"], capsys)
    assert code == 0 and json.loads(raw)["result"]["material"]["outcome"] == "skipped-existing"
    assert calls == ["lock"]
    calls.clear()
    override = tmp_path / "override"
    code, raw = _cli(["materials", "download", ID, "--out", str(override), "--json"], capsys)
    assert code == 0 and json.loads(raw)["result"]["material"]["outcome"] == "saved"
    assert (override / "example.pdf").read_bytes() == BYTES and calls.count("fetch") == 1
    settings["materials"]["download_dir"] = str(tmp_path / "School" / "{course}" / "new")
    calls.clear()
    code, raw = _cli(["materials", "download", ID, "--json"], capsys)
    assert code == 0 and json.loads(raw)["result"]["material"]["outcome"] == "adopted"
    assert calls.count("fetch") == 1
    existing.write_bytes(b"corrupted")
    calls.clear()
    code, raw = _cli(["materials", "download", ID, "--json"], capsys)
    assert code == 0 and json.loads(raw)["result"]["material"]["outcome"] == "saved"
    assert (course / "new" / "example.pdf").read_bytes() == BYTES and calls.count("fetch") == 1
