"""Assignment metadata sync and cache-only listing."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.catalog_view import cache_metadata
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir

_LMS = "https://dcs-learning.cnu.ac.kr"
_OPERATION = "assignments.sync"


def _pin(path: str, method: str, query: dict[str, str] | None = None) -> dict[str, Any]:
    route: dict[str, Any] = {"origin": _LMS, "path": path, "operation": _OPERATION, "methods": [method]}
    if query is not None:
        route["query"] = query
    return route


def _suppress(name: str, path: str, method: str, reason: str, origin: str = _LMS) -> dict[str, Any]:
    return {
        "name": name,
        "origin": origin,
        "path_template": path,
        "operation": _OPERATION,
        "methods": [method],
        "reason": reason,
    }


CAPABILITY = {
    "commands": ["list"],
    "policy": {
        "approved": True,
        "read_only_evidence": "2026-09-25 sanitized LMS report §§1.1,2,4.1,5 and owner LMS pins decision",
        "origins": [_LMS],
        "routes": [
            _pin("/std/myLecture", "GET"),
            _pin("/std/lecture", "GET"),
            _pin("/api/v1/course/addSessionCourseInfo", "POST"),
            _pin("/std/task", "GET"),
            _pin("/api/v1/task/stdList", "POST"),
            _pin("/api/v1/user/getUserInfo", "POST"),
            _pin("/api/v1/user/getMenuList", "POST"),
            _pin("/api/v1/alarm/getAlarmListByDate", "POST"),
            _pin("/api/v1/course/getCeShortcuts", "POST"),
            _pin("/api/v1/course/get", "POST"),
            _pin("/api/v1/common/checkEnableUrl", "POST"),
            _pin("/api/v1/boardM/getBoardItemList", "POST"),
            _pin("/api/v1/term/getYearTermList", "POST"),
            _pin("/api/v1/course/getStdMyCourseList", "POST"),
            _pin("/api/v1/board/courseNotice/list", "POST"),
            _pin("/api/v1/week/getStdWeekList", "POST"),
            _pin("/api/v1/week/getStdEtcList", "POST"),
            {
                **_pin("/api/v1/week/getStdActivityStatus", "POST"),
                "logging_token_reviewed": True,
                "resource_type": "xhr",
            },
            _pin("/api/v1/survey/getApplyPopList", "POST"),
            _pin("/api/v1/board/popup/noticeList", "POST"),
            _pin("/properties/messages.properties", "GET", {"_": "cachebuster"}),
            _pin("/properties/messages_ko.properties", "GET", {"_": "cachebuster"}),
        ],
        "suppress": [
            _suppress("panopto-script", "/js/common/panopto-{hash}.js", "GET", "media-integration"),
            _suppress("panopto-saml-script", "/js/common/panoptoSaml-{hash}.js", "GET", "media-integration"),
            _suppress(
                "panopto-sso-popup",
                "/Panopto/Pages/Auth/Login.aspx",
                "POST",
                "panopto-sso-popup",
                "https://cnu.ap.panopto.com",
            ),
            _suppress("course-roster-image", "/upload/dunetadmin/college/{hash}.png", "GET", "course-roster-image"),
            _suppress("favicon-icon", "/assets/images/favicon-{hash}.ico", "GET", "favicon"),
            _suppress("external-telemetry", "/v1/events", "POST", "telemetry", "http://0.0.0.0:3000"),
            _suppress("panopto-disconnection-log", "/api/v1/panopto/addInternetDisconnectionLog", "POST", "logging"),
            _suppress("panopto-connectivity-check", "/api/v1/panopto/checkInternetConnection", "GET", "logging"),
        ],
        "static_asset_origins": [_LMS],
        "static_resource_types": ["script", "stylesheet", "font", "image"],
        "selected_file_routes": [],
        "allowed_media": [],
        "max_bytes": None,
    },
}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    assignments = subparsers.add_parser("assignments", help="inspect cached assignments")
    assignments.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    commands = assignments.add_subparsers(dest="assignments_command")
    listing = commands.add_parser("list", help="list cached assignments")
    listing.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true")


def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], None]:
    if args.assignments_command != "list":
        raise UsageError("an assignments command is required")
    catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", data_dir()))
    rows = catalog["assignments"]
    if args.course is not None:
        rows = [row for row in rows if row["course"]["id"] == args.course]
    return {
        "cache": cache_metadata(catalog, now=datetime.now(UTC), domain="assignments"),
        "assignments": rows,
    }, None


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    from campusctl.providers.cnu.assignments import sync_assignments

    return await sync_assignments(config, root, course_id, headless=headless, reviewed_policy=CAPABILITY["policy"])


def _updated(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


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
