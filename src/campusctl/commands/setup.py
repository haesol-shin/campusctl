"""The setup command."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.config import load_config
from campusctl.paths import config_path
from campusctl.sync import run_sync
from campusctl.text_layout import _text, _wrap_simple


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


def _summary(command: str, result: dict[str, Any]) -> list[str]:
    if command == "setup" and isinstance(result.get("browser"), dict):
        browser = result["browser"]
        action = browser.get("action")
        if action == "skipped-custom-executable":
            installed = browser.get("installed")
            message = (
                "Custom browser executable is available."
                if installed is True
                else "Custom browser executable is unavailable."
                if installed is False
                else "Custom browser executable availability is unknown."
            )
        elif action in {"installed", "already-installed"}:
            installed = browser.get("installed")
            if installed is True:
                message = "Chromium was installed." if action == "installed" else "Chromium is already installed."
            else:
                message = (
                    "Chromium is unavailable." if installed is False else "Chromium installation status is unknown."
                )
        else:
            actions = {
                "skipped-cdp": "Skipped Chromium installation because a CDP browser is configured.",
                "declined": "Chromium installation was declined.",
            }
            message = actions.get(action, "Browser setup status is unknown.")
        lines = [message]
        next_steps = result.get("next")
        if isinstance(next_steps, list):
            lines.extend(f"Next: {_text(step)}" for step in next_steps if isinstance(step, str) and _text(step))
        return lines
    return []


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    return [wrapped for line in _summary(command, result) for wrapped in _wrap_simple(line, width)]
