"""The setup command."""

from __future__ import annotations

import argparse
import sys

from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.config import load_config
from campusctl.paths import config_path
from campusctl.sync import run_sync


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    setup = subparsers.add_parser("setup", help="guide account, browser and first sync")
    _with_json(setup)


def _confirm_setup(prompt: str) -> bool:
    return input(f"{prompt} ").strip().lower() not in {"n", "no"}


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    del bindings
    from campusctl.setup import run_guided_setup, run_setup

    if context.interactive:
        return command_result(
            *run_guided_setup(
                confirm=_confirm_setup,
                out=sys.stdout,
                run_sync=run_sync,
                headless_override=args.headless_override,
            )
        )
    path = config_path()
    config = load_config(path) if path.exists() else None
    return command_result(run_setup(config, confirm=None, out=sys.stdout if context.output_mode == "human" else None))
