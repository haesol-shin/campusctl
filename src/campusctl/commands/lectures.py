"""The lectures command family."""

from __future__ import annotations

import argparse
import re
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog import read_catalog
from campusctl.catalog_view import cache_metadata
from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import (
    CommandBindings,
    CommandContext,
    CommandResult,
    command_result,
    unchanged_bare_failure,
)
from campusctl.commands.course_inputs import (
    list_refresh_scope,
    network_course,
    refresh,
    refresh_result,
    select_course,
)
from campusctl.config import SUPPORTED_SPEEDS, load_config
from campusctl.envelope import CampusError
from campusctl.paths import data_dir
from campusctl.progress import _progress_output

CAPABILITY = {"commands": ["list", "play"]}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    lectures = subparsers.add_parser("lectures", help="list or play official lectures")
    _with_json(lectures)
    lecture_commands = lectures.add_subparsers(dest="lectures_command", parser_class=EnvelopeArgumentParser)
    lectures_list = lecture_commands.add_parser("list", help="list cached lectures")
    _with_json(lectures_list)
    lectures_list.add_argument("--all", action="store_true", help="include completed or recorded lectures")
    lectures_list.add_argument("--course", help="limit results to one course ID")
    lectures_list.add_argument("--refresh", action="store_true", help="sync lectures before listing")
    lectures_play = lecture_commands.add_parser("play", help="play explicit lecture IDs")
    _with_json(lectures_play)
    lectures_play.add_argument("entity_ids", nargs="+", metavar="ID", help="full lecture IDs from lectures list")
    lectures_play.add_argument("--speed", type=float, choices=SUPPORTED_SPEEDS, help="playback speed")
    lectures_play.add_argument("--replay", action="store_true", help="explicitly replay completed lectures")


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    return list_refresh_scope(args, ("lectures",))


def _natural_sort_key(value: Any) -> tuple[int, tuple[tuple[int, str | int], ...]]:
    if not isinstance(value, str) or not value.strip():
        return 1, ()
    parts = tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold()) for part in re.split(r"(\d+)", value.strip())
    )
    return 0, parts


@dataclass(frozen=True, slots=True)
class _LectureList:
    """List payload. `refresh` is omitted unless a refresh ran."""

    cache: dict[str, Any]
    lectures: list[Any]
    refresh: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        payload = {"cache": self.cache, "lectures": self.lectures}
        if self.refresh is not None:
            payload["refresh"] = self.refresh
        return payload


def _lecture_course_id(lecture: dict[str, Any]) -> str:
    course = lecture.get("course")
    if not isinstance(course, dict):
        return ""
    course_id = course.get("id")
    return course_id if isinstance(course_id, str) else ""


def _list(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    root = data_dir()
    ids_only = context.output_mode == "json"
    if args.refresh:
        config = load_config()
        mode = preflight_browser_mode(config, "lectures.sync", override=args.headless_override)
        course_id, snapshot = network_course(
            args.course,
            root,
            config,
            "lectures",
            ids_only=ids_only,
            mode=mode,
            discover_courses=bindings.discover_courses,
        )
    else:
        course_id, snapshot = select_course(args.course, root, ids_only=ids_only)
    refreshed = refresh("lectures", root, course_id, args, snapshot, profile=context.profile) if args.refresh else None
    try:
        catalog = read_catalog()
    except CampusError:
        if refreshed is None or not refreshed[1]:
            raise
        return command_result({"refresh": refresh_result(refreshed)}, refreshed[1])
    course_order = {course["course_id"]: index for index, course in enumerate(catalog["courses"])}
    lectures = catalog["lectures"]
    if course_id is not None:
        lectures = [lecture for lecture in lectures if _lecture_course_id(lecture) == course_id]
    if not args.all:
        lectures = [lecture for lecture in lectures if lecture.get("completion") == "incomplete"]
    lectures = sorted(
        lectures,
        key=lambda lecture: (
            course_order.get(_lecture_course_id(lecture), len(course_order)),
            _natural_sort_key(lecture.get("week")),
            _natural_sort_key(lecture.get("sequence")),
        ),
    )
    listed = _LectureList(
        cache={**cache_metadata(catalog, now=datetime.now(UTC), domain="lectures"), "path_present": True},
        lectures=lectures,
        refresh=refresh_result(refreshed) if refreshed is not None else None,
    )
    return command_result(listed.to_json(), None if refreshed is None else refreshed[1])


def _play(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    import asyncio

    from campusctl.browser import open_session

    catalog = read_catalog()
    lectures = bindings.validate_requested_lectures(args.entity_ids, catalog["lectures"], replay=args.replay)
    config = load_config()
    if args.replay and context.output_mode == "human":
        if not context.interactive:
            raise CampusError(
                "replay-confirmation-required",
                "Replay needs two interactive terminals.",
                "Run replay in a terminal or use explicit IDs with --json.",
                "user-action",
            )
        sys.stdout.write(f"Replay {len(lectures)} selected lectures?\n")
        for lecture in lectures:
            sys.stdout.write(f"  {lecture['entity_id']} — {lecture.get('title', '')}\n")
        sys.stdout.flush()
        try:
            consent = input("Replay using the official player? [y/N] ").strip().lower()
        except EOFError:
            consent = ""
        if consent not in {"y", "yes"}:
            raise CampusError("replay-cancelled", "Replay was cancelled.", "Select lectures again.", "user-action")
    mode = preflight_browser_mode(config, "lectures.play", override=args.headless_override)

    progress_callback = None
    finish_progress = None
    if context.output_mode == "human":
        progress_callback, finish_progress = _progress_output()

    async def _run() -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
        try:
            async with open_session(config, data_dir=data_dir(), headless=mode, operation="lectures.play") as session:
                return await bindings.play_lectures(
                    session.page,
                    config,
                    lectures,
                    speed=args.speed,
                    progress=progress_callback,
                    replay=args.replay,
                )
        finally:
            if finish_progress is not None:
                with suppress(Exception):
                    finish_progress()

    return command_result(*asyncio.run(_run()))


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    match args.lectures_command:
        case "list":
            return _list(args, bindings, context)
        case "play":
            return _play(args, bindings, context)
        case _:
            unchanged_bare_failure()
