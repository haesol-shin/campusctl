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
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from campusctl import __version__
from campusctl.browser_options import preflight_browser_mode, resolve_headless
from campusctl.catalog import catalog_path, read_catalog
from campusctl.catalog_view import (
    DOMAINS,
    cache_metadata,
    catalog_snapshot,
    course_roster,
    publish_course_snapshot,
    publish_material_snapshot,
    read_course_snapshot,
)
from campusctl.commands import discover_domain_modules
from campusctl.config import load_config
from campusctl.config_init import run_config_init
from campusctl.course_selection import CourseAmbiguous, resolve_course
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
from campusctl.profiling import SpanRecorder
from campusctl.sync import parse_domains, run_sync

SUPPORTED_SPEEDS = [1.0, 1.25, 1.5]
_DOMAIN_MODULES: dict[str, ModuleType] = discover_domain_modules()
CAPABILITIES = {
    "sync": list(DOMAINS),
    "lectures": ["list", "play"],
    "status": ["local"],
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", dest="headless_override", action="store_const", const=True, default=None)
    mode.add_argument("--headed", dest="headless_override", action="store_const", const=False)
    parser.add_argument("--profile", action="store_true", help="profile sync or refresh on stderr")
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

    sync = commands.add_parser("sync", help="sync all metadata domains")
    _with_json(sync)
    sync.add_argument("--only", help="comma-separated domain subset")
    sync.add_argument("--course", help="limit sync to one course")

    status = commands.add_parser("status", help="summarize cached coursework")
    _with_json(status)
    status.add_argument("--course", help="select one course")
    courses = commands.add_parser("courses", help="list cached courses")
    _with_json(courses)
    course_commands = courses.add_subparsers(dest="courses_command", parser_class=EnvelopeArgumentParser)
    courses_list = course_commands.add_parser("list", help="list cached courses")
    _with_json(courses_list)
    courses_list.add_argument("--refresh", action="store_true")

    lectures = commands.add_parser("lectures", help="list or play lectures")
    _with_json(lectures)
    lecture_commands = lectures.add_subparsers(dest="lectures_command", parser_class=EnvelopeArgumentParser)
    lectures_list = lecture_commands.add_parser("list", help="list cached lectures")
    _with_json(lectures_list)
    lectures_list.add_argument("--all", action="store_true", help="include completed or recorded lectures")
    lectures_list.add_argument("--course", help="limit results to one course ID")
    lectures_list.add_argument("--refresh", action="store_true")
    lectures_play = lecture_commands.add_parser("play", help="play explicit lecture IDs")
    _with_json(lectures_play)
    lectures_play.add_argument("entity_ids", nargs="+")
    lectures_play.add_argument("--speed", type=float, choices=SUPPORTED_SPEEDS)
    lectures_play.add_argument("--replay", action="store_true", help="explicitly replay completed lectures")
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


def _select_course(
    selector: str | None,
    root: Path,
    *,
    ids_only: bool,
    allow_live_id: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    if selector is None:
        return None, None
    try:
        roster = course_roster(root)
    except CampusError as error:
        if error.code != "catalog-missing":
            raise
        if allow_live_id and ids_only and selector.strip():
            return selector, None
        if ids_only:
            code = "course-index-unavailable" if selector.isdecimal() else "course-id-required"
            raise CampusError(
                code,
                "JSON course selection requires an exact full course ID.",
                "Use a full course ID from 'campusctl courses list --json'.",
                "user-action",
            ) from None
        if selector.isdecimal():
            read_course_snapshot(root)
        raise
    snapshot = None
    if selector.isdecimal() and not ids_only and not any(row["course_id"] == selector for row in roster["courses"]):
        snapshot = read_course_snapshot(root)
    return resolve_course(selector, roster, ids_only=ids_only, printed_roster=snapshot), snapshot


def _refresh(
    domain: str,
    root: Path,
    course_id: str | None,
    args: argparse.Namespace,
    snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    config = load_config()
    mode = preflight_browser_mode(config, f"{domain}.sync", override=args.headless_override)
    return run_sync(
        config, root, (domain,), course_id, headless=mode,
        profile=getattr(args, "_profile", None), course_snapshot=snapshot,
    )


def _command_key(args: argparse.Namespace | None) -> str:
    if args is None:
        return "usage"
    if args.version:
        return "version"
    command = getattr(args, "command", None)
    if command == "sync" and getattr(args, "only", None) is not None:
        try:
            domains = parse_domains(args.only)
        except CampusError:
            return "sync"
        return f"sync.{domains[0]}" if len(domains) == 1 else "sync"
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
    root = data_dir()
    ids_only = args._output_mode == "json"
    if args.command == "sync":
        domains = parse_domains(args.only)
        course_id, snapshot = _select_course(args.course, root, ids_only=ids_only, allow_live_id=True)
        config = load_config()
        for domain in domains:
            preflight_browser_mode(config, f"{domain}.sync", override=args.headless_override)
        return run_sync(
            config,
            root,
            domains,
            course_id,
            headless=args.headless_override,
            profile=getattr(args, "_profile", None),
            course_snapshot=snapshot,
        )
    if args.command == "courses" and args.courses_command == "list":
        refresh = None
        if args.refresh:
            refresh = _refresh("lectures", root, None, args)
        try:
            roster = course_roster(root)
        except CampusError:
            if refresh is None or not refresh[1]:
                raise
            errors = refresh[1] if isinstance(refresh[1], list) else [refresh[1]]
            return {
                "refresh": {"status": "error", "result": refresh[0], "errors": [error_item(item) for item in errors]}
            }, refresh[1]
        result = {"cache": roster["cache"], "courses": roster["courses"]}
        if refresh is not None:
            result["refresh"] = {
                "status": "partial" if refresh[1] else "ok",
                "result": refresh[0],
                "errors": [error_item(error) for error in refresh[1]]
                if isinstance(refresh[1], list)
                else [error_item(refresh[1])]
                if refresh[1]
                else [],
            }
            if refresh[1]:
                return result, refresh[1]
        if not ids_only:
            args._course_roster = roster
        return result, None
    if args.command == "status":
        from campusctl.status import build_status

        course_id, _ = _select_course(args.course, root, ids_only=ids_only)
        result, status, errors = build_status(root, course_id, now=datetime.now(UTC))
        args._status = status
        args._status_errors = errors
        return result, None
    if args.command in _DOMAIN_MODULES:
        if getattr(args, f"{args.command}_command", None) == "list":
            args.course, snapshot = _select_course(args.course, root, ids_only=ids_only)
            refresh = _refresh(args.command, root, args.course, args, snapshot) if args.refresh else None
            try:
                result, error = _DOMAIN_MODULES[args.command].dispatch(args)
            except CampusError:
                if refresh is None or not refresh[1]:
                    raise
                return {
                    "refresh": {
                        "status": "error",
                        "result": refresh[0],
                        "errors": [error_item(e) for e in refresh[1]]
                        if isinstance(refresh[1], list)
                        else [error_item(refresh[1])],
                    }
                }, refresh[1]
            if refresh is not None:
                result["refresh"] = {
                    "status": "partial" if refresh[1] else "ok",
                    "result": refresh[0],
                    "errors": [error_item(e) for e in refresh[1]]
                    if isinstance(refresh[1], list)
                    else [error_item(refresh[1])]
                    if refresh[1]
                    else [],
                }
                return result, refresh[1]
            return result, error
        return _DOMAIN_MODULES[args.command].dispatch(args)
    if args.command == "lectures" and args.lectures_command == "list":
        course_id, snapshot = _select_course(args.course, root, ids_only=ids_only)
        refresh = _refresh("lectures", root, course_id, args, snapshot) if args.refresh else None
        try:
            catalog = read_catalog()
        except CampusError:
            if refresh is None or not refresh[1]:
                raise
            errors = refresh[1] if isinstance(refresh[1], list) else [refresh[1]]
            return {
                "refresh": {"status": "error", "result": refresh[0], "errors": [error_item(item) for item in errors]}
            }, refresh[1]
        course_order = {course["course_id"]: index for index, course in enumerate(catalog["courses"])}
        lectures = catalog["lectures"]
        if course_id is not None:
            lectures = [lecture for lecture in lectures if _lecture_course_id(lecture) == course_id]
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
        result = {"cache": cache_metadata(catalog, now=datetime.now(UTC), domain="lectures"), "lectures": lectures}
        if refresh is not None:
            result["refresh"] = {
                "status": "partial" if refresh[1] else "ok",
                "result": refresh[0],
                "errors": [error_item(error) for error in refresh[1]]
                if isinstance(refresh[1], list)
                else [error_item(refresh[1])]
                if refresh[1]
                else [],
            }
            return result, refresh[1]
        return result, None
    if args.command == "lectures" and args.lectures_command == "play":
        import asyncio

        from campusctl.browser import open_session
        from campusctl.providers.cnu.player import play_lectures, validate_requested_lectures

        catalog = read_catalog()
        lectures = validate_requested_lectures(args.entity_ids, catalog["lectures"], replay=args.replay)
        config = load_config()
        output_mode = args._output_mode
        if args.replay and output_mode == "human":
            if not args._interactive:
                raise CampusError(
                    "replay-confirmation-required",
                    "Replay needs two interactive terminals.",
                    "Run replay in a terminal or use explicit IDs with --json.",
                    "user-action",
                )
            sys.stdout.write(f"Replay {len(lectures)} selected lectures?\n")
            for lecture in lectures:
                sys.stdout.write(f"  {lecture['entity_id']} — {lecture.get('title', '')}\n")
            sys.stdout.flush()
            try:
                consent = input("Replay using the official player? [y/N] ").strip().lower()
            except EOFError:
                consent = ""
            if consent not in {"y", "yes"}:
                raise CampusError("replay-cancelled", "Replay was cancelled.", "Select lectures again.", "user-action")
        mode = preflight_browser_mode(config, "lectures.play", override=args.headless_override)

        progress_callback = None
        finish_progress = None
        if output_mode == "human":
            progress_callback, finish_progress = _progress_output()

        async def _play() -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
            try:
                async with open_session(
                    config, data_dir=data_dir(), headless=mode, operation="lectures.play"
                ) as session:
                    return await play_lectures(
                        session.page,
                        config,
                        lectures,
                        speed=args.speed,
                        progress=progress_callback,
                        replay=args.replay,
                    )
            finally:
                if finish_progress is not None:
                    with suppress(Exception):
                        finish_progress()

        return asyncio.run(_play())


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
            refresh = args.command == "courses" and args.courses_command == "list" and args.refresh
            refresh |= args.command == "lectures" and args.lectures_command == "list" and args.refresh
            refresh |= (
                args.command in _DOMAIN_MODULES
                and getattr(args, f"{args.command}_command", None) == "list"
                and args.refresh
            )
            if args.command != "sync" and not refresh:
                raise UsageError("--profile is valid only for sync or list --refresh.")
            scope = (
                parse_domains(args.only)
                if args.command == "sync"
                else (("lectures",) if args.command == "courses" else (args.command,))
            )
            mode = resolve_headless(load_config(), args.headless_override)
            recorder = SpanRecorder(enabled=True, mode="headless" if mode else "headed", scope=scope)
        args._profile = recorder
        args._interactive = interactive
        args._output_mode = output_mode
        result, error = _dispatch(args)
        command_key = _command_key(args)
        if args.command == "status":
            envelope = make_envelope(status=args._status, result=result, errors=args._status_errors)
            if output_mode == "json":
                write_json(envelope)
            else:
                render_human(command_key, envelope, sys.stdout)
            code = EXIT_CODES[args._status]
        elif isinstance(error, list):
            code = _emit_partial(result, error, output_mode=output_mode, command_key=command_key)
        else:
            code = _emit_response(command_key, output_mode, result=result, error=error)
        if code == 0 and output_mode == "human" and command_key == "courses.list" and hasattr(args, "_course_roster"):
            sys.stdout.flush()
            publish_course_snapshot(data_dir(), args._course_roster)
        if code == 0 and output_mode == "human" and command_key == "materials.list":
            sys.stdout.flush()
            source = catalog_snapshot("materials", data_dir())
            if source is not None:
                publish_material_snapshot(
                    data_dir(), source[1], [row["entity_id"] for row in result["materials"]], course_id=args.course
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
