"""The config command family."""

from __future__ import annotations

import argparse
from typing import Any

from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import CommandBindings
from campusctl.config_init import run_config_init
from campusctl.envelope import CampusError, UsageError


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config = subparsers.add_parser("config", help="create or inspect configuration")
    _with_json(config)
    config_commands = config.add_subparsers(dest="config_command", parser_class=EnvelopeArgumentParser)
    config_init = config_commands.add_parser("init", help="create an initial configuration file")
    _with_json(config_init)
    config_init.add_argument("--username", help="CNU login ID")


def _interactive_config_init(args: argparse.Namespace | None) -> bool:
    return (
        args is not None
        and getattr(args, "command", None) == "config"
        and not args.version
        and getattr(args, "config_command", None) == "init"
        and bool(getattr(args, "_interactive", False))
    )


def dispatch(args: argparse.Namespace, bindings: CommandBindings) -> tuple[Any, CampusError | list[CampusError] | None]:
    del bindings
    if args.config_command is None:
        raise UsageError("a config command is required")
    if args.config_command == "init":
        return run_config_init(args.username, interactive=_interactive_config_init(args))
    raise UsageError("a config command is required")
