from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.envelope import CampusError
from campusctl.paths import data_dir, ensure_private_dir

SCHEMA_VERSION = 1


def catalog_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "catalog" / "lectures.json"


def _invalid(
    path: Path, message: str, remediation: str = "Run 'campusctl sync --only lectures' to rebuild the catalog."
) -> CampusError:
    return CampusError("catalog-invalid", f"The lecture catalog at {path} {message}.", remediation, "user-action")


def read_catalog(path: Path | None = None) -> dict[str, Any]:
    target = path or catalog_path()
    try:
        with target.open(encoding="utf-8") as source:
            value = json.load(source)
    except FileNotFoundError:
        raise CampusError(
            "catalog-missing",
            f"Lecture catalog not found: {target}.",
            "Run 'campusctl sync --only lectures' to create it.",
            "user-action",
        ) from None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise _invalid(target, "could not be read as valid JSON") from None
    return _validate_catalog(value, target)


def _validate_catalog(value: Any, target: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(target, "does not contain an object")
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise CampusError(
            "catalog-schema-unsupported",
            f"The lecture catalog at {target} uses an unsupported schema version.",
            "Run 'campusctl sync --only lectures' to rebuild the catalog.",
            "user-action",
        )
    if not isinstance(value.get("courses"), list) or not isinstance(value.get("lectures"), list):
        raise _invalid(target, "is missing course or lecture records")
    if not isinstance(value.get("generated_at"), str):
        raise _invalid(target, "is missing its generated timestamp")
    if "generation_id" in value and not isinstance(value["generation_id"], str):
        raise _invalid(target, "has an invalid generation ID")
    return value


def write_catalog(value: dict[str, Any], path: Path | None = None) -> Path:
    target = path or catalog_path()
    if not isinstance(value, dict):
        raise CampusError(
            "catalog-schema-unsupported",
            "Cannot write a lecture catalog with an unsupported schema version.",
            "Use schema version 1.",
            "user-action",
        )
    version = value.get("schema_version", SCHEMA_VERSION)
    if type(version) is not int or version != SCHEMA_VERSION:
        raise CampusError(
            "catalog-schema-unsupported",
            "Cannot write a lecture catalog with an unsupported schema version.",
            "Use schema version 1.",
            "user-action",
        )
    payload = dict(value)
    payload["schema_version"] = SCHEMA_VERSION
    payload.setdefault("generated_at", datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"))
    payload.setdefault("courses", [])
    payload.setdefault("lectures", [])
    payload["generation_id"] = uuid.uuid4().hex
    if not isinstance(payload["courses"], list) or not isinstance(payload["lectures"], list):
        raise CampusError(
            "catalog-invalid",
            "The lecture catalog requires course and lecture lists.",
            "Provide course and lecture arrays, then retry.",
            "user-action",
        )
    try:
        ensure_private_dir(target.parent)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as destination:
                json.dump(payload, destination, ensure_ascii=False, separators=(",", ":"))
                destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temp_path, target)
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)
            raise
    except OSError:
        raise CampusError(
            "catalog-write-failed",
            "The lecture catalog could not be written atomically.",
            "Check the data directory and retry the sync.",
            "error",
        ) from None
    return target


def merge_catalog(
    previous: dict[str, Any] | None,
    courses: list[dict[str, Any]],
    lectures: list[dict[str, Any]],
    *,
    failed_course_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Replace successful course records and retain old data for failed or untouched courses."""
    old = previous or {"schema_version": SCHEMA_VERSION, "generated_at": "", "courses": [], "lectures": []}
    failed = failed_course_ids or set()
    successful_courses = [
        course for course in courses if course.get("course_id") is not None and str(course["course_id"]) not in failed
    ]
    successful_ids = {str(course["course_id"]) for course in successful_courses}
    previous_courses = [
        course for course in old.get("courses", []) if str(course.get("course_id")) not in successful_ids
    ]
    courses_by_id = {str(course.get("course_id")): course for course in [*previous_courses, *successful_courses]}
    previous_lectures = [
        lecture for lecture in old.get("lectures", []) if str(lecture.get("course", {}).get("id")) not in successful_ids
    ]
    successful_lectures = [lecture for lecture in lectures if str(lecture.get("course", {}).get("id")) not in failed]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "courses": list(courses_by_id.values()),
        "lectures": [*previous_lectures, *successful_lectures],
    }
