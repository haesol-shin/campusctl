from __future__ import annotations

import hashlib
import json
import re
import struct
import unicodedata
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from campusctl.identity import assignment_entity_id, material_entity_id, notice_entity_id

FIXTURE = Path(__file__).parent / "fixtures" / "lms_sources" / "identity.json"
PACKAGE_SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "lms_sources" / "package_schema.json"
SAFE_NAMES_FIXTURE = Path(__file__).parent / "fixtures" / "lms_sources" / "safe_names.json"

_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_INVALID_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def _canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _file_resource_id(course_id: str, provider_file_id: str) -> str:
    payload = _canonical_json(["file", course_id, provider_file_id])
    return hashlib.sha256(payload).hexdigest()


def _inline_resource_id(entity_id: str, kind: str, reference_order: int) -> str:
    payload = _canonical_json(["inline", entity_id, kind, reference_order])
    return hashlib.sha256(payload).hexdigest()


def _compute_package_sha256(
    manifest: dict[str, Any],
    content_md: str,
    resources: dict[str, str],
    *,
    manifest_digest_excludes: list[str],
) -> tuple[str, str]:
    filtered_manifest = {k: v for k, v in manifest.items() if k not in manifest_digest_excludes}
    manifest_bytes = _canonical_json(filtered_manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    records: list[tuple[str, bytes]] = [
        ("package.json", manifest_bytes),
        ("content.md", content_md.encode("utf-8")),
    ]
    for path in sorted(resources.keys()):
        records.append((path, resources[path].encode("utf-8")))

    hasher = hashlib.sha256()
    for name, content in records:
        name_bytes = name.encode("utf-8")
        hasher.update(struct.pack(">Q", len(name_bytes)))
        hasher.update(name_bytes)
        hasher.update(struct.pack(">Q", len(content)))
        hasher.update(content)

    return manifest_sha256, hasher.hexdigest()


def _safe_component(name: str, *, suffix: str = "", max_bytes: int = 200) -> str:
    normalized = unicodedata.normalize("NFC", name)
    basename = normalized.replace("\\", "/").split("/")[-1]
    cleaned = _INVALID_FILENAME_CHARS.sub("_", basename).rstrip(". ")
    stem_candidate = cleaned.split(".")[0].upper()
    if not cleaned or cleaned in {".", ".."} or stem_candidate in _WINDOWS_DEVICE_NAMES:
        cleaned = "resource"

    if "." in cleaned and not cleaned.startswith(".") and not cleaned.endswith("."):
        stem, ext = cleaned.rsplit(".", 1)
        ext = f".{ext}"
    else:
        stem, ext = cleaned, ""

    suffix_part = f"~{suffix}" if suffix else ""
    target_stem_bytes = max_bytes - len(suffix_part.encode("utf-8")) - len(ext.encode("utf-8"))
    while len(stem.encode("utf-8")) > target_stem_bytes and len(stem) > 1:
        stem = stem[:-1]

    return f"{stem}{suffix_part}{ext}"


def test_fixture_identity_vectors() -> None:
    vectors = json.loads(FIXTURE.read_text(encoding="utf-8"))["vectors"]
    assignment = vectors["assignment"]
    notice = vectors["notice"]
    material = vectors["material"]

    assert assignment_entity_id(assignment["course_id"], assignment["task_id"]) == assignment["expected_entity_id"]
    assert (
        notice_entity_id(notice["course_id"], notice["displayed_date_time"], notice["number"])
        == notice["expected_entity_id"]
    )
    assert material_entity_id(material["course_id"], material["file_id"]) == material["expected_entity_id"]
    assert (
        f"{notice['course_label']}_{notice['displayed_date_time']}_{notice['number']}" == notice["expected_legacy_key"]
    )


def test_identity_ids_preserve_native_components_and_course_scope() -> None:
    assert notice_entity_id("course-a", "2026-01-02  03:04", "07") == "cnu_notice:course-a:2026-01-02  03%3A04:07"
    assert assignment_entity_id("course-a", "task-1") != assignment_entity_id("course-b", "task-1")
    assert material_entity_id("course-a", "file-1") != material_entity_id("course-b", "file-1")


def test_encoded_identity_components_are_unambiguous() -> None:
    assert assignment_entity_id("A:B", "C") == "cnu_assignment:A%3AB:C"
    assert assignment_entity_id("A:B", "C") != assignment_entity_id("A", "B:C")
    assert assignment_entity_id("A%3AB", "C") == "cnu_assignment:A%253AB:C"
    assert material_entity_id("A:B", "C") != material_entity_id("A", "B:C")
    assert notice_entity_id("A:B", "C", "D") != notice_entity_id("A", "B:C", "D")


def test_identity_builder_is_unique_over_separator_and_escape_inputs() -> None:
    components = ("A", "A:B", "B:C", "%", "%3A", "é")
    identities = {assignment_entity_id(course_id, task_id) for course_id, task_id in product(components, repeat=2)}
    assert len(identities) == len(components) ** 2


@pytest.mark.parametrize(
    ("builder", "arguments"),
    [
        (assignment_entity_id, ("", "task-1")),
        (assignment_entity_id, ("course-a", "")),
        (notice_entity_id, ("course-a", "", "01")),
        (notice_entity_id, ("course-a", "2026-01-02 03:04", " ")),
        (material_entity_id, ("course-a", "")),
    ],
)
def test_identity_builders_reject_missing_components(builder, arguments) -> None:
    with pytest.raises(ValueError):
        builder(*arguments)


def test_design_fixture_resource_ids() -> None:
    package_data = json.loads(PACKAGE_SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    manifest = package_data["manifest"]
    digest_inputs = package_data["digest_inputs"]
    canonicalization = package_data["canonicalization"]
    expected = package_data["expected"]

    # 1. Deterministic resource ID validation
    file_resource = manifest["resources"][0]
    computed_file_id = _file_resource_id(manifest["course"]["id"], file_resource["provider_file_id"])
    assert computed_file_id == expected["included_file_resource_id"]
    assert file_resource["resource_id"] == expected["included_file_resource_id"]

    omitted_resource = manifest["omitted_resources"][0]
    computed_omitted_id = _inline_resource_id(manifest["entity_id"], "image", 2)
    assert computed_omitted_id == expected["omitted_inline_resource_id"]
    assert omitted_resource["resource_id"] == expected["omitted_inline_resource_id"]

    # 2. Included resource content hash and length validation
    included_bytes = digest_inputs["resources_utf8"][file_resource["path"]].encode("utf-8")
    assert hashlib.sha256(included_bytes).hexdigest() == file_resource["sha256"]
    assert len(included_bytes) == file_resource["size_bytes"]

    # 3. Deterministic manifest and package digest validation
    manifest_digest, package_digest = _compute_package_sha256(
        manifest,
        digest_inputs["content_md_utf8"],
        digest_inputs["resources_utf8"],
        manifest_digest_excludes=canonicalization["manifest_digest_excludes"],
    )
    assert manifest_digest == expected["manifest_sha256_without_retrieved_at"]
    assert package_digest == expected["package_sha256"]

    # 4. Safe-name normalization cases validation
    safe_names_data = json.loads(SAFE_NAMES_FIXTURE.read_text(encoding="utf-8"))
    for case in safe_names_data["normalization_cases"]:
        assert _safe_component(case["input_name"]) == case["expected_name"]

    # 5. Casefold collision resolution validation
    collision_data = safe_names_data["casefold_collision"]
    seen_casefold_keys: set[str] = set()
    resolved_names: list[str] = []
    for input_name, resource_id in zip(collision_data["input_names"], collision_data["resource_ids"], strict=True):
        base_name = _safe_component(input_name)
        collision_key = unicodedata.normalize("NFC", base_name).casefold()
        if collision_key in seen_casefold_keys:
            resolved = _safe_component(input_name, suffix=resource_id)
        else:
            resolved = base_name
            seen_casefold_keys.add(collision_key)
        resolved_names.append(resolved)
    assert resolved_names == collision_data["expected_names"]

    # 6. Suffix boundary and UTF-8 component byte limit validation
    boundary_data = safe_names_data["suffix_boundary"]
    long_stem = boundary_data["input_stem_character"] * boundary_data["input_stem_repetitions"]
    boundary_name = _safe_component(
        f"{long_stem}{boundary_data['extension']}",
        suffix=boundary_data["suffix_resource_id"],
        max_bytes=boundary_data["max_component_bytes"],
    )
    assert len(boundary_name.encode("utf-8")) == boundary_data["expected_utf8_bytes"]
    assert boundary_name.startswith(boundary_data["input_stem_character"] * boundary_data["expected_stem_repetitions"])
