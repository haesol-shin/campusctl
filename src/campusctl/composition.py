"""Assemble the ordered command modules, render routing and provider bindings."""

from __future__ import annotations

import argparse
from types import ModuleType
from typing import Any, TextIO

from campusctl.command_types import CommandBindings, Renderers
from campusctl.commands import (
    assignments,
    auth,
    config,
    courses,
    doctor,
    lectures,
    materials,
    notices,
    setup,
    status,
    sync,
)
from campusctl.presentation import render_human
from campusctl.providers.cnu import courses as cnu_courses
from campusctl.providers.cnu import player as cnu_player
from campusctl.providers.cnu import sync_all as cnu_sync_all

# Registration order is parser and help order.
COMMANDS = (config, doctor, auth, setup, sync, status, courses, lectures, assignments, materials, notices)
COMMANDS_BY_NAME: dict[str, ModuleType] = {module.__name__.rpartition(".")[2]: module for module in COMMANDS}
# Doctor's JSON key order predates registration order, so it is listed separately.
CAPABILITIES: dict[str, list[str]] = {
    module.__name__.rpartition(".")[2]: list(module.CAPABILITY["commands"])
    for module in (sync, lectures, status, assignments, materials, notices)
}
RENDERERS = Renderers(
    results={name: module.render for name, module in COMMANDS_BY_NAME.items() if hasattr(module, "render")},
    responses={
        name: module.render_response for name, module in COMMANDS_BY_NAME.items() if hasattr(module, "render_response")
    },
)


def write_human(command: str, envelope: dict[str, Any], stream: TextIO) -> None:
    """Write one human envelope with the renderers assembled above."""
    render_human(command, envelope, stream, renderers=RENDERERS)


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for module in COMMANDS:
        module.register(subparsers)


def bind() -> CommandBindings:
    return CommandBindings(
        capabilities=CAPABILITIES,
        discover_courses=cnu_courses.discover_courses,
        play_lectures=cnu_player.play_lectures,
        sync_all=cnu_sync_all.sync_all,
        validate_requested_lectures=cnu_player.validate_requested_lectures,
    )


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    """Return the profile scope of the selected command, or None when it cannot be profiled."""
    hook = getattr(COMMANDS_BY_NAME.get(args.command), "profile_scope", None)
    return None if hook is None else hook(args)
