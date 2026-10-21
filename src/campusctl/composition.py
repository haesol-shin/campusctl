"""Assemble the ordered command modules, render routing and provider bindings."""

from __future__ import annotations

import argparse
from types import ModuleType

from campusctl.command_types import CommandBindings, Renderer
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
from campusctl.commands.course_inputs import list_refresh_scope
from campusctl.providers.cnu import courses as cnu_courses
from campusctl.providers.cnu import player as cnu_player

# Assignments, materials and notices still share the old list/refresh dispatch until P4.
COURSEWORK = (assignments, materials, notices)
# Registration order is parser and help order.
COMMANDS = (config, doctor, auth, setup, sync, status, courses, lectures, *COURSEWORK)
COMMANDS_BY_NAME: dict[str, ModuleType] = {module.__name__.rpartition(".")[2]: module for module in COMMANDS}
# Doctor's JSON key order predates registration order, so it is listed separately.
CAPABILITIES: dict[str, list[str]] = {
    module.__name__.rpartition(".")[2]: list(module.CAPABILITY["commands"])
    for module in (sync, lectures, status, *COURSEWORK)
}
RENDERERS: dict[str, Renderer] = {
    name: module.render for name, module in COMMANDS_BY_NAME.items() if hasattr(module, "render")
}


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for module in COMMANDS:
        module.register(subparsers)


def bind() -> CommandBindings:
    return CommandBindings(
        capabilities=CAPABILITIES,
        discover_courses=cnu_courses.discover_courses,
        play_lectures=cnu_player.play_lectures,
        validate_requested_lectures=cnu_player.validate_requested_lectures,
    )


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    """Return the profile scope of the selected command, or None when it cannot be profiled."""
    module = COMMANDS_BY_NAME.get(args.command)
    if module in COURSEWORK:
        return list_refresh_scope(args, (args.command,))
    hook = getattr(module, "profile_scope", None)
    return None if hook is None else hook(args)
