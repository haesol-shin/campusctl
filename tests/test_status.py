"""Synthetic local catalogs only; no browser or LMS access."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from campusctl.envelope import CampusError
from campusctl.status import build_status

NOW = datetime(2026, 3, 1, 15, tzinfo=UTC)  # Seoul: March 2 at midnight.
A = {"course_id": "course-a", "label": "Course A"}
B = {"course_id": "course-b", "label": "Course B"}


def _write(root: Path, domain: str, rows: list[dict], *, courses: list[dict] | None = None, **metadata: object) -> None:
    location = root / "catalog" / f"{domain}.json"
    location.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1,
        "generated_at": "2026-03-01T14:00:00Z",
        "enrollment_state": "known",
        "courses": courses if courses is not None else [A, B],
        "failed_courses": [],
        domain: rows,
        **metadata,
    }
    location.write_text(json.dumps(value), encoding="utf-8")


def _row(identity: str, course: str = "course-a", **fields: object) -> dict:
    return {
        "entity_id": identity,
        "course": {"id": course, "label": "Course A" if course == "course-a" else "Course B"},
        **fields,
    }


def test_fixed_seoul_window_and_known_unknown_states(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "assignments",
        [
            _row("a-start", due_date="2026-03-02T00:00", is_submitted=False),
            _row("a-last", due_date="2026-03-08", is_submitted=False),
            _row("a-end", due_date="2026-03-09T00:00", is_submitted=False),
            _row("a-before", due_date="2026-03-01T23:59", is_submitted=False),
            _row("a-submitted", due_date="2026-03-03", is_submitted=True),
            _row("a-unknown", due_date="not a date", is_submitted=False),
            _row("a-other", "course-b", due_date=None, is_submitted=False),
        ],
    )
    _write(
        tmp_path,
        "notices",
        [
            _row("n-old", is_unread=True, posted_date="2026-03-01"),
            _row("n-new", is_unread=True, posted_date="2026-03-02T12:00"),
            _row("n-unknown", is_unread=None),
            _row("n-read", is_unread=False),
            _row("n-other", "course-b", is_unread=None),
        ],
    )
    _write(
        tmp_path,
        "lectures",
        [
            _row(
                "l-soon", completion="incomplete", open=True, due_date="2026-03-03", available_from="2026-03-02T12:00"
            ),
            _row(
                "l-tie",
                completion="incomplete",
                open=True,
                due_date="2026-03-04T12:00",
                available_from="2026-03-04T12:00",
            ),
            _row("l-past", completion="incomplete", open=True, due_date="2026-03-01T23:00"),
            _row("l-unknown", completion="incomplete", open=None),
            _row("l-closed", completion="incomplete", open=False),
            _row("l-complete", completion="complete", open=True),
            _row("l-other", "course-b", completion="incomplete", open=None),
        ],
    )
    _write(tmp_path, "materials", [])

    result, status, errors = build_status(tmp_path, "course-a", now=NOW)
    assert (status, errors) == ("ok", [])
    assert result["as_of"] == "2026-03-01T15:00:00Z"
    assert result["timezone"] == "Asia/Seoul"
    assert result["window"] == {
        "start": "2026-03-02T00:00:00+09:00",
        "end": "2026-03-09T00:00:00+09:00",
        "end_inclusive": False,
    }
    assert [row["entity_id"] for row in result["assignments"]["due_soon"]] == ["a-start", "a-last"]
    assert result["assignments"]["unknown_due_count"] == 1
    assert [row["entity_id"] for row in result["notices"]["unread"]] == ["n-new", "n-old"]
    assert result["notices"]["unknown_count"] == 1
    assert [row["entity_id"] for row in result["lectures"]["open_incomplete"]] == ["l-soon", "l-tie", "l-past"]
    assert result["lectures"]["unknown_open_count"] == 1
    assert [(row["next_at"], row["next_kind"]) for row in result["lectures"]["open_incomplete"]] == [
        ("2026-03-02T12:00:00+09:00", "availability"),
        ("2026-03-04T12:00:00+09:00", "deadline"),
        (None, None),
    ]
    assert result["stale_courses"] == []


def test_partial_coverage_failed_courses_and_filtered_stale_rows(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "lectures",
        [],
        courses=[A, B],
        enrollment_state="unknown",
        failed_courses=[{"course_id": "course-a", "label": "Course A", "reason": "collection-failed"}],
    )
    _write(
        tmp_path,
        "notices",
        [_row("n-b", "course-b", is_unread=True)],
        courses=[A, B],
        generated_at="2026-02-28T00:00:00Z",
    )
    (tmp_path / "catalog" / "materials.json").write_text("not json", encoding="utf-8")
    result, status, errors = build_status(tmp_path, "course-a", now=NOW)
    assert status == "partial"
    assert [(error["domain"], error["code"]) for error in errors] == [
        ("assignments", "catalog-missing"),
        ("materials", "catalog-invalid"),
    ]
    assert {domain: item["state"] for domain, item in result["coverage"].items()} == {
        "lectures": "available",
        "assignments": "missing",
        "notices": "available",
        "materials": "invalid",
    }
    assert result["notices"]["unread"] == []
    assert result["stale_courses"] == [
        {
            "course_id": "course-a",
            "label": "Course A",
            "domains": ["lectures", "notices"],
            "reasons": ["catalog-stale", "course-sync-failed", "enrollment-unknown"],
        }
    ]


def test_no_readable_catalog_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(CampusError) as caught:
        build_status(tmp_path, None, now=NOW)
    assert caught.value.code == "catalog-missing"
    assert caught.value.status == "user-action"


def test_date_only_deadline_excludes_end_day_and_keeps_unread_undated(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "assignments",
        [
            _row("within", due_date="2026-03-08", is_submitted=False),
            _row("outside", due_date="2026-03-09", is_submitted=False),
            _row("bad", due_date="2026-02-30", is_submitted=False),
        ],
    )
    _write(tmp_path, "notices", [_row("unknown-date", is_unread=True, posted_date=None)])
    result, status, _ = build_status(tmp_path, None, now=NOW)
    assert status == "partial"
    assert [row["entity_id"] for row in result["assignments"]["due_soon"]] == ["within"]
    assert result["assignments"]["unknown_due_count"] == 1
    assert [row["entity_id"] for row in result["notices"]["unread"]] == ["unknown-date"]
