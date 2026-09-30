from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from test_play import FIRST_ID, SECOND_ID, FakePage, _lecture, _prepare

from campusctl import cli
from campusctl.presentation import render_progress
from campusctl.providers.cnu import player


def _play(
    page: FakePage,
    lectures: list[dict[str, Any]],
    progress: Any = None,
) -> tuple[dict[str, Any], Any]:
    return asyncio.run(player.play_lectures(page, {"playback": {"default_speed": 1.0}}, lectures, progress=progress))


class FailingTTY(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.position_written = False
        self.fail_on_position_newline = True
        self.raised = 0

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if text.startswith("  ") and " / " in text:
            self.position_written = True
        if self.position_written and text == "\n" and self.fail_on_position_newline:
            self.fail_on_position_newline = False
            self.raised += 1
            raise OSError("fake terminal write failed")
        return super().write(text)


def _interrupt_after_position(monkeypatch: pytest.MonkeyPatch) -> None:
    reads = 0

    async def read_video_state(_frame: Any) -> dict[str, Any]:
        nonlocal reads
        reads += 1
        if reads == 1:
            return {
                "exists": True,
                "currentTime": 0.0,
                "duration": 10.0,
                "paused": True,
                "ended": False,
                "playedUntil": 0.0,
                "playbackRate": 1.0,
            }
        if reads == 2:
            return {
                "exists": True,
                "currentTime": 0.0,
                "duration": 10.0,
                "paused": False,
                "ended": False,
                "playedUntil": 0.0,
                "playbackRate": 1.0,
            }
        raise KeyboardInterrupt

    monkeypatch.setattr(player, "_read_video_state", read_video_state)


def test_progress_events_follow_two_lectures_in_playback_order(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(complete_after_play={"row-1": True, "row-2": True})
    _prepare(tmp_path, monkeypatch, page)
    events: list[dict[str, Any]] = []

    result, error = _play(page, [_lecture(FIRST_ID), _lecture(SECOND_ID)], events.append)

    assert error is None
    assert [item["outcome"] for item in result["items"]] == ["completed", "completed"]
    assert [(event["type"], event["entity_id"]) for event in events] == [
        ("started", FIRST_ID),
        ("position", FIRST_ID),
        ("verifying", FIRST_ID),
        ("finished", FIRST_ID),
        ("started", SECOND_ID),
        ("position", SECOND_ID),
        ("verifying", SECOND_ID),
        ("finished", SECOND_ID),
    ]
    assert events[0] == {
        "type": "started",
        "index": 1,
        "total": 2,
        "entity_id": FIRST_ID,
        "title": "row-1",
    }


def test_position_events_are_throttled_to_thirty_seconds(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page)
    clock = [0.0]
    reads = 0

    async def advance_clock(seconds: float) -> None:
        clock[0] += seconds

    async def read_video_state(_frame: Any) -> dict[str, Any]:
        nonlocal reads
        reads += 1
        if reads == 1:
            return {
                "exists": True,
                "currentTime": 0.0,
                "duration": 65.0,
                "paused": True,
                "ended": False,
                "playedUntil": 0.0,
                "playbackRate": 1.0,
            }
        position = min(clock[0], 65.0)
        return {
            "exists": True,
            "currentTime": position,
            "duration": 65.0,
            "paused": False,
            "ended": position >= 65.0,
            "playedUntil": position,
            "playbackRate": 1.0,
        }

    monkeypatch.setattr(
        player, "current_clock", lambda: SimpleNamespace(monotonic=lambda: clock[0], sleep=advance_clock)
    )
    monkeypatch.setattr(player, "_read_video_state", read_video_state)
    events: list[dict[str, Any]] = []

    result, error = _play(page, [_lecture(FIRST_ID)], events.append)

    assert error is None
    assert result["items"][0]["outcome"] == "completed"
    positions = [event["position_seconds"] for event in events if event["type"] == "position"]
    assert positions == [0.0, 30.0, 60.0]
    assert all(later - earlier >= 30 for earlier, later in zip(positions, positions[1:], strict=False))


def test_callback_exception_disables_progress_without_changing_playback(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FakePage(complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page)
    calls: list[dict[str, Any]] = []

    def broken_callback(event: dict[str, Any]) -> None:
        calls.append(event)
        raise RuntimeError("progress output failed")

    result, error = _play(page, [_lecture(FIRST_ID)], broken_callback)

    assert error is None
    assert result["items"][0]["outcome"] == "completed"
    assert [event["type"] for event in calls] == ["started"]
    assert ("close", "row-1") in page.calls


def test_json_play_output_is_compact_and_contains_no_progress_text(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page)

    exit_code = cli.main(["lectures", "play", FIRST_ID, "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert captured.out == json.dumps(json.loads(captured.out), ensure_ascii=False, separators=(",", ":")) + "\n"


def test_human_tty_early_stop_finishes_progress_on_a_clean_line(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(complete_after_play={"row-1": True}, fail_close=True)
    _prepare(tmp_path, monkeypatch, page)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)

    exit_code = cli.main(["lectures", "play", FIRST_ID])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.startswith(player.PLAY_DIAGNOSTIC_PREFIX)
    assert "\r  00:10 / 00:10\n  Done: failed\n" in captured.out
    assert captured.out.endswith("\n")


def test_human_play_survives_a_terminal_write_failure_after_position(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FakePage(complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page)
    stream = FailingTTY()
    monkeypatch.setattr(cli.sys, "stdout", stream)

    exit_code = cli.main(["lectures", "play", FIRST_ID])

    assert exit_code == 0
    assert stream.raised == 1


def test_terminal_write_failure_does_not_mask_keyboard_interrupt(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FakePage(complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)
    _interrupt_after_position(monkeypatch)
    stream = FailingTTY()
    monkeypatch.setattr(cli.sys, "stdout", stream)
    args = cli.build_parser().parse_args(["lectures", "play", FIRST_ID])
    args._output_mode = "human"

    with pytest.raises(KeyboardInterrupt):
        cli._dispatch(args)
    assert stream.raised == 1


def test_human_ctrl_c_closes_progress_line_and_reports_interrupted(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FakePage(complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)
    _interrupt_after_position(monkeypatch)
    stream = FailingTTY()
    stream.fail_on_position_newline = False
    monkeypatch.setattr(cli.sys, "stdout", stream)

    exit_code = cli.main(["lectures", "play", FIRST_ID])

    assert exit_code == 130
    assert "\r  00:00 / 00:10\nInterrupted.\n" in stream.getvalue()
    assert stream.raised == 0


def test_render_progress_is_ascii_and_title_fits_the_requested_width() -> None:
    stream = io.StringIO()

    line = render_progress(
        {"type": "started", "index": 1, "total": 3, "title": "Long 演題\nlecture title"},
        stream,
        28,
    )

    assert stream.getvalue() == line
    assert line.isascii()
    assert len(line) <= 28
    assert "\n" not in line

    stream = io.StringIO()
    assert (
        render_progress({"type": "position", "position_seconds": 750, "duration_seconds": 1680}, stream, 80)
        == "  12:30 / 28:00"
    )
