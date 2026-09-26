from __future__ import annotations

import codecs
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any, TextIO

from campusctl.browser import chromium_installed
from campusctl.envelope import CampusError

_INSTALL_PROMPT = "campusctl needs a Chromium browser for playback and sync. Download and install it now? [Y/n]"


def _install_error(exit_code: int | None = None) -> CampusError:
    message = "Chromium browser installation failed"
    if exit_code is not None:
        message += f" (exit code {exit_code})"
    message += "."
    return CampusError(
        "browser-install-failed",
        message,
        "Rerun 'campusctl setup' and check your network connection and antivirus settings.",
        "error",
    )


def _installer_command() -> list[str]:
    return [sys.executable, "-m", "playwright", "install", "chromium"]


def _discard_stream(stream: Any) -> None:
    while stream.read1(8192):
        pass


def _run_installer(out: TextIO | None) -> int:
    process = subprocess.Popen(
        _installer_command(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    if out is None:
        try:
            process.communicate()
        except KeyboardInterrupt:
            with suppress(OSError):
                process.kill()
            process.wait()
            raise
        return process.returncode

    assert process.stdout is not None
    assert process.stderr is not None
    stderr_reader = threading.Thread(target=_discard_stream, args=(process.stderr,), daemon=True)
    stderr_reader.start()
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while chunk := process.stdout.read1(8192):
            text = decoder.decode(chunk)
            if text:
                out.write(text)
                out.flush()
        remaining = decoder.decode(b"", final=True)
        if remaining:
            out.write(remaining)
            out.flush()
        process.wait()
        stderr_reader.join()
    except BaseException:
        with suppress(OSError):
            process.kill()
        process.wait()
        stderr_reader.join()
        raise
    return process.returncode


def _result(action: str, installed: bool) -> dict[str, Any]:
    return {
        "browser": {"installed": installed, "action": action},
        "next": ["campusctl sync"] if installed else [],
    }


def run_setup(
    config: dict[str, Any] | None,
    *,
    confirm: Callable[[str], bool] | None,
    out: TextIO | None,
) -> dict[str, Any]:
    browser_config = config.get("browser", {}) if config is not None else {}
    if browser_config.get("cdp_endpoint"):
        return _result("skipped-cdp", True)

    executable_path = browser_config.get("executable_path")
    if executable_path:
        return _result("skipped-custom-executable", chromium_installed(executable_path))

    if chromium_installed():
        return _result("already-installed", True)

    if confirm is not None and not confirm(_INSTALL_PROMPT):
        return _result("declined", False)

    try:
        exit_code = _run_installer(out)
    except OSError:
        raise _install_error() from None

    if exit_code != 0:
        raise _install_error(exit_code)
    if not chromium_installed():
        raise _install_error()
    return _result("installed", True)
