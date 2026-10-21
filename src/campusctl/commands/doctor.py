"""The doctor command."""

from __future__ import annotations

import argparse
from typing import Any

from campusctl.cli_args import _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, Rendered, command_result
from campusctl.doctor import doctor_result
from campusctl.text_layout import _command_hint, _wrap


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = subparsers.add_parser("doctor", help="check local readiness")
    _with_json(doctor)


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    del context
    return command_result(*doctor_result(args.headless_override, bindings.capabilities))


def _marker(ok: bool | None, label: str, color: bool) -> str:
    if ok is True:
        marker = "[ok]"
        if color:
            marker = f"\033[32m{marker}\033[0m"
    else:
        marker = "[!!]"
        if color:
            marker = f"\033[33m{marker}\033[0m"
    return f"{marker} {label}"


def _doctor(result: dict[str, Any], width: int, errors: object) -> tuple[list[str], bool]:
    checks: list[str] = []
    config = result.get("config")
    if isinstance(config, dict) and isinstance(config.get("present"), bool):
        present = config["present"]
        checks.append(
            _marker(
                present,
                "Configuration file present" if present else "Configuration file missing",
                False,
            )
        )

    credentials = result.get("credentials")
    if isinstance(credentials, dict) and "configured" in credentials:
        configured = credentials.get("configured")
        label = (
            "Credentials configured"
            if configured is True
            else "Credentials not configured"
            if configured is False
            else "Credential status unknown"
        )
        checks.append(_marker(configured is True, label, False))

    browser = result.get("browser")
    if isinstance(browser, dict):
        mode = browser.get("mode")
        if mode == "cdp":
            checks.append(
                _marker(
                    True,
                    "Using an existing browser (connection is checked when you run sync or play)",
                    False,
                )
            )
        elif mode == "local":
            if isinstance(browser.get("playwright_importable"), bool):
                installed = browser["playwright_importable"]
                checks.append(
                    _marker(installed, "Playwright available" if installed else "Playwright not installed", False)
                )
            if isinstance(browser.get("chromium_installed"), bool):
                installed = browser["chromium_installed"]
                checks.append(
                    _marker(installed, "Chromium installed" if installed else "Chromium not installed", False)
                )
            display_available = result.get("display_available")
            if isinstance(display_available, bool):
                checks.append(
                    _marker(
                        display_available,
                        "Display available" if display_available else "Display unavailable",
                        False,
                    )
                )

    materials = result.get("materials")
    if isinstance(materials, dict) and materials.get("adopt_existing") is not None:
        if materials.get("download_dir") is None:
            checks.append("[ok] Materials downloads: default")
        else:
            adoption = "on" if materials["adopt_existing"] else "off"
            checks.append(f"[ok] Materials downloads: configured (adoption {adoption})")

    catalog = result.get("catalog")
    catalog_missing = isinstance(catalog, dict) and catalog.get("present") is False
    if isinstance(catalog, dict) and isinstance(catalog.get("present"), bool):
        present = catalog["present"]
        checks.append(_marker(present, "Lecture catalog present" if present else "Lecture catalog missing", False))

    has_errors = bool(errors)
    only_catalog_missing_error = (
        isinstance(errors, list)
        and len(errors) == 1
        and isinstance(errors[0], dict)
        and errors[0].get("code") == "catalog-missing"
    )
    has_other_failures = any(check.startswith("[!!]") and check != "[!!] Lecture catalog missing" for check in checks)
    catalog_is_only_failure = (
        catalog_missing
        and only_catalog_missing_error
        and not has_other_failures
        and any(check == "[!!] Lecture catalog missing" for check in checks)
    )
    if catalog_is_only_failure:
        checks.remove("[!!] Lecture catalog missing")

    lines = [line for check in checks for line in _wrap(check, width)]
    if catalog_is_only_failure:
        lines.extend(_command_hint("Almost ready. Next:", "campusctl sync", width))
    elif not has_errors and not catalog_missing and not has_other_failures and checks:
        lines.append("Ready.")
    return lines, catalog_is_only_failure


def render_response(command: str, result: dict[str, Any], width: int, envelope: dict[str, Any]) -> Rendered:
    del command
    lines, suppress_catalog_error = _doctor(result, width, envelope.get("errors"))
    return Rendered(lines, suppress_catalog_error)
