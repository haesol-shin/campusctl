"""Cached notices and selected-detail source packages."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog_view import cache_metadata
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir

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

_DETAIL_TIMEOUT_STEPS = frozenset(
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
)


def _fetch_error(step: str, error: Exception) -> CampusError:
    code = error.code if isinstance(error, CampusError) else "fetch-failed"
    status = error.status if isinstance(error, CampusError) else "error"
    if step == "detail capture" and isinstance(error, CampusError) and code == "browser-timeout":
        detail_step = error.message.removeprefix("Timed out while ").removesuffix(".")
        if error.message == f"Timed out while {detail_step}." and detail_step in _DETAIL_TIMEOUT_STEPS:
            label = "opening course notice board" if detail_step == "opening notice board" else detail_step
            return CampusError(code, f"Notices fetch: {label} timed out.", "Check the browser and retry.", status)
    if step == "detail capture" and isinstance(error, CampusError) and code == "entity-unknown":
        from campusctl.providers.cnu.notice_detail import NoticeDetailError

        if isinstance(error, NoticeDetailError):
            return CampusError(code, f"Notices fetch: detail capture: {error.message}", error.remediation, status)
    remediation = {
        "catalog load": "Sync notices again and retry.",
        "configuration load": "Check campusctl configuration and retry.",
        "authentication": "Sign in and retry.",
        "detail capture": "Sync notices again and select the current full ID.",
        "package creation": "Retry the fetch and inspect unsupported resources.",
    }.get(step, "Check the browser session and retry.")
    return CampusError(code, f"Notices fetch: {step} failed.", remediation, status)


def _selected_ids(args: argparse.Namespace) -> list[str]:
    raw = getattr(args, "entity_ids", None)
    if raw is None:
        single = getattr(args, "entity_id", None)
        raw = None if single is None else [single]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not item.strip() for item in raw):
        raise UsageError("Notices fetch selection: an entity ID is required")
    ordered: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _matching_rows(catalog: dict[str, Any], entity_ids: list[str]) -> list[dict[str, Any]]:
    records = catalog.get("notices", [])
    rows: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        matching = [row for row in records if row.get("entity_id") == entity_id]
        if len(matching) != 1:
            raise CampusError(
                "entity-unknown",
                "Notices fetch selection: selected ID is not in the catalog.",
                "Select one full ID from 'campusctl notices list'.",
                "user-action",
            )
        rows.append(matching[0])
    return rows


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Install notice listing and selected-detail fetch parsers."""
    from campusctl.cli import EnvelopeArgumentParser

    domain = subparsers.add_parser("notices", help="inspect cached LMS notices")
    domain.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    commands = domain.add_subparsers(dest="notices_command", parser_class=EnvelopeArgumentParser)
    listing = commands.add_parser("list", help="list cached notices")
    listing.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true")
    fetch = commands.add_parser("fetch", help="package selected notice details")
    fetch.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    fetch.add_argument("entity_ids", nargs="+", metavar="ENTITY_ID", help="full IDs from notices list")
    fetch.add_argument("--out", type=Path, metavar="DIR", help="new package directory")


def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    """Dispatch cached notice listing or selected detail packages."""
    command = getattr(args, "notices_command", None)
    if command == "list":
        catalog = read_domain_catalog("notices", domain_catalog_path("notices", data_dir()))
        rows = catalog["notices"]
        if args.course is not None:
            rows = [row for row in rows if row["course"]["id"] == args.course]
        return {
            "cache": {**cache_metadata(catalog, now=datetime.now(UTC), domain="notices"), "path_present": True},
            "notices": [{key: value for key, value in row.items() if key in _PUBLIC_ROW_KEYS} for row in rows],
        }, None
    if command == "fetch":
        return _dispatch_fetch(args)
    raise UsageError("A notices command is required.")


def _dispatch_fetch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    entity_ids = _selected_ids(args)
    root = data_dir()
    cat_path = domain_catalog_path("notices", root)
    if not cat_path.exists():
        raise CampusError(
            "catalog-missing",
            "Notices fetch catalog lookup: catalog is missing.",
            "Run 'campusctl sync --only notices' to create it.",
            "user-action",
        )
    try:
        catalog = read_domain_catalog("notices", cat_path)
    except Exception as exc:
        raise _fetch_error("catalog load", exc) from exc
    rows = _matching_rows(catalog, entity_ids)
    out_path = getattr(args, "out", None)
    if out_path is not None and len(rows) > 1:
        raise UsageError("Notices fetch selection: --out applies to one selected ID.")
    if out_path is not None:
        destination = Path(out_path)
        if destination.exists() or destination.is_symlink():
            raise CampusError(
                "output-path-conflict",
                "Notices fetch output selection: output path already exists.",
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
        mode = preflight_browser_mode(config, "notices.fetch", override=headless_override)
    except CampusError as exc:
        raise CampusError(
            exc.code, f"Notices fetch browser preflight: {exc.message}", exc.remediation, exc.status
        ) from exc
    if len(rows) == 1:
        package = asyncio.run(_fetch_notice(config, root, rows[0], out=out_path, headless=mode))
        errors = (
            [
                CampusError(
                    "resource-omitted", "Notices fetch packaging: some resources are unsupported.", status="user-action"
                )
            ]
            if package.get("completeness") == "partial"
            else None
        )
        return {"source_package": package}, errors
    items, errors = asyncio.run(_fetch_notice_queue(config, root, rows, headless=mode))
    return {"items": items}, errors


async def _fetch_notice(
    config: dict[str, Any],
    root: Path,
    row: dict[str, Any],
    *,
    out: Path | None = None,
    headless: bool = False,
) -> dict[str, Any]:
    from campusctl.browser import open_session, settle_sso_popups
    from campusctl.providers.cnu.login import ensure_logged_in
    from campusctl.providers.cnu.notice_detail import capture_notice_detail
    from campusctl.source_package import build_source_package

    try:
        async with open_session(config, data_dir=root, headless=headless, operation="notices.fetch") as session:
            page = session.page
            try:
                await ensure_logged_in(page, config)
            except Exception as exc:
                raise _fetch_error("authentication", exc) from exc
            try:
                await settle_sso_popups(session, domain="notices")
            except Exception as exc:
                raise _fetch_error("SSO settlement", exc) from exc
            try:
                snapshot = await capture_notice_detail(page, row)
            except Exception as exc:
                raise _fetch_error("detail capture", exc) from exc
            try:
                return await build_source_package(
                    page,
                    snapshot,
                    entity_id=row["entity_id"],
                    kind="notice",
                    course_id=row["course"]["id"],
                    course_label=row["course"]["label"],
                    root=root,
                    out=out,
                )
            except Exception as exc:
                raise _fetch_error("package creation", exc) from exc
    except Exception as exc:
        if isinstance(exc, CampusError) and exc.message.startswith("Notices fetch:"):
            raise
        raise _fetch_error("browser session", exc) from exc


async def _fetch_notice_queue(
    config: dict[str, Any],
    root: Path,
    rows: list[dict[str, Any]],
    *,
    headless: bool,
) -> tuple[list[dict[str, Any]], list[CampusError] | None]:
    from campusctl.commands.fetch_queue import run_fetch_queue
    from campusctl.providers.cnu.notice_detail import capture_notice_detail

    async def capture(page: Any, row: dict[str, Any]) -> Any:
        return await capture_notice_detail(page, row)

    return await run_fetch_queue(
        config,
        root,
        rows,
        headless=headless,
        operation="notices.fetch",
        domain="notices",
        kind="notice",
        label="Notices",
        capture=capture,
        fetch_error=_fetch_error,
    )


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    from campusctl.providers.cnu.notices import sync_notices

    return await sync_notices(config, root, course_id, headless=headless)


def _wrap(message: str, width: int, *, indent: str = "") -> list[str]:
    # Presentation imports command modules during discovery, so import its
    # cell-aware helpers only when a command is actually rendered.
    from campusctl.presentation import _cells
    from campusctl.presentation import _wrap as wrap_cells

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
