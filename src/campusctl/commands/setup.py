"""The setup command."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings
from campusctl.config import load_config
from campusctl.envelope import CampusError
from campusctl.paths import config_path
from campusctl.sync import run_sync


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    setup = subparsers.add_parser("setup", help="guide account, browser and first sync")
    _with_json(setup)


def _confirm_setup(prompt: str) -> bool:
    return input(f"{prompt} ").strip().lower() not in {"n", "no"}


def dispatch(args: argparse.Namespace, bindings: CommandBindings) -> tuple[Any, CampusError | None]:
    del bindings
    from campusctl.setup import run_guided_setup, run_setup

    if args._interactive:
        return run_guided_setup(
            confirm=_confirm_setup,
            out=sys.stdout,
            run_sync=run_sync,
            headless_override=args.headless_override,
        )
    path = config_path()
    config = load_config(path) if path.exists() else None
    return run_setup(config, confirm=None, out=sys.stdout if args._output_mode == "human" else None), None
