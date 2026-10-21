"""Failure and diagnostic records shared by preview and playback handlers.

Preview and handlers both update these records. Keeping them here avoids a handler
importing preview, which would cycle once preview calls the media table.
"""

from __future__ import annotations

import json
import math
from contextlib import suppress
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded

PLAY_BUTTON_SELECTOR = (
    "#playIconContainer, #playIcon, #player.is-splash, .play.material-icons, .play-pause, [aria-label='Play']"
)
PLAYER_MAX_SPLASH_CLICKS = 2
PLAYER_FRAME_WAIT_SECONDS = 30.0
PLAYER_POLL_INTERVAL_SECONDS = 1.0
PLAYER_STALL_SECONDS = 120.0
PLAYER_MAX_SECONDS = 5400.0
PLAYER_WATCH_SLACK_SECONDS = 300.0
POSITION_PROGRESS_INTERVAL_SECONDS = 30.0
PLAY_DIAGNOSTIC_PREFIX = "campusctl-play-diagnostic: "

CLASSIFY_PLAYER_PAGE_JS = r"""/* campusctl-classify-player-page */(splashSelector) => {
    const videoCount = document.querySelectorAll('video').length;
    if (videoCount > 0) return {page_state: 'ready', video_count: videoCount};
    const path = ((window.location && window.location.pathname) || '').toLowerCase();
    if (path.includes('/login') || path.includes('/auth') || path.includes('/signin') || path.includes('/sso')) {
        return {page_state: 'auth', video_count: videoCount};
    }
    const text = ((document.body && document.body.textContent) || '').toLowerCase();
    if (text.includes('being processed') || text.includes('under processing')
        || text.includes('처리 중') || text.includes('처리중')) {
        return {page_state: 'processing', video_count: videoCount};
    }
    if (splashSelector && document.querySelector(splashSelector)) {
        return {page_state: 'splash', video_count: videoCount};
    }
    return {page_state: 'unknown', video_count: videoCount};
}"""


class MediaKind(StrEnum):
    """Catalog media values that have a playback path. Catalog JSON stays plain strings."""

    VIDEO = "video"  # the Panopto path
    YOUTUBE = "youtube"


PAGE_STATES = frozenset({"ready", "auth", "processing", "splash", "unknown"})
PLAY_STAGES = frozenset({"start", "room", "row", "preview", "frame", "video", "playback", "verify", "close"})


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


def _frame_kind(frame_url: str, page_url: str) -> str:
    try:
        parsed = urlsplit(frame_url)
        host = (parsed.hostname or "").lower()
        page_host = (urlsplit(page_url).hostname or "").lower()
    except (TypeError, ValueError):
        return "other"
    if parsed.scheme == "about" and parsed.path == "blank":
        return "blank"
    if not host:
        return "other"
    if "panopto" in host:
        return "panopto"
    if host in {"youtube.com", "youtube-nocookie.com"} or host.endswith((".youtube.com", ".youtube-nocookie.com")):
        return "youtube"
    if host == page_host:
        return "lms"
    return "other"


class _PlayDiagnostic:
    """Sanitized per-item failure facts; only fixed tokens reach stderr."""

    __slots__ = (
        "frame",
        "frame_kind",
        "frame_panopto",
        "page_state",
        "splash_clicks",
        "splash_visible",
        "stage",
        "video_count",
    )

    def __init__(self) -> None:
        self.stage = "start"
        self.frame_panopto: bool | None = None
        self.frame_kind = "none"
        self.video_count = 0
        self.splash_visible = False
        self.splash_clicks = 0
        self.page_state = "unknown"
        self.frame: Any | None = None

    async def refresh(self) -> None:
        """Re-read the open player frame so a failure reports its page state."""
        frame = self.frame
        if frame is None:
            return
        with suppress(Exception):
            value = await bounded(
                frame.evaluate(CLASSIFY_PLAYER_PAGE_JS, PLAY_BUTTON_SELECTOR),
                PROTOCOL_TIMEOUT_SECONDS,
                "classifying the player page",
            )
            if isinstance(value, dict):
                state = value.get("page_state")
                if isinstance(state, str) and state in PAGE_STATES:
                    self.page_state = state
                count = value.get("video_count")
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    self.video_count = count
        with suppress(Exception):
            control = await _visible_locator(frame, PLAY_BUTTON_SELECTOR, "checking the player splash control")
            self.splash_visible = control is not None

    def record(self, index: int, reason_code: str) -> str:
        payload = {
            "schema_version": 2,
            "index": index,
            "stage": self.stage if self.stage in PLAY_STAGES else "unknown",
            "reason_code": reason_code,
            "frame_panopto": self.frame_panopto if isinstance(self.frame_panopto, bool) else None,
            "frame_kind": self.frame_kind
            if self.frame_kind in {"panopto", "youtube", "lms", "blank", "other"}
            else "none",
            "video_count": max(0, self.video_count),
            "splash_visible": bool(self.splash_visible),
            "splash_clicks": min(max(0, self.splash_clicks), PLAYER_MAX_SPLASH_CLICKS),
            "page_state": self.page_state if self.page_state in PAGE_STATES else "unknown",
        }
        return PLAY_DIAGNOSTIC_PREFIX + json.dumps(payload, separators=(",", ":"), sort_keys=True)


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


def _video_number(status: dict[str, Any], key: str) -> float:
    value = status.get(key)
    return float(value) if isinstance(value, int | float) and math.isfinite(float(value)) else 0.0


def _position_event_due(last_event_at: float | None, now: float) -> bool:
    return last_event_at is None or now - last_event_at >= POSITION_PROGRESS_INTERVAL_SECONDS


async def _visible_locator(page: Any, selector: str, what: str) -> Any | None:
    locator = page.locator(selector)
    count = await bounded(locator.count(), PROTOCOL_TIMEOUT_SECONDS, what)
    for index in range(count):
        candidate = locator.nth(index)
        if await bounded(candidate.is_visible(), PROTOCOL_TIMEOUT_SECONDS, what):
            return candidate
    return None
