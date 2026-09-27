from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from campusctl.browser import open_session
from campusctl.browser_options import (
    HEADLESS_SUPPORT,
    operation_headless_supported,
    preflight_browser_mode,
    resolve_headless,
)
from campusctl.config import load_config, validate_config
from campusctl.envelope import CampusError


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, [], {}])
def test_config_rejects_non_boolean_headless(tmp_path: Path, value: object) -> None:
    with pytest.raises(CampusError) as caught:
        validate_config({"browser": {"headless": value}}, path=tmp_path / "config.toml")
    assert caught.value.code == "config-invalid"
    assert "browser.headless" in caught.value.message


def test_toml_true_false_and_absent_default(tmp_path: Path) -> None:
    for contents, expected in (
        ("", False),
        ("[browser]\nheadless = true\n", True),
        ("[browser]\nheadless = false\n", False),
    ):
        path = tmp_path / "config.toml"
        path.write_text(contents, encoding="utf-8")
        assert resolve_headless(load_config(path)) is expected
    assert resolve_headless({"browser": {"headless": True}}, override=False) is False
    assert resolve_headless({"browser": {"headless": False}}, override=True) is True


def test_pending_operations_refuse_before_lock_or_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import browser

    assert HEADLESS_SUPPORT["materials.download"] is True
    monkeypatch.setattr(browser, "_playwright_manager", lambda: pytest.fail("browser started"))
    data_dir = tmp_path / "data"
    for operation in (
        "lectures.sync",
        "assignments.sync",
        "notices.sync",
        "materials.sync",
        "materials.download",
        "assignments.fetch",
        "notices.fetch",
    ):
        assert operation_headless_supported(operation)
        assert preflight_browser_mode({"browser": {"headless": True}}, operation) is True
    pending = ("lectures.play",)
    for operation in (*pending, "unregistered.operation"):
        assert not operation_headless_supported(operation)
        with pytest.raises(CampusError) as caught:
            preflight_browser_mode({"browser": {"headless": True}}, operation)
        assert caught.value.code == "headless-unavailable"
        assert caught.value.status == "user-action"
        with pytest.raises(CampusError) as caught:
            asyncio.run(open_session({}, data_dir=data_dir, headless=True, operation=operation).__aenter__())
        assert caught.value.code == "headless-unavailable"
        with pytest.raises(CampusError) as caught:
            asyncio.run(
                open_session({"browser": {"headless": True}}, data_dir=data_dir, operation=operation).__aenter__()
            )
        assert caught.value.code == "headless-unavailable"
        assert not data_dir.exists()
    assert preflight_browser_mode({"browser": {"headless": True}}, "lectures.play", override=False) is False


def test_approved_download_mode_still_rejects_cdp_before_lock(tmp_path: Path) -> None:
    config = {"browser": {"cdp_endpoint": "http://browser.invalid:9222", "headless": True}}
    assert operation_headless_supported("materials.download")
    with pytest.raises(CampusError) as caught:
        asyncio.run(open_session(config, data_dir=tmp_path / "data", operation="materials.download").__aenter__())
    assert caught.value.code == "headless-unavailable"
    assert "CDP" in caught.value.message
    assert not (tmp_path / "data").exists()


@pytest.mark.skipif(os.name == "nt", reason="no-display assertion is POSIX-only")
def test_actual_local_headless_chromium_without_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.async_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as manager:
        if not Path(manager.chromium.executable_path).is_file():
            pytest.skip("local Playwright Chromium not installed")

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    data_dir = tmp_path / "data"

    async def scenario() -> None:
        async with open_session(
            {"browser": {"headless": True}}, data_dir=data_dir, operation="materials.download"
        ) as session:
            await session.page.goto("data:text/html,<title>Synthetic</title><main>Local fixture</main>")
            assert await session.page.title() == "Synthetic"
            assert await session.page.locator("main").inner_text() == "Local fixture"
            with pytest.raises(CampusError) as caught:
                async with open_session({}, data_dir=data_dir, headless=True):
                    pass
            assert caught.value.code == "session-busy"
        async with open_session(
            {"browser": {"headless": True}}, data_dir=data_dir, operation="materials.download"
        ) as session:
            assert await session.page.evaluate("2 + 3") == 5

    asyncio.run(scenario())


def test_doctor_exposes_fetch_headless_candidate_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path / "data"))
    from campusctl.cli import doctor_result

    result, _error = doctor_result()
    support = result["browser"]["headless_support"]
    assert support["assignments.fetch"] is True
    assert support["notices.fetch"] is True
    assert support["lectures.play"] is False


@pytest.mark.skipif(os.name == "nt", reason="no-display assertion is POSIX-only")
def test_headless_fetch_publishes_synthetic_details_without_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("playwright.async_api")
    import json
    from urllib.parse import urlsplit

    from playwright.sync_api import sync_playwright

    from campusctl.providers.cnu import assignment_detail, login, notice_detail
    from campusctl.providers.cnu.assignment_detail import capture_assignment_detail
    from campusctl.providers.cnu.notice_detail import capture_notice_detail
    from campusctl.source_package import build_source_package

    with sync_playwright() as manager:
        if not Path(manager.chromium.executable_path).is_file():
            pytest.skip("local Playwright Chromium not installed")

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    origin = "https://lms.invalid"
    monkeypatch.setattr(login, "MY_LECTURE_URL", origin + "/std/myLecture")
    monkeypatch.setattr(assignment_detail, "MY_LECTURE_URL", origin + "/std/myLecture")
    monkeypatch.setattr(assignment_detail, "_ORIGIN", origin)
    monkeypatch.setattr(notice_detail, "_ORIGIN", origin)
    monkeypatch.setattr(notice_detail, "MY_LECTURE_URL", origin + "/std/myLecture")

    topbar = (
        "<div id='topbarCurrentLecture'>Synthetic Course</div>"
        "<div id='topbarLectureDropdown'><a data-act='changeLecture' data-courseid='course-1'>Synthetic Course</a></div>"
    )
    pages = {
        "/std/myLecture": "<a data-act='moveLecture' data-courseid='course-1' href='/std/lecture'>Synthetic Course</a>",
        "/std/lecture": topbar
        + "<a href='/std/course'>Course</a><a href='/std/task'>Tasks</a><a href='/std/notice'>Notices</a>",
        "/std/task": (
            topbar
            + "<a href='/std/course'>Course</a>"
            + "<table id='table_list'><tbody id='tbody'><tr><td>"
            + "<a data-act='detail' data-id='TB_L_REPORT101' href='/std/taskView'>Open</a>"
            + "</td></tr></tbody></table>"
        ),
        "/std/taskView": (
            "<div data-id='TB_L_REPORT101'><div class='card-body'><h4>Synthetic assignment</h4>"
            "<p>Selected assignment brief</p></div></div>"
            "<script>fetch('/api/v1/task/detail',{method:'POST',body:'{}'});"
            "fetch('/api/v1/task/stdDetail',{method:'POST',body:'{}'});</script>"
        ),
        "/std/notice": (
            topbar + "<table><tbody id='table-body'><tr><td>"
            "<a href='noticeDetail?no=TB_L_BOARDITEM7001&curPage=1'>Synthetic notice</a>"
            "</td></tr></tbody></table>"
            "<script>fetch('/api/v1/board/notice/list/top',{method:'POST',body:'{}'});"
            "fetch('/api/v1/board/notice/list',{method:'POST',body:'{}'});</script>"
        ),
        "/std/noticeDetail": (
            "<h1>Synthetic notice</h1>"
            "<script>fetch('/api/v1/board/notice/info',{method:'POST',body:'{}'});"
            "fetch('/api/v1/board/cmt/list',{method:'POST',body:'{}'});</script>"
        ),
    }
    posts = {
        "/api/v1/task/detail": {
            "header": {"code": 200},
            "body": {"report_no": "TB_L_REPORT101", "course_id": "course-1", "contents_id": "TB_L_REPORT101"},
        },
        "/api/v1/task/stdDetail": {
            "header": {"code": 200},
            "body": {"report_no": "TB_L_REPORT101", "course_id": "course-1"},
        },
        "/api/v1/board/notice/list/top": {"header": {"code": 200}, "body": {"list": []}},
        "/api/v1/board/notice/list": {
            "header": {"code": 200},
            "body": {
                "list": [
                    {
                        "course_id": "course-1",
                        "delete_yn": "N",
                        "boarditem_no": "TB_L_BOARDITEM7001",
                        "boarditem_title": "Synthetic notice",
                        "row_idx": 1,
                        "insert_dt": "2026-09-25",
                        "insert_dt_addtime": "2026-09-25 09:00",
                        "boarditem_viewcnt": 1,
                        "file_yn": 0,
                        "writeruser_name": "Synthetic",
                    }
                ]
            },
        },
        "/api/v1/board/notice/info": {
            "header": {"code": 200},
            "body": {
                "boarditem_no": "TB_L_BOARDITEM7001",
                "course_id": "course-1",
                "boarditem_title": "Synthetic notice",
                "boarditem_content": "<p>Selected notice text</p>",
                "attach_file_list": [],
            },
        },
        "/api/v1/board/cmt/list": {"header": {"code": 200}, "body": {"list": []}},
    }

    async def install(page: object) -> None:
        async def fulfill(route: object) -> None:
            request = route.request
            parsed = urlsplit(request.url)
            if parsed.scheme != "https" or parsed.hostname != "lms.invalid":
                await route.abort()
                return
            if request.method == "POST":
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(posts[parsed.path]),
                )
                return
            await route.fulfill(status=200, content_type="text/html", body=pages[parsed.path])

        await page.route("https://lms.invalid/**", fulfill)

    async def scenario() -> None:
        assignment = {
            "entity_id": "cnu_assignment:course-1:TB_L_REPORT101",
            "task_id": "TB_L_REPORT101",
            "course": {"id": "course-1", "label": "Synthetic Course"},
        }
        notice = {
            "entity_id": "cnu_notice:course-1:2026-09-25:1",
            "legacy_key": "Synthetic Course_2026-09-25_1",
            "title": "Synthetic notice",
            "date": "2026-09-25",
            "native_id": "TB_L_BOARDITEM7001",
            "has_attachments": False,
            "status": None,
            "is_unread": None,
            "course": {"id": "course-1", "label": "Synthetic Course"},
        }
        async with open_session(
            {"browser": {"headless": True}}, data_dir=tmp_path / "assignment", operation="assignments.fetch"
        ) as session:
            await install(session.page)
            await session.page.goto(origin + "/std/myLecture")
            snapshot = await capture_assignment_detail(session.page, {}, assignment)
            package = await build_source_package(
                session.page,
                snapshot,
                entity_id=assignment["entity_id"],
                kind="assignment",
                course_id="course-1",
                course_label="Synthetic Course",
                root=tmp_path / "assignment",
            )
        content = Path(package["content_path"]).read_text(encoding="utf-8")
        assert package["entity_id"] == assignment["entity_id"]
        assert "Selected assignment brief" in content
        assert package["provenance"]["source_ref"]["origin"] == origin
        assert package["provenance"]["course_id"] == "course-1"

        async with open_session(
            {"browser": {"headless": True}}, data_dir=tmp_path / "notice", operation="notices.fetch"
        ) as session:
            await install(session.page)
            snapshot = await capture_notice_detail(session.page, notice)
            package = await build_source_package(
                session.page,
                snapshot,
                entity_id=notice["entity_id"],
                kind="notice",
                course_id="course-1",
                course_label="Synthetic Course",
                root=tmp_path / "notice",
            )
        content = Path(package["content_path"]).read_text(encoding="utf-8")
        assert package["entity_id"] == notice["entity_id"]
        assert "Synthetic notice" in content
        assert "Selected notice text" in content
        assert package["provenance"]["source_ref"]["origin"] == origin
        manifest = json.loads(Path(package["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["entity_id"] == notice["entity_id"]
        assert manifest["kind"] == "notice"

    asyncio.run(scenario())
