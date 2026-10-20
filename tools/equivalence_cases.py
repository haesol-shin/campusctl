"""Small synthetic starting corpus; later neutral changes add their affected paths here."""

import json
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import NotRequired, TypedDict
from unittest.mock import patch


class Case(TypedDict):
    name: str
    argv: list[str]
    width: int
    expected: int
    fixture: NotRequired[str]
    env: NotRequired[dict[str, str]]
    input: NotRequired[str]
    stdin_tty: NotRequired[bool]
    stdout_tty: NotRequired[bool]


def _lecture_row(entity_id: str, title: str, course_id: str, label: str) -> dict:
    return {
        "entity_id": entity_id,
        "course": {"id": course_id, "label": label},
        "kind": "lecture",
        "title": title,
        "week": "1",
        "sequence": "1",
        "progress_text": None,
        "duration_minutes": None,
        "available_from": None,
        "due_date": "2026-03-04T05:06:00",
        "late_until": None,
        "media": "video",
        "attendance_counted": None,
        "open": True,
        "completion": "incomplete",
        "provider_state": "N",
    }


def _write_catalogs(root: Path, fixture: str) -> None:
    catalog = root / "data" / "catalog" / "lectures.json"
    catalog.parent.mkdir()
    if fixture == "layout":
        _write_layout_catalogs(root)
        return
    if fixture.startswith("progress-"):
        _write_progress_catalog(catalog)
        return
    courses = [{"course_id": "course.invalid", "label": "Synthetic course with a long display label", "class_no": None}]
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-generation",
                "courses": courses if fixture == "filled" else [],
                "lectures": [],
            }
        ),
        encoding="utf-8",
    )


def _write_layout_catalogs(root: Path) -> None:
    # Synthetic wide text and a combining mark, not a real course name.
    short_label = "\uc790\ub8cc"
    long_label = "\uc790\u0301" + ("\uad6c\uc870" * 30)
    short_id = "s"
    wide_id = "wide.invalid"
    long_id = "course-" + ("x" * 70) + ".invalid"
    courses = [
        {"course_id": short_id, "label": short_label, "class_no": None},
        {"course_id": wide_id, "label": long_label, "class_no": None},
        {"course_id": long_id, "label": "\uc9e7\uc740", "class_no": None},
    ]
    long_entity = "cnu_lecture:wide.invalid:" + ("row-" * 10) + "end"
    lectures = [
        _lecture_row("cnu_lecture:s:r", "\uc9e7\uc740", short_id, short_label),
        _lecture_row(long_entity, "\uac15\u0301\uc758" + ("\uc601\uc0c1" * 20), wide_id, long_label),
    ]
    (root / "data" / "catalog" / "lectures.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-layout",
                "courses": courses,
                "lectures": lectures,
            }
        ),
        encoding="utf-8",
    )
    notices = root / "data" / "catalog" / "notices.json"
    notices.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-notices",
                "enrollment_state": "known",
                "courses": [{"course_id": short_id, "label": long_label, "class_no": None}],
                "failed_courses": [],
                "notices": [
                    {
                        "entity_id": "n1",
                        "course": {"id": short_id, "label": long_label},
                        "title": "\uacf5\u0301\uc9c0" + ("\uc0ac\ud56d" * 20),
                        "date": "2026-01-02",
                        "author": "\uad50\uc218",
                        "view_count": 1,
                        "has_attachments": False,
                        "is_unread": True,
                    },
                    {
                        "entity_id": "cnu_notice:" + ("id-" * 16) + "end.invalid",
                        "course": {"id": short_id, "label": short_label},
                        "title": "\uc9e7\uc740",
                        "date": "2026-01-03",
                        "author": "\uc870\uad50",
                        "view_count": 2,
                        "has_attachments": False,
                        "is_unread": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_progress_catalog(catalog: Path) -> None:
    course_id = "course.invalid"
    label = "Synthetic course"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-progress",
                "courses": [{"course_id": course_id, "label": label, "class_no": None}],
                "lectures": [
                    _lecture_row(
                        "cnu_lecture:course.invalid:row-1",
                        "\uac15\u0301\uc758" + ("\uc601\uc0c1" * 20),
                        course_id,
                        label,
                    )
                ],
            }
        ),
        encoding="utf-8",
    )


def _install_progress_fakes(stack: ExitStack, actions: list, fixture: str) -> None:
    from contextlib import asynccontextmanager

    import campusctl.browser as browser_module
    from campusctl.envelope import CampusError
    from campusctl.providers.cnu import player as player_module

    @asynccontextmanager
    async def fake_open_session(*args, **kwargs):
        del args, kwargs
        actions.append(["session", "open", "replaced"])
        try:
            yield SimpleNamespace(page=SimpleNamespace(name="synthetic-page"))
        finally:
            actions.append(["session", "close", "replaced"])

    async def fake_play(page, config, lectures, *, speed=None, replay=False, progress=None):
        del page, config, speed, replay
        actions.append(["playback", "invoke", fixture])
        lecture = lectures[0]
        entity_id = lecture["entity_id"]
        if progress is not None:
            progress(
                {
                    "type": "started",
                    "index": 1,
                    "total": 1,
                    "entity_id": entity_id,
                    "title": lecture.get("title", ""),
                }
            )
            progress(
                {
                    "type": "position",
                    "entity_id": entity_id,
                    "position_seconds": 3661,
                    "duration_seconds": 3661,
                }
            )
            progress(
                {
                    "type": "position",
                    "entity_id": entity_id,
                    "position_seconds": 5,
                    "duration_seconds": 3661,
                }
            )
        if fixture == "progress-interrupt":
            raise KeyboardInterrupt
        if fixture == "progress-failure":
            if progress is not None:
                progress({"type": "finished", "entity_id": entity_id, "outcome": "failed"})
            return {"items": [{"entity_id": entity_id, "outcome": "failed"}]}, CampusError(
                "playback-failed",
                "Synthetic playback failed.",
                "Retry the synthetic lecture.",
                "error",
            )
        if progress is not None:
            progress({"type": "finished", "entity_id": entity_id, "outcome": "completed"})
        return {"items": [{"entity_id": entity_id, "outcome": "completed"}]}, None

    stack.enter_context(patch.object(browser_module, "open_session", fake_open_session))
    stack.enter_context(patch.object(player_module, "play_lectures", fake_play))


def cases() -> list[Case]:
    help_paths = [
        [],
        ["config"],
        ["config", "init"],
        ["doctor"],
        ["setup"],
        ["auth"],
        ["auth", "set"],
        ["auth", "status"],
        ["sync"],
        ["status"],
        ["courses"],
        ["courses", "list"],
        ["lectures"],
        ["lectures", "list"],
        ["lectures", "play"],
        ["assignments"],
        ["assignments", "list"],
        ["assignments", "fetch"],
        ["notices"],
        ["notices", "list"],
        ["notices", "fetch"],
        ["materials"],
        ["materials", "list"],
        ["materials", "download"],
    ]
    definitions = [("help-" + "-".join(path), [*path, "--help"], "empty", 0) for path in help_paths]
    definitions += [
        ("version", ["--version"], "empty", 0),
        ("invalid", ["unknown"], "empty", 2),
        ("missing", ["lectures", "play"], "empty", 2),
        ("abbreviation", ["--ver"], "empty", 2),
        ("list-empty", ["courses", "list"], "empty", 0),
        ("list-filled", ["courses", "list"], "filled", 0),
        ("helper-failed", ["auth", "status", "--check"], "helper-failed", 1),
        ("busy", ["sync"], "busy", 75),
    ]
    definitions += [
        (f"doctor-{speed}", ["doctor"], speed, 0 if speed in {"1", "1.0", "1.25", "1.5"} else 2)
        for speed in ("1", "1.0", "1.25", "1.5", "1.1", "2.0", "true", "false", '"1.25"')
    ]
    definitions += [
        (f"play-speed-{speed}", ["lectures", "play", "synthetic-lecture", "--speed", speed], "empty", 2)
        for speed in ("1.0", "1.25", "1.5", "1.1", "2.0", "true")
    ]
    result: list[Case] = []
    for name, argv, fixture, expected in definitions:
        for width in (48, 100):
            for mode in ("json", "human"):
                result.append(
                    {
                        "name": f"{name}-{width}-{mode}",
                        "argv": argv,
                        "fixture": fixture,
                        "width": width,
                        "env": {"CAMPUSCTL_OUTPUT": mode},
                        "expected": expected,
                    }
                )
    result += [
        {
            "name": "json-overrides-human-tty",
            "argv": ["--version", "--json"],
            "fixture": "empty",
            "width": 100,
            "env": {"CAMPUSCTL_OUTPUT": "human"},
            "stdout_tty": True,
            "expected": 0,
        },
        {
            "name": "auto-human-tty",
            "argv": ["--version"],
            "fixture": "empty",
            "width": 100,
            "stdout_tty": True,
            "expected": 0,
        },
        {"name": "auto-json-pipe", "argv": ["--version"], "fixture": "empty", "width": 100, "expected": 0},
    ]
    for path in help_paths:
        label = "root" if not path else "-".join(path)
        for width in (40, 120):
            result.append(
                {
                    "name": f"fixed-help-{label}-{width}",
                    "argv": [*path, "--help"],
                    "fixture": "empty",
                    "width": width,
                    "env": {"CAMPUSCTL_OUTPUT": "human"},
                    "expected": 0,
                }
            )
    for name, argv, width in (
        ("layout-lectures-table-72", ["lectures", "list", "--all"], 72),
        ("layout-courses-ids-72", ["courses", "list"], 72),
        ("layout-notices-text-32", ["notices", "list"], 32),
    ):
        result.append(
            {
                "name": name,
                "argv": argv,
                "fixture": "layout",
                "width": width,
                "env": {"CAMPUSCTL_OUTPUT": "human"},
                "expected": 0,
            }
        )
    for outcome, expected in (("success", 0), ("failure", 1), ("interrupt", 130)):
        for tty, suffix in ((True, "tty"), (False, "pipe")):
            case = {
                "name": f"progress-{outcome}-{suffix}",
                "argv": ["lectures", "play", "cnu_lecture:course.invalid:row-1", "--speed", "1.0"],
                "fixture": f"progress-{outcome}",
                "width": 40,
                "env": {"CAMPUSCTL_OUTPUT": "human"},
                "expected": expected,
            }
            if tty:
                case["stdout_tty"] = True
            result.append(case)
    return result


@contextmanager
def prepare(root: Path, case: Case, actions: list):
    from playwright.sync_api._context_manager import PlaywrightContextManager

    # A command provider avoids touching a real keyring even when reporting doctor status.
    fixture = case["fixture"]
    speed = fixture if fixture in {"1", "1.0", "1.25", "1.5", "1.1", "2.0", "true", "false", '"1.25"'} else "1.0"
    executable = root / "browser"
    executable.write_bytes(b"synthetic browser marker")
    headed = fixture.startswith("progress-")
    (root / "config" / "config.toml").write_text(
        'provider = "cnu"\n[credentials]\nprovider = "command"\n'
        f"command = [{json.dumps(str(root / 'synthetic-helper'))}]\n"
        f"[browser]\nheadless = {'false' if headed else 'true'}\nexecutable_path = {json.dumps(str(executable))}\n"
        f"[playback]\ndefault_speed = {speed}\n",
        encoding="utf-8",
    )
    _write_catalogs(root, fixture)
    state = {"helper_attempts": 0, "lock_attempts": 0, "browser_probe_open": False}

    def fail_helper(*args, **kwargs):
        state["helper_attempts"] += 1
        actions.append(["helper", "execute", "failed"])
        raise OSError("synthetic helper failure")

    def busy_lock(fd, operation):
        state["lock_attempts"] += 1
        actions.append(["lock", "acquire", "busy"])
        raise BlockingIOError()

    def stop_browser():
        assert state["browser_probe_open"]
        state["browser_probe_open"] = False
        actions.append(["browser-probe", "close"])

    def probe_browser(self):
        assert not state["browser_probe_open"]
        state["browser_probe_open"] = True
        actions.append(["browser-probe", "open"])
        return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(executable)), stop=stop_browser)

    with patch.object(PlaywrightContextManager, "start", probe_browser), ExitStack() as stack:
        if fixture == "helper-failed":
            stack.enter_context(patch.object(subprocess, "Popen", fail_helper))
        elif fixture == "busy":
            import fcntl

            stack.enter_context(patch.object(fcntl, "flock", busy_lock))
        elif fixture.startswith("progress-"):
            _install_progress_fakes(stack, actions, fixture)
        yield state
