from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from typing import Any

from campusctl import __version__
from campusctl.presentation import render_human

ENTITY_ID = "cnu_lecture:course-a:lecture-row-001"


def _lecture(
    *,
    entity_id: str = ENTITY_ID,
    title: str = "자료구조와 알고리즘의 이해",
    course_id: str = "course-a",
    course_label: str = "Example Course",
    media: str = "video",
    due_date: str | None = "2026-10-05T23:59",
    available_from: str | None = None,
    progress_text: str | None = "0분/20분",
    completion: str = "incomplete",
    is_open: bool | None = True,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "course": {"id": course_id, "label": course_label},
        "kind": "lecture",
        "title": title,
        "week": "1",
        "sequence": "1",
        "progress_text": progress_text,
        "duration_minutes": 20,
        "available_from": available_from,
        "due_date": due_date,
        "late_until": None,
        "media": media,
        "attendance_counted": None,
        "open": is_open,
        "completion": completion,
        "provider_state": None,
    }


def _render(command: str, result: dict[str, Any], *, width: int = 80, errors: list[dict] | None = None) -> str:
    stream = StringIO()
    render_human(
        command,
        {"status": "partial" if errors else "ok", "result": result, "errors": errors or []},
        stream,
        width=width,
    )
    return stream.getvalue()


def test_explicit_lecture_sync_has_visible_human_summary() -> None:
    output = _render("sync.lectures", {"courses": 2, "lectures": 3, "incomplete": 1})
    assert "Synced 2 courses, 3 lectures, 1 unfinished." in output


def test_single_catalog_age_and_sync_counts_use_singular_nouns() -> None:
    output = _render("lectures.list", {"lectures": [], "cache": {"age_seconds": 1}})
    assert "Catalog generated 1 second ago." in output
    assert "1 seconds ago" not in output
    output = _render("sync.lectures", {"courses": 1, "lectures": 1, "incomplete": 1})
    assert "Synced 1 course, 1 lecture, 1 unfinished." in output


def test_lectures_list_renders_korean_titles_and_local_due_dates_at_wide_width() -> None:
    first = _lecture(due_date="2026-10-05T23:59:42")
    second = _lecture(
        entity_id="cnu_lecture:course-a:lecture-row-002",
        title="운영체제와 컴퓨터 시스템의 구조",
        media="youtube",
        due_date=None,
        progress_text="3분 2초/20분",
    )
    third = _lecture(
        entity_id="cnu_lecture:course-b:lecture-row-003",
        title="컴퓨터 네트워크",
        course_id="course-b",
        course_label="Other Course",
        due_date=None,
        progress_text=None,
        completion="complete",
    )
    fourth = _lecture(
        entity_id="cnu_lecture:course-a:lecture-row-004",
        title="Weekly lab",
        due_date=None,
        progress_text="0분/20분",
    )
    output = _render(
        "lectures.list",
        {"cache": {"generated_at": "2026-09-24T12:00:00Z"}, "lectures": [first, second, third, fourth]},
        width=80,
    )

    assert "4 lectures (updated 2026-09-24 12:00 UTC)" in output
    assert "2026-09-24T12:00:00Z" not in output
    assert "자료구조와 알고리즘의 이해" in output
    assert "2026-10-05 23:59" in output
    assert "[youtube]" in output
    assert "[video]" not in output
    assert "done" in output
    assert "not started" in output
    assert "in progress" in output
    assert "Title" not in output
    assert "|" not in output
    assert f"    {ENTITY_ID}" in output
    assert "To play one: campusctl lectures play " + ENTITY_ID in output
    assert "To refresh: campusctl sync" in output
    assert all(line == line.rstrip() for line in output.splitlines())
    # "운영체제와 컴퓨터 시스템의 구조" is 31 cells; due is 16, so status starts at cell 53.
    assert "  자료구조와 알고리즘의 이해       2026-10-05 23:59  not started" in output
    assert "  운영체제와 컴퓨터 시스템의 구조  -                 in progress  [youtube]" in output
    assert "  Weekly lab                       -                 not started" in output


def test_lectures_list_uses_vertical_layout_at_40_columns() -> None:
    lecture = _lecture(title="한국어 강의 제목이 좁은 화면에서도 잘리지 않도록 표시됩니다", media="youtube")
    playable = _lecture(
        entity_id="cnu_lecture:course-a:lecture-row-002",
        title="Play example",
        due_date=None,
    )
    output = _render(
        "lectures.list",
        {"cache": {"generated_at": "2026-09-24T12:00:00Z"}, "lectures": [lecture, playable]},
        width=40,
    )

    assert "Course:" in output
    assert "Title:" in output
    assert "…" in output
    assert "Due: 2026-10-05 23:59" in output
    playable_id = "cnu_lecture:course-a:lecture-row-002"
    assert f"    {playable_id}" in output
    assert f"To play one:\n  campusctl lectures play {ENTITY_ID}" in output
    assert "To refresh:\n  campusctl sync" in output
    assert all(line == line.rstrip() for line in output.splitlines())
    field_lines = [
        line for line in output.splitlines() if line.lstrip().startswith(("Title:", "Due:", "Status:", "Media:"))
    ]
    assert len(field_lines) == 7
    assert all(line.startswith("  ") and not line.startswith("    ") for line in field_lines)
    assert all(line == line.rstrip() for line in output.splitlines())


def test_empty_lecture_list_explains_all_and_sync_hints() -> None:
    output = _render(
        "lectures.list",
        {"cache": {"generated_at": "2026-09-24T12:00:00Z"}, "lectures": []},
        width=40,
    )

    assert "0 lectures" in output
    assert "Nothing unfinished." in output
    assert "To include completed or recorded\nlectures:\n  campusctl lectures list --all" in output
    assert "To refresh:\n  campusctl sync" in output


def test_empty_states_wrap_at_narrow_width() -> None:
    empty_list = _render("lectures.list", {"lectures": []}, width=12)
    empty_playback = _render("lectures.play", {"items": []}, width=12)

    command_lines = {"  campusctl lectures list --all", "  campusctl sync"}
    assert all(len(line) <= 12 for line in empty_list.splitlines() if line not in command_lines)
    assert all(len(line) <= 12 for line in empty_playback.splitlines())


def test_populated_list_wraps_prefixes_at_narrow_width() -> None:
    lecture = _render("lectures.list", {"lectures": [_lecture(title="Loops")]}, width=8)
    assert "Loops" in lecture
    course = _render("courses.list", {"courses": [{"course_id": "course-a", "label": "Course A"}]}, width=8)

    lecture_ids = {f"    {ENTITY_ID}", f"  {ENTITY_ID}"}
    lecture_commands = {
        f"  campusctl lectures play {ENTITY_ID}",
        "  campusctl sync",
    }
    assert "Loops" in lecture
    assert all(len(line) <= 8 for line in lecture.splitlines() if line not in lecture_ids | lecture_commands)
    assert all(len(line) <= 8 for line in course.splitlines())


def test_narrow_ascii_output_wraps_every_line_except_full_ids() -> None:
    lecture = _lecture(title="Loops", due_date="2026-10-05T23:59", course_label="Course A")
    output = _render(
        "lectures.list",
        {"cache": {"generated_at": "2026-09-24T12:00:00Z"}, "lectures": [lecture]},
        width=20,
    )

    lines = output.splitlines()
    id_lines = {f"    {ENTITY_ID}", f"  {ENTITY_ID}"}
    command_lines = {f"  campusctl lectures play {ENTITY_ID}", "  campusctl sync"}
    assert lines[0] == "1 lecture"
    assert all(len(line) <= 20 for line in lines if line not in id_lines | command_lines)
    assert f"  campusctl lectures play {ENTITY_ID}" in output
    assert "To refresh:\n  campusctl sync" in output


def test_recorded_status_and_youtube_play_hint() -> None:
    youtube = _lecture(media="youtube")
    youtube_output = _render("lectures.list", {"lectures": [youtube]}, width=80)
    assert f"To play one: campusctl lectures play {ENTITY_ID}" in youtube_output

    recorded = _lecture(media="youtube", completion="recorded")
    recorded_output = _render("lectures.list", {"lectures": [recorded]}, width=80)
    assert "watched (not counted)" in recorded_output
    assert "To play one:" not in recorded_output


def test_recorded_playback_result_explains_no_attendance_credit_or_lms_completion() -> None:
    output = _render(
        "lectures.play",
        {"items": [{"entity_id": ENTITY_ID, "outcome": "recorded"}]},
        width=120,
    )

    assert "Watched (not counted):" in output
    assert "The LMS row reports full watch progress" in output
    assert "does not count for attendance" in output
    assert "or mark the lecture complete" in output
    assert "Playback: 1 recorded." in output


def test_list_hides_play_hint_without_incomplete_video() -> None:
    output = _render("lectures.list", {"lectures": [_lecture(completion="complete")]}, width=80)

    assert "To play one:" not in output


def test_play_hint_skips_completed_rows_and_wide_layout_stays_tabular() -> None:
    completed_id = "cnu_lecture:course-a:lecture-done"
    incomplete_id = "cnu_lecture:course-a:lecture-next"
    result = {
        "lectures": [
            _lecture(entity_id=completed_id, title="Loops", due_date=None, completion="complete"),
            _lecture(entity_id=incomplete_id, title="Loops", due_date=None),
        ]
    }
    narrow = _render("lectures.list", result, width=20)
    wide = _render("lectures.list", result, width=100)

    assert narrow.startswith("2 lectures\n")
    assert narrow.rsplit("\nTo refresh:", 1)[0].endswith(f"  campusctl lectures play {incomplete_id}")
    assert f"To play one:\n  campusctl lectures play {incomplete_id}" in narrow
    assert "  Loops  -    done" in wide
    assert "Title:" not in wide
    assert wide.rsplit("To play one:", 1)[1].startswith(f" campusctl lectures play {incomplete_id}")


def test_list_marks_closed_rows_and_play_hint_selects_only_open_lectures() -> None:
    closed_id = "cnu_lecture:course-a:lecture-future"
    open_id = "cnu_lecture:course-a:lecture-open"
    output = _render(
        "lectures.list",
        {
            "lectures": [
                _lecture(
                    entity_id=closed_id,
                    title="Future lecture",
                    due_date=None,
                    available_from="2026-11-10T00:00",
                    is_open=False,
                ),
                _lecture(entity_id=open_id, title="Open lecture", due_date=None, is_open=True),
            ]
        },
        width=80,
    )

    assert "opens 11-10" in output
    hint = output.rsplit("To play one:", 1)[1]
    assert f"campusctl lectures play {open_id}" in hint
    assert closed_id not in hint


def test_no_open_playable_lecture_shows_earliest_opening_date() -> None:
    output = _render(
        "lectures.list",
        {
            "lectures": [
                _lecture(
                    entity_id="cnu_lecture:course-a:lecture-later",
                    title="Later lecture",
                    due_date=None,
                    available_from="2026-11-10",
                    is_open=False,
                ),
                _lecture(
                    entity_id="cnu_lecture:course-a:lecture-sooner",
                    title="Sooner lecture",
                    due_date=None,
                    available_from="2026-10-05T00:00",
                    is_open=False,
                ),
            ]
        },
        width=80,
    )

    assert "opens 11-10" in output
    assert "opens 10-05" in output
    assert "No lectures are open to play yet. Next opens: 10-05." in output
    assert "To play one:" not in output


def test_no_open_playable_lecture_without_opening_dates_says_dates_are_unpublished() -> None:
    output = _render(
        "lectures.list",
        {
            "lectures": [
                _lecture(
                    entity_id="cnu_lecture:course-a:lecture-unknown-opening",
                    title="Future lecture",
                    due_date=None,
                    available_from=None,
                    is_open=False,
                )
            ]
        },
        width=80,
    )

    assert "No lectures are open to play yet. Opening dates are not published yet." in output
    assert "To play one:" not in output


def test_wide_layout_falls_back_when_fixed_columns_leave_no_title_room() -> None:
    lecture = _lecture(title="Loops", due_date="D" * 50)
    output = _render("lectures.list", {"lectures": [lecture]}, width=60)

    ids = {f"    {ENTITY_ID}", f"  {ENTITY_ID}"}
    commands = {f"  campusctl lectures play {ENTITY_ID}", "  campusctl sync"}
    assert "  Title: Loops" in output
    assert all(len(line) <= 60 for line in output.splitlines() if line not in ids | commands)


def test_list_preserves_combining_and_format_characters_in_title_width() -> None:
    combining_title = "가" * 15 + "e\u0301"
    joined_emoji_title = "a" * 27 + "👩‍💻"
    output = _render(
        "lectures.list",
        {
            "lectures": [
                _lecture(title=combining_title),
                _lecture(entity_id="cnu_lecture:course-a:lecture-row-002", title=joined_emoji_title),
            ]
        },
        width=40,
    )

    assert f"  Title: {combining_title}" in output
    assert f"  Title: {joined_emoji_title}" in output


def test_doctor_wraps_checklist_at_narrow_width() -> None:
    output = _render("doctor", {"config": {"present": True}}, width=20)

    assert output.splitlines() == ["[ok] Configuration", "file present", "Ready."]
    assert all(len(line) <= 20 for line in output.splitlines())


def test_renderer_uses_terminal_width_when_omitted(monkeypatch) -> None:
    monkeypatch.setattr(
        "campusctl.presentation.shutil.get_terminal_size",
        lambda fallback: SimpleNamespace(columns=20, lines=24),
    )
    stream = StringIO()
    render_human(
        "doctor",
        {"status": "ok", "result": {"config": {"present": True}}, "errors": []},
        stream,
    )

    assert stream.getvalue() == "[ok] Configuration\nfile present\nReady.\n"


def test_play_wraps_outcomes_and_keeps_each_id_on_its_own_line() -> None:
    output = _render(
        "lectures.play",
        {"items": [{"entity_id": ENTITY_ID, "outcome": "completed"}]},
        width=20,
    )

    assert "Completed:" in output
    assert f"  {ENTITY_ID}" in output
    assert all(len(line) <= 20 for line in output.splitlines() if ENTITY_ID not in line)


def test_missing_play_outcome_and_unknown_completion_stay_unknown() -> None:
    playback = _render(
        "lectures.play",
        {"items": [{"entity_id": ENTITY_ID}, {"entity_id": "other", "outcome": None}]},
        width=80,
    )
    listing = _render("lectures.list", {"lectures": [_lecture(completion=None)]}, width=80)

    assert "2 unknown" in playback
    assert playback.count("Unknown:") == 2
    assert "unknown" in listing
    assert "unfinished" not in listing


def test_null_and_unavailable_statuses_are_not_invented() -> None:
    assert "status is unknown" in _render("config.init", {"created": None})
    assert "Browser setup status is unknown." in _render("setup", {"browser": {"action": None}})
    assert "Chromium installation status is unknown." in _render("setup", {"browser": {"action": "installed"}})
    assert "Custom browser executable is unavailable." in _render(
        "setup", {"browser": {"action": "skipped-custom-executable", "installed": False}}
    )
    helper = _render(
        "auth.status",
        {"provider": "command", "helper": "helper.exe", "configured": True, "check": "ok"},
    )
    assert "Credential helper helper.exe is configured; check: ok." in helper
    assert "check: not run" in _render(
        "auth.status", {"provider": "command", "helper": "helper.exe", "configured": True}
    )
    keyring = _render("auth.status", {"provider": "keyring", "backend": "Windows Vault", "configured": True})
    assert "Windows Vault" in keyring


def test_partial_play_keeps_completed_item_and_summarizes_all_outcomes() -> None:
    output = _render(
        "lectures.play",
        {
            "items": [
                {"entity_id": ENTITY_ID, "outcome": "completed"},
                {"entity_id": "cnu_lecture:course-a:failed", "outcome": "failed"},
                {"entity_id": "cnu_lecture:course-a:unverified", "outcome": "unverified"},
                {"entity_id": "cnu_lecture:course-a:later", "outcome": "not-started"},
            ]
        },
        width=120,
        errors=[
            {
                "code": "playback-failed",
                "message": "A requested lecture could not be played.",
                "remediation": "Check the LMS session, then retry.",
            }
        ],
    )

    assert "Completed:" in output
    assert f"  {ENTITY_ID}" in output
    assert "Failed:" in output
    assert "Unverified:" in output
    assert "Not started:" in output
    assert "Playback: 1 completed, 1 unverified, 1 failed, 1 not started." in output
    assert "A requested lecture could not be played; check the LMS session, then retry (playback-failed)." in output


def test_narrow_error_and_next_step_commands_stay_whole() -> None:
    error = _render(
        "lectures.list",
        {},
        width=20,
        errors=[
            {
                "code": "catalog-missing",
                "message": "The lecture catalog is missing.",
                "remediation": "Run 'campusctl sync --only lectures' to create it.",
            }
        ],
    )
    next_step = _render(
        "config.init",
        {"config_path": "config.toml", "created": True, "next": ["campusctl sync"]},
        width=20,
    )
    usage = _render("usage", {}, width=20)

    assert "command below" in error
    assert "  campusctl sync --only lectures" in error
    assert "Next:\n  campusctl sync" in next_step
    assert "To see commands:\n  campusctl --help" in usage


def test_doctor_renders_checklist_then_first_error_fix() -> None:
    output = _render(
        "doctor",
        {
            "config": {"present": True},
            "credentials": {"configured": False},
            "browser": {"mode": "local", "playwright_importable": True, "chromium_installed": False},
            "display_available": True,
            "catalog": {"present": False, "generated_at": None},
        },
        errors=[
            {
                "code": "credentials-not-configured",
                "message": "No saved credentials are available.",
                "remediation": "Run 'campusctl auth set' to save them.",
            }
        ],
    )

    assert "[ok] Configuration file present" in output
    assert "[!!] Credentials not configured" in output
    assert "[ok] Playwright available" in output
    assert "[!!] Chromium not installed" in output
    assert "[!!] Lecture catalog missing" in output
    assert output.index("[!!] Lecture catalog missing") < output.index("credentials-not-configured")
    assert "run the command below to save them" in output
    assert "  campusctl auth set" in output
    assert "Ready." not in output
    assert "Almost ready." not in output


def test_doctor_cdp_mode_ignores_display_and_summarizes_catalog_error() -> None:
    output = _render(
        "doctor",
        {
            "browser": {
                "mode": "cdp",
                "playwright_importable": False,
                "chromium_installed": False,
            },
            "display_available": False,
            "catalog": {"present": False},
        },
        width=120,
        errors=[
            {
                "code": "catalog-missing",
                "message": "Lecture catalog not found: catalog.json.",
                "remediation": "Run 'campusctl sync --only lectures' to create it.",
            }
        ],
    )

    assert "[!!] Display unavailable" not in output
    assert "[!!] Playwright not installed" not in output
    assert "[!!] Chromium not installed" not in output
    assert "[!!] Lecture catalog missing" not in output
    assert "[ok] Using an existing browser (connection is checked when you run sync or play)" in output
    assert "Almost ready. Next: campusctl sync" in output
    assert "catalog-missing" not in output
    assert "Ready." not in output


def test_doctor_local_mode_keeps_display_and_catalog_errors() -> None:
    output = _render(
        "doctor",
        {
            "browser": {"mode": "local", "playwright_importable": True, "chromium_installed": True},
            "display_available": False,
            "catalog": {"present": False},
        },
        width=120,
        errors=[
            {
                "code": "catalog-missing",
                "message": "Lecture catalog not found: catalog.json.",
                "remediation": "Run 'campusctl sync --only lectures' to create it.",
            }
        ],
    )

    assert "[!!] Display unavailable" in output
    assert "[!!] Lecture catalog missing" in output
    assert "(catalog-missing)" in output
    assert "Ready." not in output
    assert "Almost ready." not in output
    assert "Using an existing browser" not in output


def test_doctor_keeps_other_errors_alongside_catalog_error() -> None:
    output = _render(
        "doctor",
        {"browser": {"mode": "cdp"}, "catalog": {"present": False}},
        width=120,
        errors=[
            {
                "code": "catalog-missing",
                "message": "Lecture catalog not found: catalog.json.",
                "remediation": "Run 'campusctl sync --only lectures' to create it.",
            },
            {
                "code": "credentials-not-configured",
                "message": "No saved credentials are available.",
                "remediation": "Run 'campusctl auth set' to save them.",
            },
        ],
    )

    assert "(catalog-missing)" in output
    assert "(credentials-not-configured)" in output
    assert "Almost ready." not in output


def test_compact_command_renderers() -> None:
    courses = _render("courses.list", {"courses": [{"course_id": "course-a", "label": "Example Course"}]})
    assert "Example Course" in courses
    assert "|" not in courses
    assert all(line == line.rstrip() for line in courses.splitlines())
    assert "Synced 2 courses, 8 lectures, 5 unfinished." in _render(
        "sync", {"courses": 2, "lectures": 8, "incomplete": 5, "failed_courses": []}
    )
    assert "Failed: Example Course (course-a)" in _render(
        "sync",
        {
            "courses": 2,
            "lectures": 8,
            "incomplete": 5,
            "failed_courses": [{"course_id": "course-a", "label": "Example Course"}],
        },
    )
    assert "Credentials are saved in the OS keyring (Windows Vault)." in _render(
        "auth.status", {"provider": "keyring", "backend": "Windows Vault", "configured": True}
    )
    assert "Configuration created at config.toml." in _render(
        "config.init", {"config_path": "config.toml", "created": True, "next": ["campusctl sync"]}
    )
    assert "Chromium was installed." in _render(
        "setup", {"browser": {"installed": True, "action": "installed"}, "next": ["campusctl sync"]}
    )
    assert f"campusctl version {__version__}." in _render("version", {"version": __version__})
    assert "campusctl --help" in _render("usage", {})
