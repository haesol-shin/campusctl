from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from campusctl import __version__, cli, config_init
from campusctl.config import load_config


class _TerminalOutput:
    def __init__(self, stream: object) -> None:
        self._stream = stream

    def isatty(self) -> bool:
        return True

    def write(self, value: str) -> int:
        return self._stream.write(value)  # type: ignore[attr-defined,no-any-return]

    def flush(self) -> None:
        self._stream.flush()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _local_browser_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_init, "chromium_installed", lambda _path=None: True)


def test_version_request_does_not_prompt_during_config_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("CAMPUSCTL_OUTPUT", raising=False)
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("prompted for a version request"))

    assert cli.main(["--version", "config", "init"]) == 0
    assert capsys.readouterr().out.strip() == f"campusctl version {__version__}."
    assert not (config_dir / "config.toml").exists()


def test_config_init_creates_and_loads_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "nested" / "settings"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))

    assert cli.main(["config", "init", "--username", "sample-id", "--json"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    config_file = config_dir / "config.toml"
    assert envelope["status"] == "ok"
    assert envelope["result"] == {
        "config_path": str(config_file),
        "created": True,
        "next": ["campusctl auth set"],
    }
    assert load_config(config_file) == {
        "provider": "cnu",
        "account": {"username": "sample-id"},
        "credentials": {"provider": "keyring"},
        "playback": {"default_speed": 1.0},
    }


def test_config_init_toml_escapes_quotes_backslashes_and_unicode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    username = 'user"\\name-학번'
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))

    assert cli.main(["config", "init", "--username", username, "--json"]) == 0
    capsys.readouterr()
    assert load_config(tmp_path / "config" / "config.toml")["account"]["username"] == username


def test_config_init_does_not_overwrite_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    original = b"existing user settings\n"
    config_file.write_bytes(original)
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))

    assert cli.main(["config", "init", "--username", "new-id", "--json"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["status"] == "user-action"
    assert envelope["errors"][0]["code"] == "config-exists"
    assert str(config_file) in envelope["errors"][0]["remediation"]
    assert config_file.read_bytes() == original


def test_config_init_honors_config_directory_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "override"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))

    assert cli.main(["config", "init", "--username", "sample-id", "--json"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["result"]["config_path"] == str(config_dir / "config.toml")
    assert (config_dir / "config.toml").is_file()


def test_config_init_requires_username_without_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cli.sys, "stdin", type("Pipe", (), {"isatty": lambda self: False})())

    assert cli.main(["config", "init"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["status"] == "user-action"
    assert envelope["errors"][0]["code"] == "usage-error"
    assert "--username" in envelope["errors"][0]["message"]


@pytest.mark.parametrize("username", ["", " \t ", "bad\nid"])
def test_config_init_rejects_empty_or_control_character_usernames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], username: str
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))

    assert cli.main(["config", "init", "--username", username, "--json"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["status"] == "user-action"
    assert envelope["errors"][0]["code"] == "config-invalid"
    assert not (config_dir / "config.toml").exists()


def test_config_init_interactive_prompt_declines_password_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    prompts: list[str] = []
    responses = iter(["sample-id", "n"])

    def fake_input(prompt: str = "") -> str:
        print(prompt)
        prompts.append(prompt)
        return next(responses)

    stored: list[object] = []
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(config_init, "prompt_and_store", lambda *args, **kwargs: stored.append((args, kwargs)))

    assert cli.main(["config", "init"]) == 0
    output = capsys.readouterr().out
    assert prompts == ["CNU login ID: ", "Save your password now? [Y/n] "]
    assert stored == []
    assert output.splitlines()[:2] == ["CNU login ID: ", "Save your password now? [Y/n] "]
    assert "Configuration created at" in output
    assert output.splitlines()[-1] == "Next: campusctl auth set"
    assert not output.lstrip().startswith("{")


def test_config_init_interactive_empty_password_answer_uses_existing_auth_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    responses = iter(["sample-id", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    stored: list[tuple[dict[str, object], bool]] = []

    def store(config: dict[str, object], *, stdin_isatty: bool) -> None:
        stored.append((config, stdin_isatty))

    monkeypatch.setattr(config_init, "prompt_and_store", store)

    assert cli.main(["config", "init"]) == 0
    output = capsys.readouterr().out
    assert len(stored) == 1
    assert stored[0][0]["account"] == {"username": "sample-id"}
    assert stored[0][1] is True
    assert "Password saved to your OS keyring." in output
    assert output.splitlines()[-1] == "Next: campusctl sync"


def test_config_init_interactive_existing_file_explains_edit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text('provider = "cnu"\n[account]\nusername = "sample-id"\n', encoding="utf-8")
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    monkeypatch.setattr("builtins.input", lambda prompt="": (print(prompt), "n")[1])
    monkeypatch.setattr(config_init, "keyring_status", lambda config: ("SecureBackend", False))
    stored: list[object] = []
    monkeypatch.setattr(config_init, "prompt_and_store", lambda *args, **kwargs: stored.append((args, kwargs)))

    assert cli.main(["config", "init", "--username", "sample-id"]) == 2
    captured = capsys.readouterr()
    assert "Configuration already exists at" in captured.out
    assert "directly" in captured.out
    assert "Save your password now? [Y/n]" in captured.out
    assert "Next: campusctl auth set" in captured.out
    assert stored == []
    assert config_file.read_text(encoding="utf-8").endswith('username = "sample-id"\n')


def test_config_init_only_prompts_when_both_streams_are_ttys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("CAMPUSCTL_OUTPUT", raising=False)
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("prompted without a terminal stdout"))

    assert cli.main(["config", "init"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["errors"][0]["code"] == "usage-error"
    assert not (tmp_path / "config" / "config.toml").exists()


@pytest.mark.parametrize("mode", ["environment", "flag"])
def test_config_init_json_modes_disable_interactive_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    args = ["config", "init"]
    if mode == "environment":
        monkeypatch.setenv("CAMPUSCTL_OUTPUT", "json")
    else:
        monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
        args.append("--json")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("prompted in JSON output mode"))

    assert cli.main(args) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["errors"][0]["code"] == "usage-error"
    assert not (tmp_path / "config" / "config.toml").exists()


def test_config_init_password_failure_ends_with_auth_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    responses = iter(["sample-id", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    def fail_store(*args: object, **kwargs: object) -> None:
        raise cli.CampusError("credential-backend-unavailable", "Password could not be saved.", "Check the keyring.")

    monkeypatch.setattr(config_init, "prompt_and_store", fail_store)

    assert cli.main(["config", "init"]) == 2
    captured = capsys.readouterr()
    assert "Next: campusctl auth set" in captured.out
    assert "Password could not be saved" in captured.out
    assert captured.err == ""


def test_config_init_existing_file_recovers_missing_password_without_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    original = 'provider = "cnu"\n[account]\nusername = "sample-id"\n'
    config_file.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    monkeypatch.setattr(config_init, "keyring_status", lambda config: ("SecureBackend", False))
    monkeypatch.setattr("builtins.input", lambda prompt="": (print(prompt), "")[1])
    stored: list[object] = []
    monkeypatch.setattr(config_init, "prompt_and_store", lambda *args, **kwargs: stored.append((args, kwargs)))

    assert cli.main(["config", "init"]) == 0
    output = capsys.readouterr().out
    assert "Configuration already exists at" in output
    assert "Password saved to your OS keyring." in output
    assert output.splitlines()[-1] == "Next: campusctl sync"
    assert len(stored) == 1
    assert config_file.read_text(encoding="utf-8") == original


def test_config_init_existing_password_failure_keeps_config_exists_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text('provider = "cnu"\n[account]\nusername = "sample-id"\n', encoding="utf-8")
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    monkeypatch.setattr(config_init, "keyring_status", lambda config: ("SecureBackend", False))
    monkeypatch.setattr("builtins.input", lambda prompt="": (print(prompt), "")[1])

    def fail_store(*args: object, **kwargs: object) -> None:
        raise cli.CampusError("credential-backend-unavailable", "Password could not be saved.", "Check the keyring.")

    monkeypatch.setattr(config_init, "prompt_and_store", fail_store)

    assert cli.main(["config", "init"]) == 2
    captured = capsys.readouterr()
    assert "Next: campusctl auth set" in captured.out
    assert "Password could not be saved" in captured.out
    assert captured.err == ""
    assert config_file.read_text(encoding="utf-8") == 'provider = "cnu"\n[account]\nusername = "sample-id"\n'


def test_config_init_does_not_clobber_a_racing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))

    def race_link(temporary: Path, target: Path) -> None:
        del temporary
        target.write_text("written by another process\n", encoding="utf-8")
        raise FileExistsError

    monkeypatch.setattr(config_init.os, "link", race_link)

    assert cli.main(["config", "init", "--username", "sample-id", "--json"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["errors"][0]["code"] == "config-exists"
    assert (config_dir / "config.toml").read_text(encoding="utf-8") == "written by another process\n"
    assert list(config_dir.glob(".*.tmp")) == []


def test_config_init_operational_failure_uses_plain_interactive_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))

    def fail_create(*args: object, **kwargs: object) -> None:
        raise OSError("private exception detail")

    monkeypatch.setattr(config_init.tempfile, "mkstemp", fail_create)

    assert cli.main(["config", "init", "--username", "sample-id"]) == 1
    captured = capsys.readouterr()
    assert "OSError" in captured.out
    assert "Traceback" not in captured.out
    assert "private exception detail" not in captured.out
    assert captured.err == ""
    assert not (config_dir / "config.toml").exists()


def test_missing_custom_browser_guides_config_edit_instead_of_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text(
        'provider = "cnu"\n'
        '[account]\nusername = "sample-id"\n'
        '[credentials]\nprovider = "keyring"\n'
        '[browser]\nexecutable_path = "C:/missing/chrome.exe"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli.sys, "stdin", type("TerminalInput", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(cli.sys, "stdout", _TerminalOutput(sys.stdout))
    monkeypatch.setattr(config_init, "keyring_status", lambda _config: ("SecureBackend", False))
    monkeypatch.setattr(config_init, "chromium_installed", lambda _path=None: False)
    monkeypatch.setattr(config_init, "run_setup", lambda *_args, **_kwargs: pytest.fail("setup was offered"))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert cli.main(["config", "init", "--username", "sample-id"]) == 2
    output = capsys.readouterr().out
    compact_output = "".join(output.split())
    expected_guidance = (
        f"Next: The browser at browser.executable_path was not found. Fix or remove it in {config_file}."
    )
    assert "".join(expected_guidance.split()) in compact_output
    assert "campusctl setup" not in output
    assert "Next: campusctl auth set" in output
