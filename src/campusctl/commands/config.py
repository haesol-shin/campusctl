"""The config command family."""

from __future__ import annotations

import argparse

from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.config_init import run_config_init
from campusctl.envelope import UsageError


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config = subparsers.add_parser("config", help="create or inspect configuration")
    _with_json(config)
    config_commands = config.add_subparsers(dest="config_command", parser_class=EnvelopeArgumentParser)
    config_init = config_commands.add_parser("init", help="create an initial configuration file")
    _with_json(config_init)
    config_init.add_argument("--username", help="CNU login ID")


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    del bindings
    if args.config_command != "init":
        raise UsageError("a config command is required")
    return command_result(*run_config_init(args.username, interactive=context.interactive))
