from __future__ import annotations

import codecs
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, TextIO

from campusctl.browser import chromium_installed
from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import cache_metadata, catalog_snapshot
from campusctl.credentials import helper_status, keyring_status, prompt_and_store
from campusctl.envelope import CampusError
from campusctl.paths import config_path, data_dir

_SYNC_DOMAINS = ("lectures", "assignments", "notices", "materials")
SyncCallable = Callable[..., tuple[dict[str, Any], CampusError | list[CampusError] | None]]


def _incomplete(action: str) -> CampusError:
    return CampusError("setup-incomplete", "Setup stopped before " + action + ".", action, "user-action")


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


def run_guided_setup(
    *,
    confirm: Callable[[str], bool],
    out: TextIO,
    run_sync: SyncCallable | None = None,
    headless_override: bool | None = None,
) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    """Resume the interactive stages from config, browser, and credential state."""
    from campusctl.config import load_config
    from campusctl.config_init import run_config_init

    if not sys.stdin.isatty() or not out.isatty():
        return {"steps": [], "next": ["campusctl setup"]}, CampusError(
            "auth-tty-required",
            "Guided setup requires interactive input and output terminals.",
            "Run 'campusctl setup' from a terminal.",
            "user-action",
        )
    result: dict[str, Any] = {"steps": [], "next": []}
    steps: list[dict[str, str]] = result["steps"]

    def step(name: str, status: str) -> None:
        steps.append({"name": name, "status": status})

    path = config_path()
    try:
        config_result, _ = run_config_init(None, interactive=True, guided=True)
        config = load_config(path)
    except CampusError as error:
        step("config", "failed")
        result["next"] = [f"Edit {path} and rerun 'campusctl setup'."] if path.exists() else ["campusctl config init"]
        return result, error
    result["config_path"] = config_result["config_path"]
    step("config", "completed" if config_result["created"] else "reused")

    try:
        browser = run_setup(config, confirm=confirm, out=out)["browser"]
    except CampusError as error:
        step("browser", "failed")
        result["next"] = ["campusctl setup"]
        return result, error
    result["browser"] = browser
    if not browser["installed"]:
        step("browser", "declined" if browser["action"] == "declined" else "failed")
        result["next"] = (
            ["campusctl setup"]
            if browser["action"] == "declined"
            else [f"Fix browser.executable_path in {path} and rerun 'campusctl setup'."]
        )
        return result, _incomplete(result["next"][0])
    step("browser", "completed" if browser["action"] == "installed" else "reused")

    provider = config.get("credentials", {}).get("provider", "keyring")
    if provider == "command":
        step("credentials", "skipped")
    else:
        try:
            _, configured = keyring_status(config)
        except CampusError as error:
            step("credentials", "failed")
            result["next"] = ["campusctl auth status --check"]
            return result, error
        if configured:
            step("credentials", "reused")
        elif not confirm("Save your password now? [Y/n]"):
            step("credentials", "declined")
            result["next"] = ["campusctl auth set"]
            return result, _incomplete("campusctl auth set")
        else:
            out.write("Password input is hidden.\n")
            out.flush()
            try:
                prompt_and_store(config, stdin_isatty=sys.stdin.isatty())
            except CampusError as error:
                step("credentials", "failed")
                result["next"] = ["campusctl auth set"]
                return result, error
            step("credentials", "completed")

    out.write("Checking credential presence or helper execution; this does not verify LMS login.\n")
    out.flush()
    try:
        if provider == "command":
            _, _, check_status = helper_status(config, check=True)
            if check_status != "ok":
                raise CampusError(
                    "credential-helper-failed",
                    "The credential helper failed its status check.",
                    "Check the configured helper and account, then retry.",
                    "error",
                )
        elif not keyring_status(config)[1]:
            raise CampusError(
                "credentials-not-configured",
                "No saved credentials are available for this account.",
                "Run 'campusctl auth set' to save credentials.",
                "user-action",
            )
    except CampusError as error:
        step("check", "failed")
        result["next"] = ["campusctl auth status --check"]
        return result, error
    step("check", "completed")

    if run_sync is not None:
        try:
            now = datetime.now(UTC)
            for domain in _SYNC_DOMAINS:
                snapshot = catalog_snapshot(domain, data_dir())
                if snapshot is None:
                    break
                health = cache_metadata(snapshot[0], now=now, domain=domain)
                if health["failed_courses"] or health["enrollment_state"] != "known":
                    break
            else:
                step("sync", "reused")
                return result, None
        except CampusError:
            pass

    if run_sync is None or input("Run a first full sync now? [y/N] ").strip().lower() not in {"y", "yes"}:
        step("sync", "skipped")
        result["next"] = ["campusctl sync"]
        return result, None
    try:
        mode = preflight_browser_mode(config, "lectures.sync", override=headless_override)
        for domain in _SYNC_DOMAINS[1:]:
            preflight_browser_mode(config, f"{domain}.sync", override=headless_override)
        sync_result, errors = run_sync(config, data_dir(), _SYNC_DOMAINS, None, headless=mode)
    except CampusError as error:
        step("sync", "failed")
        result["next"] = ["campusctl sync"]
        return result, error
    result["sync"] = sync_result
    step("sync", "failed" if errors else "completed")
    result["next"] = ["campusctl sync"] if errors else []
    return result, errors or None
