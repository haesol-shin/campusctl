from __future__ import annotations

import os
import sys
from pathlib import Path


def _xdg_home(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable)
    if not value:
        return fallback
    path = Path(value).expanduser()
    return path if path.is_absolute() else fallback


def _platform_config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "campusctl"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "campusctl"
    return _xdg_home("XDG_CONFIG_HOME", Path.home() / ".config") / "campusctl"


def _platform_data_dir() -> Path:
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home() / "AppData" / "Local")
            / "campusctl"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "campusctl"
    return _xdg_home("XDG_DATA_HOME", Path.home() / ".local" / "share") / "campusctl"


def config_dir(*, create: bool = False) -> Path:
    path = Path(os.environ.get("CAMPUSCTL_CONFIG_DIR") or _platform_config_dir()).expanduser()
    return ensure_private_dir(path) if create else path


def data_dir(*, create: bool = False) -> Path:
    path = Path(os.environ.get("CAMPUSCTL_DATA_DIR") or _platform_data_dir()).expanduser()
    return ensure_private_dir(path) if create else path


def config_path(*, create_dir: bool = False) -> Path:
    return config_dir(create=create_dir) / "config.toml"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
