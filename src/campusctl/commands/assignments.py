"""Assignment metadata listing and selected-detail source packages."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog_view import cache_metadata
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.commands.course_inputs import list_refresh_scope, list_with_refresh
from campusctl.commands.fetch_queue import Capture, FetchDomain, dispatch_fetch, fetch_single, run_fetch_queue
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir

CAPABILITY = {"commands": ["list", "fetch"]}

_DOMAIN = FetchDomain(
    name="assignments",
    label="Assignments",
    kind="assignment",
    timeout_steps=frozenset(
        {
            "settling CNU course requests",
            "opening a CNU course",
            "waiting for the CNU course menu",
            "opening the CNU course section",
            "waiting for CNU task rows",
            "checking the selected CNU task course",
            "binding the selected task row",
            "checking the selected task ID",
            "opening the selected CNU task",
            "waiting for CNU task detail",
            "finishing CNU task detail",
            "verifying CNU task detail identity",
            "waiting for selected CNU task content",
            "reading selected CNU task brief",
        }
    ),
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    assignments = subparsers.add_parser("assignments", help="list or fetch assignment details")
    assignments.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope"
    )
    commands = assignments.add_subparsers(dest="assignments_command")
    listing = commands.add_parser("list", help="list cached assignments")
    listing.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope"
    )
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true", help="sync assignments before listing")
    fetch = commands.add_parser("fetch", help="fetch selected assignment details")
    fetch.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope")
    fetch.add_argument("entity_ids", nargs="+", metavar="ID", help="full assignment IDs from the list")
    fetch.add_argument("--out", type=Path, metavar="DIR", help="new package directory")


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    return list_refresh_scope(args, ("assignments",))


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    # Resolve the data directory first, as before the unified pipeline: it outranks input checks.
    data_dir()
    command = getattr(args, "assignments_command", None)
    if command == "list":
        return list_with_refresh("assignments", args, bindings, context, _list)
    if command == "fetch":
        return dispatch_fetch(args, _DOMAIN, fetch_one=_fetch_assignment, fetch_many=_fetch_assignment_queue)
    raise UsageError("an assignments command is required")


def _list(course_id: str | None) -> CommandResult:
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", data_dir()))
    rows = catalog["assignments"]
    if course_id is not None:
        rows = [row for row in rows if row["course"]["id"] == course_id]
    return command_result(
        {
            "cache": {**cache_metadata(catalog, now=datetime.now(UTC), domain="assignments"), "path_present": True},
            "assignments": rows,
        }
    )


def _capture(config: dict[str, Any]) -> Capture:
    async def capture(page: Any, row: dict[str, Any]) -> Any:
        from campusctl.providers.cnu.assignment_detail import capture_assignment_detail

        return await capture_assignment_detail(page, config, row)

    return capture


async def _fetch_assignment(
    config: dict[str, Any],
    root: Path,
    row: dict[str, Any],
    *,
    out: Path | None = None,
    headless: bool = False,
) -> dict[str, Any]:
    return await fetch_single(config, root, row, out=out, headless=headless, domain=_DOMAIN, capture=_capture(config))


async def _fetch_assignment_queue(
    config: dict[str, Any],
    root: Path,
    rows: list[dict[str, Any]],
    *,
    headless: bool,
) -> tuple[list[dict[str, Any]], list[CampusError] | None]:
    return await run_fetch_queue(config, root, rows, headless=headless, domain=_DOMAIN, capture=_capture(config))


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    from campusctl.providers.cnu.assignments import sync_assignments

    return await sync_assignments(config, root, course_id, headless=headless)


def _updated(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _package_lines(package: dict[str, Any]) -> list[str]:
    lines = [
        f"Assignment source: {package['entity_id']}",
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
            lines.append(f"Assignment source: {item.get('entity_id', '')}")
        lines.append(f"Outcome: {item.get('outcome', 'failed')}")
        reason = item.get("reason_code")
        if isinstance(reason, str) and reason:
            lines.append(f"Reason: {reason}")
    return lines


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    del width  # IDs are deliberately not truncated or split, including on narrow terminals.
    if command == "sync.assignments":
        if "courses" not in result:
            return []
        courses = result["courses"]
        assignments = result.get("assignments", 0)
        return [
            f"Synced {courses} {'course' if courses == 1 else 'courses'}, "
            f"{assignments} {'assignment' if assignments == 1 else 'assignments'}."
        ]
    if command == "assignments.fetch":
        items = result.get("items")
        if isinstance(items, list):
            return _render_items(items)
        package = result.get("source_package")
        if not package:
            return []
        return _package_lines(package)
    if command != "assignments.list" or "assignments" not in result:
        return []
    rows = result["assignments"]
    cache = result.get("cache", {})
    updated = _updated(cache.get("generated_at"))
    header = f"{len(rows)} {'assignment' if len(rows) == 1 else 'assignments'}"
    lines = [f"{header} (updated {updated})" if updated else header]
    course_id: str | None = None
    for row in rows:
        course = row["course"]
        if course["id"] != course_id:
            course_id = course["id"]
            lines.extend(["", course["label"]])
        status = "Submitted" if row["is_submitted"] else "Not submitted"
        due = row["due_date"] if row["due_date"] is not None else "—"
        lines.extend([f"  {row['title']} — {status} — Due: {due}", f"    {row['entity_id']}"])
    if not rows:
        lines.extend(["", "No cached assignments match this list."])
    if cache.get("enrollment_state") == "unknown":
        lines.extend(["", "Warning: assignment cache is stale; enrollment is unknown."])
    for failure in cache.get("failed_courses", []):
        lines.append(
            f"Warning: assignment cache is stale for {failure['label']} ({failure['course_id']}): {failure['reason']}."
        )
    return lines
