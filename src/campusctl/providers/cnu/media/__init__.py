"""Catalog media keys mapped to the Panopto and YouTube handlers.

`video` is the catalog label of the Panopto path, not a third player. Its playback
body stays in preview until P6; only the existing post-authentication SSO step is
callable from this table, so preview does not have to import itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.envelope import CampusError
from campusctl.wait_clock import current_clock

from ..courses import COURSE_LINK_SELECTOR
from ..login import MY_LECTURE_URL, ensure_logged_in
from ..player_types import _PlaybackFailure
from . import youtube

PANOPTO_SESSION_WAIT_SECONDS = 10.0

PANOPTO_LOGIN_JS = r"""() => Boolean(
    window.panoptoSaml &&
    window.panoptoSaml.panoptoSamlLogin_submit &&
    window.panoptoSaml.panoptoSamlLogin_submit()
)"""


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


class Media(StrEnum):
    VIDEO = "video"  # catalog label of the Panopto path
    YOUTUBE = "youtube"


@dataclass(frozen=True, slots=True)
class MediaHandler:
    name: str
    queue_speed_rejected: Callable[[float, bool], bool] | None
    item_speed_rejected: Callable[[float], _PlaybackFailure | None] | None
    prepare: Callable[[Any, dict[str, Any]], Awaitable[None]] | None
    play: Callable[..., Awaitable[tuple[float, str]]] | None


# Exactly video -> panopto and youtube -> youtube. Panopto play stays None so preview
# keeps running the existing video body.
MEDIA_HANDLERS = {
    Media.VIDEO: MediaHandler(
        "panopto",
        queue_speed_rejected=None,
        item_speed_rejected=None,
        prepare=_ensure_panopto_session,
        play=None,
    ),
    Media.YOUTUBE: MediaHandler(
        "youtube",
        queue_speed_rejected=youtube.queue_speed_rejection,
        item_speed_rejected=youtube.item_speed_rejection,
        prepare=None,
        play=youtube.play,
    ),
}
