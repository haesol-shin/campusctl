"""Parser subclasses, help formatting and JSON-option helpers."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from campusctl.envelope import UsageError
from campusctl.paths import config_path, data_dir

_HELP_GROUPS = (
    ("Get started", ("setup", "doctor")),
    ("Everyday", ("status", "sync", "lectures", "assignments", "notices", "materials", "courses")),
    ("Settings", ("config", "auth")),
)
_OUTPUT_NOTE = (
    "Terminal stdout defaults to human output; redirected stdout defaults to JSON.\n"
    "--json forces the JSON envelope; CAMPUSCTL_OUTPUT=human|json overrides auto mode\n"
    "unless --json is set."
)


class RootHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def _format_action(self, action: argparse.Action) -> str:
        if not isinstance(action, argparse._SubParsersAction):
            return super()._format_action(action)
        choices = {choice.dest: choice for choice in action._get_subactions()}
        lines = []
        for heading, names in _HELP_GROUPS:
            lines.append(f"  {heading}:\n")
            for name in names:
                lines.append("  " + super()._format_action(choices[name]))
        return "".join(lines)


class _HelpRequested(Exception):
    pass


class EnvelopeArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        del message
        raise UsageError("Invalid command-line usage.")

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status == 0:
            if message:
                self._print_message(message, sys.stdout)
            raise _HelpRequested
        raise UsageError("Invalid command-line usage.")


class RootArgumentParser(EnvelopeArgumentParser):
    def format_help(self) -> str:
        self.epilog = (
            "Examples:\n  campusctl setup\n  campusctl sync\n  campusctl status\n\n"
            f"Config: {config_path().resolve()}\nData: {data_dir().resolve()}\n"
            "Docs: https://github.com/haesol-shin/campusctl/blob/main/docs/usage.md\n\n"
            + _OUTPUT_NOTE
            + "\n\nAre you an AI agent? Read these only when your task asks for it:\n"
            "  Help a user install or set up campusctl:\n"
            "    https://raw.githubusercontent.com/haesol-shin/campusctl/main/docs/agent-guide.md\n"
            "  Use campusctl for the user:\n"
            "    SKIP if the campusctl skill is already loaded. Otherwise read\n"
            "    https://raw.githubusercontent.com/haesol-shin/campusctl/main/skills/campusctl/SKILL.md\n"
            "  Never run `campusctl setup` (with any flags, including `--json`), `campusctl config init`,\n"
            "  or `campusctl auth set` yourself. The user runs `campusctl setup` in their own terminal."
        )
        return super().format_help()


def _with_json(parser: argparse.ArgumentParser, *, default: Any = argparse.SUPPRESS) -> None:
    parser.add_argument("--json", action="store_true", default=default, help="emit the JSON response envelope")
