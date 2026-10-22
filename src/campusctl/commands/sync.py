"""The sync command."""

from __future__ import annotations

import argparse
from typing import Any

from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import DOMAINS
from campusctl.cli_args import _OUTPUT_NOTE, _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.commands.course_inputs import network_course
from campusctl.config import load_config
from campusctl.envelope import CampusError
from campusctl.paths import data_dir
from campusctl.sync import parse_domains, run_sync
from campusctl.text_layout import _text, _wrap

CAPABILITY = {"commands": list(DOMAINS)}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sync = subparsers.add_parser("sync", help="refresh four coursework catalogs", epilog=_OUTPUT_NOTE)
    _with_json(sync)
    sync.add_argument(
        "--only",
        metavar="DOMAIN[,DOMAIN...]",
        help="sync only lectures, assignments, notices, materials (comma-separated)",
    )
    sync.add_argument("--course", help="limit sync to one course")


def command_key(args: argparse.Namespace) -> str:
    if getattr(args, "only", None) is not None:
        try:
            domains = parse_domains(args.only)
        except CampusError:
            return "sync"
        return f"sync.{domains[0]}" if len(domains) == 1 else "sync"
    return "sync"


def profile_scope(args: argparse.Namespace) -> tuple[str, ...]:
    return parse_domains(args.only)


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    root = data_dir()
    ids_only = context.output_mode == "json"
    domains = parse_domains(args.only)
    config = load_config()
    modes = {
        domain: preflight_browser_mode(config, f"{domain}.sync", override=args.headless_override) for domain in domains
    }
    course_id, snapshot = network_course(
        args.course,
        root,
        config,
        domains[0],
        ids_only=ids_only,
        mode=modes[domains[0]],
        discover_courses=bindings.discover_courses,
    )
    return command_result(
        *run_sync(
            config,
            root,
            domains,
            course_id,
            collector=bindings.sync_all,
            headless=args.headless_override,
            profile=context.profile,
            course_snapshot=snapshot,
        )
    )


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    del command  # `sync` and `sync.lectures` print the same summary.
    if isinstance(result.get("domains"), dict):
        return [f"{name}: {entry.get('status', 'unknown')}" for name, entry in result["domains"].items()]
    if not any(key in result for key in ("courses", "lectures", "incomplete", "failed_courses")):
        return []
    courses = result.get("courses", 0)
    lectures = result.get("lectures", 0)
    incomplete = result.get("incomplete", 0)
    lines = _wrap(
        f"Synced {courses} {'course' if courses == 1 else 'courses'}, "
        f"{lectures} {'lecture' if lectures == 1 else 'lectures'}, {incomplete} unfinished.",
        width,
    )
    failed = result.get("failed_courses")
    if isinstance(failed, list):
        for course in failed:
            if not isinstance(course, dict):
                continue
            label = _text(course.get("label"), _text(course.get("course_id"), "Unknown course"))
            course_id = _text(course.get("course_id"), "unknown")
            lines.extend(_wrap(f"Failed: {label} ({course_id})", width))
    return lines
