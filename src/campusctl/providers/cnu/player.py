"""Serial CNU lecture queue, row verification and catalog completion."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.catalog import catalog_path, read_catalog, write_catalog
from campusctl.envelope import CampusError
from campusctl.wait_clock import current_clock

from . import player_preview
from .courses import COURSE_LINK_SELECTOR
from .lectures import LEARNING_ROW_SELECTOR, progress_is_full
from .login import MY_LECTURE_URL, ensure_logged_in
from .media import MEDIA_HANDLERS
from .player_types import MediaKind, _css_string, _PlaybackFailure, _PlayDiagnostic, _watch_time

COURSE_ROOM_SELECTOR = 'a[href="/std/course"]'
COMPLETION_POLL_SECONDS = 600.0
COMPLETION_POLL_INTERVAL_SECONDS = 30.0

READ_ROW_JS = r"""/* campusctl-read-lecture-row */(rowId) => {
    const row = document.getElementById(rowId);
    if (!row) return null;
    const rightColumn = row.querySelector('.col-lg-4.text-end') || row.querySelector('.col-lg-4');
    const progress = rightColumn ? rightColumn.querySelector('span.ms-2') : null;
    return {
        state: row.getAttribute('data-state'),
        moduletype: row.getAttribute('data-moduletype'),
        openyn: row.getAttribute('data-openyn'),
        progress_text: progress ? (progress.textContent || '').trim() : null,
        attendance_counted: (row.textContent || '').includes('출석 미반영') ? false : null
    };
}"""


def _emit_play_diagnostic(diagnostic: _PlayDiagnostic, index: int, reason_code: str) -> None:
    print(diagnostic.record(index + 1, reason_code), file=sys.stderr)


@dataclass(frozen=True, slots=True)
class _LectureItem:
    """One queue item's result. `to_json` is the only conversion to the envelope's dict item."""

    entity_id: str
    outcome: str
    elapsed_seconds: float = 0.0
    watch_time: str | None = None
    provider_state: str | None = None
    replay_requested: bool = False
    player_opened: bool = False
    reason_code: str | None = None
    # Handler failures list the reason right after the outcome; queue-level failures append it.
    reason_after_outcome: bool = False

    def to_json(self) -> dict[str, Any]:
        item: dict[str, Any] = {"entity_id": self.entity_id, "outcome": self.outcome}
        if self.reason_after_outcome and self.reason_code is not None:
            item["reason_code"] = self.reason_code
        item["elapsed_seconds"] = self.elapsed_seconds
        item["watch_time"] = self.watch_time
        item["provider_state"] = self.provider_state
        item["replay_requested"] = self.replay_requested
        item["player_opened"] = self.player_opened
        if not self.reason_after_outcome and self.reason_code is not None:
            item["reason_code"] = self.reason_code
        return item


def _not_started(entity_id: str, replay: bool) -> _LectureItem:
    return _LectureItem(entity_id, "not-started", replay_requested=replay)


def _row_parts(entity_id: str) -> tuple[str, str] | None:
    parts = entity_id.split(":", 2)
    if len(parts) != 3 or parts[0] != "cnu_lecture" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def validate_requested_lectures(
    entity_ids: list[str], lectures: list[dict[str, Any]], *, replay: bool = False
) -> list[dict[str, Any]]:
    """Validate the complete request before any browser session can be opened."""
    by_id: dict[str, dict[str, Any]] = {}
    for lecture in lectures:
        if isinstance(lecture, dict) and isinstance(lecture.get("entity_id"), str):
            by_id.setdefault(lecture["entity_id"], lecture)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity_id in entity_ids:
        lecture = by_id.get(entity_id)
        if lecture is None:
            raise CampusError(
                "lecture-unknown",
                "A requested lecture ID is not present in the local catalog.",
                "Run 'campusctl sync --only lectures' to refresh the catalog.",
            )
        if lecture.get("kind") != "lecture" or lecture.get("moduletype", "LV") != "LV" or _row_parts(entity_id) is None:
            raise CampusError(
                "lecture-unsupported",
                "A requested catalog item is not a supported CNU lecture.",
                "Use an entity ID for a catalog lecture item.",
            )
        if "media" not in lecture:
            raise CampusError(
                "catalog-outdated",
                "The local lecture catalog must be refreshed before playback.",
                "Run 'campusctl sync --only lectures' to refresh the catalog.",
            )
        if lecture.get("open") is False:
            available_from = lecture.get("available_from")
            opening_date = (
                available_from.split("T", 1)[0] if isinstance(available_from, str) and available_from.strip() else None
            )
            message = (
                f"The requested lecture is not open yet; it opens on {opening_date}."
                if opening_date is not None
                else "The requested lecture is not open yet."
            )
            raise CampusError(
                "lecture-not-open",
                message,
                "Run 'campusctl lectures list' to check when the lecture opens, then retry.",
                "user-action",
            )
        if lecture.get("media") not in MEDIA_HANDLERS:
            raise CampusError(
                "lecture-not-playable",
                "Only CNU video and YouTube lectures can be played.",
                "Use 'campusctl lectures list' to inspect the lecture media type.",
            )
        if lecture.get("completion") in {"complete", "recorded"} and not replay:
            raise CampusError(
                "lecture-complete",
                "A requested lecture is already complete or fully watched in the local catalog.",
                "Use 'campusctl lectures list --all' to inspect completed or recorded lectures.",
            )
        if entity_id not in seen:
            selected.append(lecture)
            seen.add(entity_id)
    return selected


async def _read_row(page: Any, row_id: str, timeout: float = PROTOCOL_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    value = await bounded(page.evaluate(READ_ROW_JS, row_id), timeout, "reading the CNU lecture row")
    return value if isinstance(value, dict) else None


async def _enter_course_room(page: Any, course_id: str) -> None:
    await bounded(
        page.wait_for_selector(COURSE_LINK_SELECTOR, state="attached", timeout=7000),
        PROTOCOL_TIMEOUT_SECONDS,
        "waiting for CNU course links",
    )
    course_selector = f'[data-act="moveLecture"][data-courseid={_css_string(course_id)}]'
    await bounded(page.click(course_selector, timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "entering the CNU course")
    await bounded(
        page.wait_for_selector(COURSE_ROOM_SELECTOR, timeout=7000),
        PROTOCOL_TIMEOUT_SECONDS,
        "waiting for the CNU course room",
    )
    await bounded(
        page.click(COURSE_ROOM_SELECTOR, timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "opening the CNU course lecture list"
    )
    await bounded(
        page.wait_for_selector(".spinner-border", state="hidden", timeout=7000),
        PROTOCOL_TIMEOUT_SECONDS,
        "waiting for the CNU course room to finish loading",
    )
    # Rows in collapsed weeks are present but not visible; wait for attachment so
    # the ordinary UI can expand the appropriate week before the lecture is clicked.
    await bounded(
        page.wait_for_selector(LEARNING_ROW_SELECTOR, state="attached", timeout=7000),
        PROTOCOL_TIMEOUT_SECONDS,
        "waiting for CNU lecture rows",
    )


def _queue_speed_rejected(lectures: list[dict[str, Any]], speed: float, replay: bool) -> bool:
    """Apply each handler's replay speed rule before authentication, in queue order."""
    for lecture in lectures:
        handler = MEDIA_HANDLERS.get(lecture.get("media"))
        rule = None if handler is None else handler.queue_speed_rejected
        if rule is not None and rule(speed, replay):
            return True
    return False


async def _prepare_media(page: Any, config: dict[str, Any], lectures: list[dict[str, Any]]) -> None:
    """Run each handler's queue preparation once, in first-seen order, after authentication.

    Preview plays Panopto unless the table entry has a play function. That is every
    input other than ``youtube``, including missing, ``None`` and unknown media.
    Those inputs still get the one Panopto SSO; this is not a new playability check.
    """
    prepared: set[str] = set()
    panopto = MEDIA_HANDLERS[MediaKind.VIDEO]
    for lecture in lectures:
        media = lecture.get("media")
        handler = MEDIA_HANDLERS.get(media) if isinstance(media, str) else None
        if handler is None or handler.play is None:
            handler = panopto
        if handler.prepare is None or handler.name in prepared:
            continue
        prepared.add(handler.name)
        await handler.prepare(page, config)


def _mark_catalog_completion(entity_id: str, row: dict[str, Any], completion: str) -> None:
    path = catalog_path()
    catalog = read_catalog(path)
    for lecture in catalog["lectures"]:
        if isinstance(lecture, dict) and lecture.get("entity_id") == entity_id:
            if lecture.get("completion") == "complete" and completion == "recorded":
                return
            lecture["completion"] = completion
            lecture["provider_state"] = row.get("state")
            if "progress_text" in row:
                lecture["progress_text"] = row["progress_text"]
            if "attendance_counted" in row:
                lecture["attendance_counted"] = row["attendance_counted"]
            write_catalog(catalog, path)
            return
    raise CampusError(
        "catalog-invalid",
        "The lecture record disappeared from the catalog during playback.",
        "Run 'campusctl sync --only lectures' to rebuild the catalog.",
        "error",
    )


def _settled_item(
    entity_id: str,
    outcome: str,
    state: str | None,
    replay: bool,
    *,
    elapsed: float = 0.0,
    watch_time: str | None = None,
    opened: bool = False,
) -> _LectureItem:
    """An item the queue reached a provider verdict for: completed, recorded, unverified or already complete."""
    return _LectureItem(entity_id, outcome, round(elapsed, 1), watch_time, state, replay, opened)


def _queue_failed_item(entity_id: str, replay: bool, state: str | None = None, opened: bool = False) -> _LectureItem:
    """Failure with no handler code: an unusable ID, a catalog or LMS error, or an unexpected exception."""
    return _LectureItem(entity_id, "failed", 0.0, None, state, replay, opened, "playback-failed")


def _playback_failure_item(
    entity_id: str, failure: _PlaybackFailure, state: str | None, replay: bool, opened: bool
) -> _LectureItem:
    elapsed = failure.elapsed_seconds
    watch_time = _watch_time(elapsed) if opened and elapsed else None
    return _LectureItem(
        entity_id,
        "failed",
        round(elapsed, 1),
        watch_time,
        state,
        replay,
        opened,
        failure.code,
        reason_after_outcome=True,
    )


def _attendance_recorded(row: dict[str, Any] | None) -> bool:
    """The LMS counted full progress but not attendance: watched, not credited."""
    return row is not None and row.get("attendance_counted") is False and progress_is_full(row.get("progress_text"))


def _playback_outcome(state: str | None, provider_row: dict[str, Any] | None) -> str:
    if state == "F":
        return "completed"
    return "recorded" if _attendance_recorded(provider_row) else "unverified"


def _queue_result(items: list[_LectureItem]) -> dict[str, Any]:
    return {"items": [item.to_json() for item in items]}


async def _poll_completion(page: Any, row_id: str) -> dict[str, Any] | None:
    clock = current_clock()
    deadline = clock.monotonic() + COMPLETION_POLL_SECONDS
    last_row: dict[str, Any] | None = None
    while True:
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return last_row
        try:
            row = await _read_row(page, row_id, timeout=min(PROTOCOL_TIMEOUT_SECONDS, remaining))
        except CampusError as error:
            if error.code == "browser-timeout" and clock.monotonic() >= deadline:
                return last_row
            raise
        last_row = row
        if row is not None and (row.get("state") == "F" or _attendance_recorded(row)):
            return row
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return last_row
        await clock.sleep(min(COMPLETION_POLL_INTERVAL_SECONDS, remaining))


def _partial_error(code: str, message: str, remediation: str) -> CampusError:
    return CampusError(code, message, remediation, "error")


def _playback_failed_error(message: str) -> CampusError:
    return _partial_error("playback-failed", message, "Check the LMS session and lecture availability, then retry.")


def _speed_rejected_error() -> CampusError:
    return _partial_error(
        "playback-speed-unavailable",
        "YouTube lectures support only 1.0 playback speed.",
        "Use 1.0 speed for YouTube or retry at a speed supported by the selected player.",
    )


def _unverified_error() -> CampusError:
    return _partial_error(
        "playback-unverified",
        "The LMS did not report the lecture as complete after playback.",
        "Check the lecture status in the LMS before trying again.",
    )


def _failure_error(failure: _PlaybackFailure) -> CampusError:
    """Map a handler failure code to the partial-status error shown for it."""
    code = failure.code
    message = failure.message or "A requested lecture could not be played."
    remediation = "Check the LMS session and lecture availability, then retry."
    if code == "player-frame-unavailable":
        remediation = (
            "Another session for the same account can replace the player page. "
            "Close other LMS sessions for that account and retry."
        )
    if code == "playback-speed-unavailable":
        message = failure.message or "The requested playback speed is unavailable in the player."
        remediation = "Use 1.0 speed for YouTube or retry at a speed supported by the selected player."
    elif code == "youtube-autoplay-blocked":
        message = failure.message or "YouTube did not start automatically or through its official start button."
        remediation = "Check whether the visible YouTube player can start playback, then retry."
    return _partial_error(code, message, remediation)


async def play_lectures(  # noqa: C901 - ordered LMS step sequence
    page: Any,
    config: dict[str, Any],
    lectures: list[dict[str, Any]],
    *,
    speed: float | None = None,
    replay: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    """Play selected lecture records serially and report provider-confirmed state."""
    progress_enabled = progress is not None
    results: list[_LectureItem] = []

    def _emit(event: dict[str, Any]) -> None:
        nonlocal progress_enabled
        if progress_enabled and progress is not None:
            try:
                progress(event)
            except Exception:
                # Display failures stay best-effort. Cancellation, interruption and other BaseException signals propagate.
                progress_enabled = False

    def _finished(entity_id: str, outcome: str) -> None:
        _emit({"type": "finished", "entity_id": entity_id, "outcome": outcome})

    def _not_started_from(start: int) -> list[_LectureItem]:
        remaining = [_not_started(item["entity_id"], replay) for item in lectures[start:]]
        for item in remaining:
            _finished(item.entity_id, item.outcome)
        return remaining

    def _record(item: _LectureItem) -> None:
        results.append(item)
        _finished(item.entity_id, item.outcome)

    def _stop(index: int, errors: list[CampusError]) -> tuple[dict[str, Any], list[CampusError]]:
        """End the queue after item `index`: report every later item as not started."""
        results.extend(_not_started_from(index + 1))
        return _queue_result(results), errors

    selected_speed = float(config.get("playback", {}).get("default_speed", 1.0) if speed is None else speed)
    if _queue_speed_rejected(lectures, selected_speed, replay):
        return _queue_result(_not_started_from(0)), [_speed_rejected_error()]
    try:
        await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)
    except CampusError as error:
        return _queue_result(_not_started_from(0)), error
    try:
        await _prepare_media(page, config, lectures)
    except CampusError as error:
        return _queue_result(_not_started_from(0)), error

    for index, lecture in enumerate(lectures):
        entity_id = lecture["entity_id"]
        diagnostic = _PlayDiagnostic()
        parts = _row_parts(entity_id)
        if parts is None:
            _record(_queue_failed_item(entity_id, replay))
            _emit_play_diagnostic(diagnostic, index, "playback-failed")
            return _stop(index, [_playback_failed_error("A requested lecture could not be opened in its course room.")])
        course_id, row_id = parts
        state: str | None = None
        opened = False

        def mark_opened() -> None:
            nonlocal opened
            opened = True

        try:
            diagnostic.stage = "room"
            if index:
                await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)
            await _enter_course_room(page, course_id)
            diagnostic.stage = "row"
            row = await _read_row(page, row_id)
            if row is None or row.get("moduletype") != "LV":
                raise _PlaybackFailure(code="lecture-row-unavailable")
            state = row.get("state") if isinstance(row.get("state"), str) else None
            if state == "F" and not replay:
                item = _settled_item(entity_id, "already-complete", state, replay)
                try:
                    _mark_catalog_completion(entity_id, row, "complete")
                except CampusError as error:
                    _record(item)
                    return _stop(index, [error])
                _record(item)
                continue
            if not replay and _attendance_recorded(row):
                item = _settled_item(entity_id, "recorded", state, replay)
                try:
                    _mark_catalog_completion(entity_id, row, "recorded")
                except CampusError as error:
                    _record(item)
                    return _stop(index, [error])
                _record(item)
                continue
            if row.get("openyn") == "N":
                raise _PlaybackFailure(code="lecture-row-unavailable")
            lecture_title = lecture.get("title", "")

            elapsed, watch_time = await player_preview._play_visible_lecture(
                page,
                row_id,
                selected_speed,
                media=lecture.get("media"),
                replay=replay,
                diagnostic=diagnostic,
                on_opened=mark_opened,
                on_started=lambda index=index, entity_id=entity_id, title=lecture_title: _emit(
                    {
                        "type": "started",
                        "index": index + 1,
                        "total": len(lectures),
                        "entity_id": entity_id,
                        "title": title,
                    }
                ),
                on_position=lambda position, duration, entity_id=entity_id: _emit(
                    {
                        "type": "position",
                        "entity_id": entity_id,
                        "position_seconds": position,
                        "duration_seconds": duration,
                    }
                ),
            )
            diagnostic.stage = "verify"
            _emit({"type": "verifying", "entity_id": entity_id})
            try:
                provider_row = await _poll_completion(page, row_id)
            except Exception:
                raise _PlaybackFailure(elapsed) from None
            state = provider_row.get("state") if provider_row is not None else None
            outcome = _playback_outcome(state, provider_row)
            item = _settled_item(
                entity_id, outcome, state, replay, elapsed=elapsed, watch_time=watch_time, opened=opened
            )
            if outcome == "unverified":
                _record(item)
                return _stop(index, [_unverified_error()])
            try:
                _mark_catalog_completion(
                    entity_id,
                    provider_row or {"state": state},
                    "complete" if outcome == "completed" else "recorded",
                )
            except CampusError as error:
                _record(item)
                return _stop(index, [error])
            _record(item)
        except _PlaybackFailure as error:
            _record(_playback_failure_item(entity_id, error, state, replay, opened))
            _emit_play_diagnostic(diagnostic, index, error.code)
            return _stop(index, [_failure_error(error)])
        except Exception:  # CampusError included: both report an unclassified failure
            _record(_queue_failed_item(entity_id, replay, state, opened))
            _emit_play_diagnostic(diagnostic, index, "playback-failed")
            return _stop(index, [_playback_failed_error("A requested lecture could not be played.")])
    return _queue_result(results), None
