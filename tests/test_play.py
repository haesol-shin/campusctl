from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from virtual_clock import VirtualClock, drive

from campusctl import browser as browser_module
from campusctl import cli
from campusctl.lock import exclusive_lock
from campusctl.providers.cnu import player
from campusctl.wait_clock import current_clock

GENERATED_AT = "2026-10-01T12:00:00Z"
FIRST_ID = "cnu_lecture:course-a:row-1"
SECOND_ID = "cnu_lecture:course-a:row-2"
THIRD_ID = "cnu_lecture:course-b:row-3"


def _lecture(
    entity_id: str,
    *,
    kind: str = "lecture",
    completion: str = "incomplete",
    media: str = "video",
    progress_text: str | None = None,
    attendance_counted: bool | None = None,
) -> dict[str, Any]:
    course_id, row_id = entity_id.split(":")[1:]
    return {
        "entity_id": entity_id,
        "course": {"id": course_id, "label": course_id},
        "kind": kind,
        "title": row_id,
        "week": "1",
        "sequence": "1",
        "progress_text": progress_text,
        "duration_minutes": None,
        "available_from": None,
        "due_date": None,
        "late_until": None,
        "media": media,
        "attendance_counted": attendance_counted,
        "open": True,
        "completion": completion,
        "provider_state": "F" if completion == "complete" else "N",
    }


def _write_catalog(data_dir: Path, lectures: list[dict[str, Any]] | None = None) -> None:
    path = data_dir / "catalog" / "lectures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": GENERATED_AT,
                "courses": [
                    {"course_id": "course-a", "label": "Course A", "class_no": None},
                    {"course_id": "course-b", "label": "Course B", "class_no": None},
                ],
                "lectures": lectures
                if lectures is not None
                else [_lecture(FIRST_ID), _lecture(SECOND_ID), _lecture(THIRD_ID)],
            }
        ),
        encoding="utf-8",
    )


class FakeRequest:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def get(self, url: str, **kwargs: Any) -> None:
        self.page.requests.append(("get", url, kwargs))

    async def post(self, url: str, **kwargs: Any) -> None:
        self.page.requests.append(("post", url, kwargs))


class FakeLocator:
    def __init__(
        self,
        page: FakePage,
        selector: str,
        frame: FakeFrame | None = None,
        index: int = 0,
    ) -> None:
        self.page = page
        self.selector = selector
        self.frame = frame
        self.index = index

    @property
    def first(self) -> FakeLocator:
        return self

    def nth(self, index: int) -> FakeLocator:
        return FakeLocator(self.page, self.selector, self.frame, index)

    def _row_id(self) -> str | None:
        match = re.search(r'\[id="([^"]+)"\]', self.selector)
        return match.group(1) if match and match.group(1) in self.page.rows else None

    async def count(self) -> int:
        if self.frame is not None:
            if "data-speed" in self.selector:
                return int(self.page.speed_option_present)
            if "play" in self.selector.lower():
                return 2 if self.page.hide_first_control else 1
            return 0
        if "btnPreviewClose" in self.selector or "btn_popClose" in self.selector:
            return int(self.page.player_open)
        week_tab = re.search(r'data-weekidx="([^"]+)"', self.selector)
        if week_tab:
            return int(week_tab.group(1) not in self.page.missing_week_tabs)
        if '[data-act="showAllWeek"]' in self.selector:
            return 1
        if self._row_id() is not None:
            return 1
        return 1

    async def get_attribute(self, name: str) -> str | None:
        row_id = self._row_id()
        if name == "data-weekno" and row_id is not None:
            return self.page.week_by_row[row_id]
        if name == "data-status" and '[data-act="showAllWeek"]' in self.selector:
            return self.page.show_all_status
        return None

    async def is_visible(self) -> bool:
        if self.frame is not None:
            return not (
                (self.page.hide_first_control and "play" in self.selector.lower() and self.index == 0)
                or (self.page.hide_speed_option and "data-speed" in self.selector)
            )
        week_tab = re.search(r'data-weekidx="([^"]+)"', self.selector)
        if week_tab:
            return week_tab.group(1) not in self.page.missing_week_tabs
        if '[data-act="showAllWeek"]' in self.selector:
            return True
        collapse = re.search(r'data-bs-target="#weekCollapse([^"]+)"', self.selector)
        if collapse:
            return self.page._week_is_displayed(collapse.group(1))
        row_id = self._row_id()
        if row_id is not None:
            if "titleDetailContents" in self.selector:
                return self.page._title_is_displayed(row_id)
            return self.page._row_is_displayed(row_id)
        week_container = re.search(r'\[id="weekIdx([^"]+)"\]', self.selector)
        if week_container:
            return self.page._week_is_displayed(week_container.group(1))
        return True

    async def scroll_into_view_if_needed(self, **kwargs: Any) -> None:
        self.page.calls.append(("scroll", self.selector))

    async def click(self, **kwargs: Any) -> None:
        if self.frame is not None:
            self.frame.clicks.append(self.selector)
            speed = re.search(r'data-speed="([0-9.]+)"', self.selector)
            if speed:
                self.page.calls.append(("speed", float(speed.group(1))))
                self.page.calls.append(("speed-force", bool(kwargs.get("force"))))
                if self.page.speed_applies:
                    self.frame.playback_rate = float(speed.group(1))
            elif "play" in self.selector.lower():
                self.frame.video_ready = True
                self.frame.paused = False
                self.page.play_started = True
                self.page.calls.append(("play", self.selector))
                self.page.calls.append(("play-for", self.page.active_row_id))
                self.page.calls.append(("play-index", self.index))
            return
        if self.page.player_open and "btnPreviewClose" not in self.selector and "btn_popClose" not in self.selector:
            self.page.player_control_clicks.append(self.selector)
        week_tab = re.search(r'data-weekidx="([^"]+)"', self.selector)
        if week_tab:
            week = week_tab.group(1)
            self.page.calls.append(("week-tab", week))
            self.page.selected_week = week
            return
        if '[data-act="showAllWeek"]' in self.selector:
            self.page.calls.append(("show-all-weeks",))
            self.page.show_all_weeks = True
            self.page.show_all_status = "close"
            return
        collapse = re.search(r'data-bs-target="#weekCollapse([^"]+)"', self.selector)
        if collapse:
            week = collapse.group(1)
            self.page.calls.append(("week-collapse", week))
            self.page.closed_rows = {
                row_id for row_id in self.page.closed_rows if self.page.week_by_row[row_id] != week
            }
            return
        if "btnPreviewClose" in self.selector or "btn_popClose" in self.selector:
            self.page.calls.append(("close-attempt", self.page.active_row_id))
            if self.page.fail_close:
                raise RuntimeError("fake close failed")
            self.page.player_open = False
            row = self.page.rows[self.page.active_row_id]
            if self.page.complete_after_play.get(self.page.active_row_id, False):
                row["state"] = "F"
            if self.page.active_row_id in self.page.progress_after_play:
                row["progress_text"] = self.page.progress_after_play[self.page.active_row_id]
            self.page.calls.append(("close", self.page.active_row_id))
            return
        if "titleDetailContents" in self.selector:
            match = re.search(r'\[id="([^"]+)"\]', self.selector)
            assert match is not None
            row_id = match.group(1)
            assert row_id == self.page.last_read_row_id
            if row_id in self.page.fail_click_ids:
                raise RuntimeError("fake title click failed")
            self.page.active_row_id = row_id
            self.page.frame.paused = True
            self.page.frame.playback_rate = self.page.initial_playback_rate
            self.page.frame.video_ready = not self.page.video_after_splash_click
            self.page.frame.pause_emitted = False
            self.page.play_started = False
            self.page.player_open = True
            self.page.calls.append(("open-player", row_id))
            return
        self.page.calls.append(("click", self.selector))


class FakeVideo:
    pass


class FakeFrame:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.clicks: list[str] = []
        self.paused = True
        self.playback_rate = 1.0
        self.video_ready = not page.video_after_splash_click
        self.pause_emitted = False
        self.youtube_reads = 0
        self.parent_frame = object()

    @property
    def url(self) -> str:
        if self.page.active_row_id in self.page.youtube_rows:
            return "https://www.youtube-nocookie.com/embed/fake-video"
        return "https://panopto.example/Embed.aspx"

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.page, selector, self)

    async def query_selector(self, selector: str) -> FakeVideo | None:
        return FakeVideo() if selector == "video" and self.video_ready else None

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        assert selector == "video"
        self.page.calls.append(("video-wait", self.video_ready))
        assert self.video_ready

    async def evaluate(self, script: str, *args: Any) -> dict[str, Any]:
        self.page.evaluations.append(script)
        if "campusctl-read-youtube-video-state" in script:
            if self.page.youtube_mode == "blocked":
                return {
                    "exists": True,
                    "currentTime": 0.0,
                    "duration": 15.0,
                    "paused": True,
                    "ended": False,
                    "playbackRate": self.page.initial_playback_rate,
                }
            if self.page.youtube_mode in {"stalled", "interrupt"}:
                self.youtube_reads += 1
                if self.youtube_reads == 1:
                    current_time, paused = 0.0, False
                elif self.youtube_reads == 2:
                    current_time, paused = 1.0, False
                elif self.page.youtube_mode == "interrupt":
                    raise KeyboardInterrupt
                else:
                    current_time, paused = 1.0, True
                return {
                    "exists": True,
                    "currentTime": current_time,
                    "duration": 15.0,
                    "paused": paused,
                    "ended": False,
                    "playbackRate": self.page.initial_playback_rate,
                }
            self.youtube_reads += 1
            if self.page.youtube_mode == "initializing" and self.youtube_reads <= 2:
                return {"exists": False}
            if self.youtube_reads == 1:
                current_time, ended = 0.0, False
            elif self.youtube_reads == 2:
                current_time, ended = 1.0, False
            else:
                current_time, ended = 15.0, True
            return {
                "exists": True,
                "currentTime": current_time,
                "duration": 15.0,
                "paused": False,
                "ended": ended,
                "playbackRate": self.page.initial_playback_rate,
            }
        assert "campusctl-read-video-state" in script
        if self.page.pause_once_after_start and self.page.play_started and not self.pause_emitted:
            self.pause_emitted = True
            self.paused = True
            return {
                "exists": True,
                "currentTime": 2.0,
                "duration": 10.0,
                "paused": True,
                "ended": False,
                "playedUntil": 2.0,
                "playbackRate": self.playback_rate,
            }
        if self.paused:
            return {
                "exists": True,
                "currentTime": 0.0,
                "duration": 10.0,
                "paused": True,
                "ended": False,
                "playedUntil": 0.0,
                "playbackRate": self.playback_rate,
            }
        return {
            "exists": True,
            "currentTime": 10.0,
            "duration": 10.0,
            "paused": False,
            "ended": True,
            "playedUntil": 10.0,
            "playbackRate": self.playback_rate,
        }


class FakeIframe:
    def __init__(self, frame: FakeFrame) -> None:
        self.frame = frame

    async def content_frame(self) -> FakeFrame:
        return self.frame


class FakeContext:
    def __init__(self, authenticated: bool = True) -> None:
        self.authenticated = authenticated

    async def cookies(self) -> list[dict[str, str]]:
        if not self.authenticated:
            return []
        return [{"name": ".ASPXAUTH", "domain": "panopto.example"}]


class FakePage:
    def __init__(
        self,
        states: dict[str, str] | None = None,
        *,
        complete_after_play: dict[str, bool] | None = None,
        fail_click_ids: set[str] | None = None,
        fail_close: bool = False,
        hide_first_control: bool = False,
        hide_speed_option: bool = False,
        speed_option_present: bool = True,
        speed_applies: bool = True,
        video_after_splash_click: bool = False,
        pause_once_after_start: bool = False,
        initial_playback_rate: float = 1.0,
        panopto_authenticated: bool = True,
        youtube_rows: set[str] | None = None,
        youtube_mode: str = "natural",
        progress_by_row: dict[str, str] | None = None,
        progress_after_play: dict[str, str] | None = None,
        not_counted_rows: set[str] | None = None,
        week_by_row: dict[str, str] | None = None,
        missing_week_tabs: set[str] | None = None,
        closed_rows: set[str] | None = None,
        stuck_title_rows: set[str] | None = None,
        all_weeks_open: bool = False,
        spinner_behavior: str = "absent",
    ) -> None:
        self.rows = {
            row_id: {"state": state, "moduletype": "LV", "openyn": "Y"}
            for row_id, state in (states or {"row-1": "N", "row-2": "N", "row-3": "N"}).items()
        }
        self.youtube_rows = youtube_rows or set()
        self.youtube_mode = youtube_mode
        self.progress_after_play = progress_after_play or {}
        progress_by_row = progress_by_row or {}
        not_counted_rows = not_counted_rows or set()
        for row_id, row in self.rows.items():
            row["progress_text"] = progress_by_row.get(row_id)
            row["attendance_counted"] = False if row_id in not_counted_rows else None
        default_weeks = {"row-1": "1", "row-2": "2", "row-3": "3"}
        default_weeks.update(week_by_row or {})
        self.week_by_row = {row_id: default_weeks.get(row_id, "1") for row_id in self.rows}
        self.missing_week_tabs = missing_week_tabs or set()
        self.closed_rows = closed_rows or set()
        self.stuck_title_rows = stuck_title_rows or set()
        self.selected_week = "1"
        self.show_all_weeks = all_weeks_open
        self.show_all_status = "close" if all_weeks_open else "open"
        self.spinner_behavior = spinner_behavior
        self.spinner_present = False
        self.complete_after_play = complete_after_play or {}
        self.fail_click_ids = fail_click_ids or set()
        self.fail_close = fail_close
        self.hide_first_control = hide_first_control
        self.hide_speed_option = hide_speed_option
        self.speed_option_present = speed_option_present
        self.speed_applies = speed_applies
        self.video_after_splash_click = video_after_splash_click
        self.pause_once_after_start = pause_once_after_start
        self.initial_playback_rate = initial_playback_rate
        self.play_started = False
        self.player_open = False
        self.active_row_id = ""
        self.last_read_row_id = ""
        self.current_course = ""
        self.calls: list[tuple[str, Any]] = []
        self.player_control_clicks: list[str] = []
        self.evaluations: list[str] = []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.request = FakeRequest(self)
        self.frame = FakeFrame(self)
        self.context = FakeContext(panopto_authenticated)

    @property
    def frames(self) -> list[FakeFrame]:
        return [self.frame] if self.player_open else []

    def _week_is_displayed(self, week: str) -> bool:
        return self.show_all_weeks or self.selected_week == week

    def _row_is_displayed(self, row_id: str) -> bool:
        return row_id in self.rows and self._week_is_displayed(self.week_by_row[row_id])

    def _title_is_displayed(self, row_id: str) -> bool:
        return self._row_is_displayed(row_id) and row_id not in self.closed_rows and row_id not in self.stuck_title_rows

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        state = kwargs.get("state")
        if selector == ".spinner-border":
            assert state == "hidden"
            self.calls.append(("spinner-wait", self.spinner_present))
            self.calls.append(("spinner-timeout-ms", kwargs.get("timeout")))
            if self.spinner_behavior == "stuck":
                raise TimeoutError("fake loading overlay stayed visible")
            if self.spinner_behavior == "delayed":
                assert self.spinner_present
                self.spinner_present = False
                self.calls.append(("spinner-cleared",))
            else:
                assert self.spinner_behavior == "absent"
                assert not self.spinner_present
            return
        if selector == player.LEARNING_ROW_SELECTOR:
            self.calls.append(("lecture-rows-wait", self.spinner_present))
            assert not self.spinner_present
            return
        if state == "visible":
            self.calls.append(("wait-visible", selector))
            for candidate in selector.split(","):
                if await self.locator(candidate.strip()).is_visible():
                    return
            raise TimeoutError("fake element stayed hidden")
        if "previewModal" in selector:
            assert self.player_open
        if selector == "iframe#previewFrame":
            assert self.player_open

    async def click(self, selector: str, **kwargs: Any) -> None:
        if self.player_open:
            self.player_control_clicks.append(selector)
        course = re.search(r'data-courseid="([^"]+)"', selector)
        if course:
            self.current_course = course.group(1)
            self.calls.append(("course", self.current_course))
        elif selector == 'a[href="/std/course"]':
            self.calls.append(("course-room", self.current_course))
            self.spinner_present = self.spinner_behavior in {"delayed", "stuck"}
        else:
            self.calls.append(("click", selector))

    async def query_selector(self, selector: str) -> FakeIframe | None:
        return FakeIframe(self.frame) if selector == "iframe#previewFrame" and self.player_open else None

    async def evaluate(self, script: str, *args: Any) -> Any:
        self.evaluations.append(script)
        if "panoptoSamlLogin_submit" in script:
            return False
        if "campusctl-read-collapse-toggles" in script:
            row_id = args[0]
            if row_id in self.closed_rows:
                week = self.week_by_row[row_id]
                return [f'[data-bs-target="#weekCollapse{week}"]']
            return []
        if "campusctl-read-lecture-row" in script:
            row_id = args[0]
            self.last_read_row_id = row_id
            return dict(self.rows[row_id]) if row_id in self.rows else None
        raise AssertionError(f"unexpected page evaluation: {script[:80]}")


class FakeSessions:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.opens = 0
        self.active = False

    @asynccontextmanager
    async def open_session(
        self,
        config: dict[str, Any],
        *,
        data_dir: Path | None = None,
        headless: bool = False,
        operation: str | None = None,
    ):
        assert not headless and operation == "lectures.play"
        self.opens += 1
        self.active = True
        try:
            yield type("Session", (), {"page": self.page})()
        finally:
            self.active = False


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: FakePage,
    *,
    lectures: list[dict[str, Any]] | None = None,
    default_speed: float = 1.25,
) -> FakeSessions:
    _write_catalog(tmp_path, lectures)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {"playback": {"default_speed": default_speed}})
    sessions = FakeSessions(page)
    monkeypatch.setattr(browser_module, "open_session", sessions.open_session)

    async def fake_login(*args: Any, **kwargs: Any) -> None:
        page.calls.append(("login", None))

    monkeypatch.setattr(player, "ensure_logged_in", fake_login)
    return sessions


def _invoke(capsys: pytest.CaptureFixture[str], entity_ids: list[str], *options: str) -> tuple[int, dict[str, Any]]:
    code = cli.main(["lectures", "play", *entity_ids, *options, "--json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, json.loads(captured.out)


def _fast_player_sleeps(monkeypatch: pytest.MonkeyPatch, page: FakePage) -> None:
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        page.calls.append(("sleep", seconds))
        await real_sleep(min(seconds, 0.001))

    monkeypatch.setattr(
        player, "current_clock", lambda: SimpleNamespace(monotonic=current_clock().monotonic, sleep=fast_sleep)
    )


def _assert_no_dom_visibility_mutations(page: FakePage) -> None:
    mutation = re.compile(r"\.style(?:\.|\[|\s*=)|\.classList\.(?:add|remove|toggle|replace)\s*\(")
    assert not any(mutation.search(script) for script in page.evaluations)


def test_play_selects_hidden_week_and_expands_its_closed_accordion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        complete_after_play={"row-1": True},
        week_by_row={"row-1": "4"},
        closed_rows={"row-1"},
        spinner_behavior="delayed",
    )
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert ("spinner-wait", True) in page.calls
    assert ("lecture-rows-wait", False) in page.calls
    assert ("week-tab", "4") in page.calls
    assert ("week-collapse", "4") in page.calls
    assert page.calls.index(("spinner-wait", True)) < page.calls.index(("week-tab", "4"))
    assert page.calls.index(("week-tab", "4")) < page.calls.index(("week-collapse", "4"))
    assert page.calls.index(("week-collapse", "4")) < page.calls.index(("open-player", "row-1"))
    _assert_no_dom_visibility_mutations(page)


def test_play_uses_show_all_weeks_when_the_official_week_tab_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        complete_after_play={"row-1": True},
        week_by_row={"row-1": "4"},
        missing_week_tabs={"4"},
    )
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert page.calls.count(("show-all-weeks",)) == 1
    assert ("week-tab", "4") not in page.calls
    _assert_no_dom_visibility_mutations(page)


def test_play_does_not_toggle_show_all_weeks_when_already_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        complete_after_play={"row-1": True},
        week_by_row={"row-1": "4"},
        missing_week_tabs={"4"},
        all_weeks_open=True,
    )
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert not [call for call in page.calls if call[0] == "show-all-weeks"]


def test_course_room_does_not_wait_for_a_loading_overlay_that_never_appears() -> None:
    page = FakePage(spinner_behavior="absent")

    asyncio.run(player._enter_course_room(page, "course-a"))

    assert ("spinner-wait", False) in page.calls
    assert ("spinner-timeout-ms", 7000) in page.calls
    assert ("lecture-rows-wait", False) in page.calls


def test_course_room_waits_for_a_delayed_loading_overlay_to_clear() -> None:
    page = FakePage(spinner_behavior="delayed")

    asyncio.run(player._enter_course_room(page, "course-a"))

    assert ("spinner-cleared",) in page.calls
    assert page.calls.index(("spinner-wait", True)) < page.calls.index(("spinner-cleared",))
    assert page.calls.index(("spinner-cleared",)) < page.calls.index(("lecture-rows-wait", False))
    assert ("spinner-timeout-ms", 7000) in page.calls


def test_course_room_stops_when_the_loading_overlay_never_clears() -> None:
    page = FakePage(spinner_behavior="stuck")

    with pytest.raises(player.CampusError) as error:
        asyncio.run(player._enter_course_room(page, "course-a"))

    assert error.value.code == "browser-timeout"
    assert ("spinner-wait", True) in page.calls
    assert ("spinner-timeout-ms", 7000) in page.calls
    assert not [call for call in page.calls if call[0] == "lecture-rows-wait"]


def test_play_pauses_queue_when_the_lecture_title_stays_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        week_by_row={"row-1": "4"},
        stuck_title_rows={"row-1"},
    )
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 1
    assert [item["outcome"] for item in response["result"]["items"]] == ["failed", "not-started"]
    assert response["errors"][0]["code"] == "playback-failed"
    assert "remained hidden" in response["errors"][0]["message"]
    assert not [call for call in page.calls if call[0] == "open-player"]
    _assert_no_dom_visibility_mutations(page)


def test_validation_rejects_unknown_nonlecture_and_complete_before_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unsupported_id = "cnu_lecture:course-a:assignment-1"
    non_lv_id = "cnu_lecture:course-b:row-nonlv"
    non_lv_record = _lecture(non_lv_id)
    non_lv_record["moduletype"] = "HW"
    cases = [
        ("cnu_lecture:course-a:unknown", [_lecture(FIRST_ID)], "lecture-unknown"),
        (unsupported_id, [_lecture(FIRST_ID), _lecture(unsupported_id, kind="assignment")], "lecture-unsupported"),
        (non_lv_id, [_lecture(FIRST_ID), non_lv_record], "lecture-unsupported"),
        (FIRST_ID, [_lecture(FIRST_ID, completion="complete")], "lecture-complete"),
        (FIRST_ID, [_lecture(FIRST_ID, completion="recorded")], "lecture-complete"),
    ]
    for entity_id, lectures, expected_code in cases:
        page = FakePage()
        sessions = _prepare(tmp_path, monkeypatch, page, lectures=lectures)

        exit_code, response = _invoke(capsys, [FIRST_ID, entity_id])

        assert exit_code == 2
        assert response["errors"][0]["code"] == expected_code
        assert "items" not in response["result"]
        assert sessions.opens == 0


def test_closed_lecture_is_rejected_before_opening_session_for_entire_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    closed = _lecture(SECOND_ID)
    closed["open"] = False
    closed["available_from"] = "2026-10-15T00:00"
    sessions = _prepare(tmp_path, monkeypatch, FakePage(), lectures=[_lecture(FIRST_ID), closed])

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 2
    assert response["status"] == "user-action"
    assert response["errors"][0]["code"] == "lecture-not-open"
    assert "2026-10-15" in response["errors"][0]["message"]
    assert "items" not in response["result"]
    assert sessions.opens == 0


def test_unknown_open_status_passes_catalog_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lecture = _lecture(FIRST_ID)
    lecture["open"] = None
    sessions = _prepare(tmp_path, monkeypatch, FakePage(), lectures=[lecture])
    selected: list[dict[str, Any]] = []

    async def fake_play(page: Any, config: dict[str, Any], lectures: list[dict[str, Any]], **kwargs: Any):
        selected.extend(lectures)
        return {"items": []}, None

    monkeypatch.setattr(player, "play_lectures", fake_play)
    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["status"] == "ok"
    assert selected == [lecture]
    assert sessions.opens == 1


def test_offline_and_other_media_are_rejected_with_supported_types_before_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for media in ("offline", "other"):
        sessions = _prepare(tmp_path, monkeypatch, FakePage(), lectures=[_lecture(FIRST_ID, media=media)])

        exit_code, response = _invoke(capsys, [FIRST_ID])

        assert exit_code == 2
        assert response["errors"][0]["code"] == "lecture-not-playable"
        assert "video and YouTube" in response["errors"][0]["message"]
        assert sessions.opens == 0


def test_visible_playback_updates_only_observed_catalog_record_and_has_no_attendance_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(complete_after_play={"row-1": True}, hide_first_control=True, hide_speed_option=True)
    assert not asyncio.run(page.frame.locator('[data-speed="1.25"]').is_visible())
    sessions = _prepare(tmp_path, monkeypatch, page)
    real_write_catalog = player.write_catalog

    def assert_locked_write(catalog: dict[str, Any], path: Path | None = None) -> Path:
        assert sessions.active
        return real_write_catalog(catalog, path)

    monkeypatch.setattr(player, "write_catalog", assert_locked_write)

    exit_code, response = _invoke(capsys, [FIRST_ID, FIRST_ID])

    assert exit_code == 0
    item = response["result"]["items"][0]
    assert len(response["result"]["items"]) == 1
    assert item["entity_id"] == FIRST_ID
    assert item["outcome"] == "completed"
    assert isinstance(item["elapsed_seconds"], int | float) and item["elapsed_seconds"] >= 0
    assert isinstance(item["watch_time"], str) and re.fullmatch(r"\d{2,}:\d{2}:\d{2}", item["watch_time"])
    assert item["provider_state"] == "F"
    assert ("speed", 1.25) in page.calls
    assert ("speed-force", True) in page.calls
    assert ("play-index", 1) in page.calls
    catalog = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))
    records = {lecture["entity_id"]: lecture for lecture in catalog["lectures"]}
    assert records[FIRST_ID]["completion"] == "complete"
    assert records[SECOND_ID]["completion"] == "incomplete"
    assert records[THIRD_ID]["title"] == "row-3"
    assert sessions.opens == 1
    assert not any("attendManage/reCalculateAttend" in url for _, url, _ in page.requests)
    assert not any("attendManage/reCalculateAttend" in script for script in page.evaluations)


def test_already_complete_provider_row_is_reported_and_queue_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(states={"row-1": "F", "row-2": "N"}, complete_after_play={"row-2": True})
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID, FIRST_ID])

    assert exit_code == 0
    assert [item["outcome"] for item in response["result"]["items"]] == ["already-complete", "completed"]
    assert [call for call in page.calls if call[0] == "open-player"] == [("open-player", "row-2")]
    catalog = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))
    records = {lecture["entity_id"]: lecture for lecture in catalog["lectures"]}
    assert records[FIRST_ID]["completion"] == "complete"
    assert records[FIRST_ID]["provider_state"] == "F"
    assert records[SECOND_ID]["completion"] == "complete"


def test_unverified_playback_pauses_queue_and_reports_remaining_as_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(complete_after_play={"row-1": False})
    _prepare(tmp_path, monkeypatch, page)
    monkeypatch.setattr(player, "COMPLETION_POLL_SECONDS", 0.01)
    monkeypatch.setattr(player, "COMPLETION_POLL_INTERVAL_SECONDS", 0.001)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID, THIRD_ID])

    assert exit_code == 1
    assert response["status"] == "partial"
    assert response["errors"][0]["code"] == "playback-unverified"
    assert [item["outcome"] for item in response["result"]["items"]] == [
        "unverified",
        "not-started",
        "not-started",
    ]
    assert [call for call in page.calls if call[0] == "open-player"] == [("open-player", "row-1")]


def test_youtube_autoplay_to_natural_end_records_full_not_counted_watch_and_continues_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        youtube_rows={"row-1"},
        youtube_mode="initializing",
        progress_by_row={"row-1": "0분/15분"},
        progress_after_play={"row-1": "15분/15분"},
        not_counted_rows={"row-1"},
        complete_after_play={"row-2": True},
    )
    lectures = [
        _lecture(
            FIRST_ID,
            media="youtube",
            progress_text="0분/15분",
            attendance_counted=False,
        ),
        _lecture(SECOND_ID),
    ]
    _prepare(tmp_path, monkeypatch, page, lectures=lectures, default_speed=1.25)
    _fast_player_sleeps(monkeypatch, page)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_resolve_output_mode", lambda *args, **kwargs: "human")
    monkeypatch.setattr(cli, "_progress_output", lambda: (events.append, lambda: None))

    exit_code = cli.main(["lectures", "play", FIRST_ID, SECOND_ID, "--speed", "1.0"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert output.err == ""
    assert "Watched (not counted):" in output.out
    assert "The LMS row reports full watch progress" in output.out
    assert "Completed:" in output.out
    assert "Playback: 1 completed, 1 recorded." in output.out
    assert any(event.get("type") == "position" for event in events)
    assert ("play-for", "row-1") not in page.calls
    end_wait = page.calls.index(("sleep", player.YOUTUBE_AFTER_END_SECONDS))
    close = page.calls.index(("close", "row-1"))
    assert end_wait < close
    catalog = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))
    records = {lecture["entity_id"]: lecture for lecture in catalog["lectures"]}
    assert records[FIRST_ID]["completion"] == "recorded"
    assert records[FIRST_ID]["progress_text"] == "15분/15분"
    assert records[FIRST_ID]["attendance_counted"] is False
    assert records[SECOND_ID]["completion"] == "complete"


def test_live_not_counted_full_watch_is_recorded_without_replaying_or_stopping_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        youtube_rows={"row-1"},
        progress_by_row={"row-1": "15분/15분"},
        not_counted_rows={"row-1"},
        complete_after_play={"row-2": True},
    )
    lectures = [
        _lecture(FIRST_ID, media="youtube", progress_text="0분/15분", attendance_counted=False),
        _lecture(SECOND_ID),
    ]
    _prepare(tmp_path, monkeypatch, page, lectures=lectures, default_speed=1.25)
    _fast_player_sleeps(monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 0
    assert [item["outcome"] for item in response["result"]["items"]] == ["recorded", "completed"]
    assert response["result"]["items"][0]["elapsed_seconds"] == 0.0
    assert response["result"]["items"][0]["watch_time"] is None
    assert [call for call in page.calls if call[0] == "open-player"] == [("open-player", "row-2")]
    records = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))["lectures"]
    first = next(record for record in records if record["entity_id"] == FIRST_ID)
    assert first["completion"] == "recorded"
    assert first["progress_text"] == "15분/15분"
    assert first["attendance_counted"] is False


def test_youtube_provider_state_f_is_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        youtube_rows={"row-1"},
        not_counted_rows={"row-1"},
        complete_after_play={"row-1": True},
    )
    _prepare(
        tmp_path,
        monkeypatch,
        page,
        lectures=[_lecture(FIRST_ID, media="youtube", attendance_counted=False)],
        default_speed=1.0,
    )
    _fast_player_sleeps(monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert response["result"]["items"][0]["provider_state"] == "F"


def test_attendance_counted_youtube_without_state_f_is_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(youtube_rows={"row-1"}, progress_by_row={"row-1": "15분/15분"})
    _prepare(
        tmp_path,
        monkeypatch,
        page,
        lectures=[_lecture(FIRST_ID, media="youtube", progress_text="0분/15분")],
        default_speed=1.0,
    )
    _fast_player_sleeps(monkeypatch, page)
    monkeypatch.setattr(player, "COMPLETION_POLL_SECONDS", 0.01)
    monkeypatch.setattr(player, "COMPLETION_POLL_INTERVAL_SECONDS", 0.001)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 1
    assert response["errors"][0]["code"] == "playback-unverified"
    assert response["result"]["items"][0]["outcome"] == "unverified"
    assert response["result"]["items"][0]["provider_state"] == "N"


def test_youtube_only_playback_does_not_require_a_panopto_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        panopto_authenticated=False,
        youtube_rows={"row-1"},
        complete_after_play={"row-1": True},
    )
    _prepare(tmp_path, monkeypatch, page, lectures=[_lecture(FIRST_ID, media="youtube")], default_speed=1.0)
    _fast_player_sleeps(monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert ("close", "row-1") in page.calls
    assert not page.player_open


def test_youtube_autoplay_blocked_fails_without_player_control_click(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(youtube_rows={"row-1"}, youtube_mode="blocked")
    _prepare(
        tmp_path,
        monkeypatch,
        page,
        lectures=[_lecture(FIRST_ID, media="youtube")],
        default_speed=1.0,
    )
    _fast_player_sleeps(monkeypatch, page)
    monkeypatch.setattr(player, "YOUTUBE_AUTOPLAY_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(player, "PLAYER_POLL_INTERVAL_SECONDS", 0.001)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 1
    assert response["errors"][0]["code"] == "youtube-autoplay-blocked"
    assert response["result"]["items"][0]["outcome"] == "failed"
    assert ("close", "row-1") in page.calls
    assert ("play-for", "row-1") not in page.calls
    assert not page.frame.clicks
    assert not page.player_control_clicks


def test_youtube_stall_after_two_minutes_fails_and_closes_modal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(youtube_rows={"row-1"}, youtube_mode="stalled")
    _prepare(tmp_path, monkeypatch, page, lectures=[_lecture(FIRST_ID, media="youtube")], default_speed=1.0)
    clock = [0.0]

    async def advance(seconds: float) -> None:
        page.calls.append(("sleep", seconds))
        clock[0] += seconds

    monkeypatch.setattr(player, "current_clock", lambda: SimpleNamespace(monotonic=lambda: clock[0], sleep=advance))

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 1
    assert response["errors"][0]["code"] == "playback-failed"
    assert response["result"]["items"][0]["outcome"] == "failed"
    assert clock[0] > player.PLAYER_STALL_SECONDS
    assert ("close", "row-1") in page.calls
    assert not page.player_open


def test_ctrl_c_during_youtube_playback_closes_modal_and_releases_session_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FakePage(youtube_rows={"row-1"}, youtube_mode="interrupt")
    sessions = _prepare(tmp_path, monkeypatch, page, lectures=[_lecture(FIRST_ID, media="youtube")], default_speed=1.0)
    _fast_player_sleeps(monkeypatch, page)
    lock_path = tmp_path / "session.lock"
    open_fake_session = sessions.open_session

    @asynccontextmanager
    async def locked_session(
        config: dict[str, Any],
        *,
        data_dir: Path | None = None,
        headless: bool = False,
        operation: str | None = None,
    ):
        with exclusive_lock(lock_path):
            async with open_fake_session(config, data_dir=data_dir, headless=headless, operation=operation) as session:
                yield session

    monkeypatch.setattr(browser_module, "open_session", locked_session)
    args = cli.build_parser().parse_args(["lectures", "play", FIRST_ID, "--json"])
    args._output_mode = "json"
    args._interactive = False

    with pytest.raises(KeyboardInterrupt):
        cli._dispatch(args)

    assert page.frame.youtube_reads == 3
    assert ("close", "row-1") in page.calls
    assert not page.player_open
    assert not sessions.active
    with exclusive_lock(lock_path):
        pass


def test_youtube_non_1x_speed_fails_before_opening_modal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(youtube_rows={"row-1"})
    _prepare(tmp_path, monkeypatch, page, lectures=[_lecture(FIRST_ID, media="youtube")])

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 1
    assert response["errors"][0]["code"] == "playback-speed-unavailable"
    assert "only 1.0" in response["errors"][0]["message"]
    assert not [call for call in page.calls if call[0] == "open-player"]
    assert not any("youtube-video-state" in script for script in page.evaluations)


def test_youtube_non_1x_native_rate_fails_without_player_control_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(youtube_rows={"row-1"}, initial_playback_rate=1.25)
    _prepare(
        tmp_path,
        monkeypatch,
        page,
        lectures=[_lecture(FIRST_ID, media="youtube")],
        default_speed=1.0,
    )
    _fast_player_sleeps(monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 1
    assert response["errors"][0]["code"] == "playback-speed-unavailable"
    assert response["result"]["items"][0]["outcome"] == "failed"
    assert ("close", "row-1") in page.calls
    assert not any(call[0] in {"play", "play-for", "speed", "speed-force"} for call in page.calls)


def test_failed_playback_pauses_queue_and_marks_remaining_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(fail_click_ids={"row-1"})
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 1
    assert response["status"] == "partial"
    assert response["errors"][0]["code"] == "playback-failed"
    assert [item["outcome"] for item in response["result"]["items"]] == ["failed", "not-started"]
    failed = response["result"]["items"][0]
    assert failed["reason_code"] == response["errors"][0]["code"]
    assert not [call for call in page.calls if call[0] == "open-player"]


def test_login_failure_reports_every_item_not_started_without_opening_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage()
    sessions = _prepare(tmp_path, monkeypatch, page)

    async def login_failure(*args: Any, **kwargs: Any) -> None:
        raise player.CampusError("login-failed", "Login failed safely.", None)

    monkeypatch.setattr(player, "ensure_logged_in", login_failure)
    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 2
    assert response["errors"][0]["code"] == "login-failed"
    assert [item["outcome"] for item in response["result"]["items"]] == ["not-started", "not-started"]
    assert not [call for call in page.calls if call[0] == "open-player"]
    assert sessions.opens == 1
    assert response["result"]["items"][0]["provider_state"] is None


def test_player_close_failure_preserves_failed_item_and_pauses_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(complete_after_play={"row-1": True}, fail_close=True)
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 1
    assert response["status"] == "partial"
    assert response["errors"][0]["code"] == "playback-failed"
    assert [item["outcome"] for item in response["result"]["items"]] == ["failed", "not-started"]
    assert isinstance(response["result"]["items"][0]["elapsed_seconds"], int | float)
    assert ("close-attempt", "row-1") in page.calls
    catalog = json.loads((tmp_path / "catalog" / "lectures.json").read_text(encoding="utf-8"))
    assert next(item for item in catalog["lectures"] if item["entity_id"] == FIRST_ID)["completion"] == "incomplete"


def test_missing_youtube_frame_reaches_virtual_deadline() -> None:
    clock = VirtualClock()
    with pytest.raises(player._PlaybackFailure) as caught:
        asyncio.run(drive(player._youtube_frame(SimpleNamespace(frames=[])), clock))
    assert caught.value.message == "The YouTube embed frame did not appear."
    assert clock.now() == player.PLAYER_FRAME_WAIT_SECONDS
    assert not clock.sleepers


def test_panopto_splash_control_creates_video_before_waiting_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(video_after_splash_click=True)
    page.player_open = True
    monkeypatch.setattr(player, "PLAYER_POLL_INTERVAL_SECONDS", 0.0)

    frame = asyncio.run(player._player_frame(page))

    assert frame is page.frame
    assert page.frame.video_ready
    play_index = next(index for index, call in enumerate(page.calls) if call[0] == "play")
    video_wait_index = next(index for index, call in enumerate(page.calls) if call[0] == "video-wait")
    assert play_index < video_wait_index


@pytest.mark.parametrize(("speed_option_present", "speed_applies"), [(False, True), (True, False)])
def test_speed_selection_failure_pauses_queue_before_playback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    speed_option_present: bool,
    speed_applies: bool,
) -> None:
    page = FakePage(speed_option_present=speed_option_present, speed_applies=speed_applies)
    _prepare(tmp_path, monkeypatch, page)
    monkeypatch.setattr(player, "SPEED_CHANGE_TIMEOUT_SECONDS", 0.01)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 1
    assert response["status"] == "partial"
    assert response["errors"][0]["code"] == "playback-speed-unavailable"
    assert [item["outcome"] for item in response["result"]["items"]] == ["failed", "not-started"]
    assert not [call for call in page.calls if call[0] == "play-index"]


def test_default_speed_does_not_require_a_speed_menu_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(speed_option_present=False, complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert not [call for call in page.calls if call[0] == "speed"]


def test_requested_normal_rate_replaces_persisted_player_speed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(initial_playback_rate=1.5, complete_after_play={"row-1": True})
    _prepare(tmp_path, monkeypatch, page, default_speed=1.0)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert ("speed", 1.0) in page.calls
    assert ("speed-force", True) in page.calls


def test_playback_resumes_a_paused_video_through_the_official_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(complete_after_play={"row-1": True}, pause_once_after_start=True)
    _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID])

    assert exit_code == 0
    assert response["result"]["items"][0]["outcome"] == "completed"
    assert len([call for call in page.calls if call[0] == "play-index"]) == 2


def test_cancelled_playback_is_not_replaced_by_player_close_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(fail_close=True)
    page.last_read_row_id = "row-1"

    async def cancel_read(_frame: FakeFrame) -> dict[str, Any]:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)
        return {}

    monkeypatch.setattr(player, "_read_video_state", cancel_read)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(player._play_visible_lecture(page, "row-1", 1.25))

    assert ("close-attempt", "row-1") in page.calls


def test_panopto_login_failure_reports_all_not_started_before_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(panopto_authenticated=False)
    sessions = _prepare(tmp_path, monkeypatch, page)
    monkeypatch.setattr(player, "PANOPTO_SESSION_WAIT_SECONDS", 0.01)

    exit_code, response = _invoke(capsys, [FIRST_ID, SECOND_ID])

    assert exit_code == 1
    assert response["errors"][0]["code"] == "lms-unavailable"
    assert [item["outcome"] for item in response["result"]["items"]] == ["not-started", "not-started"]
    assert not [call for call in page.calls if call[0] == "open-player"]
    assert sessions.opens == 1


def test_unsupported_speed_is_usage_error_before_browser_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage()
    sessions = _prepare(tmp_path, monkeypatch, page)

    exit_code, response = _invoke(capsys, [FIRST_ID], "--speed", "2")

    assert exit_code == 2
    assert response["status"] == "user-action"
    assert response["errors"][0]["code"] == "usage-error"
    assert sessions.opens == 0
