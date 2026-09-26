"""Cached notices and the reviewed notice-sync command surface."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir

_ORIGIN = "https://dcs-learning.cnu.ac.kr"
_OPERATION = "notices.sync"
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


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Install the cached notice-list parser without changing shared CLI code."""
    from campusctl.cli import EnvelopeArgumentParser

    domain = subparsers.add_parser("notices", help="inspect cached LMS notices")
    domain.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    commands = domain.add_subparsers(dest="notices_command", parser_class=EnvelopeArgumentParser)
    listing = commands.add_parser("list", help="list cached notices")
    listing.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    listing.add_argument("--course", help="limit results to one course ID")


def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    """Read cache without configuration, network, browser, or session lock."""
    if args.notices_command != "list":
        raise UsageError("A notices command is required.")
    catalog = read_domain_catalog("notices", domain_catalog_path("notices", data_dir()))
    rows = catalog["notices"]
    if args.course is not None:
        rows = [row for row in rows if row["course"]["id"] == args.course]
    return {
        "cache": {
            "generated_at": catalog["generated_at"],
            "path_present": True,
            "enrollment_state": catalog["enrollment_state"],
            "failed_courses": catalog["failed_courses"],
        },
        "notices": rows,
    }, None


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    """Delegate network work to the provider, after enforcing headed-only mode."""
    if headless:
        raise CampusError(
            "headless-unavailable",
            "Headless notice sync is not approved for this LMS.",
            "Run headed notice sync until headless compatibility is reviewed.",
            "user-action",
        )
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
