from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from campusctl import cli

GENERATED_AT = "2026-10-01T12:00:00Z"
COURSES = [
    {"course_id": "course-z", "label": "Course Z", "class_no": None},
    {"course_id": "course-a", "label": "Course A", "class_no": "001"},
]


def _lecture(
    entity_id: str,
    course_id: str,
    week: str | None,
    sequence: str | None,
    *,
    completion: str = "incomplete",
    media: str = "video",
    progress_text: str | None = None,
    attendance_counted: bool | None = None,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "course": {"id": course_id, "label": course_id},
        "kind": "lecture",
        "title": entity_id,
        "week": week,
        "sequence": sequence,
        "progress_text": progress_text,
        "duration_minutes": None,
        "available_from": None,
        "due_date": None,
        "late_until": None,
        "media": media,
        "attendance_counted": attendance_counted,
        "open": None,
        "completion": completion,
        "provider_state": None,
    }


LECTURES = [
    _lecture("cnu_lecture:course-a:a-1", "course-a", "1", "1"),
    _lecture("cnu_lecture:course-z:z-10", "course-z", "2", "10", media="youtube"),
    _lecture("cnu_lecture:course-z:z-2", "course-z", "2", "2"),
    _lecture("cnu_lecture:course-z:z-10-week", "course-z", "10", "1"),
    _lecture(
        "cnu_lecture:course-z:z-recorded",
        "course-z",
        "11",
        "1",
        completion="recorded",
        media="youtube",
        progress_text="20분/20분",
        attendance_counted=False,
    ),
    _lecture("cnu_lecture:course-z:z-done", "course-z", "2", "1", completion="complete"),
]


def _write_catalog(data_dir: Path, *, courses: list[dict] = COURSES, lectures: list[dict] = LECTURES) -> None:
    path = data_dir / "catalog" / "lectures.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": GENERATED_AT,
                "courses": courses,
                "lectures": lectures,
            }
        ),
        encoding="utf-8",
    )


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    assert captured.err == ""
    return exit_code, json.loads(captured.out)


def test_courses_list_reads_catalog_and_returns_cache_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))

    exit_code, response = _invoke(["courses", "list", "--json"], capsys)

    assert exit_code == 0
    assert response["status"] == "ok"
    assert [row["course_id"] for row in response["result"]["courses"]] == ["course-a", "course-z"]
    assert response["result"]["cache"]["generated_at"] == GENERATED_AT
    assert response["result"]["cache"]["stale_after_seconds"] == 21600
    assert response["result"]["cache"]["path_present"] is True


def test_lectures_list_filters_completed_and_sorts_course_week_and_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))

    exit_code, response = _invoke(["lectures", "list", "--json"], capsys)

    assert exit_code == 0
    assert response["result"]["cache"]["generated_at"] == GENERATED_AT
    assert response["result"]["cache"]["stale_after_seconds"] == 21600
    assert response["result"]["cache"]["path_present"] is True
    assert [lecture["entity_id"] for lecture in response["result"]["lectures"]] == [
        "cnu_lecture:course-z:z-2",
        "cnu_lecture:course-z:z-10",
        "cnu_lecture:course-z:z-10-week",
        "cnu_lecture:course-a:a-1",
    ]
    assert (
        next(
            lecture for lecture in response["result"]["lectures"] if lecture["entity_id"] == "cnu_lecture:course-z:z-10"
        )["media"]
        == "youtube"
    )


def test_lectures_list_all_includes_completed_and_recorded_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))

    exit_code, response = _invoke(["lectures", "list", "--all", "--json"], capsys)

    assert exit_code == 0
    assert [lecture["entity_id"] for lecture in response["result"]["lectures"]] == [
        "cnu_lecture:course-z:z-done",
        "cnu_lecture:course-z:z-2",
        "cnu_lecture:course-z:z-10",
        "cnu_lecture:course-z:z-10-week",
        "cnu_lecture:course-z:z-recorded",
        "cnu_lecture:course-a:a-1",
    ]


def test_lectures_list_filters_by_course(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))

    exit_code, response = _invoke(["lectures", "list", "--course", "course-a", "--json"], capsys)

    assert exit_code == 0
    assert [lecture["entity_id"] for lecture in response["result"]["lectures"]] == ["cnu_lecture:course-a:a-1"]


def test_unknown_course_returns_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))

    exit_code, response = _invoke(["lectures", "list", "--course", "unknown-course", "--json"], capsys)

    assert exit_code == 2
    assert response["errors"][0]["code"] == "course-id-required"


def test_list_commands_report_missing_catalog_with_sync_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))

    exit_code, response = _invoke(["lectures", "list", "--json"], capsys)

    assert exit_code == 2
    assert response["status"] == "user-action"
    assert response["errors"][0]["code"] == "catalog-missing"
    assert response["errors"][0]["remediation"] == "Run 'campusctl sync --only lectures' to create it."
