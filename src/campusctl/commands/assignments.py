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
_FETCH_OPERATION = "assignments.fetch"


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
FETCH_POLICY = {
    "approved": True,
    "read_only_evidence": "2026-09-25 sanitized LMS report §§1.5,2 and owner LMS pins decision",
    "origins": [_LMS],
    "routes": [
        {"origin": _LMS, "path": "/std/myLecture", "operation": _FETCH_OPERATION, "methods": ["GET"]},
        {"origin": _LMS, "path": "/std/lecture", "operation": _FETCH_OPERATION, "methods": ["GET"]},
        {
            "origin": _LMS,
            "path": "/api/v1/week/getStdActivityStatus",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
            "logging_token_reviewed": True,
            "resource_type": "xhr",
        },
        {"origin": _LMS, "path": "/std/task", "operation": _FETCH_OPERATION, "methods": ["GET"]},
        {
            "origin": _LMS,
            "path": "/std/taskView",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "query": {"curPage": "page"},
        },
        {
            "origin": _LMS,
            "path": "/properties/messages.properties",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "query": {"_": "cachebuster"},
        },
        {
            "origin": _LMS,
            "path": "/properties/messages_ko.properties",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "query": {"_": "cachebuster"},
        },
        {
            "origin": _LMS,
            "path": "/api/v1/course/addSessionCourseInfo",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _LMS, "path": "/api/v1/user/getUserInfo", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/user/getMenuList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {
            "origin": _LMS,
            "path": "/api/v1/alarm/getAlarmListByDate",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _LMS, "path": "/api/v1/course/getCeShortcuts", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/common/checkEnableUrl", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/boardM/getBoardItemList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/term/getYearTermList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {
            "origin": _LMS,
            "path": "/api/v1/course/getStdMyCourseList",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
        },
        {"origin": _LMS, "path": "/api/v1/course/get", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/board/courseNotice/list", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/week/getStdWeekList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/week/getStdEtcList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/survey/getApplyPopList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/board/popup/noticeList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/task/stdList", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/task/detail", "operation": _FETCH_OPERATION, "methods": ["POST"]},
        {"origin": _LMS, "path": "/api/v1/task/stdDetail", "operation": _FETCH_OPERATION, "methods": ["POST"]},
    ],
    "suppress": [
        {
            "name": "panopto-script",
            "origin": _LMS,
            "path_template": "/js/common/panopto-{hash}.js",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "reason": "media-integration",
        },
        {
            "name": "course-roster-image",
            "origin": _LMS,
            "path_template": "/upload/dunetadmin/college/{hash}.png",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "reason": "course-roster-image",
        },
        {
            "name": "favicon-icon",
            "origin": _LMS,
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
            "origin": _LMS,
            "path_template": "/api/v1/panopto/addInternetDisconnectionLog",
            "operation": _FETCH_OPERATION,
            "methods": ["POST"],
            "reason": "logging",
        },
        {
            "name": "panopto-connectivity-check",
            "origin": _LMS,
            "path_template": "/api/v1/panopto/checkInternetConnection",
            "operation": _FETCH_OPERATION,
            "methods": ["GET"],
            "reason": "logging",
        },
    ],
    "static_asset_origins": [_LMS],
    "static_resource_types": ["script", "stylesheet", "font", "image"],
    "selected_file_routes": [],
    "allowed_media": [],
    "max_bytes": 200_000_000,
}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    assignments = subparsers.add_parser("assignments", help="inspect cached assignments")
    assignments.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    commands = assignments.add_subparsers(dest="assignments_command")
    listing = commands.add_parser("list", help="list cached assignments")
    listing.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true")


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
        entity_id = getattr(args, "entity_id", None)
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise UsageError("an entity ID is required")
        root = data_dir()
        cat_path = domain_catalog_path("assignments", root)
        if not cat_path.exists():
            raise CampusError(
                "catalog-missing",
                "Assignment catalog is missing.",
                "Run 'campusctl sync --only assignments' to create it.",
                "user-action",
            )
        catalog = read_domain_catalog("assignments", cat_path)
        matching = [r for r in catalog.get("assignments", []) if r.get("entity_id") == entity_id]
        if len(matching) != 1:
            raise CampusError(
                "entity-unknown",
                "The selected assignment ID is not in the catalog.",
                "Select one full ID from 'campusctl assignments list'.",
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
        mode = preflight_browser_mode(config, "assignments.fetch", override=headless_override)
        pkg = asyncio.run(_fetch_assignment(config, root, row, out=out_path, headless=mode))
        errors = (
            [CampusError("resource-omitted", "Some resources were omitted by reviewed policy.", status="user-action")]
            if pkg.get("completeness") == "policy-filtered"
            else None
        )
        return {"source_package": pkg}, errors
    raise UsageError("an assignments command is required")


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
    from campusctl.providers.cnu.request_policy import RequestPolicy
    from campusctl.providers.cnu.ui_policy import UiRequestDiagnostics, UiRequestPolicy, install_ui_request_interceptor
    from campusctl.source_package import build_source_package

    ui_policy = UiRequestPolicy.from_reviewed_config(FETCH_POLICY)
    if not ui_policy.approved:
        raise CampusError("policy-blocked", "The reviewed assignment fetch policy is unavailable.", None, "error")
    diagnostics = UiRequestDiagnostics()
    request_policy = RequestPolicy(ui_policy, "assignment", diagnostics)

    async with open_session(config, data_dir=root, headless=headless, operation="assignments.fetch") as session:
        page = session.page
        await ensure_logged_in(page, config)
        await settle_sso_popups(session, domain="assignments")
        interceptor = await install_ui_request_interceptor(
            session.context,
            ui_policy,
            operation="assignments.fetch",
            diagnostics=diagnostics,
        )
        snapshot = await capture_assignment_detail(page, config, row)
        interceptor.raise_if_denied()
        result = await build_source_package(
            page,
            snapshot,
            entity_id=row["entity_id"],
            kind="assignment",
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
    if command == "assignments.fetch":
        pkg = result.get("source_package")
        if not pkg:
            return []
        lines = [
            f"Assignment source: {pkg['entity_id']}",
            f"Package: {pkg['path']}",
            f"Content: {pkg['content_path']}",
            f"Completeness: {pkg['completeness']}",
        ]
        for omitted in pkg.get("omitted_resources", []):
            reason = omitted.get("reason", "omitted")
            name = omitted.get("original_name") or omitted.get("resource_id", "resource")
            lines.append(f"Omitted: {reason} — {name}")
        return lines
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
