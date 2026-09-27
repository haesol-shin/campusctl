"""Ordered multi-ID fetch in one authenticated browser session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS
from campusctl.envelope import CampusError
from campusctl.providers.cnu.login import LOGIN_FORM_SELECTOR

Capture = Callable[[Any, dict[str, Any]], Awaitable[Any]]
ErrorFactory = Callable[[str, Exception], CampusError]

_ITEM_CODES = frozenset(
    {
        "entity-unknown",
        "fetch-failed",
        "unsupported-media-type",
        "file-too-large",
        "policy-blocked",
        "output-path-conflict",
    }
)
_SESSION_CODES = frozenset(
    {
        "login-action-required",
        "session-busy",
        "lms-unavailable",
        "browser-endpoint-unreachable",
        "browser-launch-failed",
        "browser-session-failed",
        "browser-not-installed",
        "display-unavailable",
    }
)
_PACKAGE_CODES = frozenset({"unsupported-media-type", "file-too-large", "policy-blocked", "output-path-conflict"})


def _closed(page: Any, error: BaseException | None = None) -> bool:
    is_closed = getattr(page, "is_closed", None)
    if callable(is_closed):
        try:
            if is_closed():
                return True
        except Exception:
            return True
    context = getattr(page, "context", None)
    browser = getattr(context, "browser", None) if context is not None else None
    connected = getattr(browser, "is_connected", None)
    if callable(connected):
        try:
            if not connected():
                return True
        except Exception:
            return True
    if error is None:
        return False
    name = type(error).__name__.casefold()
    text = str(error).casefold()
    return "targetclosed" in name or "has been closed" in text or "target closed" in text


async def _login_form_visible(page: Any) -> bool:
    is_visible = getattr(page, "is_visible", None)
    if not callable(is_visible):
        return False
    try:
        return bool(await asyncio.wait_for(is_visible(LOGIN_FORM_SELECTOR), timeout=PROTOCOL_TIMEOUT_SECONDS))
    except Exception:
        return False


def _expired() -> CampusError:
    return CampusError(
        "lms-unavailable",
        "The LMS session is no longer authenticated.",
        "Check the LMS session and network connection, then retry.",
        "error",
    )


async def _release_item_state(page: Any) -> None:
    unroute_all = getattr(page, "unroute_all", None)
    if not callable(unroute_all):
        return
    with suppress(Exception):
        await asyncio.wait_for(unroute_all(behavior="ignoreErrors"), timeout=PROTOCOL_TIMEOUT_SECONDS)


async def classify_item_failure(
    page: Any,
    error: Exception,
    *,
    label: str,
    fetch_error: ErrorFactory,
) -> tuple[CampusError, bool]:
    """Classify one item failure before generic fetch-failed normalization."""
    closed = _closed(page, error)
    if isinstance(error, CampusError) and error.code == "login-failed":
        return fetch_error("authentication", _expired()), True
    if isinstance(error, CampusError) and error.code in _SESSION_CODES:
        step = "authentication" if error.code in {"login-action-required", "lms-unavailable"} else "browser session"
        return fetch_error(step, error), True
    if closed:
        return fetch_error("browser session", _closed_error()), True
    if isinstance(error, CampusError) and error.code == "browser-timeout":
        return fetch_error("detail capture", error), False
    if isinstance(error, CampusError) and error.code in _ITEM_CODES:
        step = "package creation" if error.code in _PACKAGE_CODES else "detail capture"
        return fetch_error(step, error), False
    if await _login_form_visible(page):
        return fetch_error("authentication", _expired()), True
    return fetch_error("detail capture", error), False


def _closed_error() -> CampusError:
    return CampusError(
        "browser-session-failed",
        "The browser session closed.",
        "Check the browser session and retry.",
        "error",
    )


def _failed_item(entity_id: str, error: CampusError) -> dict[str, Any]:
    return {"entity_id": entity_id, "outcome": "failed", "reason_code": error.code}


def _not_started(entity_id: str) -> dict[str, Any]:
    return {"entity_id": entity_id, "outcome": "not-started"}


async def run_fetch_queue(
    config: dict[str, Any],
    root: Path,
    rows: list[dict[str, Any]],
    *,
    headless: bool,
    operation: str,
    domain: str,
    kind: str,
    label: str,
    capture: Capture,
    fetch_error: ErrorFactory,
) -> tuple[list[dict[str, Any]], list[CampusError] | None]:
    """Authenticate once, then capture and publish each selected detail in order."""
    from campusctl.browser import open_session, settle_sso_popups
    from campusctl.providers.cnu.login import ensure_logged_in
    from campusctl.source_package import build_source_package

    items: list[dict[str, Any]] = []
    errors: list[CampusError] = []
    async with open_session(config, data_dir=root, headless=headless, operation=operation) as session:
        page = session.page
        try:
            await ensure_logged_in(page, config)
        except Exception as exc:
            raise fetch_error("authentication", exc) from exc
        try:
            await settle_sso_popups(session, domain=domain)
        except Exception as exc:
            raise fetch_error("SSO settlement", exc) from exc

        for index, row in enumerate(rows):
            entity_id = row["entity_id"]
            try:
                try:
                    snapshot = await capture(page, row)
                    package = await build_source_package(
                        page,
                        snapshot,
                        entity_id=entity_id,
                        kind=kind,
                        course_id=row["course"]["id"],
                        course_label=row["course"]["label"],
                        root=root,
                    )
                except Exception as exc:
                    classified, session_level = await classify_item_failure(
                        page, exc, label=label, fetch_error=fetch_error
                    )
                    items.append(_failed_item(entity_id, classified))
                    errors.append(classified)
                    if session_level:
                        items.extend(_not_started(later["entity_id"]) for later in rows[index + 1 :])
                        break
                else:
                    if package.get("completeness") == "partial":
                        errors.append(
                            CampusError(
                                "resource-omitted",
                                f"{label} fetch packaging: some resources are unsupported.",
                                status="user-action",
                            )
                        )
                        items.append(
                            {
                                "entity_id": entity_id,
                                "outcome": "partial",
                                "source_package": package,
                                "reason_code": "resource-omitted",
                            }
                        )
                    else:
                        items.append({"entity_id": entity_id, "outcome": "completed", "source_package": package})
            finally:
                await _release_item_state(page)
    return items, errors or None
