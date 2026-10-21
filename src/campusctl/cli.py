from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any

from campusctl import __version__, composition
from campusctl.browser_options import resolve_headless
from campusctl.cli_args import (
    EnvelopeArgumentParser,
    RootArgumentParser,
    RootHelpFormatter,
    _HelpRequested,
    _with_json,
)
from campusctl.command_types import CommandContext, CommandResult, command_result
from campusctl.config import load_config
from campusctl.course_selection import CourseAmbiguous
from campusctl.envelope import (
    EXIT_CODES,
    CampusError,
    UsageError,
    make_envelope,
    prepare_stdout_for_text,
    write_json,
)
from campusctl.presentation import render_human
from campusctl.profiling import SpanRecorder


def build_parser() -> argparse.ArgumentParser:
    parser = RootArgumentParser(
        prog="campusctl",
        description="Check CNU coursework from the terminal or an AI agent.",
        formatter_class=RootHelpFormatter,
    )
    parser._positionals.title = "Commands"
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--headless",
        dest="headless_override",
        action="store_const",
        const=True,
        default=None,
        help="use headless Chromium where supported",
    )
    mode.add_argument(
        "--headed", dest="headless_override", action="store_const", const=False, help="force a visible browser"
    )
    parser.add_argument("--profile", action="store_true", help="profile sync or refresh on stderr")
    _with_json(parser, default=False)
    parser.add_argument("--version", action="store_true", help="show the campusctl version")
    commands = parser.add_subparsers(dest="command", parser_class=EnvelopeArgumentParser)
    composition.register_commands(commands)
    return parser


def _emit_response(command_key: str, output_mode: str, *, result: Any = None, error: CampusError | None = None) -> int:
    return _emit_command(command_key, output_mode, command_result(result, error))


def _resolve_output_mode(argv: Sequence[str], *, stdout_isatty: bool) -> str:
    if "--json" in argv:
        return "json"
    configured = os.environ.get("CAMPUSCTL_OUTPUT")
    if configured == "json":
        return "json"
    if configured == "human":
        return "human"
    return "human" if stdout_isatty else "json"


def _command_key(args: argparse.Namespace | None) -> str:
    if args is None:
        return "usage"
    if args.version:
        return "version"
    command = getattr(args, "command", None)
    hook = getattr(composition.COMMANDS_BY_NAME.get(command), "command_key", None)
    if hook is not None:
        return hook(args)
    subcommand = getattr(args, f"{command}_command", None)
    if isinstance(subcommand, str) and subcommand:
        return f"{command}.{subcommand}"
    return command if isinstance(command, str) else "usage"


def _emit_command(command_key: str, output_mode: str, record: CommandResult) -> int:
    """Write a result whose status and errors were resolved at the command edge."""
    envelope = make_envelope(status=record.status, result=record.result, errors=list(record.errors))
    if output_mode == "json":
        write_json(envelope)
    else:
        render_human(command_key, envelope, sys.stdout, renderers=composition.RENDERERS)
    return EXIT_CODES[record.status]


def _dispatch(args: argparse.Namespace, context: CommandContext) -> CommandResult:
    if args.version:
        return command_result({"version": __version__})
    if args.command is None:
        raise UsageError("a command is required")
    return composition.COMMANDS_BY_NAME[args.command].dispatch(args, composition.bind(), context)


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    output_mode = "json"
    recorder: SpanRecorder | None = None
    outcome = "failed"
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        stdout_isatty = sys.stdout.isatty()
        stdin_isatty = sys.stdin.isatty()
        output_mode = _resolve_output_mode(arguments, stdout_isatty=stdout_isatty)
        if output_mode == "human":
            prepare_stdout_for_text()
        interactive = output_mode == "human" and stdin_isatty and stdout_isatty
        parser = build_parser()
        args = parser.parse_args(arguments)
        if args.profile:
            scope = composition.profile_scope(args)
            if scope is None:
                raise UsageError("--profile is valid only for sync or list --refresh.")
            mode = resolve_headless(load_config(), args.headless_override)
            recorder = SpanRecorder(enabled=True, mode="headless" if mode else "headed", scope=scope)
        context = CommandContext(output_mode, interactive, recorder)
        dispatched = _dispatch(args, context)
        command_key = _command_key(args)
        code = _emit_command(command_key, output_mode, dispatched)
        if code == 0 and output_mode == "human" and dispatched.publish is not None:
            sys.stdout.flush()
            dispatched.publish()
        outcome = "ok" if code == 0 else "failed"
        return code
    except KeyboardInterrupt:
        if output_mode == "json":
            raise
        sys.stdout.write("Interrupted.\n")
        return 130
    except _HelpRequested:
        return 0
    except CourseAmbiguous as error:
        return _emit_response(_command_key(args), output_mode, result={"candidates": error.candidates}, error=error)
    except CampusError as error:
        return _emit_response(_command_key(args), output_mode, error=error)
    except Exception as error:
        # Never include exception text: third-party exceptions can contain credentials or endpoints.
        safe_error = CampusError("internal", type(error).__name__, None, "error")
        return _emit_response(_command_key(args), output_mode, error=safe_error)
    finally:
        if recorder is not None:
            recorder.finish(outcome=outcome)


if __name__ == "__main__":
    raise SystemExit(main())
