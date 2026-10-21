"""Panopto session preparation, frame and splash discovery, speed and watch loop."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from playwright.async_api import Error as PlaywrightError

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.envelope import CampusError
from campusctl.wait_clock import current_clock

from ..courses import COURSE_LINK_SELECTOR
from ..login import MY_LECTURE_URL, ensure_logged_in
from ..player_types import (
    PLAY_BUTTON_SELECTOR,
    PLAYER_FRAME_WAIT_SECONDS,
    PLAYER_MAX_SECONDS,
    PLAYER_MAX_SPLASH_CLICKS,
    PLAYER_POLL_INTERVAL_SECONDS,
    PLAYER_STALL_SECONDS,
    PLAYER_WATCH_SLACK_SECONDS,
    _frame_kind,
    _PlaybackFailure,
    _PlayDiagnostic,
    _position_event_due,
    _video_number,
    _visible_locator,
    _watch_time,
)

PANOPTO_SESSION_WAIT_SECONDS = 10.0
PLAYER_FRAME_SELECTOR = "iframe#previewFrame"
PLAYER_VIDEO_WAIT_SECONDS = 60.0
PLAYER_SPLASH_RETRY_SECONDS = 10.0
REPLAY_COVERAGE_TOLERANCE_SECONDS = 2.0
SPEED_CHANGE_TIMEOUT_SECONDS = 5.0

PANOPTO_LOGIN_JS = r"""() => Boolean(
    window.panoptoSaml &&
    window.panoptoSaml.panoptoSamlLogin_submit &&
    window.panoptoSaml.panoptoSamlLogin_submit()
)"""

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


async def prepare(page: Any, config: dict[str, Any]) -> None:
    if await _has_panopto_session(page):
        return
    with suppress(Exception):
        await bounded(page.evaluate(PANOPTO_LOGIN_JS), PROTOCOL_TIMEOUT_SECONDS, "starting Panopto sign-in")
    clock = current_clock()
    deadline = clock.monotonic() + PANOPTO_SESSION_WAIT_SECONDS
    authenticated = False
    while clock.monotonic() < deadline:
        if await _has_panopto_session(page):
            authenticated = True
            break
        remaining = deadline - clock.monotonic()
        if remaining > 0:
            await clock.sleep(min(1.0, remaining))
    if not authenticated:
        raise CampusError(
            "lms-unavailable",
            "Panopto sign-in did not complete.",
            "Check LMS access to the lecture player, then retry.",
            "error",
        )
    await ensure_logged_in(page, config, target_url=MY_LECTURE_URL, expected_selector=COURSE_LINK_SELECTOR)


def _is_recognized_player(kind: str, frame_url: str) -> bool:
    return kind == "panopto" and "Embed.aspx" in frame_url


def _splash_click_due(splash_clicks: int, first_click_at: float | None, now: float) -> bool:
    """The first splash click is always due; a retry waits for the retry interval, up to the click cap."""
    if splash_clicks >= PLAYER_MAX_SPLASH_CLICKS:
        return False
    return splash_clicks == 0 or (first_click_at is not None and now - first_click_at >= PLAYER_SPLASH_RETRY_SECONDS)


def _video_check_failure(elapsed: float, recognized_player: bool) -> _PlaybackFailure:
    if recognized_player:
        return _PlaybackFailure(
            elapsed,
            code="player-video-unavailable",
            message="The Panopto player frame loaded without an accessible video element.",
        )
    return _PlaybackFailure(
        elapsed, code="player-frame-unavailable", message="The Panopto player frame was unavailable."
    )


async def _player_frame(  # noqa: C901 - ordered frame, video and splash wait
    page: Any, diagnostic: _PlayDiagnostic | None = None
) -> Any:
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
                recognized_player = _is_recognized_player(kind, frame_url)
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
                    raise _video_check_failure(current_clock().monotonic() - started_at, recognized_player) from None
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
            if play_button is not None and _splash_click_due(splash_clicks, first_click_at, now):
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
        if (
            isinstance(status, dict)
            and status.get("exists")
            and _panopto_rate_matches(status.get("playbackRate"), speed)
        ):
            return True
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return False
        await clock.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, remaining))


def _panopto_rate_matches(rate: object, speed: float) -> bool:
    """Panopto reads any number, a Boolean included, and accepts a rate near the request."""
    return isinstance(rate, int | float) and math.isclose(float(rate), speed)


def _panopto_finished(status: dict[str, Any], current_time: float, duration: float, replay: bool) -> bool:
    if status.get("ended"):
        return True
    if duration <= 0:
        return False
    if replay:
        return _video_number(status, "playedCoverage") >= max(0.0, duration - REPLAY_COVERAGE_TOLERANCE_SECONDS)
    return max(current_time, _video_number(status, "playedUntil")) >= duration


def _panopto_watch_limit(duration: float, speed: float) -> float:
    if duration > 0:
        return min(PLAYER_MAX_SECONDS, duration / speed + PLAYER_WATCH_SLACK_SECONDS)
    return PLAYER_MAX_SECONDS


def _watch_abort_code(idle_seconds: float, elapsed: float, limit: float) -> str | None:
    """Failure code for a watch loop that made no progress for too long or ran past its limit."""
    if idle_seconds >= PLAYER_STALL_SECONDS:
        return "playback-stalled"
    if elapsed >= limit:
        return "playback-timeout"
    return None


def _speed_selector(speed: float) -> str:
    return f'div.item[data-speed="{speed:g}"], [data-speed="{speed:g}"]'


async def play(  # noqa: C901 - ordered LMS step sequence
    page: Any,
    diagnostic: _PlayDiagnostic,
    *,
    speed: float,
    replay: bool = False,
    on_opened: Callable[[], None] | None = None,
    on_started: Callable[[float], None] | None = None,
    on_position: Callable[[float, float], None] | None = None,
) -> tuple[float, str]:
    frame = await _player_frame(page, diagnostic)
    diagnostic.stage = "playback"
    if on_opened is not None:
        on_opened()
    status = await _read_video_state(frame)

    if not _panopto_rate_matches(status.get("playbackRate"), speed):
        speed_selector = _speed_selector(speed)
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

    if status.get("paused") or already_ended:
        play_button = await _visible_locator(frame, PLAY_BUTTON_SELECTOR, "finding a visible Panopto play button")
        if play_button is None:
            raise _PlaybackFailure()
        await bounded(play_button.click(timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "starting the Panopto video")
        if already_ended and (await _read_video_state(frame)).get("ended"):
            raise _PlaybackFailure(message="The official player did not restart playback.")

    started_at = current_clock().monotonic()
    if on_started is not None:
        on_started(started_at)
    last_progress_at = started_at
    previous_time = -1.0
    last_position_event_at: float | None = None
    while True:
        status = await _read_video_state(frame)
        now = current_clock().monotonic()
        current_time = _video_number(status, "currentTime")
        duration = _video_number(status, "duration")
        elapsed = now - started_at
        if on_position is not None and _position_event_due(last_position_event_at, now):
            on_position(current_time, duration)
            last_position_event_at = now
        if _panopto_finished(status, current_time, duration, replay):
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
        abort_code = _watch_abort_code(now - last_progress_at, elapsed, _panopto_watch_limit(duration, speed))
        if abort_code is not None:
            raise _PlaybackFailure(elapsed, code=abort_code)
        await current_clock().sleep(PLAYER_POLL_INTERVAL_SECONDS)
