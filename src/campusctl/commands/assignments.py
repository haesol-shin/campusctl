"""Assignment metadata listing and selected-detail source packages."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog_view import cache_metadata
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir

CAPABILITY = {"commands": ["list", "fetch"]}

_DETAIL_TIMEOUT_STEPS = frozenset(
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
)


def _fetch_error(step: str, error: Exception) -> CampusError:
    code = error.code if isinstance(error, CampusError) else "fetch-failed"
    status = error.status if isinstance(error, CampusError) else "error"
    if step == "detail capture" and isinstance(error, CampusError) and code == "browser-timeout":
        detail_step = error.message.removeprefix("Timed out while ").removesuffix(".")
        if error.message == f"Timed out while {detail_step}." and detail_step in _DETAIL_TIMEOUT_STEPS:
            return CampusError(
                code, f"Assignments fetch: {detail_step} timed out.", "Check the browser and retry.", status
            )
    remediation = {
        "catalog load": "Sync assignments again and retry.",
        "configuration load": "Check campusctl configuration and retry.",
        "authentication": "Sign in and retry.",
        "detail capture": "Sync assignments again and select the current full ID.",
        "package creation": "Retry the fetch and inspect unsupported resources.",
    }.get(step, "Check the browser session and retry.")
    return CampusError(code, f"Assignments fetch: {step} failed.", remediation, status)


def _selected_ids(args: argparse.Namespace) -> list[str]:
    raw = getattr(args, "entity_ids", None)
    if raw is None:
        single = getattr(args, "entity_id", None)
        raw = None if single is None else [single]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not item.strip() for item in raw):
        raise UsageError("Assignments fetch selection: an entity ID is required")
    ordered: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _matching_rows(catalog: dict[str, Any], entity_ids: list[str]) -> list[dict[str, Any]]:
    records = catalog.get("assignments", [])
    rows: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        matching = [row for row in records if row.get("entity_id") == entity_id]
        if len(matching) != 1:
            raise CampusError(
                "entity-unknown",
                "Assignments fetch selection: selected ID is not in the catalog.",
                "Select one full ID from 'campusctl assignments list'.",
                "user-action",
            )
        rows.append(matching[0])
    return rows


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


def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    command = getattr(args, "assignments_command", None)
    if command == "list":
        catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", data_dir()))
        rows = catalog["assignments"]
        if args.course is not None:
            rows = [row for row in rows if row["course"]["id"] == args.course]
        return {
            "cache": {**cache_metadata(catalog, now=datetime.now(UTC), domain="assignments"), "path_present": True},
            "assignments": rows,
        }, None
    if command == "fetch":
        return _dispatch_fetch(args)
    raise UsageError("an assignments command is required")


def _dispatch_fetch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    entity_ids = _selected_ids(args)
    root = data_dir()
    cat_path = domain_catalog_path("assignments", root)
    if not cat_path.exists():
        raise CampusError(
            "catalog-missing",
            "Assignments fetch catalog lookup: catalog is missing.",
            "Run 'campusctl sync --only assignments' to create it.",
            "user-action",
        )
    try:
        catalog = read_domain_catalog("assignments", cat_path)
    except Exception as exc:
        raise _fetch_error("catalog load", exc) from exc
    rows = _matching_rows(catalog, entity_ids)
    out_path = getattr(args, "out", None)
    if out_path is not None and len(rows) > 1:
        raise UsageError("Assignments fetch selection: --out applies to one selected ID.")
    if out_path is not None:
        destination = Path(out_path)
        if destination.exists() or destination.is_symlink():
            raise CampusError(
                "output-path-conflict",
                "Assignments fetch output selection: output path already exists.",
                "Choose a nonexistent destination path with --out.",
                "user-action",
            )
    import asyncio

    from campusctl.browser_options import preflight_browser_mode
    from campusctl.config import load_config

    try:
        config = load_config()
    except Exception as exc:
        raise _fetch_error("configuration load", exc) from exc
    headless_override = getattr(args, "headless_override", None)
    try:
        mode = preflight_browser_mode(config, "assignments.fetch", override=headless_override)
    except CampusError as exc:
        raise CampusError(
            exc.code, f"Assignments fetch browser preflight: {exc.message}", exc.remediation, exc.status
        ) from exc
    if len(rows) == 1:
        package = asyncio.run(_fetch_assignment(config, root, rows[0], out=out_path, headless=mode))
        errors = (
            [
                CampusError(
                    "resource-omitted",
                    "Assignments fetch packaging: some resources are unsupported.",
                    status="user-action",
                )
            ]
            if package.get("completeness") == "partial"
            else None
        )
        return {"source_package": package}, errors
    items, errors = asyncio.run(_fetch_assignment_queue(config, root, rows, headless=mode))
    return {"items": items}, errors


async def _fetch_assignment(
    config: dict[str, Any],
    root: Path,
    row: dict[str, Any],
    *,
    out: Path | None = None,
    headless: bool = False,
) -> dict[str, Any]:
    from campusctl.browser import open_session, settle_sso_popups
    from campusctl.providers.cnu.assignment_detail import capture_assignment_detail
    from campusctl.providers.cnu.login import ensure_logged_in
    from campusctl.source_package import build_source_package

    try:
        async with open_session(config, data_dir=root, headless=headless, operation="assignments.fetch") as session:
            page = session.page
            try:
                await ensure_logged_in(page, config)
            except Exception as exc:
                raise _fetch_error("authentication", exc) from exc
            try:
                await settle_sso_popups(session, domain="assignments")
            except Exception as exc:
                raise _fetch_error("SSO settlement", exc) from exc
            try:
                snapshot = await capture_assignment_detail(page, config, row)
            except Exception as exc:
                raise _fetch_error("detail capture", exc) from exc
            try:
                return await build_source_package(
                    page,
                    snapshot,
                    entity_id=row["entity_id"],
                    kind="assignment",
                    course_id=row["course"]["id"],
                    course_label=row["course"]["label"],
                    root=root,
                    out=out,
                )
            except Exception as exc:
                raise _fetch_error("package creation", exc) from exc
    except Exception as exc:
        if isinstance(exc, CampusError) and exc.message.startswith("Assignments fetch:"):
            raise
        raise _fetch_error("browser session", exc) from exc


async def _fetch_assignment_queue(
    config: dict[str, Any],
    root: Path,
    rows: list[dict[str, Any]],
    *,
    headless: bool,
) -> tuple[list[dict[str, Any]], list[CampusError] | None]:
    from campusctl.commands.fetch_queue import run_fetch_queue
    from campusctl.providers.cnu.assignment_detail import capture_assignment_detail

    async def capture(page: Any, row: dict[str, Any]) -> Any:
        return await capture_assignment_detail(page, config, row)

    return await run_fetch_queue(
        config,
        root,
        rows,
        headless=headless,
        operation="assignments.fetch",
        domain="assignments",
        kind="assignment",
        label="Assignments",
        capture=capture,
        fetch_error=_fetch_error,
    )


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
