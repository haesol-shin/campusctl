"""Best-effort, private, structural evidence for failed roster discovery."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

_ID = re.compile(r"^(?:[0-9]+|[0-9a-fA-F]{16,}|[A-Za-z0-9_+=-]{16,})$")
_STATIC_SEGMENTS = frozenset(
    {
        "api",
        "archive",
        "course",
        "download",
        "lecture",
        "list",
        "materials",
        "myLecture",
        "notice",
        "roster",
        "std",
        "task",
        "todo",
        "v1",
    }
)
_SAFE_OPERATION = re.compile(r"[^a-z0-9-]")
_LIMIT = 12


def _location(url: str) -> dict[str, str]:
    try:
        from .login import MY_LECTURE_URL

        parts = urlsplit(url)
        host = (
            "blank" if not parts.hostname else "lms" if parts.hostname == urlsplit(MY_LECTURE_URL).hostname else "other"
        )
        path = "/" + "/".join(
            segment if segment in _STATIC_SEGMENTS and not _ID.fullmatch(segment) else ":id"
            for segment in (unquote(item) for item in parts.path.split("/"))
            if segment
        )
        return {"host_class": host, "path": path}
    except Exception:
        return {"host_class": "other", "path": "/"}


class RosterRequests:
    """Track only the roster window, without fetching bodies or modifying routes."""

    def __init__(self, page: Any, *, headless: bool) -> None:
        self.page = page
        self.headless = headless
        self.step = "goto"
        self.started = time.monotonic()
        self.pending: dict[Any, float] = {}
        self.failed: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self._installed = False

    @property
    def elapsed_s(self) -> float:
        return round(time.monotonic() - self.started, 3)

    def start(self) -> None:
        if isinstance(getattr(self.page, "listeners", None), dict):
            # Single-callback test doubles cannot support another observer.
            return
        self._installed = True
        try:
            self.page.on("request", self._request)
            self.page.on("requestfinished", self._finished)
            self.page.on("requestfailed", self._failed)
            self.page.on("response", self._response)
        except Exception:
            self.close()

    def close(self) -> None:
        if not self._installed:
            return
        for event, callback in (
            ("request", self._request),
            ("requestfinished", self._finished),
            ("requestfailed", self._failed),
            ("response", self._response),
        ):
            with contextlib.suppress(Exception):
                self.page.remove_listener(event, callback)
        self._installed = False

    def _request(self, request: Any) -> None:
        try:
            if request.resource_type in {"xhr", "fetch", "document"}:
                self.pending[request] = time.monotonic()
        except Exception:
            pass

    def _finished(self, request: Any) -> None:
        with contextlib.suppress(Exception):
            self.pending.pop(request, None)

    def _failed(self, request: Any) -> None:
        self._finished(request)
        try:
            if len(self.failed) < _LIMIT:
                failure = request.failure or "request failed"
                # Chromium error codes are useful; arbitrary failure strings can contain secrets.
                safe_failure = failure if re.fullmatch(r"net::ERR_[A-Z_]+", failure) else "request failed"
                self.failed.append(
                    {"resource_type": request.resource_type, **_location(request.url), "failure": safe_failure}
                )
        except Exception:
            pass

    def _response(self, response: Any) -> None:
        try:
            if response.status >= 400 and len(self.responses) < _LIMIT:
                self.responses.append(
                    {
                        "status": response.status,
                        "resource_type": response.request.resource_type,
                        **_location(response.url),
                    }
                )
        except Exception:
            pass

    def in_flight(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {"resource_type": request.resource_type, **_location(request.url), "age_s": round(now - started, 3)}
            for request, started in islice(self.pending.items(), _LIMIT)
        ]


# A page's current roster window is registered only while its listeners are active.
_WINDOWS: dict[Any, RosterRequests] = {}


def start_roster_requests(page: Any, *, headless: bool) -> RosterRequests:
    previous = _WINDOWS.pop(page, None)
    if previous:
        previous.close()
    window = RosterRequests(page, headless=headless)
    _WINDOWS[page] = window
    window.start()
    return window


def current_roster_requests(page: Any) -> RosterRequests | None:
    return _WINDOWS.get(page)


def stop_roster_requests(page: Any) -> None:
    window = _WINDOWS.pop(page, None)
    if window:
        window.close()


def _memory() -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, _, value = line.partition(":")
            if name in {"MemAvailable", "SwapTotal", "SwapFree"}:
                result[name] = int(value.strip().split()[0])  # kB, as reported by procfs
    except (OSError, ValueError, IndexError):
        pass
    return result


_STRUCTURE_JS = """() => {
    const visible = el => { const s = getComputedStyle(el); return s.display !== 'none' &&
        s.visibility !== 'hidden' && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length); };
    const acts = [...new Set([...document.querySelectorAll('[data-act]')].map(e => e.getAttribute('data-act')))];
    return {ready_state: document.readyState, password_inputs: document.querySelectorAll('input[type=password]').length,
        course_links: document.querySelectorAll('[data-act="moveLecture"]').length,
        total_nodes: document.getElementsByTagName('*').length,
        data_acts: acts.slice(0, 32),
        visible_loading: [...document.querySelectorAll(
            '[class*=loading], [id*=loading], [class*=spinner], [role=progressbar], [aria-busy=true]'
        )].some(visible),
        visible_modals: [...document.querySelectorAll('[role=dialog], dialog, .modal')].filter(visible).length,
        iframes: document.querySelectorAll('iframe').length};
}"""


async def _collect(page: Any, operation: str, step: str, elapsed_s: float) -> dict[str, Any]:
    window = _WINDOWS.get(page)
    record: dict[str, Any] = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "operation": operation,
        "step": step,
        "elapsed_s": round(elapsed_s, 3),
        "headless": window.headless if window else None,
        "page": _location(page.url),
        "other_pages": [],
        "in_flight": window.in_flight() if window else [],
        "failed_requests": list(window.failed) if window else [],
        "error_responses": list(window.responses) if window else [],
    }
    try:
        structure = await page.evaluate(_STRUCTURE_JS)
        structure["data_acts"] = [act if act == "moveLecture" else "other" for act in structure["data_acts"][:_LIMIT]]
        record.update(structure)
    except Exception:
        pass
    with contextlib.suppress(Exception):
        record["other_pages"] = [_location(other.url) for other in page.context.pages if other != page][:_LIMIT]
    memory = _memory()
    if memory:
        record["memory_kb"] = memory
    return record


async def capture_roster_failure(page: Any, *, operation: str, step: str, elapsed_s: float, root: Path) -> Path | None:
    """Write one private JSON snapshot, at most three seconds of effort; never raise."""
    try:
        record = await asyncio.wait_for(_collect(page, operation, step, elapsed_s), timeout=2.5)
        directory = root / "diagnostics"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        name = _SAFE_OPERATION.sub("-", operation.lower())
        target = directory / f"roster-{stamp}-{name}.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(record, output, separators=(",", ":"), sort_keys=True)
            output.write("\n")
        for old in sorted(directory.glob("roster-*.json"), reverse=True)[20:]:
            old.unlink()
        print(f"Roster diagnostic saved to {target}", file=sys.stderr)
        return target
    except Exception:
        return None
