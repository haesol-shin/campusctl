"""The courses command family."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import partial
from typing import Any

from campusctl.catalog_view import course_roster, publish_course_snapshot
from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import CommandBindings, CommandResult
from campusctl.commands.course_inputs import list_refresh_scope, refresh, refresh_result
from campusctl.envelope import CampusError
from campusctl.paths import data_dir


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    courses = subparsers.add_parser("courses", help="list cached courses")
    _with_json(courses)
    course_commands = courses.add_subparsers(dest="courses_command", parser_class=EnvelopeArgumentParser)
    courses_list = course_commands.add_parser("list", help="list cached courses")
    _with_json(courses_list)
    courses_list.add_argument("--refresh", action="store_true", help="sync courses before listing")


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    return list_refresh_scope(args, ("lectures",))


def dispatch(
    args: argparse.Namespace, bindings: CommandBindings
) -> tuple[Any, CampusError | list[CampusError] | None] | CommandResult | None:
    del bindings
    root = data_dir()
    ids_only = args._output_mode == "json"
    if args.courses_command == "list":
        refreshed = refresh("lectures", root, None, args) if args.refresh else None
        try:
            roster = course_roster(root)
        except CampusError:
            if refreshed is None or not refreshed[1]:
                raise
            return {"refresh": refresh_result(refreshed)}, refreshed[1]
        result = {"cache": {**roster["cache"], "path_present": True}, "courses": roster["courses"]}
        if refreshed is not None:
            result["refresh"] = refresh_result(refreshed)
            if refreshed[1]:
                return result, refreshed[1]
        publish: Callable[[], None] | None = None
        if not ids_only:
            publish = partial(publish_course_snapshot, root, roster)
        return CommandResult(result, publish=publish)
    # No subcommand: the caller's unpacking rejects this as before; L3 decides the contract.
    return None
