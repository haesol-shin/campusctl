"""Tests for unpublished assignments fetch command, dispatch, render, and policy pins."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.commands import assignments
from campusctl.envelope import CampusError


def _setup_catalog(root: Path) -> dict[str, Any]:
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    catalog_data = {
        "schema_version": 1,
        "generated_at": "2026-09-25T12:00:00Z",
        "enrollment_state": "known",
        "courses": [{"id": "course-1", "label": "Course 1"}],
        "failed_courses": [],
        "assignments": [
            {
                "entity_id": "cnu_assignment:course-1:task-101",
                "task_id": "task-101",
                "title": "Assignment 1",
                "course": {"id": "course-1", "label": "Course 1"},
                "due_date": "2026-10-01",
                "is_submitted": False,
            }
        ],
    }
    import json

    (catalog_dir / "assignments.json").write_text(json.dumps(catalog_data), encoding="utf-8")
    return catalog_data


def test_assignment_fetch_json_and_human(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assignments, "data_dir", lambda: tmp_path)
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    _setup_catalog(tmp_path)

    # 1. Verify fetch is UNPUBLISHED
    assert "fetch" not in assignments.CAPABILITY["commands"]
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    assignments.register(subparsers)
    with pytest.raises(SystemExit):
        parser.parse_args(["assignments", "fetch", "cnu_assignment:course-1:task-101"])
    # Verify reviewed fetch policy is enabled and free of unapproved logging reads
    from campusctl.providers.cnu.ui_policy import UiRequestPolicy

    ui_policy = UiRequestPolicy.from_reviewed_config(assignments.FETCH_POLICY)
    assert ui_policy.approved is True
    assert not any(r.path == "/api/v1/week/getStdActivityStatus" for r in ui_policy.routes)
    assert not any(s.name == "panopto-saml-script" for s in ui_policy.suppress)
    assert {s.name for s in ui_policy.suppress} == {
        "panopto-script",
        "panopto-disconnection-log",
        "panopto-connectivity-check",
        "course-roster-image",
        "favicon-icon",
        "external-telemetry",
        "panopto-sso-popup",
    }

    # 2. Test successful dispatch (complete package)
    fake_pkg_complete = {
        "entity_id": "cnu_assignment:course-1:task-101",
        "completeness": "complete",
        "path": "/data/sources/assignment/abc/def",
        "manifest_path": "/data/sources/assignment/abc/def/package.json",
        "content_path": "/data/sources/assignment/abc/def/content.md",
        "resources": [
            {
                "resource_id": "res-1",
                "kind": "image",
                "path": "/data/sources/assignment/abc/def/images/diagram.png",
                "source_ref": {
                    "origin": "https://dcs-learning.cnu.ac.kr",
                    "page_path": "/upload/diagram.png",
                    "provider_native_id": None,
                },
                "original_name": "diagram.png",
                "media_type": "image/png",
                "size_bytes": 100,
                "sha256": "1111",
            }
        ],
        "omitted_resources": [],
        "provenance": {
            "provider": "cnu",
            "course_id": "course-1",
            "source_ref": {
                "origin": "https://dcs-learning.cnu.ac.kr",
                "page_path": "/std/taskView",
                "provider_native_id": "task-101",
            },
            "retrieved_at": "2026-09-25T12:00:00Z",
        },
    }

    async def mock_fetch_success(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return fake_pkg_complete

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_fetch_success)

    args = SimpleNamespace(
        assignments_command="fetch",
        entity_id="cnu_assignment:course-1:task-101",
        out=None,
        headless_override=None,
    )
    result, errors = assignments.dispatch(args)
    assert errors is None
    assert result["source_package"]["completeness"] == "complete"
    assert result["source_package"]["entity_id"] == "cnu_assignment:course-1:task-101"

    # Verify human rendering of complete package
    human_lines = assignments.render("assignments.fetch", result, 80)
    assert any("Assignment source: cnu_assignment:course-1:task-101" in line for line in human_lines)
    assert any("Package: /data/sources/assignment/abc/def" in line for line in human_lines)
    assert any("Completeness: complete" in line for line in human_lines)
    assert not any("Omitted:" in line for line in human_lines)

    # 3. Test policy-filtered partial result
    fake_pkg_partial = dict(fake_pkg_complete)
    fake_pkg_partial["completeness"] = "policy-filtered"
    fake_pkg_partial["omitted_resources"] = [
        {
            "resource_id": "res-2",
            "source_ref": {},
            "original_name": "lecture.mp4",
            "media_type": "video/mp4",
            "reason": "unsupported-media-type",
        }
    ]

    async def mock_fetch_partial(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return fake_pkg_partial

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_fetch_partial)
    result_p, errors_p = assignments.dispatch(args)
    assert errors_p is not None
    assert len(errors_p) == 1
    assert errors_p[0].code == "resource-omitted"
    assert result_p["source_package"]["completeness"] == "policy-filtered"

    # Human render of partial
    human_lines_p = assignments.render("assignments.fetch", result_p, 80)
    assert any("Completeness: policy-filtered" in line for line in human_lines_p)
    assert any("Omitted: unsupported-media-type — lecture.mp4" in line for line in human_lines_p)

    # 4. Error: catalog-missing
    monkeypatch.setattr(assignments, "data_dir", lambda: tmp_path / "nonexistent")
    with pytest.raises(CampusError) as exc_cat:
        assignments.dispatch(args)
    assert exc_cat.value.code == "catalog-missing"
    monkeypatch.setattr(assignments, "data_dir", lambda: tmp_path)

    # 5. Error: entity-unknown (including numeric material selection)
    args_unknown = SimpleNamespace(
        assignments_command="fetch", entity_id="cnu_assignment:course-1:task-999", out=None, headless_override=None
    )
    with pytest.raises(CampusError) as exc_unk:
        assignments.dispatch(args_unknown)
    assert exc_unk.value.code == "entity-unknown"

    args_num = SimpleNamespace(assignments_command="fetch", entity_id="1", out=None, headless_override=None)
    with pytest.raises(CampusError) as exc_num:
        assignments.dispatch(args_num)
    assert exc_num.value.code == "entity-unknown"

    # 6. Error: output-path-conflict
    existing_file = tmp_path / "already_exists"
    existing_file.write_text("exists")
    args_conflict = SimpleNamespace(
        assignments_command="fetch",
        entity_id="cnu_assignment:course-1:task-101",
        out=str(existing_file),
        headless_override=None,
    )
    with pytest.raises(CampusError) as exc_conf:
        assignments.dispatch(args_conflict)
    assert exc_conf.value.code == "output-path-conflict"

    # 7. Error: headless-unavailable (unsupported effective headless fails before navigation)
    args_headless = SimpleNamespace(
        assignments_command="fetch", entity_id="cnu_assignment:course-1:task-101", out=None, headless_override=True
    )
    with pytest.raises(CampusError) as exc_hl:
        assignments.dispatch(args_headless)
    assert exc_hl.value.code == "headless-unavailable"

    # 8. Error: session-busy
    async def mock_fetch_busy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CampusError("session-busy", "Browser session lock is occupied.", status="busy")

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_fetch_busy)
    with pytest.raises(CampusError) as exc_busy:
        assignments.dispatch(args)
    assert exc_busy.value.code == "session-busy"
    assert exc_busy.value.status == "busy"
