## Changed

- Report specific play failure reason codes (`lecture-row-unavailable`, `player-frame-unavailable`, `player-video-unavailable`, `playback-stalled`, `playback-timeout`) instead of generic playback failures
- Emit one sanitized `campusctl-play-diagnostic:` JSON line on stderr for each failed lecture item

## Fixed

- Complete replay playback when this page session's covered play time reaches the duration tolerance even if the official player omits its end event
- Bounded Panopto splash play control clicks to at most twice instead of clicking on every poll
