"""Cached/network course selection and refresh policy shared by commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import course_roster, read_course_snapshot
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.config import load_config
from campusctl.course_selection import resolve_course
from campusctl.envelope import CampusError, error_item
from campusctl.paths import data_dir
from campusctl.profiling import SpanRecorder
from campusctl.sync import run_sync


def select_course(
    selector: str | None,
    root: Path,
    *,
    ids_only: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    if selector is None:
        return None, None
    roster = course_roster(root)
    snapshot = None
    if selector.isdecimal() and not ids_only and not any(row["course_id"] == selector for row in roster["courses"]):
        snapshot = read_course_snapshot(root)
    return resolve_course(selector, roster, ids_only=ids_only, printed_roster=snapshot), snapshot


def discover_live_course(
    selector: str,
    config: dict[str, Any],
    root: Path,
    domain: str,
    *,
    ids_only: bool,
    mode: bool,
    discover_courses: Callable[[Any, dict[str, Any]], Any],
) -> str:
    import asyncio

    from campusctl.browser import open_session

    async def discover() -> list[dict[str, Any]]:
        async with open_session(config, data_dir=root, headless=mode, operation=f"{domain}.sync") as session:
            return await discover_courses(session.page, config)

    selected = resolve_course(selector, asyncio.run(discover()), ids_only=ids_only)
    assert selected is not None
    return selected


def network_course(
    selector: str | None,
    root: Path,
    config: dict[str, Any],
    domain: str,
    *,
    ids_only: bool,
    mode: bool,
    discover_courses: Callable[[Any, dict[str, Any]], Any],
) -> tuple[str | None, dict[str, Any] | None]:
    if selector is None:
        return None, None
    try:
        return select_course(selector, root, ids_only=ids_only)
    except CampusError as error:
        if error.code not in {"catalog-missing", "course-id-required", "course-index-unavailable", "course-not-found"}:
            raise
        try:
            cached = course_roster(root)
        except CampusError as roster_error:
            if roster_error.code != "catalog-missing":
                raise
            cached = None
        if cached is None and not ids_only and selector.isdecimal():
            read_course_snapshot(root)
        if cached is not None and ids_only:
            if selector.isdecimal():
                raise error
            fragment = "".join(selector.split()).casefold()
            if any(fragment in "".join(row["label"].split()).casefold() for row in cached["courses"]):
                raise error
        return (
            discover_live_course(
                selector, config, root, domain, ids_only=ids_only, mode=mode, discover_courses=discover_courses
            ),
            None,
        )


def refresh(
    domain: str,
    root: Path,
    course_id: str | None,
    args: argparse.Namespace,
    snapshot: dict[str, Any] | None = None,
    *,
    profile: SpanRecorder | None = None,
) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    config = load_config()
    mode = preflight_browser_mode(config, f"{domain}.sync", override=args.headless_override)
    return run_sync(
        config,
        root,
        (domain,),
        course_id,
        headless=mode,
        profile=profile,
        course_snapshot=snapshot,
    )


def refresh_result(
    refresh: tuple[dict[str, Any], CampusError | list[CampusError] | None],
) -> dict[str, Any]:
    result, errors = refresh
    failures = errors if isinstance(errors, list) else [errors] if errors else []
    status = "partial" if result and failures else failures[0].status if failures else "ok"
    return {"status": status, "result": result, "errors": [error_item(error) for error in failures]}


def list_refresh_scope(args: argparse.Namespace, scope: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return the profile scope when this command is `<command> list --refresh`."""
    if getattr(args, f"{args.command}_command", None) == "list" and args.refresh:
        return scope
    return None


def list_with_refresh(
    domain: str,
    args: argparse.Namespace,
    bindings: CommandBindings,
    context: CommandContext,
    read: Callable[[str | None], CommandResult],
) -> CommandResult:
    """Resolve the course, optionally refresh the domain catalog, then list it with `read`.

    A failed read still reports a refresh that failed first.
    """
    root = data_dir()
    ids_only = context.output_mode == "json"
    if args.refresh:
        config = load_config()
        mode = preflight_browser_mode(config, f"{domain}.sync", override=args.headless_override)
        course, snapshot = network_course(
            args.course,
            root,
            config,
            domain,
            ids_only=ids_only,
            mode=mode,
            discover_courses=bindings.discover_courses,
        )
    else:
        course, snapshot = select_course(args.course, root, ids_only=ids_only)
    refreshed = refresh(domain, root, course, args, snapshot, profile=context.profile) if args.refresh else None
    try:
        listed = read(course)
    except CampusError:
        if refreshed is None or not refreshed[1]:
            raise
        return command_result({"refresh": refresh_result(refreshed)}, refreshed[1])
    if refreshed is None:
        return listed
    listed.result["refresh"] = refresh_result(refreshed)
    return command_result(listed.result, refreshed[1], publish=listed.publish)
