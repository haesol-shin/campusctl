"""Course and row preview lifetime, including one modal close and cleanup precedence.

Each media handler owns its own startup and watch loop. Preview opens and closes the modal once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.wait_clock import current_clock

from .media import MEDIA_HANDLERS, MediaHandler
from .player_types import (
    MediaKind,
    _css_string,
    _PlaybackFailure,
    _PlayDiagnostic,
    _visible_locator,
)

PREVIEW_MODAL_SELECTOR = '#previewModal.show, #previewModal[style*="display: block"]'
PLAYER_CLOSE_SELECTOR = "#previewModal #btnPreviewClose, #previewModal .btn_popClose"

COLLAPSED_TOGGLES_JS = r"""/* campusctl-read-collapse-toggles */(rowId) => {
    const row = document.getElementById(rowId);
    if (!row) return [];
    const toggles = [];
    for (let node = row.parentElement; node && node !== document.body; node = node.parentElement) {
        if (!node.classList.contains('collapse') || node.classList.contains('show') || !node.id) continue;
        const target = `#${CSS.escape(node.id)}`;
        const trigger = document.querySelector(
            `[data-bs-target="${target}"], [data-target="${target}"], a[href="${target}"]`
        );
        if (trigger) {
            if (trigger.id) toggles.push(`#${CSS.escape(trigger.id)}`);
            else if (trigger.hasAttribute('data-bs-target')) toggles.push(`[data-bs-target="${target}"]`);
            else if (trigger.hasAttribute('data-target')) toggles.push(`[data-target="${target}"]`);
            else toggles.push(`a[href="${target}"]`);
        }
    }
    return toggles.reverse();
}"""


async def _expand_row_through_ui(page: Any, row_id: str) -> None:
    row_selector = f"[id={_css_string(row_id)}]"
    row = page.locator(row_selector)
    weekno = await bounded(
        row.get_attribute("data-weekno"),
        PROTOCOL_TIMEOUT_SECONDS,
        "reading the CNU lecture week",
    )
    if not isinstance(weekno, str) or not weekno.strip():
        raise _PlaybackFailure(
            code="lecture-row-unavailable",
            message="The requested lecture's course week could not be found.",
        )
    weekno = weekno.strip()
    week_container_selector = f"[id={_css_string(f'weekIdx{weekno}')}]"

    week_tab = page.locator(f'a[data-act="shortCutWeek"][data-weekidx={_css_string(weekno)}]').first
    tab_count = await bounded(week_tab.count(), PROTOCOL_TIMEOUT_SECONDS, "finding the CNU lecture week tab")
    if tab_count:
        await bounded(
            week_tab.click(timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "selecting the CNU lecture week",
        )
    else:
        show_all = page.locator('[data-act="showAllWeek"]').first
        show_all_count = await bounded(
            show_all.count(),
            PROTOCOL_TIMEOUT_SECONDS,
            "finding the CNU show-all-weeks control",
        )
        if not show_all_count:
            raise _PlaybackFailure(
                code="lecture-row-unavailable",
                message="The requested lecture's course week could not be opened.",
            )
        show_all_status = await bounded(
            show_all.get_attribute("data-status"),
            PROTOCOL_TIMEOUT_SECONDS,
            "checking the CNU show-all-weeks control",
        )
        week_is_visible = await bounded(
            page.locator(week_container_selector).is_visible(),
            PROTOCOL_TIMEOUT_SECONDS,
            "checking whether the requested CNU week is visible",
        )
        if show_all_status == "open" or not week_is_visible:
            await bounded(
                show_all.click(timeout=7000),
                PROTOCOL_TIMEOUT_SECONDS,
                "opening all CNU course weeks",
            )

    title_selector = f'{row_selector} [data-act="titleDetailContents"]'
    displayed_selector = f"{week_container_selector}, {row_selector}, {title_selector}"
    try:
        await bounded(
            page.wait_for_selector(displayed_selector, state="visible", timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for the selected CNU week to become visible",
        )
    except Exception:
        raise _PlaybackFailure(
            code="lecture-row-unavailable",
            message="The requested lecture's course week did not become visible.",
        ) from None

    selectors = await bounded(
        page.evaluate(COLLAPSED_TOGGLES_JS, row_id),
        PROTOCOL_TIMEOUT_SECONDS,
        "finding CNU lecture accordion controls",
    )
    if isinstance(selectors, list):
        for selector in selectors:
            if not isinstance(selector, str) or not selector:
                continue
            toggle = page.locator(selector).first
            if await bounded(toggle.is_visible(), PROTOCOL_TIMEOUT_SECONDS, "checking a CNU lecture accordion"):
                await bounded(
                    toggle.click(timeout=7000),
                    PROTOCOL_TIMEOUT_SECONDS,
                    "expanding the CNU lecture accordion",
                )

    try:
        await bounded(
            page.wait_for_selector(title_selector, state="visible", timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for the CNU lecture title to become visible",
        )
    except Exception:
        raise _PlaybackFailure(
            code="lecture-row-unavailable",
            message="The requested lecture remained hidden after opening its course week.",
        ) from None


async def _close_player(page: Any) -> None:
    close_button = await _visible_locator(page, PLAYER_CLOSE_SELECTOR, "finding the player close button")
    if close_button is None:
        raise _PlaybackFailure()
    await bounded(close_button.click(timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "closing the lecture player")


def _reject_item_speed(handler: MediaHandler | None, speed: float) -> None:
    if handler is None or handler.item_speed_rejected is None:
        return
    rejected = handler.item_speed_rejected(speed)
    if rejected is not None:
        raise rejected


# Ordered preview lifetime: one modal open and close, with the existing cleanup-error precedence.
async def _play_visible_lecture(
    page: Any,
    row_id: str,
    speed: float,
    *,
    media: object = MediaKind.VIDEO,
    replay: bool = False,
    on_opened: Callable[[], None] | None = None,
    on_started: Callable[[], None] | None = None,
    on_position: Callable[[float, float], None] | None = None,
    diagnostic: _PlayDiagnostic | None = None,
) -> tuple[float, str]:
    diagnostic = diagnostic if diagnostic is not None else _PlayDiagnostic()
    started_at: float | None = None
    modal_open = False
    primary_error: BaseException | None = None
    try:
        found = MEDIA_HANDLERS.get(media)
        _reject_item_speed(found, speed)
        diagnostic.stage = "row"
        await _expand_row_through_ui(page, row_id)
        diagnostic.stage = "preview"
        row_selector = f"[id={_css_string(row_id)}] [data-act='titleDetailContents']"
        title = page.locator(row_selector)
        await bounded(
            title.scroll_into_view_if_needed(timeout=7000),
            PROTOCOL_TIMEOUT_SECONDS,
            "scrolling to the lecture",
        )
        await bounded(title.click(timeout=7000), PROTOCOL_TIMEOUT_SECONDS, "opening the lecture preview")
        modal_open = True
        await bounded(
            page.wait_for_selector(PREVIEW_MODAL_SELECTOR, timeout=15000),
            16.0,
            "waiting for the lecture preview",
        )
        diagnostic.stage = "frame"

        handler = found if found is not None and found.play is not None else MEDIA_HANDLERS[MediaKind.VIDEO]

        def on_media_started(measured_at: float | None = None) -> None:
            nonlocal started_at
            # Panopto passes the clock value it just read. YouTube passes nothing, so this
            # still reads the clock, which is where its wrapped elapsed came from.
            started_at = measured_at if measured_at is not None else current_clock().monotonic()
            if on_started is not None:
                on_started()

        return await handler.play(
            page,
            diagnostic,
            speed=speed,
            replay=replay,
            on_opened=on_opened,
            on_started=on_media_started,
            on_position=on_position,
        )
    except _PlaybackFailure as error:
        primary_error = error
        raise
    except Exception:
        elapsed = current_clock().monotonic() - started_at if started_at is not None else 0.0
        primary_error = _PlaybackFailure(elapsed)
        raise primary_error from None
    except BaseException as error:
        primary_error = error
        raise
    finally:
        with suppress(BaseException):
            await diagnostic.refresh()
        if primary_error is None:
            diagnostic.stage = "close"
        if modal_open:
            try:
                await _close_player(page)
            except asyncio.CancelledError:
                if primary_error is None:
                    raise
            except BaseException:
                if primary_error is None:
                    elapsed = current_clock().monotonic() - started_at if started_at is not None else 0.0
                    raise _PlaybackFailure(elapsed) from None
