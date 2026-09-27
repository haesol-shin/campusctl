import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from campusctl import browser
from campusctl.browser import (
    _DocumentTimeline,
    _InheritedLabels,
    _SessionLabels,
    profile_context,
    profile_labels,
    profile_span,
)
from campusctl.profiling import SpanRecorder, attribute_profile


class Clock:
    def __init__(self) -> None:
        self.now = 0
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return self.now


class Frame:
    def __init__(self) -> None:
        self.same_document = False
        self.committed_request = None


class Request:
    def __init__(self, url: str, frame: Frame, *, resource_type: str = "document", navigation: bool = True) -> None:
        self.url = url
        self.frame = frame
        self.resource_type = resource_type
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class Page:
    def __init__(self) -> None:
        self.main_frame = Frame()
        self.listeners: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        self.listeners[event].remove(handler)

    def emit(self, event: str, payload) -> None:
        for handler in list(self.listeners.get(event, ())):
            handler(payload)


def _timeline(recorder: SpanRecorder, page: Page, labels: _SessionLabels) -> _DocumentTimeline:
    timeline = _DocumentTimeline(recorder, page, labels)
    timeline.attach()
    return timeline


def _commit(page: Page, request: Request) -> None:
    page.main_frame.same_document = False
    page.main_frame.committed_request = request
    page.emit("framenavigated", page.main_frame)
    page.main_frame.committed_request = None


def test_document_stream_correlates_requests_and_censors_teardown():
    clock = Clock()
    recorder = SpanRecorder(enabled=True, clock=clock)
    labels = _SessionLabels(domain="materials", course=2, page_kind="archive")
    page = Page()
    token = browser._SESSION_LABELS.set(labels)
    stale = browser._INHERITED.set(_InheritedLabels(domain="lectures", course=9, page_kind="login"))
    timeline = _timeline(recorder, page, labels)
    try:
        same = "https://lms.example.invalid/std/archive?course=SECRET"
        first = Request(same, page.main_frame)
        clock.now = 10
        page.emit("request", first)
        clock.now = 15
        page.emit("response", SimpleNamespace(request=first))
        clock.now = 20
        _commit(page, first)
        second = Request(same, page.main_frame)
        clock.now = 30
        page.emit("request", second)
        redirected = Request("https://lms.example.invalid/std/archive?next=SECRET", page.main_frame)
        clock.now = 40
        page.emit("request", redirected)
        clock.now = 45
        page.emit("response", SimpleNamespace(request=second))
        clock.now = 50
        _commit(page, redirected)
        failed = Request("https://lms.example.invalid/std/task?id=SECRET", page.main_frame)
        clock.now = 60
        page.emit("request", failed)
        clock.now = 70
        page.emit("requestfailed", failed)
        page.main_frame.same_document = True
        clock.now = 80
        page.emit("framenavigated", page.main_frame)
        page.emit("request", Request(same, Frame(), resource_type="document"))
        page.emit("request", Request("https://lms.example.invalid/asset.js", page.main_frame, resource_type="script"))
        page.emit("request", Request("about:blank", page.main_frame))
        clock.now = 90
        timeline.close()
        timeline.detach()
        page.emit("request", Request(same, page.main_frame))
    finally:
        browser._INHERITED.reset(stale)
        browser._SESSION_LABELS.reset(token)
    payload = recorder.finish(stderr=io.StringIO())
    serialized = json.dumps(payload)
    assert "SECRET" not in serialized
    assert payload["counts"]["documents"] == 2
    assert [row["end_reason"] for row in payload["documents"]] == [
        "next-document",
        "next-document",
        "next-document",
        "failed",
    ]
    assert [row["commit_ns"] for row in payload["documents"]] == [20, None, 50, None]
    assert payload["documents"][0]["end_ns"] == 30
    assert payload["documents"][-1]["end_ns"] == 70
    assert all(row["domain"] == "materials" and row["course"] == 2 for row in payload["documents"][:3])
    assert payload["documents"][0]["page_kind"] == "archive"
    assert payload["documents"][3]["page_kind"] == "assignments"
    assert page.listeners == {"request": [], "response": [], "requestfailed": [], "framenavigated": []}
    summary = attribute_profile(payload)
    assert summary["censored"] == []
    assert summary["document_dwell_ns"]["archive"] == 50
    assert summary["commit_latency_ns"]["archive"] == 20


def test_session_end_during_navigation_is_censored_and_context_resets():
    clock = Clock()
    recorder = SpanRecorder(enabled=True, clock=clock)
    labels = _SessionLabels()
    page = Page()
    token = browser._SESSION_LABELS.set(labels)
    timeline = _timeline(recorder, page, labels)
    try:
        with profile_context(recorder), profile_labels(domain="notices", course=1, page_kind="notices"):
            clock.now = 5
            page.emit("request", Request("https://lms.example.invalid/std/notice", page.main_frame))
            with profile_span("idle", wait_kind="load"):
                clock.now = 8
            with pytest.raises(RuntimeError), profile_labels(course=4, page_kind="login"):
                raise RuntimeError("reset")
            with profile_span("extract"):
                clock.now = 9
        clock.now = 12
        timeline.close()
    finally:
        timeline.detach()
        browser._SESSION_LABELS.reset(token)
    payload = recorder.finish(stderr=io.StringIO())
    assert payload["documents"][0]["end_reason"] == "session-end"
    assert payload["documents"][0]["commit_ns"] is None
    assert payload["documents"][0]["domain"] == "notices"
    extract = next(span for span in payload["spans"] if span["phase"] == "extract")
    assert extract["course"] == 1 and extract["page_kind"] == "notices"
    assert attribute_profile(payload)["censored"][0]["document"] == 1
    assert "session-end" not in attribute_profile(payload)["document_dwell_ns"]


def test_disabled_session_installs_no_document_listeners(tmp_path, monkeypatch):
    from test_browser import install_fake_playwright

    monkeypatch.setenv("DISPLAY", ":99")
    executable = tmp_path / "chromium"
    executable.touch()
    install_fake_playwright(monkeypatch, __import__("test_browser", fromlist=["FakeChromium"]).FakeChromium())
    recorder = SpanRecorder(enabled=False)
    clock_reads = []

    async def scenario() -> None:
        with profile_context(recorder):
            async with browser.open_session(
                {"browser": {"executable_path": str(executable)}}, data_dir=tmp_path, headless=True
            ) as session:
                clock_reads.append(session.page.listeners)

    asyncio.run(scenario())
    assert clock_reads == [{}]
    assert recorder.finish(stderr=io.StringIO()) is None


def test_profiled_sync_matches_plain_sync_and_attributes_waits(tmp_path, monkeypatch, capsys):
    from test_sync_all import DOMAINS, IDS, _catalogs, _install_fixture, fixture_server

    from campusctl import cli
    from campusctl.catalog import catalog_path, write_catalog
    from campusctl.sync import run_sync

    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        off_root = tmp_path / "off"
        on_root = tmp_path / "on"
        off_result, off_errors = run_sync(config, off_root, DOMAINS, IDS[0], headless=True)
        off_requests = list(server.requests)
        server.requests.clear()
        write_catalog(
            {
                "schema_version": 1,
                "generated_at": "2026-01-01T00:00:00Z",
                "courses": [{"course_id": IDS[0], "label": "Fixture Course 1", "class_no": "01"}],
                "lectures": [],
            },
            catalog_path(on_root),
        )
        monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: config)
        monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(on_root))
        code = cli.main(["--headless", "--profile", "--json", "sync", "--only", ",".join(DOMAINS), "--course", IDS[0]])
        captured = capsys.readouterr()
        on_requests = list(server.requests)
    assert off_errors is None
    assert code == 0, captured.err
    envelope = json.loads(captured.out)
    assert envelope["status"] == "ok"
    assert envelope["errors"] == []
    assert off_result["domains"].keys() == envelope["result"]["domains"].keys()

    def application(requests):
        return [request[:2] for request in requests if request[1] != "/favicon.ico"]

    assert application(off_requests) == application(on_requests)

    def comparable(catalogs):
        return {
            domain: {key: value for key, value in catalog.items() if key not in {"generated_at", "generation_id"}}
            for domain, catalog in catalogs.items()
        }

    assert comparable(_catalogs(off_root)) == comparable(_catalogs(on_root))
    lines = [line for line in captured.err.splitlines() if line.startswith("campusctl-profile: ")]
    assert len(lines) == 1
    profile = json.loads(lines[0].removeprefix("campusctl-profile: "))
    serialized = lines[0]
    assert IDS[0] not in serialized
    assert "SECRET" not in serialized
    assert profile["schema_version"] == 2
    assert profile["dropped_events"] == 0
    assert profile["coverage"]["unattributed_ns"] <= profile["coverage"]["lock_ns"]
    observed = {(span["phase"], span["wait_kind"]) for span in profile["spans"]}
    assert ("idle", "load") in observed
    assert ("document-commit", "navigation") in observed
    assert ("dom-ready", "selector") in observed
    assert ("wait", "function") in observed
    assert ("wait", "action") in observed
    assert ("response-completion", "response") in observed
    assert any(span["phase"] == "archive-restore" for span in profile["spans"])
    assert profile["documents"]
    assert all(row["end_reason"] in {"next-document", "session-end", "failed"} for row in profile["documents"])
    summary = attribute_profile(profile)
    assert summary["waits"]
    assert summary["coverage"] == profile["coverage"]


def test_playwright_redirect_commit_uses_request_identity(tmp_path):
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    if os.name != "nt" and Path("/proc/meminfo").is_file():
        available = 0
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1])
                break
        if available and available < 1_500_000:
            pytest.skip("not enough memory for Chromium")
    pytest.importorskip("playwright.async_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as manager:
        if not Path(manager.chromium.executable_path).is_file():
            pytest.skip("local Playwright Chromium not installed")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/start"):
                self.send_response(302)
                self.send_header("Location", "/std/archive")
                self.end_headers()
                return
            body = b"<!doctype html><title>archive</title><p>ok</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port

    async def scenario() -> dict:
        import threading

        from playwright.async_api import async_playwright

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        recorder = SpanRecorder(enabled=True)
        labels = _SessionLabels(domain="materials", course=1)
        async with async_playwright() as playwright:
            browser_instance = await playwright.chromium.launch(headless=True)
            page = await browser_instance.new_page()
            timeline = _DocumentTimeline(recorder, page, labels)
            timeline.attach()
            try:
                await page.goto(f"http://127.0.0.1:{port}/start?token=SECRET")
            finally:
                timeline.close()
                timeline.detach()
                await browser_instance.close()
        server.shutdown()
        finished = recorder.finish(stderr=io.StringIO())
        assert finished is not None
        return finished

    payload = asyncio.run(scenario())
    serialized = json.dumps(payload)
    assert "SECRET" not in serialized
    assert "127.0.0.1" not in serialized
    committed = [row for row in payload["documents"] if row["commit_ns"] is not None]
    assert len(committed) == 1
    assert committed[0]["page_kind"] == "archive"
    assert payload["counts"]["documents"] == 1
    assert any(row["commit_ns"] is None and row["end_reason"] == "next-document" for row in payload["documents"])


def test_unrelated_task_does_not_record_into_another_profile():
    import contextvars

    recorder = SpanRecorder(enabled=True, scope=("lectures",))
    seen = []

    async def outsider() -> None:
        seen.append(browser.current_profile())
        with profile_span("extract", domain="lectures"):
            pass

    async def scenario() -> None:
        with profile_context(recorder):
            await asyncio.create_task(outsider(), context=contextvars.Context())

    asyncio.run(scenario())
    assert seen == [None]
    profile = recorder.finish(stderr=io.StringIO())
    assert profile["spans"] == []


def test_session_labels_carry_only_that_sessions_recorder():
    owned = SpanRecorder(enabled=True)
    labels = _SessionLabels(recorder=owned)
    token = browser._SESSION_LABELS.set(labels)
    try:
        assert browser.current_profile() is owned
    finally:
        browser._SESSION_LABELS.reset(token)
    assert browser.current_profile() is None


def test_section_settle_uses_domain_labels(monkeypatch):
    from campusctl.providers.cnu import sync_all

    recorder = SpanRecorder(enabled=True, scope=("lectures",))
    page = SimpleNamespace(main_frame=SimpleNamespace(url="https://lms.example.invalid/std/lecture"))

    async def settle(_state: str) -> None:
        return None

    async def click(_selector: str) -> None:
        return None

    async def collect(*_args, **_kwargs):
        return []

    page.wait_for_load_state = settle
    page.click = click
    monkeypatch.setattr(sync_all, "collect_lectures_rows", collect)

    async def scenario() -> None:
        with profile_context(recorder):
            await sync_all._section(
                page,
                2,
                "lectures",
                {"course_id": "course-a", "label": "Course A"},
                [],
                {},
            )

    asyncio.run(scenario())
    profile = recorder.finish(stderr=io.StringIO())
    idle = next(span for span in profile["spans"] if span["phase"] == "idle")
    assert idle["domain"] == "lectures"
    assert idle["course"] == 2
    assert idle["page_kind"] == "lecture"
    assert idle["wait_kind"] == "load"
