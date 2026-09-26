from __future__ import annotations

import io
import subprocess
import sys
from typing import Any

import pytest

from campusctl import setup
from campusctl.envelope import CampusError


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        interrupt: bool = False,
    ) -> None:
        self.stdout = io.BufferedReader(io.BytesIO(stdout))
        self.returncode = returncode
        self.stderr = io.BufferedReader(io.BytesIO(stderr))
        self.interrupt = interrupt
        self.killed = False
        self.waited = False

    def communicate(self) -> tuple[bytes, bytes]:
        if self.interrupt:
            raise KeyboardInterrupt
        return self.stdout.read(), self.stderr.read()

    def kill(self) -> None:
        self.killed = True

    def wait(self) -> int:
        self.waited = True
        return self.returncode


def _set_readiness(monkeypatch: pytest.MonkeyPatch, *readiness: bool) -> None:
    checks = iter(readiness)
    monkeypatch.setattr(setup, "chromium_installed", lambda _path=None: next(checks))


def test_already_installed_skips_prompt_and_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_readiness(monkeypatch, True)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("installer ran"))

    result = setup.run_setup(None, confirm=lambda _prompt: pytest.fail("prompted"), out=None)

    assert result == {
        "browser": {"installed": True, "action": "already-installed"},
        "next": ["campusctl sync"],
    }


def test_cdp_configuration_skips_local_browser_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "chromium_installed", lambda *_args: pytest.fail("readiness checked"))
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("installer ran"))

    result = setup.run_setup(
        {"browser": {"cdp_endpoint": "http://127.0.0.1:9223/json/version"}}, confirm=None, out=None
    )

    assert result["browser"]["action"] == "skipped-cdp"
    assert result["next"] == ["campusctl sync"]


def test_custom_executable_is_checked_but_never_provisioned(monkeypatch: pytest.MonkeyPatch) -> None:
    readiness_calls: list[str | None] = []
    monkeypatch.setattr(setup, "chromium_installed", lambda path=None: readiness_calls.append(path) or True)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("installer ran"))

    result = setup.run_setup({"browser": {"executable_path": "C:/browser/chrome.exe"}}, confirm=None, out=None)

    assert readiness_calls == ["C:/browser/chrome.exe"]
    assert result == {
        "browser": {"installed": True, "action": "skipped-custom-executable"},
        "next": ["campusctl sync"],
    }


def test_declining_install_returns_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_readiness(monkeypatch, False)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("installer ran"))
    prompts: list[str] = []

    result = setup.run_setup(None, confirm=lambda prompt: prompts.append(prompt) or False, out=None)

    assert prompts == ["campusctl needs a Chromium browser for playback and sync. Download and install it now? [Y/n]"]
    assert result == {"browser": {"installed": False, "action": "declined"}, "next": []}


def test_install_success_streams_to_output_and_rechecks(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_readiness(monkeypatch, False, True)
    process = FakeProcess(returncode=0)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(setup.subprocess, "Popen", fake_popen)
    output = io.StringIO()
    prompts: list[str] = []

    result = setup.run_setup(None, confirm=lambda prompt: prompts.append(prompt) or True, out=output)

    assert calls == [
        (
            [setup.sys.executable, "-m", "playwright", "install", "chromium"],
            {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": False},
        )
    ]
    assert "env" not in calls[0][1]
    assert len(prompts) == 1
    assert result == {"browser": {"installed": True, "action": "installed"}, "next": ["campusctl sync"]}


def test_install_failure_hides_stderr_and_reports_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_readiness(monkeypatch, False)
    calls: list[dict[str, Any]] = []

    def fake_popen(*_args: Any, **kwargs: Any) -> FakeProcess:
        calls.append(kwargs)
        return FakeProcess(returncode=23, stderr=b"secret raw installer output")

    monkeypatch.setattr(setup.subprocess, "Popen", fake_popen)

    with pytest.raises(CampusError) as caught:
        setup.run_setup(None, confirm=None, out=None)

    error = caught.value
    assert error.code == "browser-install-failed"
    assert error.status == "error"
    assert "exit code 23" in error.message
    assert "secret raw installer output" not in str(error)
    assert "campusctl setup" in error.remediation
    assert "network" in error.remediation
    assert "antivirus" in error.remediation
    assert calls == [{"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": False}]


def test_keyboard_interrupt_kills_and_reaps_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_readiness(monkeypatch, False)
    process = FakeProcess(interrupt=True)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(KeyboardInterrupt):
        setup.run_setup(None, confirm=None, out=None)

    assert process.killed
    assert process.waited


def test_success_without_readiness_after_install_is_install_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_readiness(monkeypatch, False, False)
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    with pytest.raises(CampusError) as caught:
        setup.run_setup(None, confirm=None, out=None)

    assert caught.value.code == "browser-install-failed"
    assert caught.value.status == "error"


def test_real_installer_process_streams_stdout_and_discards_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_readiness(monkeypatch, False)
    child_code = (
        "import sys; sys.stdout.buffer.write('Chromium progress: café\\n'.encode('utf-8')); "
        "sys.stderr.buffer.write('secret response body https://private.invalid/path\\n'.encode('utf-8')); "
        "raise SystemExit(1)"
    )
    monkeypatch.setattr(setup, "_installer_command", lambda: [sys.executable, "-c", child_code])
    output = io.StringIO()

    with pytest.raises(CampusError) as caught:
        setup.run_setup(None, confirm=None, out=output)

    assert "Chromium progress: café" in output.getvalue()
    assert "secret response body" not in output.getvalue()
    assert "private.invalid" not in output.getvalue()
    assert caught.value.code == "browser-install-failed"
    assert caught.value.status == "error"
    assert caught.value.message.endswith("(exit code 1).")
