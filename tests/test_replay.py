"""Replay runs the official controls and waits for this invocation's native end."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from test_play import FIRST_ID, SECOND_ID, FakeFrame, FakePage, _fast_player_sleeps, _lecture, _write_catalog

from campusctl.providers.cnu import player


class RecordingFrame(FakeFrame):
    """Official-player-shaped frame: rejected property setters cannot hide in evaluate()."""

    async def evaluate(self, script: str, *args: Any) -> dict[str, Any]:
        assert not re.search(r"\b(?:currentTime|playbackRate)\s*(?:=(?!=)|\+\+|--|\+=|-=)", script)
        assert not re.search(r"\b(?:visibilityState|hidden)\s*(?:=(?!=)|:)", script)
        assert not re.search(r"\.style\s*\.|\.classList\s*\.", script)
        assert "dispatchEvent" not in script and ".play(" not in script and ".pause(" not in script
        return await super().evaluate(script, *args)


class ReplayPage(FakePage):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.frame = RecordingFrame(self)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    page: ReplayPage,
    lectures: list[dict[str, Any]],
    *,
    speed: float = 1.0,
) -> tuple[list[dict[str, Any]], Any]:
    _write_catalog(tmp_path, lectures)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))

    async def login(*args: Any, **kwargs: Any) -> None:
        page.calls.append(("login", None))

    monkeypatch.setattr(player, "ensure_logged_in", login)
    _fast_player_sleeps(monkeypatch, page)
    selected = player.validate_requested_lectures([lecture["entity_id"] for lecture in lectures], lectures, replay=True)
    result, error = await player.play_lectures(
        page, {"playback": {"default_speed": 1.0}}, selected, speed=speed, replay=True
    )
    return result["items"], error


@pytest.mark.parametrize(
    ("completion", "state", "progress", "not_counted"),
    [
        ("complete", "F", None, False),
        ("recorded", "N", "15분/15분", True),
        ("incomplete", "F", None, False),
        ("incomplete", "N", "15분/15분", True),
    ],
)
def test_replay_opens_complete_and_recorded_catalog_or_live_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    completion: str,
    state: str,
    progress: str | None,
    not_counted: bool,
) -> None:
    lecture = _lecture(FIRST_ID, completion=completion)
    page = ReplayPage(
        states={"row-1": state},
        progress_by_row={"row-1": progress} if progress else None,
        not_counted_rows={"row-1"} if not_counted else None,
        complete_after_play={"row-1": True},
    )
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [lecture], speed=1.5))
    assert error is None
    assert items[0]["outcome"] == "completed"
    assert items[0]["replay_requested"] is True and items[0]["player_opened"] is True
    assert ("open-player", "row-1") in page.calls
    assert ("speed", 1.5) in page.calls and ("play-for", "row-1") in page.calls


def test_replay_does_not_credit_stale_f_without_native_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class NoEndFrame(RecordingFrame):
        async def evaluate(self, script: str, *args: Any) -> dict[str, Any]:
            state = await super().evaluate(script, *args)
            if "campusctl-read-video-state" in script and not state["paused"]:
                state.update(ended=False, currentTime=10.0, playedUntil=10.0)
            return state

    page = ReplayPage(states={"row-1": "F", "row-2": "N"})
    page.frame = NoEndFrame(page)
    lecture = _lecture(FIRST_ID, completion="complete")
    next_lecture = _lecture(SECOND_ID)
    monkeypatch.setattr(player, "PLAYER_STALL_SECONDS", 0.001)
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [lecture, next_lecture]))
    assert error is not None
    assert [item["outcome"] for item in items] == ["failed", "not-started"]
    assert [item["player_opened"] for item in items] == [True, False]
    assert all(item["replay_requested"] for item in items)
    assert [call for call in page.calls if call[0] == "open-player"] == [("open-player", "row-1")]
    catalog = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))
    assert catalog["lectures"][0]["completion"] == "complete"


def test_replay_validation_rejects_whole_queue_before_browser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = _lecture(FIRST_ID, completion="complete")
    closed = _lecture(SECOND_ID)
    closed["open"] = False
    for invalid in (closed, {**closed, "open": True, "media": "offline"}):
        with pytest.raises(Exception) as failure:
            player.validate_requested_lectures([FIRST_ID, SECOND_ID], [first, invalid], replay=True)
        assert failure.value.code in {"lecture-not-open", "lecture-not-playable"}
    assert player.validate_requested_lectures([FIRST_ID, FIRST_ID], [first], replay=True) == [first]
    with pytest.raises(Exception) as refusal:
        player.validate_requested_lectures([FIRST_ID], [first])
    assert refusal.value.code == "lecture-complete"


def test_replay_failure_before_modal_reports_not_opened(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    page = ReplayPage(states={"row-1": "F"}, fail_click_ids={"row-1"})
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [_lecture(FIRST_ID, completion="complete")]))
    assert error is not None
    assert items[0]["outcome"] == "failed" and items[0]["player_opened"] is False
    assert items[0]["replay_requested"] is True


@pytest.mark.parametrize("missing", ["frame", "video"])
def test_replay_modal_without_official_embed_is_not_player_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    class MissingVideoFrame(RecordingFrame):
        async def query_selector(self, selector: str) -> Any:
            if selector == "video":
                return None
            return await super().query_selector(selector)

        async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
            raise TimeoutError("video never appeared")

    class MissingEmbedPage(ReplayPage):
        async def query_selector(self, selector: str) -> Any:
            if missing == "frame" and selector == player.PLAYER_FRAME_SELECTOR:
                return None
            return await super().query_selector(selector)

    page = MissingEmbedPage(states={"row-1": "F"})
    if missing == "video":
        page.frame = MissingVideoFrame(page)
    monkeypatch.setattr(player, "PLAYER_FRAME_WAIT_SECONDS", 0.01)
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [_lecture(FIRST_ID, completion="complete")]))
    assert error is not None and items[0]["outcome"] == "failed"
    assert items[0]["player_opened"] is False and items[0]["replay_requested"] is True
    assert ("open-player", "row-1") in page.calls
    assert ("close", "row-1") in page.calls


def test_replay_does_not_downgrade_complete_to_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    page = ReplayPage(
        states={"row-1": "F"},
        not_counted_rows={"row-1"},
        progress_after_play={"row-1": "15분/15분"},
    )

    # A recorded live row after playback is reported, but existing complete history survives.
    def change_state_on_close() -> None:
        page.rows["row-1"]["state"] = "N"

    original = player._poll_completion

    async def poll(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        change_state_on_close()
        return await original(*args, **kwargs)

    monkeypatch.setattr(player, "_poll_completion", poll)
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [_lecture(FIRST_ID, completion="complete")]))
    assert error is None and items[0]["outcome"] == "recorded"
    catalog = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))
    assert catalog["lectures"][0]["completion"] == "complete"


def test_replay_youtube_uses_native_autoplay_and_refuses_non_native_speed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lecture = _lecture(FIRST_ID, completion="recorded", media="youtube")
    page = ReplayPage(states={"row-1": "F"}, youtube_rows={"row-1"})
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [lecture]))
    assert error is None and items[0]["outcome"] == "completed"
    assert items[0]["player_opened"] is True
    assert not [call for call in page.calls if call[0] in {"play", "speed"}]

    page = ReplayPage(states={"row-1": "F"}, youtube_rows={"row-1"})
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [lecture], speed=1.25))
    assert error is not None and error[0].code == "playback-speed-unavailable"
    assert items[0]["player_opened"] is False
    assert not [call for call in page.calls if call[0] == "open-player"]

    mixed = ReplayPage(states={"row-1": "F", "row-2": "F"}, youtube_rows={"row-2"})
    mixed_items, mixed_error = asyncio.run(
        _run(
            monkeypatch,
            tmp_path,
            mixed,
            [_lecture(FIRST_ID, completion="complete"), _lecture(SECOND_ID, completion="complete", media="youtube")],
            speed=1.25,
        )
    )
    assert mixed_error is not None and mixed_error[0].code == "playback-speed-unavailable"
    assert [item["outcome"] for item in mixed_items] == ["not-started", "not-started"]
    assert not [call for call in mixed.calls if call[0] == "open-player"]


def test_replay_live_closed_row_does_not_open_player(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    page = ReplayPage(states={"row-1": "F"})
    page.rows["row-1"]["openyn"] = "N"
    items, error = asyncio.run(_run(monkeypatch, tmp_path, page, [_lecture(FIRST_ID, completion="complete")]))
    assert error is not None and items[0]["outcome"] == "failed"
    assert items[0]["player_opened"] is False
    assert not [call for call in page.calls if call[0] == "open-player"]


@pytest.mark.chromium
def test_local_official_player_controls_do_not_write_media_or_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from html import escape

    from playwright.async_api import async_playwright

    inner = """<!doctype html><video></video>
<button aria-label="Play" id="play">Play</button><div class="item" data-speed="1.5">1.5x</div>
<script>
const video = document.querySelector('video');
const writes = []; window.writes = writes;
let rate = 1, playing = false, reads = 0, position = 4;
Object.defineProperties(video, {
  currentTime: {get: () => position, set: value => writes.push(['currentTime', value])},
  playbackRate: {get: () => rate, set: value => writes.push(['playbackRate', value])},
  duration: {get: () => 10},
  paused: {get: () => !playing},
  ended: {get: () => playing && reads >= 2},
  played: {get: () => ({length: playing ? 1 : 0, end: () => position})}
});
for (const property of ['hidden', 'visibilityState']) {
  Object.defineProperty(document, property, {
    configurable: true, get: () => property === 'hidden' ? false : 'visible',
    set: value => writes.push([property, value])
  });
}
document.getElementById('play').onclick = () => {playing = true; position = 4};
document.querySelector('[data-speed]').onclick = () => {rate = 1.5; writes.push(['menu', 1.5])};
setInterval(() => {if (playing && ++reads >= 2) position = 10}, 40);
</script>"""
    outer = (
        '<div id="row-1"><a data-act="titleDetailContents">Lecture</a></div>'
        '<div id="previewModal" style="display:none"><button id="btnPreviewClose">Close</button>'
        f'<iframe id="previewFrame" srcdoc="{escape(inner, quote=True)}"></iframe></div>'
        '<script>document.querySelector("a").onclick = () => '
        '{document.querySelector("#previewModal").style.display="block"};'
        'document.querySelector("#btnPreviewClose").onclick = () => '
        '{document.querySelector("#previewModal").style.display="none"};</script>'
    )

    async def scenario() -> None:
        async with async_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Playwright Chromium is not installed")
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(outer)

                async def no_expansion(*args: Any) -> None:
                    return None

                monkeypatch.setattr(player, "_expand_row_through_ui", no_expansion)
                opened: list[bool] = []
                await player._play_visible_lecture(
                    page, "row-1", 1.5, replay=True, on_opened=lambda: opened.append(True)
                )
                frame = await (await page.query_selector("#previewFrame")).content_frame()
                assert await frame.evaluate("window.writes") == [["menu", 1.5]]
                assert opened == [True]
                assert not await page.locator("#previewModal").is_visible()
            finally:
                await browser.close()

    asyncio.run(scenario())
