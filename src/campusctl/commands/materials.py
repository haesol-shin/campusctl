"""Reviewed archive metadata and one selected, verified material download."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, TextIO

from campusctl.browser import settle_sso_popups
from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import (
    assert_material_snapshot_current,
    cache_metadata,
    catalog_snapshot,
    publish_material_snapshot,
    resolve_material_number,
)
from campusctl.cli_args import _OUTPUT_NOTE, EnvelopeArgumentParser
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.commands.cache_warnings import cache_warning_lines
from campusctl.commands.course_inputs import list_refresh_scope, list_with_refresh
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import data_dir
from campusctl.source_resources import SourceResolution
from campusctl.text_layout import _wrap_indented

CAPABILITY: dict[str, Any] = {"commands": ["list", "download"]}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    domain = subparsers.add_parser("materials", help="list or download official files")
    domain.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope"
    )
    commands = domain.add_subparsers(dest="materials_command", parser_class=EnvelopeArgumentParser)
    listing = commands.add_parser("list", help="list cached archive files")
    listing.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope"
    )
    listing.add_argument("--course", help="limit results to one course ID")
    listing.add_argument("--refresh", action="store_true", help="sync materials before listing")
    download = commands.add_parser("download", help="download one official file", epilog=_OUTPUT_NOTE)
    download.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit the JSON response envelope"
    )
    download.add_argument("entity_id", nargs="?", metavar="ID", help="full material ID or printed number")
    download.add_argument("--out", type=Path, help="destination directory")


def profile_scope(args: argparse.Namespace) -> tuple[str, ...] | None:
    return list_refresh_scope(args, ("materials",))


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    # Resolve the data directory first, as before the unified pipeline: it outranks input checks.
    data_dir()
    if args.materials_command == "list":
        return list_with_refresh("materials", args, bindings, context, _list)
    if args.materials_command == "download":
        return _download_selected(args, context, bindings.source_resolution)
    raise UsageError("A materials command is required.")


def _list(course_id: str | None) -> CommandResult:
    root = data_dir()
    snapshot = catalog_snapshot("materials", root)
    if snapshot is None:
        raise CampusError(
            "catalog-missing",
            "Materials catalog is missing.",
            "Run 'campusctl sync --only materials' to create it.",
            "user-action",
        )
    catalog, generation = snapshot
    rows = catalog["materials"]
    if course_id is not None:
        rows = [row for row in rows if row["course"]["id"] == course_id]
    result = {
        "cache": {**cache_metadata(catalog, now=datetime.now(UTC), domain="materials"), "path_present": True},
        "materials": rows,
    }
    # The printed order becomes the number namespace only after the list was written.
    publish = partial(
        publish_material_snapshot, root, generation, [row["entity_id"] for row in rows], course_id=course_id
    )
    return command_result(result, publish=publish)


def _pick_number(root: Path, output: Callable[[str, dict[str, Any], TextIO], None]) -> tuple[str, dict[str, Any]]:
    import sys

    from campusctl.envelope import make_envelope

    snapshot = catalog_snapshot("materials", root)
    if snapshot is None:
        raise CampusError(
            "catalog-missing",
            "Materials catalog is missing.",
            "Run 'campusctl sync --only materials'.",
            "user-action",
        )
    catalog = snapshot[0]
    output(
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
    return resolve_material_number(root, choice)


def _requested_material(
    args: argparse.Namespace, context: CommandContext, root: Path, catalog: dict[str, Any]
) -> tuple[str, dict[str, Any] | None]:
    """Return the full ID to download and the printed-number selection it came from, if any."""
    if args.entity_id is None:
        if not context.interactive:
            raise CampusError(
                "selection-required",
                "Select a full material ID without interactive terminals.",
                "Run 'campusctl materials list --json' and pass one full ID.",
                "user-action",
            )
        return _pick_number(root, context.output)
    if args.entity_id.isdecimal() and not any(row["entity_id"] == args.entity_id for row in catalog["materials"]):
        if context.output_mode == "json":
            raise CampusError(
                "selection-invalid",
                "JSON download requires a full material ID.",
                "Use a full ID from 'campusctl materials list --json'.",
                "user-action",
            )
        return resolve_material_number(root, args.entity_id)
    return args.entity_id, None


def _download_selected(
    args: argparse.Namespace, context: CommandContext, resolution: SourceResolution
) -> CommandResult:
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
    entity_id, selection = _requested_material(args, context, root, catalog)
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
    rows = [row for row in catalog["materials"] if row.get("entity_id") == entity_id]
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
        material = asyncio.run(_download(config, root, row, args.out, resolution, headless=mode))
    return command_result({"material": material})


async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]:
    from campusctl.providers.cnu.materials import sync_materials

    return await sync_materials(config, root, course_id, headless=headless)


def _result(row: dict[str, Any], path: str, size: int, digest: str, outcome: str, provenance: str) -> dict[str, Any]:
    return {
        "entity_id": row["entity_id"],
        "path": path,
        "display_name": row["display_name"],
        "filename": row["filename"],
        "size_bytes": size,
        "sha256": digest,
        "outcome": outcome,
        "provenance": {
            "provider": provenance,
            "course_id": row["course"]["id"],
            "board_item_id": row["archive_entry"]["board_item_id"],
            "file_id": row["file_id"],
        },
    }


async def _download(
    config: dict[str, Any],
    root: Path,
    row: dict[str, Any],
    out: Path | None,
    resolution: SourceResolution,
    *,
    headless: bool = False,
) -> dict[str, Any]:
    from campusctl.attachment_policy import MAX_ATTACHMENT_BYTES, OfficialAttachmentTarget, RequestPolicy
    from campusctl.browser import open_session
    from campusctl.material_files import (
        adopt_attachment,
        prepare_output_dir,
        publish_attachment,
        resolve_download_layout,
        verified_receipt,
        write_receipt,
    )
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

    entity_id = row["entity_id"]
    course_id = row["course"]["id"]
    post_id = row["archive_entry"]["board_item_id"]
    file_id = row["file_id"]
    async with open_session(config, data_dir=root, headless=headless, operation="materials.download") as session:
        destination, course_root = resolve_download_layout(config, row, out)
        output_dir = prepare_output_dir(root, entity_id, destination)
        if course_root is not None:
            prepare_output_dir(root, entity_id, course_root)
        receipt = verified_receipt(root, entity_id, output_dir, course_root)
        if receipt is not None:
            return _result(
                row,
                receipt["path"],
                receipt["size_bytes"],
                receipt["sha256"],
                "skipped-existing",
                resolution.provenance,
            )
        page = session.page
        await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)
        await settle_sso_popups(session, domain="materials")
        await _step(page.goto(MY_LECTURE_URL), "opening course roster")
        await _step(prepare_course_section(page, config, course_id, "archive"), "selecting archive course")
        await _archive_navigation(page, lambda: open_course_section(page, "archive"))
        course = {"course_id": course_id, "label": row["course"]["label"]}
        current = await enumerate_archive(page, course)
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
        await _archive_navigation(page, lambda: page.click(_ARCHIVE_MENU))
        state = await _archive_state(page, 1)
        pages = (state["total_count"] + state["page_size"] - 1) // state["page_size"]
        selected_page = 1 if any(item["board_item_id"] == post_id for item in state["posts"]) else None
        if selected_page is None:
            for number in range(2, pages + 1):
                await _select_page(page, number)
                state = await _archive_state(page, number)
                if any(item["board_item_id"] == post_id for item in state["posts"]):
                    selected_page = number
                    break
        if selected_page is None:
            raise CampusError("entity-unknown", "Selected archive parent no longer exists.")
        await _step(page.evaluate(_CLICK_ICON_JS, post_id), "opening selected archive parent")
        inline_parent = f'#listBody tr:has([data-act="file"][data-boarditem_no={json.dumps(post_id)}])'
        modal = False
        try:
            await _step(page.wait_for_selector(_MODAL, timeout=7000), "waiting for selected file modal")
            modal = True
        except CampusError as error:
            if error.code != "browser-timeout":
                raise
        modal_locator = f'#file_download [data-act="downloadFile"][data-id={json.dumps(file_id)}]'
        modal_matches = (
            await _step(page.locator(modal_locator).count(), "checking selected modal control") if modal else 0
        )
        if modal_matches > 1:
            raise CampusError("entity-unknown", "Selected archive control is ambiguous.")
        if modal_matches == 1:
            locator = modal_locator
        else:
            if await _step(page.locator(inline_parent).count(), "checking selected archive row") != 1:
                raise CampusError("entity-unknown", "Selected archive parent no longer exists.")
            locator = f'{inline_parent} [data-act="downloadFile"][data-id={json.dumps(file_id)}]'
            if await _step(page.locator(locator).count(), "checking selected inline control") != 1:
                raise CampusError("entity-unknown", "Selected archive control no longer exists.")
        fetched = await _step(
            resolution.transfer(
                page,
                OfficialAttachmentTarget(file_id, "archive", post_id, locator, None),
                RequestPolicy(row["filename"]),
                output_dir,
                max_bytes=MAX_ATTACHMENT_BYTES,
            ),
            "fetching selected archive attachment",
        )
        try:
            published = (
                adopt_attachment(
                    fetched.temp_path, output_dir, course_root, row["filename"], fetched.size_bytes, fetched.sha256
                )
                if course_root is not None
                else None
            )
            if published is None:
                published = publish_attachment(
                    fetched.temp_path, output_dir, row["filename"], fetched.sha256, fetched.size_bytes
                )
            write_receipt(
                root,
                entity_id,
                output_dir,
                published.path,
                fetched.size_bytes,
                fetched.sha256,
                fetched.media_type,
                course_root if published.outcome == "adopted" else None,
            )
        finally:
            fetched.temp_path.unlink(missing_ok=True)
        return _result(
            row, str(published.path), fetched.size_bytes, fetched.sha256, published.outcome, resolution.provenance
        )


def _stale_course(course: dict[str, Any]) -> str:
    # Missing fields print as None: this is the wording the cache warnings always had.
    return (
        f"Warning: Material cache stale for {course.get('label') or course.get('course_id')} "
        f"({course.get('course_id')}): {course.get('reason')}."
    )


_warnings = partial(
    cache_warning_lines,
    unknown_enrollment="Warning: Enrollment unknown; materials may be stale.",
    stale_course=_stale_course,
)


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    if command == "sync.materials":
        if not any(key in result for key in ("courses", "materials", "failed_courses", "catalog")):
            return []
        count, courses = result.get("materials", 0), result.get("courses", 0)
        lines = _wrap_indented(
            f"Synced {courses} {'course' if courses == 1 else 'courses'}, "
            f"{count} {'material' if count == 1 else 'materials'}.",
            width,
        )
        catalog = result.get("catalog") or {}
        return lines + _warnings(catalog.get("enrollment_state"), result.get("failed_courses"), width)
    if command == "materials.download" and isinstance(result.get("material"), dict):
        material = result["material"]
        label = {
            "saved": "Saved",
            "reused": "Reused",
            "adopted": "Adopted",
            "skipped-existing": "Skipped existing",
        }.get(material.get("outcome"))
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
    lines = _wrap_indented(f"{count} {'material' if count == 1 else 'materials'} (updated {stamp})", width)
    lines.extend(_warnings(cache.get("enrollment_state"), cache.get("failed_courses"), width))
    if not rows:
        return lines + _wrap_indented("No cached materials match this selection.", width)
    last_course = None
    for number, row in enumerate(rows, 1):
        course = row["course"]
        if course["id"] != last_course:
            lines.append("")
            lines.extend(_wrap_indented(f"Course: {course['label']} ({course['id']})", width))
            last_course = course["id"]
        lines.extend(_wrap_indented(f"Archive: {row['archive_entry']['title']}", width, indent="  "))
        lines.extend(_wrap_indented(f"File: {row['filename']}", width, indent="  "))
        lines.extend(_wrap_indented(f"Downloadable: {'yes' if row['downloadable'] else 'no'}", width, indent="  "))
        lines.append(f"  {number}. {row['entity_id']}")
    return lines
