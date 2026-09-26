"""Dispatch selected metadata domains in canonical order."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from campusctl.browser import pre_browser_check, profile_context
from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import DOMAINS, assert_course_snapshot_current
from campusctl.commands import discover_domain_modules
from campusctl.envelope import CampusError, error_item


def parse_domains(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DOMAINS
    tokens = [token.strip() for token in value.split(",")]
    if not tokens or any(token not in DOMAINS for token in tokens):
        raise CampusError(
            "unsupported-domain",
            "Unknown or empty sync domain.",
            "Use a comma-separated subset of lectures,assignments,notices,materials.",
            "user-action",
        )
    return tuple(domain for domain in DOMAINS if domain in tokens)


def run_sync(
    config: dict[str, Any],
    root: Path,
    domains: tuple[str, ...],
    course_id: str | None,
    *,
    headless: bool | None = None,
    profile: Any = None,
    course_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    """Validate every mode before dispatch; keep committed domain outcomes visible."""
    selected = tuple(domain for domain in DOMAINS if domain in domains)
    if len(selected) != len(set(domains)) or not selected:
        raise CampusError("unsupported-domain", "Unknown sync domain.", "Use a supported --only subset.", "user-action")
    modes = {}
    for domain in selected:
        modes[domain] = preflight_browser_mode(config, f"{domain}.sync", override=headless)
    modules = discover_domain_modules()

    async def collect() -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
        outcomes: dict[str, dict[str, Any]] = {}
        flattened: list[CampusError] = []
        committed = False
        halted = False
        entered = False
        for domain in selected:
            if halted:
                outcomes[domain] = {"status": "not-started", "result": {}, "errors": []}
                continue
            try:
                check = (
                    pre_browser_check(lambda: assert_course_snapshot_current(root, course_snapshot))
                    if course_snapshot is not None and not entered
                    else nullcontext()
                )
                with check:
                    if domain == "lectures":
                        from campusctl.providers.cnu.sync import sync_lectures

                        result, errors = await sync_lectures(config, root, course_id, headless=modes[domain])
                    else:
                        result, errors = await modules[domain].sync(config, root, course_id, headless=modes[domain])
                entered = True
            except CampusError as error:
                result, errors = {}, [error]
            errors = errors or []
            flattened.extend(errors)
            status = "partial" if result and errors else errors[0].status if errors else "ok"
            outcomes[domain] = {"status": status, "result": result, "errors": [error_item(error) for error in errors]}
            committed |= bool(result)
            halted = any(
                error.status in {"busy", "user-action"}
                or error.code in {"policy-blocked", "authentication-failed", "auth-failed", "login-failed"}
                for error in errors
            )
        if len(selected) == 1:
            outcome = outcomes[selected[0]]
            return outcome["result"], (flattened[0] if flattened and not outcome["result"] else flattened or None)
        if flattened and not committed:
            return {"domains": outcomes}, flattened[0]
        return {"domains": outcomes}, flattened or None

    with profile_context(profile):
        return asyncio.run(collect())
