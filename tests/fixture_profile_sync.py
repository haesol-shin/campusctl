"""Run a synthetic lecture sync against a loopback server in a real headed browser.

Only for local fixture measurements; no LMS host or credentials are contacted.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from campusctl.profiling import SpanRecorder
from campusctl.providers.cnu import login
from campusctl.providers.cnu import sync as lectures
from campusctl.sync import run_sync


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/std/myLecture":
            body = (
                '<a data-act="moveLecture" data-courseid="course.invalid" '
                'data-coursenm="Fixture course" onclick="location.href=\'/std/room\'">Course</a>'
            )
        elif self.path == "/std/room":
            body = '<a href="/std/course">Lectures</a>'
        elif self.path == "/std/course":
            body = (
                '<div class="learningRow" id="lecture.invalid" data-moduletype="LV" '
                'data-state="F" data-openyn="Y" data-weekno="1" data-seqno="1">'
                '<span data-act="titleDetailContents">Synthetic lesson</span></div>'
            )
        else:
            self.send_error(404)
            return
        content = ("<!doctype html><html><body>" + body + "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ["CAMPUSCTL_DATA_DIR"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/std/myLecture"

    async def fixture_login(page: object, _config: dict, **_kwargs: object) -> None:
        await page.goto(url)
        await page.wait_for_selector('[data-act="moveLecture"]')

    login.ensure_logged_in = fixture_login
    lectures.ensure_logged_in = fixture_login
    recorder = SpanRecorder(enabled=args.profile, scope=("lectures",))
    try:
        with sync_playwright() as playwright:
            executable = playwright.chromium.executable_path
        config = {"browser": {"executable_path": executable}}
        result, errors = run_sync(config, root, ("lectures",), None, headless=False, profile=recorder)
        codes = [error.code for error in errors] if isinstance(errors, list) else [errors.code] if errors else []
        print(json.dumps({"status": "ok" if not codes else "partial", "result": result, "errors": codes}))
    finally:
        recorder.finish(outcome="failed" if "errors" not in locals() or errors else "ok")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
