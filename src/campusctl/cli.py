from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

from campusctl import __version__, composition
from campusctl.browser_options import resolve_headless
from campusctl.catalog_view import publish_material_snapshot
from campusctl.cli_args import (
    EnvelopeArgumentParser,
    RootArgumentParser,
    RootHelpFormatter,
    _HelpRequested,
    _with_json,
)
from campusctl.command_types import CommandContext, CommandResult, command_result
from campusctl.commands.course_inputs import dispatch_coursework
from campusctl.config import load_config
from campusctl.course_selection import CourseAmbiguous
from campusctl.envelope import (
    EXIT_CODES,
    CampusError,
    UsageError,
    error_item,
    make_envelope,
    prepare_stdout_for_text,
    write_json,
)
from campusctl.paths import data_dir
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


def _not_implemented() -> None:
    raise CampusError(
        "not-implemented",
        "This campusctl command is not implemented in the current build.",
        "Use a later v0 build that includes this command.",
        "user-action",
    )


def _emit_response(
    command_key: str,
    output_mode: str,
    *,
    result: Any = None,
    error: CampusError | None = None,
    errors: list[CampusError] | None = None,
    status: str | None = None,
) -> int:
    if errors is not None:
        envelope_status = status or "partial"
        envelope_errors = [error_item(item) for item in errors]
    elif error is not None:
        envelope_status = error.status
        envelope_errors = [error_item(error)]
    else:
        envelope_status = status or "ok"
        envelope_errors = []

    envelope = make_envelope(status=envelope_status, result=result, errors=envelope_errors)
    if output_mode == "json":
        write_json(envelope)
    else:
        render_human(command_key, envelope, sys.stdout, renderers=composition.RENDERERS)
    return EXIT_CODES[envelope_status]


def _fetch_batch_status(result: dict[str, Any], errors: list[CampusError]) -> str:
    """Return the multi-ID fetch envelope status from published packages and ordered failures."""
    items = result.get("items")
    records = items if isinstance(items, list) else []
    published = any(isinstance(item, dict) and item.get("outcome") in {"completed", "partial"} for item in records)
    unsuccessful = any(
        isinstance(item, dict) and item.get("outcome") in {"partial", "failed", "not-started"} for item in records
    )
    if published and unsuccessful:
        return "partial"
    if not published and errors:
        return errors[0].status
    return "ok"


def _emit_partial(
    result: dict[str, Any],
    errors: list[CampusError],
    *,
    output_mode: str,
    command_key: str,
) -> int:
    return _emit_response(
        command_key,
        output_mode,
        result=result,
        errors=errors,
        status="partial",
    )


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
    """Write a local result whose status and errors were resolved at the command edge."""
    envelope = make_envelope(status=record.status, result=record.result, errors=list(record.errors))
    if output_mode == "json":
        write_json(envelope)
    else:
        render_human(command_key, envelope, sys.stdout, renderers=composition.RENDERERS)
    return EXIT_CODES[record.status]


def _dispatch(
    args: argparse.Namespace, context: CommandContext
) -> CommandResult | tuple[Any, CampusError | list[CampusError] | None]:
    if args.version:
        return command_result({"version": __version__})
    if args.command is None:
        raise UsageError("a command is required")
    module = composition.COMMANDS_BY_NAME[args.command]
    bindings = composition.bind()
    if module in composition.COURSEWORK:
        # Coursework still reads these until P4. Local commands take CommandContext.
        args._profile = context.profile
        args._interactive = context.interactive
        args._output_mode = context.output_mode
        return dispatch_coursework(module, args, bindings)
    return module.dispatch(args, bindings, context)


def _emit_dispatched(
    dispatched: CommandResult | tuple[Any, CampusError | list[CampusError] | None],
    *,
    command_key: str,
    output_mode: str,
) -> tuple[int, Any, Callable[[], None] | None]:
    if isinstance(dispatched, CommandResult):
        return _emit_command(command_key, output_mode, dispatched), dispatched.result, dispatched.publish
    result, error = dispatched
    if isinstance(error, list):
        if command_key in {"assignments.fetch", "notices.fetch"} and isinstance(result, dict) and "items" in result:
            code = _emit_response(
                command_key,
                output_mode,
                result=result,
                errors=error,
                status=_fetch_batch_status(result, error),
            )
        else:
            code = _emit_partial(result, error, output_mode=output_mode, command_key=command_key)
        return code, result, None
    return _emit_response(command_key, output_mode, result=result, error=error), result, None


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
        code, result, publish = _emit_dispatched(dispatched, command_key=command_key, output_mode=output_mode)
        if code == 0 and output_mode == "human" and publish is not None:
            sys.stdout.flush()
            publish()
        if code == 0 and output_mode == "human" and command_key == "materials.list":
            sys.stdout.flush()
            publish_material_snapshot(
                data_dir(),
                args._material_generation,
                [row["entity_id"] for row in result["materials"]],
                course_id=args.course,
            )
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
