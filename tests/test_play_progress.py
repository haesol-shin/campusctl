from __future__ import annotations

import asyncio
import io
import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from test_play import FIRST_ID, SECOND_ID, THIRD_ID, FakePage, _lecture, _patch_playback_clock, _prepare

from campusctl import cli
from campusctl.command_types import CommandContext
from campusctl.commands import lectures as lectures_command
from campusctl.progress import render_progress
from campusctl.providers.cnu import player, player_preview, player_types
from campusctl.providers.cnu.media import panopto


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

    monkeypatch.setattr(panopto, "_read_video_state", read_video_state)


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

    _patch_playback_clock(monkeypatch, lambda: SimpleNamespace(monotonic=lambda: clock[0], sleep=advance_clock))
    monkeypatch.setattr(panopto, "_read_video_state", read_video_state)
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
    assert captured.err.startswith(player_types.PLAY_DIAGNOSTIC_PREFIX)
    assert "\r  00:10 / 00:10\n  Done: failed\n" in captured.out
    assert captured.out.endswith("\n")


def test_human_tty_progress_line_ends_before_stderr_diagnostic(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(complete_after_play={"row-1": True}, fail_close=True)
    _prepare(tmp_path, monkeypatch, page)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    monkeypatch.setattr(stream, "isatty", lambda: True)

    exit_code = cli.main(["lectures", "play", FIRST_ID])

    assert exit_code == 1
    content = stream.getvalue()
    progress_line = "\r  00:10 / 00:10\n  Done: failed\n"
    assert progress_line in content
    progress_end = content.index(progress_line) + len(progress_line)
    diagnostic_start = content.index(player_types.PLAY_DIAGNOSTIC_PREFIX)
    assert progress_end <= diagnostic_start


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
    args = cli.build_parser().parse_args(["lectures", "play", FIRST_ID])
    monkeypatch.setattr(cli.sys, "stdout", stream)
    with pytest.raises(KeyboardInterrupt):
        cli._dispatch(args, CommandContext(output_mode="human", interactive=False))
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


_EMIT_POINTS = ("started", "position", "verifying", "finished", "not-started")


def _is_emit(event: dict[str, Any], point: str) -> bool:
    if point == "not-started":
        return event.get("type") == "finished" and event.get("outcome") == "not-started"
    if point == "finished":
        return event.get("type") == "finished" and event.get("outcome") != "not-started"
    return event.get("type") == point


def _queue_for(point: str) -> list[dict[str, Any]]:
    if point == "not-started":
        return [_lecture(FIRST_ID), _lecture(SECOND_ID), _lecture(THIRD_ID)]
    return [_lecture(FIRST_ID), _lecture(SECOND_ID)]


def _page_for(point: str, *, fail_close: bool = False) -> FakePage:
    if point == "not-started":
        return FakePage(complete_after_play={"row-1": False}, fail_close=fail_close)
    return FakePage(complete_after_play={"row-1": True, "row-2": True}, fail_close=fail_close)


def _arm_emit(monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    if point == "not-started":
        monkeypatch.setattr(player, "COMPLETION_POLL_SECONDS", 0.01)
        monkeypatch.setattr(player, "COMPLETION_POLL_INTERVAL_SECONDS", 0.001)


def _completions(tmp_path: Any) -> dict[str, str]:
    catalog = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))
    return {item["entity_id"]: item["completion"] for item in catalog["lectures"]}


def _error_codes(error: Any) -> list[str]:
    if error is None:
        return []
    if isinstance(error, list):
        return [item.code for item in error]
    return [error.code]


def _close_attempts(page: FakePage) -> list[tuple[str, Any]]:
    return [call for call in page.calls if call[0] == "close-attempt"]


def _raising_progress(point: str, exc_type: type[BaseException]) -> tuple[list[dict[str, Any]], Any, list[int], Any]:
    calls: list[dict[str, Any]] = []
    late_writes: list[int] = []
    raised = False

    def progress(event: dict[str, Any]) -> None:
        nonlocal raised
        calls.append(event)
        if not raised and _is_emit(event, point):
            raised = True
            raise exc_type()

    real_write = player.write_catalog

    def traced_write(catalog: dict[str, Any], path: Any = None) -> Any:
        if raised:
            late_writes.append(1)
        return real_write(catalog, path)

    return calls, progress, late_writes, traced_write


@pytest.mark.parametrize("exc_type", [asyncio.CancelledError, KeyboardInterrupt])
@pytest.mark.parametrize("point", _EMIT_POINTS)
def test_progress_callback_cancellation_stops_the_queue(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, point: str, exc_type: type[BaseException]
) -> None:
    page = _page_for(point)
    _prepare(tmp_path, monkeypatch, page)
    _arm_emit(monkeypatch, point)
    calls, progress, late_writes, traced_write = _raising_progress(point, exc_type)
    monkeypatch.setattr(player, "write_catalog", traced_write)

    with pytest.raises(exc_type):
        _play(page, _queue_for(point), progress)

    assert late_writes == []
    assert len(_close_attempts(page)) == 1
    assert [call for call in page.calls if call[0] == "open-player"] == [("open-player", "row-1")]
    if point == "finished":
        assert _completions(tmp_path)[FIRST_ID] == "complete"
        assert _completions(tmp_path)[SECOND_ID] == "incomplete"
    else:
        assert set(_completions(tmp_path).values()) == {"incomplete"}
    if point == "not-started":
        assert [event["entity_id"] for event in calls if event.get("outcome") == "not-started"] == [SECOND_ID]
    else:
        assert all(event.get("entity_id") != SECOND_ID for event in calls)


@pytest.mark.parametrize("point", _EMIT_POINTS)
def test_ordinary_progress_callback_failure_keeps_today_result(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    page = _page_for(point)
    _prepare(tmp_path, monkeypatch, page)
    _arm_emit(monkeypatch, point)
    calls, progress, _late_writes, _traced_write = _raising_progress(point, RuntimeError)

    result, error = _play(page, _queue_for(point), progress)
    experiment_completions = _completions(tmp_path)

    control = _page_for(point)
    _prepare(tmp_path, monkeypatch, control)
    _arm_emit(monkeypatch, point)
    control_result, control_error = _play(control, _queue_for(point))

    assert _error_codes(error) == _error_codes(control_error)
    assert [item["entity_id"] for item in result["items"]] == [item["entity_id"] for item in control_result["items"]]
    assert [item["outcome"] for item in result["items"]] == [item["outcome"] for item in control_result["items"]]
    assert experiment_completions == _completions(tmp_path)
    assert calls[-1]["type"] == ("finished" if point == "not-started" else point)
    assert len(_close_attempts(page)) == len(_close_attempts(control))
    if point == "not-started":
        assert [event["entity_id"] for event in calls if event.get("outcome") == "not-started"] == [SECOND_ID]
        assert [item["outcome"] for item in result["items"]] == ["unverified", "not-started", "not-started"]


@pytest.mark.parametrize("exc_type", [asyncio.CancelledError, KeyboardInterrupt])
@pytest.mark.parametrize("point", ["started", "position"])
def test_progress_interrupt_wins_over_preview_close_failure(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, point: str, exc_type: type[BaseException]
) -> None:
    # origin/main non-callback interrupt: preview stores the primary error and a failed close does not replace it.
    page = _page_for(point, fail_close=True)
    _prepare(tmp_path, monkeypatch, page)
    _calls, progress, late_writes, traced_write = _raising_progress(point, exc_type)
    monkeypatch.setattr(player, "write_catalog", traced_write)

    with pytest.raises(exc_type):
        _play(page, _queue_for(point), progress)

    assert late_writes == []
    assert _close_attempts(page) == [("close-attempt", "row-1")]
    assert ("close", "row-1") not in page.calls
    assert set(_completions(tmp_path).values()) == {"incomplete"}


def test_progress_interrupt_wins_when_close_raises_another_base_exception(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _page_for("started")
    _prepare(tmp_path, monkeypatch, page)

    async def close_interrupted(_page: Any) -> None:
        page.calls.append(("close-attempt", page.active_row_id))
        raise KeyboardInterrupt

    monkeypatch.setattr(player_preview, "_close_player", close_interrupted)
    _calls, progress, late_writes, traced_write = _raising_progress("started", asyncio.CancelledError)
    monkeypatch.setattr(player, "write_catalog", traced_write)

    with pytest.raises(asyncio.CancelledError):
        _play(page, _queue_for("started"), progress)

    assert late_writes == []
    assert _close_attempts(page) == [("close-attempt", "row-1")]
    assert set(_completions(tmp_path).values()) == {"incomplete"}


def test_progress_callback_system_exit_propagates(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _page_for("position")
    _prepare(tmp_path, monkeypatch, page)
    _calls, progress, late_writes, traced_write = _raising_progress("position", SystemExit)
    monkeypatch.setattr(player, "write_catalog", traced_write)

    with pytest.raises(SystemExit):
        _play(page, _queue_for("position"), progress)

    assert late_writes == []
    assert _close_attempts(page) == [("close-attempt", "row-1")]


def test_human_play_exits_130_when_progress_callback_is_interrupted(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FakePage(complete_after_play={"row-1": True, "row-2": True})
    sessions = _prepare(tmp_path, monkeypatch, page, default_speed=1.0)

    def raising_progress() -> tuple[Any, Any]:
        def progress(_event: dict[str, Any]) -> None:
            raise KeyboardInterrupt

        return progress, lambda: None

    monkeypatch.setattr(lectures_command, "_progress_output", raising_progress)
    stream = FailingTTY()
    stream.fail_on_position_newline = False
    monkeypatch.setattr(cli.sys, "stdout", stream)

    exit_code = cli.main(["lectures", "play", FIRST_ID, SECOND_ID])

    assert exit_code == 130
    assert stream.getvalue().endswith("Interrupted.\n")
    assert sessions.active is False
    assert _close_attempts(page) == [("close-attempt", "row-1")]
    assert [call for call in page.calls if call[0] == "open-player"] == [("open-player", "row-1")]
    assert set(_completions(tmp_path).values()) == {"incomplete"}
