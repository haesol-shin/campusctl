"""Small synthetic starting corpus; later neutral changes add their affected paths here."""

import asyncio
import importlib
import json
import re
import subprocess
import sys
from contextlib import ExitStack, asynccontextmanager, contextmanager
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
    invoke: NotRequired[str]
    direct_media: NotRequired[list[str]]


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
    if fixture.startswith("l4-lectures"):
        _write_l4_lectures(catalog, fixture)
        return
    if fixture.startswith("l4-warn-"):
        _write_l4_warning_catalogs(root, fixture.removeprefix("l4-warn-"))
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


_L4_FAILED = {"course_id": "wide.invalid", "label": "\ud569\uc131 \uacfc\ubaa9", "reason": "course-sync-failed"}
# Cache warning inputs: (enrollment_state, failed_courses). "sparse" leaves fields out and adds a non-object entry.
_L4_WARNINGS: dict[str, tuple[str, list]] = {
    "none": ("known", []),
    "unknown": ("unknown", []),
    "failed": ("known", [_L4_FAILED]),
    "sparse": (
        "known",
        [
            {"course_id": "only-id.invalid"},
            {"label": "label-only"},
            {},
            "not-an-object",
            {"course_id": "blank.invalid", "label": "", "reason": ""},
        ],
    ),
    "many": (
        "unknown",
        [
            {
                "course_id": f"missing-{index}.invalid",
                "label": "\ub204\ub77d" + ("\uacfc\uc815" * (8 * index)),
                "reason": "course-sync-failed" if index != 2 else "course-not-found",
            }
            for index in (1, 2, 3)
        ],
    ),
}


def _write_l4_warning_catalogs(root: Path, kind: str) -> None:
    from campusctl.domain_catalog import write_domain_catalog

    state, failed = _L4_WARNINGS[kind]
    course = {"course_id": "s", "label": "\uc790\ub8cc", "class_no": None}
    notice = {
        "entity_id": "n1",
        "course": {"id": "s", "label": course["label"]},
        "title": "\uacf5\uc9c0",
        "date": "2026-01-02",
        "author": "\uad50\uc218",
        "view_count": 1,
        "has_attachments": False,
        "is_unread": True,
    }
    material = {
        "entity_id": "cnu_lms_material:s:file-1",
        "course": {"id": "s", "label": course["label"]},
        "archive_entry": {"board_item_id": "board-1", "title": "\uc790\ub8cc"},
        "file_id": "file-1",
        "display_name": "a.pdf",
        "filename": "a.pdf",
        "media_type": None,
        "size_bytes": None,
        "downloadable": True,
        "unavailable_reason": None,
    }
    data = root / "data" / "catalog"
    for domain, rows in (("notices", [notice]), ("materials", [material])):
        payload = {
            "generated_at": "2026-01-02T03:04:05Z",
            "enrollment_state": state,
            "courses": [course],
            "failed_courses": failed,
            domain: rows,
        }
        write_domain_catalog(domain, payload, data / f"{domain}.json")


def _l4_lecture(number: int, title: str | None, course: tuple[str, str], **fields: object) -> dict:
    row = _lecture_row(f"cnu_lecture:{course[0]}:r{number}", title or "", course[0], course[1])
    if title is None:
        row.pop("title")
    row.update(fields)
    return row


def _write_l4_lectures(catalog: Path, fixture: str) -> None:
    """Lecture rows covering every status, due format, media label and next-step hint."""
    first = ("c-a.invalid", "\uc790\ub8cc")
    second = ("c-b.invalid", "\uc790\u0301" + ("\uad6c\uc870" * 12))
    if fixture == "l4-lectures":
        rows = [
            _l4_lecture(1, "\uc9e7\uc740", first),
            _l4_lecture(2, "\uac15\u0301\uc758" + ("\uc601\uc0c1" * 20), first, due_date=None),
            _l4_lecture(3, "In progress", first, progress_text="3\ubd84 20\ucd08 / 50\ubd84", due_date="2026-03-04"),
            _l4_lecture(4, "Not started", first, progress_text="0\ubd84 0\ucd08 / 50\ubd84", media="youtube"),
            _l4_lecture(5, None, first, due_date="2026-03-04T05:06:07.5", media="ebook"),
            _l4_lecture(6, "Done", second, completion="complete"),
            _l4_lecture(7, "Watched", second, completion="recorded", due_date="soon"),
            _l4_lecture(8, "Opens later", second, open=False, available_from="2026-03-09T00:00:00"),
            _l4_lecture(9, "Closed", second, open=False),
            _l4_lecture(10, "Unknown state", second, completion=None, media="youtube"),
        ]
    else:
        # Every incomplete lecture is closed: the hint names the earliest opening date or says none is published.
        dates = ["2026-04-20", "2026-03-09T00:00:00"] if fixture == "l4-lectures-upcoming" else [None]
        rows = [
            _l4_lecture(index, f"Closed {index}", first, open=False, available_from=date)
            for index, date in enumerate(dates, 1)
        ]
        rows.append(_l4_lecture(9, "Finished", first, completion="complete"))
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-l4",
                "courses": [{"course_id": c, "label": label, "class_no": None} for c, label in (first, second)],
                "lectures": rows,
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


def _l4_status_outcome(sync_module, shape: str, domain: str):
    """Outcomes whose status is left unset, so the shared status rule decides it."""
    from campusctl.envelope import CampusError

    def failure(status: str) -> CampusError:
        return CampusError("course-sync-failed", "Synthetic course failed.", "Retry the course.", status)

    counts = {"courses": 1, domain: 1}
    outcome = sync_module.DomainOutcome
    return {
        "status-ok": lambda: outcome(result=counts),
        "status-partial": lambda: outcome(result=counts, errors=[failure("error")]),
        "status-error": lambda: outcome(errors=[failure("error")]),
        "status-user-action": lambda: outcome(errors=[failure("user-action")]),
        "status-busy": lambda: outcome(errors=[failure("busy")]),
        "status-first": lambda: outcome(errors=[failure("user-action"), failure("error")]),
    }[shape]()


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
        if sync_shape.startswith("status-"):
            return {domain: _l4_status_outcome(sync_module, sync_shape, domain) for domain in domains}
        if sync_shape.startswith("warn-"):
            state, failed = _L4_WARNINGS[sync_shape.removeprefix("warn-")]
            catalog = {"enrollment_state": state, "generated_at": "2026-01-02T03:04:05Z"}
            return {
                domain: sync_module.DomainOutcome(
                    result={"courses": 1, domain: 1, "failed_courses": failed, "catalog": catalog}
                )
                for domain in domains
            }
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
                        CampusError("course-sync-failed", "Synthetic course failed.", "Retry the course.", "error")
                    ],
                    status="partial",
                )
            elif sync_shape == "nested" and index > 1:
                outcomes[domain] = sync_module.DomainOutcome(status="not-started")
            elif sync_shape == "partial":
                outcomes[domain] = sync_module.DomainOutcome(
                    result=result,
                    errors=[
                        CampusError("course-sync-failed", "Synthetic course failed.", "Retry the course.", "error")
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


def _l4_cases() -> list[Case]:
    """Warnings, envelope status resolution and lecture list layout."""
    result: list[Case] = []

    def add(name: str, argv: list[str], fixture: str, expected: int, mode: str, width: int = 80, **extra) -> None:
        case: Case = {
            "name": f"l4-{name}-{width}-{mode}",
            "argv": argv,
            "fixture": fixture,
            "width": width,
            "env": {"CAMPUSCTL_OUTPUT": mode},
            "expected": expected,
        }
        case.update(extra)
        result.append(case)

    for kind in _L4_WARNINGS:
        for domain in ("notices", "materials"):
            for width in (48, 100):
                for mode in ("human", "json"):
                    add(f"warn-{domain}-list-{kind}", [domain, "list"], f"l4-warn-{kind}", 0, mode, width)
                    add(
                        f"warn-{domain}-sync-{kind}",
                        ["sync", "--only", domain],
                        "p3-sync",
                        0,
                        mode,
                        width,
                        sync_shape=f"warn-{kind}",
                    )
    exits = {"ok": 0, "partial": 1, "error": 1, "user-action": 2, "busy": 75, "first": 2}
    for shape, expected in exits.items():
        for mode in ("human", "json"):
            for only in ("lectures", "notices", "materials"):
                add(
                    f"status-sync-{only}-{shape}",
                    ["sync", "--only", only],
                    "p3-sync",
                    expected,
                    mode,
                    sync_shape=f"status-{shape}",
                )
            add(f"status-sync-all-{shape}", ["sync"], "p3-sync", expected, mode, sync_shape=f"status-{shape}")
            for domain in ("lectures", "assignments", "notices", "materials"):
                add(
                    f"status-list-refresh-{domain}-{shape}",
                    [domain, "list", "--refresh"],
                    "p3-sync",
                    expected,
                    mode,
                    sync_shape=f"status-{shape}",
                )
    listings = (
        ("mixed", ["lectures", "list", "--all"], "l4-lectures"),
        ("unfinished", ["lectures", "list"], "l4-lectures"),
        ("upcoming", ["lectures", "list"], "l4-lectures-upcoming"),
        ("no-dates", ["lectures", "list"], "l4-lectures-nodates"),
        ("empty-all", ["lectures", "list", "--all"], "empty"),
    )
    for name, argv, fixture in listings:
        for width in (20, 48, 59, 60, 100):
            add(f"lectures-{name}", argv, fixture, 0, "human", width)
        add(f"lectures-{name}", argv, fixture, 0, "json", 100)
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


_P5_ROW = "cnu_lecture:course.invalid:row-1"
_P5_ROW_2 = "cnu_lecture:course.invalid:row-2"
_P5_LONG = {"p5-callbacks", "p5-callback-raises"}
_P5_STALL = {"p5-stall", "p5-close-fails", "p5-stall-close-cancelled"}
_P5_ADVANCING = {"p5-advancing", "p5-close-after-success"}
_P5_RATES = {"p5-native-rate", "p5-rate-true", "p5-rate-near-one", "p5-rate-missing"}
_P5_COVERAGE = {"p5-video-coverage", "p5-video-replay-ended", "p5-video-restart-fails"}
_P5_CLOSE_FAILS = {"p5-close-fails", "p5-close-after-success", "p5-video-close-after-success"}
_P5_ODD_DURATION = {
    "p5-video-zero-duration": 0.0,
    "p5-video-negative-duration": -5.0,
    "p5-video-nan-duration": float("nan"),
    "p5-video-inf-duration": float("inf"),
}


def _p5_is_video(fixture: str) -> bool:
    return fixture == "p5-missing-video" or fixture.startswith("p5-video-")


def _p5_cases() -> list[Case]:
    """Real play_lectures paths. Names match the P5 equivalence list."""
    specs = [
        ("p5-youtube-blocked-click", [_P5_ROW, "--speed", "1.0"], "p5-blocked", 0, "json", False),
        ("p5-youtube-advancing-no-click", [_P5_ROW, "--speed", "1.0"], "p5-advancing", 0, "json", False),
        ("p5-youtube-queue-speed", [_P5_ROW, "--speed", "1.5", "--replay"], "p5-queue-speed", 1, "json", False),
        ("p5-youtube-native-rate", [_P5_ROW, "--speed", "1.0"], "p5-native-rate", 1, "json", False),
        ("p5-youtube-missing-frame", [_P5_ROW, "--speed", "1.0"], "p5-missing-frame", 1, "json", False),
        ("p5-video-missing", [_P5_ROW, "--speed", "1.0"], "p5-missing-video", 1, "json", False),
        ("p5-youtube-stall", [_P5_ROW, "--speed", "1.0"], "p5-stall", 1, "json", False),
        ("p5-youtube-item-speed", [_P5_ROW, "--speed", "1.5"], "p5-item-speed", 1, "json", False),
        ("p5-callback-timing", [_P5_ROW, "--speed", "1.0"], "p5-callbacks", 0, "human", True),
        ("p5-callback-raises", [_P5_ROW, "--speed", "1.0"], "p5-callback-raises", 0, "human", True),
        ("p5-cleanup-close-fails", [_P5_ROW, "--speed", "1.0"], "p5-close-fails", 1, "json", False),
        ("p5-cleanup-interrupt", [_P5_ROW, "--speed", "1.0"], "p5-interrupt", 130, "human", False),
        ("p5-mixed-queue", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p5-mixed", 0, "json", False),
        ("p5-video-only-sso", [_P5_ROW, "--speed", "1.0"], "p5-video-only", 0, "json", False),
        ("l5-youtube-rate-true", [_P5_ROW, "--speed", "1.0"], "p5-rate-true", 1, "json", False),
        ("l5-youtube-rate-near-one", [_P5_ROW, "--speed", "1.0"], "p5-rate-near-one", 1, "json", False),
        ("l5-youtube-rate-missing", [_P5_ROW, "--speed", "1.0"], "p5-rate-missing", 1, "json", False),
        ("l5-youtube-rate-drift", [_P5_ROW, "--speed", "1.0"], "p5-rate-drift", 1, "json", False),
        ("l5-youtube-read-error", [_P5_ROW, "--speed", "1.0"], "p5-youtube-read-error", 1, "json", False),
        ("l5-youtube-start-boundary", [_P5_ROW, "--speed", "1.0"], "p5-start-boundary", 0, "json", False),
        ("l5-youtube-start-edge", [_P5_ROW, "--speed", "1.0"], "p5-start-edge", 0, "json", False),
        ("l5-video-zero-duration", [_P5_ROW, "--speed", "1.0"], "p5-video-zero-duration", 0, "json", False),
        ("l5-video-negative-duration", [_P5_ROW, "--speed", "1.0"], "p5-video-negative-duration", 0, "json", False),
        ("l5-video-nan-duration", [_P5_ROW, "--speed", "1.0"], "p5-video-nan-duration", 0, "json", False),
        ("l5-video-inf-duration", [_P5_ROW, "--speed", "1.0"], "p5-video-inf-duration", 0, "json", False),
        ("l5-youtube-start-past-boundary", [_P5_ROW, "--speed", "1.0"], "p5-start-past", 1, "json", False),
        ("l5-youtube-near-end", [_P5_ROW, "--speed", "1.0"], "p5-near-end", 0, "json", False),
        ("l5-youtube-replay-near-end", [_P5_ROW, "--speed", "1.0", "--replay"], "p5-near-end", 0, "json", False),
        ("l5-youtube-timeout", [_P5_ROW, "--speed", "1.0"], "p5-youtube-timeout", 1, "json", False),
        ("l5-video-timeout", [_P5_ROW, "--speed", "1.0"], "p5-video-timeout", 1, "json", False),
        ("l5-video-timeout-speed", [_P5_ROW, "--speed", "1.5"], "p5-video-timeout", 1, "json", False),
        ("l5-video-rate-true", [_P5_ROW, "--speed", "1.0"], "p5-video-rate-true", 0, "json", False),
        ("l5-video-rate-near-one", [_P5_ROW, "--speed", "1.0"], "p5-video-rate-near-one", 0, "json", False),
        ("l5-video-rate-missing", [_P5_ROW, "--speed", "1.0"], "p5-video-rate-missing", 0, "json", False),
        ("l5-video-coverage", [_P5_ROW, "--speed", "1.0"], "p5-video-coverage", 0, "json", False),
        ("l5-video-replay-coverage", [_P5_ROW, "--speed", "1.0", "--replay"], "p5-video-coverage", 0, "json", False),
        ("l5-video-replay-restart", [_P5_ROW, "--speed", "1.0", "--replay"], "p5-video-replay-ended", 0, "json", False),
        (
            "l5-video-replay-no-restart",
            [_P5_ROW, "--speed", "1.0", "--replay"],
            "p5-video-restart-fails",
            1,
            "json",
            False,
        ),
        ("l5-video-callback-timing", [_P5_ROW, "--speed", "1.0"], "p5-video-callbacks", 0, "human", True),
        ("l5-video-read-error", [_P5_ROW, "--speed", "1.0"], "p5-video-read-error", 1, "json", False),
        ("l5-cleanup-close-after-success", [_P5_ROW, "--speed", "1.0"], "p5-close-after-success", 1, "json", False),
        (
            "l5-cleanup-close-cancelled-after-stall",
            [_P5_ROW, "--speed", "1.0"],
            "p5-stall-close-cancelled",
            1,
            "json",
            False,
        ),
        (
            "l5-video-cleanup-close-after-success",
            [_P5_ROW, "--speed", "1.0"],
            "p5-video-close-after-success",
            1,
            "json",
            False,
        ),
    ]
    cases = []
    for name, arguments, fixture, expected, mode, tty in specs:
        case: Case = {
            "name": name,
            "argv": ["lectures", "play", *arguments],
            "fixture": fixture,
            "width": 80,
            "env": {"CAMPUSCTL_OUTPUT": mode},
            "expected": expected,
        }
        if tty:
            case["stdout_tty"] = True
        cases.append(case)
    return cases


def _p6_cases() -> list[Case]:
    """Panopto paths that P5 did not already name. Playback cases run real play_lectures."""
    specs = [
        ("p6-mixed-reverse", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-mixed-reverse", 0, "json"),
        ("p6-splash-click", [_P5_ROW, "--speed", "1.0"], "p6-splash-click", 0, "json"),
        ("p6-splash-stuck", [_P5_ROW, "--speed", "1.0"], "p6-splash-stuck", 1, "json"),
        ("p6-frame-late", [_P5_ROW, "--speed", "1.0"], "p6-frame-late", 0, "json"),
        ("p6-video-resume", [_P5_ROW, "--speed", "1.0"], "p6-video-resume", 0, "json"),
        ("p6-row-incomplete", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-row-incomplete", 1, "json"),
        ("p6-row-read-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-row-read-fail", 1, "json"),
        ("p6-catalog-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-catalog-fail", 1, "json"),
        ("p6-started-gap", [_P5_ROW, "--speed", "1.0"], "p6-started-gap", 1, "json"),
        ("p6-video-interrupt", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-video-interrupt", 130, "human"),
        ("l6-rate-already", [_P5_ROW, "--speed", "1.5"], "p6-rate-preset", 0, "json"),
        ("l6-rate-change", [_P5_ROW, "--speed", "1.5"], "p6-rate-change", 0, "json"),
        ("l6-rate-option-missing", [_P5_ROW, "--speed", "1.5"], "p6-speed-missing", 1, "json"),
        ("l6-already-complete", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-already-complete", 0, "json"),
        ("l6-already-catalog-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-already-catalog-fail", 1, "json"),
        ("l6-recorded-pre", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-recorded-pre", 0, "json"),
        ("l6-recorded-pre-catalog-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-recorded-catalog-fail", 1, "json"),
        ("l6-recorded-post", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-recorded-post", 0, "json"),
        (
            "l6-recorded-post-catalog-fail",
            [_P5_ROW, _P5_ROW_2, "--speed", "1.0"],
            "p6-recorded-post-catalog-fail",
            1,
            "json",
        ),
        ("l6-recorded-replay", [_P5_ROW, _P5_ROW_2, "--speed", "1.0", "--replay"], "p6-recorded-pre", 0, "json"),
        ("l6-first-failure-tail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-row-closed", 1, "json"),
        ("l6-later-failure", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-second-closed", 1, "json"),
        ("l6-frame-never", [_P5_ROW, "--speed", "1.0"], "p6-frame-never", 1, "json"),
        ("l6-frame-video-error", [_P5_ROW, "--speed", "1.0"], "p6-frame-video-error", 1, "json"),
        ("l6-frame-other-error", [_P5_ROW, "--speed", "1.0"], "p6-frame-other-error", 1, "json"),
        ("l6-video-stall", [_P5_ROW, "--speed", "1.0"], "p6-video-stall", 1, "json"),
        ("l6-login-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-login-fail", 1, "json"),
        ("l6-prepare-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-prepare-fail", 1, "json"),
        ("l6-interrupt-verify", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-verify-interrupt", 130, "human"),
        ("l6-room-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-room-fail", 1, "json"),
        ("l6-room-campus-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-room-campus-fail", 1, "json"),
        ("l6-open-fail", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-open-fail", 1, "json"),
        ("l6-catalog-crash", [_P5_ROW, _P5_ROW_2, "--speed", "1.0"], "p6-catalog-crash", 1, "json"),
        ("l6-fractional-elapsed", [_P5_ROW, "--speed", "1.0"], "p6-fractional-elapsed", 0, "json"),
        ("l6-progress-only", [_P5_ROW, "--speed", "1.0"], "p6-progress-only", 0, "json"),
        ("l6-attendance-partial", [_P5_ROW, "--speed", "1.0"], "p6-attendance-partial", 0, "json"),
    ]
    cases = []
    for name, arguments, fixture, expected, mode in specs:
        cases.append(
            {
                "name": name,
                "argv": ["lectures", "play", *arguments],
                "fixture": fixture,
                "width": 80,
                "env": {"CAMPUSCTL_OUTPUT": mode},
                "expected": expected,
            }
        )
    cases.append(
        {
            "name": "p6-direct-missing-none-unknown",
            "argv": ["lectures", "play"],
            "fixture": "p6-direct",
            "width": 80,
            "env": {"CAMPUSCTL_OUTPUT": "json"},
            "expected": 0,
            "invoke": "play_lectures",
            "direct_media": ["missing", "none", "offline"],
        }
    )
    cases.append(
        {
            "name": "p6-userstring-youtube",
            "argv": ["lectures", "play"],
            "fixture": "p5-blocked",
            "width": 80,
            "env": {"CAMPUSCTL_OUTPUT": "json"},
            "expected": 0,
            "invoke": "play_lectures",
            "direct_media": ["userstring-youtube"],
        }
    )
    cases.append(
        {
            "name": "p6-unhashable-preview",
            "argv": ["lectures", "play"],
            "fixture": "p6-unhashable-preview",
            "width": 80,
            "env": {"CAMPUSCTL_OUTPUT": "json"},
            "expected": 1,
            "invoke": "play_lectures",
        }
    )
    cases.append(
        {
            "name": "p6-prepare-guard",
            "argv": ["lectures", "play"],
            "fixture": "p6-prepare-guard",
            "width": 80,
            "env": {"CAMPUSCTL_OUTPUT": "json"},
            "expected": 0,
            "invoke": "play_lectures",
        }
    )
    for name, fixture, expected in (
        ("l6-direct-bad-id", "p6-direct", 1),
        ("l6-cancel-playback", "p6-video-cancelled", 1),
        ("l6-cancel-verify", "p6-verify-cancelled", 1),
    ):
        cases.append(
            {
                "name": name,
                "argv": ["lectures", "play"],
                "fixture": fixture,
                "width": 80,
                "env": {"CAMPUSCTL_OUTPUT": "json"},
                "expected": expected,
                "invoke": "play_lectures",
                "direct_media": ["video", "bad-id", "video"] if fixture == "p6-direct" else ["video", "video"],
            }
        )
    return cases


def _write_p5_catalog(root: Path, fixture: str) -> None:
    second_media = "youtube" if fixture == "p6-mixed-reverse" else "video"
    if fixture == "p5-mixed":
        second_media = "video"
    first_media = "video" if fixture in {"p5-video-only", "p5-mixed"} or _p5_is_video(fixture) else "youtube"
    if fixture == "p5-mixed":
        first_media = "youtube"
    if fixture.startswith("p6-"):
        first_media = "video"
    lectures = [
        _lecture_row(_P5_ROW, "Synthetic lecture", "course.invalid", "Synthetic course"),
    ]
    lectures[0]["media"] = first_media
    if fixture in {
        "p5-mixed",
        "p6-mixed-reverse",
        "p6-row-incomplete",
        "p6-row-read-fail",
        "p6-catalog-fail",
        "p6-video-interrupt",
        "p6-already-complete",
        "p6-already-catalog-fail",
        "p6-recorded-pre",
        "p6-recorded-catalog-fail",
        "p6-recorded-post",
        "p6-recorded-post-catalog-fail",
        "p6-row-closed",
        "p6-second-closed",
        "p6-login-fail",
        "p6-prepare-fail",
        "p6-video-cancelled",
        "p6-verify-interrupt",
        "p6-verify-cancelled",
        "p6-room-fail",
        "p6-room-campus-fail",
        "p6-open-fail",
        "p6-catalog-crash",
    }:
        lectures.append(_lecture_row(_P5_ROW_2, "Synthetic video", "course.invalid", "Synthetic course"))
        lectures[1]["media"] = second_media
    if fixture == "p6-direct":
        lectures = [
            _lecture_row(
                f"cnu_lecture:course.invalid:row-{index}", f"Synthetic {index}", "course.invalid", "Synthetic course"
            )
            for index in (1, 2, 3)
        ]
        for row in lectures:
            row["media"] = "video"
    catalog = root / "data" / "catalog" / "lectures.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-02T03:04:05Z",
                "generation_id": "synthetic-p5",
                "courses": [{"course_id": "course.invalid", "label": "Synthetic course", "class_no": None}],
                "lectures": lectures,
            }
        ),
        encoding="utf-8",
    )


class _P5Clock:
    def __init__(self, actions: list) -> None:
        self.timestamp = 0.0
        self.actions = actions

    def now(self) -> float:
        return self.timestamp

    def monotonic(self) -> float:
        return self.timestamp

    async def sleep(self, seconds: float) -> None:
        self.actions.append(["sleep", seconds])
        self.timestamp += seconds

    def sleep_sync(self, seconds: float) -> None:
        self.timestamp += seconds

    async def wait_for(self, awaitable, seconds: float):
        del seconds
        return await awaitable


class _P5Locator:
    def __init__(self, page: "_P5Page", selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "_P5Locator":
        return self

    def nth(self, index: int) -> "_P5Locator":
        del index
        return self

    async def count(self) -> int:
        if "btnPreviewClose" in self.selector:
            self.page.actions.append(["lookup", "close-button", self.page.modal_open])
        if "data-speed" in self.selector and self.page.fixture == "p6-speed-missing":
            return 0
        return int(await self.is_visible() or self._present())

    def _present(self) -> bool:
        if "btnPreviewClose" in self.selector or "btn_popClose" in self.selector:
            return self.page.modal_open
        if self.selector == ".ytmCuedOverlayPlayButton":
            return self.page.overlay_present
        return True

    async def get_attribute(self, name: str) -> str | None:
        if name == "data-weekno":
            return "1"
        if name == "data-status":
            return "closed"
        return None

    async def is_visible(self) -> bool:
        if "btnPreviewClose" in self.selector or "btn_popClose" in self.selector:
            return self.page.modal_open
        if self.selector == ".ytmCuedOverlayPlayButton":
            return self.page.overlay_present
        if "play" in self.selector.lower() and "titleDetail" not in self.selector:
            if self.page.fixture in {"p6-splash-click", "p6-splash-stuck"} and not self.page.video_ready:
                return True
            return self.page.panopto_paused and self.page.video_ready
        return True

    async def scroll_into_view_if_needed(self, **kwargs) -> None:
        del kwargs

    async def click(self, **kwargs) -> None:
        del kwargs
        self.page._click(self.selector)


class _P5Frame:
    def __init__(self, page: "_P5Page", kind: str) -> None:
        self.page = page
        self.kind = kind
        self.parent_frame = object()

    @property
    def url(self) -> str:
        if self.kind == "youtube":
            return "https://www.youtube-nocookie.com/embed/synthetic"
        if self.page.fixture == "p6-frame-other-error":
            return "https://lms.invalid/embed"
        return "https://panopto.example/Embed.aspx"

    def locator(self, selector: str) -> _P5Locator:
        return _P5Locator(self.page, selector)

    async def query_selector(self, selector: str):
        if selector == "video" and self.page.fixture in {"p6-frame-video-error", "p6-frame-other-error"}:
            from playwright.async_api import Error as PlaywrightError

            raise PlaywrightError("synthetic frame query failure")
        if selector == "video" and self.page.video_ready:
            return object()
        return None

    async def wait_for_selector(self, selector: str, **kwargs) -> None:
        del selector, kwargs
        if not self.page.video_ready:
            raise TimeoutError("synthetic video missing")

    async def evaluate(self, script: str, *args):
        self.page.evaluations.append(script)
        if "campusctl-read-youtube-video-state" in script:
            return self.page.youtube_status()
        if "campusctl-classify-player-page" in script:
            return {
                "page_state": "ready" if self.page.video_ready else "unknown",
                "video_count": int(self.page.video_ready),
            }
        if "campusctl-read-video-state" in script:
            return self.page.panopto_status()
        raise AssertionError("unexpected frame evaluation")


class _P5Page:
    def __init__(self, actions: list, fixture: str) -> None:
        self.actions = actions
        self.fixture = fixture
        self.modal_open = False
        self.active_row = "row-1"
        self.youtube_rows = {"row-1"} if not _p5_is_video(fixture) else set()
        if fixture == "p5-mixed":
            self.youtube_rows = {"row-1"}
        if fixture.startswith("p6-") and fixture != "p6-mixed-reverse":
            self.youtube_rows = set()
        if fixture == "p6-mixed-reverse":
            self.youtube_rows = {"row-2"}
        self.states = {"row-1": "N", "row-2": "N", "row-3": "N"}
        if "already" in fixture:
            self.states["row-1"] = "F"
        self.closed_rows: set[str] = set()
        self.yt_reads = 0
        self.yt_clicked = False
        self.started = False
        self.panopto_paused = True
        self.panopto_reads = 0
        self.panopto_playing_reads = 0
        # None omits the field, as a player without a readable rate would.
        self.panopto_rate: object = {
            "p5-video-rate-true": True,
            "p5-video-rate-near-one": 1.0000000001,
            "p6-rate-preset": 1.5,
        }.get(fixture, 1.0)
        if fixture == "p5-video-rate-missing":
            self.panopto_rate = None
        self.authenticated = False
        self.frame_queries = 0
        self.row_reads = 0
        self._video_ready = fixture not in {"p5-missing-video", "p6-splash-click", "p6-splash-stuck"}
        self.sso_submits = 0
        self.evaluations: list[str] = []
        self.url = "https://lms.invalid/course"
        self.context = self
        self.youtube_frame = _P5Frame(self, "youtube")
        self.panopto_frame = _P5Frame(self, "panopto")

    @property
    def video_ready(self) -> bool:
        return self._video_ready

    @property
    def overlay_present(self) -> bool:
        needs_click = self.fixture in {
            "p5-blocked",
            "p5-callbacks",
            "p5-callback-raises",
            "p5-interrupt",
            "p5-mixed",
            "p5-start-boundary",
            "p5-start-edge",
            "p5-start-past",
            "p5-near-end",
            "p5-youtube-timeout",
            "p6-mixed-reverse",
        }
        return needs_click and not self.yt_clicked and self.active_row in self.youtube_rows

    @property
    def frames(self) -> list[_P5Frame]:
        if not self.modal_open or self.active_row not in self.youtube_rows or self.fixture == "p5-missing-frame":
            return []
        return [self.youtube_frame]

    def locator(self, selector: str) -> _P5Locator:
        return _P5Locator(self, selector)

    async def wait_for_selector(self, selector: str, **kwargs) -> None:
        del selector, kwargs

    async def click(self, selector: str, **kwargs) -> None:
        del kwargs
        self.actions.append(["click", "course-room"])

    async def query_selector(self, selector: str):
        if selector == "iframe#previewFrame" and self.modal_open and self.active_row not in self.youtube_rows:
            if self.fixture == "p6-frame-never":
                return None
            if self.fixture == "p6-frame-late":
                self.frame_queries += 1
                self.actions.append(["frame", "query", self.frame_queries])
                if self.frame_queries < 4:
                    return None
            return self

    async def content_frame(self):
        return self.panopto_frame

    async def cookies(self) -> list[dict[str, str]]:
        if not self.authenticated:
            return []
        return [{"name": ".ASPXAUTH", "domain": "panopto.example"}]

    def _click(self, selector: str) -> None:
        row = re.search(r'\[id="([^"]+)"\]', selector)
        if row:
            self.active_row = row.group(1)
        if "titleDetailContents" in selector and self.fixture == "p6-open-fail" and self.active_row == "row-2":
            self.actions.append(["modal", "open-failed", self.active_row])
            raise RuntimeError("synthetic modal failure")
        if "titleDetailContents" in selector:
            self.modal_open = True
            self.panopto_paused = True
            self.panopto_reads = 0
            self.panopto_playing_reads = 0
            self.actions.append(["modal", "open", self.active_row])
            return
        if "btnPreviewClose" in selector or "btn_popClose" in selector:
            self.actions.append(["modal", "close", self.active_row])
            self.modal_open = False
            self.closed_rows.add(self.active_row)
            if self.fixture != "p6-row-incomplete":
                self.states[self.active_row] = "F"
            if self.fixture == "p5-stall-close-cancelled":
                raise asyncio.CancelledError
            if self.fixture in _P5_CLOSE_FAILS:
                raise RuntimeError("synthetic close failure")
            return
        if "data-speed" in selector:
            self.panopto_rate = float(re.search(r'data-speed="([^"]+)"', selector).group(1))
            self.actions.append(["click", "panopto-speed", selector])
            return
        if selector == ".ytmCuedOverlayPlayButton":
            self.yt_clicked = True
            self.actions.append(["click", "youtube-start"])
            return
        if "play" in selector.lower():
            self.panopto_paused = False
            self.actions.append(["click", "panopto-play"])
            if self.fixture == "p6-splash-click":
                self._video_ready = True

    def _lecture_row_state(self, row_id: str) -> dict:
        """Row as the LMS reports it; recorded rows show full progress without counted attendance."""
        row = {
            "state": self.states.get(row_id, "N"),
            "moduletype": "LV",
            "openyn": "N"
            if (self.fixture, row_id) in {("p6-row-closed", "row-1"), ("p6-second-closed", "row-2")}
            else "Y",
            "progress_text": None,
            "attendance_counted": None,
        }
        recorded_before = self.fixture in {"p6-recorded-pre", "p6-recorded-catalog-fail"}
        recorded_after = (
            self.fixture in {"p6-recorded-post", "p6-recorded-post-catalog-fail"} and row_id in self.closed_rows
        )
        if row_id == "row-1" and (recorded_before or recorded_after):
            row.update(state="N", progress_text="10분 / 10분", attendance_counted=False)
        elif row_id == "row-1" and self.fixture == "p6-progress-only":
            row.update(progress_text="10분 / 10분")
        elif row_id == "row-1" and self.fixture == "p6-attendance-partial":
            row.update(progress_text="3분 / 10분", attendance_counted=False)
        return row

    async def evaluate(self, script: str, *args):
        self.evaluations.append(script)
        if "campusctl-read-lecture-row" in script:
            self.row_reads += 1
            row_id = args[0] if args else self.active_row
            if self.fixture == "p6-row-read-fail" and self.row_reads > 1:
                self.actions.append(["row", "read-failed", row_id])
                raise RuntimeError("synthetic row read failure")
            if self.fixture in {"p6-verify-interrupt", "p6-verify-cancelled"} and self.row_reads > 3:
                self.actions.append(["row", "read-stopped", row_id])
                raise KeyboardInterrupt if self.fixture == "p6-verify-interrupt" else asyncio.CancelledError
            if self.fixture in {"p6-room-fail", "p6-room-campus-fail"} and self.row_reads == 3:
                self.actions.append(["row", "read-failed", row_id])
                if self.fixture == "p6-room-campus-fail":
                    from campusctl.envelope import CampusError

                    raise CampusError("browser-timeout", "Synthetic row read timeout.", "Retry.", "error")
                raise RuntimeError("synthetic row read failure")
            return self._lecture_row_state(row_id)
        if "campusctl-read-collapse-toggles" in script:
            return []
        if "panoptoSamlLogin_submit" in script:
            self.authenticated = self.fixture != "p6-prepare-fail"
            self.sso_submits += 1
            self.actions.append(["sso", "submit"])
            return True
        raise AssertionError("unexpected page evaluation")

    def youtube_status(self) -> dict:
        if self.fixture in _P5_RATES:
            status = {"exists": True, "currentTime": 0.0, "duration": 15.0, "paused": True, "ended": False}
            rate = {"p5-native-rate": 1.25, "p5-rate-true": True, "p5-rate-near-one": 1.0000000001}.get(self.fixture)
            if rate is not None:
                status["playbackRate"] = rate
            return status
        if self.fixture in {"p5-rate-drift", "p5-youtube-read-error"}:
            self.yt_reads += 1
            if self.fixture == "p5-youtube-read-error" and self.yt_reads >= 4:
                raise RuntimeError("synthetic read failure")
            return {
                "exists": True,
                "currentTime": [0.0, 5.0, 10.0, 11.0][min(self.yt_reads, 4) - 1],
                "duration": 15.0,
                "paused": False,
                "ended": False,
                "playbackRate": 1.25 if self.fixture == "p5-rate-drift" and self.yt_reads >= 4 else 1.0,
            }
        if self.fixture in _P5_ADVANCING or self.fixture in _P5_STALL:
            self.yt_reads += 1
            if self.fixture in _P5_STALL and self.yt_reads >= 3:
                return {
                    "exists": True,
                    "currentTime": 1.0,
                    "duration": 15.0,
                    "paused": True,
                    "ended": False,
                    "playbackRate": 1.0,
                }
            current = 0.0 if self.yt_reads == 1 else float(self.yt_reads) * 5
            ended = self.fixture in _P5_ADVANCING and current >= 14
            if ended:
                self.states[self.active_row] = "F"
            return {
                "exists": True,
                "currentTime": current,
                "duration": 15.0,
                "paused": False,
                "ended": ended,
                "playbackRate": 1.0,
            }
        if not self.yt_clicked:
            return {
                "exists": True,
                "currentTime": {"p5-start-boundary": 0.05, "p5-start-past": 0.0500001}.get(self.fixture, 0.0),
                "duration": 90.0 if self.fixture in _P5_LONG else 15.0,
                "paused": True,
                "ended": False,
                "playbackRate": 1.0,
            }
        if self.fixture == "p5-interrupt":
            self.yt_reads += 1
            if self.yt_reads >= 2:
                raise KeyboardInterrupt
            return {
                "exists": True,
                "currentTime": 1.0,
                "duration": 90.0,
                "paused": False,
                "ended": False,
                "playbackRate": 1.0,
            }
        if self.fixture == "p5-youtube-timeout":
            self.yt_reads += 1
            return {
                "exists": True,
                "currentTime": float(self.yt_reads),
                "duration": 600.0,
                "paused": False,
                "ended": False,
                "playbackRate": 1.0,
            }
        if self.fixture == "p5-start-edge":
            self.yt_reads += 1
            # Exactly the movement threshold above the start position, then a real advance.
            current = [0.05, 5.0, 15.0][min(self.yt_reads, 3) - 1]
            if current >= 15.0:
                self.states[self.active_row] = "F"
            return {
                "exists": True,
                "currentTime": current,
                "duration": 15.0,
                "paused": False,
                "ended": current >= 15.0,
                "playbackRate": 1.0,
            }
        if self.fixture == "p5-near-end":
            self.yt_reads += 1
            current = [14.5, 14.6, 15.0][min(self.yt_reads, 3) - 1]
            if current >= 15.0:
                self.states[self.active_row] = "F"
            return {
                "exists": True,
                "currentTime": current,
                "duration": 15.0,
                "paused": False,
                "ended": current >= 15.0,
                "playbackRate": 1.0,
            }
        self.yt_reads += 1
        duration = 90.0 if self.fixture in _P5_LONG else 15.0
        current = min(duration, (30.0 if self.fixture in _P5_LONG else 15.0) * self.yt_reads)
        ended = current >= duration - 1
        if ended:
            self.states[self.active_row] = "F"
        return {
            "exists": True,
            "currentTime": current,
            "duration": duration,
            "paused": False,
            "ended": ended,
            "playbackRate": 1.0,
        }

    def _panopto_point(self, current: float, duration: float, *, ended: bool, paused: bool) -> dict:
        status = {
            "exists": self.video_ready,
            "currentTime": current,
            "duration": duration,
            "paused": paused,
            "ended": ended,
            "playedUntil": current,
            "playedCoverage": current,
        }
        if self.panopto_rate is not None:
            status["playbackRate"] = self.panopto_rate
        return status

    def _panopto_series(self) -> dict:
        """Coverage 7.9 then 8.0 sits on the replay tolerance edge for a 10 s video."""
        if self.panopto_paused:
            ended = self.fixture in {"p5-video-replay-ended", "p5-video-restart-fails"}
            return self._panopto_point(10.0 if ended else 0.0, 10.0, ended=ended, paused=not ended)
        self.panopto_playing_reads += 1
        reads = self.panopto_playing_reads
        if self.fixture == "p5-video-restart-fails":
            return self._panopto_point(10.0, 10.0, ended=True, paused=False)
        if self.fixture == "p5-video-timeout":
            return self._panopto_point(reads * 0.6, 2000.0, ended=False, paused=False)
        if self.fixture == "p5-video-callbacks":
            if reads >= 40:
                self.states[self.active_row] = "F"
            return self._panopto_point(float(reads), 40.0, ended=False, paused=False)
        if self.fixture == "p5-video-read-error" and reads >= 3:
            raise RuntimeError("synthetic read failure")
        current = [3.0, 6.0, 7.9, 8.0, 10.0][min(reads, 5) - 1] if self.fixture in _P5_COVERAGE else reads * 3.0
        if current >= 8.0:
            self.states[self.active_row] = "F"
        return self._panopto_point(current, 10.0, ended=False, paused=False)

    def _panopto_resume(self) -> dict:
        if self.panopto_paused:
            return self._panopto_point(4.0, 10.0, ended=False, paused=True)
        self.panopto_playing_reads += 1
        current = [4.0, 7.0, 10.0][min(self.panopto_playing_reads, 3) - 1]
        if current >= 10.0:
            self.states[self.active_row] = "F"
        return self._panopto_point(current, 10.0, ended=current >= 10.0, paused=False)

    def panopto_status(self) -> dict:
        self.panopto_reads += 1
        if self.fixture == "p6-video-interrupt" and not self.panopto_paused:
            self.actions.append(["interrupt", "video", self.active_row])
            raise KeyboardInterrupt
        if self.fixture == "p6-video-cancelled" and not self.panopto_paused and self.active_row == "row-2":
            self.actions.append(["cancel", "video", self.active_row])
            raise asyncio.CancelledError
        if self.fixture == "p6-video-stall":
            return self._panopto_point(1.0, 10000.0, ended=False, paused=self.panopto_paused)
        if self.fixture == "p6-video-resume":
            status = self._panopto_resume()
            self.actions.append(["panopto-time", status["currentTime"], status["paused"]])
            return status
        if self.fixture in _P5_ODD_DURATION:
            duration = _P5_ODD_DURATION[self.fixture]
            if self.panopto_paused:
                return self._panopto_point(0.0, duration, ended=False, paused=True)
            self.panopto_playing_reads += 1
            reads = self.panopto_playing_reads
            ended = reads >= 4
            if ended:
                self.states[self.active_row] = "F"
            return self._panopto_point(reads * 3.0, duration, ended=ended, paused=False)
        if self.fixture in _P5_COVERAGE or self.fixture in {
            "p5-video-callbacks",
            "p5-video-read-error",
            "p5-video-timeout",
        }:
            return self._panopto_series()
        ended = not self.panopto_paused and self.panopto_reads >= 2
        if ended and self.fixture != "p6-row-incomplete":
            self.states[self.active_row] = "F"
        return self._panopto_point(10.0 if ended else 0.0, 10.0, ended=ended, paused=self.panopto_paused)


_DIRECT_PAGE = None


def _install_p5_fakes(stack: ExitStack, actions: list, fixture: str) -> None:
    global _DIRECT_PAGE
    import campusctl.browser as browser_module
    import campusctl.commands.lectures as lectures_module
    from campusctl.providers.cnu import player as player_module
    from campusctl.wait_clock import use_clock

    page = _P5Page(actions, fixture)
    _DIRECT_PAGE = page
    clock = _P5Clock(actions)
    stack.enter_context(use_clock(clock))

    @asynccontextmanager
    async def fake_open_session(*args, **kwargs):
        del args, kwargs
        actions.append(["session", "open"])
        try:
            yield SimpleNamespace(page=page)
        finally:
            actions.append(["session", "close"])

    async def fake_login(*args, **kwargs) -> None:
        del args, kwargs
        actions.append(["auth", "login"])
        if fixture == "p6-login-fail":
            from campusctl.envelope import CampusError

            raise CampusError("auth-failed", "Synthetic login failure.", "Retry.", "error")

    stack.enter_context(patch.object(browser_module, "open_session", fake_open_session))
    stack.enter_context(patch.object(player_module, "ensure_logged_in", fake_login))
    for module_name in ("campusctl.providers.cnu.media", "campusctl.providers.cnu.media.panopto"):
        try:
            media = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(media, "ensure_logged_in"):
            stack.enter_context(patch.object(media, "ensure_logged_in", fake_login))

    real_write = player_module.write_catalog

    def traced_write(catalog, path=None):
        changed = [
            [item.get("entity_id"), item.get("completion")]
            for item in catalog.get("lectures", [])
            if isinstance(item, dict)
        ]
        actions.append(["catalog", "write", changed])
        if "catalog-fail" in fixture:
            from campusctl.envelope import CampusError

            raise CampusError("catalog-write-failed", "Synthetic catalog write failed.", "Retry.", "error")
        if fixture == "p6-catalog-crash":
            raise RuntimeError("synthetic catalog crash")
        return real_write(catalog, path)

    stack.enter_context(patch.object(player_module, "write_catalog", traced_write))

    original_stderr = sys.stderr

    class _DiagnosticStderr:
        def write(self, text: str) -> int:
            if "campusctl-play-diagnostic:" in text:
                actions.append(["diagnostic", text.strip()])
            return original_stderr.write(text)

        def flush(self) -> None:
            original_stderr.flush()

        def isatty(self) -> bool:
            return False

    sys.stderr = _DiagnosticStderr()

    if fixture in {"p5-callbacks", "p5-callback-raises", "p5-video-callbacks"}:
        real_progress = lectures_module._progress_output

        def wrapped_progress():
            callback, finish = real_progress()

            def progress(event: dict) -> None:
                actions.append(
                    [
                        "callback",
                        event.get("type"),
                        event.get("entity_id"),
                        event.get("position_seconds"),
                        event.get("duration_seconds"),
                        clock.timestamp,
                    ]
                )
                if fixture == "p5-callback-raises" and event.get("type") == "position":
                    raise RuntimeError("synthetic progress failure")
                callback(event)

            def finish_progress() -> None:
                actions.append(["callback", "finish"])
                finish()

            return progress, finish_progress

        stack.enter_context(patch.object(lectures_module, "_progress_output", wrapped_progress))
    if fixture == "p6-started-gap":
        _arm_panopto_started_gap(page, clock)
    if fixture == "p6-fractional-elapsed":
        original_status = page.panopto_status

        def status() -> dict:
            value = original_status()
            if not page.panopto_paused:
                clock.timestamp += 0.26
            return value

        page.panopto_status = status


def _arm_panopto_started_gap(page: _P5Page, clock: _P5Clock) -> None:
    """Advance once between the handler's started_at read and a later failing state read."""
    original_monotonic = clock.monotonic
    original_click = page._click
    original_status = page.panopto_status
    armed = False

    def monotonic() -> float:
        nonlocal armed
        value = original_monotonic()
        if armed:
            armed = False
            clock.timestamp += 0.002
        return value

    def click(selector: str) -> None:
        nonlocal armed
        original_click(selector)
        lowered = selector.lower()
        if (
            "play" in lowered
            and "titledetailcontents" not in lowered
            and "btnpreviewclose" not in lowered
            and "btn_popclose" not in lowered
        ):
            clock.timestamp = 2.049
            armed = True

    def status() -> dict:
        if not page.panopto_paused:
            clock.timestamp = 4.1
            raise RuntimeError("synthetic browser read failure at fixed external deadline")
        return original_status()

    clock.monotonic = monotonic
    page._click = click
    page.panopto_status = status


def _invoke_unhashable_preview() -> int:
    """Preview lookup of an unhashable media value, before the modal."""
    import asyncio

    from campusctl.providers.cnu.player_preview import _play_visible_lecture
    from campusctl.providers.cnu.player_types import _PlaybackFailure

    page = _DIRECT_PAGE
    try:
        asyncio.run(_play_visible_lecture(page, "row-1", 1.0, media=[]))
    except _PlaybackFailure as error:
        print(
            json.dumps(
                {
                    "code": error.code,
                    "elapsed": error.elapsed_seconds,
                    "error": "_PlaybackFailure",
                    "modal_open": page.modal_open,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"error": None, "modal_open": page.modal_open}, separators=(",", ":"), sort_keys=True))
    return 0


def _invoke_prepare_guard() -> int:
    """Queue preparation keeps its string guard; each input is one call."""
    import asyncio
    from collections import UserString

    from campusctl.providers.cnu.player import _prepare_media

    page = _DIRECT_PAGE
    config = {"playback": {"default_speed": 1.0}}

    async def run() -> list[dict]:
        results = []
        for index, spec in enumerate(("missing", "none", "userstring", "list", "offline", "youtube"), 1):
            page.authenticated = False
            page.sso_submits = 0
            row = _lecture_row(
                f"cnu_lecture:course.invalid:row-{index}",
                f"Synthetic {spec}",
                "course.invalid",
                "Synthetic course",
            )
            if spec == "missing":
                row.pop("media", None)
            elif spec == "none":
                row["media"] = None
            elif spec == "userstring":
                row["media"] = UserString("youtube")
            elif spec == "list":
                row["media"] = []
            elif spec == "offline":
                row["media"] = "offline"
            else:
                row["media"] = "youtube"
            error_name = None
            try:
                await _prepare_media(page, config, [row])
            except Exception as error:
                error_name = type(error).__name__
            results.append({"error": error_name, "spec": spec, "sso": page.sso_submits})
        return results

    print(json.dumps(asyncio.run(run()), separators=(",", ":")))
    return 0


def invoke_play(case: Case) -> int:
    """Call play_lectures directly so missing, None and unknown media skip catalog validation."""
    import asyncio

    from campusctl.providers.cnu.player import play_lectures

    if case.get("fixture") == "p6-unhashable-preview":
        return _invoke_unhashable_preview()
    if case.get("fixture") == "p6-prepare-guard":
        return _invoke_prepare_guard()
    lectures = []
    for index, spec in enumerate(case.get("direct_media", ()), 1):
        row = _lecture_row(
            f"cnu_lecture:course.invalid:row-{index}",
            f"Synthetic {spec}",
            "course.invalid",
            "Synthetic course",
        )
        if spec == "missing":
            row.pop("media", None)
        elif spec == "none":
            row["media"] = None
        elif spec == "userstring-youtube":
            from collections import UserString

            row["media"] = UserString("youtube")
        elif spec == "bad-id":
            row["entity_id"] = "not-a-lecture-id"
        else:
            row["media"] = spec
        lectures.append(row)
    events: list[dict] = []
    try:
        result, error = asyncio.run(
            play_lectures(_DIRECT_PAGE, {"playback": {"default_speed": 1.0}}, lectures, progress=events.append)
        )
    except asyncio.CancelledError:
        # A cancelled queue returns nothing; the events show which items had reported before it.
        finished = [[event["entity_id"], event["outcome"]] for event in events if event["type"] == "finished"]
        print(json.dumps({"raised": "CancelledError", "finished": finished}, separators=(",", ":")))
        return 1
    if error is None:
        code = None
    elif isinstance(error, list):
        code = [item.code for item in error]
    else:
        code = error.code
    # Keep insertion order: item key order and reason_code position are part of the output contract.
    print(json.dumps({"error": code, "items": result["items"]}, separators=(",", ":")))
    return 0 if error is None else 1


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
    return [*result, *_command_owner_cases(), *_fetch_cases(), *_p4b_cases(), *_l4_cases(), *_p5_cases(), *_p6_cases()]


@contextmanager
def prepare(root: Path, case: Case, actions: list):
    from playwright.sync_api._context_manager import PlaywrightContextManager

    # A command provider avoids touching a real keyring even when reporting doctor status.
    fixture = case["fixture"]
    speed = fixture if fixture in {"1", "1.0", "1.25", "1.5", "1.1", "2.0", "true", "false", '"1.25"'} else "1.0"
    executable = root / "browser"
    executable.write_bytes(b"synthetic browser marker")
    headed = fixture.startswith("progress-") or fixture.startswith("p5-") or fixture.startswith("p6-")
    if fixture.startswith("p3-"):
        _write_p3_files(root, fixture, executable)
    elif fixture == "p4b-unreadable":
        catalog = root / "data" / "catalog" / "lectures.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text("not-json", encoding="utf-8")
    elif fixture in {"p4b-empty", "p4b-warnings"}:
        _write_p4b_catalogs(root, fixture)
    elif fixture.startswith("p5-") or fixture.startswith("p6-"):
        (root / "config" / "config.toml").write_text(
            'provider = "cnu"\n[credentials]\nprovider = "command"\n'
            f"command = [{json.dumps(str(root / 'synthetic-helper'))}]\n"
            f"[browser]\nheadless = false\nexecutable_path = {json.dumps(str(executable))}\n"
            "[playback]\ndefault_speed = 1.0\n",
            encoding="utf-8",
        )
        _write_p5_catalog(root, fixture)
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
        elif fixture.startswith("p5-") or fixture.startswith("p6-"):
            _install_p5_fakes(stack, actions, fixture)
        if "fetch_fake" in case:
            _install_fetch_fakes(stack, actions, case["fetch_fake"])
        if case["name"] == "owner-courses-list-human" or case.get("trace_publication"):
            _trace_publication()
        yield state
