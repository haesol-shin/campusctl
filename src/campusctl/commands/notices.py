"""Cached notices and selected-detail source packages."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog_view import cache_metadata
from campusctl.cli_args import EnvelopeArgumentParser
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.commands.course_inputs import list_refresh_scope, list_with_refresh
from campusctl.commands.fetch_queue import FetchDomain, dispatch_fetch, fetch_single, run_fetch_queue
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir
from campusctl.text_layout import _cells
from campusctl.text_layout import _wrap as wrap_cells

_PUBLIC_ROW_KEYS = frozenset(
    {
        "entity_id",
        "legacy_key",
        "course",
        "kind",
        "title",
        "date",
        "status",
        "is_unread",
        "posted_date",
        "author_role",
        "author",
        "view_count",
        "has_attachments",
    }
)


CAPABILITY: dict[str, Any] = {"commands": ["list", "fetch"]}


def _adapt_entity_unknown(error: CampusError) -> CampusError | None:
    from campusctl.providers.cnu.notice_detail import NoticeDetailError

    if isinstance(error, NoticeDetailError):
        return CampusError(
            error.code, f"Notices fetch: detail capture: {error.message}", error.remediation, error.status
        )
    return None


_DOMAIN = FetchDomain(
    name="notices",
    label="Notices",
    kind="notice",
    timeout_steps=frozenset(
        {
            "finishing notice response",
            "reading notice response",
            "opening notice course roster",
            "opening selected notice course",
            "waiting for notice menu",
            "opening notice board",
            "settling notice board",
            "reading board links",
            "opening selected notice",
            "settling notice detail",
            "reading notice detail",
        }
    ),
    timeout_wording={"opening notice board": "opening course notice board"},
    entity_unknown=_adapt_entity_unknown,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Install notice listing and selected-detail fetch parsers."""
    domain = subparsers.add_parser("notices", help="list or fetch notice details")
    domain.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope"
    )
    commands = domain.add_subparsers(dest="notices_command", parser_class=EnvelopeArgumentParser)
    listing = commands.add_parser("list", help="list cached notices")
    listing.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope"
    )
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true", help="sync notices before listing")
    fetch = commands.add_parser("fetch", help="fetch selected notice details")
    fetch.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope")
    fetch.add_argument("entity_ids", nargs="+", metavar="ID", help="full notice IDs from the list")
    fetch.add_argument("--out", type=Path, metavar="DIR", help="new package directory")


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    return list_refresh_scope(args, ("notices",))


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    """Dispatch cached notice listing or selected detail packages."""
    # Resolve the data directory first, as before the unified pipeline: it outranks input checks.
    data_dir()
    command = getattr(args, "notices_command", None)
    if command == "list":
        return list_with_refresh("notices", args, bindings, context, _list)
    if command == "fetch":
        return dispatch_fetch(args, _DOMAIN, fetch_one=_fetch_notice, fetch_many=_fetch_notice_queue)
    raise UsageError("A notices command is required.")


def _list(course_id: str | None) -> CommandResult:
    catalog = read_domain_catalog("notices", domain_catalog_path("notices", data_dir()))
    rows = catalog["notices"]
    if course_id is not None:
        rows = [row for row in rows if row["course"]["id"] == course_id]
    return command_result(
        {
            "cache": {**cache_metadata(catalog, now=datetime.now(UTC), domain="notices"), "path_present": True},
            "notices": [{key: value for key, value in row.items() if key in _PUBLIC_ROW_KEYS} for row in rows],
        }
    )


async def _capture(page: Any, row: dict[str, Any]) -> Any:
    from campusctl.providers.cnu.notice_detail import capture_notice_detail

    return await capture_notice_detail(page, row)


async def _fetch_notice(
    config: dict[str, Any],
    root: Path,
    row: dict[str, Any],
    *,
    out: Path | None = None,
    headless: bool = False,
) -> dict[str, Any]:
    return await fetch_single(config, root, row, out=out, headless=headless, domain=_DOMAIN, capture=_capture)


async def _fetch_notice_queue(
    config: dict[str, Any],
    root: Path,
    rows: list[dict[str, Any]],
    *,
    headless: bool,
) -> tuple[list[dict[str, Any]], list[CampusError] | None]:
    return await run_fetch_queue(config, root, rows, headless=headless, domain=_DOMAIN, capture=_capture)


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    from campusctl.providers.cnu.notices import sync_notices

    return await sync_notices(config, root, course_id, headless=headless)


def _wrap(message: str, width: int, *, indent: str = "") -> list[str]:
    available = max(1, width - _cells(indent))
    return [f"{indent}{line}" for line in wrap_cells(message, available)]


def _stamp(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M UTC") if parsed.tzinfo else parsed.strftime("%Y-%m-%d %H:%M")


def _warnings(state: object, failed: object, width: int) -> list[str]:
    warnings: list[str] = []
    if state == "unknown":
        warnings.extend(_wrap("Warning: Enrollment unknown; all notices may be stale.", width))
    if isinstance(failed, list):
        for course in failed:
            if not isinstance(course, dict):
                continue
            label = course.get("label") or course.get("course_id") or "Unknown course"
            course_id = course.get("course_id") or "unknown"
            reason = course.get("reason") or "course-sync-failed"
            warnings.extend(_wrap(f"Warning: Notice cache stale for {label} ({course_id}): {reason}.", width))
    return warnings


def _package_lines(package: dict[str, Any]) -> list[str]:
    lines = [
        f"Notice source: {package['entity_id']}",
        f"Package: {package['path']}",
        f"Content: {package['content_path']}",
        f"Completeness: {package['completeness']}",
    ]
    for omitted in package.get("omitted_resources", []):
        reason = omitted.get("reason", "omitted")
        name = omitted.get("original_name") or omitted.get("resource_id", "resource")
        lines.append(f"Omitted: {reason} — {name}")
    return lines


def _render_items(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if lines:
            lines.append("")
        package = item.get("source_package")
        if isinstance(package, dict):
            lines.extend(_package_lines(package))
        else:
            lines.append(f"Notice source: {item.get('entity_id', '')}")
        lines.append(f"Outcome: {item.get('outcome', 'failed')}")
        reason = item.get("reason_code")
        if isinstance(reason, str) and reason:
            lines.append(f"Reason: {reason}")
    return lines


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    """Render domain summaries and lists, keeping selectable IDs untruncated."""
    if command == "notices.fetch":
        items = result.get("items")
        if isinstance(items, list):
            return _render_items(items)
        package = result.get("source_package")
        if not package:
            return []
        return _package_lines(package)
    if command == "sync.notices":
        if not any(key in result for key in ("courses", "notices", "failed_courses", "catalog")):
            return []
        courses = result.get("courses", 0)
        notices = result.get("notices", 0)
        lines = _wrap(
            f"Synced {courses} {'course' if courses == 1 else 'courses'}, "
            f"{notices} {'notice' if notices == 1 else 'notices'}.",
            width,
        )
        catalog = result.get("catalog")
        state = catalog.get("enrollment_state") if isinstance(catalog, dict) else None
        lines.extend(_warnings(state, result.get("failed_courses"), width))
        return lines
    if command != "notices.list" or not isinstance(result.get("notices"), list):
        return []
    rows = result["notices"]
    cache = result.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    count = len(rows)
    lines = _wrap(
        f"{count} {'notice' if count == 1 else 'notices'} (updated {_stamp(cache.get('generated_at'))})", width
    )
    lines.extend(_warnings(cache.get("enrollment_state"), cache.get("failed_courses"), width))
    if not rows:
        lines.extend(_wrap("No cached notices match this selection.", width))
        return lines
    last_course: object = None
    for row in rows:
        course = row.get("course") or {}
        course_id = course.get("id", "unknown")
        if course_id != last_course:
            lines.append("")
            lines.extend(_wrap(f"Course: {course.get('label', course_id)} ({course_id})", width))
            last_course = course_id
        unread = " (unread)" if row.get("is_unread") is True else " (read)" if row.get("is_unread") is False else ""
        status = f" — {row['status']}" if row.get("status") else ""
        lines.extend(
            _wrap(
                f"{row.get('title') or '(untitled notice)'} — {row.get('date') or 'unknown'}{status}{unread}",
                width,
                indent="  ",
            )
        )
        attachment = row.get("has_attachments")
        presence = "yes" if attachment is True else "no" if attachment is False else "unknown"
        views = row.get("view_count")
        lines.extend(
            _wrap(
                f"Author: {row.get('author') or 'unknown'} · Views: {views if type(views) is int else 'unknown'}"
                f" · Attachments: {presence}",
                width,
                indent="  ",
            )
        )
        lines.append(f"    {row['entity_id']}")
    return lines
