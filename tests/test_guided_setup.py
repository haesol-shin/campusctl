from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from campusctl import setup
from campusctl.envelope import CampusError


@pytest.fixture
def ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path / "data"))
    state: dict[str, Any] = {"browser": False, "password": False, "prompts": [], "saved": []}
    monkeypatch.setattr(setup, "chromium_installed", lambda _path=None: state["browser"])
    monkeypatch.setattr(setup, "_run_installer", lambda _out: state.update(browser=True) or 0)
    monkeypatch.setattr(setup, "keyring_status", lambda _config: ("SyntheticBackend", state["password"]))

    def save(config: dict[str, Any], *, stdin_isatty: bool) -> None:
        assert stdin_isatty
        state["saved"].append(config["account"]["username"])
        state["password"] = True

    monkeypatch.setattr(setup, "prompt_and_store", save)
    return state


def _confirm(state: dict[str, Any], *answers: bool):
    replies = iter(answers)

    def confirm(prompt: str) -> bool:
        state["prompts"].append(prompt)
        return next(replies)

    return confirm


def _names(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(step["name"], step["status"]) for step in result["steps"]]


def test_guided_resume_from_install_and_password_declines(
    ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "sample-id")
    output = io.StringIO()
    first, error = setup.run_guided_setup(confirm=_confirm(ready, False), out=output)
    assert error is not None and error.code == "setup-incomplete" and error.status == "user-action"
    assert _names(first) == [("config", "completed"), ("browser", "declined")]
    path = tmp_path / "settings" / "config.toml"
    original = path.read_bytes()

    second, error = setup.run_guided_setup(confirm=_confirm(ready, True, False), out=output)
    assert error is not None and error.code == "setup-incomplete"
    assert _names(second) == [("config", "reused"), ("browser", "completed"), ("credentials", "declined")]
    assert path.read_bytes() == original

    third, error = setup.run_guided_setup(confirm=_confirm(ready, True), out=output)
    assert error is None
    assert _names(third) == [
        ("config", "reused"),
        ("browser", "reused"),
        ("credentials", "completed"),
        ("check", "completed"),
        ("sync", "skipped"),
    ]
    assert ready["saved"] == ["sample-id"]
    assert path.read_bytes() == original
    fourth, error = setup.run_guided_setup(confirm=_confirm(ready), out=output)
    assert error is None and _names(fourth)[2] == ("credentials", "reused")
    assert ready["saved"] == ["sample-id"]
    assert "does not verify LMS login" in output.getvalue()


def test_optional_full_sync_defaults_no_and_uses_injected_callable(
    ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready.update(browser=True, password=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "sample-id")
    setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO())
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "")
    called: list[tuple[Any, ...]] = []

    def sync(config: dict[str, Any], root: Path, domains: tuple[str, ...], course_id: None, *, headless: bool):
        called.append((config["account"]["username"], root, domains, course_id, headless))
        return {"domains": {}}, None

    result, error = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO(), run_sync=sync)
    assert error is None and result["steps"][-1] == {"name": "sync", "status": "skipped"}
    assert called == [] and prompts == ["Run a first full sync now? [y/N] "]
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    result, error = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO(), run_sync=sync)
    assert error is None and result["steps"][-1] == {"name": "sync", "status": "completed"}
    assert called[0][0] == "sample-id"
    assert called[0][2:] == (("lectures", "assignments", "notices", "materials"), None, False)


def test_helper_check_failure_and_recovery(
    ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "settings" / "config.toml"
    config_file.parent.mkdir()
    original = (
        'provider = "cnu"\n[account]\nusername = "sample-id"\n'
        '[credentials]\nprovider = "command"\n'
        f"command = [{json.dumps(sys.executable)}]\n"
    ).encode()
    config_file.write_bytes(original)
    ready["browser"] = True
    checks = iter(["failed", "ok"])
    monkeypatch.setattr(setup, "helper_status", lambda _config, *, check: ("helper.invalid", True, next(checks)))
    first, error = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO())
    assert error is not None and error.code == "credential-helper-failed"
    assert _names(first)[-2:] == [("credentials", "skipped"), ("check", "failed")]
    second, error = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO())
    assert error is None and _names(second)[-2:] == [("check", "completed"), ("sync", "skipped")]
    assert config_file.read_bytes() == original and ready["saved"] == []


def test_invalid_existing_config_is_preserved(ready: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "settings" / "config.toml"
    path.parent.mkdir()
    path.write_text("invalid TOML [", encoding="utf-8")
    result, error = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO())
    assert error is not None and _names(result) == [("config", "failed")]
    assert path.read_text(encoding="utf-8") == "invalid TOML ["


def test_interrupted_password_stage_resumes_without_reinstall(
    ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "sample-id")
    ready["browser"] = True

    def interrupted(_config: dict[str, Any], *, stdin_isatty: bool) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(setup, "prompt_and_store", interrupted)
    with pytest.raises(KeyboardInterrupt):
        setup.run_guided_setup(confirm=_confirm(ready, True), out=io.StringIO())
    ready["password"] = True
    result, error = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO())
    assert error is None and _names(result)[2] == ("credentials", "reused")


def test_check_failure_after_save_resumes_without_replacing_password(
    ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready["browser"] = True
    monkeypatch.setattr("builtins.input", lambda _prompt: "sample-id")
    original_status = setup.keyring_status
    checks = 0

    def status(config: dict[str, Any]) -> tuple[str, bool]:
        nonlocal checks
        checks += 1
        if checks == 2:
            return "SyntheticBackend", False
        return original_status(config)

    monkeypatch.setattr(setup, "keyring_status", status)
    first, error = setup.run_guided_setup(confirm=_confirm(ready, True), out=io.StringIO())
    assert error is not None and error.code == "credentials-not-configured"
    assert _names(first)[-2:] == [("credentials", "completed"), ("check", "failed")]
    second, error = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO())
    assert error is None and _names(second)[2] == ("credentials", "reused")
    assert ready["saved"] == ["sample-id"]


def test_sync_failure_preserves_completed_setup_steps(ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    ready.update(browser=True, password=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "sample-id")
    setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO())
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    failure = CampusError("sync-failed", "Synthetic sync failed.", "Run 'campusctl sync'.", "error")

    def sync(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], list[CampusError]]:
        return {"domains": {"lectures": {"status": "error"}}}, [failure]

    result, errors = setup.run_guided_setup(confirm=_confirm(ready), out=io.StringIO(), run_sync=sync)
    assert errors == [failure]
    assert _names(result)[-2:] == [("check", "completed"), ("sync", "failed")]
    assert result["sync"]["domains"]["lectures"]["status"] == "error"
    assert result["next"] == ["campusctl sync"]
