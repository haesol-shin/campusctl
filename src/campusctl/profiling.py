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
        "sso-settle",
        "course-selection",
        "document-commit",
        "response-completion",
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
        "wait",
        "page-readiness",
    }
)
DOMAINS = frozenset({"lectures", "assignments", "notices", "materials"})
COUNT_NAMES = frozenset({"course_selections", "documents", "sso_settles"})
PAGE_KINDS = frozenset(
    {
        "login",
        "todo",
        "roster",
        "course-entry",
        "lecture",
        "assignments",
        "notices",
        "archive",
        "assignment-detail",
        "notice-detail",
        "other",
    }
)
WAIT_KINDS = frozenset({"selector", "function", "load", "timeout", "action", "response", "navigation", "readiness"})
END_REASONS = frozenset({"next-document", "session-end", "failed"})
SCHEMA_VERSION = 2
_SPAN_KEY = (
    "phase",
    "domain",
    "course",
    "window",
    "page_kind",
    "wait_kind",
    "document",
    "failed",
    "count",
    "inclusive_ns",
    "exclusive_ns",
)
_DOCUMENT_KEY = (
    "document",
    "page_kind",
    "domain",
    "course",
    "start_ns",
    "commit_ns",
    "end_ns",
    "end_reason",
)


@dataclass
class _Span:
    phase: str
    domain: str | None
    course: int | None
    window: int | None
    page_kind: str | None
    wait_kind: str | None
    document: int | None
    parent: int | None
    start: int
    end: int | None = None
    failed: bool = False


@dataclass
class _Document:
    ordinal: int
    page_kind: str
    domain: str | None
    course: int | None
    start: int
    commit: int | None = None
    end: int | None = None
    end_reason: str | None = None


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


def _clip_ns(intervals: list[tuple[int, int]], windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    clipped = []
    for start, end in intervals:
        for left, right in windows:
            bound_left = max(start, left)
            bound_right = min(end, right)
            if bound_right > bound_left:
                clipped.append((bound_left, bound_right))
    return clipped


def _ordinal(value: object, label: str) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{label} must be a positive ephemeral ordinal")


def _optional_enum(value: object, allowed: frozenset[str], label: str) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"unknown {label}")


class SpanRecorder:
    """Bounded per-run spans. Integer course/window/document labels are ephemeral ordinals only.

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
        max_documents: int = 4096,
        clock=perf_counter_ns,
    ) -> None:
        if mode not in {"headed", "headless"} or any(domain not in DOMAINS for domain in scope):
            raise ValueError("invalid mode or scope")
        if type(run) is not int or run < 1 or max_events < 1 or max_documents < 1:
            raise ValueError("invalid recorder bounds")
        self.enabled = enabled
        self.mode = mode
        self.scope = tuple(scope)
        self.run = run
        self.max_events = max_events
        self.max_documents = max_documents
        self._clock = clock
        self._started = clock() if enabled else 0
        self._spans: list[_Span] = []
        self._documents: list[_Document] = []
        self._active_document: int | None = None
        self._parent: ContextVar[int | None] = ContextVar("profile_parent", default=None)
        self._counts = dict.fromkeys(sorted(COUNT_NAMES), 0)
        self._dropped = 0
        self._event_loop_lag_ns: int | None = None
        self._finished: dict | None = None

    def _relative(self, instant: int | None = None) -> int:
        return (self._clock() if instant is None else instant) - self._started

    @contextmanager
    def span(
        self,
        phase: str,
        *,
        domain: str | None = None,
        course: int | None = None,
        window: int | None = None,
        page_kind: str | None = None,
        wait_kind: str | None = None,
        document: int | None = None,
    ) -> Iterator[None]:
        if phase not in PHASES or (domain is not None and domain not in DOMAINS):
            raise ValueError("unknown span category")
        _optional_enum(page_kind, PAGE_KINDS, "page kind")
        _optional_enum(wait_kind, WAIT_KINDS, "wait kind")
        _ordinal(course, "course")
        _ordinal(window, "window")
        _ordinal(document, "document")
        if not self.enabled:
            yield
            return
        if self._finished is not None:
            raise RuntimeError("recorder finished")
        if len(self._spans) >= self.max_events:
            self._dropped += 1
            yield
            return
        parent = self._parent.get()
        index = len(self._spans)
        record = _Span(
            phase,
            domain,
            course,
            window,
            page_kind,
            wait_kind,
            document,
            parent,
            self._clock(),
        )
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

    def open_document(self, *, page_kind: str, domain: str | None = None, course: int | None = None) -> int | None:
        """Start a run-local document row and close the previous row at this instant."""
        if page_kind not in PAGE_KINDS or (domain is not None and domain not in DOMAINS):
            raise ValueError("unknown document category")
        _ordinal(course, "course")
        if not self.enabled:
            return None
        if self._finished is not None:
            raise RuntimeError("recorder finished")
        now = self._relative()
        self._close_active(now, "next-document")
        if len(self._documents) >= self.max_documents:
            self._dropped += 1
            return None
        ordinal = len(self._documents) + 1
        self._documents.append(_Document(ordinal, page_kind, domain, course, now))
        self._active_document = ordinal
        return ordinal

    def commit_document(self, ordinal: int) -> None:
        """Attach commit time only while this document row is still open."""
        _ordinal(ordinal, "document")
        if not self.enabled:
            return
        row = self._row(ordinal)
        if row is None or row.end is not None or row.commit is not None:
            return
        row.commit = self._relative()
        self._counts["documents"] += 1

    def fail_document(self, ordinal: int) -> None:
        """Close an uncommitted navigation without treating it as a completed dwell."""
        _ordinal(ordinal, "document")
        if not self.enabled:
            return
        row = self._row(ordinal)
        if row is None or row.end is not None:
            return
        row.end = self._relative()
        row.end_reason = "failed"
        if self._active_document == ordinal:
            self._active_document = None

    def close_documents(self) -> None:
        """Censor the final open interval at session teardown. Does not read the clock if idle."""
        if not self.enabled or self._active_document is None:
            return
        self._close_active(self._relative(), "session-end")

    def _row(self, ordinal: int) -> _Document | None:
        if ordinal < 1 or ordinal > len(self._documents):
            return None
        return self._documents[ordinal - 1]

    def _close_active(self, end: int, reason: str) -> None:
        ordinal = self._active_document
        if ordinal is None:
            return
        row = self._documents[ordinal - 1]
        self._active_document = None
        if row.end is None:
            row.end = end
            row.end_reason = reason

    def count(self, name: str, amount: int = 1) -> None:
        if not self.enabled:
            return
        if name not in COUNT_NAMES or type(amount) is not int or amount < 0:
            raise ValueError("unknown count or invalid increment")
        self._counts[name] += amount

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
        self._close_active(end - self._started, "session-end")
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
        aggregates: dict[tuple, dict] = {}
        intervals: dict[tuple, tuple[list, list]] = {}
        for index, span in enumerate(self._spans):
            assert span.end is not None
            key = (
                span.phase,
                span.domain,
                span.course,
                span.window,
                span.page_kind,
                span.wait_kind,
                span.document,
                span.failed,
            )
            entry = aggregates.setdefault(
                key,
                {
                    "phase": span.phase,
                    "domain": span.domain,
                    "course": span.course,
                    "window": span.window,
                    "page_kind": span.page_kind,
                    "wait_kind": span.wait_kind,
                    "document": span.document,
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
        lock_intervals = [
            (span.start, span.end) for span in self._spans if span.phase == "lock" and span.end is not None
        ]
        other_intervals = [
            (span.start, span.end) for span in self._spans if span.phase != "lock" and span.end is not None
        ]
        lock_ns = _union_ns(lock_intervals)
        covered_ns = _union_ns(_clip_ns(other_intervals, lock_intervals))
        documents = [
            {
                "document": row.ordinal,
                "page_kind": row.page_kind,
                "domain": row.domain,
                "course": row.course,
                "start_ns": row.start,
                "commit_ns": row.commit,
                "end_ns": row.end,
                "end_reason": row.end_reason,
            }
            for row in self._documents
        ]
        self._finished = {
            "schema_version": SCHEMA_VERSION,
            "run": self.run,
            "mode": self.mode,
            "scope": list(self.scope),
            "outcome": outcome,
            "wall_ns": end - self._started,
            "spans": list(aggregates.values()),
            "documents": documents,
            "coverage": {
                "lock_ns": lock_ns,
                "covered_ns": covered_ns,
                "unattributed_ns": lock_ns - covered_ns,
            },
            "counts": self._counts.copy(),
            "dropped_events": self._dropped,
            "event_loop_lag_ns": self._event_loop_lag_ns,
        }
        print("campusctl-profile: " + json.dumps(self._finished, separators=(",", ":")), file=stderr or sys.stderr)
        return self._finished


def attribute_profile(profile: dict) -> dict:
    """Summarize waits and document time. Session-end rows stay censored, not completed dwell."""
    waits = [
        {
            "phase": span["phase"],
            "domain": span["domain"],
            "page_kind": span["page_kind"],
            "course": span["course"],
            "wait_kind": span["wait_kind"],
            "count": span["count"],
            "inclusive_ns": span["inclusive_ns"],
            "exclusive_ns": span["exclusive_ns"],
        }
        for span in profile["spans"]
        if span.get("wait_kind") is not None
        or span["phase"]
        in {"idle", "dom-ready", "wait", "page-readiness", "response-completion", "document-commit", "sso-settle"}
    ]
    dwell: dict[str, int] = {}
    latency: dict[str, int] = {}
    censored = []
    for row in profile["documents"]:
        if row["commit_ns"] is not None:
            latency[row["page_kind"]] = latency.get(row["page_kind"], 0) + row["commit_ns"] - row["start_ns"]
        if row["end_reason"] == "next-document":
            dwell[row["page_kind"]] = dwell.get(row["page_kind"], 0) + row["end_ns"] - row["start_ns"]
        elif row["end_reason"] == "session-end":
            censored.append(
                {
                    "document": row["document"],
                    "page_kind": row["page_kind"],
                    "start_ns": row["start_ns"],
                    "commit_ns": row["commit_ns"],
                    "end_ns": row["end_ns"],
                }
            )
    return {
        "waits": waits,
        "coverage": dict(profile["coverage"]),
        "dropped_events": profile["dropped_events"],
        "document_dwell_ns": dwell,
        "commit_latency_ns": latency,
        "censored": censored,
    }
