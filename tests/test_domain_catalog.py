from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from campusctl.domain_catalog import (
    domain_catalog_path,
    mark_enrollment_unknown,
    merge_domain_catalog,
    read_domain_catalog,
    write_domain_catalog,
)
from campusctl.envelope import CampusError

FIXTURE = Path(__file__).parent / "fixtures" / "lms_sources" / "catalog_transitions.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _merge_vector(vector: dict[str, Any], *, domain: str = "assignments") -> dict[str, Any]:
    return merge_domain_catalog(
        domain,
        vector["previous"],
        vector["roster"],
        vector["rows"],
        successful_course_ids=set(vector["successful_course_ids"]),
        failed_courses=vector["failed_courses"],
        selected_course_id=vector.get("selected_course_id"),
    )


def _assert_merge_result(actual: dict[str, Any], expected: dict[str, Any], *, old_timestamp: str) -> None:
    expected = dict(expected)
    if expected["generated_at"] == "<merge-time>":
        timestamp = actual["generated_at"]
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert parsed.tzinfo == UTC
        assert timestamp != old_timestamp
        expected["generated_at"] = timestamp
    assert actual == expected


def test_fixture_merge_transitions(tmp_path: Path) -> None:
    transitions = _fixture()["transitions"]

    for name in ("full", "partial", "filtered"):
        vector = transitions[name]
        actual = _merge_vector(vector)
        _assert_merge_result(actual, vector["expected"], old_timestamp=vector["previous"]["generated_at"])

    unknown = transitions["unknown"]
    path = domain_catalog_path("assignments", tmp_path)
    write_domain_catalog("assignments", unknown["previous"], path)
    mark_enrollment_unknown("assignments", tmp_path)
    actual = read_domain_catalog("assignments", path)
    assert actual.pop("generation_id") != unknown["previous"].get("generation_id")
    assert actual == unknown["expected"]


@pytest.mark.parametrize("domain", ["assignments", "notices", "materials"])
def test_first_filtered_catalog_remains_unknown_until_full_success(domain: str) -> None:
    course = {"course_id": "synthetic-course", "label": "Synthetic course", "class_no": None}
    filtered = merge_domain_catalog(
        domain,
        None,
        [course],
        [],
        successful_course_ids={"synthetic-course"},
        failed_courses=[],
        selected_course_id="synthetic-course",
    )
    assert filtered["enrollment_state"] == "unknown"
    assert filtered["failed_courses"] == []
    full = merge_domain_catalog(
        domain,
        filtered,
        [course],
        [],
        successful_course_ids={"synthetic-course"},
        failed_courses=[],
    )
    assert full["enrollment_state"] == "known"
    assert full["failed_courses"] == []


def test_domain_paths_and_missing_catalog_are_domain_specific(tmp_path: Path) -> None:
    assert domain_catalog_path("assignments", tmp_path) == tmp_path / "catalog" / "assignments.json"
    assert domain_catalog_path("notices", tmp_path) != domain_catalog_path("materials", tmp_path)
    with pytest.raises(CampusError) as missing:
        read_domain_catalog("notices", tmp_path / "notices.json")
    assert missing.value.code == "catalog-missing"
    assert missing.value.remediation == "Run 'campusctl sync --only notices' to create it."

    mark_enrollment_unknown("materials", tmp_path)
    assert not domain_catalog_path("materials", tmp_path).exists()


def test_atomic_write_round_trips_private_catalog(tmp_path: Path) -> None:
    path = domain_catalog_path("notices", tmp_path)
    value = {
        "generated_at": "2026-01-02T03:04:05Z",
        "enrollment_state": "known",
        "courses": [],
        "failed_courses": [],
        "notices": [],
    }

    assert write_domain_catalog("notices", value, path) == path
    saved = read_domain_catalog("notices", path)
    assert len(saved.pop("generation_id")) == 32
    assert saved == {"schema_version": 1, **value}
    assert list(path.parent.glob(".notices.json.*.tmp")) == []
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_failed_atomic_replace_preserves_old_catalog_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = domain_catalog_path("assignments", tmp_path)
    old = {
        "schema_version": 1,
        "generated_at": "2026-01-02T03:04:05Z",
        "enrollment_state": "known",
        "courses": [],
        "failed_courses": [],
        "assignments": [],
    }
    write_domain_catalog("assignments", old, path)
    prior_bytes = path.read_bytes()

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("sentinel filesystem failure")

    monkeypatch.setattr("campusctl.domain_catalog.os.replace", fail_replace)
    with pytest.raises(CampusError) as failure:
        write_domain_catalog("assignments", {**old, "enrollment_state": "unknown"}, path)

    assert failure.value.code == "catalog-write-failed"
    assert path.read_bytes() == prior_bytes
    assert list(path.parent.glob(".assignments.json.*.tmp")) == []


def test_filtered_discovery_failure_leaves_catalog_unchanged() -> None:
    previous = _fixture()["transitions"]["filtered"]["previous"]
    actual = merge_domain_catalog(
        "assignments",
        previous,
        [],
        [],
        successful_course_ids=set(),
        failed_courses=[],
        selected_course_id="course-a",
    )
    assert actual is previous


def test_filtered_discovery_failure_without_catalog_raises() -> None:
    with pytest.raises(CampusError) as failure:
        merge_domain_catalog(
            "assignments",
            None,
            [],
            [],
            successful_course_ids=set(),
            failed_courses=[],
            selected_course_id="course-a",
        )
    assert failure.value.code == "course-discovery-failed"


def test_unaccounted_filtered_course_gets_stale_failure_marker() -> None:
    previous = _fixture()["transitions"]["filtered"]["previous"]
    actual = merge_domain_catalog(
        "assignments",
        previous,
        [{"course_id": "course-a", "label": "Course A", "class_no": "001"}],
        [{"entity_id": "assignment:partial-a", "course": {"id": "course-a", "label": "Course A"}}],
        successful_course_ids=set(),
        failed_courses=[],
        selected_course_id="course-a",
    )

    assert actual["enrollment_state"] == previous["enrollment_state"]
    assert actual["assignments"] == previous["assignments"]
    assert actual["failed_courses"] == [
        previous["failed_courses"][0],
        {"course_id": "course-a", "label": "Course A", "reason": "course-sync-failed"},
    ]


def test_failed_filtered_course_keeps_target_rows_and_other_state() -> None:
    previous = _fixture()["transitions"]["filtered"]["previous"]
    actual = merge_domain_catalog(
        "assignments",
        previous,
        [{"course_id": "course-a", "label": "Course A", "class_no": "001"}],
        [
            {"entity_id": "assignment:partial-a", "course": {"id": "course-a", "label": "Course A"}},
            {"entity_id": "assignment:unused-b", "course": {"id": "course-b", "label": "Course B"}},
        ],
        successful_course_ids=set(),
        failed_courses=[{"course_id": "course-a", "label": "Course A", "reason": "item-identity-missing"}],
        selected_course_id="course-a",
    )

    assert actual["enrollment_state"] == previous["enrollment_state"]
    assert actual["assignments"] == previous["assignments"]
    assert actual["failed_courses"] == [
        previous["failed_courses"][0],
        {"course_id": "course-a", "label": "Course A", "reason": "item-identity-missing"},
    ]


def test_merge_rejects_duplicate_course_and_row_ids() -> None:
    with pytest.raises(CampusError):
        merge_domain_catalog(
            "assignments",
            None,
            [
                {"course_id": "course-a", "label": "A", "class_no": None},
                {"course_id": "course-a", "label": "A duplicate", "class_no": None},
            ],
            [],
            successful_course_ids={"course-a"},
            failed_courses=[],
        )

    duplicate = {"entity_id": "assignment:one", "course": {"id": "course-a", "label": "A"}}
    with pytest.raises(CampusError):
        merge_domain_catalog(
            "assignments",
            None,
            [{"course_id": "course-a", "label": "A", "class_no": None}],
            [duplicate, duplicate],
            successful_course_ids={"course-a"},
            failed_courses=[],
        )
