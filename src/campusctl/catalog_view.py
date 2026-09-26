"""Immutable local catalog views and printed-selection generations."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog import _validate_catalog as _validate_lectures
from campusctl.catalog import catalog_path
from campusctl.domain_catalog import _validate_catalog as _validate_domain
from campusctl.domain_catalog import domain_catalog_path
from campusctl.envelope import CampusError
from campusctl.paths import ensure_private_dir

DOMAINS = ("lectures", "assignments", "notices", "materials")
STALE_AFTER_SECONDS = 21600


def catalog_snapshot(domain: str, root: Path) -> tuple[dict[str, Any], dict[str, Any]] | None:
    path = catalog_path(root) if domain == "lectures" else domain_catalog_path(domain, root)
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise CampusError(
            "catalog-invalid", f"The {domain} catalog could not be read.", "Run 'campusctl sync' again."
        ) from None
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError):
        raise CampusError(
            "catalog-invalid", f"The {domain} catalog is not valid JSON.", "Run 'campusctl sync' again."
        ) from None
    validated = _validate_lectures(value, path) if domain == "lectures" else _validate_domain(domain, value, path)
    return validated, catalog_generation(validated, content)


def catalog_generation(catalog: dict[str, Any], content: bytes) -> dict[str, str | None]:
    """Identify the exact bytes of one catalog, including legacy writes without IDs."""
    return {
        "generation_id": catalog.get("generation_id"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "generated_at": catalog["generated_at"],
    }


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def course_roster(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Build a generation-bound, sorted union from exactly one read of each source."""
    sources: dict[str, Any] = {}
    source_cache: dict[str, Any] = {}
    courses: dict[str, dict[str, Any]] = {}
    reference_time = now or datetime.now(UTC)
    for domain in DOMAINS:
        snapshot = catalog_snapshot(domain, root)
        sources[domain] = None if snapshot is None else snapshot[1]
        source_cache[domain] = (
            None if snapshot is None else cache_metadata(snapshot[0], now=reference_time, domain=domain)
        )
        if snapshot is None:
            continue
        for course in snapshot[0]["courses"]:
            if not isinstance(course, dict) or not isinstance(course.get("course_id"), str) or not course["course_id"]:
                raise CampusError(
                    "catalog-invalid",
                    f"The {domain} catalog contains a course without an ID.",
                    "Run 'campusctl sync' again.",
                )
            identifier = course["course_id"]
            if identifier not in courses:
                label = course.get("label")
                courses[identifier] = {
                    "course_id": identifier,
                    "label": label if isinstance(label, str) else identifier,
                    "class_no": course.get("class_no"),
                }
    if all(source is None for source in sources.values()):
        raise CampusError(
            "catalog-missing", "No local course catalog is available.", "Run 'campusctl sync' to create one."
        )
    ordered = [courses[key] for key in sorted(courses)]
    available = [entry for entry in source_cache.values() if entry is not None]
    oldest = max(
        available, key=lambda entry: entry["age_seconds"] if entry["age_seconds"] is not None else float("inf")
    )
    cache = {
        "sources": source_cache,
        "generated_at": oldest["generated_at"],
        "age_seconds": oldest["age_seconds"],
        "stale": any(entry["stale"] for entry in available),
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "clock_skew": any(entry["clock_skew"] for entry in available),
        "refresh_command": "campusctl courses list --refresh",
    }
    return {"courses": ordered, "sources": sources, "roster_generation": _digest([ordered, sources]), "cache": cache}


def cache_metadata(catalog: dict[str, Any], *, now: datetime, domain: str | None = None) -> dict[str, Any]:
    """Report advisory age, skew, and incomplete coverage independently."""
    timestamp = catalog.get("generated_at")
    if timestamp is None:
        age = None
        elapsed = None
        skew = False
    else:
        try:
            generated = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if generated.tzinfo is None:
                raise ValueError("timezone missing")
            elapsed = (now.astimezone(UTC) - generated.astimezone(UTC)).total_seconds()
        except (AttributeError, TypeError, ValueError):
            raise CampusError(
                "catalog-invalid", "Catalog generation timestamp is invalid.", "Run 'campusctl sync' again."
            ) from None
        age = max(0, int(elapsed // 1))
        skew = elapsed < 0
    unknown = catalog.get("enrollment_state", "unknown") != "known"
    failed = bool(catalog.get("failed_courses"))
    return {
        "generated_at": timestamp,
        "age_seconds": age,
        "stale": elapsed is None or elapsed > STALE_AFTER_SECONDS or unknown or failed,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "clock_skew": skew,
        "enrollment_state": catalog.get("enrollment_state", "unknown"),
        "failed_courses": catalog.get("failed_courses", []),
        "refresh_command": f"campusctl {domain} list --refresh" if domain else "campusctl sync",
    }


def _fsync_directory(path: Path) -> None:
    if hasattr(os, "O_DIRECTORY"):
        directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _write_selection(target: Path, payload: dict[str, Any], kind: str) -> None:
    backup: Path | None = None
    replaced = False
    try:
        ensure_private_dir(target.parent)
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as destination:
                json.dump(payload, destination, ensure_ascii=False, separators=(",", ":"))
                destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            if target.exists():
                backup_fd, backup_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".bak", dir=target.parent)
                backup = Path(backup_name)
                with target.open("rb") as source, os.fdopen(backup_fd, "wb") as old:
                    shutil.copyfileobj(source, old)
                    old.flush()
                    os.fsync(old.fileno())
            os.replace(temp, target)
            replaced = True
            _fsync_directory(target.parent)
        except BaseException:
            if replaced:
                if backup is not None:
                    os.replace(backup, target)
                    backup = None
                else:
                    target.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    _fsync_directory(target.parent)
            with contextlib.suppress(OSError):
                temp.unlink(missing_ok=True)
            raise
        finally:
            if backup is not None:
                with contextlib.suppress(OSError):
                    backup.unlink(missing_ok=True)
    except OSError:
        raise CampusError(
            "selection-write-failed",
            f"The printed {kind} selection could not be saved.",
            f"Run 'campusctl {kind} list' again.",
            "error",
        ) from None


def course_snapshot_path(root: Path) -> Path:
    return root / "selection" / "courses-last-list.json"


def publish_course_snapshot(
    root: Path, roster: dict[str, Any], *, printed_at: datetime | None = None
) -> dict[str, Any]:
    """Call only after successful human output flush; a failed write leaves the old snapshot."""
    current = course_roster(root)
    if current["roster_generation"] != roster["roster_generation"]:
        raise _selection_stale()
    payload = {
        "schema_version": 1,
        "roster_generation": roster["roster_generation"],
        "sources": roster["sources"],
        "courses": roster["courses"],
        "printed_at": (printed_at or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    _write_selection(course_snapshot_path(root), payload, "courses")
    return payload


def read_course_snapshot(root: Path) -> dict[str, Any]:
    try:
        value = json.loads(course_snapshot_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CampusError(
            "selection-missing", "No printed course list is available.", "Run 'campusctl courses list' again."
        ) from None
    except (OSError, ValueError, UnicodeError):
        raise CampusError(
            "selection-invalid", "The printed course list cannot be read.", "Run 'campusctl courses list' again."
        ) from None
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or not isinstance(value.get("roster_generation"), str)
        or not isinstance(value.get("courses"), list)
        or not isinstance(value.get("sources"), dict)
    ):
        raise CampusError(
            "selection-invalid", "The printed course list is malformed.", "Run 'campusctl courses list' again."
        )
    if (
        set(value["sources"]) != set(DOMAINS)
        or _digest([value["courses"], value["sources"]]) != value["roster_generation"]
    ):
        raise CampusError(
            "selection-invalid", "The printed course list is malformed.", "Run 'campusctl courses list' again."
        )
    return value


def _selection_stale() -> CampusError:
    return CampusError(
        "selection-stale", "The printed course list is no longer current.", "Run 'campusctl courses list' again."
    )


def assert_course_snapshot_current(root: Path, snapshot: dict[str, Any]) -> None:
    """Recheck under the browser session lock immediately before network entry."""
    try:
        current = course_roster(root)
    except CampusError as error:
        if error.code == "catalog-missing":
            raise _selection_stale() from None
        raise
    if snapshot["roster_generation"] != current["roster_generation"]:
        raise _selection_stale()


def material_snapshot_path(root: Path) -> Path:
    return root / "selection" / "materials-last-list.json"


def publish_material_snapshot(
    root: Path,
    generation: dict[str, Any],
    entity_ids: list[str],
    *,
    course_id: str | None = None,
    printed_at: datetime | None = None,
) -> dict[str, Any]:
    """Publish the exact successfully printed filtered order, never a fresh re-sort."""
    current = catalog_snapshot("materials", root)
    if current is None or current[1] != generation:
        raise _material_stale()
    payload = {
        "schema_version": 1,
        "catalog_generation": generation,
        "course_id": course_id,
        "entity_ids": entity_ids,
        "printed_at": (printed_at or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    _write_selection(material_snapshot_path(root), payload, "materials")
    return payload


def read_material_snapshot(root: Path) -> dict[str, Any]:
    try:
        value = json.loads(material_snapshot_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CampusError(
            "selection-missing", "No printed materials list is available.", "Run 'campusctl materials list' again."
        ) from None
    except (OSError, ValueError, UnicodeError):
        raise CampusError(
            "selection-invalid", "The printed materials list cannot be read.", "Run 'campusctl materials list' again."
        ) from None
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or not isinstance(value.get("catalog_generation"), dict)
        or not isinstance(value.get("entity_ids"), list)
        or not all(isinstance(item, str) for item in value["entity_ids"])
    ):
        raise CampusError(
            "selection-invalid", "The printed materials list is malformed.", "Run 'campusctl materials list' again."
        )
    return value


def _material_stale() -> CampusError:
    return CampusError(
        "selection-stale", "The printed materials list is no longer current.", "Run 'campusctl materials list' again."
    )


def assert_material_snapshot_current(root: Path, snapshot: dict[str, Any]) -> None:
    """Recheck under the browser session lock before selected-file transfer."""
    current = catalog_snapshot("materials", root)
    if current is None or current[1] != snapshot["catalog_generation"]:
        raise _material_stale()


def resolve_material_number(root: Path, number: str) -> tuple[str, dict[str, Any]]:
    """Return one printed full ID and its generation precondition."""
    snapshot = read_material_snapshot(root)
    assert_material_snapshot_current(root, snapshot)
    try:
        if not number.isdecimal() or int(number) < 1 or int(number) > len(snapshot["entity_ids"]):
            raise ValueError("invalid index")
    except (AttributeError, ValueError):
        raise CampusError(
            "selection-invalid", "The printed material number is out of range.", "Run 'campusctl materials list' again."
        ) from None
    return snapshot["entity_ids"][int(number) - 1], snapshot
