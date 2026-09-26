import io
import json

import pytest

from campusctl.profiling import SpanRecorder


class Clock:
    def __init__(self):
        self.now = 0
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now


def test_disabled_recorder_never_touches_clock_or_emits():
    clock = Clock()
    recorder = SpanRecorder(clock=clock)
    with recorder.span("extract"):
        recorder.count("documents")
    assert recorder.finish(stderr=io.StringIO()) is None
    assert clock.calls == 0


def test_overlapping_children_exclusive_uses_interval_union():
    clock = Clock()
    recorder = SpanRecorder(enabled=True, scope=("notices",), clock=clock)
    sink = io.StringIO()
    with recorder.span("roster"):
        clock.now = 2
        with recorder.span("extract", domain="notices", course=1):
            clock.now = 6
        # Simulate a second asynchronously overlapping child of the root.
        clock.now = 4
        with recorder.span("extract", domain="notices", course=1):
            clock.now = 8
        clock.now = 10
    recorder.observe_event_loop_lag(8)
    recorder.observe_event_loop_lag(3)
    payload = recorder.finish(stderr=sink)
    root = next(s for s in payload["spans"] if s["phase"] == "roster")
    children = next(s for s in payload["spans"] if s["phase"] == "extract")
    assert root["inclusive_ns"] == 10
    assert root["exclusive_ns"] == 4  # union of [2,6] and [4,8]
    assert children["count"] == 2
    assert children["inclusive_ns"] == 8
    assert payload["event_loop_lag_ns"] == 8
    assert json.loads(sink.getvalue().removeprefix("campusctl-profile: ")) == payload
    assert recorder.finish(stderr=sink) is payload
    assert sink.getvalue().count("campusctl-profile:") == 1


def test_failure_flush_closes_active_spans_and_never_serializes_error():
    clock = Clock()
    recorder = SpanRecorder(enabled=True, clock=clock)
    sink = io.StringIO()
    with pytest.raises(RuntimeError, match="secret.invalid"), recorder.span("auth"):
        clock.now = 7
        raise RuntimeError("secret.invalid")
    result = recorder.finish(outcome="failed", stderr=sink)
    assert result["outcome"] == "failed"
    assert result["spans"][0]["failed"] is True
    assert "secret.invalid" not in sink.getvalue()


def test_bounded_and_untrusted_categories_rejected():
    recorder = SpanRecorder(enabled=True, max_events=1, clock=Clock())
    with recorder.span("lock"), recorder.span("auth"):
        pass
    recorder.request("document", "blocked")
    recorder.count("course_selections")
    with pytest.raises(ValueError), recorder.span("secret.invalid"):
        pass
    with pytest.raises(ValueError), recorder.span("extract", course="private-id"):
        pass
    with pytest.raises(ValueError):
        recorder.request("https://secret.invalid", "allowed")
    result = recorder.finish(stderr=io.StringIO())
    assert result["dropped_events"] == 1
    assert result["counts"]["requests"] == 1
    assert result["routes"] == {"document:blocked": 1}
