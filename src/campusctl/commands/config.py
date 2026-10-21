"""The config command family."""

from __future__ import annotations

import argparse
from typing import Any

from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.config_init import run_config_init
from campusctl.envelope import UsageError
from campusctl.text_layout import _text, _wrap_simple


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


def _summary(command: str, result: dict[str, Any]) -> list[str]:
    if command == "config.init" and any(key in result for key in ("created", "config_path", "next")):
        path = _text(result.get("config_path"), "the configuration path")
        created = result.get("created")
        state = "created" if created is True else "already exists" if created is False else "status is unknown"
        lines = [f"Configuration {state} at {path}."]
        next_steps = result.get("next")
        if isinstance(next_steps, list):
            lines.extend(f"Next: {_text(step)}" for step in next_steps if isinstance(step, str) and _text(step))
        return lines
    return []


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    return [wrapped for line in _summary(command, result) for wrapped in _wrap_simple(line, width)]
