"""Visible CNU lecture playback and authoritative lecture-state reads."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.catalog import catalog_path, read_catalog, write_catalog
from campusctl.envelope import CampusError

from .courses import COURSE_LINK_SELECTOR
from .lectures import LEARNING_ROW_SELECTOR, progress_is_full
from .login import MY_LECTURE_URL, ensure_logged_in

COURSE_ROOM_SELECTOR = 'a[href="/std/course"]'
PREVIEW_MODAL_SELECTOR = '#previewModal.show, #previewModal[style*="display: block"]'
PLAYER_FRAME_SELECTOR = "iframe#previewFrame"
PLAYER_CLOSE_SELECTOR = "#previewModal #btnPreviewClose, #previewModal .btn_popClose"
PLAY_BUTTON_SELECTOR = (
    "#playIconContainer, #playIcon, #player.is-splash, .play.material-icons, .play-pause, [aria-label='Play']"
)
COMPLETION_POLL_SECONDS = 600.0
COMPLETION_POLL_INTERVAL_SECONDS = 30.0
PLAYER_FRAME_WAIT_SECONDS = 30.0
PLAYER_POLL_INTERVAL_SECONDS = 1.0
YOUTUBE_AUTOPLAY_WAIT_SECONDS = 30.0
YOUTUBE_POLL_INTERVAL_SECONDS = 30.0
YOUTUBE_AFTER_END_SECONDS = 60.0
PLAYER_STALL_SECONDS = 120.0
POSITION_PROGRESS_INTERVAL_SECONDS = 30.0
PLAYER_MAX_SECONDS = 5400.0
PLAYER_WATCH_SLACK_SECONDS = 300.0
SPEED_CHANGE_TIMEOUT_SECONDS = 5.0
PANOPTO_SESSION_WAIT_SECONDS = 10.0

PANOPTO_LOGIN_JS = r"""() => Boolean(
    window.panoptoSaml &&
    window.panoptoSaml.panoptoSamlLogin_submit &&
    window.panoptoSaml.panoptoSamlLogin_submit()
)"""

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

COLLAPSED_TOGGLES_JS = r"""/* campusctl-read-collapse-toggles */(rowId) => {
    const row = document.getElementById(rowId);
    if (!row) return [];
    const toggles = [];
    for (let node = row.parentElement; node && node !== document.body; node = node.parentElement) {
        if (!node.classList.contains('collapse') || node.classList.contains('show') || !node.id) continue;
        const target = `#${CSS.escape(node.id)}`;
        const trigger = document.querySelector(
            `[data-bs-target="${target}"], [data-target="${target}"], a[href="${target}"]`
        );
        if (trigger) {
            if (trigger.id) toggles.push(`#${CSS.escape(trigger.id)}`);
            else if (trigger.hasAttribute('data-bs-target')) toggles.push(`[data-bs-target="${target}"]`);
            else if (trigger.hasAttribute('data-target')) toggles.push(`[data-target="${target}"]`);
            else toggles.push(`a[href="${target}"]`);
        }
    }
    return toggles.reverse();
}"""

READ_VIDEO_JS = r"""/* campusctl-read-video-state */() => {
    const video = document.querySelector('video');
    if (!video) return {exists: false};
    return {
        exists: true,
        currentTime: video.currentTime,
        duration: video.duration,
        paused: video.paused,
        ended: video.ended,
        playedUntil: video.played && video.played.length
            ? video.played.end(video.played.length - 1)
            : 0,
        playbackRate: video.playbackRate
    };
}"""


READ_YOUTUBE_JS = r"""/* campusctl-read-youtube-video-state */() => {
    const video = document.querySelector('video');
    if (!video) return {exists: false};
    return {
        exists: true,
        currentTime: video.currentTime,
        duration: video.duration,
        paused: video.paused,
        ended: video.ended,
        playbackRate: video.playbackRate
    };
}"""


class _PlaybackFailure(Exception):
    def __init__(
        self,
        elapsed_seconds: float = 0.0,
        code: str = "playback-failed",
        message: str | None = None,
    ) -> None:
        self.elapsed_seconds = max(0.0, elapsed_seconds)
        self.code = code
        self.message = message


def _not_started(entity_id: str, replay: bool = False) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "outcome": "not-started",
        "elapsed_seconds": 0.0,
        "watch_time": None,
        "provider_state": None,
        "replay_requested": replay,
        "player_opened": False,
    }


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
        if lecture.get("media") not in {"video", "youtube"}:
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


def _css_string(value: str) -> str:
    escaped = []
    for char in value:
        if char in "\0\n\r\f":
            escaped.append(f"\\{ord(char):x} ")
        elif char in '"\\':
            escaped.append(f"\\{char}")
        else:
            escaped.append(char)
    return '"' + "".join(escaped) + '"'


def _watch_time(elapsed_seconds: float) -> str:
    total = max(0, int(elapsed_seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


async def _read_row(page: Any, row_id: str, timeout: float = PROTOCOL_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    value = await bounded(page.evaluate(READ_ROW_JS, row_id), timeout, "reading the CNU lecture row")
    return value if isinstance(value, dict) else None


async def _has_panopto_session(page: Any) -> bool:
    context = getattr(page, "context", None)
    if context is None:
        return False
    try:
        cookies = await bounded(context.cookies(), PROTOCOL_TIMEOUT_SECONDS, "checking Panopto sign-in")
    except Exception:
        return False
    return any(
        isinstance(cookie, dict)
        and cookie.get("name") == ".ASPXAUTH"
        and "panopto" in str(cookie.get("domain", "")).lower()
        for cookie in cookies
    )


async def _ensure_panopto_session(page: Any, config: dict[str, Any]) -> None:
    if await _has_panopto_session(page):
        return
    with suppress(Exception):
        await bounded(page.evaluate(PANOPTO_LOGIN_JS), PROTOCOL_TIMEOUT_SECONDS, "starting Panopto sign-in")
    deadline = time.monotonic() + PANOPTO_SESSION_WAIT_SECONDS
    authenticated = False
    while time.monotonic() < deadline:
        if await _has_panopto_session(page):
            authenticated = True
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(1.0, remaining))
    if not authenticated:
        raise CampusError(
            "lms-unavailable",
            "Panopto sign-in did not complete.",
            "Check LMS access to the lecture player, then retry.",
            "error",
        )
    await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)


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


async def _expand_row_through_ui(page: Any, row_id: str) -> None:
    row_selector = f"[id={_css_string(row_id)}]"
    row = page.locator(row_selector)
    weekno = await bounded(
        row.get_attribute("data-weekno"),
        PROTOCOL_TIMEOUT_SECONDS,
        "reading the CNU lecture week",
    )
    if not isinstance(weekno, str) or not weekno.strip():
        raise _PlaybackFailure(message="The requested lecture's course week could not be found.")
    weekno = weekno.strip()
    week_container_selector = f"[id={_css_string(f'weekIdx{weekno}')}]"

    week_tab = page.locator(f'a[data-act="shortCutWeek"][data-weekidx={_css_string(weekno)}]').first
    tab_count = await bounded(week_tab.count(), PROTOCOL_TIMEOUT_SECONDS, "finding the CNU lecture week tab")
    if tab_count:
        await bounded(
            week_tab.click(timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "selecting the CNU lecture week",
        )
    else:
        show_all = page.locator('[data-act="showAllWeek"]').first
        show_all_count = await bounded(
            show_all.count(),
            PROTOCOL_TIMEOUT_SECONDS,
            "finding the CNU show-all-weeks control",
        )
        if not show_all_count:
            raise _PlaybackFailure(message="The requested lecture's course week could not be opened.")
        show_all_status = await bounded(
            show_all.get_attribute("data-status"),
            PROTOCOL_TIMEOUT_SECONDS,
            "checking the CNU show-all-weeks control",
        )
        week_is_visible = await bounded(
            page.locator(week_container_selector).is_visible(),
            PROTOCOL_TIMEOUT_SECONDS,
            "checking whether the requested CNU week is visible",
        )
        if show_all_status == "open" or not week_is_visible:
            await bounded(
                show_all.click(timeout=7000),
                PROTOCOL_TIMEOUT_SECONDS,
                "opening all CNU course weeks",
            )

    title_selector = f'{row_selector} [data-act="titleDetailContents"]'
    displayed_selector = f"{week_container_selector}, {row_selector}, {title_selector}"
    try:
        await bounded(
            page.wait_for_selector(displayed_selector, state="visible", timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for the selected CNU week to become visible",
        )
    except Exception:
        raise _PlaybackFailure(message="The requested lecture's course week did not become visible.") from None

    selectors = await bounded(
        page.evaluate(COLLAPSED_TOGGLES_JS, row_id),
        PROTOCOL_TIMEOUT_SECONDS,
        "finding CNU lecture accordion controls",
    )
    if isinstance(selectors, list):
        for selector in selectors:
            if not isinstance(selector, str) or not selector:
                continue
            toggle = page.locator(selector).first
            if await bounded(toggle.is_visible(), PROTOCOL_TIMEOUT_SECONDS, "checking a CNU lecture accordion"):
                await bounded(
                    toggle.click(timeout=7000),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "expanding the CNU lecture accordion",
                )

    try:
        await bounded(
            page.wait_for_selector(title_selector, state="visible", timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for the CNU lecture title to become visible",
        )
    except Exception:
        raise _PlaybackFailure(message="The requested lecture remained hidden after opening its course week.") from None


async def _close_player(page: Any) -> None:
    close_button = await _visible_locator(page, PLAYER_CLOSE_SELECTOR, "finding the player close button")
    if close_button is None:
        raise _PlaybackFailure()
    await bounded(close_button.click(timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "closing the lecture player")


async def _player_frame(page: Any) -> Any:
    deadline = time.monotonic() + PLAYER_FRAME_WAIT_SECONDS
    frame = None
    while time.monotonic() < deadline:
        iframe = await bounded(
            page.query_selector(PLAYER_FRAME_SELECTOR),
            PROTOCOL_TIMEOUT_SECONDS,
            "finding the Panopto player frame",
        )
        if iframe is not None:
            candidate = await bounded(
                iframe.content_frame(), PROTOCOL_TIMEOUT_SECONDS, "opening the Panopto player frame"
            )
            if candidate is not None:
                frame_url = getattr(candidate, "url", "")
                video = await bounded(
                    candidate.query_selector("video"),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "checking the Panopto video frame",
                )
                if "Embed.aspx" in frame_url or video is not None:
                    frame = candidate
                    if video is not None:
                        break
                    play_button = await _visible_locator(
                        frame,
                        PLAY_BUTTON_SELECTOR,
                        "finding a visible Panopto splash play control",
                    )
                    if play_button is not None:
                        await bounded(
                            play_button.click(timeout=7000),
                            PROTOCOL_TIMEOUT_SECONDS,
                            "starting the Panopto player",
                        )
        await asyncio.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    if frame is None:
        raise _PlaybackFailure()
    await bounded(
        frame.wait_for_selector("video", timeout=15000),
        16.0,
        "waiting for the Panopto video",
    )
    return frame


async def _read_video_state(frame: Any) -> dict[str, Any]:
    value = await bounded(
        frame.evaluate(READ_VIDEO_JS),
        PROTOCOL_TIMEOUT_SECONDS,
        "reading Panopto playback state",
    )
    if not isinstance(value, dict) or not value.get("exists"):
        raise _PlaybackFailure()
    return value


def _youtube_embed_frame(frame: Any) -> bool:
    try:
        parsed = urlsplit(getattr(frame, "url", ""))
        host = (parsed.hostname or "").lower()
    except (TypeError, ValueError):
        return False
    valid_host = host in {"youtube.com", "youtube-nocookie.com"} or host.endswith(
        (".youtube.com", ".youtube-nocookie.com")
    )
    return parsed.path.startswith("/embed/") and valid_host


async def _youtube_frame(page: Any) -> Any:
    deadline = time.monotonic() + PLAYER_FRAME_WAIT_SECONDS
    while True:
        for candidate in page.frames:
            if getattr(candidate, "parent_frame", None) is not None and _youtube_embed_frame(candidate):
                return candidate
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _PlaybackFailure(message="The YouTube embed frame did not appear.")
        await asyncio.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, remaining))


async def _read_youtube_state(frame: Any, timeout: float = PROTOCOL_TIMEOUT_SECONDS) -> dict[str, Any]:
    value = await bounded(
        frame.evaluate(READ_YOUTUBE_JS),
        timeout,
        "reading YouTube playback state",
    )
    return value if isinstance(value, dict) else {"exists": False}


def _video_number(status: dict[str, Any], key: str) -> float:
    value = status.get(key)
    return float(value) if isinstance(value, int | float) and math.isfinite(float(value)) else 0.0


def _youtube_plays_at_one(status: dict[str, Any]) -> bool:
    rate = status.get("playbackRate")
    return isinstance(rate, int | float) and not isinstance(rate, bool) and float(rate) == 1.0


async def _play_youtube_video(
    frame: Any,
    *,
    replay: bool = False,
    on_started: Callable[[], None] | None = None,
    on_position: Callable[[float, float], None] | None = None,
) -> tuple[float, str]:
    deadline = time.monotonic() + YOUTUBE_AUTOPLAY_WAIT_SECONDS
    try:
        first = await _read_youtube_state(
            frame,
            min(PROTOCOL_TIMEOUT_SECONDS, YOUTUBE_AUTOPLAY_WAIT_SECONDS),
        )
    except Exception:
        raise _PlaybackFailure(
            code="youtube-autoplay-blocked",
            message="YouTube playback did not start through native autoplay.",
        ) from None
    if first.get("exists") is True and not _youtube_plays_at_one(first):
        raise _PlaybackFailure(
            code="playback-speed-unavailable",
            message="YouTube playback must remain at the native 1.0 speed.",
        )
    initial_time = _video_number(first, "currentTime")
    status = first
    current_time = initial_time
    while not (status.get("exists") is True and status.get("paused") is False and current_time > initial_time + 0.05):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _PlaybackFailure(
                code="youtube-autoplay-blocked",
                message="YouTube playback did not start through native autoplay.",
            )
        await asyncio.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _PlaybackFailure(
                code="youtube-autoplay-blocked",
                message="YouTube playback did not start through native autoplay.",
            )
        try:
            status = await _read_youtube_state(frame, min(PROTOCOL_TIMEOUT_SECONDS, remaining))
        except Exception:
            raise _PlaybackFailure(
                code="youtube-autoplay-blocked",
                message="YouTube playback did not start through native autoplay.",
            ) from None
        if status.get("exists") is True and not _youtube_plays_at_one(status):
            raise _PlaybackFailure(
                code="playback-speed-unavailable",
                message="YouTube playback must remain at the native 1.0 speed.",
            )
        current_time = _video_number(status, "currentTime")

    started_at = time.monotonic()
    if on_started is not None:
        on_started()
    previous_time = current_time
    last_progress_at = started_at
    paused_since: float | None = None
    last_position_event_at: float | None = None
    while True:
        status = await _read_youtube_state(frame)
        now = time.monotonic()
        current_time = _video_number(status, "currentTime")
        duration = _video_number(status, "duration")
        elapsed = now - started_at
        if status.get("exists") is not True:
            raise _PlaybackFailure(elapsed)
        if not _youtube_plays_at_one(status):
            raise _PlaybackFailure(
                elapsed,
                code="playback-speed-unavailable",
                message="YouTube playback must remain at the native 1.0 speed.",
            )
        if on_position is not None and (
            last_position_event_at is None or now - last_position_event_at >= POSITION_PROGRESS_INTERVAL_SECONDS
        ):
            on_position(current_time, duration)
            last_position_event_at = now
        if status.get("ended") or (not replay and duration > 0 and current_time >= duration - 1.0):
            await asyncio.sleep(YOUTUBE_AFTER_END_SECONDS)
            return elapsed, _watch_time(elapsed)
        if status.get("paused"):
            paused_since = now if paused_since is None else paused_since
            if now - paused_since >= PLAYER_STALL_SECONDS:
                raise _PlaybackFailure(elapsed)
        else:
            paused_since = None
        if current_time > previous_time + 0.05:
            previous_time = current_time
            last_progress_at = now
        elif now - last_progress_at >= PLAYER_STALL_SECONDS:
            raise _PlaybackFailure(elapsed)
        watch_limit = (
            min(PLAYER_MAX_SECONDS, duration + PLAYER_WATCH_SLACK_SECONDS) if duration > 0 else PLAYER_MAX_SECONDS
        )
        if elapsed >= watch_limit:
            raise _PlaybackFailure(elapsed)
        await asyncio.sleep(YOUTUBE_POLL_INTERVAL_SECONDS)


async def _visible_locator(page: Any, selector: str, what: str) -> Any | None:
    locator = page.locator(selector)
    count = await bounded(locator.count(), PROTOCOL_TIMEOUT_SECONDS, what)
    for index in range(count):
        candidate = locator.nth(index)
        if await bounded(candidate.is_visible(), PROTOCOL_TIMEOUT_SECONDS, what):
            return candidate
    return None


async def _wait_for_speed(frame: Any, speed: float) -> bool:
    deadline = time.monotonic() + SPEED_CHANGE_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            status = await bounded(
                frame.evaluate(READ_VIDEO_JS),
                min(PROTOCOL_TIMEOUT_SECONDS, remaining),
                "verifying Panopto playback speed",
            )
        except Exception:
            return False
        rate = status.get("playbackRate") if isinstance(status, dict) else None
        if (
            isinstance(status, dict)
            and status.get("exists")
            and isinstance(rate, int | float)
            and math.isclose(float(rate), speed)
        ):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, remaining))


async def _play_visible_lecture(
    page: Any,
    row_id: str,
    speed: float,
    *,
    media: str = "video",
    replay: bool = False,
    on_opened: Callable[[], None] | None = None,
    on_started: Callable[[], None] | None = None,
    on_position: Callable[[float, float], None] | None = None,
) -> tuple[float, str]:
    started_at: float | None = None
    modal_open = False
    primary_error: BaseException | None = None
    try:
        if media == "youtube" and not math.isclose(speed, 1.0):
            raise _PlaybackFailure(
                code="playback-speed-unavailable",
                message="YouTube lectures support only 1.0 playback speed.",
            )
        await _expand_row_through_ui(page, row_id)
        row_selector = f"[id={_css_string(row_id)}] [data-act='titleDetailContents']"
        title = page.locator(row_selector)
        await bounded(
            title.scroll_into_view_if_needed(timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "scrolling to the lecture",
        )
        await bounded(title.click(timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "opening the lecture preview")
        modal_open = True
        await bounded(
            page.wait_for_selector(PREVIEW_MODAL_SELECTOR, timeout=15000),
            16.0,
            "waiting for the lecture preview",
        )
        if media == "youtube":
            frame = await _youtube_frame(page)
            if on_opened is not None:
                on_opened()

            def on_youtube_started() -> None:
                nonlocal started_at
                started_at = time.monotonic()
                if on_started is not None:
                    on_started()

            return await _play_youtube_video(
                frame, replay=replay, on_started=on_youtube_started, on_position=on_position
            )
        frame = await _player_frame(page)
        if on_opened is not None:
            on_opened()
        status = await _read_video_state(frame)

        current_rate = status.get("playbackRate")
        if not isinstance(current_rate, int | float) or not math.isclose(float(current_rate), speed):
            speed_selector = f'div.item[data-speed="{speed:g}"], [data-speed="{speed:g}"]'
            speed_option = frame.locator(speed_selector).first
            count = await bounded(speed_option.count(), PROTOCOL_TIMEOUT_SECONDS, "finding the Panopto speed option")
            if not count:
                raise _PlaybackFailure(code="playback-speed-unavailable")
            try:
                await bounded(
                    speed_option.click(force=True, timeout=7000),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "selecting the Panopto speed option",
                )
            except Exception:
                raise _PlaybackFailure(code="playback-speed-unavailable") from None
            if not await _wait_for_speed(frame, speed):
                raise _PlaybackFailure(code="playback-speed-unavailable")
            status = await _read_video_state(frame)
        already_ended = replay and bool(status.get("ended"))

        if status.get("paused") or (replay and status.get("ended")):
            play_button = await _visible_locator(frame, PLAY_BUTTON_SELECTOR, "finding a visible Panopto play button")
            if play_button is None:
                raise _PlaybackFailure()
            await bounded(play_button.click(timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "starting the Panopto video")
            if already_ended and (await _read_video_state(frame)).get("ended"):
                raise _PlaybackFailure(message="The official player did not restart playback.")

        started_at = time.monotonic()
        if on_started is not None:
            on_started()
        last_progress_at = started_at
        previous_time = -1.0
        last_position_event_at: float | None = None
        while True:
            status = await _read_video_state(frame)
            now = time.monotonic()
            current = status.get("currentTime")
            duration_value = status.get("duration")
            current_time = float(current) if isinstance(current, int | float) and math.isfinite(float(current)) else 0.0
            duration = (
                float(duration_value)
                if isinstance(duration_value, int | float) and math.isfinite(float(duration_value))
                else 0.0
            )
            played_until_value = status.get("playedUntil")
            played_until = (
                float(played_until_value)
                if isinstance(played_until_value, int | float) and math.isfinite(float(played_until_value))
                else 0.0
            )
            elapsed = now - started_at
            if on_position is not None and (
                last_position_event_at is None or now - last_position_event_at >= POSITION_PROGRESS_INTERVAL_SECONDS
            ):
                on_position(current_time, duration)
                last_position_event_at = now
            if status.get("ended") or (not replay and duration > 0 and max(current_time, played_until) >= duration):
                return elapsed, _watch_time(elapsed)
            if status.get("paused"):
                play_button = await _visible_locator(
                    frame,
                    PLAY_BUTTON_SELECTOR,
                    "finding a visible Panopto play button",
                )
                if play_button is not None:
                    await bounded(
                        play_button.click(timeout=7000),
                        PROTOCOL_TIMEOUT_SECONDS,
                        "resuming the Panopto video",
                    )
            if current_time > previous_time + 0.5:
                previous_time = current_time
                last_progress_at = now
            elif now - last_progress_at >= PLAYER_STALL_SECONDS:
                raise _PlaybackFailure(elapsed)
            watch_limit = (
                min(PLAYER_MAX_SECONDS, duration / speed + PLAYER_WATCH_SLACK_SECONDS)
                if duration > 0
                else PLAYER_MAX_SECONDS
            )
            if elapsed >= watch_limit:
                raise _PlaybackFailure(elapsed)
            await asyncio.sleep(PLAYER_POLL_INTERVAL_SECONDS)
    except _PlaybackFailure as error:
        primary_error = error
        raise
    except Exception:
        elapsed = time.monotonic() - started_at if started_at is not None else 0.0
        primary_error = _PlaybackFailure(elapsed)
        raise primary_error from None
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if modal_open:
            try:
                await _close_player(page)
            except asyncio.CancelledError:
                if primary_error is None:
                    raise
            except BaseException:
                if primary_error is None:
                    elapsed = time.monotonic() - started_at if started_at is not None else 0.0
                    raise _PlaybackFailure(elapsed) from None


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


def _completed_item(
    entity_id: str,
    state: str | None,
    elapsed: float = 0.0,
    watch_time: str | None = None,
    *,
    replay: bool = False,
    opened: bool = False,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "outcome": "completed",
        "elapsed_seconds": round(elapsed, 1),
        "watch_time": watch_time,
        "provider_state": state,
        "replay_requested": replay,
        "player_opened": opened,
    }


async def _poll_completion(page: Any, row_id: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + COMPLETION_POLL_SECONDS
    last_row: dict[str, Any] | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return last_row
        try:
            row = await _read_row(page, row_id, timeout=min(PROTOCOL_TIMEOUT_SECONDS, remaining))
        except CampusError as error:
            if error.code == "browser-timeout" and time.monotonic() >= deadline:
                return last_row
            raise
        last_row = row
        if row is not None and (
            row.get("state") == "F"
            or row.get("attendance_counted") is False
            and progress_is_full(row.get("progress_text"))
        ):
            return row
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return last_row
        await asyncio.sleep(min(COMPLETION_POLL_INTERVAL_SECONDS, remaining))


def _partial_error(code: str, message: str, remediation: str) -> CampusError:
    return CampusError(code, message, remediation, "error")


async def play_lectures(
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

    def _emit(event: dict[str, Any]) -> None:
        nonlocal progress_enabled
        if progress_enabled and progress is not None:
            try:
                progress(event)
            except BaseException:
                progress_enabled = False

    def _finished(entity_id: str, outcome: str) -> None:
        _emit({"type": "finished", "entity_id": entity_id, "outcome": outcome})

    def _not_started_from(start: int) -> list[dict[str, Any]]:
        remaining = [_not_started(item["entity_id"], replay) for item in lectures[start:]]
        for item in remaining:
            _finished(item["entity_id"], item["outcome"])
        return remaining

    selected_speed = float(config.get("playback", {}).get("default_speed", 1.0) if speed is None else speed)
    if (
        replay
        and any(lecture.get("media") == "youtube" for lecture in lectures)
        and not math.isclose(selected_speed, 1.0)
    ):
        return {"items": _not_started_from(0)}, [
            _partial_error(
                "playback-speed-unavailable",
                "YouTube lectures support only 1.0 playback speed.",
                "Use 1.0 speed for YouTube or retry at a speed supported by the selected player.",
            )
        ]
    try:
        await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)
    except CampusError as error:
        return {"items": _not_started_from(0)}, error
    if any(lecture.get("media") != "youtube" for lecture in lectures):
        try:
            await _ensure_panopto_session(page, config)
        except CampusError as error:
            return {"items": _not_started_from(0)}, error

    results: list[dict[str, Any]] = []
    for index, lecture in enumerate(lectures):
        entity_id = lecture["entity_id"]
        parts = _row_parts(entity_id)
        if parts is None:
            result = {**_not_started(entity_id, replay), "outcome": "failed", "reason_code": "playback-failed"}
            results.append(result)
            _finished(entity_id, result["outcome"])
            results.extend(_not_started_from(index + 1))
            return {"items": results}, [
                _partial_error(
                    "playback-failed",
                    "A requested lecture could not be opened in its course room.",
                    "Check the LMS session and lecture availability, then retry.",
                )
            ]
        course_id, row_id = parts
        state: str | None = None
        opened = False

        def mark_opened() -> None:
            nonlocal opened
            opened = True

        try:
            if index:
                await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)
            await _enter_course_room(page, course_id)
            row = await _read_row(page, row_id)
            if row is None or row.get("moduletype") != "LV":
                raise _PlaybackFailure()
            state = row.get("state") if isinstance(row.get("state"), str) else None
            if state == "F" and not replay:
                result = {
                    "entity_id": entity_id,
                    "outcome": "already-complete",
                    "elapsed_seconds": 0.0,
                    "watch_time": None,
                    "provider_state": state,
                    "replay_requested": replay,
                    "player_opened": False,
                }
                try:
                    _mark_catalog_completion(entity_id, row, "complete")
                except CampusError as error:
                    results.append(result)
                    _finished(entity_id, result["outcome"])
                    results.extend(_not_started_from(index + 1))
                    return {"items": results}, [error]
                results.append(result)
                _finished(entity_id, result["outcome"])
                continue
            if not replay and row.get("attendance_counted") is False and progress_is_full(row.get("progress_text")):
                result = {
                    "entity_id": entity_id,
                    "outcome": "recorded",
                    "elapsed_seconds": 0.0,
                    "watch_time": None,
                    "provider_state": state,
                    "replay_requested": replay,
                    "player_opened": False,
                }
                try:
                    _mark_catalog_completion(entity_id, row, "recorded")
                except CampusError as error:
                    results.append(result)
                    _finished(entity_id, result["outcome"])
                    results.extend(_not_started_from(index + 1))
                    return {"items": results}, [error]
                results.append(result)
                _finished(entity_id, result["outcome"])
                continue
            if row.get("openyn") == "N":
                raise _PlaybackFailure()
            lecture_title = lecture.get("title", "")

            elapsed, watch_time = await _play_visible_lecture(
                page,
                row_id,
                selected_speed,
                media=lecture.get("media", "video"),
                replay=replay,
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
            _emit({"type": "verifying", "entity_id": entity_id})
            try:
                provider_row = await _poll_completion(page, row_id)
            except Exception:
                raise _PlaybackFailure(elapsed) from None
            state = provider_row.get("state") if provider_row is not None else None
            if state != "F":
                if (
                    provider_row is not None
                    and provider_row.get("attendance_counted") is False
                    and progress_is_full(provider_row.get("progress_text"))
                ):
                    result = {
                        "entity_id": entity_id,
                        "outcome": "recorded",
                        "elapsed_seconds": round(elapsed, 1),
                        "watch_time": watch_time,
                        "provider_state": state,
                        "replay_requested": replay,
                        "player_opened": opened,
                    }
                    try:
                        _mark_catalog_completion(entity_id, provider_row, "recorded")
                    except CampusError as error:
                        results.append(result)
                        _finished(entity_id, result["outcome"])
                        results.extend(_not_started_from(index + 1))
                        return {"items": results}, [error]
                    results.append(result)
                    _finished(entity_id, result["outcome"])
                    continue
                result = {
                    "entity_id": entity_id,
                    "outcome": "unverified",
                    "elapsed_seconds": round(elapsed, 1),
                    "watch_time": watch_time,
                    "provider_state": state,
                    "replay_requested": replay,
                    "player_opened": opened,
                }
                results.append(result)
                _finished(entity_id, result["outcome"])
                results.extend(_not_started_from(index + 1))
                return {"items": results}, [
                    _partial_error(
                        "playback-unverified",
                        "The LMS did not report the lecture as complete after playback.",
                        "Check the lecture status in the LMS before trying again.",
                    )
                ]
            result = _completed_item(entity_id, state, elapsed, watch_time, replay=replay, opened=opened)
            try:
                _mark_catalog_completion(entity_id, provider_row or {"state": state}, "complete")
            except CampusError as error:
                results.append(result)
                _finished(entity_id, result["outcome"])
                results.extend(_not_started_from(index + 1))
                return {"items": results}, [error]
            results.append(result)
            _finished(entity_id, result["outcome"])
        except CampusError:
            result = {
                **_not_started(entity_id, replay),
                "outcome": "failed",
                "reason_code": "playback-failed",
                "provider_state": state,
                "player_opened": opened,
            }
            results.append(result)
            _finished(entity_id, result["outcome"])
            results.extend(_not_started_from(index + 1))
            return {"items": results}, [
                _partial_error(
                    "playback-failed",
                    "A requested lecture could not be played.",
                    "Check the LMS session and lecture availability, then retry.",
                )
            ]
        except _PlaybackFailure as error:
            elapsed = error.elapsed_seconds
            result = {
                "entity_id": entity_id,
                "outcome": "failed",
                "reason_code": error.code,
                "elapsed_seconds": round(elapsed, 1),
                "watch_time": _watch_time(elapsed) if elapsed else None,
                "provider_state": state,
                "replay_requested": replay,
                "player_opened": opened,
            }
            results.append(result)
            _finished(entity_id, result["outcome"])
            results.extend(_not_started_from(index + 1))
            code = error.code
            message = error.message or "A requested lecture could not be played."
            remediation = "Check the LMS session and lecture availability, then retry."
            if code == "playback-speed-unavailable":
                message = error.message or "The requested playback speed is unavailable in the player."
                remediation = "Use 1.0 speed for YouTube or retry at a speed supported by the selected player."
            elif code == "youtube-autoplay-blocked":
                message = error.message or "YouTube did not start through native autoplay."
                remediation = "Check whether the visible YouTube player can autoplay, then retry."
            return {"items": results}, [_partial_error(code, message, remediation)]
        except Exception:
            result = {
                **_not_started(entity_id, replay),
                "outcome": "failed",
                "reason_code": "playback-failed",
                "provider_state": state,
                "player_opened": opened,
            }
            results.append(result)
            _finished(entity_id, result["outcome"])
            results.extend(_not_started_from(index + 1))
            return {"items": results}, [
                _partial_error(
                    "playback-failed",
                    "A requested lecture could not be played.",
                    "Check the LMS session and lecture availability, then retry.",
                )
            ]
    return {"items": results}, None
