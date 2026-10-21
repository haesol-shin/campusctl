"""Course and row preview lifetime, including one modal close and cleanup precedence.

Video playback stays here until P6. YouTube playback is the media-table handler.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from playwright.async_api import Error as PlaywrightError

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.envelope import CampusError
from campusctl.wait_clock import current_clock

from .media import MEDIA_HANDLERS
from .player_types import (
    PLAY_BUTTON_SELECTOR,
    PLAYER_FRAME_WAIT_SECONDS,
    PLAYER_MAX_SECONDS,
    PLAYER_MAX_SPLASH_CLICKS,
    PLAYER_POLL_INTERVAL_SECONDS,
    PLAYER_STALL_SECONDS,
    PLAYER_WATCH_SLACK_SECONDS,
    POSITION_PROGRESS_INTERVAL_SECONDS,
    _css_string,
    _frame_kind,
    _PlaybackFailure,
    _PlayDiagnostic,
    _video_number,
    _visible_locator,
    _watch_time,
)

PREVIEW_MODAL_SELECTOR = '#previewModal.show, #previewModal[style*="display: block"]'
PLAYER_FRAME_SELECTOR = "iframe#previewFrame"
PLAYER_CLOSE_SELECTOR = "#previewModal #btnPreviewClose, #previewModal .btn_popClose"
PLAYER_VIDEO_WAIT_SECONDS = 60.0
PLAYER_SPLASH_RETRY_SECONDS = 10.0
REPLAY_COVERAGE_TOLERANCE_SECONDS = 2.0
SPEED_CHANGE_TIMEOUT_SECONDS = 5.0

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
    let playedCoverage = 0;
    if (video.played && video.played.length) {
        for (let index = 0; index < video.played.length; index++) {
            playedCoverage += video.played.end(index) - video.played.start(index);
        }
    }
    return {
        exists: true,
        currentTime: video.currentTime,
        duration: video.duration,
        paused: video.paused,
        ended: video.ended,
        playedUntil: video.played && video.played.length
            ? video.played.end(video.played.length - 1)
            : 0,
        playedCoverage: playedCoverage,
        playbackRate: video.playbackRate
    };
}"""


async def _expand_row_through_ui(page: Any, row_id: str) -> None:
    row_selector = f"[id={_css_string(row_id)}]"
    row = page.locator(row_selector)
    weekno = await bounded(
        row.get_attribute("data-weekno"),
        PROTOCOL_TIMEOUT_SECONDS,
        "reading the CNU lecture week",
    )
    if not isinstance(weekno, str) or not weekno.strip():
        raise _PlaybackFailure(
            code="lecture-row-unavailable",
            message="The requested lecture's course week could not be found.",
        )
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
            raise _PlaybackFailure(
                code="lecture-row-unavailable",
                message="The requested lecture's course week could not be opened.",
            )
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
        raise _PlaybackFailure(
            code="lecture-row-unavailable",
            message="The requested lecture's course week did not become visible.",
        ) from None

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
        raise _PlaybackFailure(
            code="lecture-row-unavailable",
            message="The requested lecture remained hidden after opening its course week.",
        ) from None


async def _close_player(page: Any) -> None:
    close_button = await _visible_locator(page, PLAYER_CLOSE_SELECTOR, "finding the player close button")
    if close_button is None:
        raise _PlaybackFailure()
    await bounded(close_button.click(timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "closing the lecture player")


async def _player_frame(page: Any, diagnostic: _PlayDiagnostic | None = None) -> Any:  # noqa: C901
    clock = current_clock()
    started_at = clock.monotonic()
    frame_deadline = started_at + PLAYER_FRAME_WAIT_SECONDS
    video_deadline = started_at + PLAYER_VIDEO_WAIT_SECONDS
    if diagnostic is not None:
        diagnostic.stage = "frame"
    frame = None
    video = None
    splash_clicks = 0
    first_click_at: float | None = None
    while current_clock().monotonic() < video_deadline:
        now = current_clock().monotonic()
        if frame is None and now >= frame_deadline:
            break
        if frame is None:
            candidate = None
            try:
                iframe = await bounded(
                    page.query_selector(PLAYER_FRAME_SELECTOR),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "finding the Panopto player frame",
                )
                if iframe is not None:
                    candidate = await bounded(
                        iframe.content_frame(), PROTOCOL_TIMEOUT_SECONDS, "opening the Panopto player frame"
                    )
            except (CampusError, PlaywrightError):
                raise _PlaybackFailure(
                    current_clock().monotonic() - started_at,
                    code="player-frame-unavailable",
                    message="The Panopto player frame was unavailable.",
                ) from None
            if candidate is not None:
                frame_url = str(getattr(candidate, "url", "") or "")
                kind = _frame_kind(frame_url, str(getattr(page, "url", "") or ""))
                recognized_player = kind == "panopto" and "Embed.aspx" in frame_url
                if diagnostic is not None:
                    diagnostic.frame = candidate
                    diagnostic.frame_kind = kind
                    diagnostic.frame_panopto = kind == "panopto"
                try:
                    video = await bounded(
                        candidate.query_selector("video"),
                        PROTOCOL_TIMEOUT_SECONDS,
                        "checking the Panopto video frame",
                    )
                except (CampusError, PlaywrightError):
                    if diagnostic is not None and recognized_player:
                        diagnostic.stage = "video"
                    raise _PlaybackFailure(
                        current_clock().monotonic() - started_at,
                        code="player-video-unavailable" if recognized_player else "player-frame-unavailable",
                        message=(
                            "The Panopto player frame loaded without an accessible video element."
                            if recognized_player
                            else "The Panopto player frame was unavailable."
                        ),
                    ) from None
                if "Embed.aspx" in frame_url or video is not None:
                    frame = candidate
        else:
            video = await bounded(
                frame.query_selector("video"),
                PROTOCOL_TIMEOUT_SECONDS,
                "checking the Panopto video frame",
            )
        if frame is not None and video is not None:
            break
        if frame is not None:
            if diagnostic is not None:
                diagnostic.stage = "video"
            play_button = await _visible_locator(
                frame,
                PLAY_BUTTON_SELECTOR,
                "finding a visible Panopto splash play control",
            )
            if diagnostic is not None:
                diagnostic.splash_visible = play_button is not None
            if play_button is not None and splash_clicks < PLAYER_MAX_SPLASH_CLICKS:
                retry_due = (
                    splash_clicks > 0
                    and first_click_at is not None
                    and now - first_click_at >= PLAYER_SPLASH_RETRY_SECONDS
                )
                if splash_clicks == 0 or retry_due:
                    await bounded(
                        play_button.click(timeout=7000),
                        PROTOCOL_TIMEOUT_SECONDS,
                        "starting the Panopto player",
                    )
                    splash_clicks += 1
                    if first_click_at is None:
                        first_click_at = now
                    if diagnostic is not None:
                        diagnostic.splash_clicks = splash_clicks
        await current_clock().sleep(
            min(PLAYER_POLL_INTERVAL_SECONDS, max(0.0, video_deadline - current_clock().monotonic()))
        )
    if frame is None:
        raise _PlaybackFailure(
            current_clock().monotonic() - started_at,
            code="player-frame-unavailable",
            message="The Panopto player frame did not appear.",
        )
    if video is None:
        if diagnostic is not None:
            diagnostic.stage = "video"
            await diagnostic.refresh()
        raise _PlaybackFailure(
            code="player-video-unavailable",
            message="The Panopto player frame loaded without a video element.",
        )
    try:
        await bounded(
            frame.wait_for_selector("video", timeout=15000),
            16.0,
            "waiting for the Panopto video",
        )
    except Exception:
        if diagnostic is not None:
            await diagnostic.refresh()
        raise _PlaybackFailure(
            code="player-video-unavailable",
            message="The Panopto player frame loaded without a video element.",
        ) from None
    return frame


async def _read_video_state(frame: Any) -> dict[str, Any]:
    value = await bounded(
        frame.evaluate(READ_VIDEO_JS),
        PROTOCOL_TIMEOUT_SECONDS,
        "reading Panopto playback state",
    )
    if not isinstance(value, dict) or not value.get("exists"):
        raise _PlaybackFailure(
            code="player-video-unavailable",
            message="The Panopto player frame loaded without a video element.",
        )
    return value


async def _wait_for_speed(frame: Any, speed: float) -> bool:
    clock = current_clock()
    deadline = clock.monotonic() + SPEED_CHANGE_TIMEOUT_SECONDS
    while True:
        remaining = deadline - clock.monotonic()
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
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return False
        await clock.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, remaining))


async def _play_visible_lecture(  # noqa: C901
    page: Any,
    row_id: str,
    speed: float,
    *,
    media: str = "video",
    replay: bool = False,
    on_opened: Callable[[], None] | None = None,
    on_started: Callable[[], None] | None = None,
    on_position: Callable[[float, float], None] | None = None,
    diagnostic: _PlayDiagnostic | None = None,
) -> tuple[float, str]:
    diagnostic = diagnostic if diagnostic is not None else _PlayDiagnostic()
    started_at: float | None = None
    modal_open = False
    primary_error: BaseException | None = None
    try:
        handler = MEDIA_HANDLERS.get(media)
        if handler is not None and handler.item_speed_rejected is not None:
            rejected = handler.item_speed_rejected(speed)
            if rejected is not None:
                raise rejected
        diagnostic.stage = "row"
        await _expand_row_through_ui(page, row_id)
        diagnostic.stage = "preview"
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
        diagnostic.stage = "frame"
        if handler is not None and handler.play is not None:

            def on_media_started() -> None:
                nonlocal started_at
                started_at = current_clock().monotonic()
                if on_started is not None:
                    on_started()

            return await handler.play(
                page,
                diagnostic,
                replay=replay,
                on_opened=on_opened,
                on_started=on_media_started,
                on_position=on_position,
            )
        frame = await _player_frame(page, diagnostic)
        diagnostic.stage = "playback"
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

        started_at = current_clock().monotonic()
        if on_started is not None:
            on_started()
        last_progress_at = started_at
        previous_time = -1.0
        last_position_event_at: float | None = None
        while True:
            status = await _read_video_state(frame)
            now = current_clock().monotonic()
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
            played_coverage = _video_number(status, "playedCoverage")
            elapsed = now - started_at
            if on_position is not None and (
                last_position_event_at is None or now - last_position_event_at >= POSITION_PROGRESS_INTERVAL_SECONDS
            ):
                on_position(current_time, duration)
                last_position_event_at = now
            if status.get("ended") or (
                duration > 0
                and (
                    played_coverage >= max(0.0, duration - REPLAY_COVERAGE_TOLERANCE_SECONDS)
                    if replay
                    else max(current_time, played_until) >= duration
                )
            ):
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
                raise _PlaybackFailure(elapsed, code="playback-stalled")
            watch_limit = (
                min(PLAYER_MAX_SECONDS, duration / speed + PLAYER_WATCH_SLACK_SECONDS)
                if duration > 0
                else PLAYER_MAX_SECONDS
            )
            if elapsed >= watch_limit:
                raise _PlaybackFailure(elapsed, code="playback-timeout")
            await current_clock().sleep(PLAYER_POLL_INTERVAL_SECONDS)
    except _PlaybackFailure as error:
        primary_error = error
        raise
    except Exception:
        elapsed = current_clock().monotonic() - started_at if started_at is not None else 0.0
        primary_error = _PlaybackFailure(elapsed)
        raise primary_error from None
    except BaseException as error:
        primary_error = error
        raise
    finally:
        with suppress(BaseException):
            await diagnostic.refresh()
        if primary_error is None:
            diagnostic.stage = "close"
        if modal_open:
            try:
                await _close_player(page)
            except asyncio.CancelledError:
                if primary_error is None:
                    raise
            except BaseException:
                if primary_error is None:
                    elapsed = current_clock().monotonic() - started_at if started_at is not None else 0.0
                    raise _PlaybackFailure(elapsed) from None
