from __future__ import annotations

import json
from pathlib import Path

import pytest

from campusctl.catalog_view import (
    assert_course_snapshot_current,
    course_roster,
    publish_course_snapshot,
    read_course_snapshot,
)
from campusctl.course_selection import CourseAmbiguous, resolve_course
from campusctl.domain_catalog import write_domain_catalog
from campusctl.envelope import CampusError


def _save(root: Path, courses: list[dict[str, str]]) -> None:
    write_domain_catalog(
        "materials", {"generated_at": "2026-01-01T00:00:00Z", "courses": courses}, root / "catalog" / "materials.json"
    )


def _course(course_id: str, label: str) -> dict[str, str]:
    return {"course_id": course_id, "label": label, "class_no": "01"}


def test_exact_numeric_id_and_normalized_human_name(tmp_path: Path) -> None:
    _save(tmp_path, [_course("42", "Logic"), _course("course-b", "Data\u3000Science")])
    roster = course_roster(tmp_path)
    assert resolve_course("42", roster, ids_only=False) == "42"
    assert resolve_course("  dAtA science ", roster, ids_only=False) == "course-b"
    assert resolve_course("course-b", roster, ids_only=True) == "course-b"
    with pytest.raises(CampusError) as failure:
        resolve_course("DataScience", roster, ids_only=True)
    assert (failure.value.code, failure.value.status) == ("course-id-required", "user-action")
    with pytest.raises(CampusError) as failure:
        resolve_course("2", roster, ids_only=True)
    assert failure.value.code == "course-index-unavailable"
    with pytest.raises(CampusError) as failure:
        resolve_course("2", roster, ids_only=False)
    assert failure.value.code == "selection-missing"


def test_printed_indices_invalidate_on_same_second_replacement_and_lower_sort_insert(tmp_path: Path) -> None:
    original = [_course("course-b", "Biology"), _course("course-c", "Chemistry")]
    _save(tmp_path, original)
    first = course_roster(tmp_path)
    snapshot = publish_course_snapshot(tmp_path, first)
    assert resolve_course("1", first, ids_only=False, printed_roster=snapshot) == "course-b"
    _save(tmp_path, original)
    replacement = course_roster(tmp_path)
    assert replacement["roster_generation"] != first["roster_generation"]
    with pytest.raises(CampusError, match="no longer current") as failure:
        resolve_course("1", replacement, ids_only=False, printed_roster=snapshot)
    assert failure.value.code == "selection-stale"
    with pytest.raises(CampusError) as failure:
        assert_course_snapshot_current(tmp_path, snapshot)
    assert failure.value.code == "selection-stale"
    assert resolve_course("course-b", replacement, ids_only=False, printed_roster=snapshot) == "course-b"
    _save(tmp_path, [_course("course-a", "Algebra"), *original])
    with pytest.raises(CampusError) as failure:
        resolve_course("1", course_roster(tmp_path), ids_only=False, printed_roster=snapshot)
    assert failure.value.code == "selection-stale"


def test_rename_and_removal_invalidate_but_json_never_replaces_snapshot(tmp_path: Path) -> None:
    _save(tmp_path, [_course("course-a", "Algebra"), _course("course-b", "Biology")])
    snapshot = publish_course_snapshot(tmp_path, course_roster(tmp_path))
    assert read_course_snapshot(tmp_path) == snapshot
    with pytest.raises(CampusError) as failure:
        resolve_course("1", course_roster(tmp_path), ids_only=True, printed_roster=snapshot)
    assert failure.value.code == "course-index-unavailable"
    _save(tmp_path, [_course("course-b", "Biology")])
    with pytest.raises(CampusError) as failure:
        assert_course_snapshot_current(tmp_path, snapshot)
    assert failure.value.code == "selection-stale"
    _save(tmp_path, [_course("course-a", "New Algebra"), _course("course-b", "Biology")])
    with pytest.raises(CampusError) as failure:
        assert_course_snapshot_current(tmp_path, snapshot)
    assert failure.value.code == "selection-stale"


def test_ambiguous_invalid_and_ids_only_live_roster() -> None:
    rows = [_course("course-a", "Linear Analysis"), _course("course-b", "Applied Analysis")]
    with pytest.raises(CourseAmbiguous) as failure:
        resolve_course("analysis", rows, ids_only=False)
    assert failure.value.candidates == [
        {"course_id": "course-a", "label": "Linear Analysis"},
        {"course_id": "course-b", "label": "Applied Analysis"},
    ]
    for selector, code in [
        (" \u3000 ", "selection-invalid"),
        ("99", "selection-missing"),
        ("missing", "course-not-found"),
    ]:
        with pytest.raises(CampusError) as failure:
            resolve_course(selector, rows, ids_only=False)
        assert failure.value.code == code
    assert resolve_course("course-b", rows, ids_only=True) == "course-b"
    for selector, code in [("99", "course-index-unavailable"), ("Applied", "course-id-required")]:
        with pytest.raises(CampusError) as failure:
            resolve_course(selector, rows, ids_only=True)
        assert failure.value.code == code


def test_out_of_range_and_corrupt_snapshot(tmp_path: Path) -> None:
    _save(tmp_path, [_course("course-a", "Algebra")])
    roster = course_roster(tmp_path)
    with pytest.raises(CampusError) as failure:
        read_course_snapshot(tmp_path)
    assert failure.value.code == "selection-missing"
    snapshot = publish_course_snapshot(tmp_path, roster)
    for selector in ("0", "2", "999", "9" * 5000):
        with pytest.raises(CampusError) as failure:
            resolve_course(selector, roster, ids_only=False, printed_roster=snapshot)
        assert failure.value.code == "selection-invalid"
        assert failure.value.status == "user-action"
    path = tmp_path / "selection" / "courses-last-list.json"
    path.write_text(json.dumps({**snapshot, "courses": [_course("course-z", "Changed")]}))
    with pytest.raises(CampusError) as failure:
        read_course_snapshot(tmp_path)
    assert failure.value.code == "selection-invalid"
    (tmp_path / "selection" / "courses-last-list.json").write_text(json.dumps({"schema_version": 2}))
    with pytest.raises(CampusError) as failure:
        read_course_snapshot(tmp_path)
    assert failure.value.code == "selection-invalid"
