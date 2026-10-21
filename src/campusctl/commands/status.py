"""The status command."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog import catalog_path
from campusctl.catalog_view import DOMAINS, catalog_snapshot, course_roster, read_course_snapshot
from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, Rendered, command_result
from campusctl.course_selection import resolve_course
from campusctl.domain_catalog import domain_catalog_path
from campusctl.envelope import CampusError
from campusctl.paths import data_dir
from campusctl.text_layout import _text, _truncate

CAPABILITY = {"commands": ["local"]}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    status = subparsers.add_parser("status", help="summarize cached coursework")
    _with_json(status)
    status.add_argument("--course", help="select one course")


def _select_status_course(selector: str | None, root: Path, *, ids_only: bool) -> str | None:
    if selector is None:
        return None
    courses: dict[str, dict[str, Any]] = {}
    for domain in DOMAINS:
        try:
            source = catalog_snapshot(domain, root)
        except CampusError:
            continue
        if source is not None:
            for row in source[0]["courses"]:
                courses.setdefault(row["course_id"], row)
    if not courses:
        raise CampusError(
            "catalog-missing",
            "No readable local catalog is available.",
            "Run 'campusctl sync' to create one.",
            "user-action",
        )
    if selector.isdecimal() and selector not in courses and not ids_only:
        roster = course_roster(root)
        return resolve_course(selector, roster, ids_only=False, printed_roster=read_course_snapshot(root))
    return resolve_course(selector, list(courses.values()), ids_only=ids_only)


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    del bindings
    from campusctl.status import build_status

    root = data_dir()
    course_id = _select_status_course(args.course, root, ids_only=context.output_mode == "json")
    result, status, errors = build_status(root, course_id, now=datetime.now(UTC))
    # build_status already lists domain error keys; do not convert them again.
    return command_result(result, status=status, errors=errors)


def _summary(result: dict[str, Any], width: int) -> list[str]:
    sections = (
        ("Assignments due soon", "assignments", "due_soon", "unknown_due_count", "campusctl assignments list"),
        ("Unread notices", "notices", "unread", "unknown_count", "campusctl notices list"),
        ("Open incomplete lectures", "lectures", "open_incomplete", "unknown_open_count", "campusctl lectures list"),
    )
    lines = ["Coursework status (local catalogs)"]
    for label, domain, key, unknown, command in sections:
        section = result.get(domain, {})
        entries = section.get(key, [])
        lines.append(f"{label}: {len(entries)}; unknown: {section.get(unknown, 0)}")
        for entry in entries[:3]:
            lines.append("  " + _truncate(_text(entry.get("title"), _text(entry.get("entity_id"))), width - 2))
        if len(entries) > 3:
            lines.append(f"  +{len(entries) - 3} more")
        lines.append(f"  See: {command}")
    coverage = result.get("coverage", {})
    gaps = [f"{name}: {value['state']}" for name, value in coverage.items() if value.get("state") != "available"]
    if gaps:
        lines.append("Missing coverage: " + ", ".join(gaps))
    stale = result.get("stale_courses", [])
    if stale:
        lines.append(f"Stale courses: {len(stale)}")
        lines.extend(
            "  " + _truncate(_text(item.get("label"), _text(item.get("course_id"))), width - 2) for item in stale[:3]
        )
        if len(stale) > 3:
            lines.append(f"  +{len(stale) - 3} more")
    return lines


def render_response(command: str, result: dict[str, Any], width: int, envelope: dict[str, Any]) -> Rendered:
    del command
    missing_all = (
        envelope.get("status") == "user-action"
        and any(
            isinstance(error, dict) and error.get("code") == "catalog-missing" for error in envelope.get("errors", [])
        )
        and not result
    )
    if not missing_all:
        return Rendered(_summary(result, width))
    root = data_dir()
    paths = (
        catalog_path(root),
        *(domain_catalog_path(domain, root) for domain in ("assignments", "notices", "materials")),
    )
    absent = not any(path.exists() for path in paths)
    return Rendered(
        [
            "No coursework has been synced yet. Next: campusctl sync"
            if absent
            else "No readable local catalog is available. Next: campusctl sync"
        ],
        hide_errors=True,
    )
