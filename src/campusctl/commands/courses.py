"""The courses command family."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from campusctl.catalog_view import course_roster, publish_course_snapshot
from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import (
    CommandBindings,
    CommandContext,
    CommandResult,
    command_result,
    unchanged_bare_failure,
)
from campusctl.commands.course_inputs import list_refresh_scope, refresh, refresh_result
from campusctl.envelope import CampusError
from campusctl.paths import data_dir
from campusctl.text_layout import _cells, _course_heading, _pad, _text, _truncate, _wrap


@dataclass(frozen=True, slots=True)
class _CourseList:
    """List payload. `refresh` is omitted unless a refresh ran."""

    cache: dict[str, Any]
    courses: list[Any]
    refresh: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        payload = {"cache": self.cache, "courses": self.courses}
        if self.refresh is not None:
            payload["refresh"] = self.refresh
        return payload


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    courses = subparsers.add_parser("courses", help="list cached courses")
    _with_json(courses)
    course_commands = courses.add_subparsers(dest="courses_command", parser_class=EnvelopeArgumentParser)
    courses_list = course_commands.add_parser("list", help="list cached courses")
    _with_json(courses_list)
    courses_list.add_argument("--refresh", action="store_true", help="sync courses before listing")


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    return list_refresh_scope(args, ("lectures",))


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    # Resolve the data directory before the historical bare-command TypeError.
    root = data_dir()
    if args.courses_command != "list":
        unchanged_bare_failure()
    refreshed = (
        refresh("lectures", root, None, args, collector=bindings.sync_all, profile=context.profile)
        if args.refresh
        else None
    )
    try:
        roster = course_roster(root)
    except CampusError:
        if refreshed is None or not refreshed[1]:
            raise
        return command_result({"refresh": refresh_result(refreshed)}, refreshed[1])
    listed = _CourseList(
        cache={**roster["cache"], "path_present": True},
        courses=roster["courses"],
        refresh=refresh_result(refreshed) if refreshed is not None else None,
    )
    if refreshed is not None and refreshed[1]:
        return command_result(listed.to_json(), refreshed[1])
    publish: Callable[[], None] | None = None
    if context.output_mode != "json":
        publish = partial(publish_course_snapshot, root, roster)
    return command_result(listed.to_json(), publish=publish)


def _render_list(result: dict[str, Any], width: int) -> list[str]:
    courses = result.get("courses")
    if not isinstance(courses, list):
        return []
    rows = [course for course in courses if isinstance(course, dict)]
    count_label = "course" if len(rows) == 1 else "courses"
    lines = _wrap(f"{len(rows)} {count_label}", width)
    for number, course in enumerate(rows, 1):
        course_id = _text(course.get("course_id"), "unknown")
        label = _text(course.get("label"), course_id)
        if width < 60 or _cells(course_id) + 8 > width:
            lines.extend(_course_heading(label, width))
            lines.extend(_wrap(f"  {number}. {course_id}", width))
        else:
            label_width = width - _cells(course_id) - 8
            lines.append(f"{number}. {_pad(_truncate(label, label_width), label_width)}  {course_id}")
    return lines


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    return _render_list(result, width) if command == "courses.list" else []
