"""Small synthetic starting corpus; later neutral changes add their affected paths here."""

import json
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import NotRequired, TypedDict
from unittest.mock import patch


class Case(TypedDict):
    name: str
    argv: list[str]
    width: int
    expected: int
    fixture: NotRequired[str]
    env: NotRequired[dict[str, str]]
    input: NotRequired[str]
    stdin_tty: NotRequired[bool]
    stdout_tty: NotRequired[bool]
    trace_publication: NotRequired[bool]
    fetch_fake: NotRequired[dict]
    sync_shape: NotRequired[str]


def _lecture_row(entity_id: str, title: str, course_id: str, label: str) -> dict:
    return {
        "entity_id": entity_id,
        "course": {"id": course_id, "label": label},
        "kind": "lecture",
        "title": title,
        "week": "1",
        "sequence": "1",
        "progress_text": None,
        "duration_minutes": None,
        "available_from": None,
        "due_date": "2026-03-04T05:06:00",
        "late_until": None,
        "media": "video",
        "attendance_counted": None,
        "open": True,
        "completion": "incomplete",
        "provider_state": "N",
    }


def _write_catalogs(root: Path, fixture: str) -> None:
    catalog = root / "data" / "catalog" / "lectures.json"
    catalog.parent.mkdir()
    if fixture == "layout":
        _write_layout_catalogs(root)
        return
    if fixture.startswith("progress-"):
        _write_progress_catalog(catalog)
        return
    courses = [{"course_id": "course.invalid", "label": "Synthetic course with a long display label", "class_no": None}]
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-generation",
                "courses": courses if fixture == "filled" else [],
                "lectures": [],
            }
        ),
        encoding="utf-8",
    )


def _write_layout_catalogs(root: Path) -> None:
    # Synthetic wide text and a combining mark, not a real course name.
    short_label = "\uc790\ub8cc"
    long_label = "\uc790\u0301" + ("\uad6c\uc870" * 30)
    short_id = "s"
    wide_id = "wide.invalid"
    long_id = "course-" + ("x" * 70) + ".invalid"
    courses = [
        {"course_id": short_id, "label": short_label, "class_no": None},
        {"course_id": wide_id, "label": long_label, "class_no": None},
        {"course_id": long_id, "label": "\uc9e7\uc740", "class_no": None},
    ]
    long_entity = "cnu_lecture:wide.invalid:" + ("row-" * 10) + "end"
    lectures = [
        _lecture_row("cnu_lecture:s:r", "\uc9e7\uc740", short_id, short_label),
        _lecture_row(long_entity, "\uac15\u0301\uc758" + ("\uc601\uc0c1" * 20), wide_id, long_label),
    ]
    (root / "data" / "catalog" / "lectures.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-layout",
                "courses": courses,
                "lectures": lectures,
            }
        ),
        encoding="utf-8",
    )
    notices = root / "data" / "catalog" / "notices.json"
    notices.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-notices",
                "enrollment_state": "known",
                "courses": [{"course_id": short_id, "label": long_label, "class_no": None}],
                "failed_courses": [],
                "notices": [
                    {
                        "entity_id": "n1",
                        "course": {"id": short_id, "label": long_label},
                        "title": "\uacf5\u0301\uc9c0" + ("\uc0ac\ud56d" * 20),
                        "date": "2026-01-02",
                        "author": "\uad50\uc218",
                        "view_count": 1,
                        "has_attachments": False,
                        "is_unread": True,
                    },
                    {
                        "entity_id": "cnu_notice:" + ("id-" * 16) + "end.invalid",
                        "course": {"id": short_id, "label": short_label},
                        "title": "\uc9e7\uc740",
                        "date": "2026-01-03",
                        "author": "\uc870\uad50",
                        "view_count": 2,
                        "has_attachments": False,
                        "is_unread": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_layout_domain_catalogs(root, short_label, long_label, short_id)


def _write_layout_domain_catalogs(root: Path, short_label: str, long_label: str, short_id: str) -> None:
    from campusctl.domain_catalog import write_domain_catalog
    from campusctl.providers.cnu.assignments import parse_assignment_rows

    course = {"course_id": short_id, "label": long_label, "class_no": None}
    data = root / "data" / "catalog"
    rows = parse_assignment_rows(
        [{"task_id": "TB_L_REPORT1", "title": "\uacf5\u0301\uc9c0" + ("\uacfc\uc81c" * 12), "due_date": "2026-03-04"}],
        course,
    )
    write_domain_catalog(
        "assignments",
        {"generated_at": "2026-01-02T03:04:05Z", "courses": [course], "assignments": rows},
        data / "assignments.json",
    )
    material = {
        "entity_id": "cnu_lms_material:s:file-1",
        "course": {"id": short_id, "label": long_label},
        "archive_entry": {"board_item_id": "board-1", "title": "\uc790\u0301" + ("\uc790\ub8cc" * 16)},
        "file_id": "file-1",
        "display_name": "\uc608\uc2dc.pdf",
        "filename": "\uc608\uc2dc.pdf",
        "media_type": None,
        "size_bytes": None,
        "downloadable": True,
        "unavailable_reason": None,
    }
    write_domain_catalog(
        "materials",
        {"generated_at": "2026-01-02T03:04:05Z", "courses": [course], "materials": [material]},
        data / "materials.json",
    )


def _write_p4b_catalogs(root: Path, fixture: str) -> None:
    from campusctl.domain_catalog import write_domain_catalog
    from campusctl.providers.cnu.assignments import parse_assignment_rows

    data = root / "data" / "catalog"
    data.mkdir(parents=True)
    course = {"course_id": "wide.invalid", "label": "\ud569\uc131 \uacfc\ubaa9", "class_no": None}
    warnings = fixture == "p4b-warnings"
    rows = (
        parse_assignment_rows([{"task_id": "TB_L_REPORT1", "title": "\uacfc\uc81c", "due_date": "2026-03-04"}], course)
        if warnings
        else []
    )
    payload = {
        "generated_at": "2026-01-02T03:04:05Z",
        "enrollment_state": "unknown" if warnings else "known",
        "courses": [course] if warnings else [],
        "failed_courses": (
            [
                {
                    "course_id": "missing.invalid",
                    "label": "\ub204\ub77d" + ("\uacfc\uc815" * 12),
                    "reason": "course-sync-failed",
                }
            ]
            if warnings
            else []
        ),
        "assignments": rows,
    }
    write_domain_catalog("assignments", payload, data / "assignments.json")
    if warnings:
        return
    for domain in ("notices", "materials"):
        write_domain_catalog(
            domain,
            {"generated_at": "2026-01-02T03:04:05Z", "courses": [], domain: []},
            data / f"{domain}.json",
        )


def _write_progress_catalog(catalog: Path) -> None:
    course_id = "course.invalid"
    label = "Synthetic course"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-progress",
                "courses": [{"course_id": course_id, "label": label, "class_no": None}],
                "lectures": [
                    _lecture_row(
                        "cnu_lecture:course.invalid:row-1",
                        "\uac15\u0301\uc758" + ("\uc601\uc0c1" * 20),
                        course_id,
                        label,
                    )
                ],
            }
        ),
        encoding="utf-8",
    )


def _install_progress_fakes(stack: ExitStack, actions: list, fixture: str) -> None:
    from contextlib import asynccontextmanager

    import campusctl.browser as browser_module
    from campusctl.envelope import CampusError
    from campusctl.providers.cnu import player as player_module

    @asynccontextmanager
    async def fake_open_session(*args, **kwargs):
        del args, kwargs
        actions.append(["session", "open", "replaced"])
        try:
            yield SimpleNamespace(page=SimpleNamespace(name="synthetic-page"))
        finally:
            actions.append(["session", "close", "replaced"])

    async def fake_play(page, config, lectures, *, speed=None, replay=False, progress=None):
        del page, config, speed, replay
        actions.append(["playback", "invoke", fixture])
        lecture = lectures[0]
        entity_id = lecture["entity_id"]
        if progress is not None:
            progress(
                {
                    "type": "started",
                    "index": 1,
                    "total": 1,
                    "entity_id": entity_id,
                    "title": lecture.get("title", ""),
                }
            )
            progress(
                {
                    "type": "position",
                    "entity_id": entity_id,
                    "position_seconds": 3661,
                    "duration_seconds": 3661,
                }
            )
            progress(
                {
                    "type": "position",
                    "entity_id": entity_id,
                    "position_seconds": 5,
                    "duration_seconds": 3661,
                }
            )
        if fixture == "progress-interrupt":
            raise KeyboardInterrupt
        if fixture == "progress-failure":
            if progress is not None:
                progress({"type": "finished", "entity_id": entity_id, "outcome": "failed"})
            return {"items": [{"entity_id": entity_id, "outcome": "failed"}]}, CampusError(
                "playback-failed",
                "Synthetic playback failed.",
                "Retry the synthetic lecture.",
                "error",
            )
        if progress is not None:
            progress({"type": "finished", "entity_id": entity_id, "outcome": "completed"})
        return {"items": [{"entity_id": entity_id, "outcome": "completed"}]}, None

    stack.enter_context(patch.object(browser_module, "open_session", fake_open_session))
    stack.enter_context(patch.object(player_module, "play_lectures", fake_play))


# Bare commands that run without a subcommand; courses and lectures fall through to an internal error.
_BARE_EXIT = {"doctor": 0, "setup": 0, "sync": 0, "status": 0, "courses": 1, "lectures": 1}


# Command-owner fixtures: name -> (config kind, keyring state, catalogs). Synthetic and offline.
_P3_FIXTURES = {
    "p3-healthy": ("keyring", "ready", "all"),
    "p3-no-config": ("none", "ready", "none"),
    "p3-invalid-config": ("invalid", "ready", "none"),
    "p3-no-credentials": ("keyring", "empty", "all"),
    "p3-backend-down": ("keyring", "down", "all"),
    "p3-no-browser": ("no-browser", "ready", "all"),
    "p3-headed": ("headed", "ready", "all"),
    "p3-partial": ("keyring", "ready", "partial"),
    "p3-no-catalogs": ("keyring", "ready", "none"),
    "p3-helper": ("helper", "ready", "all"),
    "p3-sync": ("keyring", "ready", "all"),
    "p3-sync-failure": ("keyring", "ready", "all"),
    "p3-sync-uncached": ("keyring", "ready", "none"),
    "p3-fetch": ("keyring", "ready", "fetch"),
    "p3-doctor-suppress": ("cdp", "ready", "none"),
}
_P3_HELPER_FIXTURES = {"p3-helper"}
_P3_COURSE = {"course_id": "course.invalid", "label": "Synthetic course", "class_no": None}


def _p3_config_text(kind: str, root: Path, executable: Path) -> str | None:
    if kind == "none":
        return None
    if kind == "invalid":
        return "provider = [unterminated\n"
    if kind == "cdp":
        return (
            'provider = "cnu"\n[account]\nusername = "synthetic-user"\n'
            '[credentials]\nprovider = "keyring"\n'
            '[browser]\ncdp_endpoint = "http://127.0.0.1:9"\n'
        )
    browser = str(root / "missing-browser") if kind == "no-browser" else str(executable)
    headless = "false" if kind == "headed" else "true"
    credentials = (
        f'provider = "command"\ncommand = [{json.dumps(str(root / "synthetic-helper"))}]\n'
        if kind == "helper"
        else 'provider = "keyring"\n'
    )
    return (
        'provider = "cnu"\n[account]\nusername = "synthetic-user"\n'
        f"[credentials]\n{credentials}"
        f"[browser]\nheadless = {headless}\nexecutable_path = {json.dumps(browser)}\n"
    )


def _write_p3_files(root: Path, fixture: str, executable: Path) -> None:
    from campusctl.domain_catalog import write_domain_catalog
    from campusctl.providers.cnu.assignments import parse_assignment_rows

    config_kind, _keyring, catalogs = _P3_FIXTURES[fixture]
    text = _p3_config_text(config_kind, root, executable)
    if text is not None:
        (root / "config" / "config.toml").write_text(text, encoding="utf-8")
    if catalogs == "none":
        return
    data = root / "data"
    _write_catalogs(root, "progress-catalog")
    course = {"id": _P3_COURSE["course_id"], "label": _P3_COURSE["label"]}
    count = 3 if catalogs == "fetch" else 1
    notices = [
        {
            "entity_id": f"cnu_notice:course.invalid:n{index}",
            "course": course,
            "title": "Synthetic notice",
            "date": "2026-01-02",
            "author": "Synthetic author",
            "view_count": 1,
            "has_attachments": False,
            "is_unread": True,
        }
        for index in range(1, count + 1)
    ]
    write_domain_catalog(
        "notices",
        {"generated_at": "2026-01-02T03:04:05Z", "courses": [_P3_COURSE], "notices": notices},
        data / "catalog" / "notices.json",
    )
    if catalogs == "partial":
        return
    tasks = [{"task_id": f"TB_L_REPORT{index}", "title": "Synthetic assignment"} for index in range(1, count + 1)]
    rows = parse_assignment_rows(tasks, {"course_id": _P3_COURSE["course_id"], "label": _P3_COURSE["label"]})
    write_domain_catalog(
        "assignments",
        {"generated_at": "2026-01-02T03:04:05Z", "courses": [_P3_COURSE], "assignments": rows},
        data / "catalog" / "assignments.json",
    )
    material = {
        "entity_id": "cnu_lms_material:course.invalid:file-1",
        "course": course,
        "archive_entry": {"board_item_id": "board-1", "title": "Synthetic archive"},
        "file_id": "file-1",
        "display_name": "example.pdf",
        "filename": "example.pdf",
        "media_type": None,
        "size_bytes": None,
        "downloadable": True,
        "unavailable_reason": None,
    }
    write_domain_catalog(
        "materials",
        {"generated_at": "2026-01-02T03:04:05Z", "courses": [_P3_COURSE], "materials": [material]},
        data / "catalog" / "materials.json",
    )


def _install_p3_fakes(
    stack: ExitStack, actions: list, fixture: str, state: dict, sync_shape: str | None = None
) -> None:
    import getpass
    import sys
    from contextlib import asynccontextmanager

    import campusctl.browser as browser_module
    import campusctl.sync as sync_module
    from campusctl.envelope import CampusError
    from campusctl.providers.cnu import courses as courses_module

    keyring_state = _P3_FIXTURES[fixture][1]
    stored = {"present": keyring_state == "ready"}

    class SyntheticBackend:
        priority = 5

    class SyntheticKeyring:
        @staticmethod
        def get_keyring():
            return SyntheticBackend()

        @staticmethod
        def get_password(service, username):
            actions.append(["keyring", "get", keyring_state])
            if keyring_state == "down":
                raise RuntimeError("synthetic backend failure")
            return "synthetic-secret" if stored["present"] else None

        @staticmethod
        def set_password(service, username, password):
            actions.append(["keyring", "set", bool(password)])
            if keyring_state == "down":
                raise RuntimeError("synthetic backend failure")
            stored["present"] = True

    def read_password(prompt=""):
        actions.append(["prompt", "password"])
        return sys.stdin.readline().rstrip("\n")

    @asynccontextmanager
    async def fake_open_session(*args, **kwargs):
        actions.append(["session", "open", "replaced"])
        try:
            yield SimpleNamespace(page=SimpleNamespace(name="synthetic-page"))
        finally:
            actions.append(["session", "close", "replaced"])

    async def fake_discover(page, config):
        actions.append(["roster", "discover"])
        return [_P3_COURSE]

    async def fake_sync_all(config, root, domains, course_id, *, headless, course_snapshot=None):
        actions.append(["sync", "collect", list(domains), course_id, headless, course_snapshot is not None])
        if fixture == "p3-sync-failure":
            raise CampusError("session-busy", "Synthetic busy session.", "Retry later.", "busy")
        if sync_shape is None:
            counts = {"courses": 1, domains[0]: 1}
            return {domain: sync_module.DomainOutcome(result=dict(counts)) for domain in domains}
        failed = [{"course_id": "wide.invalid", "label": "\ud569\uc131 \uacfc\ubaa9", "reason": "course-sync-failed"}]
        outcomes = {}
        for index, domain in enumerate(domains):
            result = {
                "courses": 2,
                domain: 1,
                "failed_courses": failed,
                "catalog": {"enrollment_state": "unknown", "generated_at": "2026-01-02T03:04:05Z"},
            }
            if domain == "lectures":
                result["incomplete"] = 1
            if sync_shape == "nested" and index == 1:
                outcomes[domain] = sync_module.DomainOutcome(
                    result=result,
                    errors=[
                        CampusError("course-sync-failed", "Synthetic course failed.", "Retry the course.", "partial")
                    ],
                    status="partial",
                )
            elif sync_shape == "nested" and index > 1:
                outcomes[domain] = sync_module.DomainOutcome(status="not-started")
            elif sync_shape == "partial":
                outcomes[domain] = sync_module.DomainOutcome(
                    result=result,
                    errors=[
                        CampusError("course-sync-failed", "Synthetic course failed.", "Retry the course.", "partial")
                    ],
                    status="partial",
                )
            else:
                outcomes[domain] = sync_module.DomainOutcome(result=result, status="ok")
        return outcomes

    stack.enter_context(patch.dict(sys.modules, {"keyring": SyntheticKeyring}))
    stack.enter_context(patch.object(getpass, "getpass", read_password))
    stack.enter_context(patch.object(browser_module, "open_session", fake_open_session))
    stack.enter_context(patch.object(courses_module, "discover_courses", fake_discover))
    stack.enter_context(patch.object(sync_module, "sync_all", fake_sync_all))
    state["keyring"] = keyring_state


def _fetch_error(spec: dict) -> Exception:
    from campusctl.envelope import CampusError

    if "plain" in spec:
        return RuntimeError(spec["plain"])
    return CampusError(spec["code"], spec["message"], spec["remediation"], spec["status"])


def _install_fetch_fakes(stack: ExitStack, actions: list, plan: dict) -> None:
    """Fake only the browser session, login, detail readers and the download network; the real packager runs."""
    from contextlib import asynccontextmanager

    import campusctl.browser as browser_module
    from campusctl.providers.cnu import assignment_detail, login, notice_detail
    from campusctl.source_package import DetailSnapshot, ResourceReference

    class Response:
        status = 200
        url = "https://lms.invalid/big.png"
        headers = {"Content-Type": "image/png", "Content-Length": str(10**12)}

        async def dispose(self) -> None:
            actions.append(["network", "dispose"])

    class Request:
        async def get(self, url: str, **kwargs: object) -> Response:
            actions.append(["network", "get", url])
            return Response()

    class Page:
        context = SimpleNamespace(request=Request())

        def is_closed(self) -> bool:
            actions.append(["page", "is-closed"])
            return False

        async def is_visible(self, selector: str) -> bool:
            actions.append(["page", "login-form-check"])
            return False

        async def unroute_all(self, behavior: str | None = None) -> None:
            actions.append(["page", "unroute-all"])

    @asynccontextmanager
    async def fake_open_session(config, **kwargs):
        operation = kwargs["operation"]
        actions.append(["session", "open", operation, kwargs["headless"]])
        if plan.get("open"):
            raise _fetch_error(plan["open"])
        try:
            yield SimpleNamespace(page=Page())
        finally:
            actions.append(["session", "close", operation])

    async def fake_login(page, config, **kwargs):
        actions.append(["login", "ensure"])
        if plan.get("login"):
            raise _fetch_error(plan["login"])

    async def fake_settle(session, *, domain=None, timeout=8.0):
        actions.append(["sso", "settle", domain])

    async def capture(domain: str, row: dict) -> DetailSnapshot:
        entity_id = row["entity_id"]
        actions.append(["capture", domain, entity_id])
        if entity_id in plan.get("capture", {}):
            raise _fetch_error(plan["capture"][entity_id])
        parts: list = ["Synthetic body\n"]
        if entity_id in plan.get("partial", []):
            # A resource kind the packager does not support is recorded as omitted, making the package partial.
            parts.append(ResourceReference("video", "https://lms.invalid/video", "clip.bin", None, "Clip", None, None))
        if entity_id in plan.get("package", {}):
            # The image download (network boundary) answers with an oversized length, which the real packager rejects.
            parts.append(ResourceReference("image", "https://lms.invalid/big.png", "big.png", None, "Big", None, None))
        return DetailSnapshot(f"https://lms.invalid/{entity_id}", entity_id, tuple(parts))

    async def fake_assignment_detail(page, config, row):
        return await capture("assignments", row)

    async def fake_notice_detail(page, row):
        return await capture("notices", row)

    stack.enter_context(patch.object(browser_module, "open_session", fake_open_session))
    stack.enter_context(patch.object(browser_module, "settle_sso_popups", fake_settle))
    stack.enter_context(patch.object(login, "ensure_logged_in", fake_login))
    stack.enter_context(patch.object(assignment_detail, "capture_assignment_detail", fake_assignment_detail))
    stack.enter_context(patch.object(notice_detail, "capture_notice_detail", fake_notice_detail))


def _fetch_cases() -> list[Case]:
    """Fetch, replay consent and materials publication through the one command pipeline."""

    def spec(code: str, message: str, status: str = "error") -> dict:
        return {"code": code, "message": message, "remediation": "Retry.", "status": status}

    unknown = spec("entity-unknown", "Synthetic unknown.", "user-action")
    plain = {"plain": "synthetic capture failure"}
    session = spec("browser-session-failed", "Synthetic session failure.")
    launch = spec("browser-launch-failed", "Synthetic launch failure.")
    login = spec("login-action-required", "Synthetic login action.", "user-action")
    oversized = True  # the download answers with an oversized length; the real packager raises file-too-large
    domains = {
        "notices": ("cnu_notice:course.invalid:n", "Timed out while opening notice board."),
        "assignments": ("cnu_assignment:course.invalid:TB_L_REPORT", "Timed out while opening a CNU course."),
    }
    result: list[Case] = []

    def add(name: str, argv: list[str], expected: int, mode: str, plan: dict | None = None, **extra) -> None:
        case: Case = {
            "name": f"p4a-{name}-{mode}",
            "argv": argv,
            "fixture": extra.pop("fixture", "p3-fetch"),
            "width": 80,
            "env": {"CAMPUSCTL_OUTPUT": mode},
            "expected": expected,
        }
        if plan is not None:
            case["fetch_fake"] = plan
        case.update(extra)
        result.append(case)

    for domain, (prefix, timeout_message) in domains.items():
        first, second, third = (f"{prefix}{index}" for index in (1, 2, 3))
        timeout = spec("browser-timeout", timeout_message)
        for mode in ("json", "human"):

            def fetch(name: str, ids: list[str], expected: int, plan: dict | None = None, **extra) -> None:
                add(f"{domain}-fetch-{name}", [domain, "fetch", *ids], expected, mode, plan or {}, **extra)  # noqa: B023

            fetch("single", [first], 0)
            fetch("batch", [second, first, second], 0)
            fetch("batch-untrimmed-id", [first, f" {second} "], 2)
            fetch("single-item-unknown", [first], 2, {"capture": {first: unknown}})
            fetch("batch-item-unknown", [first, second, third], 1, {"capture": {second: unknown}})
            fetch("single-item-plain", [first], 1, {"capture": {first: plain}})
            fetch("batch-item-plain", [first, second, third], 1, {"capture": {second: plain}})
            fetch("single-package-failure", [first], 2, {"package": {first: oversized}})
            fetch("batch-package-failure", [first, second, third], 1, {"package": {second: oversized}})
            fetch("single-partial", [first], 1, {"partial": [first]})
            fetch("batch-partial", [first, second], 1, {"partial": [second]})
            fetch("single-timeout", [first], 1, {"capture": {first: timeout}})
            fetch("batch-timeout", [first, second], 1, {"capture": {second: timeout}})
            fetch("single-open-failure", [first], 1, {"open": launch})
            fetch("batch-open-failure", [first, second], 1, {"open": launch})
            fetch("single-login-failure", [first], 2, {"login": login})
            fetch("batch-login-failure", [first, second], 2, {"login": login})
            fetch("single-session-error", [first], 1, {"capture": {first: session}})
            fetch("batch-session-error", [first, second, third], 1, {"capture": {second: session}})
            fetch("invalid-id-before-session", [first, f"{prefix}missing", second], 2)
            fetch("blank-id-before-session", [first, " "], 2)
    row = "cnu_lecture:course.invalid:row-1"
    for mode in ("json", "human"):
        add("materials-list", ["materials", "list"], 0, mode, fixture="p3-healthy", trace_publication=True)
    tty = {"stdin_tty": True, "stdout_tty": True}
    replay = ["lectures", "play", row, "--replay", "--speed", "1.0"]
    for name, answer, expected in (("yes", "y\n", 0), ("yes-word", "YES\n", 0), ("no", "n\n", 2), ("eof", "", 2)):
        add(f"replay-consent-{name}", replay, expected, "human", fixture="progress-success", input=answer, **tty)
    add("replay-consent-json", replay, 0, "json", fixture="progress-success")
    return result


def _command_owner_cases() -> list[Case]:
    """Precedence, doctor/status, registration and error-envelope cases for the command owners."""
    families = ("config", "doctor", "auth", "setup", "sync", "status", "courses", "lectures", "assignments")
    families += ("notices", "materials")
    definitions: list[tuple[str, list[str], str, int, dict]] = []

    def add(name: str, argv: list[str], fixture: str, expected: int, **extra) -> None:
        definitions.append((name, argv, fixture, expected, extra))

    # Precedence: flag over config over default, prompt only on interactive terminals, missing values.
    tty = {"tty": True}
    add("auth-status", ["auth", "status"], "p3-healthy", 0)
    add("auth-status-check", ["auth", "status", "--check"], "p3-healthy", 0)
    add("auth-status-empty", ["auth", "status"], "p3-no-credentials", 0)
    add("auth-status-empty-check", ["auth", "status", "--check"], "p3-no-credentials", 2)
    add("auth-status-backend-down", ["auth", "status", "--check"], "p3-backend-down", 2)
    add("auth-status-no-config", ["auth", "status"], "p3-no-config", 2)
    add("auth-status-helper", ["auth", "status"], "p3-helper", 0)
    add("auth-status-helper-check", ["auth", "status", "--check"], "p3-helper", 1)
    add("auth-set-no-tty", ["auth", "set"], "p3-healthy", 2)
    add("auth-set-helper-refused", ["auth", "set"], "p3-helper", 2, **tty)
    add("auth-set-prompt", ["auth", "set"], "p3-no-credentials", 0, input="synthetic-secret\n", **tty)
    add("auth-set-empty-prompt", ["auth", "set"], "p3-no-credentials", 2, input="\n", **tty)
    add("auth-set-backend-down", ["auth", "set"], "p3-backend-down", 2, input="synthetic-secret\n", **tty)
    add("config-init-flag-no-tty", ["config", "init", "--username", "synthetic-user"], "p3-no-config", 0)
    add("config-init-existing", ["config", "init", "--username", "synthetic-user"], "p3-healthy", 2)
    add("config-init-missing-username", ["config", "init"], "p3-no-config", 2)
    add("config-init-empty-flag", ["config", "init", "--username", ""], "p3-no-config", 2)
    add("config-init-flag-value-missing", ["config", "init", "--username"], "p3-no-config", 2)
    add("config-init-prompt-login", ["config", "init"], "p3-no-config", 0, input="synthetic-user\nn\n", **tty)
    add(
        "config-init-flag-skips-login-prompt",
        ["config", "init", "--username", "flag-user"],
        "p3-no-config",
        0,
        input="n\n",
        **tty,
    )
    add(
        "config-init-prompt-password",
        ["config", "init", "--username", "flag-user"],
        "p3-no-config",
        0,
        input="y\nsynthetic-secret\n",
        **tty,
    )
    add("config-init-existing-save", ["config", "init"], "p3-no-credentials", 0, input="y\nsynthetic-secret\n", **tty)
    add("config-init-existing-decline", ["config", "init"], "p3-no-credentials", 2, input="n\n", **tty)
    add("config-init-existing-ready", ["config", "init"], "p3-healthy", 2, **tty)
    add("config-init-invalid-existing", ["config", "init"], "p3-invalid-config", 2, **tty)
    add("doctor-flag-headless-over-config", ["--headless", "doctor"], "p3-headed", 0)
    add("doctor-flag-headed-over-config", ["--headed", "doctor"], "p3-healthy", 2)
    add("doctor-config-headless", ["doctor"], "p3-healthy", 0)
    add("doctor-config-headed-no-display", ["doctor"], "p3-headed", 2)
    add("doctor-display-env", ["doctor"], "p3-headed", 0, env={"DISPLAY": ":synthetic"})
    add("setup-no-tty-config", ["setup"], "p3-healthy", 0)
    add("setup-no-config", ["setup"], "p3-no-config", 0)
    add("setup-prompt-decline", ["setup"], "p3-no-credentials", 2, input="n\n", **tty)
    add("setup-invalid-config", ["setup"], "p3-invalid-config", 2)
    add("sync-headed-flag-over-config", ["--headed", "sync", "--only", "lectures"], "p3-sync", 0)
    add("sync-config-headless", ["sync", "--only", "assignments"], "p3-sync", 0)
    add("sync-subset", ["sync", "--only", "notices,materials"], "p3-sync", 0)
    add("sync-unknown-domain", ["sync", "--only", "bogus"], "p3-sync", 2)
    add("sync-only-value-missing", ["sync", "--only"], "p3-sync", 2)
    add("sync-no-config", ["sync"], "p3-no-config", 2)
    add("sync-failure", ["sync", "--only", "lectures"], "p3-sync-failure", 75)
    add("sync-cached-course", ["sync", "--only", "lectures", "--course", "course.invalid"], "p3-sync", 0)
    add("sync-course-discovery", ["sync", "--only", "lectures", "--course", "course.invalid"], "p3-sync-uncached", 0)
    add("profile-sync", ["--profile", "sync", "--only", "lectures"], "p3-sync", 0)
    add("profile-status-refused", ["--profile", "status"], "p3-healthy", 2)
    add("profile-courses-refresh", ["--profile", "courses", "list", "--refresh"], "p3-sync", 0)
    add("profile-list-without-refresh", ["--profile", "lectures", "list"], "p3-sync", 2)
    add("profile-domain-refresh", ["--profile", "notices", "list", "--refresh"], "p3-sync", 0)
    # Doctor and status: healthy, missing config, missing credentials and failing checks.
    for fixture in (
        "p3-healthy",
        "p3-no-config",
        "p3-invalid-config",
        "p3-no-credentials",
        "p3-backend-down",
        "p3-no-browser",
        "p3-headed",
        "p3-helper",
        "p3-no-catalogs",
    ):
        add(f"doctor-{fixture}", ["doctor"], fixture, 0 if fixture in {"p3-healthy", "p3-helper"} else 2)
    status_exit = {"p3-healthy": 0, "p3-partial": 1, "p3-no-catalogs": 2, "p3-no-config": 2}
    for fixture, expected in status_exit.items():
        add(f"status-{fixture}", ["status"], fixture, expected)
        add(f"status-course-{fixture}", ["status", "--course", "course.invalid"], fixture, expected)
    add("status-unknown-course", ["status", "--course", "missing-course"], "p3-healthy", 2)
    add("status-course-index", ["status", "--course", "1"], "p3-healthy", 2)
    add("status-course-value-missing", ["status", "--course"], "p3-healthy", 2)
    # Registration without scanning: every command family lists, dispatches and renders.
    listing = ("lectures", "courses", "assignments", "notices", "materials")
    for command in listing:
        add(f"{command}-list", [command, "list"], "p3-healthy", 0)
        # The courses command has no --course option, so it reports a usage error.
        course_exit = 2 if command == "courses" else 0
        add(f"{command}-list-course", [command, "list", "--course", "course.invalid"], "p3-healthy", course_exit)
        add(f"{command}-list-no-catalogs", [command, "list"], "p3-no-catalogs", 2)
        add(f"{command}-refresh", [command, "list", "--refresh"], "p3-sync", 0)
        add(f"{command}-refresh-failure", [command, "list", "--refresh"], "p3-sync-failure", 75)
        add(
            f"{command}-refresh-course",
            [command, "list", "--refresh", "--course", "course.invalid"],
            "p3-sync",
            course_exit,
        )
    add("lectures-list-all", ["lectures", "list", "--all"], "p3-healthy", 0)
    add(
        "assignments-refresh-discovery",
        ["assignments", "list", "--refresh", "--course", "course.invalid"],
        "p3-sync-uncached",
        2,  # discovery and refresh succeed, then the unwritten catalog is missing
    )
    row = "cnu_lecture:course.invalid:row-1"
    add("lectures-play-headless-refused", ["lectures", "play", row], "p3-healthy", 2)
    add("lectures-play-unknown-id", ["lectures", "play", "cnu_lecture:course.invalid:missing"], "p3-healthy", 2)
    add("lectures-play-replay-no-tty", ["lectures", "play", row, "--replay"], "p3-healthy", 2)
    add(
        "assignments-fetch-unknown-id",
        ["assignments", "fetch", "cnu_assignment:course.invalid:missing"],
        "p3-healthy",
        2,
    )
    add("notices-fetch-unknown-id", ["notices", "fetch", "cnu_notice:course.invalid:missing"], "p3-healthy", 2)
    add("materials-download-selection-required", ["materials", "download"], "p3-healthy", 2)
    add(
        "materials-download-unknown-id", ["materials", "download", "cnu_lms_material:course.invalid:x"], "p3-healthy", 2
    )
    add("materials-download-number", ["materials", "download", "1"], "p3-healthy", 2)
    # Error envelope: unknown subcommands, missing subcommands and usage errors per family.
    for family in families:
        add(f"usage-{family}-bare", [family], "p3-healthy", _BARE_EXIT.get(family, 2))
        add(f"usage-{family}-unknown-option", [family, "--bogus"], "p3-healthy", 2)
        add(f"usage-{family}-unknown-subcommand", [family, "bogus"], "p3-healthy", 2)
    # Bare courses resolves data_dir() before its TypeError; bare lectures does not.
    unresolvable = {"env": {"CAMPUSCTL_DATA_DIR": "~nonexistent-campusctl-review.invalid"}}
    add("bare-courses-unresolvable-data", ["courses"], "p3-healthy", 1, **unresolvable)
    add("bare-lectures-unresolvable-data", ["lectures"], "p3-healthy", 1, **unresolvable)
    # Coursework resolves data_dir() before subcommand and fetch ID checks; --profile refusal precedes both.
    for family in ("assignments", "notices", "materials"):
        add(f"bare-{family}-unresolvable-data", [family], "p3-healthy", 1, **unresolvable)
    for family in ("assignments", "notices"):
        add(f"{family}-fetch-blank-id-unresolvable-data", [family, "fetch", " "], "p3-healthy", 1, **unresolvable)
    add("materials-download-bare-unresolvable-data", ["materials", "download"], "p3-healthy", 1, **unresolvable)
    add("profile-refused-unresolvable-data", ["--profile", "assignments", "list"], "p3-healthy", 2, **unresolvable)
    add("usage-no-command", [], "p3-healthy", 2)
    add("usage-unknown-command", ["bogus"], "p3-healthy", 2)
    add("usage-abbreviated-subcommand", ["lectures", "li"], "p3-healthy", 2)
    add("usage-global-option-after-command", ["doctor", "--headless"], "p3-healthy", 2)
    add("usage-play-without-ids", ["lectures", "play", "--speed", "1.0"], "p3-healthy", 2)
    result: list[Case] = []
    for name, argv, fixture, expected, extra in definitions:
        # Prompts exist only on interactive terminals, which always use human output.
        for mode in ("human",) if extra.get("tty") else ("json", "human"):
            case: Case = {
                "name": f"owner-{name}-{mode}",
                "argv": argv,
                "fixture": fixture,
                "width": 80,
                "env": {"CAMPUSCTL_OUTPUT": mode, **extra.get("env", {})},
                "expected": expected,
            }
            if extra.get("tty"):
                case["stdin_tty"] = case["stdout_tty"] = True
            if "input" in extra:
                case["input"] = extra["input"]
            result.append(case)
    return result


def _p4b_cases() -> list[Case]:
    """Human renderer bytes at widths 48 and 100: lists, sync, doctor, status, picker."""
    specs = (
        ("courses-wide", ["courses", "list"], "layout", 0, {}),
        ("courses-empty", ["courses", "list"], "empty", 0, {}),
        ("lectures-wide", ["lectures", "list", "--all"], "layout", 0, {}),
        ("lectures-empty", ["lectures", "list"], "empty", 0, {}),
        (
            "lectures-play",
            ["lectures", "play", "cnu_lecture:course.invalid:row-1", "--speed", "1.0"],
            "progress-success",
            0,
            {},
        ),
        ("assignments-wide", ["assignments", "list"], "layout", 0, {}),
        ("notices-wide", ["notices", "list"], "layout", 0, {}),
        ("materials-wide", ["materials", "list"], "layout", 0, {}),
        ("assignments-empty", ["assignments", "list"], "p4b-empty", 0, {}),
        ("notices-empty", ["notices", "list"], "p4b-empty", 0, {}),
        ("materials-empty", ["materials", "list"], "p4b-empty", 0, {}),
        (
            "assignments-fetch",
            ["assignments", "fetch", "cnu_assignment:course.invalid:TB_L_REPORT1"],
            "p3-fetch",
            0,
            {"fetch_fake": {}},
        ),
        (
            "notices-fetch",
            ["notices", "fetch", "cnu_notice:course.invalid:n1"],
            "p3-fetch",
            0,
            {"fetch_fake": {}},
        ),
        ("auth-status", ["auth", "status"], "p3-healthy", 0, {}),
        ("config-init", ["config", "init", "--username", "synthetic-user"], "p3-no-config", 0, {}),
        ("setup", ["setup"], "p3-healthy", 0, {}),
        ("doctor-ready", ["doctor"], "p3-healthy", 0, {}),
        ("doctor-suppress", ["doctor"], "p3-doctor-suppress", 2, {}),
        ("status-ready", ["status"], "p3-healthy", 0, {}),
        ("status-missing-all", ["status"], "p3-no-catalogs", 2, {}),
        ("status-unreadable", ["status"], "p4b-unreadable", 2, {}),
        ("sync-nested", ["sync"], "p3-sync", 1, {"sync_shape": "nested"}),
        ("sync-lectures-failed", ["sync", "--only", "lectures"], "p3-sync", 0, {"sync_shape": "failed"}),
        ("sync-materials-failed", ["sync", "--only", "materials"], "p3-sync", 0, {"sync_shape": "failed"}),
        ("sync-notices-failed", ["sync", "--only", "notices"], "p3-sync", 0, {"sync_shape": "failed"}),
        ("sync-partial", ["sync", "--only", "assignments"], "p3-sync", 1, {"sync_shape": "partial"}),
        ("sync-failure", ["sync", "--only", "lectures"], "p3-sync-failure", 75, {}),
        ("picker-cancel", ["materials", "download"], "layout", 2, {"input": "\n", "tty": True}),
        ("cache-warning", ["assignments", "list"], "p4b-warnings", 0, {}),
    )
    result: list[Case] = []
    for name, argv, fixture, expected, extra in specs:
        for width in (48, 100):
            case: Case = {
                "name": f"p4b-{name}-{width}",
                "argv": argv,
                "fixture": fixture,
                "width": width,
                "env": {"CAMPUSCTL_OUTPUT": "human"},
                "expected": expected,
            }
            if extra.get("tty"):
                case["stdin_tty"] = case["stdout_tty"] = True
            if "input" in extra:
                case["input"] = extra["input"]
            if "fetch_fake" in extra:
                case["fetch_fake"] = extra["fetch_fake"]
            if "sync_shape" in extra:
                case["sync_shape"] = extra["sync_shape"]
            result.append(case)
    return result


def cases() -> list[Case]:
    help_paths = [
        [],
        ["config"],
        ["config", "init"],
        ["doctor"],
        ["setup"],
        ["auth"],
        ["auth", "set"],
        ["auth", "status"],
        ["sync"],
        ["status"],
        ["courses"],
        ["courses", "list"],
        ["lectures"],
        ["lectures", "list"],
        ["lectures", "play"],
        ["assignments"],
        ["assignments", "list"],
        ["assignments", "fetch"],
        ["notices"],
        ["notices", "list"],
        ["notices", "fetch"],
        ["materials"],
        ["materials", "list"],
        ["materials", "download"],
    ]
    definitions = [("help-" + "-".join(path), [*path, "--help"], "empty", 0) for path in help_paths]
    definitions += [
        ("version", ["--version"], "empty", 0),
        ("invalid", ["unknown"], "empty", 2),
        ("missing", ["lectures", "play"], "empty", 2),
        ("abbreviation", ["--ver"], "empty", 2),
        ("list-empty", ["courses", "list"], "empty", 0),
        ("list-filled", ["courses", "list"], "filled", 0),
        ("helper-failed", ["auth", "status", "--check"], "helper-failed", 1),
        ("busy", ["sync"], "busy", 75),
    ]
    definitions += [
        (f"doctor-{speed}", ["doctor"], speed, 0 if speed in {"1", "1.0", "1.25", "1.5"} else 2)
        for speed in ("1", "1.0", "1.25", "1.5", "1.1", "2.0", "true", "false", '"1.25"')
    ]
    definitions += [
        (f"play-speed-{speed}", ["lectures", "play", "synthetic-lecture", "--speed", speed], "empty", 2)
        for speed in ("1.0", "1.25", "1.5", "1.1", "2.0", "true")
    ]
    result: list[Case] = []
    for name, argv, fixture, expected in definitions:
        for width in (48, 100):
            for mode in ("json", "human"):
                result.append(
                    {
                        "name": f"{name}-{width}-{mode}",
                        "argv": argv,
                        "fixture": fixture,
                        "width": width,
                        "env": {"CAMPUSCTL_OUTPUT": mode},
                        "expected": expected,
                    }
                )
    result += [
        {
            "name": "json-overrides-human-tty",
            "argv": ["--version", "--json"],
            "fixture": "empty",
            "width": 100,
            "env": {"CAMPUSCTL_OUTPUT": "human"},
            "stdout_tty": True,
            "expected": 0,
        },
        {
            "name": "auto-human-tty",
            "argv": ["--version"],
            "fixture": "empty",
            "width": 100,
            "stdout_tty": True,
            "expected": 0,
        },
        {"name": "auto-json-pipe", "argv": ["--version"], "fixture": "empty", "width": 100, "expected": 0},
    ]
    for path in help_paths:
        label = "root" if not path else "-".join(path)
        for width in (40, 120):
            result.append(
                {
                    "name": f"fixed-help-{label}-{width}",
                    "argv": [*path, "--help"],
                    "fixture": "empty",
                    "width": width,
                    "env": {"CAMPUSCTL_OUTPUT": "human"},
                    "expected": 0,
                }
            )
    for name, argv, width in (
        ("layout-lectures-table-72", ["lectures", "list", "--all"], 72),
        ("layout-courses-ids-72", ["courses", "list"], 72),
        ("layout-notices-text-32", ["notices", "list"], 32),
    ):
        result.append(
            {
                "name": name,
                "argv": argv,
                "fixture": "layout",
                "width": width,
                "env": {"CAMPUSCTL_OUTPUT": "human"},
                "expected": 0,
            }
        )
    for outcome, expected in (("success", 0), ("failure", 1), ("interrupt", 130)):
        for tty, suffix in ((True, "tty"), (False, "pipe")):
            case = {
                "name": f"progress-{outcome}-{suffix}",
                "argv": ["lectures", "play", "cnu_lecture:course.invalid:row-1", "--speed", "1.0"],
                "fixture": f"progress-{outcome}",
                "width": 40,
                "env": {"CAMPUSCTL_OUTPUT": "human"},
                "expected": expected,
            }
            if tty:
                case["stdout_tty"] = True
            result.append(case)
    for outcome, expected in (("success", 0), ("failure", 1)):
        result.append(
            {
                "name": f"play-json-{outcome}",
                "argv": ["lectures", "play", "cnu_lecture:course.invalid:row-1", "--speed", "1.0"],
                "fixture": f"progress-{outcome}",
                "width": 80,
                "env": {"CAMPUSCTL_OUTPUT": "json"},
                "expected": expected,
            }
        )
    return [*result, *_command_owner_cases(), *_fetch_cases(), *_p4b_cases()]


@contextmanager
def prepare(root: Path, case: Case, actions: list):
    from playwright.sync_api._context_manager import PlaywrightContextManager

    # A command provider avoids touching a real keyring even when reporting doctor status.
    fixture = case["fixture"]
    speed = fixture if fixture in {"1", "1.0", "1.25", "1.5", "1.1", "2.0", "true", "false", '"1.25"'} else "1.0"
    executable = root / "browser"
    executable.write_bytes(b"synthetic browser marker")
    headed = fixture.startswith("progress-")
    if fixture.startswith("p3-"):
        _write_p3_files(root, fixture, executable)
    elif fixture == "p4b-unreadable":
        catalog = root / "data" / "catalog" / "lectures.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text("not-json", encoding="utf-8")
    elif fixture in {"p4b-empty", "p4b-warnings"}:
        _write_p4b_catalogs(root, fixture)
    else:
        (root / "config" / "config.toml").write_text(
            'provider = "cnu"\n[credentials]\nprovider = "command"\n'
            f"command = [{json.dumps(str(root / 'synthetic-helper'))}]\n"
            f"[browser]\nheadless = {'false' if headed else 'true'}\nexecutable_path = {json.dumps(str(executable))}\n"
            f"[playback]\ndefault_speed = {speed}\n",
            encoding="utf-8",
        )
        _write_catalogs(root, fixture)
    state = {"helper_attempts": 0, "lock_attempts": 0, "browser_probe_open": False}

    def fail_helper(*args, **kwargs):
        state["helper_attempts"] += 1
        actions.append(["helper", "execute", "failed"])
        raise OSError("synthetic helper failure")

    def busy_lock(fd, operation):
        state["lock_attempts"] += 1
        actions.append(["lock", "acquire", "busy"])
        raise BlockingIOError()

    def stop_browser():
        assert state["browser_probe_open"]
        state["browser_probe_open"] = False
        actions.append(["browser-probe", "close"])

    def probe_browser(self):
        assert not state["browser_probe_open"]
        state["browser_probe_open"] = True
        actions.append(["browser-probe", "open"])
        return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(executable)), stop=stop_browser)

    def _trace_publication() -> None:
        # Record flush-then-publish order without stubbing the publication owner.
        import os
        import sys

        original_flush = sys.stdout.flush
        original_replace = os.replace

        def flush() -> None:
            actions.append(["stdout", "flush"])
            original_flush()

        def replace(src, dst, *args, **kwargs):
            for suffix, name in (("courses-last-list.json", "courses"), ("materials-last-list.json", "materials")):
                if os.fspath(dst).endswith(suffix):
                    actions.append(["selection", "publish", name])
            return original_replace(src, dst, *args, **kwargs)

        sys.stdout.flush = flush
        os.replace = replace

    with patch.object(PlaywrightContextManager, "start", probe_browser), ExitStack() as stack:
        if fixture == "helper-failed":
            stack.enter_context(patch.object(subprocess, "Popen", fail_helper))
        elif fixture == "busy":
            import fcntl

            stack.enter_context(patch.object(fcntl, "flock", busy_lock))
        elif fixture.startswith("p3-"):
            _install_p3_fakes(stack, actions, fixture, state, case.get("sync_shape"))
            if fixture in _P3_HELPER_FIXTURES:
                stack.enter_context(patch.object(subprocess, "Popen", fail_helper))
        elif fixture.startswith("progress-"):
            _install_progress_fakes(stack, actions, fixture)
        if "fetch_fake" in case:
            _install_fetch_fakes(stack, actions, case["fetch_fake"])
        if case["name"] == "owner-courses-list-human" or case.get("trace_publication"):
            _trace_publication()
        yield state
