"""Summarize coursework from local catalog snapshots without opening a session."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from campusctl.catalog_view import DOMAINS, cache_metadata, catalog_snapshot
from campusctl.envelope import CampusError, error_item

SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")


def _date(value: object, *, end_of_day: bool = False) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.combine(
                date.fromisoformat(text), time.max.replace(microsecond=0) if end_of_day else time.min
            )
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=SEOUL) if parsed.tzinfo is None else parsed.astimezone(SEOUL)
    except ValueError:
        return None


def _row_id(row: dict[str, Any], domain: str) -> str:
    identity = row.get("entity_id")
    course = row.get("course")
    if not isinstance(identity, str) or not isinstance(course, dict) or not isinstance(course.get("id"), str):
        raise CampusError(
            "catalog-invalid", f"The {domain} catalog contains an invalid row.", "Run 'campusctl sync' again."
        )
    return identity


def _ordered(rows: list[tuple[datetime | None, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows.sort(key=lambda pair: (pair[0] is None, pair[0] or datetime.max.replace(tzinfo=SEOUL), pair[1]["entity_id"]))
    return [row for _, row in rows]


def _summarize(
    catalogs: dict[str, dict[str, Any]], course_id: str | None, start: datetime, end: datetime
) -> dict[str, Any]:
    assignments: list[tuple[datetime | None, dict[str, Any]]] = []
    notices: list[tuple[datetime | None, dict[str, Any]]] = []
    lectures: list[tuple[datetime | None, dict[str, Any]]] = []
    unknown_due = unknown_read = unknown_open = 0
    for domain in ("assignments", "notices", "lectures"):
        if domain not in catalogs:
            continue
        for row in catalogs[domain][domain]:
            _row_id(row, domain)
            if course_id is not None and row["course"]["id"] != course_id:
                continue
            if domain == "assignments":
                if row.get("is_submitted") is not False:
                    continue
                due = _date(row.get("due_date"), end_of_day=True)
                if due is None:
                    unknown_due += 1
                elif start <= due < end:
                    assignments.append((due, row))
            elif domain == "notices":
                if row.get("is_unread") is True:
                    notices.append((_date(row.get("posted_date")) or _date(row.get("date")), row))
                elif row.get("is_unread") is None:
                    unknown_read += 1
            elif row.get("completion") == "incomplete":
                if row.get("open") is None:
                    unknown_open += 1
                elif row.get("open") is True:
                    candidates = [
                        (at, kind)
                        for kind, at in (
                            ("deadline", _date(row.get("due_date"), end_of_day=True)),
                            ("availability", _date(row.get("available_from"))),
                        )
                        if at is not None and at >= start
                    ]
                    nearest, kind = (
                        min(candidates, key=lambda entry: (entry[0], entry[1] != "deadline"))
                        if candidates
                        else (None, None)
                    )
                    lectures.append(
                        (nearest, {**row, "next_at": nearest.isoformat() if nearest else None, "next_kind": kind})
                    )
    notices.sort(key=lambda pair: (pair[0] is None, -(pair[0].timestamp()) if pair[0] else 0, pair[1]["entity_id"]))
    return {
        "assignments": {"due_soon": _ordered(assignments), "unknown_due_count": unknown_due},
        "notices": {"unread": [row for _, row in notices], "unknown_count": unknown_read},
        "lectures": {"open_incomplete": _ordered(lectures), "unknown_open_count": unknown_open},
    }


def _stale_courses(
    catalogs: dict[str, dict[str, Any]], coverage: dict[str, Any], course_id: str | None
) -> list[dict[str, Any]]:
    courses: dict[str, dict[str, Any]] = {}
    for domain in DOMAINS:
        catalog = catalogs.get(domain)
        if catalog is None:
            continue
        cache = coverage[domain]["cache"]
        reasons: dict[str, set[str]] = {}
        failed_only: list[dict[str, Any]] = []
        known_ids = {course["course_id"] for course in catalog["courses"]}
        for failure in catalog.get("failed_courses", []):
            if not isinstance(failure, dict) or not isinstance(failure.get("course_id"), str):
                continue
            cid = failure["course_id"]
            reason = failure.get("reason")
            reasons.setdefault(cid, set()).add(reason if isinstance(reason, str) else "course-sync-failed")
            if cid not in known_ids:
                failed_only.append(failure)
        for course in [*catalog["courses"], *failed_only]:
            if not isinstance(course, dict) or not isinstance(course.get("course_id"), str):
                raise CampusError(
                    "catalog-invalid",
                    f"The {domain} catalog contains an invalid course.",
                    "Run 'campusctl sync' again.",
                )
            cid = course["course_id"]
            if course_id is not None and course_id != cid:
                continue
            entry = courses.setdefault(
                cid,
                {
                    "course_id": cid,
                    "label": course.get("label") if isinstance(course.get("label"), str) else cid,
                    "domains": [],
                    "reasons": set(),
                },
            )
            current = reasons.get(cid, set()).copy()
            if cache["age_seconds"] is None or cache["age_seconds"] > cache["stale_after_seconds"]:
                current.add("catalog-stale")
            if cache["enrollment_state"] != "known":
                current.add("enrollment-unknown")
            if current:
                entry["domains"].append(domain)
                entry["reasons"].update(current)
    return [{**entry, "reasons": sorted(entry["reasons"])} for _, entry in sorted(courses.items()) if entry["reasons"]]


def build_status(
    root: Path, course_id: str | None, *, now: datetime
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Return (complete local result, envelope status, envelope errors).

    A missing/invalid source limits coverage but never triggers network access.
    """
    instant = now.astimezone(UTC)
    start = instant.astimezone(SEOUL)
    end = start + timedelta(days=7)
    catalogs: dict[str, dict[str, Any]] = {}
    coverage: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for domain in DOMAINS:
        try:
            snapshot = catalog_snapshot(domain, root)
            if snapshot is None:
                raise CampusError(
                    "catalog-missing", f"The {domain} catalog is missing.", f"Run 'campusctl sync --only {domain}'."
                )
            catalog, _generation = snapshot
            cache = cache_metadata(catalog, now=instant, domain=domain)
            # Check row/course structure before accepting this domain as available.
            for row in catalog[domain]:
                _row_id(row, domain)
            for course in catalog["courses"]:
                if not isinstance(course, dict) or not isinstance(course.get("course_id"), str):
                    raise CampusError(
                        "catalog-invalid",
                        f"The {domain} catalog contains an invalid course.",
                        "Run 'campusctl sync' again.",
                    )
            catalogs[domain] = catalog
            coverage[domain] = {"state": "available", "cache": cache}
            known_ids = {course["course_id"] for course in catalog["courses"]}
            if any(
                isinstance(failure, dict)
                and isinstance(failure.get("course_id"), str)
                and failure["course_id"] not in known_ids
                and (course_id is None or course_id == failure["course_id"])
                for failure in catalog.get("failed_courses", [])
            ):
                errors.append(
                    {
                        "domain": domain,
                        **error_item(
                            CampusError(
                                "course-sync-failed",
                                f"The {domain} catalog has a failed course without cached records.",
                                f"Run 'campusctl sync --only {domain}' again.",
                                "error",
                            )
                        ),
                    }
                )
        except CampusError as error:
            coverage[domain] = {"state": "missing" if error.code == "catalog-missing" else "invalid", "cache": None}
            errors.append({"domain": domain, **error_item(error)})
    if not catalogs:
        raise CampusError(
            "catalog-missing", "No readable local catalog is available.", "Run 'campusctl sync' to create one."
        )
    result = {
        "as_of": instant.isoformat().replace("+00:00", "Z"),
        "timezone": "Asia/Seoul",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "end_inclusive": False},
        **_summarize(catalogs, course_id, start, end),
        "coverage": coverage,
        "stale_courses": _stale_courses(catalogs, coverage, course_id),
    }
    return result, "partial" if errors else "ok", errors
