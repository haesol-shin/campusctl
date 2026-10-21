"""The status command."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog_view import DOMAINS, catalog_snapshot, course_roster, read_course_snapshot
from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.course_selection import resolve_course
from campusctl.envelope import CampusError
from campusctl.paths import data_dir

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
