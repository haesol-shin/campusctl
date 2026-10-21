"""Stale-cache warning lines shared by the notices and materials renderers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from campusctl.text_layout import _wrap_indented


def cache_warning_lines(
    state: object,
    failed: object,
    width: int,
    *,
    unknown_enrollment: str,
    stale_course: Callable[[dict[str, Any]], str],
) -> list[str]:
    """Warnings in fixed order: unknown enrollment, then one per failed course in catalog order.

    Catalog JSON is not validated this deep, so a non-list `failed` and non-object entries are
    skipped. Each domain words its own messages; this owns the order and omission rule.
    """
    lines: list[str] = []
    if state == "unknown":
        lines.extend(_wrap_indented(unknown_enrollment, width))
    if isinstance(failed, list):
        for course in failed:
            if isinstance(course, dict):
                lines.extend(_wrap_indented(stale_course(course), width))
    return lines
