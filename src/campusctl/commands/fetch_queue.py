"""Fetch selection, single-item fetch and ordered multi-ID fetch for detail-package domains."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS
from campusctl.command_types import CommandResult, command_result
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog
from campusctl.envelope import CampusError, Status, UsageError, error_item
from campusctl.paths import data_dir
from campusctl.providers.cnu.login import LOGIN_FORM_SELECTOR

Capture = Callable[[Any, dict[str, Any]], Awaitable[Any]]
ErrorFactory = Callable[[str, Exception], CampusError]
# Adapts a provider's typed entity-unknown error; None leaves it to the generic wording.
EntityUnknownAdapter = Callable[[CampusError], CampusError | None]


FetchOutcome = Literal["completed", "partial", "failed", "not-started"]


@dataclass(frozen=True, slots=True)
class FetchItem:
    """One selected ID's result in a multi-ID fetch."""

    entity_id: str
    outcome: FetchOutcome
    source_package: dict[str, Any] | None = None
    reason_code: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Keys in envelope order; a package or reason the item lacks is omitted."""
        item: dict[str, Any] = {"entity_id": self.entity_id, "outcome": self.outcome}
        if self.source_package is not None:
            item["source_package"] = self.source_package
        if self.reason_code is not None:
            item["reason_code"] = self.reason_code
        return item


@dataclass(frozen=True, slots=True)
class FetchDomain:
    """Wording and policy a domain supplies to the shared single and batch fetch.

    `name` is the catalog, command and operation name (`assignments`); `label` prefixes messages
    (`Assignments`); `kind` is the source-package kind. `timeout_steps` lists the detail steps whose
    timeout message may be repeated; `timeout_wording` renames a step in that message.
    """

    name: str
    label: str
    kind: str
    timeout_steps: frozenset[str]
    timeout_wording: Mapping[str, str] = field(default_factory=dict)
    entity_unknown: EntityUnknownAdapter | None = None

    def fetch_error(self, step: str, error: Exception) -> CampusError:
        code = error.code if isinstance(error, CampusError) else "fetch-failed"
        status = error.status if isinstance(error, CampusError) else "error"
        if step == "detail capture" and isinstance(error, CampusError):
            adapted = self._detail_error(error)
            if adapted is not None:
                return adapted
        remediation = {
            "catalog load": f"Sync {self.name} again and retry.",
            "configuration load": "Check campusctl configuration and retry.",
            "authentication": "Sign in and retry.",
            "detail capture": f"Sync {self.name} again and select the current full ID.",
            "package creation": "Retry the fetch and inspect unsupported resources.",
        }.get(step, "Check the browser session and retry.")
        return CampusError(code, f"{self.label} fetch: {step} failed.", remediation, status)

    def _detail_error(self, error: CampusError) -> CampusError | None:
        if error.code == "browser-timeout":
            detail_step = error.message.removeprefix("Timed out while ").removesuffix(".")
            if error.message == f"Timed out while {detail_step}." and detail_step in self.timeout_steps:
                wording = self.timeout_wording.get(detail_step, detail_step)
                return CampusError(
                    error.code,
                    f"{self.label} fetch: {wording} timed out.",
                    "Check the browser and retry.",
                    error.status,
                )
        if error.code == "entity-unknown" and self.entity_unknown is not None:
            return self.entity_unknown(error)
        return None


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
    # A visible login form is session expiry even when the detail reader reports
    # a timeout, parse failure, or identity mismatch. Do not reopen that item.
    login_visible = await _login_form_visible(page)
    if login_visible and (
        not isinstance(error, CampusError) or error.code in {"browser-timeout", "fetch-failed", "entity-unknown"}
    ):
        return fetch_error("authentication", _expired()), True
    if isinstance(error, CampusError) and error.code == "browser-timeout":
        return fetch_error("detail capture", error), False
    if isinstance(error, CampusError) and error.code in _ITEM_CODES:
        step = "package creation" if error.code in _PACKAGE_CODES else "detail capture"
        return fetch_error(step, error), False
    return fetch_error("detail capture", error), False


def _closed_error() -> CampusError:
    return CampusError(
        "browser-session-failed",
        "The browser session closed.",
        "Check the browser session and retry.",
        "error",
    )


def _omitted_error(label: str) -> CampusError:
    return CampusError(
        "resource-omitted",
        f"{label} fetch packaging: some resources are unsupported.",
        status="user-action",
    )


async def _authenticate(session: Any, config: dict[str, Any], domain: FetchDomain) -> Any:
    """Sign in and settle SSO popups; both single and batch fetch start here."""
    from campusctl.browser import settle_sso_popups
    from campusctl.providers.cnu.login import ensure_logged_in

    page = session.page
    try:
        await ensure_logged_in(page, config)
    except Exception as exc:
        raise domain.fetch_error("authentication", exc) from exc
    try:
        await settle_sso_popups(session, domain=domain.name)
    except Exception as exc:
        raise domain.fetch_error("SSO settlement", exc) from exc
    return page


async def _fetch_item(
    page: Any,
    row: dict[str, Any],
    *,
    root: Path,
    out: Path | None,
    domain: FetchDomain,
    capture: Capture,
    step_error: ErrorFactory | None,
) -> dict[str, Any]:
    """Capture one detail and build its package; the single and batch paths share this body.

    step_error wraps a failing step in the domain error (single fetch); None re-raises
    the original exception so the batch caller can classify it.
    """
    from campusctl.source_package import build_source_package

    try:
        snapshot = await capture(page, row)
    except Exception as exc:
        if step_error is None:
            raise
        raise step_error("detail capture", exc) from exc
    try:
        return await build_source_package(
            page,
            snapshot,
            entity_id=row["entity_id"],
            kind=domain.kind,
            course_id=row["course"]["id"],
            course_label=row["course"]["label"],
            root=root,
            out=out,
        )
    except Exception as exc:
        if step_error is None:
            raise
        raise step_error("package creation", exc) from exc


async def fetch_single(
    config: dict[str, Any],
    root: Path,
    row: dict[str, Any],
    *,
    out: Path | None,
    headless: bool,
    domain: FetchDomain,
    capture: Capture,
) -> dict[str, Any]:
    """Fetch one selected detail; each failing step raises the domain's error for that step."""
    from campusctl.browser import open_session

    try:
        async with open_session(config, data_dir=root, headless=headless, operation=f"{domain.name}.fetch") as session:
            page = await _authenticate(session, config, domain)
            return await _fetch_item(
                page, row, root=root, out=out, domain=domain, capture=capture, step_error=domain.fetch_error
            )
    except Exception as exc:
        # Step errors above already carry the domain wording; only a session-level failure is adapted here.
        if isinstance(exc, CampusError) and exc.message.startswith(f"{domain.label} fetch:"):
            raise
        raise domain.fetch_error("browser session", exc) from exc


async def run_fetch_queue(
    config: dict[str, Any],
    root: Path,
    rows: list[dict[str, Any]],
    *,
    headless: bool,
    domain: FetchDomain,
    capture: Capture,
) -> tuple[list[FetchItem], list[CampusError] | None]:
    """Authenticate once, then capture and publish each selected detail in order."""
    from campusctl.browser import open_session

    items: list[FetchItem] = []
    errors: list[CampusError] = []
    async with open_session(config, data_dir=root, headless=headless, operation=f"{domain.name}.fetch") as session:
        page = await _authenticate(session, config, domain)

        for index, row in enumerate(rows):
            entity_id = row["entity_id"]
            try:
                try:
                    package = await _fetch_item(
                        page, row, root=root, out=None, domain=domain, capture=capture, step_error=None
                    )
                except Exception as exc:
                    classified, session_level = await classify_item_failure(
                        page, exc, label=domain.label, fetch_error=domain.fetch_error
                    )
                    items.append(FetchItem(entity_id, "failed", reason_code=classified.code))
                    errors.append(classified)
                    if session_level:
                        items.extend(FetchItem(later["entity_id"], "not-started") for later in rows[index + 1 :])
                        break
                else:
                    if package.get("completeness") == "partial":
                        errors.append(_omitted_error(domain.label))
                        items.append(FetchItem(entity_id, "partial", package, "resource-omitted"))
                    else:
                        items.append(FetchItem(entity_id, "completed", package))
            finally:
                await _release_item_state(page)
    return items, errors or None


def _selected_ids(args: argparse.Namespace, domain: FetchDomain) -> list[str]:
    """Return the requested IDs in first-seen order; IDs are not trimmed or otherwise normalized."""
    raw = getattr(args, "entity_ids", None)
    if raw is None:
        single = getattr(args, "entity_id", None)
        raw = None if single is None else [single]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not item.strip() for item in raw):
        raise UsageError(f"{domain.label} fetch selection: an entity ID is required")
    ordered: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _matching_rows(catalog: dict[str, Any], domain: FetchDomain, entity_ids: list[str]) -> list[dict[str, Any]]:
    records = catalog.get(domain.name, [])
    rows: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        matching = [row for row in records if row.get("entity_id") == entity_id]
        if len(matching) != 1:
            raise CampusError(
                "entity-unknown",
                f"{domain.label} fetch selection: selected ID is not in the catalog.",
                f"Select one full ID from 'campusctl {domain.name} list'.",
                "user-action",
            )
        rows.append(matching[0])
    return rows


def _selected_rows(domain: FetchDomain, root: Path, entity_ids: list[str]) -> list[dict[str, Any]]:
    cat_path = domain_catalog_path(domain.name, root)
    if not cat_path.exists():
        raise CampusError(
            "catalog-missing",
            f"{domain.label} fetch catalog lookup: catalog is missing.",
            f"Run 'campusctl sync --only {domain.name}' to create it.",
            "user-action",
        )
    try:
        catalog = read_domain_catalog(domain.name, cat_path)
    except Exception as exc:
        raise domain.fetch_error("catalog load", exc) from exc
    return _matching_rows(catalog, domain, entity_ids)


def _check_output(domain: FetchDomain, out_path: Path | None, rows: list[dict[str, Any]]) -> None:
    if out_path is not None and len(rows) > 1:
        raise UsageError(f"{domain.label} fetch selection: --out applies to one selected ID.")
    if out_path is not None:
        destination = Path(out_path)
        if destination.exists() or destination.is_symlink():
            raise CampusError(
                "output-path-conflict",
                f"{domain.label} fetch output selection: output path already exists.",
                "Choose a nonexistent destination path with --out.",
                "user-action",
            )


def _batch_status(items: list[FetchItem], errors: list[CampusError]) -> str:
    """Return the multi-ID envelope status from published packages and ordered failures."""
    published = any(item.outcome in {"completed", "partial"} for item in items)
    unsuccessful = any(item.outcome in {"partial", "failed", "not-started"} for item in items)
    if published and unsuccessful:
        return Status.PARTIAL
    if not published and errors:
        return errors[0].status
    return Status.OK


def dispatch_fetch(
    args: argparse.Namespace,
    domain: FetchDomain,
    *,
    fetch_one: Callable[..., Awaitable[dict[str, Any]]],
    fetch_many: Callable[..., Awaitable[tuple[list[FetchItem], list[CampusError] | None]]],
) -> CommandResult:
    """Validate the selection, then fetch one detail or an ordered batch.

    Everything that can fail before a browser session opens runs first, in a fixed order.
    """
    from campusctl.browser_options import preflight_browser_mode
    from campusctl.config import load_config

    root = data_dir()
    entity_ids = _selected_ids(args, domain)
    rows = _selected_rows(domain, root, entity_ids)
    out_path = getattr(args, "out", None)
    _check_output(domain, out_path, rows)
    try:
        config = load_config()
    except Exception as exc:
        raise domain.fetch_error("configuration load", exc) from exc
    try:
        mode = preflight_browser_mode(config, f"{domain.name}.fetch", override=getattr(args, "headless_override", None))
    except CampusError as exc:
        raise CampusError(
            exc.code, f"{domain.label} fetch browser preflight: {exc.message}", exc.remediation, exc.status
        ) from exc
    if len(rows) == 1:
        package = asyncio.run(fetch_one(config, root, rows[0], out=out_path, headless=mode))
        omitted = [_omitted_error(domain.label)] if package.get("completeness") == "partial" else None
        return command_result({"source_package": package}, omitted)
    items, errors = asyncio.run(fetch_many(config, root, rows, headless=mode))
    result = {"items": [item.to_json() for item in items]}
    if errors is None:
        return command_result(result)
    return CommandResult(result, _batch_status(items, errors), tuple(error_item(error) for error in errors))
