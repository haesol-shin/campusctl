from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lms_sources"


def _load(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _keys(value: dict[str, Any], expected: set[str]) -> None:
    assert set(value) == expected


def test_lms_source_fixture_shapes_are_frozen() -> None:
    identity = _load("identity.json")
    _keys(identity, {"schema_version", "vectors"})
    assert identity["schema_version"] == 1
    vectors = identity["vectors"]
    _keys(vectors, {"assignment", "notice", "material"})
    _keys(vectors["assignment"], {"course_id", "task_id", "expected_entity_id"})
    _keys(
        vectors["notice"],
        {"course_id", "course_label", "displayed_date_time", "number", "expected_entity_id", "expected_legacy_key"},
    )
    _keys(vectors["material"], {"course_id", "file_id", "expected_entity_id"})

    package = _load("package_schema.json")
    _keys(package, {"schema_version", "manifest", "digest_inputs", "canonicalization", "expected"})
    assert package["schema_version"] == 1
    manifest = package["manifest"]
    _keys(
        manifest,
        {
            "schema_version",
            "entity_id",
            "kind",
            "course",
            "source_ref",
            "retrieved_at",
            "content_path",
            "completeness",
            "resources",
            "omitted_resources",
        },
    )
    _keys(manifest["course"], {"id", "label"})
    _keys(manifest["source_ref"], {"origin", "page_path", "provider_native_id"})
    assert isinstance(manifest["resources"], list) and manifest["resources"]
    for resource in manifest["resources"]:
        _keys(
            resource,
            {
                "resource_id",
                "kind",
                "path",
                "source_ref",
                "original_name",
                "media_type",
                "size_bytes",
                "provider_file_id",
                "sha256",
            },
        )
        _keys(resource["source_ref"], {"origin", "page_path", "provider_native_id"})
    assert isinstance(manifest["omitted_resources"], list) and manifest["omitted_resources"]
    for resource in manifest["omitted_resources"]:
        _keys(resource, {"resource_id", "source_ref", "original_name", "media_type", "reason"})
        _keys(resource["source_ref"], {"origin", "page_path", "provider_native_id"})
    _keys(package["digest_inputs"], {"content_md_utf8", "resources_utf8"})
    _keys(
        package["canonicalization"],
        {
            "encoding",
            "sort_keys",
            "separators",
            "ensure_ascii",
            "manifest_digest_excludes",
            "record_encoding",
            "record_order",
        },
    )
    _keys(
        package["expected"],
        {
            "included_file_resource_id",
            "omitted_inline_resource_id",
            "manifest_sha256_without_retrieved_at",
            "package_sha256",
        },
    )

    names = _load("safe_names.json")
    _keys(names, {"schema_version", "normalization_cases", "casefold_collision", "suffix_boundary"})
    assert names["schema_version"] == 1
    assert isinstance(names["normalization_cases"], list) and names["normalization_cases"]
    for case in names["normalization_cases"]:
        _keys(case, {"case", "input_name", "expected_name"})
    _keys(names["casefold_collision"], {"input_names", "resource_ids", "expected_names"})
    _keys(
        names["suffix_boundary"],
        {
            "input_stem_character",
            "input_stem_repetitions",
            "suffix_resource_id",
            "extension",
            "max_component_bytes",
            "expected_stem_repetitions",
            "expected_utf8_bytes",
        },
    )

    policy = _load("policy.json")
    _keys(policy, {"approved", "read_only_evidence", "origins", "routes", "allowed_media", "max_bytes", "notes"})
    assert policy == {
        "approved": False,
        "read_only_evidence": None,
        "origins": [],
        "routes": [],
        "allowed_media": [],
        "max_bytes": None,
        "notes": "No logging, media, stream or range permission",
    }

    navigation = _load("course_navigation.json")
    _keys(
        navigation,
        {
            "schema_version",
            "course_id",
            "section",
            "selectors",
            "expected_events",
            "response_timing_ms",
            "session_request",
            "session_response",
            "topbar",
        },
    )
    assert navigation["schema_version"] == 1
    _keys(navigation["selectors"], {"course_row", "section_link"})
    _keys(navigation["response_timing_ms"], {"course_row_to_menu", "section_click_to_list"})
    assert sum(navigation["response_timing_ms"].values()) > 7000
    _keys(navigation["session_request"], {"e"})
    _keys(navigation["session_response"], {"header", "body"})
    _keys(navigation["session_response"]["body"], {"result", "data"})
    assert navigation["session_response"]["body"]["data"]["course_id"] == navigation["course_id"]
    _keys(navigation["topbar"], {"current_label", "selected_link"})
    _keys(navigation["topbar"]["selected_link"], {"selector", "data-courseid", "data-coursenm"})

    headers = _load("request_headers.json")
    _keys(headers, {"schema_version", "synthetic_only", "route", "request", "cases"})
    assert headers["schema_version"] == 1
    assert headers["synthetic_only"] is True
    _keys(headers["route"], {"origin", "path", "operation", "methods"})
    _keys(headers["request"], {"url", "method", "resource_type"})
    assert headers["request"]["method"] == "GET"
    assert headers["route"]["methods"] == ["GET"]
    assert headers["cases"] == [
        {"name": "ordinary_get", "headers": {}, "expected_decision": "allow"},
        {
            "name": "mixed_case_range",
            "headers": {"rAnGe": "bytes=0-1023"},
            "expected_decision": "block",
        },
    ]
