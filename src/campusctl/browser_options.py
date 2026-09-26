"""Resolve browser presentation without granting pending LMS operations."""

from __future__ import annotations

from typing import Any

from campusctl.envelope import CampusError

# Release integration changes a value only after recording that operation's live gate.
HEADLESS_SUPPORT: dict[str, bool] = {
    "lectures.sync": False,
    "assignments.sync": False,
    "notices.sync": False,
    "materials.sync": False,
    "materials.download": True,
    "lectures.play": False,
}


def resolve_headless(config: dict[str, Any], override: bool | None = None) -> bool:
    """Return the effective mode: explicit CLI choice, config, then headed."""
    if override is not None:
        return override
    return config.get("browser", {}).get("headless", False)


def operation_headless_supported(operation: str) -> bool:
    """Report only explicitly reviewed operation support."""
    return HEADLESS_SUPPORT.get(operation, False)


def preflight_browser_mode(config: dict[str, Any], operation: str, *, override: bool | None = None) -> bool:
    """Refuse unsupported modes before acquiring a session lock or launching Chromium."""
    headless = resolve_headless(config, override)
    if headless and not operation_headless_supported(operation):
        raise CampusError(
            "headless-unavailable",
            f"Headless mode is not available for {operation}.",
            "Use --headed for this operation until headless support is approved.",
            "user-action",
        )
    if headless and config.get("browser", {}).get("cdp_endpoint"):
        raise CampusError(
            "headless-unavailable",
            "Headless mode cannot be used with a configured CDP browser.",
            "Use a local browser for headless mode.",
            "user-action",
        )
    return headless
