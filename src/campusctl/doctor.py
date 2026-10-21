"""Collect local readiness diagnostics for the doctor command."""

from __future__ import annotations

import importlib
import os
import platform
import sys
from pathlib import Path
from typing import Any

from campusctl import __version__
from campusctl.browser_options import operation_headless_supported, resolve_headless
from campusctl.catalog import catalog_path, read_catalog
from campusctl.config import SUPPORTED_SPEEDS, load_config
from campusctl.credentials import keyring_status
from campusctl.envelope import CampusError
from campusctl.paths import config_path, data_dir


def _playwright_diagnostics(executable_path: str | None) -> tuple[bool, bool]:
    try:
        importlib.import_module("playwright.sync_api._context_manager")
    except Exception:
        return False, False
    if executable_path:
        try:
            return True, Path(executable_path).is_file()
        except OSError:
            return True, False
    try:
        context_manager = importlib.import_module("playwright.sync_api._context_manager")
        playwright = context_manager.PlaywrightContextManager().start()
        try:
            return True, Path(playwright.chromium.executable_path).is_file()
        finally:
            playwright.stop()
    except Exception:
        return True, False


def _read_catalog_generated_at(path: Path) -> tuple[bool, str | None, CampusError | None]:
    try:
        catalog = read_catalog(path)
    except CampusError as error:
        return path.exists(), None, error
    return True, catalog["generated_at"], None


def doctor_result(  # noqa: C901
    override: bool | None, capabilities: dict[str, list[str]]
) -> tuple[dict[str, Any], CampusError | None]:
    cfg_path = config_path()
    data_path = data_dir()
    config_present = cfg_path.is_file()
    config: dict[str, Any] | None = None
    first_error: CampusError | None = None
    if config_present:
        try:
            config = load_config(cfg_path)
        except CampusError as error:
            first_error = error
    else:
        try:
            load_config(cfg_path)
        except CampusError as error:
            first_error = error

    if config is not None:
        from campusctl.material_files import check_download_environment

        try:
            check_download_environment(config)
        except CampusError as error:
            first_error = error
            config = None

    provider = config.get("provider", "cnu") if config else None
    credential_provider = config.get("credentials", {}).get("provider", "keyring") if config else None
    credentials_configured = False
    if config and credential_provider == "keyring":
        try:
            _, credentials_configured = keyring_status(config)
        except CampusError as error:
            if first_error is None:
                first_error = error
        if first_error is None and not credentials_configured:
            first_error = CampusError(
                "credentials-not-configured",
                "No saved credentials are available for this provider.",
                "Run 'campusctl auth set' or configure the credential helper.",
                "user-action",
            )
    elif config and credential_provider == "command":
        credentials_configured = bool(config.get("credentials", {}).get("command"))

    browser_config = config.get("browser", {}) if config else {}
    browser_mode = "cdp" if browser_config.get("cdp_endpoint") else "local"
    effective_headless = resolve_headless(config or {}, override)
    playwright_importable: bool | None = None
    chromium_installed: bool | None = None
    if browser_mode == "local":
        playwright_importable, chromium_installed = _playwright_diagnostics(browser_config.get("executable_path"))
        if first_error is None and (not playwright_importable or not chromium_installed):
            first_error = CampusError(
                "browser-not-installed",
                "Playwright or its Chromium browser is not installed.",
                "Run 'campusctl setup' to install the browser.",
                "user-action",
            )
    display_available: bool | None = None
    if sys.platform.startswith("linux"):
        display_available = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if browser_mode == "local" and not effective_headless and not display_available and first_error is None:
            first_error = CampusError(
                "display-unavailable",
                "No graphical display is available for visible browser playback.",
                "Configure a display or run the command with 'xvfb-run'.",
                "user-action",
            )

    catalog_present, generated_at, catalog_error = _read_catalog_generated_at(catalog_path(data_path))
    if first_error is None:
        first_error = catalog_error
    result = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config": {"path": str(cfg_path), "present": config_present},
        "data_dir": str(data_path),
        "provider": provider,
        "credentials": {"provider": credential_provider, "configured": credentials_configured},
        "browser": {
            "mode": browser_mode,
            "headless": effective_headless,
            "headless_support": {
                name: operation_headless_supported(name)
                for name in (
                    "lectures.sync",
                    "assignments.sync",
                    "notices.sync",
                    "materials.sync",
                    "materials.download",
                    "assignments.fetch",
                    "notices.fetch",
                    "lectures.play",
                )
            },
            "playwright_importable": playwright_importable,
            "chromium_installed": chromium_installed,
        },
        "display_available": display_available,
        "playback": {
            "default_speed": config.get("playback", {}).get("default_speed", 1.0) if config else 1.0,
            "supported_speeds": list(SUPPORTED_SPEEDS),
        },
        "materials": {
            "download_dir": config.get("materials", {}).get("download_dir") if config else None,
            "adopt_existing": config.get("materials", {}).get("adopt_existing", False) if config else None,
            "semester": config.get("materials", {}).get("semester") if config else None,
        },
        "catalog": {"present": catalog_present, "generated_at": generated_at},
        "capabilities": capabilities,
    }
    return result, first_error
