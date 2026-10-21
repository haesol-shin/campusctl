"""The doctor command."""

from __future__ import annotations

import argparse
from typing import Any

from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings
from campusctl.doctor import doctor_result
from campusctl.envelope import CampusError


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = subparsers.add_parser("doctor", help="check local readiness")
    _with_json(doctor)


def dispatch(args: argparse.Namespace, bindings: CommandBindings) -> tuple[dict[str, Any], CampusError | None]:
    return doctor_result(args.headless_override, bindings.capabilities)
