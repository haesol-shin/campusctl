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
from campusctl.text_layout import _cells, _command_hint, _course_heading, _pad, _text, _truncate, _wrap, _wrap_field

CAPABILITY = {"commands": ["list", "play"]}
_STATUS_WIDTH = 11


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


def _lecture_status(lecture: dict[str, Any]) -> str:
    if lecture.get("open") is False:
        available_date = _available_date(lecture.get("available_from"))
        return f"opens {available_date[5:]}" if available_date is not None else "not open"
    completion = lecture.get("completion")
    if completion == "complete":
        return "done"
    if completion == "recorded":
        return "watched (not counted)"
    if completion != "incomplete":
        return "unknown"
    progress = lecture.get("progress_text")
    if isinstance(progress, str):
        match = re.match(r"\s*(\d+)\s*분(?:\s*(\d+)\s*초)?\s*/", progress)
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2) or 0)
            return "in progress" if minutes or seconds else "not started"
    return "unfinished"


def _course(lecture: dict[str, Any]) -> tuple[str, str]:
    course = lecture.get("course")
    if isinstance(course, dict):
        course_id = _text(course.get("id"), "unknown")
        label = _text(course.get("label"), course_id)
        return course_id, label
    return "unknown", "Unknown course"


def _due_date(lecture: dict[str, Any]) -> str:
    value = lecture.get("due_date")
    if not isinstance(value, str):
        return "-"
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?", value)
    return f"{match.group(1)} {match.group(2)}" if match else value


def _cache_timestamp(value: object) -> str:
    timestamp = _text(value)
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|\+00:00)", timestamp)
    return f"{match.group(1)} {match.group(2)} UTC" if match else timestamp


def _media(lecture: dict[str, Any]) -> str:
    media = _text(lecture.get("media"), "other")
    return "" if media == "video" else f"[{media}]"


def _available_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:$|T)", value)
    return match.group(1) if match else None


def _render_list(result: dict[str, Any], width: int) -> list[str]:  # noqa: C901
    lectures = result.get("lectures")
    if not isinstance(lectures, list):
        return []
    rows = [lecture for lecture in lectures if isinstance(lecture, dict)]
    cache = result.get("cache")
    generated_at = _cache_timestamp(cache.get("generated_at")) if isinstance(cache, dict) else ""
    count_label = "lecture" if len(rows) == 1 else "lectures"
    header = f"{len(rows)} {count_label}"
    if generated_at:
        updated = f"(updated {generated_at})"
        if _cells(f"{header} {updated}") <= width:
            lines = [f"{header} {updated}"]
        else:
            lines = _wrap(header, width) + _wrap(updated, width)
    else:
        lines = _wrap(header, width)
    if not rows:
        lines.append("")
        lines.extend(_wrap("Nothing unfinished.", width))
        lines.extend(
            _command_hint("To include completed or recorded lectures:", "campusctl lectures list --all", width)
        )
        lines.extend(_command_hint("To refresh:", "campusctl sync", width))
        return lines

    groups: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for lecture in rows:
        course_id, label = _course(lecture)
        if course_id not in groups:
            groups[course_id] = (label, [])
        groups[course_id][1].append(lecture)

    for label, group_rows in groups.values():
        lines.append("")
        lines.extend(_course_heading(label, width))
        due_width = max(3, max((_cells(_due_date(lecture)) for lecture in group_rows), default=3))
        media_width = max((_cells(_media(lecture)) for lecture in group_rows), default=0)
        has_media = media_width > 0
        available_title_width = width - 6 - due_width - _STATUS_WIDTH - (2 + media_width if has_media else 0)
        title_width = min(
            available_title_width,
            max((_cells(_text(lecture.get("title"), "(untitled lecture)")) for lecture in group_rows), default=0),
        )
        if width >= 60 and title_width > 0:
            for lecture in group_rows:
                title = _truncate(_text(lecture.get("title"), "(untitled lecture)"), title_width)
                due = _due_date(lecture)
                status = _lecture_status(lecture)
                row = f"  {_pad(title, title_width)}  {_pad(due, due_width)}  {_pad(status, _STATUS_WIDTH)}"
                if has_media:
                    row += f"  {_media(lecture)}"
                lines.append(row.rstrip())
                entity_id = _text(lecture.get("entity_id"), "unknown-entity")
                lines.append(f"    {entity_id}")
        else:
            for lecture in group_rows:
                title_text = _text(lecture.get("title"), "(untitled lecture)")
                title_width = max(0, width - _cells("  Title: "))
                title = _truncate(title_text, title_width) if title_width else title_text
                lines.extend(_wrap_field("Title", title, width))
                lines.extend(_wrap_field("Due", _due_date(lecture), width))
                lines.extend(_wrap_field("Status", _lecture_status(lecture), width))
                media = _media(lecture)
                if media:
                    lines.extend(_wrap_field("Media", media, width))
                entity_id = _text(lecture.get("entity_id"), "unknown-entity")
                lines.append(f"    {entity_id}")

    playable = next(
        (
            lecture
            for lecture in rows
            if lecture.get("open") is True
            and lecture.get("completion") == "incomplete"
            and lecture.get("media") in {"video", "youtube"}
            and isinstance(lecture.get("entity_id"), str)
            and lecture["entity_id"]
        ),
        None,
    )
    lines.append("")
    if playable is not None:
        entity_id = playable["entity_id"]
        command = f"campusctl lectures play {entity_id}"
        lines.extend(_command_hint("To play one:", command, width))
    else:
        opening_dates: list[str] = []
        for lecture in rows:
            if (
                lecture.get("open") is False
                and lecture.get("completion") == "incomplete"
                and lecture.get("media") in {"video", "youtube"}
                and isinstance(lecture.get("entity_id"), str)
                and lecture["entity_id"]
            ):
                available_date = _available_date(lecture.get("available_from"))
                if available_date is not None:
                    opening_dates.append(available_date)
        if opening_dates:
            next_opening = min(opening_dates)
            message = f"No lectures are open to play yet. Next opens: {next_opening[5:]}."
        else:
            message = "No lectures are open to play yet. Opening dates are not published yet."
        lines.extend(_wrap(message, width))
    lines.extend(_command_hint("To refresh:", "campusctl sync", width))
    return lines


def _render_play(result: dict[str, Any], width: int) -> list[str]:
    items = result.get("items")
    if not isinstance(items, list):
        return []
    labels = {
        "completed": "Completed",
        "already-complete": "Already complete",
        "recorded": "Watched (not counted)",
        "unverified": "Unverified",
        "failed": "Failed",
        "not-started": "Not started",
        "unknown": "Unknown",
    }
    counts: dict[str, int] = dict.fromkeys(labels, 0)
    lines: list[str] = []
    processed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("outcome")
        outcome = value if isinstance(value, str) and value else "unknown"
        if outcome not in labels:
            labels[outcome] = outcome.replace("-", " ").capitalize()
            counts[outcome] = 0
        counts[outcome] += 1
        processed += 1
        entity_id = _text(item.get("entity_id"), "unknown-entity")
        lines.extend(_wrap(f"{labels[outcome]}:", width))
        lines.append(f"  {entity_id}")
        if outcome == "recorded":
            lines.extend(
                _wrap(
                    "The LMS row reports full watch progress. It does not count for attendance or mark the lecture complete.",
                    width,
                )
            )
    if not processed:
        lines.extend(_wrap("No lectures were played.", width))
        return lines
    summary = [f"{count} {key.replace('-', ' ')}" for key, count in counts.items() if count]
    lines.extend(_wrap("Playback: " + ", ".join(summary) + ".", width))
    return lines


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    if command == "lectures.list":
        return _render_list(result, width)
    if command == "lectures.play":
        return _render_play(result, width)
    return []
