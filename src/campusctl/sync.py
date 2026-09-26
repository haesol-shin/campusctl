"""Dispatch selected metadata domains in canonical order."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from campusctl.browser import profile_context
from campusctl.browser_options import preflight_browser_mode
from campusctl.catalog_view import DOMAINS
from campusctl.envelope import CampusError, error_item
from campusctl.providers.cnu.sync_all import DomainOutcome, sync_all


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
    """Validate all requested modes, then collect in one browser session."""
    selected = tuple(domain for domain in DOMAINS if domain in domains)
    if len(selected) != len(set(domains)) or not selected:
        raise CampusError("unsupported-domain", "Unknown sync domain.", "Use a supported --only subset.", "user-action")
    modes = {domain: preflight_browser_mode(config, f"{domain}.sync", override=headless) for domain in selected}

    async def collect() -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
        try:
            outcomes = await sync_all(
                config, root, selected, course_id, headless=modes[selected[0]], course_snapshot=course_snapshot
            )
        except CampusError as error:
            outcomes = {selected[0]: DomainOutcome(errors=[error], status=error.status)}
            outcomes.update({domain: DomainOutcome(status="not-started") for domain in selected[1:]})
        flattened: list[CampusError] = []
        visible: dict[str, dict[str, Any]] = {}
        committed = False
        for domain in selected:
            outcome = outcomes[domain]
            flattened.extend(outcome.errors)
            committed |= bool(outcome.result)
            status = outcome.status or (
                "partial" if outcome.result and outcome.errors else outcome.errors[0].status if outcome.errors else "ok"
            )
            visible[domain] = {
                "status": status,
                "result": outcome.result,
                "errors": [error_item(error) for error in outcome.errors],
            }
        if len(selected) == 1:
            result = visible[selected[0]]["result"]
            return result, (flattened[0] if flattened and not result else flattened or None)
        result = {"domains": visible}
        return result, flattened[0] if flattened and not committed else flattened or None

    with profile_context(profile):
        return asyncio.run(collect())
