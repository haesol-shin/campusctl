"""Progress line formatting and carriage-return/finish state."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from typing import Any, TextIO

from campusctl.text_layout import _ascii_truncate, _cells, _text


def _progress_time(value: object) -> str:
    try:
        seconds = max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        seconds = 0
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes:02d}:{seconds:02d}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def render_progress(event: dict[str, Any], stream: TextIO, width: int) -> str:
    """Write one ASCII playback-progress line without a newline and return it."""
    width = max(1, width)
    event_type = event.get("type")
    if event_type == "started":
        index = _text(str(event.get("index", "?")))
        total = _text(str(event.get("total", "?")))
        prefix = f"[{index}/{total}] Playing: "
        title = _text(event.get("title"), "Untitled lecture").encode("ascii", errors="replace").decode("ascii")
        title_width = width - _cells(prefix)
        line = (
            prefix + _ascii_truncate(title, title_width) if title_width > 0 else _ascii_truncate(prefix + title, width)
        )
    elif event_type == "position":
        line = f"  {_progress_time(event.get('position_seconds'))} / {_progress_time(event.get('duration_seconds'))}"
        line = _ascii_truncate(line, width)
    elif event_type == "verifying":
        line = _ascii_truncate("  Checking completion in the LMS...", width)
    elif event_type == "finished":
        outcome = _text(event.get("outcome"), "finished")
        line = _ascii_truncate(f"  Done: {outcome}", width)
    else:
        line = ""
    stream.write(line)
    return line


def _progress_output() -> tuple[Callable[[dict[str, Any]], None], Callable[[], None]]:
    is_tty = sys.stdout.isatty()
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    position_open = False
    previous_position = ""

    def finish() -> None:
        nonlocal position_open
        if position_open:
            position_open = False
            sys.stdout.write("\n")
            sys.stdout.flush()

    def report(event: dict[str, Any]) -> None:
        nonlocal position_open, previous_position
        if position_open:
            if is_tty and event.get("type") == "position":
                sys.stdout.write("\r")
                line = render_progress(event, sys.stdout, width)
                sys.stdout.write(" " * max(0, len(previous_position) - len(line)))
                previous_position = line
                sys.stdout.flush()
                return
            finish()
        if event.get("type") == "position" and is_tty:
            sys.stdout.write("\r")
            previous_position = render_progress(event, sys.stdout, width)
            position_open = True
            sys.stdout.flush()
        else:
            render_progress(event, sys.stdout, width)
            sys.stdout.write("\n")
            sys.stdout.flush()

    return report, finish
