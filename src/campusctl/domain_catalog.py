from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from campusctl.catalog import SCHEMA_VERSION
from campusctl.envelope import CampusError
from campusctl.paths import data_dir, ensure_private_dir

Domain = Literal["assignments", "notices", "materials"]
_DOMAINS = frozenset({"assignments", "notices", "materials"})
_FAILURE_REASONS = frozenset(
    {
        "course-sync-failed",
        "item-identity-missing",
        "notice-identity-ambiguous",
        "notice-board-paginated",
        "removal-deferred",
    }
)


def _domain_key(domain: Domain) -> str:
    if not isinstance(domain, str) or domain not in _DOMAINS:
        raise ValueError(f"Unsupported domain catalog: {domain}")
    return domain


def domain_catalog_path(domain: Domain, root: Path | None = None) -> Path:
    key = _domain_key(domain)
    return (root or data_dir()) / "catalog" / f"{key}.json"


def _remediation(domain: str) -> str:
    return f"Run 'campusctl sync --only {domain}' to create it."


def _invalid(domain: str, path: Path, detail: str) -> CampusError:
    return CampusError(
        "catalog-invalid",
        f"The {domain} catalog at {path} {detail}.",
        _remediation(domain),
        "user-action",
    )


def _validate_catalog(domain: str, value: Any, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(domain, path, "does not contain an object")
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise CampusError(
            "catalog-schema-unsupported",
            f"The {domain} catalog at {path} uses an unsupported schema version.",
            f"Run 'campusctl sync --only {domain}' to rebuild it.",
            "user-action",
        )
    if not isinstance(value.get("generated_at"), str):
        raise _invalid(domain, path, "is missing its generated timestamp")
    state = value.get("enrollment_state")
    if not isinstance(state, str) or state not in {"known", "unknown"}:
        raise _invalid(domain, path, "has an invalid enrollment state")
    for key in ("courses", "failed_courses", domain):
        if not isinstance(value.get(key), list):
            raise _invalid(domain, path, f"is missing its {key} records")
    if "generation_id" in value and not isinstance(value["generation_id"], str):
        raise _invalid(domain, path, "has an invalid generation ID")
    return value


def read_domain_catalog(domain: Domain, path: Path | None = None) -> dict[str, Any]:
    key = _domain_key(domain)
    target = path or domain_catalog_path(key)
    try:
        with target.open(encoding="utf-8") as source:
            value = json.load(source)
    except FileNotFoundError:
        raise CampusError(
            "catalog-missing",
            f"{key.capitalize()} catalog not found: {target}.",
            _remediation(key),
            "user-action",
        ) from None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise _invalid(key, target, "could not be read as valid JSON") from None
    return _validate_catalog(key, value, target)


def _generated_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_domain_catalog(domain: Domain, value: dict[str, Any], path: Path | None = None) -> Path:
    key = _domain_key(domain)
    target = path or domain_catalog_path(key)
    if not isinstance(value, dict):
        raise CampusError(
            "catalog-schema-unsupported",
            f"Cannot write a {key} catalog with an unsupported schema version.",
            f"Use schema version {SCHEMA_VERSION}.",
            "user-action",
        )
    version = value.get("schema_version", SCHEMA_VERSION)
    if type(version) is not int or version != SCHEMA_VERSION:
        raise CampusError(
            "catalog-schema-unsupported",
            f"Cannot write a {key} catalog with an unsupported schema version.",
            f"Use schema version {SCHEMA_VERSION}.",
            "user-action",
        )
    payload = dict(value)
    payload["schema_version"] = SCHEMA_VERSION
    payload.setdefault("generated_at", _generated_at())
    payload.setdefault("enrollment_state", "known")
    payload.setdefault("courses", [])
    payload.setdefault("failed_courses", [])
    payload.setdefault(key, [])
    payload["generation_id"] = uuid.uuid4().hex
    _validate_catalog(key, payload, target)
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
            f"The {key} catalog could not be written atomically.",
            "Check the data directory and retry the sync.",
            "error",
        ) from None
    return target


def _merge_invalid(message: str) -> CampusError:
    return CampusError("catalog-invalid", f"Cannot merge the domain catalog: {message}.", None, "user-action")


def _course_id(course: Any, *, context: str) -> str:
    if not isinstance(course, dict):
        raise _merge_invalid(f"{context} contains a non-object course")
    course_id = course.get("course_id")
    if not isinstance(course_id, str) or not course_id.strip():
        raise _merge_invalid(f"{context} contains a course without an ID")
    return course_id


def _row_course_id(row: Any, *, context: str) -> str:
    if not isinstance(row, dict) or not isinstance(row.get("course"), dict):
        raise _merge_invalid(f"{context} contains a row without course metadata")
    course_id = row["course"].get("id")
    if not isinstance(course_id, str) or not course_id.strip():
        raise _merge_invalid(f"{context} contains a row without a course ID")
    entity_id = row.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise _merge_invalid(f"{context} contains a row without an entity ID")
    return course_id


def _unique_courses(courses: list[dict[str, Any]], *, context: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for course in courses:
        course_id = _course_id(course, context=context)
        if course_id in result:
            raise _merge_invalid(f"{context} contains duplicate course IDs")
        result[course_id] = course
    return result


def _unique_rows(rows: list[dict[str, Any]], *, context: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for row in rows:
        _row_course_id(row, context=context)
        entity_id = row["entity_id"]
        if entity_id in seen:
            raise _merge_invalid(f"{context} contains duplicate entity IDs")
        seen.add(entity_id)
    return rows


def _failure_map(
    failures: list[dict[str, str]], roster: dict[str, dict[str, Any]], previous_courses: dict[str, dict[str, Any]]
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for failure in failures:
        course_id = _course_id(failure, context="failed_courses")
        reason = failure.get("reason")
        if not isinstance(reason, str) or reason not in _FAILURE_REASONS:
            raise _merge_invalid("failed_courses contains an unsupported failure reason")
        if course_id in result:
            raise _merge_invalid("failed_courses contains duplicate course IDs")
        course = roster.get(course_id) or previous_courses.get(course_id) or failure
        label = course.get("label") if isinstance(course.get("label"), str) else course_id
        result[course_id] = {"course_id": course_id, "label": label, "reason": reason}
    return result


def _catalog_rows(
    previous: dict[str, Any] | None, domain: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if previous is None:
        return [], [], []
    if not isinstance(previous, dict):
        raise _merge_invalid("previous value is not an object")
    courses = previous.get("courses", [])
    failed = previous.get("failed_courses", [])
    rows = previous.get(domain, [])
    if not isinstance(courses, list) or not isinstance(failed, list) or not isinstance(rows, list):
        raise _merge_invalid("previous catalog is missing its record lists")
    _unique_courses(courses, context="previous catalog")
    _unique_rows(rows, context="previous catalog")
    return courses, failed, rows


def _same_timestamp() -> str:
    return _generated_at()


def merge_domain_catalog(
    domain: Domain,
    previous: dict[str, Any] | None,
    roster: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    successful_course_ids: set[str],
    failed_courses: list[dict[str, str]],
    selected_course_id: str | None = None,
) -> dict[str, Any]:
    key = _domain_key(domain)
    old_courses, old_failures, old_rows = _catalog_rows(previous, key)
    old_courses_by_id = _unique_courses(old_courses, context="previous catalog")
    roster_by_id = _unique_courses(roster, context="roster")
    _unique_rows(rows, context="incoming rows")
    failures = _failure_map(failed_courses, roster_by_id, old_courses_by_id)
    _failure_map(old_failures, {}, old_courses_by_id)
    if any(not isinstance(course_id, str) or not course_id.strip() for course_id in successful_course_ids):
        raise _merge_invalid("successful_course_ids contains an empty course ID")

    if selected_course_id is not None:
        if not isinstance(selected_course_id, str) or not selected_course_id.strip():
            raise _merge_invalid("selected_course_id is empty")
        selected_failure = failures.get(selected_course_id)
        selected_is_rostered = selected_course_id in roster_by_id
        selected_succeeded = (
            selected_is_rostered and selected_course_id in successful_course_ids and selected_failure is None
        )
        if selected_is_rostered and not selected_succeeded and selected_failure is None:
            course = roster_by_id[selected_course_id]
            label = course.get("label") if isinstance(course.get("label"), str) else selected_course_id
            selected_failure = {
                "course_id": selected_course_id,
                "label": label,
                "reason": "course-sync-failed",
            }
        selected_failed = selected_failure is not None
        if not selected_succeeded and not selected_failed:
            if previous is not None:
                return previous
            raise CampusError(
                "course-discovery-failed",
                "Course discovery failed; the filtered catalog was not changed.",
                None,
                "error",
            )

        merged_courses = list(old_courses)
        course_record = roster_by_id.get(selected_course_id)
        if course_record is not None:
            if selected_course_id in old_courses_by_id:
                merged_courses = [
                    course_record if _course_id(course, context="previous catalog") == selected_course_id else course
                    for course in old_courses
                ]
            else:
                merged_courses.append(course_record)
        merged_failures = [failure for failure in old_failures if failure.get("course_id") != selected_course_id]
        if selected_failed:
            merged_failures.append(selected_failure)
        if selected_succeeded:
            selected_rows = [row for row in rows if _row_course_id(row, context="incoming rows") == selected_course_id]
            merged_rows: list[dict[str, Any]] = []
            inserted_selected = False
            for row in old_rows:
                if _row_course_id(row, context="previous catalog") == selected_course_id:
                    if not inserted_selected:
                        merged_rows.extend(selected_rows)
                        inserted_selected = True
                else:
                    merged_rows.append(row)
            if not inserted_selected:
                merged_rows.extend(selected_rows)
        else:
            merged_rows = list(old_rows)
        _unique_courses(merged_courses, context="merged catalog")
        _unique_rows(merged_rows, context="merged catalog")
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _same_timestamp(),
            "enrollment_state": previous.get("enrollment_state", "known") if previous else "known",
            "courses": merged_courses,
            "failed_courses": merged_failures,
            key: merged_rows,
        }

    # Full sync: every current roster course is either fully successful or stale.
    effective_failures = dict(failures)
    successful = (set(successful_course_ids) & set(roster_by_id)) - set(effective_failures)
    for course_id in roster_by_id.keys() - successful - effective_failures.keys():
        effective_failures[course_id] = {
            "course_id": course_id,
            "label": roster_by_id[course_id].get("label", course_id),
            "reason": "course-sync-failed",
        }
    partial = bool(effective_failures)
    deferred: dict[str, dict[str, str]] = {}
    if partial:
        for course_id, old_course in old_courses_by_id.items():
            if course_id not in roster_by_id and course_id not in effective_failures:
                label = old_course.get("label") if isinstance(old_course.get("label"), str) else course_id
                deferred[course_id] = {
                    "course_id": course_id,
                    "label": label,
                    "reason": "removal-deferred",
                }

    merged_courses = list(roster)
    merged_courses.extend(
        course
        for course in old_courses
        if _course_id(course, context="previous catalog") not in roster_by_id and partial
    )
    merged_failures = [*effective_failures.values(), *deferred.values()]
    merged_rows = [
        row
        for row in old_rows
        if _row_course_id(row, context="previous catalog") not in successful
        and (
            _row_course_id(row, context="previous catalog") in effective_failures
            or _row_course_id(row, context="previous catalog") in deferred
        )
    ]
    merged_rows.extend(row for row in rows if _row_course_id(row, context="incoming rows") in successful)
    _unique_courses(merged_courses, context="merged catalog")
    _unique_rows(merged_rows, context="merged catalog")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _same_timestamp(),
        "enrollment_state": "known",
        "courses": merged_courses,
        "failed_courses": merged_failures,
        key: merged_rows,
    }


def mark_enrollment_unknown(domain: Domain, root: Path) -> None:
    key = _domain_key(domain)
    try:
        catalog = read_domain_catalog(key, domain_catalog_path(key, root))
    except CampusError as error:
        if error.code == "catalog-missing":
            return None
        raise
    if catalog["enrollment_state"] == "unknown":
        return None
    updated = dict(catalog)
    updated["enrollment_state"] = "unknown"
    write_domain_catalog(key, updated, domain_catalog_path(key, root))
