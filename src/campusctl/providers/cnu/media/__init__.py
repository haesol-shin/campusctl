"""Catalog media keys mapped to the Panopto and YouTube handlers.

`video` is the catalog label of the Panopto path, not a third player.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..player_types import MediaKind, _PlaybackFailure
from . import panopto, youtube


@dataclass(frozen=True, slots=True)
class MediaHandler:
    name: str
    queue_speed_rejected: Callable[[float, bool], bool] | None
    item_speed_rejected: Callable[[float], _PlaybackFailure | None] | None
    prepare: Callable[[Any, dict[str, Any]], Awaitable[None]] | None
    play: Callable[..., Awaitable[tuple[float, str]]] | None


# Keys are the catalog strings; MediaKind members compare and hash as those strings.
MEDIA_HANDLERS = {
    MediaKind.VIDEO: MediaHandler(
        "panopto",
        queue_speed_rejected=None,
        item_speed_rejected=None,
        prepare=panopto.prepare,
        play=panopto.play,
    ),
    MediaKind.YOUTUBE: MediaHandler(
        "youtube",
        queue_speed_rejected=youtube.queue_speed_rejection,
        item_speed_rejected=youtube.item_speed_rejection,
        prepare=None,
        play=youtube.play,
    ),
}


def handler_for(media: object) -> MediaHandler:
    """Missing, None and unknown media stay on Panopto, the same as queue preparation.

    Every input other than ``youtube`` still takes that path. This is not a new playability check.
    """
    found = MEDIA_HANDLERS.get(media) if isinstance(media, str) else None
    if found is None:
        return MEDIA_HANDLERS[MediaKind.VIDEO]
    return found
