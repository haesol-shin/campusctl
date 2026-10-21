"""The sync command."""

from __future__ import annotations

import argparse
from typing import Any

from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import DOMAINS
from campusctl.cli_args import _OUTPUT_NOTE, _with_json
from campusctl.command_types import CommandBindings
from campusctl.commands.course_inputs import network_course
from campusctl.config import load_config
from campusctl.envelope import CampusError
from campusctl.paths import data_dir
from campusctl.sync import parse_domains, run_sync

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


def dispatch(args: argparse.Namespace, bindings: CommandBindings) -> tuple[Any, CampusError | list[CampusError] | None]:
    root = data_dir()
    ids_only = args._output_mode == "json"
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
    return run_sync(
        config,
        root,
        domains,
        course_id,
        headless=args.headless_override,
        profile=getattr(args, "_profile", None),
        course_snapshot=snapshot,
    )
