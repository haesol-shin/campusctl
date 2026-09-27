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
    assert children["inclusive_ns"] == 6
    assert children["exclusive_ns"] == 6
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
    recorder.count("course_selections")
    with pytest.raises(ValueError), recorder.span("secret.invalid"):
        pass
    with pytest.raises(ValueError), recorder.span("extract", course="private-id"):
        pass
    result = recorder.finish(stderr=io.StringIO())
    assert result["dropped_events"] == 1
    assert result["counts"]["course_selections"] == 1


def test_wait_coverage_cancellation_and_caps_use_exact_durations():
    clock = Clock()
    recorder = SpanRecorder(enabled=True, max_events=4, max_documents=1, clock=clock)
    sink = io.StringIO()
    with recorder.span("lock"):
        clock.now = 10
        with recorder.span("idle", wait_kind="load", page_kind="archive"):
            clock.now = 40
        clock.now = 20
        with recorder.span("wait", wait_kind="function", page_kind="roster"):
            clock.now = 50
        clock.now = 80
    with pytest.raises(TimeoutError), recorder.span("wait", wait_kind="timeout"):
        clock.now = 90
        raise TimeoutError
    with pytest.raises(ValueError):
        recorder.span("wait", wait_kind="secret.invalid").__enter__()
    assert recorder.open_document(page_kind="roster") == 1
    clock.now = 95
    recorder.commit_document(1)
    clock.now = 100
    assert recorder.open_document(page_kind="todo") is None
    clock.now = 110
    payload = recorder.finish(stderr=sink)
    assert payload["schema_version"] == 2
    assert payload["coverage"] == {"lock_ns": 80, "covered_ns": 40, "unattributed_ns": 40}
    idle = next(span for span in payload["spans"] if span["phase"] == "idle")
    assert idle["inclusive_ns"] == 30 and idle["exclusive_ns"] == 30
    assert idle["page_kind"] == "archive" and idle["wait_kind"] == "load"
    cancelled = next(span for span in payload["spans"] if span["failed"])
    assert cancelled["inclusive_ns"] == 10
    assert payload["dropped_events"] == 1
    assert payload["documents"][0]["end_reason"] == "next-document"
    assert payload["documents"][0]["commit_ns"] == 95
    assert payload["documents"][0]["end_ns"] == 100
    assert payload["counts"]["documents"] == 1
    assert "secret.invalid" not in sink.getvalue()
    assert recorder.finish(stderr=sink) is payload
    assert sink.getvalue().count("campusctl-profile:") == 1


def test_disabled_document_methods_do_not_read_clock():
    clock = Clock()
    recorder = SpanRecorder(clock=clock)
    assert recorder.open_document(page_kind="roster") is None
    recorder.commit_document(1)
    recorder.fail_document(1)
    recorder.close_documents()
    with recorder.span("wait", page_kind="login", wait_kind="selector", document=1):
        recorder.count("documents")
    assert recorder.finish(stderr=io.StringIO()) is None
    assert clock.calls == 0


def test_failed_check_is_bounded_and_contains_only_allowlisted_evidence():
    clock = Clock()
    recorder = SpanRecorder(enabled=True, max_events=1, clock=clock)
    started = recorder.check_start()
    clock.now = 7
    recorder.diagnostic(
        "archive-state",
        started=started,
        bound_ns=10,
        domain="materials",
        course=2,
        page_kind="archive",
        counts={"rendered_rows": 1, "expected_rows": 3, "tot_cnt": 3},
        states={"ids_match": False, "completed": True},
    )
    with pytest.raises(ValueError):
        recorder.diagnostic("archive-state", started=started, bound_ns=10, counts={"course_id": 1})
    with pytest.raises(ValueError):
        recorder.diagnostic("archive-state", started=started, bound_ns=10, states={"ids_match": "secret.invalid"})
    recorder.diagnostic("archive-state", started=started, bound_ns=10)
    sink = io.StringIO()
    profile = recorder.finish(stderr=sink)
    assert profile["dropped_events"] == 1
    assert profile["diagnostics"] == [
        {
            "check": "archive-state",
            "outcome": "failed",
            "page_kind": "archive",
            "domain": "materials",
            "course": 2,
            "counts": {"rendered_rows": 1, "expected_rows": 3, "tot_cnt": 3},
            "states": {"ids_match": False, "completed": True},
            "elapsed_ns": 7,
            "bound_ns": 10,
        }
    ]
    assert "secret.invalid" not in sink.getvalue()
    shared = SpanRecorder(enabled=True, max_events=1, clock=Clock())
    with shared.span("extract"):
        shared.diagnostic("todo-grid", started=shared.check_start(), bound_ns=10)
    result = shared.finish(stderr=io.StringIO())
    assert result["diagnostics"] == [] and result["dropped_events"] == 1


def test_passing_check_does_not_emit_and_disabled_check_does_not_read_clock():
    clock = Clock()
    disabled = SpanRecorder(clock=clock)
    assert disabled.check_start() is None
    assert disabled.finish(stderr=io.StringIO()) is None
    assert clock.calls == 0
    recorder = SpanRecorder(enabled=True, clock=clock)
    assert recorder.check_start() == 0
    assert recorder.finish(stderr=io.StringIO())["diagnostics"] == []
