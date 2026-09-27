"""CLI integration and selected-detail handler tests for fetch."""

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl import cli
from campusctl.commands import assignments, notices
from campusctl.envelope import CampusError, make_envelope
from campusctl.presentation import render_human


def _setup_catalogs(root: Path) -> None:
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    assignment_data = {
        "schema_version": 1,
        "generated_at": "2026-09-25T12:00:00Z",
        "enrollment_state": "known",
        "courses": [{"id": "course-xyz", "label": "Course XYZ"}],
        "failed_courses": [],
        "assignments": [
            {
                "entity_id": "cnu_assignment:course-xyz:task-999",
                "task_id": "task-999",
                "title": "Assignment XYZ",
                "course": {"id": "course-xyz", "label": "Course XYZ"},
                "due_date": "2026-10-15",
                "is_submitted": False,
            }
        ],
    }
    (catalog_dir / "assignments.json").write_text(json.dumps(assignment_data), encoding="utf-8")

    notice_data = {
        "schema_version": 1,
        "generated_at": "2026-09-25T12:00:00Z",
        "enrollment_state": "known",
        "courses": [{"id": "course-xyz", "label": "Course XYZ"}],
        "failed_courses": [],
        "notices": [
            {
                "entity_id": "cnu_notice:course-xyz:2026-09-25:10",
                "title": "Notice XYZ",
                "course": {"id": "course-xyz", "label": "Course XYZ"},
                "date": "2026-09-25",
                "is_unread": True,
                "has_attachments": False,
                "view_count": 12,
            }
        ],
    }
    (catalog_dir / "notices.json").write_text(json.dumps(notice_data), encoding="utf-8")


def test_fetch_released_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "json")
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})
    _setup_catalogs(tmp_path)
    selected: list[tuple[str, str, Path | None]] = []

    async def package(_config: Any, _root: Path, row: dict[str, Any], **options: Any) -> dict[str, Any]:
        selected.append((row["entity_id"], row["course"]["id"], options["out"]))
        return {
            "entity_id": row["entity_id"],
            "path": str(tmp_path / "sources" / "package"),
            "content_path": str(tmp_path / "sources" / "package" / "content.md"),
            "manifest_path": str(tmp_path / "sources" / "package" / "package.json"),
            "completeness": "complete",
            "resources": [],
            "omitted_resources": [],
        }

    monkeypatch.setattr(assignments, "_fetch_assignment", package)
    monkeypatch.setattr(notices, "_fetch_notice", package)

    for domain, entity_id in (
        ("assignments", "cnu_assignment:course-xyz:task-999"),
        ("notices", "cnu_notice:course-xyz:2026-09-25:10"),
    ):
        assert cli.main([domain, "--help"]) == 0
        assert "fetch" in capsys.readouterr().out
        target = tmp_path / f"{domain}-package"
        assert cli.main([domain, "fetch", entity_id, "--out", str(target), "--json"]) == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["status"] == "ok"
        assert envelope["errors"] == []
        assert envelope["result"]["source_package"]["entity_id"] == entity_id
        assert selected[-1] == (entity_id, "course-xyz", target)

        assert cli.main([domain, "fetch", "1", "--json"]) == 2
        error = json.loads(capsys.readouterr().out)
        assert error["status"] == "user-action"
        assert error["errors"][0]["code"] == "entity-unknown"
        assert selected[-1] == (entity_id, "course-xyz", target)

    async def omitted(_config: Any, _root: Path, row: dict[str, Any], **_options: Any) -> dict[str, Any]:
        return {
            "entity_id": row["entity_id"],
            "path": str(tmp_path / "sources" / "package"),
            "content_path": str(tmp_path / "sources" / "package" / "content.md"),
            "completeness": "partial",
            "resources": [],
            "omitted_resources": [{"reason": "unverified-notice-attachment", "original_name": "appendix.pdf"}],
        }

    monkeypatch.setattr(notices, "_fetch_notice", omitted)
    assert cli.main(["notices", "fetch", "cnu_notice:course-xyz:2026-09-25:10", "--json"]) == 1
    partial = json.loads(capsys.readouterr().out)
    assert partial["status"] == "partial"
    assert partial["errors"][0]["code"] == "resource-omitted"
    assert partial["result"]["source_package"]["omitted_resources"][0]["reason"] == "unverified-notice-attachment"

    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    assert cli.main(["notices", "fetch", "cnu_notice:course-xyz:2026-09-25:10"]) == 1
    human = capsys.readouterr().out
    assert "Notice source: cnu_notice:course-xyz:2026-09-25:10" in human
    assert "Completeness: partial" in human
    assert "Omitted: unverified-notice-attachment" in human

    # Numeric material selections are not fetch IDs
    for num_id in ("1", "2", "42"):
        args_num_a = SimpleNamespace(assignments_command="fetch", entity_id=num_id, out=None, headless_override=None)
        with pytest.raises(CampusError) as exc_a:
            assignments.dispatch(args_num_a)
        assert exc_a.value.code == "entity-unknown"

        args_num_n = SimpleNamespace(notices_command="fetch", entity_id=num_id, out=None, headless_override=None)
        with pytest.raises(CampusError) as exc_n:
            notices.dispatch(args_num_n)
        assert exc_n.value.code == "entity-unknown"

    # Full selected IDs retain course binding
    captured_assignment_course: list[str] = []

    async def mock_fetch_assignment(_cfg: Any, _root: Any, row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured_assignment_course.append(row["course"]["id"])
        return {
            "entity_id": row["entity_id"],
            "completeness": "complete",
            "path": "/data/sources/assignment/hash/digest",
            "manifest_path": "/data/sources/assignment/hash/digest/package.json",
            "content_path": "/data/sources/assignment/hash/digest/content.md",
            "resources": [],
            "omitted_resources": [],
            "provenance": {
                "provider": "cnu",
                "course_id": row["course"]["id"],
                "source_ref": {
                    "origin": "https://dcs-learning.cnu.ac.kr",
                    "page_path": "/std/taskView",
                    "provider_native_id": row["task_id"],
                },
                "retrieved_at": "2026-09-25T12:00:00Z",
            },
        }

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_fetch_assignment)

    args_a = SimpleNamespace(
        assignments_command="fetch",
        entity_id="cnu_assignment:course-xyz:task-999",
        out=None,
        headless_override=None,
    )
    res_a, err_a = assignments.dispatch(args_a)
    assert err_a is None
    assert captured_assignment_course == ["course-xyz"]
    assert res_a["source_package"]["provenance"]["course_id"] == "course-xyz"

    captured_notice_course: list[str] = []

    async def mock_fetch_notice(_cfg: Any, _root: Any, row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured_notice_course.append(row["course"]["id"])
        return {
            "entity_id": row["entity_id"],
            "completeness": "complete",
            "path": "/data/sources/notice/hash/digest",
            "manifest_path": "/data/sources/notice/hash/digest/package.json",
            "content_path": "/data/sources/notice/hash/digest/content.md",
            "resources": [],
            "omitted_resources": [],
            "provenance": {
                "provider": "cnu",
                "course_id": row["course"]["id"],
                "source_ref": {
                    "origin": "https://dcs-learning.cnu.ac.kr",
                    "page_path": "/std/noticeDetail",
                    "provider_native_id": None,
                },
                "retrieved_at": "2026-09-25T12:00:00Z",
            },
        }

    monkeypatch.setattr(notices, "_fetch_notice", mock_fetch_notice)

    args_n = SimpleNamespace(
        notices_command="fetch",
        entity_id="cnu_notice:course-xyz:2026-09-25:10",
        out=None,
        headless_override=None,
    )
    res_n, err_n = notices.dispatch(args_n)
    assert err_n is None
    assert captured_notice_course == ["course-xyz"]
    assert res_n["source_package"]["provenance"]["course_id"] == "course-xyz"

    # CDP headless is rejected before either fetch starts.
    monkeypatch.setattr(
        "campusctl.config.load_config",
        lambda: {"browser": {"cdp_endpoint": "http://browser.invalid:9222"}},
    )
    args_hl_a = SimpleNamespace(
        assignments_command="fetch", entity_id="cnu_assignment:course-xyz:task-999", out=None, headless_override=True
    )
    with pytest.raises(CampusError) as exc_hl_a:
        assignments.dispatch(args_hl_a)
    assert exc_hl_a.value.code == "headless-unavailable"

    args_hl_n = SimpleNamespace(
        notices_command="fetch", entity_id="cnu_notice:course-xyz:2026-09-25:10", out=None, headless_override=True
    )
    with pytest.raises(CampusError) as exc_hl_n:
        notices.dispatch(args_hl_n)
    assert exc_hl_n.value.code == "headless-unavailable"
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})

    # Direct handler presentation
    envelope_a = make_envelope(status="ok", result=res_a)
    assert envelope_a["schema_version"] == 1
    assert envelope_a["status"] == "ok"
    assert envelope_a["result"]["source_package"]["entity_id"] == "cnu_assignment:course-xyz:task-999"

    envelope_n = make_envelope(status="ok", result=res_n)
    assert envelope_n["schema_version"] == 1
    assert envelope_n["status"] == "ok"
    assert envelope_n["result"]["source_package"]["entity_id"] == "cnu_notice:course-xyz:2026-09-25:10"

    import io

    stream_a = io.StringIO()
    render_human("assignments.fetch", envelope_a, stream_a, width=80)
    lines_a = stream_a.getvalue().splitlines()
    assert any("Assignment source: cnu_assignment:course-xyz:task-999" in line for line in lines_a)
    assert any("Package: /data/sources/assignment/hash/digest" in line for line in lines_a)
    assert any("Completeness: complete" in line for line in lines_a)

    stream_n = io.StringIO()
    render_human("notices.fetch", envelope_n, stream_n, width=80)
    lines_n = stream_n.getvalue().splitlines()
    assert any("Notice source: cnu_notice:course-xyz:2026-09-25:10" in line for line in lines_n)
    assert any("Package: /data/sources/notice/hash/digest" in line for line in lines_n)
    assert any("Completeness: complete" in line for line in lines_n)

    # Busy session
    async def mock_busy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CampusError("session-busy", "Browser session lock is occupied.", status="busy")

    monkeypatch.setattr(assignments, "_fetch_assignment", mock_busy)
    with pytest.raises(CampusError) as exc_busy:
        assignments.dispatch(args_a)
    assert exc_busy.value.code == "session-busy"
    assert exc_busy.value.status == "busy"


class _Page:
    def __init__(self) -> None:
        self.closed = False
        self.login_form = False
        self.cleared = 0
        self.context = None

    def is_closed(self) -> bool:
        return self.closed

    async def is_visible(self, selector: str) -> bool:
        from campusctl.providers.cnu.login import LOGIN_FORM_SELECTOR

        return self.login_form and selector == LOGIN_FORM_SELECTOR

    async def unroute_all(self, **_kwargs: Any) -> None:
        self.cleared += 1


class _Script:
    def __init__(self) -> None:
        self.page = _Page()
        self.opened = 0
        self.captured: list[str] = []
        self.built: list[str] = []
        self.authenticated = 0
        self.fail_at: dict[str, Any] = {}
        self.auth_error: Exception | None = None
        self.open_error: Exception | None = None
        self.hold_lock: Path | None = None


def _ids(domain: str) -> list[str]:
    if domain == "assignments":
        return [f"cnu_assignment:course-1:task-{number}" for number in (1, 2, 3, 4)]
    return [f"cnu_notice:course-1:2026-09-25:{number}" for number in (1, 2, 3, 4)]


def _catalog_row(domain: str, entity_id: str) -> dict[str, Any]:
    course = {"id": "course-1", "label": "Synthetic Course"}
    if domain == "assignments":
        return {
            "entity_id": entity_id,
            "task_id": entity_id.rsplit(":", 1)[-1],
            "title": "Synthetic assignment",
            "course": course,
            "due_date": None,
            "is_submitted": False,
        }
    return {
        "entity_id": entity_id,
        "legacy_key": f"Synthetic Course_2026-09-25_{entity_id.rsplit(':', 1)[-1]}",
        "title": "Synthetic notice",
        "course": course,
        "date": "2026-09-25",
        "kind": "notice",
        "is_unread": False,
        "has_attachments": False,
        "view_count": 1,
    }


def _write_domain_catalog(root: Path, domain: str, entity_ids: list[str]) -> None:
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": "2026-09-25T12:00:00Z",
        "enrollment_state": "known",
        "courses": [{"id": "course-1", "label": "Synthetic Course"}],
        "failed_courses": [],
        domain: [_catalog_row(domain, entity_id) for entity_id in entity_ids],
    }
    (catalog_dir / f"{domain}.json").write_text(json.dumps(payload), encoding="utf-8")


def _install_queue(monkeypatch: pytest.MonkeyPatch, script: _Script, domain: str, root: Path) -> None:
    command = assignments if domain == "assignments" else notices
    monkeypatch.setattr(command, "data_dir", lambda: root)
    monkeypatch.setattr("campusctl.config.load_config", lambda: {})

    @contextlib.asynccontextmanager
    async def open_session(*_args: Any, **_kwargs: Any) -> Any:
        script.opened += 1
        if script.open_error is not None:
            raise script.open_error
        if script.hold_lock is None:
            yield SimpleNamespace(page=script.page, mode="local")
            return
        from campusctl.lock import exclusive_lock

        with exclusive_lock(script.hold_lock):
            yield SimpleNamespace(page=script.page, mode="local")

    async def ensure_logged_in(*_args: Any, **_kwargs: Any) -> None:
        script.authenticated += 1
        if script.auth_error is not None:
            raise script.auth_error

    async def settle(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def capture(*args: Any) -> object:
        row = args[-1]
        entity_id = row["entity_id"]
        script.captured.append(entity_id)
        action = script.fail_at.get(entity_id)
        if action == "login-form":
            script.page.login_form = True
            raise RuntimeError("detail unavailable")
        if action == "closed":
            script.page.closed = True
            raise RuntimeError("browser closed")
        if action == "interrupt":
            raise KeyboardInterrupt
        package_codes = {"unsupported-media-type", "file-too-large", "policy-blocked"}
        if isinstance(action, Exception) and getattr(action, "code", None) not in package_codes:
            raise action
        return object()

    async def build(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        entity_id = kwargs["entity_id"]
        action = script.fail_at.get(entity_id)
        if isinstance(action, Exception):
            raise action
        destination = root / "published" / entity_id.replace(":", "_")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "content.md").write_text(entity_id, encoding="utf-8")
        partial = action == "partial"
        script.built.append(entity_id)
        return {
            "entity_id": entity_id,
            "completeness": "partial" if partial else "complete",
            "path": str(destination),
            "content_path": str(destination / "content.md"),
            "manifest_path": str(destination / "package.json"),
            "resources": [],
            "omitted_resources": [{"reason": "unsupported-media-type", "original_name": "clip.mp4"}] if partial else [],
        }

    capture_target = (
        "campusctl.providers.cnu.assignment_detail.capture_assignment_detail"
        if domain == "assignments"
        else "campusctl.providers.cnu.notice_detail.capture_notice_detail"
    )
    monkeypatch.setattr("campusctl.browser.open_session", open_session)
    monkeypatch.setattr("campusctl.providers.cnu.login.ensure_logged_in", ensure_logged_in)
    monkeypatch.setattr("campusctl.browser.settle_sso_popups", settle)
    monkeypatch.setattr(capture_target, capture)
    monkeypatch.setattr("campusctl.source_package.build_source_package", build)


def _fetch_args(domain: str, entity_ids: list[str], **extra: Any) -> SimpleNamespace:
    values = {f"{domain}_command": "fetch", "entity_ids": entity_ids, "out": extra.get("out")}
    values["headless_override"] = extra.get("headless")
    return SimpleNamespace(**values)


def _dispatch(domain: str, args: SimpleNamespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]:
    command = assignments if domain == "assignments" else notices
    return command.dispatch(args)


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_multi_fetch_queue_publishes_in_order_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, ids[:3])
    script = _Script()
    script.fail_at[ids[1]] = "partial"
    _install_queue(monkeypatch, script, domain, tmp_path)

    result, errors = _dispatch(domain, _fetch_args(domain, [ids[0], ids[0], ids[1], ids[2]]))
    assert script.opened == 1
    assert script.authenticated == 1
    assert script.captured == ids[:3]
    assert script.built == ids[:3]
    assert script.page.cleared == 3
    assert errors is not None
    assert [item["outcome"] for item in result["items"]] == ["completed", "partial", "completed"]
    assert [item["entity_id"] for item in result["items"]] == ids[:3]
    assert "reason_code" not in result["items"][0]
    assert result["items"][1]["reason_code"] == "resource-omitted"
    assert errors[0].code == "resource-omitted"
    published = tmp_path / "published"
    assert (published / ids[0].replace(":", "_") / "content.md").read_text(encoding="utf-8") == ids[0]
    assert (published / ids[2].replace(":", "_") / "content.md").read_text(encoding="utf-8") == ids[2]


@pytest.mark.parametrize("domain", ["assignments", "notices"])
@pytest.mark.parametrize(
    ("kind", "code", "status"),
    [
        ("identity", "entity-unknown", "user-action"),
        ("parse", "fetch-failed", "error"),
        ("attachment", "unsupported-media-type", "error"),
        ("timeout", "browser-timeout", "error"),
    ],
)
def test_item_failure_preserves_neighbors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str, kind: str, code: str, status: str
) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, ids[:3])
    script = _Script()
    if kind == "timeout":
        step = "opening the selected CNU task" if domain == "assignments" else "opening selected notice"
        script.fail_at[ids[1]] = CampusError(
            "browser-timeout", f"Timed out while {step}.", "Check the browser and try again.", "error"
        )
    else:
        message = {
            "identity": "Selected detail does not match.",
            "parse": "Detail response could not be verified.",
            "attachment": "Selected image type is not approved.",
        }[kind]
        script.fail_at[ids[1]] = CampusError(code, message, status=status)
    _install_queue(monkeypatch, script, domain, tmp_path)

    result, errors = _dispatch(domain, _fetch_args(domain, ids[:3]))
    assert script.captured == ids[:3]
    assert script.captured.count(ids[1]) == 1
    assert script.built == [ids[0], ids[2]]
    assert [item["outcome"] for item in result["items"]] == ["completed", "failed", "completed"]
    assert "source_package" not in result["items"][1]
    assert result["items"][1]["reason_code"] == code
    assert errors is not None and errors[0].code == code and errors[0].status == status
    published = tmp_path / "published"
    assert (published / ids[0].replace(":", "_")).is_dir()
    assert not (published / ids[1].replace(":", "_")).exists()
    assert (published / ids[2].replace(":", "_") / "content.md").read_text(encoding="utf-8") == ids[2]


@pytest.mark.parametrize("domain", ["assignments", "notices"])
@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("login-form", "lms-unavailable"),
        ("login-failed", "lms-unavailable"),
        ("login-action", "login-action-required"),
        ("closed", "browser-session-failed"),
        ("busy", "session-busy"),
        ("unavailable", "lms-unavailable"),
    ],
)
def test_session_failure_stops_queue_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str, kind: str, code: str
) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, ids)
    script = _Script()
    if kind == "login-form":
        script.fail_at[ids[1]] = "login-form"
    elif kind == "login-failed":
        script.fail_at[ids[1]] = CampusError(
            "login-failed", "The LMS rejected the configured credentials.", status="user-action"
        )
    elif kind == "login-action":
        script.fail_at[ids[1]] = CampusError("login-action-required", "Terms are required.", status="user-action")
    elif kind == "closed":
        script.fail_at[ids[1]] = "closed"
    elif kind == "busy":
        script.fail_at[ids[1]] = CampusError("session-busy", "Browser session lock is occupied.", status="busy")
    else:
        script.fail_at[ids[1]] = CampusError("lms-unavailable", "The LMS could not be reached.", status="error")
    _install_queue(monkeypatch, script, domain, tmp_path)

    result, errors = _dispatch(domain, _fetch_args(domain, ids))
    assert script.captured == ids[:2]
    assert script.captured.count(ids[1]) == 1
    assert ids[2] not in script.captured and ids[3] not in script.captured
    assert [item["outcome"] for item in result["items"]] == ["completed", "failed", "not-started", "not-started"]
    assert result["items"][1]["reason_code"] == code
    assert "source_package" not in result["items"][1]
    assert "reason_code" not in result["items"][2]
    assert errors is not None and errors[-1].code == code
    if kind == "login-failed":
        assert "rejected" not in errors[-1].message
    assert (tmp_path / "published" / ids[0].replace(":", "_") / "content.md").is_file()


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_fetch_preflight_rejections_do_not_open_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    ids = _ids(domain)
    script = _Script()
    _install_queue(monkeypatch, script, domain, tmp_path)
    command = assignments if domain == "assignments" else notices

    with pytest.raises(CampusError) as missing:
        _dispatch(domain, _fetch_args(domain, ids[:2]))
    assert missing.value.code == "catalog-missing"
    assert script.opened == 0

    _write_domain_catalog(tmp_path, domain, ids[:2])
    with pytest.raises(CampusError) as unknown:
        _dispatch(domain, _fetch_args(domain, [ids[0], "*", "course-1"]))
    assert unknown.value.code == "entity-unknown"
    assert "*" not in unknown.value.message
    assert script.opened == 0

    with pytest.raises(CampusError) as usage:
        _dispatch(domain, _fetch_args(domain, ids[:2], out=tmp_path / "package"))
    assert usage.value.code == "usage-error"
    assert script.opened == 0

    monkeypatch.setattr(
        "campusctl.config.load_config",
        lambda: {"browser": {"cdp_endpoint": "http://browser.invalid:9222", "headless": True}},
    )
    args = SimpleNamespace(
        **{f"{domain}_command": "fetch", "entity_ids": [ids[0]], "out": None, "headless_override": None}
    )
    with pytest.raises(CampusError) as headless:
        command.dispatch(args)
    assert headless.value.code == "headless-unavailable"
    assert script.opened == 0


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_duplicate_fetch_id_opens_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, [ids[0]])
    script = _Script()
    _install_queue(monkeypatch, script, domain, tmp_path)

    result, errors = _dispatch(domain, _fetch_args(domain, [ids[0], ids[0]]))
    assert errors is None
    assert "items" not in result
    assert result["source_package"]["entity_id"] == ids[0]
    assert script.opened == 1
    assert script.captured == [ids[0]]


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_fetch_interruption_releases_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str) -> None:
    from campusctl.lock import exclusive_lock

    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, ids[:2])
    script = _Script()
    script.hold_lock = tmp_path / "session.lock"
    script.fail_at[ids[1]] = "interrupt"
    _install_queue(monkeypatch, script, domain, tmp_path)

    with pytest.raises(KeyboardInterrupt):
        _dispatch(domain, _fetch_args(domain, ids[:2]))
    assert script.captured == ids[:2]
    assert script.captured.count(ids[1]) == 1
    with exclusive_lock(script.hold_lock):
        assert script.opened == 1


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_session_startup_failure_has_no_item_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, ids[:2])
    script = _Script()
    script.auth_error = CampusError(
        "login-failed", "The LMS rejected the configured credentials.", status="user-action"
    )
    _install_queue(monkeypatch, script, domain, tmp_path)

    with pytest.raises(CampusError) as caught:
        _dispatch(domain, _fetch_args(domain, ids[:2]))
    assert caught.value.code == "login-failed"
    assert script.captured == []
    assert script.built == []


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_fetch_batch_status_follows_first_failure_and_published_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], domain: str
) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, ids)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "json")
    script = _Script()
    script.fail_at[ids[0]] = CampusError("entity-unknown", "Selected detail does not match.", status="user-action")
    script.fail_at[ids[1]] = CampusError("fetch-failed", "Detail response could not be verified.", status="error")
    script.fail_at[ids[2]] = CampusError("session-busy", "Browser session lock is occupied.", status="busy")
    _install_queue(monkeypatch, script, domain, tmp_path)

    assert cli.main([domain, "fetch", *ids, "--json"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["status"] == "user-action"
    assert [item["outcome"] for item in envelope["result"]["items"]] == ["failed", "failed", "failed", "not-started"]
    assert [item["code"] for item in envelope["errors"]] == ["entity-unknown", "fetch-failed", "session-busy"]
    assert all("source_package" not in item for item in envelope["result"]["items"])

    script.fail_at = {ids[1]: CampusError("fetch-failed", "Detail response could not be verified.", status="error")}
    script.captured.clear()
    script.built.clear()
    assert cli.main([domain, "fetch", *ids[:3], "--json"]) == 1
    published = json.loads(capsys.readouterr().out)
    assert published["status"] == "partial"
    assert published["result"]["items"][0]["outcome"] == "completed"
    assert published["result"]["items"][0]["source_package"]["entity_id"] == ids[0]


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_real_parser_keeps_one_id_shape_and_renders_multi_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], domain: str
) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, ids[:2])
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "json")
    script = _Script()
    _install_queue(monkeypatch, script, domain, tmp_path)

    assert cli.main([domain, "fetch", ids[0], "--json"]) == 0
    single = json.loads(capsys.readouterr().out)
    assert single["status"] == "ok"
    assert single["errors"] == []
    assert single["result"]["source_package"]["entity_id"] == ids[0]
    assert "items" not in single["result"]

    assert cli.main([domain, "fetch", ids[0], ids[1], ids[0], "--json"]) == 0
    batch = json.loads(capsys.readouterr().out)
    assert [item["entity_id"] for item in batch["result"]["items"]] == ids[:2]
    assert script.captured.count(ids[0]) == 2

    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    assert cli.main([domain, "fetch", ids[0], ids[1]]) == 0
    human = capsys.readouterr().out
    assert ids[0] in human and ids[1] in human
    assert "Outcome: completed" in human
    assert not human.startswith("{")


@pytest.mark.parametrize("domain", ["assignments", "notices"])
def test_local_headless_fetch_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], domain: str
) -> None:
    ids = _ids(domain)
    _write_domain_catalog(tmp_path, domain, [ids[0]])
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "json")
    seen: list[bool] = []
    command = assignments if domain == "assignments" else notices

    async def package(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["headless"])
        return {
            "entity_id": ids[0],
            "completeness": "complete",
            "path": str(tmp_path / "package"),
            "content_path": str(tmp_path / "package" / "content.md"),
            "manifest_path": str(tmp_path / "package" / "package.json"),
            "resources": [],
            "omitted_resources": [],
        }

    monkeypatch.setattr(command, "_fetch_assignment" if domain == "assignments" else "_fetch_notice", package)
    monkeypatch.setattr("campusctl.config.load_config", lambda: {"browser": {"headless": True}})
    assert cli.main([domain, "fetch", ids[0], "--json"]) == 0
    assert cli.main(["--headless", domain, "fetch", ids[0], "--json"]) == 0
    assert cli.main(["--headed", domain, "fetch", ids[0], "--json"]) == 0
    assert seen == [True, True, False]
    capsys.readouterr()

    monkeypatch.setattr(
        "campusctl.config.load_config",
        lambda: {"browser": {"cdp_endpoint": "http://browser.invalid:9222", "headless": True}},
    )
    assert cli.main(["--headless", domain, "fetch", ids[0], "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["errors"][0]["code"] == "headless-unavailable"
    assert seen == [True, True, False]
