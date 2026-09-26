from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from campusctl import cli
from campusctl.commands import assignments

TIMESTAMP = "2026-09-25T10:00:00Z"


def _row(course_id: str, task_id: str, *, submitted: bool, due: str | None) -> dict[str, Any]:
    return {
        "entity_id": f"cnu_assignment:{course_id}:{task_id}",
        "task_id": task_id,
        "course": {"id": course_id, "label": f"Course {course_id}"},
        "kind": "assignment",
        "title": "Synthetic task",
        "due_date": due,
        "is_submitted": submitted,
    }


def _catalog(root: Path) -> list[dict[str, Any]]:
    rows = [
        _row("course-alpha", "TB_L_REPORT12345678901234567890", submitted=True, due="2026-09-28 23:59"),
        _row("course-beta", "TB_L_REPORT98765432109876543210", submitted=False, due=None),
    ]
    path = root / "catalog" / "assignments.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": TIMESTAMP,
                "enrollment_state": "unknown",
                "courses": [
                    {"course_id": course_id, "label": f"Course {course_id}", "class_no": None}
                    for course_id in ("course-alpha", "course-beta")
                ],
                "failed_courses": [
                    {"course_id": "course-beta", "label": "Course course-beta", "reason": "removal-deferred"}
                ],
                "assignments": rows,
            }
        ),
        encoding="utf-8",
    )
    return rows


def _invoke(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = cli.main(args)
    output = capsys.readouterr()
    assert not output.err
    return code, json.loads(output.out)


def test_cached_list_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    rows = _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    code, response = _invoke(["assignments", "list", "--json"], capsys)
    assert code == 0
    assert response["result"]["assignments"] == rows
    cache = response["result"]["cache"]
    assert cache["generated_at"] == TIMESTAMP
    assert cache["stale"] is True
    assert cache["failed_courses"][0]["course_id"] == "course-beta"
    assert assignments.CAPABILITY["commands"] == ["list"]


def test_narrow_list_full_ids_and_stale_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    code, response = _invoke(["assignments", "list", "--course", "course-alpha", "--json"], capsys)
    assert code == 0
    assert response["result"]["assignments"] == rows[:1]
    lines = assignments.render("assignments.list", response["result"], 18)
    assert rows[0]["entity_id"] in [line.strip() for line in lines]
    assert "Submitted" in "\n".join(lines)
    assert "2026-09-28 23:59" in "\n".join(lines)
    assert "enrollment is unknown" in "\n".join(lines)
    assert "removal-deferred" in "\n".join(lines)

    code, empty = _invoke(["assignments", "list", "--course", "nonexistent", "--json"], capsys)
    assert code == 2 and empty["errors"][0]["code"] == "course-id-required"
    all_lines = assignments.render("assignments.list", {**response["result"], "assignments": rows}, 12)
    assert all(row["entity_id"] in [line.strip() for line in all_lines] for row in rows)
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    assert cli.main(["assignments", "list", "--course", "course-alpha"]) == 0
    displayed = capsys.readouterr().out
    assert rows[0]["entity_id"] in displayed
    assert "enrollment is unknown" in displayed


def test_missing_catalog_explains_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    code, response = _invoke(["assignments", "list", "--json"], capsys)
    assert code == 2
    assert response["errors"][0]["code"] == "catalog-missing"
    assert response["errors"][0]["remediation"] == "Run 'campusctl sync --only assignments' to create it."
