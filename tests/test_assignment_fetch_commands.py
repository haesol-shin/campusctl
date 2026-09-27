"""Tests for selected assignment fetch, dispatch, and render."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.commands import assignments, notices
from campusctl.envelope import CampusError, error_item


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

    # Complete package
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

    # Unsupported-resource partial result
    fake_pkg_partial = dict(fake_pkg_complete)
    fake_pkg_partial["completeness"] = "partial"
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
    assert "Assignments fetch packaging" in errors_p[0].message
    assert result_p["source_package"]["completeness"] == "partial"

    # Human render of partial
    human_lines_p = assignments.render("assignments.fetch", result_p, 80)
    assert any("Completeness: partial" in line for line in human_lines_p)
    assert any("Omitted: unsupported-media-type — lecture.mp4" in line for line in human_lines_p)

    # Missing catalog
    monkeypatch.setattr(assignments, "data_dir", lambda: tmp_path / "nonexistent")
    with pytest.raises(CampusError) as exc_cat:
        assignments.dispatch(args)
    assert exc_cat.value.code == "catalog-missing"
    assert "Assignments fetch catalog lookup" in exc_cat.value.message
    monkeypatch.setattr(assignments, "data_dir", lambda: tmp_path)

    # Unknown ID, including numeric material selection
    args_unknown = SimpleNamespace(
        assignments_command="fetch", entity_id="cnu_assignment:course-1:task-999", out=None, headless_override=None
    )
    with pytest.raises(CampusError) as exc_unk:
        assignments.dispatch(args_unknown)
    assert exc_unk.value.code == "entity-unknown"
    assert "Assignments fetch selection" in exc_unk.value.message
    assert "task-999" not in exc_unk.value.message

    args_num = SimpleNamespace(assignments_command="fetch", entity_id="1", out=None, headless_override=None)
    with pytest.raises(CampusError) as exc_num:
        assignments.dispatch(args_num)
    assert exc_num.value.code == "entity-unknown"

    # Existing output path
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
    assert "Assignments fetch output selection" in exc_conf.value.message
    assert str(existing_file) not in exc_conf.value.message

    # Unsupported effective headless fails before navigation
    args_headless = SimpleNamespace(
        assignments_command="fetch", entity_id="cnu_assignment:course-1:task-101", out=None, headless_override=True
    )
    with pytest.raises(CampusError) as exc_hl:
        assignments.dispatch(args_headless)
    assert exc_hl.value.code == "headless-unavailable"
    assert "Assignments fetch browser preflight" in exc_hl.value.message

    # Busy session
    async def mock_fetch_busy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CampusError("session-busy", "Browser session lock is occupied.", status="busy")

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_fetch_busy)
    with pytest.raises(CampusError) as exc_busy:
        assignments.dispatch(args)
    assert exc_busy.value.code == "session-busy"
    assert exc_busy.value.status == "busy"


@pytest.mark.parametrize(
    ("command", "kind"),
    [(assignments, "Assignments"), (notices, "Notices")],
)
@pytest.mark.parametrize(
    "failed_step",
    ["browser session", "authentication", "SSO settlement", "detail capture", "package creation"],
)
def test_fetch_error_names_stage_without_exposing_provider_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: Any,
    kind: str,
    failed_step: str,
) -> None:
    secret = "TB_L_REPORT987654321"
    origin = CampusError("entity-unknown", f"Private provider URL contains {secret}", status="user-action")

    @contextlib.asynccontextmanager
    async def open_session(*_args: Any, **_kwargs: Any):
        if failed_step == "browser session":
            raise origin
        yield SimpleNamespace(page=object())

    async def stage(name: str) -> None:
        if failed_step == name:
            raise origin

    async def login(*_args: Any) -> None:
        await stage("authentication")

    async def settle(*_args: Any, **_kwargs: Any) -> None:
        await stage("SSO settlement")

    async def capture(*_args: Any) -> object:
        await stage("detail capture")
        return object()

    async def package(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await stage("package creation")
        return {"completeness": "complete"}

    monkeypatch.setattr("campusctl.browser.open_session", open_session)
    monkeypatch.setattr("campusctl.browser.settle_sso_popups", settle)
    monkeypatch.setattr("campusctl.providers.cnu.login.ensure_logged_in", login)
    monkeypatch.setattr("campusctl.providers.cnu.assignment_detail.capture_assignment_detail", capture)
    monkeypatch.setattr("campusctl.providers.cnu.notice_detail.capture_notice_detail", capture)
    monkeypatch.setattr("campusctl.source_package.build_source_package", package)
    row = {"entity_id": secret, "course": {"id": secret, "label": "Example course"}}
    fetch = command._fetch_assignment if command is assignments else command._fetch_notice

    with pytest.raises(CampusError) as failure:
        asyncio.run(fetch({}, tmp_path, row))
    item = error_item(failure.value)
    assert item["code"] == "entity-unknown"
    assert item["message"] == f"{kind} fetch: {failed_step} failed."
    assert secret not in str(item)


def test_fetch_timeout_keeps_only_known_detail_step() -> None:
    known = CampusError(
        "browser-timeout",
        "Timed out while opening notice board.",
        "Private provider URL contains TB_L_BOARDITEM123.",
        "error",
    )
    safe = notices._fetch_error("detail capture", known)
    assert error_item(safe)["message"] == "Notices fetch: opening course notice board timed out."
    assert "TB_L_BOARDITEM123" not in str(error_item(safe))

    unknown = CampusError(
        "browser-timeout", "Timed out while opening notice board?no=TB_L_BOARDITEM123.", status="error"
    )
    masked = notices._fetch_error("detail capture", unknown)
    assert error_item(masked)["message"] == "Notices fetch: detail capture failed."
    assert "TB_L_BOARDITEM123" not in str(error_item(masked))


@pytest.mark.parametrize(
    ("command", "domain"),
    [(assignments, "assignments"), (notices, "notices")],
)
@pytest.mark.parametrize("stage", ["catalog load", "configuration load"])
def test_fetch_preflight_failure_names_safe_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: Any, domain: str, stage: str
) -> None:
    secret = "TB_L_BOARDITEM987654321"
    catalog = tmp_path / "catalog" / f"{domain}.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("{}")
    monkeypatch.setattr(command, "data_dir", lambda: tmp_path)
    row = {"entity_id": secret, "course": {"id": "course-a", "label": "Example course"}}
    origin = CampusError("catalog-corrupt", f"Private provider ID {secret}", f"Do not expose {secret}", "user-action")

    def read_catalog(*_args: Any) -> dict[str, Any]:
        if stage == "catalog load":
            raise origin
        return {domain: [row]}

    def load_config() -> dict[str, Any]:
        raise origin

    monkeypatch.setattr(command, "read_domain_catalog", read_catalog)
    monkeypatch.setattr("campusctl.config.load_config", load_config)
    args = SimpleNamespace(
        **{f"{domain}_command": "fetch"},
        entity_id=secret,
        out=None,
        headless_override=None,
    )
    with pytest.raises(CampusError) as failure:
        command.dispatch(args)
    item = error_item(failure.value)
    assert item["code"] == "catalog-corrupt"
    assert item["message"] == f"{domain.title()} fetch: {stage} failed."
    assert secret not in str(item)
