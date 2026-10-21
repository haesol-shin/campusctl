"""YouTube embed discovery, one official start click, and 1x observation."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.wait_clock import current_clock

from ..player_types import (
    PLAYER_FRAME_WAIT_SECONDS,
    PLAYER_MAX_SECONDS,
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

YOUTUBE_AUTOPLAY_WAIT_SECONDS = 30.0
YOUTUBE_POLL_INTERVAL_SECONDS = 30.0
YOUTUBE_AFTER_END_SECONDS = 60.0
# One tick of the player clock: a start position this close to 0 is still the start,
# and a later position this much ahead means the video moved.
YOUTUBE_MOVEMENT_SECONDS = 0.05

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
    clock = current_clock()
    deadline = clock.monotonic() + PLAYER_FRAME_WAIT_SECONDS
    while True:
        for candidate in page.frames:
            if getattr(candidate, "parent_frame", None) is not None and _youtube_embed_frame(candidate):
                return candidate
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise _PlaybackFailure(
                code="player-frame-unavailable",
                message="The YouTube embed frame did not appear.",
            )
        await clock.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, remaining))


async def _read_youtube_state(frame: Any, timeout: float = PROTOCOL_TIMEOUT_SECONDS) -> dict[str, Any]:
    value = await bounded(
        frame.evaluate(READ_YOUTUBE_JS),
        timeout,
        "reading YouTube playback state",
    )
    return value if isinstance(value, dict) else {"exists": False}


def _is_number(value: object) -> bool:
    """A JSON number. Unlike Panopto's readers, a Boolean is not one."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _youtube_plays_at_one(status: dict[str, Any]) -> bool:
    rate = status.get("playbackRate")
    return _is_number(rate) and float(rate) == 1.0


def _youtube_off_native_rate(status: dict[str, Any]) -> bool:
    return status.get("exists") is True and not _youtube_plays_at_one(status)


def _youtube_in_start_state(status: dict[str, Any]) -> bool:
    position = status.get("currentTime")
    return (
        status.get("exists") is True
        and status.get("paused") is True
        and status.get("ended") is False
        and _is_number(position)
        and math.isfinite(position)
        and 0 <= position <= YOUTUBE_MOVEMENT_SECONDS
    )


def _youtube_started(status: dict[str, Any], current_time: float, initial_time: float) -> bool:
    return (
        status.get("exists") is True
        and status.get("paused") is False
        and current_time > initial_time + YOUTUBE_MOVEMENT_SECONDS
    )


def _youtube_finished(status: dict[str, Any], current_time: float, duration: float, replay: bool) -> bool:
    return bool(status.get("ended")) or (not replay and duration > 0 and current_time >= duration - 1.0)


def _youtube_watch_limit(duration: float) -> float:
    return min(PLAYER_MAX_SECONDS, duration + PLAYER_WATCH_SLACK_SECONDS) if duration > 0 else PLAYER_MAX_SECONDS


def _native_speed_failure(elapsed_seconds: float = 0.0) -> _PlaybackFailure:
    return _PlaybackFailure(
        elapsed_seconds,
        code="playback-speed-unavailable",
        message="YouTube playback must remain at the native 1.0 speed.",
    )


def _stalled_failure(elapsed_seconds: float) -> _PlaybackFailure:
    return _PlaybackFailure(
        elapsed_seconds,
        code="playback-stalled",
        message="YouTube playback stopped making progress.",
    )


async def _click_youtube_start(frame: Any, timeout: float) -> tuple[bool, str]:
    button = await _visible_locator(frame, ".ytmCuedOverlayPlayButton", "finding the official YouTube start overlay")
    if button is None:
        return False, "official start overlay is not visible"
    # Visibility checks can outlive native autoplay; never toggle a player that has started.
    status = await _read_youtube_state(frame)
    if not _youtube_in_start_state(status) or not _youtube_plays_at_one(status):
        return False, "player is no longer in its start state"
    await button.click(timeout=timeout * 1000)
    return True, "official start button clicked once"


async def _wait_for_youtube_start(frame: Any) -> float:
    clock = current_clock()
    deadline = clock.monotonic() + YOUTUBE_AUTOPLAY_WAIT_SECONDS
    diagnostic = "playback state could not be read"

    def failure() -> _PlaybackFailure:
        return _PlaybackFailure(
            code="youtube-autoplay-blocked",
            message=f"YouTube playback did not start: {diagnostic}.",
        )

    try:
        first = await _read_youtube_state(frame, min(PROTOCOL_TIMEOUT_SECONDS, YOUTUBE_AUTOPLAY_WAIT_SECONDS))
    except Exception:
        raise failure() from None
    if _youtube_off_native_rate(first):
        raise _native_speed_failure()
    initial_time = _video_number(first, "currentTime")
    status = first
    current_time = initial_time
    clicked = False
    diagnostic = "player is not in its start state"
    while not _youtube_started(status, current_time, initial_time):
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise failure()
        if not clicked and _youtube_in_start_state(status):
            try:
                clicked, diagnostic = await bounded(
                    _click_youtube_start(frame, min(PROTOCOL_TIMEOUT_SECONDS, remaining)),
                    min(PROTOCOL_TIMEOUT_SECONDS, remaining),
                    "checking or clicking the official YouTube start button",
                )
            except Exception:
                diagnostic = "official start button check or click failed"
                raise failure() from None
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise failure()
        await clock.sleep(min(PLAYER_POLL_INTERVAL_SECONDS, remaining))
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise failure()
        try:
            status = await _read_youtube_state(frame, min(PROTOCOL_TIMEOUT_SECONDS, remaining))
        except Exception:
            raise failure() from None
        if _youtube_off_native_rate(status):
            raise _native_speed_failure()
        current_time = _video_number(status, "currentTime")

    return current_time


async def _play_youtube_video(
    frame: Any,
    *,
    replay: bool = False,
    on_started: Callable[[], None] | None = None,
    on_position: Callable[[float, float], None] | None = None,
) -> tuple[float, str]:
    current_time = await _wait_for_youtube_start(frame)
    clock = current_clock()
    started_at = clock.monotonic()
    if on_started is not None:
        on_started()
    previous_time = current_time
    last_progress_at = started_at
    paused_since: float | None = None
    last_position_event_at: float | None = None
    while True:
        status = await _read_youtube_state(frame)
        now = clock.monotonic()
        current_time = _video_number(status, "currentTime")
        duration = _video_number(status, "duration")
        elapsed = now - started_at
        if status.get("exists") is not True:
            raise _PlaybackFailure(elapsed)
        if not _youtube_plays_at_one(status):
            raise _native_speed_failure(elapsed)
        if on_position is not None and _position_event_due(last_position_event_at, now):
            on_position(current_time, duration)
            last_position_event_at = now
        if _youtube_finished(status, current_time, duration, replay):
            await clock.sleep(YOUTUBE_AFTER_END_SECONDS)
            return elapsed, _watch_time(elapsed)
        if status.get("paused"):
            paused_since = now if paused_since is None else paused_since
            if now - paused_since >= PLAYER_STALL_SECONDS:
                raise _stalled_failure(elapsed)
        else:
            paused_since = None
        if current_time > previous_time + YOUTUBE_MOVEMENT_SECONDS:
            previous_time = current_time
            last_progress_at = now
        elif now - last_progress_at >= PLAYER_STALL_SECONDS:
            raise _stalled_failure(elapsed)
        watch_limit = _youtube_watch_limit(duration)
        if elapsed >= watch_limit:
            raise _PlaybackFailure(
                elapsed,
                code="playback-timeout",
                message="YouTube playback exceeded its watch time limit.",
            )
        await clock.sleep(YOUTUBE_POLL_INTERVAL_SECONDS)


def _requests_native_speed(speed: float) -> bool:
    return math.isclose(speed, 1.0)


def queue_speed_rejection(speed: float, replay: bool) -> bool:
    """Queue-wide replay rule. The queue decides when this runs, before authentication."""
    return replay and not _requests_native_speed(speed)


def item_speed_rejection(speed: float) -> _PlaybackFailure | None:
    """Item-level native-speed rule. Preview raises it at the original pre-modal point."""
    if _requests_native_speed(speed):
        return None
    return _PlaybackFailure(
        code="playback-speed-unavailable",
        message="YouTube lectures support only 1.0 playback speed.",
    )


async def play(
    page: Any,
    diagnostic: _PlayDiagnostic,
    *,
    replay: bool = False,
    on_opened: Callable[[], None] | None = None,
    on_started: Callable[[], None] | None = None,
    on_position: Callable[[float, float], None] | None = None,
) -> tuple[float, str]:
    diagnostic.stage = "frame"
    frame = await _youtube_frame(page)
    diagnostic.frame = frame
    diagnostic.frame_panopto = False
    diagnostic.frame_kind = _frame_kind(str(getattr(frame, "url", "") or ""), str(getattr(page, "url", "") or ""))
    diagnostic.stage = "playback"
    if on_opened is not None:
        on_opened()
    return await _play_youtube_video(frame, replay=replay, on_started=on_started, on_position=on_position)
