"""One CNU session for serial, course-bound domain collection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from campusctl import browser
from campusctl.catalog import _validate_catalog as validate_lecture_catalog
from campusctl.catalog import catalog_path, merge_catalog, read_catalog, write_catalog
from campusctl.domain_catalog import _validate_catalog as validate_domain_catalog
from campusctl.domain_catalog import (
    domain_catalog_path,
    mark_enrollment_unknown,
    merge_domain_catalog,
    read_domain_catalog,
    write_domain_catalog,
)
from campusctl.envelope import CampusError
from campusctl.providers.cnu import assignments, materials, notices
from campusctl.providers.cnu.course_context import _css_string, _wait_for_topbar_course_id
from campusctl.providers.cnu.courses import COURSE_LINK_SELECTOR, EXTRACT_COURSES_JS, parse_courses
from campusctl.providers.cnu.login import MY_LECTURE_URL, ensure_logged_in
from campusctl.providers.cnu.roster_diagnostics import (
    capture_roster_failure,
    start_roster_requests,
    stop_roster_requests,
)
from campusctl.providers.cnu.sync import COURSE_ROOM_URL_ANCHOR, _merge_health, collect_lectures_rows

_SECTION_PATH = {
    "lectures": "/std/course",
    "assignments": "/std/task",
    "notices": "/std/notice",
    "materials": "/std/archive",
}

_SELECTION_PATH = "/api/v1/course/addSessionCourseInfo"


def _error_detail(error: Exception) -> str:
    """Expose a stable error kind without leaking browser URLs or response payloads."""
    if isinstance(error, CampusError):
        if error.code == "browser-timeout":
            match = re.fullmatch(r"Timed out while ([A-Za-z ]+)\.", error.message)
            if match is not None:
                return f"{match[1]} timed out"
        return error.code
    return type(error).__name__


@dataclass(slots=True)
class DomainOutcome:
    result: dict[str, Any] = field(default_factory=dict)
    errors: list[CampusError] = field(default_factory=list)
    status: str | None = None


@dataclass(slots=True)
class _Stage:
    rows: list[dict[str, Any]] = field(default_factory=list)
    successful: set[str] = field(default_factory=set)
    failures: list[dict[str, str]] = field(default_factory=list)
    errors: list[CampusError] = field(default_factory=list)

    def fail(self, domain: str, course: dict[str, Any], step: str, error: Exception | None = None) -> None:
        if course["course_id"] in self.successful or any(
            failed["course_id"] == course["course_id"] for failed in self.failures
        ):
            return
        reason = "course-sync-failed"
        if isinstance(error, CampusError) and error.code in {
            "item-identity-missing",
            "notice-identity-ambiguous",
            "notice-board-paginated",
        }:
            reason = error.code
        self.failures.append({"course_id": course["course_id"], "label": course["label"], "reason": reason})
        detail = _error_detail(error) if error is not None else "prerequisite unavailable"
        self.errors.append(
            CampusError(
                reason,
                f"{domain} sync failed for {course['label']} ({course['course_id']}) at {step}: {detail}",
                "Check the course in the LMS and retry sync.",
                "error",
            )
        )


async def _settle(page: Any) -> None:
    await browser.bounded(
        page.wait_for_load_state("networkidle"), browser.PROTOCOL_TIMEOUT_SECONDS, "settling page requests"
    )


async def _roster(page: Any, root: Path, headless: bool, domain: str) -> list[dict[str, Any]]:
    trace = start_roster_requests(page, headless=headless)
    try:
        try:
            with browser.profile_span("roster", domain=domain):
                with browser.profile_span("document-commit", domain=domain):
                    await browser.bounded(
                        page.goto(MY_LECTURE_URL, wait_until="domcontentloaded"),
                        browser.PROTOCOL_TIMEOUT_SECONDS,
                        "opening course roster",
                    )
                trace.step = "wait"
                with browser.profile_span("dom-ready", domain=domain):
                    await browser.bounded(
                        page.wait_for_selector(COURSE_LINK_SELECTOR, state="attached"),
                        browser.PROTOCOL_TIMEOUT_SECONDS,
                        "waiting for course roster",
                    )
                await _settle(page)
                trace.step = "evaluate"
                with browser.profile_span("extract", domain=domain):
                    raw = await browser.bounded(
                        page.evaluate(EXTRACT_COURSES_JS), browser.PROTOCOL_TIMEOUT_SECONDS, "extracting course roster"
                    )
                trace.step = "parse"
                if not isinstance(raw, list) or not raw or any(not isinstance(item, dict) for item in raw):
                    raise ValueError("Roster was not completely rendered")
                roster = parse_courses(raw)
                if len(roster) != len(raw):
                    raise ValueError("Roster contains duplicate or unaddressable courses")
                return roster
        except Exception as error:
            await capture_roster_failure(
                page, operation=f"{domain}.sync", step=trace.step, elapsed_s=trace.elapsed_s, root=root
            )
            raise CampusError(
                "course-discovery-failed",
                f"{domain} roster {trace.step}: {_error_detail(error)}",
                "Retry the sync.",
                "error",
            ) from None
    finally:
        stop_roster_requests(page)


async def _select_course(page: Any, course: dict[str, Any], ordinal: int, domain: str) -> None:
    """Prove the selected course from its committed entry page, not the navigating POST body."""
    await _settle(page)
    frame = page.main_frame
    sequence = 0
    requests: list[tuple[int, Any]] = []
    documents: list[tuple[int, Any]] = []
    commits = 0

    def on_request(request: Any) -> None:
        nonlocal sequence
        sequence += 1
        path = urlsplit(request.url).path
        if path == _SELECTION_PATH and request.method == "POST":
            requests.append((sequence, request))
        elif path == "/std/lecture" and request.resource_type == "document" and request.frame is frame:
            documents.append((sequence, request))

    def on_navigate(committed_frame: Any) -> None:
        nonlocal commits
        if committed_frame is frame and urlsplit(frame.url).path == "/std/lecture":
            commits += 1

    page.on("request", on_request)
    page.on("framenavigated", on_navigate)
    try:
        with browser.profile_span("course-selection", domain=domain, course=ordinal):
            await browser.bounded(
                page.click(f'[data-act="moveLecture"][data-courseid={_css_string(course["course_id"])}]'),
                browser.PROTOCOL_TIMEOUT_SECONDS,
                "selecting a roster course",
            )
        browser.profile_count("course_selections")
        await _settle(page)
        await browser.bounded(
            page.wait_for_selector('a[href="/std/course"]', state="attached"),
            browser.PROTOCOL_TIMEOUT_SECONDS,
            "waiting for selected course menu",
        )
        topbar_id = await _wait_for_topbar_course_id(page)
        if (
            commits != 1
            or len(requests) != 1
            or len(documents) != 1
            or requests[0][0] >= documents[0][0]
            or urlsplit(frame.url).path != "/std/lecture"
            or topbar_id != course["course_id"]
        ):
            raise ValueError("Course entry does not match its roster selection")
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("framenavigated", on_navigate)


async def _return_to_roster(page: Any) -> None:
    await _settle(page)
    previous = page.main_frame.url
    await browser.bounded(
        page.goto(MY_LECTURE_URL, wait_until="domcontentloaded", referer=previous),
        browser.PROTOCOL_TIMEOUT_SECONDS,
        "returning to the roster",
    )
    await _settle(page)


async def _section(
    page: Any,
    ordinal: int,
    domain: str,
    course: dict[str, Any],
    courses: list[dict[str, Any]],
    todo: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    await _settle(page)
    if domain == "lectures":
        with browser.profile_span("document-commit", domain=domain, course=ordinal):
            await browser.bounded(
                page.click(COURSE_ROOM_URL_ANCHOR), browser.PROTOCOL_TIMEOUT_SECONDS, "opening lecture section"
            )
        return await collect_lectures_rows(page, course, ordinal=ordinal)
    if domain == "assignments":
        capture = assignments.arm_assignment_capture(page)
        with browser.profile_span("document-commit", domain=domain, course=ordinal):
            await assignments.open_assignment_section(page, lambda: page.click('a[href="/std/task"]'), capture=capture)
        return await assignments.collect_assignment_rows(page, course, capture=capture)
    if domain == "notices":
        await browser.bounded(
            page.wait_for_selector('a[href="/std/notice"]', state="attached"),
            browser.PROTOCOL_TIMEOUT_SECONDS,
            "waiting for notice menu",
        )
        capture = notices.arm_notice_capture(page)
        with browser.profile_span("document-commit", domain=domain, course=ordinal):
            await notices.open_notice_section(page, lambda: page.click('a[href="/std/notice"]'), capture=capture)
        return await notices.collect_notice_rows(
            page, course, capture=capture, courses=courses, todo_rows=todo[course["course_id"]]
        )
    if await _wait_for_topbar_course_id(page) != course["course_id"]:
        raise ValueError("Archive menu belongs to another course")
    await browser.bounded(
        page.wait_for_selector('a[href="/std/archive"]', state="attached"),
        browser.PROTOCOL_TIMEOUT_SECONDS,
        "waiting for archive menu",
    )
    capture = await materials.arm_materials_capture(page)
    with browser.profile_span("document-commit", domain=domain, course=ordinal):
        await materials.open_materials_section(page, lambda: page.click('a[href="/std/archive"]'), capture=capture)
    return await materials.collect_materials_rows(page, course, capture=capture)


async def _todo(
    page: Any,
    courses: list[dict[str, Any]],
    selected_course_id: str | None,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    await _settle(page)
    capture = await notices.open_notice_todo(page)
    return await notices.collect_notice_todo(page, courses, capture=capture, selected_course_id=selected_course_id)


def _stage_catalog(
    domain: str, stage: _Stage, roster: list[dict[str, Any]], selected_course_id: str | None, root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    if domain == "lectures":
        target = catalog_path(root)
        previous = read_catalog(target) if target.exists() else None
        if selected_course_id is None and not stage.failures and previous is not None:
            enrolled = {course["course_id"] for course in roster}
            previous = {
                **previous,
                "courses": [course for course in previous["courses"] if course["course_id"] in enrolled],
                "lectures": [row for row in previous["lectures"] if row["course"]["id"] in enrolled],
            }
        successful = [course for course in roster if course["course_id"] in stage.successful]
        with browser.profile_span("merge", domain=domain):
            merged = merge_catalog(
                previous, successful, stage.rows, failed_course_ids={entry["course_id"] for entry in stage.failures}
            )
            _merge_health(merged, previous, roster, stage.failures, selected_course_id)
    else:
        target = domain_catalog_path(domain, root)
        try:
            previous = read_domain_catalog(domain, target)
        except CampusError as error:
            if error.code != "catalog-missing":
                raise
            previous = None
        with browser.profile_span("merge", domain=domain):
            merged = merge_domain_catalog(
                domain,
                previous,
                roster,
                stage.rows,
                successful_course_ids=stage.successful,
                failed_courses=stage.failures,
                selected_course_id=selected_course_id,
            )
    if domain == "lectures":
        validate_lecture_catalog(merged, catalog_path(root))
    else:
        validate_domain_catalog(domain, merged, domain_catalog_path(domain, root))
    json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    if domain == "notices":
        result_failures = merged["failed_courses"]
    elif domain == "lectures":
        result_failures = [{"course_id": item["course_id"], "label": item["label"]} for item in stage.failures]
    else:
        result_failures = stage.failures
    return merged, {
        "courses": len(stage.successful),
        domain: len(stage.rows),
        **(
            {"incomplete": sum(row["completion"] == "incomplete" for row in stage.rows)} if domain == "lectures" else {}
        ),
        "failed_courses": result_failures,
        "catalog": {"generated_at": merged["generated_at"], "enrollment_state": merged["enrollment_state"]},
    }


async def sync_all(
    config: dict[str, Any],
    root: Path,
    domains: tuple[str, ...],
    course_id: str | None,
    *,
    headless: bool = False,
    course_snapshot: dict[str, Any] | None = None,
) -> dict[str, DomainOutcome]:
    """Collect once and publish only after the browser has closed under its lock."""
    from campusctl.catalog_view import DOMAINS, assert_course_snapshot_current

    if (
        not domains
        or len(set(domains)) != len(domains)
        or tuple(domain for domain in DOMAINS if domain in domains) != domains
    ):
        raise CampusError(
            "unsupported-domain", "Unknown or duplicate sync domain.", "Use a supported --only subset.", "user-action"
        )
    staged = {domain: _Stage() for domain in domains}
    operation = f"{domains[0]}.sync"
    roster: list[dict[str, Any]] | None = None
    session_step = "browser open"
    discovery_failed = False
    with browser.session_lock(config, data_dir=root):
        if course_snapshot is not None:
            try:
                assert_course_snapshot_current(root, course_snapshot)
            except CampusError as error:
                raise CampusError(
                    error.code,
                    f"{domains[0]} catalog snapshot: {_error_detail(error)}",
                    error.remediation,
                    error.status,
                ) from None
        try:
            async with browser.open_session(
                config, data_dir=root, headless=headless, operation=operation, require_owned_page=True
            ) as session:
                page = session.page
                session_step = "authentication"
                with browser.profile_span("auth", domain=domains[0]):
                    await ensure_logged_in(
                        page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR
                    )
                session_step = "SSO settling"
                await browser.settle_sso_popups(session, domain=domains[0])
                session_step = "roster discovery"
                try:
                    roster = await _roster(page, root, headless, domains[0])
                except CampusError as error:
                    if error.code == "course-discovery-failed":
                        discovery_failed = True
                    raise
                selected_courses = [
                    course for course in roster if course_id is None or course["course_id"] == course_id
                ]
                if not selected_courses and course_id is not None:
                    raise CampusError(
                        "course-not-found",
                        f"{domains[0]} roster lookup: the requested course ID was not found among enrolled courses.",
                        "Check the course ID and retry.",
                        "user-action",
                    )
                todo_rows: dict[str, list[dict[str, Any]]] = {}
                todo_failures: set[str] = set()
                if "notices" in domains:
                    session_step = "todo snapshot"
                    try:
                        todo_rows, todo_failures = await _todo(page, roster, course_id)
                    except Exception:
                        # A failed global read cannot attest to any selected notice board.
                        todo_failures = {item["course_id"] for item in selected_courses}
                    try:
                        await _return_to_roster(page)
                    except Exception as error:
                        raise CampusError(
                            "course-sync-failed",
                            f"{domains[0]} roster return: {_error_detail(error)}",
                            "Retry sync after the course page settles.",
                            "error",
                        ) from None
                selected_before = False
                session_step = "course traversal"
                for ordinal, course in enumerate(selected_courses, 1):
                    if selected_before:
                        try:
                            await _return_to_roster(page)
                        except Exception as error:
                            raise CampusError(
                                "course-sync-failed",
                                f"{domains[0]} roster return: {_error_detail(error)}",
                                "Retry sync after the course page settles.",
                                "error",
                            ) from None
                    try:
                        await _select_course(page, course, ordinal, domains[0])
                    except Exception as error:
                        for domain in domains:
                            staged[domain].fail(domain, course, "course selection", error)
                        # No course data is trusted until its committed entry proves identity.
                        # Returning to the roster before the next selection is the recovery gate.
                        selected_before = True
                        continue
                    selected_before = True
                    for domain_index, domain in enumerate(domains):
                        if domain == "notices" and course["course_id"] in todo_failures:
                            staged[domain].fail(domain, course, "todo extraction")
                            continue
                        try:
                            rows = await _section(page, ordinal, domain, course, roster, todo_rows)
                        except Exception as error:
                            try:
                                path = urlsplit(page.main_frame.url).path
                            except Exception:
                                path = ""
                            if path != _SECTION_PATH[domain] and (domain != "lectures" or path != "/std/lecture"):
                                for pending in domains[domain_index:]:
                                    staged[pending].fail(pending, course, "section navigation", error)
                                # The current document cannot establish course identity. Restore
                                # the roster before attempting another enrolled course.
                                break
                            staged[domain].fail(domain, course, "section extraction", error)
                        else:
                            staged[domain].successful.add(course["course_id"])
                            staged[domain].rows.extend(rows)
                session_step = "final page settling"
                await _settle(page)
        except CampusError as error:
            if error.code in {"course-not-found", "course-discovery-failed", "course-sync-failed"}:
                raise
            raise CampusError(
                error.code, f"{domains[0]} {session_step}: {_error_detail(error)}", error.remediation, error.status
            ) from None
        except Exception as error:
            raise CampusError(
                "course-sync-failed",
                f"{domains[0]} {session_step}: {_error_detail(error)}",
                "Check the LMS session and retry sync.",
                "error",
            ) from None
        finally:
            if discovery_failed and course_id is None:
                for domain in domains:
                    if domain == "lectures":
                        from campusctl.providers.cnu.sync import _mark_enrollment_unknown

                        _mark_enrollment_unknown(root)
                    else:
                        mark_enrollment_unknown(domain, root)
        if roster is None:
            raise CampusError("course-discovery-failed", f"{domains[0]} roster unavailable.", None, "error")
        prepared: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for domain in domains:
            try:
                prepared[domain] = _stage_catalog(domain, staged[domain], roster, course_id, root)
            except Exception as error:
                code = error.code if isinstance(error, CampusError) else "course-sync-failed"
                status = error.status if isinstance(error, CampusError) else "error"
                remediation = (
                    error.remediation if isinstance(error, CampusError) else "Check catalog data and retry sync."
                )
                outcomes = {pending: DomainOutcome(status="not-started") for pending in domains}
                outcomes[domain] = DomainOutcome(
                    errors=[
                        CampusError(code, f"{domain} catalog preparation: {_error_detail(error)}", remediation, status)
                    ],
                    status=status,
                )
                return outcomes
        outcomes: dict[str, DomainOutcome] = {}
        for index, domain in enumerate(domains):
            merged, result = prepared[domain]
            try:
                with browser.profile_span("serialize-write", domain=domain):
                    if domain == "lectures":
                        write_catalog(merged, catalog_path(root))
                    else:
                        write_domain_catalog(domain, merged, domain_catalog_path(domain, root))
            except (CampusError, OSError):
                failure = CampusError(
                    "catalog-write-failed",
                    f"{domain} catalog publication failed at serialize-write.",
                    "Check catalog storage and retry sync.",
                    "error",
                )
                outcomes[domain] = DomainOutcome(errors=[failure], status="error")
                outcomes.update({pending: DomainOutcome(status="not-started") for pending in domains[index + 1 :]})
                return outcomes
            outcomes[domain] = DomainOutcome(result, staged[domain].errors)
        return outcomes


async def sync_one(
    config: dict[str, Any],
    root: Path,
    domain: str,
    course_id: str | None,
    *,
    headless: bool = False,
) -> tuple[dict[str, Any], list[CampusError]]:
    """Preserve standalone provider signatures while sharing course traversal."""
    outcome = (await sync_all(config, root, (domain,), course_id, headless=headless))[domain]
    if outcome.status == "error" and outcome.errors and not outcome.result:
        raise outcome.errors[0]
    return outcome.result, outcome.errors
