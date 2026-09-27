"""CLI integration and selected-detail handler tests for fetch."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl import cli
from campusctl.commands import assignments, notices
from campusctl.envelope import CampusError, make_envelope
from campusctl.presentation import render_human


def _setup_catalogs(root: Path) -> None:
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    assignment_data = {
        "schema_version": 1,
        "generated_at": "2026-09-25T12:00:00Z",
        "enrollment_state": "known",
        "courses": [{"id": "course-xyz", "label": "Course XYZ"}],
        "failed_courses": [],
        "assignments": [
            {
                "entity_id": "cnu_assignment:course-xyz:task-999",
                "task_id": "task-999",
                "title": "Assignment XYZ",
                "course": {"id": "course-xyz", "label": "Course XYZ"},
                "due_date": "2026-10-15",
                "is_submitted": False,
            }
        ],
    }
    (catalog_dir / "assignments.json").write_text(json.dumps(assignment_data), encoding="utf-8")

    notice_data = {
        "schema_version": 1,
        "generated_at": "2026-09-25T12:00:00Z",
        "enrollment_state": "known",
        "courses": [{"id": "course-xyz", "label": "Course XYZ"}],
        "failed_courses": [],
        "notices": [
            {
                "entity_id": "cnu_notice:course-xyz:2026-09-25:10",
                "title": "Notice XYZ",
                "course": {"id": "course-xyz", "label": "Course XYZ"},
                "date": "2026-09-25",
                "is_unread": True,
                "has_attachments": False,
                "view_count": 12,
            }
        ],
    }
    (catalog_dir / "notices.json").write_text(json.dumps(notice_data), encoding="utf-8")


def test_fetch_released_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "json")
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    _setup_catalogs(tmp_path)
    selected: list[tuple[str, str, Path | None]] = []

    async def package(_config: Any, _root: Path, row: dict[str, Any], **options: Any) -> dict[str, Any]:
        selected.append((row["entity_id"], row["course"]["id"], options["out"]))
        return {
            "entity_id": row["entity_id"],
            "path": str(tmp_path / "sources" / "package"),
            "content_path": str(tmp_path / "sources" / "package" / "content.md"),
            "manifest_path": str(tmp_path / "sources" / "package" / "package.json"),
            "completeness": "complete",
            "resources": [],
            "omitted_resources": [],
        }

    monkeypatch.setattr(assignments, "_fetch_assignment", package)
    monkeypatch.setattr(notices, "_fetch_notice", package)

    for domain, entity_id in (
        ("assignments", "cnu_assignment:course-xyz:task-999"),
        ("notices", "cnu_notice:course-xyz:2026-09-25:10"),
    ):
        assert cli.main([domain, "--help"]) == 0
        assert "fetch" in capsys.readouterr().out
        target = tmp_path / f"{domain}-package"
        assert cli.main([domain, "fetch", entity_id, "--out", str(target), "--json"]) == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["status"] == "ok"
        assert envelope["errors"] == []
        assert envelope["result"]["source_package"]["entity_id"] == entity_id
        assert selected[-1] == (entity_id, "course-xyz", target)

        assert cli.main([domain, "fetch", "1", "--json"]) == 2
        error = json.loads(capsys.readouterr().out)
        assert error["status"] == "user-action"
        assert error["errors"][0]["code"] == "entity-unknown"
        assert selected[-1] == (entity_id, "course-xyz", target)

    async def omitted(_config: Any, _root: Path, row: dict[str, Any], **_options: Any) -> dict[str, Any]:
        return {
            "entity_id": row["entity_id"],
            "path": str(tmp_path / "sources" / "package"),
            "content_path": str(tmp_path / "sources" / "package" / "content.md"),
            "completeness": "partial",
            "resources": [],
            "omitted_resources": [{"reason": "unverified-notice-attachment", "original_name": "appendix.pdf"}],
        }

    monkeypatch.setattr(notices, "_fetch_notice", omitted)
    assert cli.main(["notices", "fetch", "cnu_notice:course-xyz:2026-09-25:10", "--json"]) == 1
    partial = json.loads(capsys.readouterr().out)
    assert partial["status"] == "partial"
    assert partial["errors"][0]["code"] == "resource-omitted"
    assert partial["result"]["source_package"]["omitted_resources"][0]["reason"] == "unverified-notice-attachment"

    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    assert cli.main(["notices", "fetch", "cnu_notice:course-xyz:2026-09-25:10"]) == 1
    human = capsys.readouterr().out
    assert "Notice source: cnu_notice:course-xyz:2026-09-25:10" in human
    assert "Completeness: partial" in human
    assert "Omitted: unverified-notice-attachment" in human

    # Numeric material selections are not fetch IDs
    for num_id in ("1", "2", "42"):
        args_num_a = SimpleNamespace(assignments_command="fetch", entity_id=num_id, out=None, headless_override=None)
        with pytest.raises(CampusError) as exc_a:
            assignments.dispatch(args_num_a)
        assert exc_a.value.code == "entity-unknown"

        args_num_n = SimpleNamespace(notices_command="fetch", entity_id=num_id, out=None, headless_override=None)
        with pytest.raises(CampusError) as exc_n:
            notices.dispatch(args_num_n)
        assert exc_n.value.code == "entity-unknown"

    # Full selected IDs retain course binding
    captured_assignment_course: list[str] = []

    async def mock_fetch_assignment(_cfg: Any, _root: Any, row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured_assignment_course.append(row["course"]["id"])
        return {
            "entity_id": row["entity_id"],
            "completeness": "complete",
            "path": "/data/sources/assignment/hash/digest",
            "manifest_path": "/data/sources/assignment/hash/digest/package.json",
            "content_path": "/data/sources/assignment/hash/digest/content.md",
            "resources": [],
            "omitted_resources": [],
            "provenance": {
                "provider": "cnu",
                "course_id": row["course"]["id"],
                "source_ref": {
                    "origin": "https://dcs-learning.cnu.ac.kr",
                    "page_path": "/std/taskView",
                    "provider_native_id": row["task_id"],
                },
                "retrieved_at": "2026-09-25T12:00:00Z",
            },
        }

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_fetch_assignment)

    args_a = SimpleNamespace(
        assignments_command="fetch",
        entity_id="cnu_assignment:course-xyz:task-999",
        out=None,
        headless_override=None,
    )
    res_a, err_a = assignments.dispatch(args_a)
    assert err_a is None
    assert captured_assignment_course == ["course-xyz"]
    assert res_a["source_package"]["provenance"]["course_id"] == "course-xyz"

    captured_notice_course: list[str] = []

    async def mock_fetch_notice(_cfg: Any, _root: Any, row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured_notice_course.append(row["course"]["id"])
        return {
            "entity_id": row["entity_id"],
            "completeness": "complete",
            "path": "/data/sources/notice/hash/digest",
            "manifest_path": "/data/sources/notice/hash/digest/package.json",
            "content_path": "/data/sources/notice/hash/digest/content.md",
            "resources": [],
            "omitted_resources": [],
            "provenance": {
                "provider": "cnu",
                "course_id": row["course"]["id"],
                "source_ref": {
                    "origin": "https://dcs-learning.cnu.ac.kr",
                    "page_path": "/std/noticeDetail",
                    "provider_native_id": None,
                },
                "retrieved_at": "2026-09-25T12:00:00Z",
            },
        }

    monkeypatch.setattr(notices, "_fetch_notice", mock_fetch_notice)

    args_n = SimpleNamespace(
        notices_command="fetch",
        entity_id="cnu_notice:course-xyz:2026-09-25:10",
        out=None,
        headless_override=None,
    )
    res_n, err_n = notices.dispatch(args_n)
    assert err_n is None
    assert captured_notice_course == ["course-xyz"]
    assert res_n["source_package"]["provenance"]["course_id"] == "course-xyz"

    # Unsupported effective headless fails before navigation
    args_hl_a = SimpleNamespace(
        assignments_command="fetch", entity_id="cnu_assignment:course-xyz:task-999", out=None, headless_override=True
    )
    with pytest.raises(CampusError) as exc_hl_a:
        assignments.dispatch(args_hl_a)
    assert exc_hl_a.value.code == "headless-unavailable"

    args_hl_n = SimpleNamespace(
        notices_command="fetch", entity_id="cnu_notice:course-xyz:2026-09-25:10", out=None, headless_override=True
    )
    with pytest.raises(CampusError) as exc_hl_n:
        notices.dispatch(args_hl_n)
    assert exc_hl_n.value.code == "headless-unavailable"

    # Direct handler presentation
    envelope_a = make_envelope(status="ok", result=res_a)
    assert envelope_a["schema_version"] == 1
    assert envelope_a["status"] == "ok"
    assert envelope_a["result"]["source_package"]["entity_id"] == "cnu_assignment:course-xyz:task-999"

    envelope_n = make_envelope(status="ok", result=res_n)
    assert envelope_n["schema_version"] == 1
    assert envelope_n["status"] == "ok"
    assert envelope_n["result"]["source_package"]["entity_id"] == "cnu_notice:course-xyz:2026-09-25:10"

    import io

    stream_a = io.StringIO()
    render_human("assignments.fetch", envelope_a, stream_a, width=80)
    lines_a = stream_a.getvalue().splitlines()
    assert any("Assignment source: cnu_assignment:course-xyz:task-999" in line for line in lines_a)
    assert any("Package: /data/sources/assignment/hash/digest" in line for line in lines_a)
    assert any("Completeness: complete" in line for line in lines_a)

    stream_n = io.StringIO()
    render_human("notices.fetch", envelope_n, stream_n, width=80)
    lines_n = stream_n.getvalue().splitlines()
    assert any("Notice source: cnu_notice:course-xyz:2026-09-25:10" in line for line in lines_n)
    assert any("Package: /data/sources/notice/hash/digest" in line for line in lines_n)
    assert any("Completeness: complete" in line for line in lines_n)

    # Busy session
    async def mock_busy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CampusError("session-busy", "Browser session lock is occupied.", status="busy")

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_busy)
    with pytest.raises(CampusError) as exc_busy:
        assignments.dispatch(args_a)
    assert exc_busy.value.code == "session-busy"
    assert exc_busy.value.status == "busy"
