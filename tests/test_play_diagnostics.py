from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_play import FIRST_ID, SECOND_ID, FakeFrame, FakePage, _lecture, _prepare

from campusctl.providers.cnu import player


class MissingFramePage(FakePage):
    async def query_selector(self, selector: str) -> Any:
        if selector == player.PLAYER_FRAME_SELECTOR:
            return None
        return await super().query_selector(selector)


class SplashOnlyFrame(FakeFrame):
    async def query_selector(self, selector: str) -> Any:
        return None

    async def evaluate(self, script: str, *args: Any) -> dict[str, Any]:
        if "campusctl-classify-player-page" in script:
            return {"page_state": "splash", "video_count": 0}
        return await super().evaluate(script, *args)


class CoverageFrame(FakeFrame):
    def __init__(self, page: FakePage, coverage: float, *, duration: float = 10.0) -> None:
        super().__init__(page)
        self.coverage = coverage
        self.duration = duration

    async def evaluate(self, script: str, *args: Any) -> dict[str, Any]:
        state = await super().evaluate(script, *args)
        if "campusctl-read-video-state" in script:
            state.update(
                ended=False,
                currentTime=self.duration,
                duration=self.duration,
                playedUntil=self.duration,
                playedCoverage=self.coverage,
            )
        return state


def _run(
    monkeypatch: pytest.MonkeyPatch, page: FakePage, *, replay: bool = False
) -> tuple[dict[str, Any], Any, list[float]]:
    clock = [0.0]

    async def advance(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(player, "current_clock", lambda: SimpleNamespace(monotonic=lambda: clock[0], sleep=advance))
    result, errors = asyncio.run(
        player.play_lectures(
            page,
            {"playback": {"default_speed": 1.0}},
            [_lecture(FIRST_ID), _lecture(SECOND_ID)],
            replay=replay,
        )
    )
    return result, errors, clock


@pytest.mark.parametrize("cause", ["missing", "not-lv", "closed", "hidden"])
def test_row_unavailable_stops_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cause: str
) -> None:
    page = FakePage(stuck_title_rows={"row-1"} if cause == "hidden" else None)
    if cause == "missing":
        del page.rows["row-1"]
    elif cause == "not-lv":
        page.rows["row-1"]["moduletype"] = "HW"
    elif cause == "closed":
        page.rows["row-1"]["openyn"] = "N"
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)

    result, errors, _clock = _run(monkeypatch, page)

    assert [item["outcome"] for item in result["items"]] == ["failed", "not-started"]
    assert result["items"][0]["reason_code"] == errors[0].code == "lecture-row-unavailable"
    assert result["items"][0]["player_opened"] is False
    assert len(capsys.readouterr().err.splitlines()) == 1


@pytest.mark.parametrize("missing", ["frame", "video"])
def test_player_wait_deadlines_and_sanitized_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    page = MissingFramePage() if missing == "frame" else FakePage()
    if missing == "video":
        page.frame = SplashOnlyFrame(page)
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)

    result, errors, clock = _run(monkeypatch, page)

    code = f"player-{missing}-unavailable"
    assert result["items"][0]["reason_code"] == errors[0].code == code
    assert result["items"][0]["player_opened"] is False
    assert clock[0] == (player.PLAYER_FRAME_WAIT_SECONDS if missing == "frame" else player.PLAYER_VIDEO_WAIT_SECONDS)
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(player.PLAY_DIAGNOSTIC_PREFIX)
    record = json.loads(lines[0].removeprefix(player.PLAY_DIAGNOSTIC_PREFIX))
    assert record == {
        "schema_version": 1,
        "index": 1,
        "stage": missing,
        "reason_code": code,
        "frame_panopto": None if missing == "frame" else True,
        "video_count": 0,
        "splash_visible": missing == "video",
        "splash_clicks": 0 if missing == "frame" else 2,
        "page_state": "unknown" if missing == "frame" else "splash",
    }
    for forbidden in (FIRST_ID, "course-a", "row-1", "http", "Embed.aspx", "panopto.example", str(tmp_path)):
        assert forbidden not in lines[0]
    assert len([call for call in page.calls if call[0] == "play"]) == (0 if missing == "frame" else 2)
    assert ("close", "row-1") in page.calls


@pytest.mark.parametrize(
    ("coverage", "replay", "outcome"),
    [(8.0, True, "completed"), (7.9, True, "failed"), (0.5, True, "failed"), (0.5, False, "completed")],
)
def test_replay_coverage_boundary_and_non_replay_seek_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    coverage: float,
    replay: bool,
    outcome: str,
) -> None:
    page = FakePage(complete_after_play={"row-1": True, "row-2": True})
    page.frame = CoverageFrame(page, coverage)
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)

    result, errors, clock = _run(monkeypatch, page, replay=replay)

    assert result["items"][0]["outcome"] == outcome
    assert result["items"][0]["player_opened"] is True
    if outcome == "completed":
        assert errors is None
        assert clock[0] == 0.0
    else:
        assert result["items"][0]["reason_code"] == errors[0].code == "playback-stalled"
        assert clock[0] == player.PLAYER_STALL_SECONDS
        assert result["items"][1]["outcome"] == "not-started"


def test_watch_limit_is_distinct_from_stall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    page.frame = CoverageFrame(page, 0.5)
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)
    monkeypatch.setattr(player, "PLAYER_MAX_SECONDS", 5.0)

    result, errors, clock = _run(monkeypatch, page, replay=True)

    assert result["items"][0]["reason_code"] == errors[0].code == "playback-timeout"
    assert clock[0] == 5.0
    assert result["items"][1]["outcome"] == "not-started"


def test_unknown_failure_keeps_generic_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(fail_click_ids={"row-1"})
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)

    result, errors, _clock = _run(monkeypatch, page)

    assert result["items"][0]["reason_code"] == errors[0].code == "playback-failed"


def test_page_classification_rejects_free_text() -> None:
    class UntrustedFrame:
        async def evaluate(self, *_args: Any) -> dict[str, Any]:
            return {"page_state": "untrusted page text", "video_count": "untrusted count"}

    diagnostic = player._PlayDiagnostic()
    diagnostic.frame = UntrustedFrame()
    asyncio.run(diagnostic.refresh())
    record = json.loads(diagnostic.record(1, "playback-failed").removeprefix(player.PLAY_DIAGNOSTIC_PREFIX))
    assert record["page_state"] == "unknown"
    assert record["video_count"] == 0
    assert "untrusted" not in diagnostic.record(1, "playback-failed")
