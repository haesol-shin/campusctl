from __future__ import annotations

from campusctl.providers.cnu.courses import parse_courses
from campusctl.providers.cnu.lectures import parse_learning_rows, parse_progress_seconds, progress_is_full


def test_parse_courses_deduplicates_and_drops_missing_ids() -> None:
    assert parse_courses(
        [
            {"course_id": " course-a ", "label": " Course A ", "class_no": " 001 "},
            {"course_id": "course-a", "label": "Duplicate", "class_no": "002"},
            {"course_id": "  ", "label": "No ID", "class_no": None},
            {"course_id": "course-b", "label": None, "class_no": ""},
        ]
    ) == [
        {"course_id": "course-a", "label": "Course A", "class_no": "001"},
        {"course_id": "course-b", "label": "course-b", "class_no": None},
    ]


def test_parse_learning_rows_maps_sanitized_cnu_fixtures_to_catalog_records() -> None:
    records = parse_learning_rows(
        [
            {
                "id": "learning4_3_1",
                "moduletype": "LV",
                "state": "N",
                "openyn": "Y",
                "weekno": "4",
                "seqno": "3",
                "title": "Example video",
                "badge_text": "동영상",
                "progress_text": "11분 59초/28분",
                "period_text": "26-09-22 00:00 ~ 26-10-05 23:59",
                "late_until_text": "지각 기간 : 26-10-07 23:59",
                "start_date": "2026-09-22",
                "row_text": "Example video 11분 59초/28분 미완료",
            },
            {
                "id": "learning4_4_1",
                "moduletype": "LV",
                "state": "N",
                "openyn": "Y",
                "weekno": "4",
                "seqno": "4",
                "title": "Example YouTube",
                "badge_text": "유튜브",
                "progress_text": "0분/15분",
                "period_text": None,
                "late_until_text": None,
                "start_date": "2026-09-23",
                "row_text": "Example YouTube 0분/15분 출석 미반영",
            },
            {
                "id": "learning4_5_1",
                "moduletype": "LV",
                "state": "N",
                "openyn": "Y",
                "weekno": "4",
                "seqno": "5",
                "title": "Example Offline",
                "badge_text": "오프라인",
                "progress_text": "0분/60분",
                "period_text": None,
                "late_until_text": None,
                "start_date": None,
                "row_text": "Example Offline 0분/60분 출석 미반영",
            },
            {
                "id": "learning3_1_1",
                "moduletype": "LV",
                "state": "F",
                "openyn": "N",
                "weekno": "3",
                "seqno": "1",
                "title": "   ",
                "badge_text": "자료",
                "progress_text": None,
                "period_text": "26-09-15 00:00 ~ 26-09-28 23:59",
                "late_until_text": "지각 기간 : 26-09-30 23:59",
                "start_date": "2026-09-15",
                "row_text": "Example completed lecture",
            },
            {"id": "assignment-1", "moduletype": "HW", "state": "F", "title": "Not a lecture"},
            {"id": "  ", "moduletype": "LV", "state": "N", "title": "Missing row ID"},
        ],
        {"course_id": "course-a", "label": "Course A"},
    )

    assert records == [
        {
            "entity_id": "cnu_lecture:course-a:learning4_3_1",
            "course": {"id": "course-a", "label": "Course A"},
            "kind": "lecture",
            "title": "Example video",
            "week": "4",
            "sequence": "3",
            "progress_text": "11분 59초/28분",
            "duration_minutes": 28,
            "available_from": "2026-09-22T00:00",
            "due_date": "2026-10-05T23:59",
            "late_until": "2026-10-07T23:59",
            "media": "video",
            "attendance_counted": None,
            "open": True,
            "completion": "incomplete",
            "provider_state": "N",
        },
        {
            "entity_id": "cnu_lecture:course-a:learning4_4_1",
            "course": {"id": "course-a", "label": "Course A"},
            "kind": "lecture",
            "title": "Example YouTube",
            "week": "4",
            "sequence": "4",
            "progress_text": "0분/15분",
            "duration_minutes": 15,
            "available_from": "2026-09-23",
            "due_date": None,
            "late_until": None,
            "media": "youtube",
            "attendance_counted": False,
            "open": True,
            "completion": "incomplete",
            "provider_state": "N",
        },
        {
            "entity_id": "cnu_lecture:course-a:learning4_5_1",
            "course": {"id": "course-a", "label": "Course A"},
            "kind": "lecture",
            "title": "Example Offline",
            "week": "4",
            "sequence": "5",
            "progress_text": "0분/60분",
            "duration_minutes": 60,
            "available_from": None,
            "due_date": None,
            "late_until": None,
            "media": "offline",
            "attendance_counted": False,
            "open": True,
            "completion": "incomplete",
            "provider_state": "N",
        },
        {
            "entity_id": "cnu_lecture:course-a:learning3_1_1",
            "course": {"id": "course-a", "label": "Course A"},
            "kind": "lecture",
            "title": "강의영상",
            "week": "3",
            "sequence": "1",
            "progress_text": None,
            "duration_minutes": None,
            "available_from": "2026-09-15T00:00",
            "due_date": "2026-09-28T23:59",
            "late_until": "2026-09-30T23:59",
            "media": "other",
            "attendance_counted": None,
            "open": False,
            "completion": "complete",
            "provider_state": "F",
        },
    ]


def test_progress_text_parses_full_counters_and_rejects_malformed_values() -> None:
    assert parse_progress_seconds("11분 59초 / 12분") == (719, 720)
    assert parse_progress_seconds("15분 2초/15분") == (902, 900)
    assert progress_is_full("15분 2초/15분")
    assert not progress_is_full("14분 59초/15분")
    for value in (
        None,
        "",
        "20分/",
        "20 minutes/20 minutes",
        "-1분/20분",
        "0분/0분",
        "0분 60초/15분",
        "0분/15분 60초",
        "0분 900초/15분",
    ):
        assert parse_progress_seconds(value) is None


def test_parse_learning_rows_marks_only_fully_watched_not_counted_rows_recorded() -> None:
    records = parse_learning_rows(
        [
            {
                "id": "youtube-recorded",
                "moduletype": "LV",
                "state": "N",
                "badge_text": "유튜브",
                "progress_text": "15분/15분",
                "row_text": "출석 미반영",
            },
            {
                "id": "youtube-counted",
                "moduletype": "LV",
                "state": "N",
                "badge_text": "유튜브",
                "progress_text": "15분/15분",
                "row_text": "출석 반영",
            },
            {
                "id": "youtube-complete",
                "moduletype": "LV",
                "state": "F",
                "badge_text": "유튜브",
                "progress_text": "15분/15분",
                "row_text": "출석 미반영",
            },
        ],
        {"course_id": "course-a"},
    )

    assert [(record["completion"], record["attendance_counted"]) for record in records] == [
        ("recorded", False),
        ("incomplete", None),
        ("complete", False),
    ]


def test_parse_learning_rows_requires_course_id() -> None:
    assert parse_learning_rows([{"id": "row-1", "moduletype": "LV"}], {"label": "Course A"}) == []
