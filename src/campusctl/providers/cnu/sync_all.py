"""One guarded CNU session for serial, course-bound domain collection."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

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
from campusctl.providers.cnu.course_context import CourseSelection, _css_string, bind_on_commit
from campusctl.providers.cnu.courses import COURSE_LINK_SELECTOR, EXTRACT_COURSES_JS, parse_courses
from campusctl.providers.cnu.login import MY_LECTURE_URL, ensure_logged_in
from campusctl.providers.cnu.roster_diagnostics import (
    capture_roster_failure,
    start_roster_requests,
    stop_roster_requests,
)
from campusctl.providers.cnu.sync import COURSE_ROOM_URL_ANCHOR, _merge_health, collect_lectures_rows
from campusctl.providers.cnu.sync import _course_failure as lecture_failure
from campusctl.providers.cnu.ui_policy import (
    UiRequestDiagnostics,
    UiRequestPolicy,
    install_ui_request_interceptor,
)

_SECTION_PATH = {
    "lectures": "/std/course",
    "assignments": "/std/task",
    "notices": "/std/notice",
    "materials": "/std/archive",
}
_SELECTION_PATH = "/api/v1/course/addSessionCourseInfo"
_TOPBAR_COURSE_JS = """() => {
    const current = document.querySelector('#topbarCurrentLecture');
    const name = current?.textContent?.replace(/\\s+/g, '').trim();
    const matches = [...document.querySelectorAll('#topbarLectureDropdown a[data-act="changeLecture"][data-courseid]')]
        .filter(link => link.textContent.replace(/\\s+/g, '').trim() === name);
    return matches.length === 1 ? matches[0].getAttribute('data-courseid') : null;
}"""


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
    suppressed_count: int = 0
    suppressed_reasons: dict[str, int] = field(default_factory=dict)

    def fail(self, domain: str, course: dict[str, Any], error: Exception | None = None) -> None:
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
        if domain == "lectures":
            self.errors.append(lecture_failure(course))
        elif domain == "assignments":
            self.errors.append(assignments._course_failure(course, reason))
        elif domain == "notices":
            self.errors.append(notices._error(reason))
        else:
            self.errors.append(
                error if isinstance(error, CampusError) and error.code == reason else materials._failure(reason, course)
            )


def _policies(domains: tuple[str, ...], reviewed_policy: Mapping[str, Any] | None = None) -> dict[str, UiRequestPolicy]:
    from campusctl.commands.assignments import CAPABILITY as ASSIGNMENTS
    from campusctl.commands.materials import CAPABILITY as MATERIALS
    from campusctl.commands.notices import CAPABILITY as NOTICES
    from campusctl.commands.notices import LECTURES_SYNC_POLICY

    reviewed = {
        "lectures": LECTURES_SYNC_POLICY,
        "assignments": ASSIGNMENTS["policy"],
        "notices": NOTICES["policy"],
        "materials": MATERIALS["policy"],
    }
    if reviewed_policy is not None:
        if len(domains) != 1:
            raise ValueError("A reviewed policy override is limited to one domain")
        reviewed[domains[0]] = reviewed_policy
    policies = {domain: UiRequestPolicy.from_reviewed_config(reviewed[domain]) for domain in domains}
    if reviewed_policy is not None:
        domain = domains[0]
        policy = policies[domain]
        if domain == "assignments" and (
            not policy.approved or not any(route.operation == "assignments.sync" for route in policy.routes)
        ):
            raise CampusError("policy-unapproved", "Assignment request policy is not approved.", None, "user-action")
        if not policy.approved and domain == "notices":
            raise CampusError("policy-blocked", "The reviewed notice policy is unavailable.", None, "error")
        if not policy.approved and domain == "materials":
            raise CampusError(
                "policy-blocked",
                "Materials sync policy has not been approved.",
                "Use only an owner-reviewed operation.",
                "error",
            )
    if any(not policy.approved for policy in policies.values()):
        raise CampusError("policy-blocked", "A requested sync policy is not approved.", None, "error")
    return policies


async def _settle(page: Any, guard: Any) -> None:
    await browser.bounded(
        page.wait_for_load_state("networkidle"), browser.PROTOCOL_TIMEOUT_SECONDS, "settling guarded page"
    )
    guard.raise_if_denied()


async def _roster(page: Any, guard: Any, root: Path, headless: bool, domain: str) -> list[dict[str, Any]]:
    trace = start_roster_requests(page, headless=headless)
    try:
        try:
            guard.arm_roster(frame=page.main_frame, document_url=page.main_frame.url)
            with browser.profile_span("roster", domain=domain):
                with browser.profile_span("document-commit", domain=domain):
                    await browser.bounded(
                        page.goto(MY_LECTURE_URL, wait_until="domcontentloaded"),
                        browser.PROTOCOL_TIMEOUT_SECONDS,
                        "opening guarded course roster",
                    )
                trace.step = "wait"
                with browser.profile_span("dom-ready", domain=domain):
                    await browser.bounded(
                        page.wait_for_selector(COURSE_LINK_SELECTOR, state="attached"),
                        browser.PROTOCOL_TIMEOUT_SECONDS,
                        "waiting for course roster",
                    )
                await _settle(page, guard)
                guard.bind_roster(frame=page.main_frame, document_url=page.main_frame.url)
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
                guard.raise_if_denied()
                return roster
        except Exception as error:
            if isinstance(error, CampusError) and error.code == "policy-blocked":
                raise
            guard.raise_if_denied()
            await capture_roster_failure(
                page, operation=f"{domain}.sync", step=trace.step, elapsed_s=trace.elapsed_s, root=root
            )
            raise CampusError(
                "course-discovery-failed", "Enrolled courses could not be discovered.", "Retry the sync.", "error"
            ) from None
    finally:
        stop_roster_requests(page)


async def _select_course(page: Any, guard: Any, course: dict[str, Any], ordinal: int, domain: str) -> CourseSelection:
    await _settle(page, guard)
    frame = page.main_frame
    roster_url = frame.url
    epoch = guard.arm_selection(frame=frame, document_url=roster_url)
    sequence = 0
    requests: list[tuple[int, Any]] = []
    documents: list[tuple[int, Any]] = []
    responses: list[Any] = []
    committed = False
    captured: asyncio.Future[tuple[Any, Any, Any]] = asyncio.get_running_loop().create_future()

    def on_request(request: Any) -> None:
        nonlocal sequence
        sequence += 1
        path = urlsplit(request.url).path
        if path == _SELECTION_PATH:
            requests.append((sequence, request))
        elif path == "/std/lecture" and request.resource_type == "document" and request.frame is frame:
            documents.append((sequence, request))

    def on_response(response: Any) -> None:
        if urlsplit(response.request.url).path == _SELECTION_PATH:
            responses.append(response)

    def on_navigate(committed_frame: Any) -> None:
        nonlocal committed
        if committed_frame is frame and urlsplit(frame.url).path == "/std/lecture":
            committed = True

    async def capture(request: Any, response: Any) -> None:
        payload = await browser.bounded(response.json(), browser.PROTOCOL_TIMEOUT_SECONDS, "decoding course selection")
        if not captured.done():
            captured.set_result((request, response, payload))

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("framenavigated", on_navigate)
    guard.capture_response(_SELECTION_PATH, capture)
    try:
        with browser.profile_span("course-selection", domain=domain, course=ordinal):
            await browser.bounded(
                page.click(f'[data-act="moveLecture"][data-courseid={_css_string(course["course_id"])}]'),
                browser.PROTOCOL_TIMEOUT_SECONDS,
                "selecting a roster course",
            )
        browser.profile_count("course_selections")
        request, response, payload = await browser.bounded(
            captured, browser.PROTOCOL_TIMEOUT_SECONDS, "waiting for course selection proof"
        )
        await _settle(page, guard)
        await browser.bounded(
            page.wait_for_selector('a[href="/std/course"]', state="attached"),
            browser.PROTOCOL_TIMEOUT_SECONDS,
            "waiting for selected course menu",
        )
        topbar_id = await browser.bounded(
            page.evaluate(_TOPBAR_COURSE_JS), browser.PROTOCOL_TIMEOUT_SECONDS, "checking selected course topbar"
        )
        if (
            not committed
            or len(requests) != 1
            or requests[0][1] is not request
            or len(responses) != 1
            or responses[0] is not response
            or len(documents) != 1
            or requests[0][0] >= documents[0][0]
            or urlsplit(frame.url).path != "/std/lecture"
        ):
            raise ValueError("Course selection was stale or duplicated")
        selection = CourseSelection.from_response(
            course_id=course["course_id"],
            roster_course_id=course["course_id"],
            topbar_course_id=topbar_id,
            ordinal=ordinal,
            epoch=epoch.selection_epoch,
            response_identity=requests[0][0],
            document_identity=documents[0][0],
            response_status=response.status,
            response_body=payload,
            response_count=len(responses),
            document_committed=committed,
        )
        guard.bind_selection(selection, frame=frame, document_url=frame.url)
        guard.raise_if_denied()
        return selection
    finally:
        guard.stop_capture()
        if not captured.done():
            captured.cancel()
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
        page.remove_listener("framenavigated", on_navigate)


async def _return_to_roster(page: Any, guard: Any, selection: CourseSelection) -> None:
    await _settle(page, guard)
    frame = page.main_frame
    previous = frame.url
    if guard.epoch.phase != "bound" or guard.epoch.document_url != previous:
        raise ValueError("Selected course document is no longer bound")
    guard.quarantine()
    guard.activate(
        guard.epoch.policy,
        operation=guard.epoch.operation,
        selection=selection,
        frame=frame,
        document_url=previous,
        navigation_path="/std/myLecture",
        settled=True,
    )
    async with bind_on_commit(page, guard, frame=frame, expected_path="/std/myLecture", selection=selection):
        await browser.bounded(
            page.goto(MY_LECTURE_URL, wait_until="domcontentloaded", referer=previous),
            browser.PROTOCOL_TIMEOUT_SECONDS,
            "returning to the guarded roster",
        )
    await _settle(page, guard)
    guard.quarantine()


async def _section(
    page: Any,
    guard: Any,
    selection: CourseSelection,
    domain: str,
    policy: UiRequestPolicy,
    course: dict[str, Any],
    courses: list[dict[str, Any]],
    todo: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    await _settle(page, guard)
    frame = page.main_frame
    if guard.epoch.phase != "bound" or guard.epoch.document_url != frame.url:
        raise ValueError("Previous section is not bound")
    guard.quarantine()
    guard.activate(
        policy,
        operation=f"{domain}.sync",
        selection=selection,
        frame=frame,
        document_url=frame.url,
        navigation_path=_SECTION_PATH[domain],
        settled=True,
    )
    with browser.profile_span("document-commit", domain=domain, course=selection.ordinal):
        if domain == "lectures":
            async with bind_on_commit(page, guard, frame=frame, expected_path="/std/course", selection=selection):
                await browser.bounded(
                    page.click(COURSE_ROOM_URL_ANCHOR), browser.PROTOCOL_TIMEOUT_SECONDS, "opening lecture section"
                )
            return await collect_lectures_rows(page, course, selection, guard)
        if domain == "assignments":
            capture = assignments.arm_assignment_capture(page, guard)
            await assignments.open_assignment_section(
                page, guard, lambda: page.click('a[href="/std/task"]'), capture=capture, selection=selection
            )
            return await assignments.collect_assignment_rows(page, course, selection, guard, capture=capture)
        if domain == "notices":
            capture = notices.arm_notice_capture(page, guard)
            await notices.open_notice_section(
                page,
                guard,
                lambda: page.goto(
                    urlsplit(frame.url)._replace(path="/std/notice", query="", fragment="").geturl(),
                    referer=frame.url,
                    wait_until="domcontentloaded",
                ),
                capture=capture,
                selection=selection,
            )
            return await notices.collect_notice_rows(
                page, course, selection, guard, capture=capture, courses=courses, todo_rows=todo[course["course_id"]]
            )
        capture = await materials.arm_materials_capture(page, guard)

        async def open_archive() -> None:
            if urlsplit(frame.url).path == "/std/todo":
                # A failed to-do prerequisite skips the board; the to-do has no archive menu.
                previous = frame.url
                await page.goto(
                    urlsplit(previous)._replace(path="/std/archive", query="", fragment="").geturl(),
                    referer=previous,
                    wait_until="domcontentloaded",
                )
            else:
                await page.click('a[href="/std/archive"]')

        await materials.open_materials_section(page, guard, open_archive, capture=capture, selection=selection)
        return await materials.collect_materials_rows(page, course, selection, guard, capture=capture)


async def _todo(
    page: Any,
    guard: Any,
    selection: CourseSelection,
    policy: UiRequestPolicy,
    courses: list[dict[str, Any]],
    selected_course_id: str | None,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    await _settle(page, guard)
    frame = page.main_frame
    guard.quarantine()
    guard.activate(
        policy,
        operation="notices.sync",
        selection=selection,
        frame=frame,
        document_url=frame.url,
        navigation_path="/std/todo",
        settled=True,
    )
    capture = await notices.open_notice_todo(page, guard, selection=selection)
    return await notices.collect_notice_todo(
        page, guard, courses, capture=capture, selected_course_id=selected_course_id
    )


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
        **(
            {"suppressed_count": stage.suppressed_count, "suppressed_reasons": stage.suppressed_reasons}
            if domain == "assignments"
            else {}
        ),
    }


async def sync_all(
    config: dict[str, Any],
    root: Path,
    domains: tuple[str, ...],
    course_id: str | None,
    *,
    headless: bool = False,
    course_snapshot: dict[str, Any] | None = None,
    reviewed_policy: Mapping[str, Any] | None = None,
) -> dict[str, DomainOutcome]:
    """Collect once and publish only after the guarded browser has closed under its lock."""
    from campusctl.catalog_view import DOMAINS, assert_course_snapshot_current

    if (
        not domains
        or len(set(domains)) != len(domains)
        or tuple(domain for domain in DOMAINS if domain in domains) != domains
    ):
        raise CampusError(
            "unsupported-domain", "Unknown or duplicate sync domain.", "Use a supported --only subset.", "user-action"
        )
    policies = _policies(domains, reviewed_policy)
    staged = {domain: _Stage() for domain in domains}
    diagnostics = UiRequestDiagnostics()
    operation = f"{domains[0]}.sync"
    roster: list[dict[str, Any]] | None = None
    selected_courses: list[dict[str, Any]] = []
    guard: Any = None
    discovery_failed = False
    with browser.session_lock(config, data_dir=root):
        if course_snapshot is not None:
            assert_course_snapshot_current(root, course_snapshot)
        try:
            async with browser.open_session(
                config, data_dir=root, headless=headless, operation=operation, require_owned_page=True
            ) as session:
                page = session.page
                with browser.profile_span("auth", domain=domains[0]):
                    await ensure_logged_in(
                        page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR
                    )
                await browser.settle_sso_popups(session, domain=domains[0])
                guard = await install_ui_request_interceptor(
                    session.context,
                    policies[domains[0]],
                    operation=operation,
                    diagnostics=diagnostics,
                    require_selection=True,
                )
                try:
                    roster = await _roster(page, guard, root, headless, domains[0])
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
                        "The requested course ID was not found among enrolled courses.",
                        "Check the course ID and retry.",
                        "user-action",
                    )
                previous_selection: CourseSelection | None = None
                todo_rows: dict[str, list[dict[str, Any]]] = {}
                todo_failures: set[str] = set()
                todo_completed = False
                for ordinal, course in enumerate(selected_courses, 1):
                    if previous_selection is not None:
                        try:
                            await _return_to_roster(page, guard, previous_selection)
                        except Exception as error:
                            guard.raise_if_denied()
                            if isinstance(error, CampusError) and error.code == "policy-blocked":
                                raise
                            raise CampusError(
                                "course-sync-failed",
                                "The guarded course roster could not be rebound.",
                                "Retry sync after the course page settles.",
                                "error",
                            ) from None
                    try:
                        selection = await _select_course(page, guard, course, ordinal, domains[0])
                    except Exception as error:
                        guard.raise_if_denied()
                        if isinstance(error, CampusError) and error.code == "policy-blocked":
                            raise
                        # Until the response and committed entry agree, no course owns this epoch.
                        # Do not downgrade an unbound selection to publishable stale catalog rows.
                        raise CampusError(
                            "course-sync-failed",
                            "Course selection could not be validated.",
                            "Check the course in the LMS and retry sync.",
                            "error",
                        ) from None
                    previous_selection = selection
                    for domain in domains:
                        if domain == "notices" and not todo_completed:
                            try:
                                todo_rows, todo_failures = await _todo(
                                    page, guard, selection, policies["notices"], roster, course_id
                                )
                            except Exception as error:
                                guard.raise_if_denied()
                                if isinstance(error, CampusError) and error.code == "policy-blocked":
                                    raise
                                if guard.epoch.phase != "bound" or guard.epoch.document_url != page.main_frame.url:
                                    raise CampusError(
                                        "course-sync-failed",
                                        "The global to-do document lost its course binding.",
                                        "Retry sync after the course page settles.",
                                        "error",
                                    ) from None
                                todo_failures = {item["course_id"] for item in selected_courses}
                            todo_completed = True
                        if domain == "notices" and course["course_id"] in todo_failures:
                            staged[domain].fail(domain, course)
                            continue
                        before_suppressed = diagnostics.suppressed_count
                        before_reasons = dict(diagnostics.suppressed_reasons)
                        try:
                            rows = await _section(
                                page, guard, selection, domain, policies[domain], course, roster, todo_rows
                            )
                            guard.raise_if_denied()
                        except Exception as error:
                            guard.raise_if_denied()
                            if isinstance(error, CampusError) and error.code == "policy-blocked":
                                raise
                            if guard.epoch.phase != "bound" or guard.epoch.document_url != page.main_frame.url:
                                # Unproven navigation is a run-wide security failure, never stale success.
                                raise CampusError(
                                    "course-sync-failed",
                                    "A section document lost its course binding.",
                                    "Retry sync after the course page settles.",
                                    "error",
                                ) from None
                            staged[domain].fail(domain, course, error)
                        else:
                            staged[domain].successful.add(course["course_id"])
                            staged[domain].rows.extend(rows)
                        finally:
                            if domain == "assignments":
                                staged[domain].suppressed_count += diagnostics.suppressed_count - before_suppressed
                                for reason, count in diagnostics.suppressed_reasons.items():
                                    difference = count - before_reasons.get(reason, 0)
                                    if difference:
                                        reasons = staged[domain].suppressed_reasons
                                        reasons[reason] = reasons.get(reason, 0) + difference
                await _settle(page, guard)
                guard.raise_if_denied()
        finally:
            if guard is not None:
                try:
                    guard.raise_if_denied()
                finally:
                    try:
                        # Browser cleanup has ended: the closed local context or stopped CDP driver
                        # already detached its client-side route; other unroute failures are fatal.
                        await guard.close()
                    except PlaywrightError as error:
                        message = str(error).casefold()
                        if (
                            "target page, context or browser has been closed" not in message
                            and "connection closed" not in message
                        ):
                            raise
                guard.raise_if_denied()
            if discovery_failed and course_id is None:
                for domain in domains:
                    if domain == "lectures":
                        from campusctl.providers.cnu.sync import _mark_enrollment_unknown

                        _mark_enrollment_unknown(root)
                    else:
                        mark_enrollment_unknown(domain, root)
        if domains == ("assignments",):
            staged["assignments"].suppressed_count = diagnostics.suppressed_count
            staged["assignments"].suppressed_reasons = dict(diagnostics.suppressed_reasons)
        if roster is None:
            raise CampusError("course-discovery-failed", "Course roster unavailable.", None, "error")
        prepared: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for domain in domains:
            try:
                prepared[domain] = _stage_catalog(domain, staged[domain], roster, course_id, root)
            except CampusError as error:
                outcomes = {pending: DomainOutcome(status="not-started") for pending in domains}
                outcomes[domain] = DomainOutcome(errors=[error], status=error.status)
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
                    f"The {domain} catalog could not be published.",
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
    reviewed_policy: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[CampusError]]:
    """Preserve standalone provider signatures while sharing the guarded traversal."""
    outcome = (await sync_all(config, root, (domain,), course_id, headless=headless, reviewed_policy=reviewed_policy))[
        domain
    ]
    if outcome.status == "error" and outcome.errors and not outcome.result:
        raise outcome.errors[0]
    return outcome.result, outcome.errors
