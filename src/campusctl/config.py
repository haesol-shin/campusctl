from __future__ import annotations

import re
import sys
import tomllib
import unicodedata
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


_PLACEHOLDERS = {"course", "course_id", "semester"}
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I)


def _material_component(value: str, key: str, path: Path) -> None:
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized in {".", ".."}
        or normalized.endswith((" ", "."))
        or len(normalized.encode("utf-8")) > 200
        or _DEVICE.fullmatch(normalized)
        or any(unicodedata.category(ch) == "Cc" or ch in '<>:"|?*\\/' for ch in normalized)
    ):
        raise _invalid(path, key, "unsafe path component")


def _material_template(template: str, adopt: bool, path: Path) -> None:
    key = "materials.download_dir"
    if not template.strip() or (template.startswith("~") and not template.startswith(("~/", "~\\"))):
        raise _invalid(path, key, "expected a non-empty absolute path template")
    if sys.platform != "win32" and not template.startswith(("/", "~/", "$")):
        raise _invalid(path, key, "expected an absolute path template")
    if sys.platform == "win32" and not (
        template.startswith(("~/", "~\\", "$", "%", "\\\\", "//")) or re.match(r"^[A-Za-z]:[/\\]", template)
    ):
        raise _invalid(path, key, "expected an absolute path template")
    if template == "/" or (sys.platform == "win32" and re.fullmatch(r"[A-Za-z]:[/\\]", template)):
        if adopt:
            raise _invalid(path, key, "adoption requires a course component")
        return
    # Environment references are checked after expansion; placeholders must
    # occupy whole components, never a substring or format expression.
    components = re.split(r"[/\\]" if sys.platform == "win32" else r"/", template)
    course_positions: list[int] = []
    for index, part in enumerate(components):
        if not part:
            if index == 0 or (sys.platform == "win32" and index < 3):
                continue
            raise _invalid(path, key, "empty path component")
        if part in {"{" + name + "}" for name in _PLACEHOLDERS}:
            if part in ("{course}", "{course_id}"):
                course_positions.append(index)
            continue
        without_env = re.sub(
            r"\$[A-Za-z_][A-Za-z_0-9]*|\$\{[A-Za-z_][A-Za-z_0-9]*\}"
            + (r"|%[A-Za-z_][A-Za-z_0-9]*%" if sys.platform == "win32" else ""),
            "x",
            part,
        )
        if "{" in without_env or "}" in without_env or "$" in without_env or "%" in without_env:
            raise _invalid(path, key, "invalid placeholder or environment reference")
        if index == 0 and (part == "~" or (sys.platform == "win32" and re.fullmatch(r"[A-Za-z]:", part))):
            continue
        if without_env != "x" or part == "x":
            _material_component(without_env, key, path)
    if adopt and (len(course_positions) != 1 or len(components) - course_positions[0] - 1 > 4):
        raise _invalid(path, key, "adoption requires one course component within four directory levels")


def validate_materials(config: dict[str, Any], path: Path) -> dict[str, Any]:
    settings = _mapping(config.get("materials", {}), "materials", path)
    template = settings.get("download_dir")
    semester = settings.get("semester")
    adopt = settings.get("adopt_existing", False)
    if template is not None and (not isinstance(template, str) or not template.strip()):
        raise _invalid(path, "materials.download_dir", "expected a non-empty string")
    if semester is not None and (not isinstance(semester, str) or not semester.strip()):
        raise _invalid(path, "materials.semester", "expected a non-empty string")
    if not isinstance(adopt, bool):
        raise _invalid(path, "materials.adopt_existing", "expected true or false")
    if template is not None:
        _material_template(template, adopt, path)
        if "{semester}" in template and semester is None:
            raise _invalid(path, "materials.semester", "required by materials.download_dir")
    elif adopt:
        raise _invalid(path, "materials.adopt_existing", "requires materials.download_dir")
    return settings


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
    if "headless" in browser and not isinstance(browser["headless"], bool):
        raise _invalid(path, "browser.headless", "expected true or false")

    playback = _mapping(root.get("playback", {}), "playback", path)
    speed = playback.get("default_speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, int | float) or float(speed) not in SUPPORTED_SPEEDS:
        raise _invalid(path, "playback.default_speed", "expected one of the supported speeds")

    validate_materials(root, path)

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
