"""Small synthetic starting corpus; later neutral changes add their affected paths here."""

import json
import subprocess
from contextlib import contextmanager
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
    return result


@contextmanager
def prepare(root: Path, case: Case, actions: list):
    from playwright.sync_api._context_manager import PlaywrightContextManager

    # A command provider avoids touching a real keyring even when reporting doctor status.
    fixture = case["fixture"]
    speed = fixture if fixture in {"1", "1.0", "1.25", "1.5", "1.1", "2.0", "true", "false", '"1.25"'} else "1.0"
    executable = root / "browser"
    executable.write_bytes(b"synthetic browser marker")
    (root / "config" / "config.toml").write_text(
        'provider = "cnu"\n[credentials]\nprovider = "command"\n'
        f"command = [{json.dumps(str(root / 'synthetic-helper'))}]\n"
        f"[browser]\nheadless = true\nexecutable_path = {json.dumps(str(executable))}\n"
        f"[playback]\ndefault_speed = {speed}\n",
        encoding="utf-8",
    )
    catalog = root / "data" / "catalog" / "lectures.json"
    catalog.parent.mkdir()
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

    with patch.object(PlaywrightContextManager, "start", probe_browser):
        if fixture == "helper-failed":
            with patch.object(subprocess, "Popen", fail_helper):
                yield state
        elif fixture == "busy":
            import fcntl

            with patch.object(fcntl, "flock", busy_lock):
                yield state
        else:
            yield state
