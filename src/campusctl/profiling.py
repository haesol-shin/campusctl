"""Opt-in, identifier-free timing for a single sync invocation.

The recorder is deliberately not wired to argument parsing or browser lifetime. Callers
own the recorder, enter spans around work, and call finish once in a finally block.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter_ns
from typing import TextIO

PHASES = frozenset(
    {
        "lock",
        "playwright",
        "launch-connect",
        "user-agent",
        "auth",
        "roster",
        "todo",
        "course-selection",
        "document-commit",
        "response-completion",
        "guard-headers",
        "guard-decision",
        "guard-disposition",
        "idle",
        "dom-ready",
        "extract",
        "archive-page",
        "modal",
        "attachment-list",
        "archive-restore",
        "merge",
        "serialize-write",
        "teardown",
    }
)
DOMAINS = frozenset({"lectures", "assignments", "notices", "materials"})
ROUTES = frozenset({"document", "xhr", "fetch", "static", "attachment", "other"})
DISPOSITIONS = frozenset({"allowed", "blocked", "suppressed"})
COUNT_NAMES = frozenset({"course_selections", "documents", "requests"})


@dataclass
class _Span:
    phase: str
    domain: str | None
    course: int | None
    window: int | None
    parent: int | None
    start: int
    end: int | None = None
    failed: bool = False


def _union_ns(intervals: list[tuple[int, int]]) -> int:
    total = 0
    right = -1
    for start, end in sorted(intervals):
        if end > right:
            total += end - max(start, right)
            right = end
    return total


def _subtract_ns(start: int, end: int, blocked: list[tuple[int, int]]) -> list[tuple[int, int]]:
    remaining = []
    cursor = start
    for left, right in sorted(blocked):
        if left > cursor:
            remaining.append((cursor, min(left, end)))
        cursor = max(cursor, right)
    if cursor < end:
        remaining.append((cursor, end))
    return [(left, right) for left, right in remaining if right > left]


class SpanRecorder:
    """Bounded per-run spans. Integer course/window labels are ephemeral ordinals only.

    ``enabled=False`` does not read the clock or allocate event buffers. ``finish``
    closes active spans as failed, returns the safe schema, and writes exactly one
    prefixed stderr line when enabled. It is idempotent.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        mode: str = "headed",
        scope: tuple[str, ...] = (),
        run: int = 1,
        max_events: int = 4096,
        clock=perf_counter_ns,
    ) -> None:
        if mode not in {"headed", "headless"} or any(domain not in DOMAINS for domain in scope):
            raise ValueError("invalid mode or scope")
        if type(run) is not int or run < 1 or max_events < 1:
            raise ValueError("invalid recorder bounds")
        self.enabled = enabled
        self.mode = mode
        self.scope = tuple(scope)
        self.run = run
        self.max_events = max_events
        self._clock = clock
        self._started = clock() if enabled else 0
        self._spans: list[_Span] = []
        self._parent: ContextVar[int | None] = ContextVar("profile_parent", default=None)
        self._counts = dict.fromkeys(sorted(COUNT_NAMES), 0)
        self._routes: dict[str, int] = {}
        self._dropped = 0
        self._event_loop_lag_ns: int | None = None
        self._finished: dict | None = None

    @contextmanager
    def span(
        self, phase: str, *, domain: str | None = None, course: int | None = None, window: int | None = None
    ) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        if phase not in PHASES or (domain is not None and domain not in DOMAINS):
            raise ValueError("unknown span category")
        if any(type(value) is not int or value < 1 for value in (course, window) if value is not None):
            raise ValueError("course and window must be positive ephemeral ordinals")
        if self._finished is not None:
            raise RuntimeError("recorder finished")
        if len(self._spans) >= self.max_events:
            self._dropped += 1
            yield
            return
        parent = self._parent.get()
        index = len(self._spans)
        record = _Span(phase, domain, course, window, parent, self._clock())
        self._spans.append(record)
        token = self._parent.set(index)
        try:
            yield
        except BaseException:
            record.failed = True
            raise
        finally:
            if record.end is None:
                record.end = self._clock()
            self._parent.reset(token)

    def count(self, name: str, amount: int = 1) -> None:
        if not self.enabled:
            return
        if name not in COUNT_NAMES or type(amount) is not int or amount < 0:
            raise ValueError("unknown count or invalid increment")
        self._counts[name] += amount

    def request(self, route: str, disposition: str) -> None:
        if not self.enabled:
            return
        if route not in ROUTES or disposition not in DISPOSITIONS:
            raise ValueError("unknown route or disposition")
        self.count("requests")
        key = f"{route}:{disposition}"
        self._routes[key] = self._routes.get(key, 0) + 1

    def observe_event_loop_lag(self, lag_ns: int) -> None:
        """Record the largest observed scheduler delay from an external loop probe."""
        if not self.enabled:
            return
        if type(lag_ns) is not int or lag_ns < 0:
            raise ValueError("event loop lag must be nonnegative nanoseconds")
        self._event_loop_lag_ns = max(self._event_loop_lag_ns or 0, lag_ns)

    def finish(self, *, outcome: str = "ok", stderr: TextIO | None = None) -> dict | None:
        if not self.enabled:
            return None
        if outcome not in {"ok", "failed"}:
            raise ValueError("unknown outcome")
        if self._finished is not None:
            return self._finished
        end = self._clock()
        children: dict[int, list[tuple[int, int]]] = {}
        for span in self._spans:
            if span.end is None:
                span.end = end
                span.failed = True
            if span.failed:
                outcome = "failed"
            if span.parent is not None:
                parent = self._spans[span.parent]
                assert parent.end is not None
                children.setdefault(span.parent, []).append((max(parent.start, span.start), min(parent.end, span.end)))
        aggregates: dict[tuple[str, str | None, int | None, int | None, bool], dict] = {}
        intervals: dict[tuple[str, str | None, int | None, int | None, bool], tuple[list, list]] = {}
        for index, span in enumerate(self._spans):
            assert span.end is not None
            key = (span.phase, span.domain, span.course, span.window, span.failed)
            entry = aggregates.setdefault(
                key,
                {
                    "phase": span.phase,
                    "domain": span.domain,
                    "course": span.course,
                    "window": span.window,
                    "failed": span.failed,
                    "count": 0,
                    "inclusive_ns": 0,
                    "exclusive_ns": 0,
                },
            )
            entry["count"] += 1
            inclusive, exclusive = intervals.setdefault(key, ([], []))
            inclusive.append((span.start, span.end))
            exclusive.extend(_subtract_ns(span.start, span.end, children.get(index, [])))
        for key, entry in aggregates.items():
            inclusive, exclusive = intervals[key]
            entry["inclusive_ns"] = _union_ns(inclusive)
            entry["exclusive_ns"] = _union_ns(exclusive)
        self._finished = {
            "schema_version": 1,
            "run": self.run,
            "mode": self.mode,
            "scope": list(self.scope),
            "outcome": outcome,
            "wall_ns": end - self._started,
            "spans": list(aggregates.values()),
            "counts": self._counts.copy(),
            "routes": self._routes.copy(),
            "dropped_events": self._dropped,
            "event_loop_lag_ns": self._event_loop_lag_ns,
        }
        print("campusctl-profile: " + json.dumps(self._finished, separators=(",", ":")), file=stderr or sys.stderr)
        return self._finished
