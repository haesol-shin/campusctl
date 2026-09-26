"""Cached notices and the reviewed notice-sync command surface."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog_view import cache_metadata
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir

_ORIGIN = "https://dcs-learning.cnu.ac.kr"
_OPERATION = "notices.sync"
_FETCH_OPERATION = "notices.fetch"
_GET_PATHS = (
    "/std/myLecture",
    "/std/lecture",
    "/std/notice",
    "/std/todo",
    "/properties/messages.properties",
    "/properties/messages_ko.properties",
)
_POST_PATHS = (
    "/api/v1/board/std/notice/list",
    "/api/v1/week/getStdTodoList",
    "/api/v1/course/getStdMyCourseList",
    "/api/v1/term/getYearTermList",
    "/api/v1/board/std/qna/list",
    "/api/v1/boardM/getBoardItemList",
    "/api/v1/common/checkEnableUrl",
    "/api/v1/course/addSessionCourseInfo",
    "/api/v1/board/courseNotice/list",
    "/api/v1/week/getStdWeekList",
    "/api/v1/week/getStdEtcList",
    "/api/v1/survey/getApplyPopList",
    "/api/v1/board/popup/noticeList",
    "/api/v1/board/notice/list/top",
    "/api/v1/board/notice/list",
    "/api/v1/user/getUserInfo",
    "/api/v1/user/getMenuList",
    "/api/v1/alarm/getAlarmListByDate",
    "/api/v1/course/get",
    "/api/v1/course/getCeShortcuts",
)
CAPABILITY: dict[str, Any] = {
    "commands": ["list"],
    "policy": {
        "approved": True,
        "read_only_evidence": "docs/specs/20-notices/design.md#request-and-course-binding",
        "origins": [_ORIGIN],
        "routes": [
            {
                "origin": _ORIGIN,
                "path": path,
                "operation": _OPERATION,
                "methods": ["GET"],
                **({"query": {"_": "cachebuster"}} if path.startswith("/properties/") else {}),
            }
            for path in _GET_PATHS
        ]
        + [{"origin": _ORIGIN, "path": path, "operation": _OPERATION, "methods": ["POST"]} for path in _POST_PATHS]
        + [
            {
                "origin": _ORIGIN,
                "path": "/api/v1/week/getStdActivityStatus",
                "operation": _OPERATION,
                "methods": ["POST"],
                "logging_token_reviewed": True,
                "resource_type": "xhr",
            }
        ],
        "allowed_media": [],
        "max_bytes": None,
        "suppress": [
            {
                "name": "panopto-script",
                "origin": _ORIGIN,
                "path_template": "/js/common/panopto-{hash}.js",
                "operation": _OPERATION,
                "methods": ["GET"],
                "reason": "media-integration",
            },
            {
                "name": "panopto-saml-script",
                "origin": _ORIGIN,
                "path_template": "/js/common/panoptoSaml-{hash}.js",
                "operation": _OPERATION,
                "methods": ["GET"],
                "reason": "media-integration",
            },
            {
                "name": "panopto-sso-popup",
                "origin": "https://cnu.ap.panopto.com",
                "path_template": "/Panopto/Pages/Auth/Login.aspx",
                "operation": _OPERATION,
                "methods": ["POST"],
                "reason": "panopto-sso-popup",
            },
            {
                "name": "course-roster-image",
                "origin": _ORIGIN,
                "path_template": "/upload/dunetadmin/college/{hash}.png",
                "operation": _OPERATION,
                "methods": ["GET"],
                "reason": "course-roster-image",
            },
            {
                "name": "favicon-icon",
                "origin": _ORIGIN,
                "path_template": "/assets/images/favicon-{hash}.ico",
                "operation": _OPERATION,
                "methods": ["GET"],
                "reason": "favicon",
            },
            {
                "name": "external-telemetry",
                "origin": "http://0.0.0.0:3000",
                "path_template": "/v1/events",
                "operation": _OPERATION,
                "methods": ["POST"],
                "reason": "telemetry",
            },
            {
                "name": "panopto-disconnection-log",
                "origin": _ORIGIN,
                "path_template": "/api/v1/panopto/addInternetDisconnectionLog",
                "operation": _OPERATION,
                "methods": ["POST"],
                "reason": "logging",
            },
            {
                "name": "panopto-connectivity-check",
                "origin": _ORIGIN,
                "path_template": "/api/v1/panopto/checkInternetConnection",
                "operation": _OPERATION,
                "methods": ["GET"],
                "reason": "logging",
            },
        ],
        "static_asset_origins": [_ORIGIN],
        "static_resource_types": ["script", "stylesheet", "font", "image"],
        "selected_file_routes": [],
    },
}
FETCH_POLICY: dict[str, Any] = {
    "approved": True,
    "read_only_evidence": "2026-09-25 sanitized LMS report §§1.6,2,3.1 and owner LMS pins decision",
    "origins": [_ORIGIN],
    "routes": [
        {"origin": _ORIGIN, "path": "/std/myLecture", "operation": _FETCH_OPERATION, "methods": ["GET"]},
        {"origin": _ORIGIN, "path": "/std/lecture", "operation": _FETCH_OPERATION, "methods": ["GET"]},
        {"origin": _ORIGIN, "path": "/std/notice", "operation": _FETCH_OPERATION, "methods": ["GET"]},
        {
            "origin": _ORIGIN,
            "path": "/std/noticeDetail",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "query": {"no": "board-item-id", "curPage": "page"},
        },
        {
            "origin": _ORIGIN,
            "path": "/properties/messages.properties",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "query": {"_": "cachebuster"},
        },
        {
            "origin": _ORIGIN,
            "path": "/properties/messages_ko.properties",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "query": {"_": "cachebuster"},
        },
        {
            "origin": _ORIGIN,
            "path": "/api/v1/course/addSessionCourseInfo",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _ORIGIN, "path": "/api/v1/user/getUserInfo", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _ORIGIN, "path": "/api/v1/user/getMenuList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {
            "origin": _ORIGIN,
            "path": "/api/v1/alarm/getAlarmListByDate",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {
            "origin": _ORIGIN,
            "path": "/api/v1/course/getCeShortcuts",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {
            "origin": _ORIGIN,
            "path": "/api/v1/common/checkEnableUrl",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {
            "origin": _ORIGIN,
            "path": "/api/v1/boardM/getBoardItemList",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _ORIGIN, "path": "/api/v1/term/getYearTermList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {
            "origin": _ORIGIN,
            "path": "/api/v1/course/getStdMyCourseList",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _ORIGIN, "path": "/api/v1/course/get", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {
            "origin": _ORIGIN,
            "path": "/api/v1/board/courseNotice/list",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _ORIGIN, "path": "/api/v1/week/getStdWeekList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _ORIGIN, "path": "/api/v1/week/getStdEtcList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {
            "origin": _ORIGIN,
            "path": "/api/v1/survey/getApplyPopList",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {
            "origin": _ORIGIN,
            "path": "/api/v1/board/popup/noticeList",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _ORIGIN, "path": "/api/v1/board/notice/list", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {
            "origin": _ORIGIN,
            "path": "/api/v1/board/notice/list/top",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _ORIGIN, "path": "/api/v1/board/notice/info", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _ORIGIN, "path": "/api/v1/board/cmt/list", "operation": _FETCH_OPERATION, "methods": ["POST"]},
    ],
    "suppress": [
        {
            "name": "panopto-script",
            "origin": _ORIGIN,
            "path_template": "/js/common/panopto-{hash}.js",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "reason": "media-integration",
        },
        {
            "name": "course-roster-image",
            "origin": _ORIGIN,
            "path_template": "/upload/dunetadmin/college/{hash}.png",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "reason": "course-roster-image",
        },
        {
            "name": "favicon-icon",
            "origin": _ORIGIN,
            "path_template": "/assets/images/favicon-{hash}.ico",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "reason": "favicon",
        },
        {
            "name": "external-telemetry",
            "origin": "http://0.0.0.0:3000",
            "path_template": "/v1/events",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
            "reason": "telemetry",
        },
        {
            "name": "panopto-sso-popup",
            "origin": "https://cnu.ap.panopto.com",
            "path_template": "/Panopto/Pages/Auth/Login.aspx",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
            "reason": "panopto-sso-popup",
        },
        {
            "name": "panopto-disconnection-log",
            "origin": _ORIGIN,
            "path_template": "/api/v1/panopto/addInternetDisconnectionLog",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
            "reason": "logging",
        },
        {
            "name": "panopto-connectivity-check",
            "origin": _ORIGIN,
            "path_template": "/api/v1/panopto/checkInternetConnection",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "reason": "logging",
        },
    ],
    "static_asset_origins": [_ORIGIN],
    "static_resource_types": ["script", "stylesheet", "font", "image"],
    "selected_file_routes": [],
    "allowed_media": [],
    "max_bytes": 200_000_000,
}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Install the cached notice-list parser without changing shared CLI code."""
    from campusctl.cli import EnvelopeArgumentParser

    domain = subparsers.add_parser("notices", help="inspect cached LMS notices")
    domain.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    commands = domain.add_subparsers(dest="notices_command", parser_class=EnvelopeArgumentParser)
    listing = commands.add_parser("list", help="list cached notices")
    listing.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true")


def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    """Dispatch cached notice listing or the internal selected fetch path."""
    command = getattr(args, "notices_command", None)
    if command == "list":
        catalog = read_domain_catalog("notices", domain_catalog_path("notices", data_dir()))
        rows = catalog["notices"]
        if args.course is not None:
            rows = [row for row in rows if row["course"]["id"] == args.course]
        return {
            "cache": {**cache_metadata(catalog, now=datetime.now(UTC), domain="notices"), "path_present": True},
            "notices": rows,
        }, None
    if command == "fetch":
        entity_id = getattr(args, "entity_id", None)
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise UsageError("an entity ID is required")
        root = data_dir()
        cat_path = domain_catalog_path("notices", root)
        if not cat_path.exists():
            raise CampusError(
                "catalog-missing",
                "Notice catalog is missing.",
                "Run 'campusctl sync --only notices' to create it.",
                "user-action",
            )
        catalog = read_domain_catalog("notices", cat_path)
        matching = [r for r in catalog.get("notices", []) if r.get("entity_id") == entity_id]
        if len(matching) != 1:
            raise CampusError(
                "entity-unknown",
                "The selected notice ID is not in the catalog.",
                "Select one full ID from 'campusctl notices list'.",
                "user-action",
            )
        row = matching[0]
        out_path = getattr(args, "out", None)
        if out_path is not None:
            p = Path(out_path)
            if p.exists() or p.is_symlink():
                raise CampusError(
                    "output-path-conflict",
                    "Selected output path already exists.",
                    "Choose a nonexistent destination path with --out.",
                    "user-action",
                )
        import asyncio

        from campusctl.browser_options import preflight_browser_mode
        from campusctl.config import load_config

        config = load_config()
        headless_override = getattr(args, "headless_override", None)
        mode = preflight_browser_mode(config, "notices.fetch", override=headless_override)
        pkg = asyncio.run(_fetch_notice(config, root, row, out=out_path, headless=mode))
        errors = (
            [CampusError("resource-omitted", "Some resources were omitted by reviewed policy.", status="user-action")]
            if pkg.get("completeness") == "policy-filtered"
            else None
        )
        return {"source_package": pkg}, errors
    raise UsageError("A notices command is required.")


async def _fetch_notice(
    config: dict[str, Any],
    root: Path,
    row: dict[str, Any],
    *,
    out: Path | None = None,
    headless: bool = False,
) -> dict[str, Any]:
    from campusctl.browser import open_session
    from campusctl.providers.cnu.login import ensure_logged_in
    from campusctl.providers.cnu.notice_detail import capture_notice_detail
    from campusctl.providers.cnu.request_policy import RequestPolicy
    from campusctl.providers.cnu.ui_policy import UiRequestDiagnostics, UiRequestPolicy, install_ui_request_interceptor
    from campusctl.source_package import build_source_package

    ui_policy = UiRequestPolicy.from_reviewed_config(FETCH_POLICY)
    diagnostics = UiRequestDiagnostics()
    request_policy = RequestPolicy(ui_policy, "notice", diagnostics)

    async with open_session(config, data_dir=root, headless=headless, operation="notices.fetch") as session:
        page = session.page
        await ensure_logged_in(page, config)
        interceptor = install_ui_request_interceptor(
            page,
            ui_policy,
            operation="notices.fetch",
            diagnostics=diagnostics,
        )
        snapshot = await capture_notice_detail(page, row, interceptor=interceptor)
        result = await build_source_package(
            page,
            snapshot,
            entity_id=row["entity_id"],
            kind="notice",
            course_id=row["course"]["id"],
            course_label=row["course"]["label"],
            root=root,
            policy=request_policy,
            out=out,
            interceptor=interceptor,
        )
        return result


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    from campusctl.providers.cnu.notices import sync_notices

    return await sync_notices(config, root, course_id, headless=headless, reviewed_policy=CAPABILITY["policy"])


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


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    """Render domain summaries and lists, keeping selectable IDs untruncated."""
    if command == "notices.fetch":
        pkg = result.get("source_package")
        if not pkg:
            return []
        lines = [
            f"Notice source: {pkg['entity_id']}",
            f"Package: {pkg['path']}",
            f"Content: {pkg['content_path']}",
            f"Completeness: {pkg['completeness']}",
        ]
        for omitted in pkg.get("omitted_resources", []):
            reason = omitted.get("reason", "omitted")
            name = omitted.get("original_name") or omitted.get("resource_id", "resource")
            lines.append(f"Omitted: {reason} — {name}")
        return lines
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
