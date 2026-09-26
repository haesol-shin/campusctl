from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from campusctl.envelope import CampusError
from campusctl.paths import config_path

SUPPORTED_SPEEDS = (1.0, 1.25, 1.5)


class ConfigError(CampusError):
    pass


def _invalid(path: Path, key: str, detail: str) -> ConfigError:
    return ConfigError(
        "config-invalid",
        f"Invalid configuration at {path} (key: {key}): {detail}.",
        "Edit the named configuration key using the documented format.",
        "user-action",
    )


def _mapping(value: Any, key: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(path, key, "expected a table")
    return value


def validate_config(config: Any, *, path: Path) -> dict[str, Any]:
    root = _mapping(config, "<root>", path)
    if root.get("provider", "cnu") != "cnu":
        raise _invalid(path, "provider", "unsupported provider")

    account = _mapping(root.get("account", {}), "account", path)
    username = account.get("username")
    if username is not None and (not isinstance(username, str) or not username.strip()):
        raise _invalid(path, "account.username", "expected a non-empty string")

    credentials = _mapping(root.get("credentials", {}), "credentials", path)
    credential_provider = credentials.get("provider", "keyring")
    if not isinstance(credential_provider, str) or credential_provider not in {"keyring", "command"}:
        raise _invalid(path, "credentials.provider", "expected keyring or command")
    command = credentials.get("command")
    if credential_provider == "command":
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise _invalid(path, "credentials.command", "expected a non-empty argv list")
        if not Path(command[0]).is_absolute():
            raise _invalid(path, "credentials.command", "the helper executable path must be absolute")
    elif command is not None and (
        not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command)
    ):
        raise _invalid(path, "credentials.command", "expected a non-empty argv list")

    browser = _mapping(root.get("browser", {}), "browser", path)
    for key in ("cdp_endpoint", "lock_path", "executable_path"):
        if key in browser and (not isinstance(browser[key], str) or not browser[key]):
            raise _invalid(path, f"browser.{key}", "expected a non-empty string")

    playback = _mapping(root.get("playback", {}), "playback", path)
    speed = playback.get("default_speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, int | float) or float(speed) not in SUPPORTED_SPEEDS:
        raise _invalid(path, "playback.default_speed", "expected one of the supported speeds")

    return root


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or config_path()
    try:
        with target.open("rb") as source:
            raw = tomllib.load(source)
    except FileNotFoundError:
        raise ConfigError(
            "config-missing",
            f"Configuration file not found: {target}.",
            f"Run 'campusctl config init' to create {target}; edit the file directly for advanced settings.",
            "user-action",
        ) from None
    except (OSError, tomllib.TOMLDecodeError):
        raise ConfigError(
            "config-invalid",
            f"Could not read valid configuration from {target}.",
            "Check the configuration file syntax and permissions.",
            "user-action",
        ) from None
    return validate_config(raw, path=target)
