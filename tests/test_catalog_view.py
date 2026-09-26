from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from campusctl.catalog import catalog_path, write_catalog
from campusctl.catalog_view import (
    assert_course_snapshot_current,
    assert_material_snapshot_current,
    cache_metadata,
    catalog_generation,
    catalog_snapshot,
    course_roster,
    course_snapshot_path,
    material_snapshot_path,
    publish_course_snapshot,
    publish_material_snapshot,
    read_course_snapshot,
    resolve_material_number,
)
from campusctl.domain_catalog import domain_catalog_path, write_domain_catalog
from campusctl.envelope import CampusError

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _course(identifier: str, label: str) -> dict[str, str | None]:
    return {"course_id": identifier, "label": label, "class_no": None}


def test_domain_only_roster_priority_and_sorting(tmp_path: Path) -> None:
    with pytest.raises(CampusError) as failure:
        course_roster(tmp_path)
    assert failure.value.code == "catalog-missing"
    write_domain_catalog(
        "materials",
        {"courses": [_course("z", "Material Z"), _course("a", "Material A")]},
        domain_catalog_path("materials", tmp_path),
    )
    assert [row["course_id"] for row in course_roster(tmp_path)["courses"]] == ["a", "z"]
    write_domain_catalog("notices", {"courses": [_course("z", "Notice Z")]}, domain_catalog_path("notices", tmp_path))
    write_catalog({"courses": [_course("z", "Lecture Z")]}, catalog_path(tmp_path))
    roster = course_roster(tmp_path)
    assert roster["courses"] == [_course("a", "Material A"), _course("z", "Lecture Z")]
    assert roster["sources"]["assignments"] is None
    assert all(roster["sources"][key] for key in ("lectures", "notices", "materials"))


def test_course_cache_uses_oldest_source_and_retains_unknown_health(tmp_path: Path) -> None:
    write_catalog(
        {"generated_at": (NOW - timedelta(hours=7)).isoformat(), "courses": [_course("id-a", "A")]},
        catalog_path(tmp_path),
    )
    write_domain_catalog(
        "notices",
        {"generated_at": NOW.isoformat(), "courses": [_course("id-b", "B")]},
        domain_catalog_path("notices", tmp_path),
    )
    cache = course_roster(tmp_path, now=NOW)["cache"]
    assert cache["age_seconds"] == 25200
    assert cache["stale"] is True
    assert cache["sources"]["notices"]["age_seconds"] == 0
    assert cache["sources"]["lectures"]["enrollment_state"] == "unknown"


def test_corrupt_present_source_errors_instead_of_skipping(tmp_path: Path) -> None:
    write_catalog({"courses": []}, catalog_path(tmp_path))
    path = domain_catalog_path("assignments", tmp_path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(CampusError) as failure:
        course_roster(tmp_path)
    assert failure.value.code == "catalog-invalid"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(CampusError) as failure:
        course_roster(tmp_path)
    assert failure.value.code == "catalog-schema-unsupported"


def test_age_boundary_skew_and_health_are_distinct() -> None:
    timestamp = (NOW - timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    baseline = {"generated_at": timestamp, "enrollment_state": "known", "failed_courses": []}
    assert cache_metadata(baseline, now=NOW, domain="materials")["stale"] is False
    assert cache_metadata(baseline, now=NOW, domain="materials")["age_seconds"] == 21600
    half_second = cache_metadata(
        {**baseline, "generated_at": (NOW - timedelta(seconds=21600, milliseconds=500)).isoformat()},
        now=NOW,
    )
    assert (half_second["age_seconds"], half_second["stale"]) == (21600, True)
    stale = cache_metadata({**baseline, "generated_at": (NOW - timedelta(seconds=21601)).isoformat()}, now=NOW)
    assert stale["stale"] is True
    future = cache_metadata({**baseline, "generated_at": (NOW + timedelta(seconds=2)).isoformat()}, now=NOW)
    assert (future["age_seconds"], future["clock_skew"], future["stale"]) == (0, True, False)
    assert cache_metadata({**baseline, "enrollment_state": "unknown"}, now=NOW)["stale"] is True
    assert (
        cache_metadata(
            {**baseline, "failed_courses": [{"course_id": "synthetic", "reason": "course-sync-failed"}]}, now=NOW
        )["stale"]
        is True
    )
    with pytest.raises(CampusError) as failure:
        cache_metadata({**baseline, "generated_at": "yesterday"}, now=NOW)
    assert failure.value.code == "catalog-invalid"


def test_legacy_digest_and_replacement_even_when_bytes_identical(tmp_path: Path) -> None:
    path = catalog_path(tmp_path)
    path.parent.mkdir(parents=True)
    legacy = {
        "schema_version": 1,
        "generated_at": "2026-01-01T12:00:00Z",
        "courses": [_course("id-a", "A")],
        "lectures": [],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    first = course_roster(tmp_path)
    assert first["sources"]["lectures"]["generation_id"] is None
    path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    assert course_roster(tmp_path)["roster_generation"] != first["roster_generation"]
    write_catalog(legacy, path)
    initial_write = course_roster(tmp_path)
    write_catalog(legacy, path)
    second_write = course_roster(tmp_path)
    assert second_write["sources"]["lectures"]["generation_id"] != initial_write["sources"]["lectures"]["generation_id"]
    assert second_write["roster_generation"] != initial_write["roster_generation"]
    raw = path.read_bytes()
    assert catalog_generation(json.loads(raw), raw)["sha256"] == second_write["sources"]["lectures"]["sha256"]


def test_failed_publication_preserves_last_success_and_private_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_catalog({"courses": [_course("id-a", "A")]}, catalog_path(tmp_path))
    roster = course_roster(tmp_path)
    saved = publish_course_snapshot(tmp_path, roster)
    target = course_snapshot_path(tmp_path)
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
        assert target.parent.stat().st_mode & 0o777 == 0o700
    original = target.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("synthetic failure")

    monkeypatch.setattr("campusctl.catalog_view.os.replace", fail_replace)
    with pytest.raises(CampusError) as failure:
        publish_course_snapshot(tmp_path, roster)
    assert (failure.value.code, failure.value.status) == ("selection-write-failed", "error")
    assert target.read_bytes() == original
    assert read_course_snapshot(tmp_path) == saved
    assert list(target.parent.glob(".courses-last-list.json.*.tmp")) == []
    assert_course_snapshot_current(tmp_path, saved)


def test_material_numbers_bind_filtered_order_and_exact_write(tmp_path: Path) -> None:
    path = domain_catalog_path("materials", tmp_path)
    write_domain_catalog("materials", {"generated_at": "2026-01-01T00:00:00Z", "courses": [_course("id-a", "A")]}, path)
    generation = catalog_snapshot("materials", tmp_path)[1]
    published = publish_material_snapshot(tmp_path, generation, ["material:c", "material:a"], course_id="id-a")
    assert published["course_id"] == "id-a"
    assert resolve_material_number(tmp_path, "1")[0] == "material:c"
    assert resolve_material_number(tmp_path, "2")[0] == "material:a"
    for number in ("0", "3", "bad"):
        with pytest.raises(CampusError) as failure:
            resolve_material_number(tmp_path, number)
        assert failure.value.code == "selection-invalid"
    write_domain_catalog("materials", {"generated_at": "2026-01-01T00:00:00Z", "courses": [_course("id-a", "A")]}, path)
    with pytest.raises(CampusError) as failure:
        assert_material_snapshot_current(tmp_path, published)
    assert failure.value.code == "selection-stale"
    with pytest.raises(CampusError) as failure:
        resolve_material_number(tmp_path, "1")
    assert failure.value.code == "selection-stale"


def test_failed_post_replace_sync_preserves_last_material_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = domain_catalog_path("materials", tmp_path)
    write_domain_catalog("materials", {"courses": [_course("id-a", "A")]}, path)
    generation = catalog_snapshot("materials", tmp_path)[1]
    publish_material_snapshot(tmp_path, generation, ["material:old", "material:new"])

    previous = material_snapshot_path(tmp_path).read_bytes()
    calls = 0

    def fail_once(_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic post-replace sync failure")

    monkeypatch.setattr("campusctl.catalog_view._fsync_directory", fail_once)
    with pytest.raises(CampusError) as failure:
        publish_material_snapshot(tmp_path, generation, ["material:new", "material:old"])
    assert (failure.value.code, failure.value.status) == ("selection-write-failed", "error")
    assert material_snapshot_path(tmp_path).read_bytes() == previous
    assert resolve_material_number(tmp_path, "1")[0] == "material:old"
    assert calls == 2
    assert list(material_snapshot_path(tmp_path).parent.glob("*.bak")) == []


def test_failed_writer_does_not_undo_newer_successful_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = domain_catalog_path("materials", tmp_path)
    write_domain_catalog("materials", {"courses": [_course("id-a", "A")]}, path)
    generation = catalog_snapshot("materials", tmp_path)[1]
    publish_material_snapshot(tmp_path, generation, ["material:a"])
    calls = 0

    def newer_writer_then_fail(_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            publish_material_snapshot(tmp_path, generation, ["material:c"])
            raise OSError("synthetic older writer failure")

    monkeypatch.setattr("campusctl.catalog_view._fsync_directory", newer_writer_then_fail)
    with pytest.raises(CampusError) as failure:
        publish_material_snapshot(tmp_path, generation, ["material:b"])
    assert failure.value.code == "selection-write-failed"
    assert resolve_material_number(tmp_path, "1")[0] == "material:c"
    assert calls == 2
    assert list(material_snapshot_path(tmp_path).parent.glob("*.bak")) == []
