"""Reviewed archive metadata and one selected, verified material download."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import (
    assert_material_snapshot_current,
    cache_metadata,
    catalog_snapshot,
    publish_material_snapshot,
    resolve_material_number,
)
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir

_LEARNING = "https://dcs-learning.cnu.ac.kr"
_LCMS = "https://dcs-lcms.cnu.ac.kr"
_GET = (
    "/std/myLecture",
    "/std/lecture",
    "/std/archive",
    "/api/v1/archive/getAttachFileList",
    "/properties/messages.properties",
    "/properties/messages_ko.properties",
)
_POST = (
    "/api/v1/course/addSessionCourseInfo",
    "/api/v1/archive/list",
    "/api/v1/user/getUserInfo",
    "/api/v1/user/getMenuList",
    "/api/v1/alarm/getAlarmListByDate",
    "/api/v1/course/getCeShortcuts",
    "/api/v1/course/get",
    "/api/v1/common/checkEnableUrl",
    "/api/v1/boardM/getBoardItemList",
    "/api/v1/term/getYearTermList",
    "/api/v1/course/getStdMyCourseList",
    "/api/v1/board/courseNotice/list",
    "/api/v1/week/getStdWeekList",
    "/api/v1/week/getStdEtcList",
    "/api/v1/survey/getApplyPopList",
    "/api/v1/board/popup/noticeList",
)
_OPERATIONS = ("materials.sync", "materials.download")
CAPABILITY: dict[str, Any] = {
    "commands": ["list", "download"],
    "policy": {
        "approved": True,
        "read_only_evidence": "docs/specs/30-materials/design.md#approved-operations-and-selected-url-binding",
        "origins": [_LEARNING, _LCMS],
        "routes": [
            {
                "origin": _LEARNING,
                "path": path,
                "operation": operation,
                "methods": [method],
                **({"query": {"e": "encrypted"}} if path.endswith("/getAttachFileList") else {}),
                **({"query": {"_": "cachebuster"}} if path.startswith("/properties/") else {}),
            }
            for operation in _OPERATIONS
            for method, paths in (("GET", _GET), ("POST", _POST))
            for path in paths
        ]
        + [
            {
                "origin": _LEARNING,
                "path": "/api/v1/week/getStdActivityStatus",
                "operation": operation,
                "methods": ["POST"],
                "logging_token_reviewed": True,
                "resource_type": "xhr",
            }
            for operation in _OPERATIONS
        ]
        + [
            {
                "origin": _LEARNING,
                "path": "/api/v1/archive/fileDownload",
                "operation": "materials.download",
                "methods": ["POST"],
            }
        ],
        "selected_file_routes": [
            {"origin": origin, "path_template": template, "operation": "materials.download", "methods": ["GET"]}
            for origin, template in (
                (_LCMS, "/upload/{storage-id}/{encoded-filename}"),
                (_LEARNING, "/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}"),
            )
        ],
        "allowed_media": [
            {"mime": "application/x-pdf", "extensions": ["pdf"]},
            {
                "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "extensions": ["pptx"],
            },
            {"mime": "application/zip", "extensions": ["zip"]},
        ],
        "max_bytes": 200_000_000,
        "suppress": [
            {
                "name": name,
                "origin": origin,
                "path_template": path,
                "operation": operation,
                "methods": [method],
                "reason": reason,
            }
            for operation in _OPERATIONS
            for origin, name, path, method, reason in (
                (_LEARNING, "panopto-script", "/js/common/panopto-{hash}.js", "GET", "media-integration"),
                (_LEARNING, "panopto-saml-script", "/js/common/panoptoSaml-{hash}.js", "GET", "media-integration"),
                (
                    "https://cnu.ap.panopto.com",
                    "panopto-sso-popup",
                    "/Panopto/Pages/Auth/Login.aspx",
                    "POST",
                    "panopto-sso-popup",
                ),
                (
                    _LEARNING,
                    "course-roster-image",
                    "/upload/dunetadmin/college/{hash}.png",
                    "GET",
                    "course-roster-image",
                ),
                (_LEARNING, "favicon-icon", "/assets/images/favicon-{hash}.ico", "GET", "favicon"),
                ("http://0.0.0.0:3000", "external-telemetry", "/v1/events", "POST", "telemetry"),
                (
                    _LEARNING,
                    "panopto-disconnection-log",
                    "/api/v1/panopto/addInternetDisconnectionLog",
                    "POST",
                    "logging",
                ),
                (
                    _LEARNING,
                    "panopto-connectivity-check",
                    "/api/v1/panopto/checkInternetConnection",
                    "GET",
                    "logging",
                ),
            )
        ],
        "static_asset_origins": [_LEARNING],
        "static_resource_types": ["script", "stylesheet", "font", "image"],
    },
}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    from campusctl.cli import EnvelopeArgumentParser

    domain = subparsers.add_parser("materials", help="inspect and download official course materials")
    domain.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    commands = domain.add_subparsers(dest="materials_command", parser_class=EnvelopeArgumentParser)
    listing = commands.add_parser("list", help="list cached archive files")
    listing.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true")
    download = commands.add_parser("download", help="save one selected archive file")
    download.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    download.add_argument("entity_id", nargs="?", help="full ID or printed human number")
    download.add_argument("--out", type=Path, help="destination directory")


def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | None]:
    command = args.materials_command
    if command not in {"list", "download"}:
        raise UsageError("A materials command is required.")
    root = data_dir()
    snapshot = catalog_snapshot("materials", root)
    if snapshot is None:
        raise CampusError(
            "catalog-missing",
            "Materials catalog is missing.",
            "Run 'campusctl sync --only materials' to create it.",
            "user-action",
        )
    catalog = snapshot[0]
    if command == "list":
        args._material_generation = snapshot[1]
        rows = catalog["materials"]
        if args.course is not None:
            rows = [row for row in rows if row["course"]["id"] == args.course]
        return {
            "cache": {**cache_metadata(catalog, now=datetime.now(UTC), domain="materials"), "path_present": True},
            "materials": rows,
        }, None
    selection = None
    if args.entity_id is None:
        if not args._interactive:
            raise CampusError(
                "selection-required",
                "Select a full material ID without interactive terminals.",
                "Run 'campusctl materials list --json' and pass one full ID.",
                "user-action",
            )
        import sys

        from campusctl.envelope import make_envelope
        from campusctl.presentation import render_human

        snapshot = catalog_snapshot("materials", root)
        if snapshot is None:
            raise CampusError(
                "catalog-missing",
                "Materials catalog is missing.",
                "Run 'campusctl sync --only materials'.",
                "user-action",
            )
        catalog = snapshot[0]
        render_human(
            "materials.list",
            make_envelope(
                result={
                    "materials": catalog["materials"],
                    "cache": cache_metadata(catalog, now=datetime.now(UTC), domain="materials"),
                }
            ),
            sys.stdout,
        )
        sys.stdout.flush()
        publish_material_snapshot(root, snapshot[1], [row["entity_id"] for row in catalog["materials"]])
        try:
            choice = input("Select one file number (blank cancels): ").strip()
        except EOFError:
            choice = ""
        if not choice:
            raise CampusError(
                "selection-cancelled", "No material selected.", "Run 'campusctl materials list'.", "user-action"
            )
        args.entity_id, selection = resolve_material_number(root, choice)
    elif args.entity_id.isdecimal() and not any(row["entity_id"] == args.entity_id for row in catalog["materials"]):
        if args._output_mode == "json":
            raise CampusError(
                "selection-invalid",
                "JSON download requires a full material ID.",
                "Use a full ID from 'campusctl materials list --json'.",
                "user-action",
            )
        args.entity_id, selection = resolve_material_number(root, args.entity_id)
    if selection is not None:
        current = catalog_snapshot("materials", root)
        if current is None or current[1] != selection["catalog_generation"]:
            raise CampusError(
                "selection-stale",
                "The printed materials list changed.",
                "Run 'campusctl materials list' again.",
                "user-action",
            )
        catalog = current[0]
    rows = [row for row in catalog["materials"] if row.get("entity_id") == args.entity_id]
    if len(rows) != 1:
        raise CampusError(
            "entity-unknown",
            "The selected material ID is not in the catalog.",
            "Select one full ID from 'campusctl materials list'.",
            "user-action",
        )
    row = rows[0]
    if row.get("downloadable") is not True:
        raise CampusError(
            "unsupported-media-type",
            "The selected material is not downloadable.",
            "Select an approved non-video archive attachment.",
            "user-action",
        )
    from campusctl.config import load_config

    config = load_config()
    mode = preflight_browser_mode(config, "materials.download", override=args.headless_override)
    from contextlib import nullcontext

    from campusctl.browser import pre_browser_check

    check = pre_browser_check(lambda: assert_material_snapshot_current(root, selection)) if selection else nullcontext()
    with check:
        material = asyncio.run(_download(config, root, row, args.out, headless=mode))
    return {"material": material}, None


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    from campusctl.providers.cnu.materials import sync_materials

    return await sync_materials(config, root, course_id, headless=headless, reviewed_policy=CAPABILITY["policy"])


def _result(row: dict[str, Any], path: str, size: int, digest: str, outcome: str) -> dict[str, Any]:
    return {
        "entity_id": row["entity_id"],
        "path": path,
        "display_name": row["display_name"],
        "filename": row["filename"],
        "size_bytes": size,
        "sha256": digest,
        "outcome": outcome,
        "provenance": {
            "provider": "cnu",
            "course_id": row["course"]["id"],
            "board_item_id": row["archive_entry"]["board_item_id"],
            "file_id": row["file_id"],
        },
    }


async def _download(
    config: dict[str, Any], root: Path, row: dict[str, Any], out: Path | None, *, headless: bool = False
) -> dict[str, Any]:
    from campusctl.browser import open_session
    from campusctl.material_files import (
        default_download_dir,
        prepare_output_dir,
        publish_attachment,
        safe_component,
        verified_receipt,
        write_receipt,
    )
    from campusctl.providers.cnu.attachment_transfer import OfficialAttachmentTarget, fetch_official_attachment
    from campusctl.providers.cnu.course_context import open_course_section, prepare_course_section
    from campusctl.providers.cnu.login import COURSE_LINK_SELECTOR, MY_LECTURE_URL, ensure_logged_in
    from campusctl.providers.cnu.materials import (
        _ARCHIVE_MENU,
        _CLICK_ICON_JS,
        _MODAL,
        _archive_navigation,
        _archive_state,
        _select_page,
        _step,
        enumerate_archive,
    )
    from campusctl.providers.cnu.request_policy import RequestPolicy
    from campusctl.providers.cnu.ui_policy import UiRequestDiagnostics, UiRequestPolicy, install_ui_request_interceptor

    policy = UiRequestPolicy.from_reviewed_config(CAPABILITY["policy"])
    if not policy.approved:
        raise CampusError("policy-blocked", "Materials download policy is not approved.")
    entity_id = row["entity_id"]
    course_id = row["course"]["id"]
    post_id = row["archive_entry"]["board_item_id"]
    file_id = row["file_id"]
    async with open_session(config, data_dir=root, headless=headless, operation="materials.download") as session:
        destination = out if out is not None else default_download_dir() / safe_component(row["course"]["label"])
        output_dir = prepare_output_dir(root, entity_id, destination)
        receipt = verified_receipt(root, entity_id, output_dir)
        if receipt is not None:
            return _result(row, receipt["path"], receipt["size_bytes"], receipt["sha256"], "skipped-existing")
        page = session.page
        await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)
        diagnostics = UiRequestDiagnostics()
        guard = await install_ui_request_interceptor(
            session.context, policy, operation="materials.download", diagnostics=diagnostics
        )
        try:
            await _step(page.goto(MY_LECTURE_URL), guard, "opening guarded course roster")
            await _step(
                prepare_course_section(page, config, course_id, "archive"), guard, "selecting guarded archive course"
            )
            await _archive_navigation(page, guard, lambda: open_course_section(page, "archive"))
            course = {"course_id": course_id, "label": row["course"]["label"]}
            current = await enumerate_archive(page, course, guard)
            if (
                len(
                    [
                        item
                        for item in current
                        if item["entity_id"] == entity_id
                        and item["archive_entry"]["board_item_id"] == post_id
                        and item["file_id"] == file_id
                        and item["downloadable"] is True
                    ]
                )
                != 1
            ):
                raise CampusError("entity-unknown", "Selected archive file or parent no longer matches the catalog.")
            await _archive_navigation(page, guard, lambda: page.click(_ARCHIVE_MENU))
            state = await _archive_state(page, guard, 1)
            pages = (state["total_count"] + state["page_size"] - 1) // state["page_size"]
            selected_page = 1 if any(item["board_item_id"] == post_id for item in state["posts"]) else None
            if selected_page is None:
                for number in range(2, pages + 1):
                    await _select_page(page, guard, number)
                    state = await _archive_state(page, guard, number)
                    if any(item["board_item_id"] == post_id for item in state["posts"]):
                        selected_page = number
                        break
            if selected_page is None:
                raise CampusError("entity-unknown", "Selected archive parent no longer exists.")
            await _step(page.evaluate(_CLICK_ICON_JS, post_id), guard, "opening selected archive parent")
            inline_parent = f'#listBody tr:has([data-act="file"][data-boarditem_no={json.dumps(post_id)}])'
            modal = False
            try:
                await _step(page.wait_for_selector(_MODAL, timeout=7000), guard, "waiting for selected file modal")
                modal = True
            except CampusError as error:
                if error.code != "browser-timeout":
                    raise
            modal_locator = f'#file_download [data-act="downloadFile"][data-id={json.dumps(file_id)}]'
            modal_matches = (
                await _step(page.locator(modal_locator).count(), guard, "checking selected modal control")
                if modal
                else 0
            )
            if modal_matches > 1:
                raise CampusError("entity-unknown", "Selected archive control is ambiguous.")
            if modal_matches == 1:
                locator = modal_locator
            else:
                if await _step(page.locator(inline_parent).count(), guard, "checking selected archive row") != 1:
                    raise CampusError("entity-unknown", "Selected archive parent no longer exists.")
                locator = f'{inline_parent} [data-act="downloadFile"][data-id={json.dumps(file_id)}]'
                if await _step(page.locator(locator).count(), guard, "checking selected inline control") != 1:
                    raise CampusError("entity-unknown", "Selected archive control no longer exists.")
            request_policy = RequestPolicy(policy, row["filename"], diagnostics)
            fetched = await _step(
                fetch_official_attachment(
                    page,
                    OfficialAttachmentTarget(file_id, "archive", post_id, locator, None),
                    request_policy,
                    output_dir,
                    max_bytes=CAPABILITY["policy"]["max_bytes"],
                    interceptor=guard,
                ),
                guard,
                "fetching selected archive attachment",
            )
            try:
                published = publish_attachment(
                    fetched.temp_path, output_dir, row["filename"], fetched.sha256, fetched.size_bytes
                )
                write_receipt(
                    root, entity_id, output_dir, published.path, fetched.size_bytes, fetched.sha256, fetched.media_type
                )
            finally:
                fetched.temp_path.unlink(missing_ok=True)
            return _result(row, str(published.path), fetched.size_bytes, fetched.sha256, published.outcome)
        finally:
            try:
                guard.raise_if_denied()
            finally:
                await guard.close()


def _wrap(message: str, width: int, *, indent: str = "") -> list[str]:
    from campusctl.presentation import _cells
    from campusctl.presentation import _wrap as wrap_cells

    return [f"{indent}{line}" for line in wrap_cells(message, max(1, width - _cells(indent)))]


def _warnings(state: object, failed: object, width: int) -> list[str]:
    lines: list[str] = []
    if state == "unknown":
        lines.extend(_wrap("Warning: Enrollment unknown; materials may be stale.", width))
    if isinstance(failed, list):
        for course in failed:
            if isinstance(course, dict):
                lines.extend(
                    _wrap(
                        f"Warning: Material cache stale for {course.get('label') or course.get('course_id')} "
                        f"({course.get('course_id')}): {course.get('reason')}.",
                        width,
                    )
                )
    return lines


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    if command == "sync.materials":
        if not any(key in result for key in ("courses", "materials", "failed_courses", "catalog")):
            return []
        count, courses = result.get("materials", 0), result.get("courses", 0)
        lines = _wrap(
            f"Synced {courses} {'course' if courses == 1 else 'courses'}, "
            f"{count} {'material' if count == 1 else 'materials'}.",
            width,
        )
        catalog = result.get("catalog") or {}
        return lines + _warnings(catalog.get("enrollment_state"), result.get("failed_courses"), width)
    if command == "materials.download" and isinstance(result.get("material"), dict):
        material = result["material"]
        label = {"saved": "Saved", "reused": "Reused", "skipped-existing": "Skipped existing"}.get(
            material.get("outcome")
        )
        return [f"{label}: {material['path']}"] if label else []
    if command != "materials.list" or not isinstance(result.get("materials"), list):
        return []
    rows = result["materials"]
    cache = result.get("cache") or {}
    try:
        stamp = datetime.fromisoformat(cache["generated_at"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except (KeyError, ValueError, TypeError):
        stamp = "unknown"
    count = len(rows)
    lines = _wrap(f"{count} {'material' if count == 1 else 'materials'} (updated {stamp})", width)
    lines.extend(_warnings(cache.get("enrollment_state"), cache.get("failed_courses"), width))
    if not rows:
        return lines + _wrap("No cached materials match this selection.", width)
    last_course = None
    for number, row in enumerate(rows, 1):
        course = row["course"]
        if course["id"] != last_course:
            lines.append("")
            lines.extend(_wrap(f"Course: {course['label']} ({course['id']})", width))
            last_course = course["id"]
        lines.extend(_wrap(f"Archive: {row['archive_entry']['title']}", width, indent="  "))
        lines.extend(_wrap(f"File: {row['filename']}", width, indent="  "))
        lines.extend(_wrap(f"Downloadable: {'yes' if row['downloadable'] else 'no'}", width, indent="  "))
        lines.append(f"  {number}. {row['entity_id']}")
    return lines
