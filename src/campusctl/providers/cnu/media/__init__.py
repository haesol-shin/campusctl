"""Catalog media keys mapped to the Panopto and YouTube handlers.

`video` is the catalog label of the Panopto path, not a third player. Its playback
body stays in preview until P6; only the existing post-authentication SSO step is
callable from this table, so preview does not have to import itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..player_types import _PlaybackFailure
from . import panopto, youtube


@dataclass(frozen=True, slots=True)
class MediaHandler:
    name: str
    queue_speed_rejected: Callable[[float, bool], bool] | None
    item_speed_rejected: Callable[[float], _PlaybackFailure | None] | None
    prepare: Callable[[Any, dict[str, Any]], Awaitable[None]] | None
    play: Callable[..., Awaitable[tuple[float, str]]] | None


# Existing catalog strings. The media value set itself belongs to L5/L6.
# Panopto play stays None so preview keeps running the existing video body.
MEDIA_HANDLERS = {
    "video": MediaHandler(
        "panopto",
        queue_speed_rejected=None,
        item_speed_rejected=None,
        prepare=panopto.prepare,
        play=None,
    ),
    "youtube": MediaHandler(
        "youtube",
        queue_speed_rejected=youtube.queue_speed_rejection,
        item_speed_rejected=youtube.item_speed_rejection,
        prepare=None,
        play=youtube.play,
    ),
}
