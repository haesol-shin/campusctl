"""P2f public sync against a loopback LMS and real Chromium, never a live account."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from campusctl.catalog import catalog_path, read_catalog, write_catalog
from campusctl.domain_catalog import domain_catalog_path, read_domain_catalog, write_domain_catalog
from campusctl.profiling import SpanRecorder
from campusctl.providers.cnu import assignments, login, materials, notices, sync_all
from campusctl.providers.cnu.sync import sync_lectures
from campusctl.sync import run_sync

IDS = tuple(f"course-{n}.invalid" for n in range(1, 8))
COURSES = [{"course_id": cid, "label": f"Fixture Course {n}", "class_no": "01"} for n, cid in enumerate(IDS, 1)]
DOMAINS = ("lectures", "assignments", "notices", "materials")

pytestmark = pytest.mark.chromium


def _topbar(*, wrong_selection: bool = False, delay_ms: int = 0) -> str:
    links = "".join(
        f'<a data-act="changeLecture" data-courseid="{cid}">Fixture Course {i}</a>' for i, cid in enumerate(IDS, 1)
    )
    selected = (
        f"(sessionStorage.selected === '{IDS[0]}' ? '{IDS[1]}' : sessionStorage.selected)"
        if wrong_selection
        else "sessionStorage.selected"
    )
    update = (
        "document.getElementById('topbarCurrentLecture').textContent = "
        f"document.querySelector('#topbarLectureDropdown [data-courseid=\"' + {selected} + '\"]').textContent;"
    )
    if delay_ms:
        update = f"setTimeout(() => {{{update}}}, {delay_ms});"
    return (
        f'<span id="topbarCurrentLecture"></span><div id="topbarLectureDropdown">{links}</div><script>{update}</script>'
    )


def _roster() -> str:
    links = "".join(
        f'<a data-act="moveLecture" data-courseid="{cid}" data-coursenm="Fixture Course {i}" '
        f'data-classno="01" href="/std/lecture">Fixture Course {i}</a>'
        for i, cid in enumerate(IDS, 1)
    )
    return f"""{links}<script>
for (const a of document.querySelectorAll('[data-act="moveLecture"]')) a.addEventListener('click', e => {{
 e.preventDefault(); const id = a.dataset.courseid;
 fetch('/api/v1/course/addSessionCourseInfo', {{method:'POST',
 headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{course_id:id}})}}).then(() => {{
  sessionStorage.selected=id; location.href='/std/lecture';
 }});
}});
</script>"""


def _menu() -> str:
    return '<nav><a href="/std/course">Lectures</a><a href="/std/task">Tasks</a><a href="/std/notice">Notices</a><a href="/std/archive">Archive</a></nav>'


def _document(
    path: str,
    *,
    wrong_topbar: bool = False,
    wrong_course_topbar: bool = False,
    skip_course_navigation: bool = False,
    first_course_section_redirect: bool = False,
    delayed_topbar: bool = False,
    archive_count_mismatch: bool = False,
    malformed_todo: bool = False,
    third_party: tuple[str, str, str] | None = None,
    external_probe: tuple[str, str, str] | None = None,
    background_request: bool = False,
) -> str:
    if path == "/std/myLecture":
        body = _roster()
        if external_probe is not None:
            probe_origin, method, kind = external_probe
            if kind == "xhr":
                body += (
                    f'<script>const probe=new XMLHttpRequest();probe.open("{method}",'
                    f'"{probe_origin}/probe");probe.send();</script>'
                )
            else:
                body += f'<script>fetch("{probe_origin}/probe",{{method:"{method}"}});</script>'
    elif path == "/std/lecture":
        body = _topbar(wrong_selection=wrong_topbar, delay_ms=900 if delayed_topbar else 0) + _menu()
        if skip_course_navigation:
            body += """<script>document.querySelector('a[href="/std/course"]')
 .addEventListener('click', event => event.preventDefault());</script>"""
        if first_course_section_redirect:
            body += f"""<script>if (sessionStorage.selected === '{IDS[0]}')
 document.querySelector('a[href="/std/course"]').addEventListener('click', event => {{
 event.preventDefault(); location.href='/'; }});</script>"""
    elif path == "/std/course":
        body = (
            _topbar(wrong_selection=wrong_course_topbar, delay_ms=900 if delayed_topbar else 0)
            + _menu()
            + """<div class="learningRow" id="LV1" data-moduletype="LV"
 data-state="N" data-openyn="Y" data-weekno="2" data-seqno="1">
 <span data-act="titleDetailContents">Fixture lesson</span></div>"""
        )
    elif path == "/std/task":
        body = (
            _topbar()
            + _menu()
            + """<table id="table_list"><tbody id="tbody">
<tr><td><a data-act="detail" data-id="TB_L_REPORT101"><strong>Fixture task</strong></a>
 2026-09-01 ~ 2026-09-30</td><td>미완료</td></tr></tbody></table>
<script>fetch('/api/v1/task/stdList',{method:'POST',body:JSON.stringify({course_id:sessionStorage.selected})});</script>"""
        )
        if background_request:
            body += "<script>fetch('/background');</script>"
    elif path == "/std/todo":
        # The global to-do page deliberately has no selected-course menu.
        rows = "".join(
            f"""<div class="tabulator-row"><span class="tabulator-cell" tabulator-field="no">{i}</span>
<span class="tabulator-cell" tabulator-field="course_nm">Fixture Course {i}</span>
<span class="tabulator-cell" tabulator-field="title">Fixture notice</span>
<span class="tabulator-cell" tabulator-field="date">{"invalid-date" if malformed_todo and i == 1 else "2026-09-02 09:00"}</span>
<span class="tabulator-cell" tabulator-field="read_yn">읽지않음</span>
<a data-boarditem_no="TB_L_BOARDITEM{i}01">Notice</a></div>"""
            for i in range(1, 8)
        )
        body = f"""<div id="noticeList"><div class="tabulator">{rows}</div></div>
<script>fetch('/api/v1/board/std/notice/list',{{method:'POST'}});</script>"""
    elif path == "/std/notice":
        body = (
            _topbar()
            + _menu()
            + """<main class="card"><table class="table mb-0" data-page-size="10">
<tbody id="table-body"></tbody></table><div class="pagination"><a data-page="1">1</a></div></main>
<script>
const n=Number(sessionStorage.selected.match(/(\\d+)\\.invalid$/)[1]);
document.getElementById('table-body').innerHTML='<tr><td><a href="/std/noticeDetail?no=TB_L_BOARDITEM'+n+'01">Fixture notice</a></td></tr>';
fetch('/api/v1/board/notice/list/top',{method:'POST',headers:{'X-Fixture-Course':sessionStorage.selected}});
fetch('/api/v1/board/notice/list',{method:'POST',headers:{'X-Fixture-Course':sessionStorage.selected}});
</script>"""
        )
    elif path == "/std/archive":
        body = (
            _topbar()
            + _menu()
            + """<div id="totalCnt"><strong>1</strong></div>
<table id="table_list" data-page-size="10"><tbody id="listBody"><tr><td>
<a data-act="detail" data-id="post-1">Fixture handout</a>
<button data-act="file" data-boarditem_no="post-1" onclick="showFiles()">Files</button>
</td></tr></tbody></table><div id="listPage"><div class="page-item active"><a data-page="1">1</a></div></div>
<div id="listBlankDiv" style="display:none"></div>
<div id="file_download" style="display:none"><a data-act="downloadFile" data-id="file-1"
 data-mime="application/pdf" data-size="123">Handout.pdf</a></div>
<script>
fetch('/api/v1/archive/list',{method:'POST'});
async function showFiles(){await fetch('/api/v1/archive/getAttachFileList?e=fixture',
 {headers:{'X-Fixture-Course':sessionStorage.selected}});
 document.getElementById('file_download').style.display='block';}
</script>"""
        )
        if archive_count_mismatch:
            body += """<script>if (sessionStorage.selected === 'course-4.invalid')
 document.querySelector('#totalCnt strong').textContent = '2';</script>"""
    else:
        raise AssertionError(path)
    assets = (
        f'<script src="{third_party[0]}/fixture.js"></script>'
        f'<link rel="stylesheet" href="{third_party[1]}/fixture.css" referrerpolicy="origin">'
        '<link rel="stylesheet" href="/assets/fixture.css">'
        f'<script>fetch("{third_party[2]}/v1/events",{{method:"POST"}});</script>'
        if third_party is not None
        else ""
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        + assets
        + '</head><body><div class="fixture-background"></div>'
        + body
        + "</body></html>"
    )


class FixtureServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str | None]] = []
        self.asset_referers: list[tuple[str, str | None]] = []
        self.board_paginated = False
        self.task_count_mismatch = False
        self.archive_count_mismatch = False
        self.wrong_topbar = False
        self.wrong_course_topbar = False
        self.skip_course_navigation = False
        self.first_course_section_redirect = False
        self.delayed_topbar = False
        self.malformed_todo = False
        self.todo_redirect = False
        self.third_party: tuple[str, str, str] | None = None
        self.external_probe: tuple[str, str, str] | None = None
        self.background_request = False
        self.background_started = threading.Event()
        self.background_release = threading.Event()
        super().__init__(("127.0.0.1", 0), FixtureHandler)


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureServer

    def _respond(self, body: str | dict[str, Any], status: int = 200, content_type: str | None = None) -> None:
        data = (json.dumps(body) if isinstance(body, dict) else body).encode("utf-8")
        self.send_response(status)
        self.send_header(
            "Content-Type",
            content_type or ("application/json" if isinstance(body, dict) else "text/html; charset=utf-8"),
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(data)

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        size = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(size) if size else b""
        incoming = json.loads(payload) if payload and path == "/api/v1/course/addSessionCourseInfo" else {}
        selected = incoming.get("course_id") if incoming else self.headers.get("X-Fixture-Course")
        self.server.requests.append((self.command, path, selected))
        if path == "/background":
            self.server.background_started.set()
            self.server.background_release.wait(timeout=20)
            self._respond("background")
            return
        if path.startswith(("/assets/images/", "/assets/fonts/")):
            self.server.asset_referers.append((path, self.headers.get("Referer")))
        if self.command == "GET" and path == "/std/todo" and self.server.todo_redirect:
            self.send_response(302)
            self.send_header("Location", "/std/lecture")
            self.end_headers()
            return
        if (
            self.command == "GET"
            and path in {"/std/notice", "/std/archive"}
            and self.headers.get("Sec-Fetch-User") != "?1"
        ):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
        elif self.command == "GET" and path.startswith("/std/") and path != "/std/noticeDetail":
            self._respond(
                _document(
                    path,
                    wrong_topbar=self.server.wrong_topbar,
                    wrong_course_topbar=self.server.wrong_course_topbar,
                    skip_course_navigation=self.server.skip_course_navigation,
                    first_course_section_redirect=self.server.first_course_section_redirect,
                    delayed_topbar=self.server.delayed_topbar,
                    archive_count_mismatch=self.server.archive_count_mismatch,
                    malformed_todo=self.server.malformed_todo,
                    third_party=self.server.third_party,
                    external_probe=self.server.external_probe,
                    background_request=self.server.background_request,
                )
            )
        elif self.command == "GET" and path == "/fixture.js":
            self._respond("/* external fixture asset */", content_type="application/javascript")
        elif self.command == "GET" and path == "/fixture.css":
            self._respond("body { color: black; }", content_type="text/css")
        elif self.command == "GET" and path == "/assets/fixture.css":
            self._respond(
                '@font-face {font-family: FixtureFont; src: url("/assets/fonts/fixture.woff2")}'
                '.fixture-background {width: 1px; height: 1px; background: url("/assets/images/fixture-a.svg")}'
                '.fixture-background::before {content: "x"; font-family: FixtureFont; display: block;'
                'width: 1px; height: 1px; background: url("/assets/images/fixture-b.svg")}',
                content_type="text/css",
            )
        elif self.command == "GET" and path in {"/assets/images/fixture-a.svg", "/assets/images/fixture-b.svg"}:
            self._respond(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>', content_type="image/svg+xml"
            )
        elif self.command == "GET" and path == "/assets/fonts/fixture.woff2":
            self._respond("wOF2synthetic-font", content_type="font/woff2")
        elif path == "/api/v1/course/addSessionCourseInfo":
            self._respond("Selection receipt is intentionally not JSON.", content_type="text/plain")
        elif path == "/api/v1/board/notice/list/top":
            self._respond({"header": {"code": 200}, "body": {"list": []}})
        elif path == "/api/v1/board/notice/list":
            assert selected in IDS
            n = IDS.index(selected) + 1
            item = {
                "course_id": selected,
                "delete_yn": "N",
                "boarditem_no": f"TB_L_BOARDITEM{n}01",
                "boarditem_title": "Fixture notice",
                "row_idx": n,
                "insert_dt": "2026-09-02",
                "insert_dt_addtime": "2026-09-02 09:00",
                "boarditem_viewcnt": 7,
                "file_yn": 0,
                "writeruser_name": "Fixture Author",
            }
            self._respond(
                {
                    "header": {"code": 200},
                    "body": {"total": 11 if self.server.board_paginated and n == 4 else 1, "list": [item]},
                }
            )
        elif path == "/api/v1/task/stdList":
            if self.server.background_request:
                assert self.server.background_started.wait(timeout=5)
            cid = json.loads(payload).get("course_id")
            if self.server.task_count_mismatch and cid == IDS[3]:
                self._respond({"body": {"total": 2, "course_id": cid}})
            else:
                self._respond({"body": {"list": [{"course_id": cid}]}})
        elif path == "/api/v1/archive/list":
            self._respond({"body": {"list": [{}], "total": 1}})
        elif path == "/api/v1/archive/getAttachFileList":
            self._respond({"body": [{"file_id": "file-1", "file_name": "Handout.pdf"}]})
        elif path == "/api/v1/board/std/notice/list":
            self._respond({"header": {"code": 200}, "body": {"total": 7, "list": [{"number": n} for n in range(1, 8)]}})
        else:
            self._respond("Unreviewed fixture request", status=404)

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def log_message(self, *_args: object) -> None:
        pass


@contextmanager
def fixture_server() -> Iterator[FixtureServer]:
    server = FixtureServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _chromium() -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright is not installed")
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.is_file():
            pytest.skip("local Playwright Chromium is not installed")
        return str(executable)


def _install_fixture(monkeypatch: pytest.MonkeyPatch, server: FixtureServer) -> dict[str, Any]:
    origin = f"http://127.0.0.1:{server.server_port}"
    login_calls: list[str] = []

    async def login_once(page: Any, _config: dict[str, Any], **_kwargs: Any) -> None:
        login_calls.append("login")
        assets, probe = server.third_party, server.external_probe
        server.third_party = None
        server.external_probe = None
        try:
            await page.goto(origin + "/std/myLecture")
        finally:
            server.third_party = assets
            server.external_probe = probe

    monkeypatch.setattr(login, "ensure_logged_in", login_once)
    monkeypatch.setattr(login, "MY_LECTURE_URL", origin + "/std/myLecture")
    monkeypatch.setattr(sync_all, "MY_LECTURE_URL", origin + "/std/myLecture")
    monkeypatch.setattr(sync_all, "ensure_logged_in", login_once)
    roster_calls: list[int] = []
    original_parse = sync_all.parse_courses

    def record_roster(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        roster_calls.append(len(raw))
        return original_parse(raw)

    monkeypatch.setattr(sync_all, "parse_courses", record_roster)
    monkeypatch.setattr(notices, "_ORIGIN", origin)
    monkeypatch.setattr(notices, "_TODO_URL", origin + "/std/todo")
    monkeypatch.setattr(
        materials._RequestWindow,
        "_archive_referer",
        staticmethod(lambda request: request.headers.get("referer", "") == origin + "/std/archive"),
    )
    monkeypatch.setattr(sync_all, "_fixture_roster_calls", roster_calls, raising=False)
    return {"browser": {"executable_path": _chromium(), "headless": True}, "_fixture_login_calls": login_calls}


def _catalogs(root: Path) -> dict[str, dict[str, Any]]:
    return {
        "lectures": read_catalog(catalog_path(root)),
        **{domain: read_domain_catalog(domain, domain_catalog_path(domain, root)) for domain in DOMAINS[1:]},
    }


def _expected_row(domain: str, cid: str, n: int) -> dict[str, Any]:
    course = {"id": cid, "label": f"Fixture Course {n}"}
    if domain == "lectures":
        return {
            "entity_id": f"cnu_lecture:{cid}:LV1",
            "course": course,
            "kind": "lecture",
            "title": "Fixture lesson",
            "week": "2",
            "sequence": "1",
            "progress_text": None,
            "duration_minutes": None,
            "available_from": None,
            "due_date": None,
            "late_until": None,
            "media": "other",
            "attendance_counted": None,
            "open": True,
            "completion": "incomplete",
            "provider_state": "N",
        }
    if domain == "assignments":
        return {
            "entity_id": f"cnu_assignment:{cid}:TB_L_REPORT101",
            "task_id": "TB_L_REPORT101",
            "course": course,
            "kind": "assignment",
            "title": "Fixture task",
            "due_date": "2026-09-30",
            "is_submitted": False,
        }
    if domain == "notices":
        return {
            "entity_id": f"cnu_notice:{cid}:2026-09-02 09%3A00:{n}",
            "native_id": f"TB_L_BOARDITEM{n}01",
            "legacy_key": f"Fixture Course {n}_2026-09-02 09:00_{n}",
            "course": course,
            "kind": "notice",
            "title": "Fixture notice",
            "date": "2026-09-02 09:00",
            "status": "읽지않음",
            "is_unread": True,
            "posted_date": None,
            "author_role": None,
            "author": "Fixture Author",
            "view_count": 7,
            "has_attachments": False,
        }
    return {
        "entity_id": f"cnu_lms_material:{cid}:file-1",
        "course": course,
        "archive_entry": {"board_item_id": "post-1", "title": "Fixture handout"},
        "file_id": "file-1",
        "display_name": "Handout.pdf",
        "filename": "Handout.pdf",
        "media_type": "application/pdf",
        "size_bytes": 123,
        "downloadable": True,
        "unavailable_reason": None,
    }


def test_assignment_sync_ignores_outstanding_unrelated_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with fixture_server() as server:
        server.background_request = True
        config = _install_fixture(monkeypatch, server)
        try:
            result, errors = run_sync(config, tmp_path, ("assignments",), IDS[0], headless=True)
            assert errors is None
            assert result["assignments"] == 1
            assert server.background_started.is_set()
            assert not server.background_release.is_set()
            catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
            assert catalog["assignments"] == [_expected_row("assignments", IDS[0], 1)]
        finally:
            server.background_release.set()


def test_seven_courses_one_session_and_full_normalized_catalogs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl import cli

    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, DOMAINS, None, headless=True)
        assert errors is None, (getattr(errors, "reason_code", None), server.requests, result)
        assert list(result["domains"]) == list(DOMAINS)
        assert all(result["domains"][domain]["status"] == "ok" for domain in DOMAINS)
        catalogs = _catalogs(tmp_path)
        assert all(catalog["courses"] == COURSES for catalog in catalogs.values())
        for domain, catalog in catalogs.items():
            assert catalog["enrollment_state"] == "known"
            assert catalog["failed_courses"] == []
            assert catalog[domain] == [_expected_row(domain, cid, n) for n, cid in enumerate(IDS, 1)]
        paths = Counter((method, path) for method, path, _ in server.requests)
        assert config["_fixture_login_calls"] == ["login"]
        assert sync_all._fixture_roster_calls == [7]
        # Authentication, discovery, post-to-do return, and six inter-course returns.
        assert paths[("GET", "/std/myLecture")] == 9
        assert paths[("GET", "/std/todo")] == 1
        assert paths[("POST", "/api/v1/board/std/notice/list")] == 1
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)
        first_todo = next(
            index for index, (method, path, _) in enumerate(server.requests) if (method, path) == ("GET", "/std/todo")
        )
        first_selection = next(
            index
            for index, (method, path, _) in enumerate(server.requests)
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        )
        assert first_todo < first_selection
        for path in ("/std/lecture", "/std/course", "/std/notice"):
            assert paths[("GET", path)] == 7
        assert paths[("GET", "/std/task")] in {7, 8}  # Browser history may restore from cache.
        # One archive entry per course; unchanged attachment dialogs do not reload the document.
        assert paths[("GET", "/std/archive")] == len(IDS)
        selection_positions = [
            index
            for index, (method, path, _) in enumerate(server.requests)
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ]
        for ordinal, start in enumerate(selection_positions):
            end = selection_positions[ordinal + 1] if ordinal + 1 < 7 else len(server.requests)
            visits = [path for method, path, _ in server.requests[start:end] if method == "GET"]
            section_positions = [
                visits.index(path)
                for path in ("/std/lecture", "/std/course", "/std/task", "/std/notice", "/std/archive")
            ]
            assert section_positions == sorted(section_positions)
            assert visits.count("/std/archive") == 1
            assert "/std/todo" not in visits
        assert all("log" not in path.lower() and "video" not in path.lower() for _, path, _ in server.requests)
        monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
        assert cli.main(["notices", "list", "--json"]) == 0
        output = capsys.readouterr()
        assert output.err == ""
        visible = json.loads(output.out)["result"]["notices"]
        assert visible == [
            {key: value for key, value in _expected_row("notices", cid, n).items() if key != "native_id"}
            for n, cid in enumerate(IDS, 1)
        ]


def test_seven_course_sync_loads_external_assets_and_survives_dead_loopback_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fixture_server() as server, fixture_server() as cdn, fixture_server() as fonts:
        server.third_party = (
            f"http://127.0.0.1:{cdn.server_port}",
            f"http://127.0.0.1:{fonts.server_port}",
            "http://127.0.0.1:1",
        )
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, DOMAINS, None, headless=True)
        assert errors is None, (getattr(errors, "reason_code", None), server.requests, result)
        assert all(result["domains"][domain]["status"] == "ok" for domain in DOMAINS)
        assert ("GET", "/fixture.js", None) in cdn.requests
        assert ("GET", "/fixture.css", None) in fonts.requests
        visited = {path for method, path, _ in server.requests if method == "GET"}
        assert {"/std/myLecture", "/std/lecture", "/std/course", "/std/task", "/std/notice", "/std/archive"} <= visited
        assert {
            ("/assets/images/fixture-a.svg", f"http://127.0.0.1:{server.server_port}/assets/fixture.css"),
            ("/assets/images/fixture-b.svg", f"http://127.0.0.1:{server.server_port}/assets/fixture.css"),
            ("/assets/fonts/fixture.woff2", f"http://127.0.0.1:{server.server_port}/assets/fixture.css"),
        } <= set(server.asset_referers)


def test_delayed_entry_and_section_topbars_still_bind_the_selected_course(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fixture_server() as server:
        server.delayed_topbar = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("lectures", "assignments"), IDS[0], headless=True)
        assert errors is None, (errors, server.requests, result)
        assert all(result["domains"][domain]["status"] == "ok" for domain in ("lectures", "assignments"))
        assert read_catalog(catalog_path(tmp_path))["lectures"] == [_expected_row("lectures", IDS[0], 1)]
        assignments_catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        assert assignments_catalog["assignments"] == [_expected_row("assignments", IDS[0], 1)]


@pytest.mark.parametrize(("method", "kind"), [("POST", "fetch"), ("GET", "fetch"), ("GET", "xhr")])
def test_external_active_request_is_not_intercepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, kind: str
) -> None:
    with fixture_server() as server, fixture_server() as outside:
        server.external_probe = (f"http://127.0.0.1:{outside.server_port}", method, kind)
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, DOMAINS, None, headless=True)
        assert errors is None, (errors, result)
        assert (method, "/probe", None) in outside.requests
        assert all(result["domains"][domain]["status"] == "ok" for domain in DOMAINS)


def test_document_commit_spans_exclude_all_four_collector_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = [0]
    recorder = SpanRecorder(enabled=True, scope=DOMAINS, clock=lambda: ticks[0])
    for module, name in (
        (sync_all, "collect_lectures_rows"),
        (assignments, "collect_assignment_rows"),
        (notices, "collect_notice_rows"),
        (materials, "collect_materials_rows"),
    ):
        original = getattr(module, name)

        async def measured(*args: Any, _collector: Any = original, **kwargs: Any) -> list[dict[str, Any]]:
            ticks[0] += 1_000_000_000
            return await _collector(*args, **kwargs)

        monkeypatch.setattr(module, name, measured)

    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, DOMAINS, IDS[0], headless=True, profile=recorder)
        assert errors is None
        assert all(result["domains"][domain]["status"] == "ok" for domain in DOMAINS)

    profile = recorder.finish(stderr=io.StringIO())
    assert profile["counts"]["course_selections"] == 1
    commits = [span for span in profile["spans"] if span["phase"] == "document-commit" and span["course"] == 1]
    assert {span["domain"] for span in commits} == set(DOMAINS)
    assert all(span["inclusive_ns"] == 0 for span in commits)
    assert profile["wall_ns"] == 4_000_000_000
    assert profile["diagnostics"] == []


def test_first_entry_topbar_mismatch_retains_old_row_and_continues_other_courses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = {**_expected_row("lectures", IDS[0], 1), "title": "Previously saved lesson"}
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [COURSES[0]],
            "failed_courses": [],
            "lectures": [previous],
        },
        catalog_path(tmp_path),
    )
    recorder = SpanRecorder(enabled=True, scope=DOMAINS)
    with fixture_server() as server:
        server.wrong_topbar = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, DOMAINS, None, headless=True, profile=recorder)
        assert [error.code for error in errors] == ["course-sync-failed"] * len(DOMAINS)
        assert all(result["domains"][domain]["status"] == "partial" for domain in DOMAINS)
        assert all(
            f"{domain} sync failed" in result["domains"][domain]["errors"][0]["message"]
            and "course selection" in result["domains"][domain]["errors"][0]["message"]
            for domain in DOMAINS
        )
        catalogs = _catalogs(tmp_path)
        assert catalogs["lectures"]["lectures"] == [
            previous,
            *[_expected_row("lectures", cid, n) for n, cid in enumerate(IDS, 1) if n > 1],
        ]
        for domain in DOMAINS[1:]:
            assert catalogs[domain][domain] == [_expected_row(domain, cid, n) for n, cid in enumerate(IDS, 1) if n > 1]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)
        assert sum(path == "/std/course" for _, path, _ in server.requests) == 6
    profile = recorder.finish(stderr=io.StringIO())
    diagnostic = next(row for row in profile["diagnostics"] if row["check"] == "course-identity")
    assert diagnostic["page_kind"] == "course-entry" and diagnostic["course"] == 1
    assert diagnostic["states"]["ids_match"] is False
    assert diagnostic["elapsed_ns"] > 0 and diagnostic["bound_ns"] > 0


def test_cli_profile_stderr_carries_failed_check_without_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl import cli

    with fixture_server() as server:
        server.wrong_topbar = True
        config = _install_fixture(monkeypatch, server)
        monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cli, "load_config", lambda: config)
        code = cli.main(["--headless", "--profile", "sync", "--only", "lectures", "--course", IDS[0], "--json"])
    output = capsys.readouterr()
    assert code == 1
    lines = [line for line in output.err.splitlines() if line.startswith("campusctl-profile: ")]
    assert len(lines) == 1
    profile = json.loads(lines[0].removeprefix("campusctl-profile: "))
    assert profile["schema_version"] == 2
    assert profile["diagnostics"][0]["check"] == "course-identity"
    assert profile["diagnostics"][0]["course"] == 1
    assert profile["diagnostics"][0]["states"]["ids_match"] is False
    assert IDS[0] not in lines[0] and COURSES[0]["label"] not in lines[0]


def test_first_course_section_redirect_fails_only_that_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with fixture_server() as server:
        server.first_course_section_redirect = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, DOMAINS, None, headless=True)
        assert [error.code for error in errors] == ["course-sync-failed"] * len(DOMAINS)
        catalogs = _catalogs(tmp_path)
        for domain in DOMAINS:
            assert result["domains"][domain]["status"] == "partial"
            assert "section navigation" in result["domains"][domain]["errors"][0]["message"]
            assert catalogs[domain][domain] == [_expected_row(domain, cid, n) for n, cid in enumerate(IDS, 1) if n > 1]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)


@pytest.mark.parametrize("failure", ["wrong_course_topbar", "skip_course_navigation"])
def test_lecture_section_requires_its_own_committed_course_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    with fixture_server() as server:
        setattr(server, failure, True)
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("lectures", "assignments"), IDS[0], headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == (["course-sync-failed"] * (2 if failure == "wrong_course_topbar" else 1))
        lecture_catalog = read_catalog(catalog_path(tmp_path))
        assert lecture_catalog["lectures"] == []
        assert lecture_catalog["failed_courses"] == [
            {"course_id": IDS[0], "label": COURSES[0]["label"], "reason": "course-sync-failed"}
        ]
        assignment_catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        if failure == "wrong_course_topbar":
            assert result["domains"]["assignments"]["status"] == "partial"
            assert assignment_catalog["assignments"] == []
        else:
            assert result["domains"]["assignments"]["status"] == "ok"
            assert assignment_catalog["assignments"] == [_expected_row("assignments", IDS[0], 1)]
        assert sum(path == "/std/course" for _, path, _ in server.requests) == (
            0 if failure == "skip_course_navigation" else 1
        )


def test_filtered_selection_and_unknown_course_do_not_claim_full_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        selected = IDS[3]
        result, errors = run_sync(config, tmp_path, DOMAINS, selected, headless=True)
        assert errors is None, (errors, server.requests, result)
        catalogs = _catalogs(tmp_path)
        for domain, catalog in catalogs.items():
            assert catalog["enrollment_state"] == "unknown"
            assert catalog["failed_courses"] == []
            assert catalog["courses"] == [COURSES[3]]
            assert catalog[domain] == [_expected_row(domain, selected, 4)]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == [selected]
        before = {domain: (tmp_path / "catalog" / f"{domain}.json").read_bytes() for domain in DOMAINS}
        result, errors = run_sync(config, tmp_path, DOMAINS, "not-enrolled.invalid", headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["course-not-found"]
        assert all(result["domains"][domain]["status"] == "not-started" for domain in DOMAINS[1:])
        assert {domain: (tmp_path / "catalog" / f"{domain}.json").read_bytes() for domain in DOMAINS} == before
        assert sum(path == "/std/todo" for _, path, _ in server.requests) == 1


def test_discovery_failure_preserves_rows_and_marks_every_catalog_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_course = COURSES[0]
    for domain in DOMAINS:
        record = {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [old_course],
            "failed_courses": [],
            domain: [_expected_row(domain, IDS[0], 1)],
        }
        if domain == "lectures":
            write_catalog(record, catalog_path(tmp_path))
        else:
            write_domain_catalog(domain, record, domain_catalog_path(domain, tmp_path))
    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)

        def broken_roster(_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
            raise ValueError("Synthetic roster could not be decoded")

        monkeypatch.setattr(sync_all, "parse_courses", broken_roster)
        result, errors = run_sync(config, tmp_path, DOMAINS, None, headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["course-discovery-failed"]
        assert all(result["domains"][domain]["status"] == "not-started" for domain in DOMAINS[1:])
        for domain, catalog in _catalogs(tmp_path).items():
            assert catalog["enrollment_state"] == "unknown"
            assert catalog["courses"] == [old_course]
            assert catalog["failed_courses"] == []
            assert catalog[domain] == [_expected_row(domain, IDS[0], 1)]
        assert [
            path
            for method, path, _ in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == []


def test_second_domain_atomic_write_failure_reports_only_committed_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from campusctl.envelope import CampusError

    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)

        def fail_second(domain: str, _value: dict[str, Any], _path: Path) -> Path:
            assert domain == "assignments"
            raise CampusError("catalog-write-failed", "Synthetic disk failure.", None, "error")

        monkeypatch.setattr(sync_all, "write_domain_catalog", fail_second)
        result, errors = run_sync(config, tmp_path, DOMAINS, IDS[0], headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["catalog-write-failed"]
        outcomes = result["domains"]
        assert outcomes["lectures"]["status"] == "ok"
        assert outcomes["lectures"]["result"]["lectures"] == 1
        assert outcomes["assignments"]["status"] == "error"
        assert outcomes["assignments"]["result"] == {}
        assert all(outcomes[domain]["status"] == "not-started" for domain in ("notices", "materials"))
        lecture = read_catalog(catalog_path(tmp_path))
        assert lecture["courses"] == [COURSES[0]]
        assert lecture["lectures"] == [_expected_row("lectures", IDS[0], 1)]
        assert all(not domain_catalog_path(domain, tmp_path).exists() for domain in DOMAINS[1:])


def test_single_course_board_failure_retains_prior_row_and_other_courses_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_notice = {**_expected_row("notices", IDS[3], 4), "title": "Previously saved notice"}
    write_domain_catalog(
        "notices",
        {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [COURSES[3]],
            "failed_courses": [],
            "notices": [old_notice],
        },
        domain_catalog_path("notices", tmp_path),
    )
    with fixture_server() as server:
        server.board_paginated = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("notices", "materials"), None, headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["notice-board-paginated"]
        assert result["domains"]["notices"]["status"] == "partial"
        assert result["domains"]["materials"]["status"] == "ok"
        notices_catalog = read_domain_catalog("notices", domain_catalog_path("notices", tmp_path))
        assert notices_catalog["enrollment_state"] == "known"
        assert notices_catalog["courses"] == COURSES
        assert notices_catalog["failed_courses"] == [
            {"course_id": IDS[3], "label": "Fixture Course 4", "reason": "notice-board-paginated"}
        ]
        assert notices_catalog["notices"] == [
            old_notice,
            *[_expected_row("notices", cid, n) for n, cid in enumerate(IDS, 1) if n != 4],
        ]
        materials_catalog = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
        assert materials_catalog["courses"] == COURSES
        assert materials_catalog["failed_courses"] == []
        assert materials_catalog["materials"] == [_expected_row("materials", cid, n) for n, cid in enumerate(IDS, 1)]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)


def test_failed_task_response_retains_only_that_courses_previous_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_task = {**_expected_row("assignments", IDS[3], 4), "title": "Previously saved task"}
    write_domain_catalog(
        "assignments",
        {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [COURSES[3]],
            "failed_courses": [],
            "assignments": [old_task],
        },
        domain_catalog_path("assignments", tmp_path),
    )
    with fixture_server() as server:
        server.task_count_mismatch = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("assignments", "materials"), None, headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["course-sync-failed"]
        assert result["domains"]["assignments"]["status"] == "partial"
        assert "assignments" in result["domains"]["assignments"]["errors"][0]["message"]
        assert "section extraction" in result["domains"]["assignments"]["errors"][0]["message"]
        assert result["domains"]["materials"]["status"] == "ok"
        task_catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        assert task_catalog["enrollment_state"] == "known"
        assert task_catalog["courses"] == COURSES
        assert task_catalog["failed_courses"] == [
            {"course_id": IDS[3], "label": "Fixture Course 4", "reason": "course-sync-failed"}
        ]
        assert task_catalog["assignments"] == [
            old_task,
            *[_expected_row("assignments", cid, n) for n, cid in enumerate(IDS, 1) if n != 4],
        ]
        materials_catalog = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
        assert materials_catalog["courses"] == COURSES
        assert materials_catalog["failed_courses"] == []
        assert materials_catalog["materials"] == [_expected_row("materials", cid, n) for n, cid in enumerate(IDS, 1)]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)


def test_incomplete_archive_list_retains_only_failed_courses_previous_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_material = {**_expected_row("materials", IDS[3], 4), "display_name": "Earlier handout.pdf"}
    write_domain_catalog(
        "materials",
        {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [COURSES[3]],
            "failed_courses": [],
            "materials": [old_material],
        },
        domain_catalog_path("materials", tmp_path),
    )
    with fixture_server() as server:
        server.archive_count_mismatch = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("assignments", "materials"), None, headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["course-sync-failed"]
        assert result["domains"]["assignments"]["status"] == "ok"
        assert result["domains"]["materials"]["status"] == "partial"
        assignments_catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        assert assignments_catalog["courses"] == COURSES
        assert assignments_catalog["failed_courses"] == []
        assert assignments_catalog["assignments"] == [
            _expected_row("assignments", cid, n) for n, cid in enumerate(IDS, 1)
        ]
        material_catalog = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
        assert material_catalog["courses"] == COURSES
        assert material_catalog["enrollment_state"] == "known"
        assert material_catalog["failed_courses"] == [
            {"course_id": IDS[3], "label": "Fixture Course 4", "reason": "course-sync-failed"}
        ]
        assert material_catalog["materials"] == [
            old_material,
            *[_expected_row("materials", cid, n) for n, cid in enumerate(IDS, 1) if n != 4],
        ]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)


def test_partial_cli_exit_reflects_paginated_notice_and_committed_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from campusctl import cli

    old_notice = {**_expected_row("notices", IDS[3], 4), "title": "Previously saved notice"}
    write_catalog(
        {"schema_version": 1, "courses": [COURSES[3]], "lectures": [], "generated_at": "2026-08-01T00:00:00Z"},
        catalog_path(tmp_path),
    )
    write_domain_catalog(
        "notices",
        {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [COURSES[3]],
            "failed_courses": [],
            "notices": [old_notice],
        },
        domain_catalog_path("notices", tmp_path),
    )
    with fixture_server() as server:
        server.board_paginated = True
        config = _install_fixture(monkeypatch, server)
        monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cli, "load_config", lambda: config)
        code = cli.main(["--headless", "sync", "--only", "notices,materials", "--course", IDS[3], "--json"])
        output = capsys.readouterr()
        assert output.err == ""
        envelope = json.loads(output.out)
        assert code == 1
        assert envelope["status"] == "partial"
        assert [error["code"] for error in envelope["errors"]] == ["notice-board-paginated"]
        assert envelope["result"]["domains"]["notices"]["status"] == "partial"
        assert envelope["result"]["domains"]["materials"]["status"] == "ok"
        stale = read_domain_catalog("notices", domain_catalog_path("notices", tmp_path))
        assert stale["notices"] == [old_notice]
        assert stale["failed_courses"] == [
            {"course_id": IDS[3], "label": "Fixture Course 4", "reason": "notice-board-paginated"}
        ]
        material = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
        assert material["materials"] == [_expected_row("materials", IDS[3], 4)]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == [IDS[3]]


@pytest.mark.parametrize("domain", DOMAINS)
def test_standalone_provider_wrapper_preserves_selection_and_rows(
    domain: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        wrappers = {
            "lectures": sync_lectures,
            "assignments": assignments.sync_assignments,
            "notices": notices.sync_notices,
            "materials": materials.sync_materials,
        }
        result, errors = asyncio.run(wrappers[domain](config, tmp_path, IDS[0], headless=True))
        assert errors == []
        assert result[domain] == 1
        catalog = (
            read_catalog(catalog_path(tmp_path))
            if domain == "lectures"
            else read_domain_catalog(domain, domain_catalog_path(domain, tmp_path))
        )
        assert catalog["courses"] == [COURSES[0]]
        assert catalog[domain] == [_expected_row(domain, IDS[0], 1)]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == [IDS[0]]
        assert sum(path == "/std/todo" for _, path, _ in server.requests) == (1 if domain == "notices" else 0)
        assert config["_fixture_login_calls"] == ["login"]


def test_lecture_extraction_failure_retains_only_failed_courses_prior_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = {**_expected_row("lectures", IDS[3], 4), "title": "Previously saved lecture"}
    write_catalog(
        {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [COURSES[3]],
            "failed_courses": [],
            "lectures": [prior],
        },
        catalog_path(tmp_path),
    )
    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        collector = sync_all.collect_lectures_rows

        async def collect_or_fail(
            page: Any, course: dict[str, Any], *, ordinal: int | None = None
        ) -> list[dict[str, Any]]:
            if course["course_id"] == IDS[3]:
                assert page.main_frame.url.endswith("/std/course")
                raise ValueError("Synthetic lecture extractor failed after selected document commit")
            return await collector(page, course, ordinal=ordinal)

        monkeypatch.setattr(sync_all, "collect_lectures_rows", collect_or_fail)
        result, errors = run_sync(config, tmp_path, ("lectures", "assignments"), None, headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["course-sync-failed"]
        assert result["domains"]["lectures"]["status"] == "partial"
        assert result["domains"]["lectures"]["result"]["failed_courses"] == [
            {"course_id": IDS[3], "label": "Fixture Course 4"}
        ]
        assert result["domains"]["assignments"]["status"] == "ok"
        lecture_catalog = read_catalog(catalog_path(tmp_path))
        assert sorted(lecture_catalog["courses"], key=lambda course: course["course_id"]) == COURSES
        assert lecture_catalog["enrollment_state"] == "known"
        assert lecture_catalog["failed_courses"] == [
            {"course_id": IDS[3], "label": "Fixture Course 4", "reason": "course-sync-failed"}
        ]
        assert lecture_catalog["lectures"] == [
            prior,
            *[_expected_row("lectures", cid, n) for n, cid in enumerate(IDS, 1) if n != 4],
        ]
        assignment_catalog = read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))
        assert assignment_catalog["courses"] == COURSES
        assert assignment_catalog["failed_courses"] == []
        assert assignment_catalog["assignments"] == [
            _expected_row("assignments", cid, n) for n, cid in enumerate(IDS, 1)
        ]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)


def test_first_course_todo_failure_preserves_notice_and_continues_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_notice = {**_expected_row("notices", IDS[0], 1), "title": "Previously saved notice"}
    write_domain_catalog(
        "notices",
        {
            "schema_version": 1,
            "generated_at": "2026-08-01T00:00:00Z",
            "enrollment_state": "known",
            "courses": [COURSES[0]],
            "failed_courses": [],
            "notices": [old_notice],
        },
        domain_catalog_path("notices", tmp_path),
    )
    with fixture_server() as server:
        server.malformed_todo = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("notices", "materials"), None, headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["course-sync-failed"]
        detail = result["domains"]["notices"]["errors"][0]["message"]
        assert "notices sync failed" in detail and "todo extraction" in detail
        assert result["domains"]["notices"]["status"] == "partial"
        assert result["domains"]["materials"]["status"] == "ok"
        notice_catalog = read_domain_catalog("notices", domain_catalog_path("notices", tmp_path))
        assert notice_catalog["courses"] == COURSES
        assert notice_catalog["failed_courses"] == [
            {"course_id": IDS[0], "label": "Fixture Course 1", "reason": "course-sync-failed"}
        ]
        assert notice_catalog["notices"] == [
            old_notice,
            *[_expected_row("notices", cid, n) for n, cid in enumerate(IDS, 1) if n != 1],
        ]
        material_catalog = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
        assert material_catalog["courses"] == COURSES
        assert material_catalog["failed_courses"] == []
        assert material_catalog["materials"] == [_expected_row("materials", cid, n) for n, cid in enumerate(IDS, 1)]
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == list(IDS)
        assert ("GET", "/std/todo", None) in server.requests and sum(
            path == "/std/todo" for _, path, _ in server.requests
        ) == 1
        assert sum(path == "/std/archive" and method == "GET" for method, path, _ in server.requests) == len(IDS)


def test_failed_todo_navigation_restores_roster_before_archive_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fixture_server() as server:
        server.todo_redirect = True
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("notices", "materials"), IDS[0], headless=True)
        assert errors is not None
        assert result["domains"]["notices"]["status"] == "partial"
        assert "notices sync failed" in result["domains"]["notices"]["errors"][0]["message"]
        assert "at todo extraction" in result["domains"]["notices"]["errors"][0]["message"]
        assert result["domains"]["materials"]["status"] == "ok"
        assert ("GET", "/std/archive", None) in server.requests
        todo_index = next(i for i, (_, path, _) in enumerate(server.requests) if path == "/std/todo")
        selection_index = next(
            i for i, (_, path, _) in enumerate(server.requests) if path == "/api/v1/course/addSessionCourseInfo"
        )
        assert any(path == "/std/myLecture" for _, path, _ in server.requests[todo_index + 1 : selection_index])
        materials_catalog = read_domain_catalog("materials", domain_catalog_path("materials", tmp_path))
        assert materials_catalog["materials"] == [_expected_row("materials", IDS[0], 1)]


def test_later_invalid_catalog_blocks_all_publication_and_names_later_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = domain_catalog_path("notices", tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("{invalid JSON", encoding="utf-8")
    original = target.read_bytes()
    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("lectures", "notices"), IDS[0], headless=True)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code]
        assert codes == ["catalog-invalid"]
        assert result["domains"]["lectures"]["status"] == "not-started"
        assert result["domains"]["notices"]["status"] == "user-action"
        assert result["domains"]["lectures"]["result"] == {}
        assert result["domains"]["notices"]["result"] == {}
        assert target.read_bytes() == original
        assert not catalog_path(tmp_path).exists()
        assert [
            cid
            for method, path, cid in server.requests
            if (method, path) == ("POST", "/api/v1/course/addSessionCourseInfo")
        ] == [IDS[0]]


def test_catalogs_are_published_only_after_browser_session_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_session = sync_all.browser.open_session
    original_write = sync_all.write_catalog
    original_domain_write = sync_all.write_domain_catalog
    closed = False

    @asynccontextmanager
    async def tracked_session(*args: Any, **kwargs: Any) -> Any:
        nonlocal closed
        async with original_session(*args, **kwargs) as session:
            yield session
        closed = True

    def write_lectures(value: dict[str, Any], target: Path) -> Path:
        assert closed, "published lectures before browser close"
        return original_write(value, target)

    def write_assignments(domain: str, value: dict[str, Any], target: Path) -> Path:
        assert closed, "published assignments before browser close"
        return original_domain_write(domain, value, target)

    monkeypatch.setattr(sync_all.browser, "open_session", tracked_session)
    monkeypatch.setattr(sync_all, "write_catalog", write_lectures)
    monkeypatch.setattr(sync_all, "write_domain_catalog", write_assignments)
    with fixture_server() as server:
        config = _install_fixture(monkeypatch, server)
        result, errors = run_sync(config, tmp_path, ("lectures", "assignments"), IDS[0], headless=True)
    assert errors is None
    assert result["domains"]["lectures"]["status"] == "ok"
    assert result["domains"]["assignments"]["status"] == "ok"
    assert read_catalog(catalog_path(tmp_path))["lectures"] == [_expected_row("lectures", IDS[0], 1)]
    assert read_domain_catalog("assignments", domain_catalog_path("assignments", tmp_path))["assignments"] == [
        _expected_row("assignments", IDS[0], 1)
    ]
