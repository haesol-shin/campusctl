from __future__ import annotations

import json
import os
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from campusctl.browser import chromium_installed
from campusctl.config import load_config
from campusctl.credentials import keyring_status, prompt_and_store
from campusctl.envelope import CampusError, UsageError
from campusctl.paths import config_path
from campusctl.setup import run_setup


def config_exists_error(path: Path) -> CampusError:
    return CampusError(
        "config-exists",
        f"Configuration file already exists: {path}.",
        f"Edit {path} directly, or move it before running 'campusctl config init' again.",
    )


def create_config(username: str) -> tuple[Path, dict[str, Any]]:
    if not username.strip() or any(unicodedata.category(character) == "Cc" for character in username):
        raise CampusError(
            "config-invalid",
            "The CNU login ID must be non-empty and contain no control characters.",
            "Pass a non-empty login ID without control characters using --username.",
        )
    try:
        username.encode("utf-8")
    except UnicodeEncodeError:
        raise CampusError(
            "config-invalid",
            "The CNU login ID must be valid Unicode text.",
            "Pass a valid Unicode login ID using --username.",
        ) from None

    path = config_path(create_dir=True)
    if path.exists():
        raise config_exists_error(path)
    content = (
        'provider = "cnu"\n\n'
        "[account]\n"
        f"username = {json.dumps(username, ensure_ascii=False)}\n\n"
        "[credentials]\n"
        'provider = "keyring"\n\n'
        "[playback]\n"
        "default_speed = 1.0\n"
    )
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            raise config_exists_error(path) from None
    finally:
        temporary_path.unlink(missing_ok=True)

    return path, load_config(path)


def _password_prompt(config: dict[str, Any]) -> tuple[bool, CampusError | None]:
    try:
        answer = input("Save your password now? [Y/n] ")
    except Exception as error:
        return False, CampusError("internal", type(error).__name__, None, "error")
    if answer.strip().lower() in {"n", "no"}:
        return False, None
    print("Password input is hidden.")
    try:
        prompt_and_store(config, stdin_isatty=True)
    except CampusError as error:
        return False, error
    except Exception as error:
        return False, CampusError("internal", type(error).__name__, None, "error")
    print("Password saved to your OS keyring.")
    return True, None


def _confirm_setup(prompt: str) -> bool:
    return input(f"{prompt} ").strip().lower() not in {"n", "no"}


def _setup_browser(config: dict[str, Any]) -> tuple[bool, CampusError | None, str | None]:
    browser = config.get("browser", {})
    if browser.get("cdp_endpoint"):
        return False, None, None

    executable_path = browser.get("executable_path")
    if executable_path:
        if chromium_installed(executable_path):
            return False, None, None
        return (
            False,
            None,
            f"The browser at browser.executable_path was not found. Fix or remove it in {config_path()}.",
        )
    if chromium_installed():
        return False, None, None
    try:
        result = run_setup(config, confirm=_confirm_setup, out=sys.stdout)
    except CampusError as error:
        return True, error, None
    return not result["browser"]["installed"], None, None


def _finish_interactive(
    result: dict[str, Any],
    config: dict[str, Any],
    *,
    password_missing: bool,
    error: CampusError | None,
) -> tuple[dict[str, Any], CampusError | None]:
    browser_missing, setup_error, custom_browser_guidance = _setup_browser(config)
    if custom_browser_guidance is not None:
        next_steps = [custom_browser_guidance]
        if password_missing:
            next_steps.append("campusctl auth set")
    else:
        next_command = (
            "campusctl auth set" if password_missing else "campusctl setup" if browser_missing else "campusctl sync"
        )
        next_steps = [next_command]
    result["next"] = next_steps
    return result, error or setup_error


def _existing_config_init(path: Path) -> tuple[dict[str, Any], CampusError | None]:
    exists_error = config_exists_error(path)
    try:
        config = load_config(path)
    except CampusError:
        return {}, exists_error

    result = {"config_path": str(path), "created": False}
    credentials = config.get("credentials", {})
    username = config.get("account", {}).get("username")
    password_missing = False
    error: CampusError | None = exists_error

    if credentials.get("provider", "keyring") == "keyring" and username:
        try:
            _, configured = keyring_status(config)
        except CampusError as credential_error:
            error = CampusError(
                exists_error.code,
                credential_error.message,
                credential_error.remediation or exists_error.remediation,
            )
            password_missing = True
        else:
            if not configured:
                saved, password_error = _password_prompt(config)
                password_missing = not saved
                if saved:
                    error = None
                elif password_error is not None:
                    error = CampusError(
                        exists_error.code,
                        password_error.message,
                        password_error.remediation or exists_error.remediation,
                    )

    return _finish_interactive(
        result,
        config,
        password_missing=password_missing,
        error=error,
    )


def run_config_init(
    username: str | None, *, interactive: bool, guided: bool = False
) -> tuple[dict[str, Any], CampusError | None]:
    path = config_path()
    if path.exists():
        if guided:
            load_config(path)
            return {"config_path": str(path), "created": False, "next": []}, None
        if interactive:
            return _existing_config_init(path)
        raise config_exists_error(path)

    if username is None:
        if not interactive:
            raise UsageError("Pass --username to 'campusctl config init' when not using an interactive terminal.")
        try:
            username = input("CNU login ID: ")
        except Exception as error:
            raise CampusError("internal", type(error).__name__, None, "error") from None
    try:
        path, config = create_config(username)
    except CampusError as error:
        if guided and error.code == "config-exists":
            load_config(path)
            return {"config_path": str(path), "created": False, "next": []}, None
        if interactive and error.code == "config-exists":
            return _existing_config_init(path)
        raise

    result = {"config_path": str(path), "created": True, "next": ["campusctl auth set"]}
    if not interactive or guided:
        return result, None

    saved, password_error = _password_prompt(config)
    return _finish_interactive(
        result,
        config,
        password_missing=not saved,
        error=password_error,
    )
