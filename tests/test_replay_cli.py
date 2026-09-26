"""Replay CLI permissions precede the browser and JSON never prompts."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from campusctl import cli

ENTITY = "cnu_lecture:course-a:lecture-a"


def _catalog(root: Path) -> None:
    target = root / "catalog" / "lectures.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-09-25T12:00:00Z",
                "courses": [{"course_id": "course-a", "label": "Example", "class_no": None}],
                "lectures": [
                    {
                        "entity_id": ENTITY,
                        "kind": "lecture",
                        "title": "Introduction",
                        "media": "video",
                        "course": {"id": "course-a", "label": "Example"},
                        "completion": "complete",
                        "open": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_complete_requires_replay_and_json_never_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: {})
    events: list[str] = []

    @asynccontextmanager
    async def session(*args: object, **kwargs: object):
        events.append("browser")
        yield SimpleNamespace(page=object())

    async def play(*args: object, **kwargs: object):
        assert kwargs["replay"] is True
        return {
            "items": [{"entity_id": ENTITY, "outcome": "completed", "replay_requested": True, "player_opened": True}]
        }, None

    monkeypatch.setattr("campusctl.browser.open_session", session)
    monkeypatch.setattr("campusctl.providers.cnu.player.play_lectures", play)
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("JSON must not prompt"))
    assert cli.main(["lectures", "play", ENTITY, "--json"]) == 2
    first = json.loads(capsys.readouterr().out)
    assert first["errors"][0]["code"] == "lecture-complete" and not events
    assert cli.main(["lectures", "play", ENTITY, "--replay", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert events == ["browser"] and second["result"]["items"][0]["player_opened"] is True


def test_human_replay_without_tty_refuses_before_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    assert cli.main(["lectures", "play", ENTITY, "--replay"]) == 2
    assert "replay-confirmation-required" in capsys.readouterr().out
