from __future__ import annotations

import argparse
import importlib
import os
import platform
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any

from campusctl import __version__
from campusctl.catalog import catalog_path, read_catalog
from campusctl.commands import discover_domain_modules
from campusctl.config import load_config
from campusctl.config_init import run_config_init
from campusctl.credentials import helper_status, keyring_status, prompt_and_store
from campusctl.envelope import (
    EXIT_CODES,
    CampusError,
    UsageError,
    error_item,
    make_envelope,
    prepare_stdout_for_text,
    write_json,
)
from campusctl.paths import config_path, data_dir
from campusctl.presentation import render_human, render_progress

SUPPORTED_SPEEDS = [1.0, 1.25, 1.5]
_DOMAIN_MODULES: dict[str, ModuleType] = discover_domain_modules()
CAPABILITIES = {
    "sync": ["lectures", *_DOMAIN_MODULES],
    "lectures": ["list", "play"],
    **{name: list(module.CAPABILITY["commands"]) for name, module in _DOMAIN_MODULES.items()},
}


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


def _with_json(parser: argparse.ArgumentParser, *, default: Any = argparse.SUPPRESS) -> None:
    parser.add_argument("--json", action="store_true", default=default, help="emit the JSON response envelope")


def build_parser() -> argparse.ArgumentParser:
    parser = EnvelopeArgumentParser(prog="campusctl", description="Campus LMS control CLI")
    _with_json(parser, default=False)
    parser.add_argument("--version", action="store_true", help="show the campusctl version")
    commands = parser.add_subparsers(dest="command", parser_class=EnvelopeArgumentParser)

    config = commands.add_parser("config", help="create or inspect configuration")
    _with_json(config)
    config_commands = config.add_subparsers(dest="config_command", parser_class=EnvelopeArgumentParser)
    config_init = config_commands.add_parser("init", help="create an initial configuration file")
    _with_json(config_init)
    config_init.add_argument("--username", help="CNU login ID")

    doctor = commands.add_parser("doctor", help="inspect local readiness")
    _with_json(doctor)

    auth = commands.add_parser("auth", help="manage credential configuration")
    _with_json(auth)
    auth_commands = auth.add_subparsers(dest="auth_command", parser_class=EnvelopeArgumentParser)
    auth_set = auth_commands.add_parser("set", help="save a keyring password")
    _with_json(auth_set)
    auth_status = auth_commands.add_parser("status", help="inspect credential status")
    _with_json(auth_status)
    auth_status.add_argument("--check", action="store_true", help="run the configured helper and check its response")

    setup = commands.add_parser("setup", help="install or check the local Chromium browser")
    _with_json(setup)

    sync = commands.add_parser("sync", help="sync a local catalog (lectures by default)")
    _with_json(sync)
    sync.add_argument("--only", default="lectures", help="lectures, assignments, notices, or materials")
    sync.add_argument("--headless", action="store_true", help="request headless mode for an approved domain sync")
    sync.add_argument("--course", help="limit sync to one course ID")

    courses = commands.add_parser("courses", help="list cached courses")
    _with_json(courses)
    course_commands = courses.add_subparsers(dest="courses_command", parser_class=EnvelopeArgumentParser)
    courses_list = course_commands.add_parser("list", help="list cached courses")
    _with_json(courses_list)

    lectures = commands.add_parser("lectures", help="list or play lectures")
    _with_json(lectures)
    lecture_commands = lectures.add_subparsers(dest="lectures_command", parser_class=EnvelopeArgumentParser)
    lectures_list = lecture_commands.add_parser("list", help="list cached lectures")
    _with_json(lectures_list)
    lectures_list.add_argument("--all", action="store_true", help="include completed or recorded lectures")
    lectures_list.add_argument("--course", help="limit results to one course ID")
    lectures_play = lecture_commands.add_parser("play", help="play explicit lecture IDs")
    _with_json(lectures_play)
    lectures_play.add_argument("entity_ids", nargs="+")
    lectures_play.add_argument("--speed", type=float, choices=SUPPORTED_SPEEDS)
    for module in _DOMAIN_MODULES.values():
        module.register(commands)
    return parser


def _playwright_diagnostics(executable_path: str | None) -> tuple[bool, bool]:

    try:
        importlib.import_module("playwright.sync_api._context_manager")
    except Exception:
        return False, False
    if executable_path:
        try:
            return True, Path(executable_path).is_file()
        except OSError:
            return True, False
    try:
        context_manager = importlib.import_module("playwright.sync_api._context_manager")
        playwright = context_manager.PlaywrightContextManager().start()
        try:
            return True, Path(playwright.chromium.executable_path).is_file()
        finally:
            playwright.stop()
    except Exception:
        return True, False


def _read_catalog_generated_at(path: Path) -> tuple[bool, str | None, CampusError | None]:
    try:
        catalog = read_catalog(path)
    except CampusError as error:
        return path.exists(), None, error
    return True, catalog["generated_at"], None


def doctor_result() -> tuple[dict[str, Any], CampusError | None]:
    cfg_path = config_path()
    data_path = data_dir()
    config_present = cfg_path.is_file()
    config: dict[str, Any] | None = None
    first_error: CampusError | None = None
    if config_present:
        try:
            config = load_config(cfg_path)
        except CampusError as error:
            first_error = error
    else:
        try:
            load_config(cfg_path)
        except CampusError as error:
            first_error = error

    provider = config.get("provider", "cnu") if config else None
    credential_provider = config.get("credentials", {}).get("provider", "keyring") if config else None
    credentials_configured = False
    if config and credential_provider == "keyring":
        try:
            _, credentials_configured = keyring_status(config)
        except CampusError as error:
            if first_error is None:
                first_error = error
        if first_error is None and not credentials_configured:
            first_error = CampusError(
                "credentials-not-configured",
                "No saved credentials are available for this provider.",
                "Run 'campusctl auth set' or configure the credential helper.",
                "user-action",
            )
    elif config and credential_provider == "command":
        credentials_configured = bool(config.get("credentials", {}).get("command"))

    browser_config = config.get("browser", {}) if config else {}
    browser_mode = "cdp" if browser_config.get("cdp_endpoint") else "local"
    playwright_importable: bool | None = None
    chromium_installed: bool | None = None
    if browser_mode == "local":
        playwright_importable, chromium_installed = _playwright_diagnostics(browser_config.get("executable_path"))
        if first_error is None and (not playwright_importable or not chromium_installed):
            first_error = CampusError(
                "browser-not-installed",
                "Playwright or its Chromium browser is not installed.",
                "Run 'campusctl setup' to install the browser.",
                "user-action",
            )
    display_available: bool | None = None
    if sys.platform.startswith("linux"):
        display_available = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if browser_mode == "local" and not display_available and first_error is None:
            first_error = CampusError(
                "display-unavailable",
                "No graphical display is available for visible browser playback.",
                "Configure a display or run the command with 'xvfb-run'.",
                "user-action",
            )

    catalog_present, generated_at, catalog_error = _read_catalog_generated_at(catalog_path(data_path))
    if first_error is None:
        first_error = catalog_error
    result = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config": {"path": str(cfg_path), "present": config_present},
        "data_dir": str(data_path),
        "provider": provider,
        "credentials": {"provider": credential_provider, "configured": credentials_configured},
        "browser": {
            "mode": browser_mode,
            "playwright_importable": playwright_importable,
            "chromium_installed": chromium_installed,
        },
        "display_available": display_available,
        "playback": {
            "default_speed": config.get("playback", {}).get("default_speed", 1.0) if config else 1.0,
            "supported_speeds": SUPPORTED_SPEEDS,
        },
        "catalog": {"present": catalog_present, "generated_at": generated_at},
        "capabilities": CAPABILITIES,
    }
    return result, first_error


def _not_implemented() -> None:
    raise CampusError(
        "not-implemented",
        "This campusctl command is not implemented in the current build.",
        "Use a later v0 build that includes this command.",
        "user-action",
    )


def _natural_sort_key(value: Any) -> tuple[int, tuple[tuple[int, str | int], ...]]:
    if not isinstance(value, str) or not value.strip():
        return 1, ()
    parts = tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold()) for part in re.split(r"(\d+)", value.strip())
    )
    return 0, parts


def _lecture_course_id(lecture: dict[str, Any]) -> str:
    course = lecture.get("course")
    if not isinstance(course, dict):
        return ""
    course_id = course.get("id")
    return course_id if isinstance(course_id, str) else ""


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
        envelope_status = "partial"
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
        render_human(command_key, envelope, sys.stdout)
    return EXIT_CODES[envelope_status]


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


def _interactive_config_init(args: argparse.Namespace | None) -> bool:
    return (
        args is not None
        and getattr(args, "command", None) == "config"
        and not args.version
        and getattr(args, "config_command", None) == "init"
        and bool(getattr(args, "_interactive", False))
    )


def _command_key(args: argparse.Namespace | None) -> str:
    if args is None:
        return "usage"
    if args.version:
        return "version"
    command = getattr(args, "command", None)
    if command == "sync" and getattr(args, "only", "lectures") != "lectures":
        return f"sync.{args.only}"
    if command == "config" and getattr(args, "config_command", None) == "init":
        return "config.init"
    if command == "auth" and getattr(args, "auth_command", None):
        return f"auth.{args.auth_command}"
    if command == "courses" and getattr(args, "courses_command", None):
        return f"courses.{args.courses_command}"
    if command == "lectures" and getattr(args, "lectures_command", None):
        return f"lectures.{args.lectures_command}"
    if command in _DOMAIN_MODULES:
        subcommand = getattr(args, f"{command}_command", None)
        if isinstance(subcommand, str):
            return f"{command}.{subcommand}"
    return command if isinstance(command, str) else "usage"


def _confirm_setup(prompt: str) -> bool:
    return input(f"{prompt} ").strip().lower() not in {"n", "no"}


def _progress_output() -> tuple[Callable[[dict[str, Any]], None], Callable[[], None]]:
    is_tty = sys.stdout.isatty()
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    position_open = False
    previous_position = ""

    def finish() -> None:
        nonlocal position_open
        if position_open:
            position_open = False
            sys.stdout.write("\n")
            sys.stdout.flush()

    def report(event: dict[str, Any]) -> None:
        nonlocal position_open, previous_position
        if position_open:
            if is_tty and event.get("type") == "position":
                sys.stdout.write("\r")
                line = render_progress(event, sys.stdout, width)
                sys.stdout.write(" " * max(0, len(previous_position) - len(line)))
                previous_position = line
                sys.stdout.flush()
                return
            finish()
        if event.get("type") == "position" and is_tty:
            sys.stdout.write("\r")
            previous_position = render_progress(event, sys.stdout, width)
            position_open = True
            sys.stdout.flush()
        else:
            render_progress(event, sys.stdout, width)
            sys.stdout.write("\n")
            sys.stdout.flush()

    return report, finish


def _dispatch(args: argparse.Namespace) -> tuple[Any, CampusError | list[CampusError] | None]:
    if args.version:
        return {"version": __version__}, None
    if args.command is None:
        raise UsageError("a command is required")
    if args.command == "config":
        if args.config_command is None:
            raise UsageError("a config command is required")
        if args.config_command == "init":
            return run_config_init(args.username, interactive=_interactive_config_init(args))
        raise UsageError("a config command is required")
    if args.command == "doctor":
        return doctor_result()
    if args.command == "auth":
        if args.auth_command is None:
            raise UsageError("an auth command is required")
        config = load_config()
        if args.auth_command == "set":
            prompt_and_store(config, stdin_isatty=sys.stdin.isatty())
            return {"provider": "keyring", "configured": True}, None
        provider = config.get("credentials", {}).get("provider", "keyring")
        if provider == "command":
            backend, configured, check_status = helper_status(config, check=args.check)
            credentials = {"provider": "command", "helper": backend, "configured": configured}
            if args.check:
                credentials["check"] = check_status
                if check_status == "failed":
                    return credentials, CampusError(
                        "credential-helper-failed",
                        "The credential helper failed its status check.",
                        "Check the configured helper and account, then retry.",
                        "error",
                    )
        else:
            try:
                backend, configured = keyring_status(config)
            except CampusError as error:
                return {"provider": "keyring", "configured": None}, error
            credentials = {"provider": "keyring", "backend": backend, "configured": configured}
            if args.check and not configured:
                return credentials, CampusError(
                    "credentials-not-configured",
                    "No saved credentials are available for this account.",
                    "Run 'campusctl auth set' to save credentials.",
                    "user-action",
                )
        return credentials, None
    if args.command == "setup":
        from campusctl.setup import run_setup

        path = config_path()
        config = load_config(path) if path.exists() else None
        interactive = bool(getattr(args, "_interactive", False))
        return run_setup(
            config,
            confirm=_confirm_setup if interactive else None,
            out=sys.stdout if getattr(args, "_output_mode", "json") == "human" else None,
        ), None
    if args.command == "sync":
        if args.only == "lectures":
            if args.headless:
                raise UsageError("Headless mode is not available for lecture sync.")
            import asyncio

            from campusctl.providers.cnu.sync import sync_lectures

            result, errors = asyncio.run(sync_lectures(load_config(), data_dir(), args.course))
            return result, errors or None
        module = _DOMAIN_MODULES.get(args.only)
        if module is None:
            raise CampusError(
                "unsupported-domain",
                "That sync domain is not supported in v0.",
                "Use 'campusctl sync --only lectures'.",
            )
        import asyncio

        result, errors = asyncio.run(module.sync(load_config(), data_dir(), args.course, headless=args.headless))
        return result, errors or None
    if args.command in _DOMAIN_MODULES:
        return _DOMAIN_MODULES[args.command].dispatch(args)
    if args.command == "courses" and args.courses_command == "list":
        catalog = read_catalog()
        return {
            "cache": {"generated_at": catalog["generated_at"], "path_present": True},
            "courses": catalog["courses"],
        }, None
    if args.command == "lectures" and args.lectures_command == "list":
        catalog = read_catalog()
        course_order: dict[str, int] = {}
        for index, course in enumerate(catalog["courses"]):
            if isinstance(course, dict) and isinstance(course.get("course_id"), str):
                course_order.setdefault(course["course_id"], index)
        lectures = catalog["lectures"]
        if args.course is not None:
            lectures = [lecture for lecture in lectures if _lecture_course_id(lecture) == args.course]
        if not args.all:
            lectures = [lecture for lecture in lectures if lecture.get("completion") == "incomplete"]
        lectures = sorted(
            lectures,
            key=lambda lecture: (
                course_order.get(_lecture_course_id(lecture), len(course_order)),
                _natural_sort_key(lecture.get("week")),
                _natural_sort_key(lecture.get("sequence")),
            ),
        )
        return {
            "cache": {"generated_at": catalog["generated_at"], "path_present": True},
            "lectures": lectures,
        }, None
    if args.command == "lectures" and args.lectures_command == "play":
        import asyncio

        from campusctl.browser import open_session
        from campusctl.providers.cnu.player import play_lectures, validate_requested_lectures

        catalog = read_catalog()
        lectures = validate_requested_lectures(args.entity_ids, catalog["lectures"])
        config = load_config()

        output_mode = getattr(args, "_output_mode", "json")
        progress_callback = None
        finish_progress = None
        if output_mode == "human":
            progress_callback, finish_progress = _progress_output()

        async def _play() -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
            try:
                async with open_session(config, data_dir=data_dir()) as session:
                    return await play_lectures(
                        session.page,
                        config,
                        lectures,
                        speed=args.speed,
                        progress=progress_callback,
                    )
            finally:
                if finish_progress is not None:
                    with suppress(Exception):
                        finish_progress()

        return asyncio.run(_play())
    raise UsageError("a command is required")


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    output_mode = "json"
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
        args._interactive = interactive
        args._output_mode = output_mode
        result, error = _dispatch(args)
        command_key = _command_key(args)
        if isinstance(error, list):
            return _emit_partial(result, error, output_mode=output_mode, command_key=command_key)
        return _emit_response(command_key, output_mode, result=result, error=error)
    except KeyboardInterrupt:
        if output_mode == "json":
            raise
        sys.stdout.write("Interrupted.\n")
        return 130
    except _HelpRequested:
        return 0
    except CampusError as error:
        return _emit_response(_command_key(args), output_mode, error=error)
    except Exception as error:
        # Never include exception text: third-party exceptions can contain credentials or endpoints.
        safe_error = CampusError("internal", type(error).__name__, None, "error")
        return _emit_response(_command_key(args), output_mode, error=safe_error)


if __name__ == "__main__":
    raise SystemExit(main())
