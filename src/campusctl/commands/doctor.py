"""The doctor command."""

from __future__ import annotations

import argparse

from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.doctor import doctor_result


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = subparsers.add_parser("doctor", help="check local readiness")
    _with_json(doctor)


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    del context
    return command_result(*doctor_result(args.headless_override, bindings.capabilities))
