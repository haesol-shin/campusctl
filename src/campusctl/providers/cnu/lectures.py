"""Lecture row extraction and catalog normalization for the CNU LMS."""

from __future__ import annotations

import re
from datetime import datetime

LEARNING_ROW_SELECTOR = ".learningRow"
EXTRACT_LEARNING_ROWS_JS = r"""() => {
    const rows = [];
    document.querySelectorAll('.learningRow').forEach((row) => {
        const title = row.querySelector('[data-act="titleDetailContents"]');
        const badge = row.querySelector('.td-item .badge');
        const rightColumn = row.querySelector('.col-lg-4.text-end') || row.querySelector('.col-lg-4');
        const progress = rightColumn ? rightColumn.querySelector('span.ms-2') : null;
        const listItems = Array.from(row.querySelectorAll('.contArea ul > li'));
        const period = listItems.find((item) => /\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*~\s*\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}/.test(item.textContent || ''));
        const latePeriod = listItems.find((item) => (item.textContent || '').includes('지각 기간'));
        rows.push({
            id: row.id,
            moduletype: row.getAttribute('data-moduletype'),
            state: row.getAttribute('data-state'),
            openyn: row.getAttribute('data-openyn'),
            weekno: row.getAttribute('data-weekno'),
            seqno: row.getAttribute('data-seqno'),
            start_date: row.getAttribute('data-strdt'),
            title: title ? (title.textContent || '').trim() : '',
            badge_text: badge ? (badge.textContent || '').trim() : null,
            progress_text: progress ? (progress.textContent || '').trim() : null,
            period_text: period ? (period.textContent || '').trim() : null,
            late_until_text: latePeriod ? (latePeriod.textContent || '').trim() : null,
            row_text: (row.textContent || '').trim()
        });
    });
    return rows;
}"""

_LOCAL_DATETIME = re.compile(r"(\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})")
_PERIOD = re.compile(r"(\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*(\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})")
_PROGRESS = re.compile(r"^\s*(\d+)\s*분(?:\s*(\d+)\s*초)?\s*/\s*(\d+)\s*분(?:\s*(\d+)\s*초)?\s*$")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _nullable_text(value: object) -> str | None:
    return _text(value) or None


def _local_datetime(value: str) -> str | None:
    match = _LOCAL_DATETIME.search(value)
    if match is None:
        return None
    try:
        parsed = datetime(
            2000 + int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
        )
    except ValueError:
        return None
    return parsed.isoformat(timespec="minutes")


def _period_dates(value: object) -> tuple[str | None, str | None]:
    match = _PERIOD.search(_text(value))
    if match is None:
        return None, None
    return _local_datetime(match.group(1)), _local_datetime(match.group(2))


def _duration_minutes(value: object) -> int | None:
    _, separator, after_slash = _text(value).rpartition("/")
    if not separator:
        return None
    match = re.search(r"^\s*(\d+)\s*분", after_slash)
    return int(match.group(1)) if match else None


def parse_progress_seconds(value: object) -> tuple[int, int] | None:
    """Parse the displayed watched/required minute counters into seconds."""
    match = _PROGRESS.fullmatch(_text(value))
    if match is None:
        return None
    watched_seconds = int(match.group(2) or 0)
    required_seconds = int(match.group(4) or 0)
    if watched_seconds > 59 or required_seconds > 59:
        return None
    watched = int(match.group(1)) * 60 + watched_seconds
    required = int(match.group(3)) * 60 + required_seconds
    return (watched, required) if required > 0 else None


def progress_is_full(value: object) -> bool:
    durations = parse_progress_seconds(value)
    return durations is not None and durations[0] >= durations[1]


def _media(value: object) -> str:
    return {
        "동영상": "video",
        "유튜브": "youtube",
        "오프라인": "offline",
    }.get(_text(value), "other")


def parse_learning_rows(raw: list[dict], course: dict) -> list[dict]:
    """Normalize LV rows to catalog records, retaining all open/completion states.

    Empty titles use the CNU fallback title ``강의영상``, ensuring catalog
    titles stay strings even when the LMS omits title content.
    """
    course_id = _text(course.get("course_id"))
    if not course_id:
        return []
    course_label = _text(course.get("label")) or course_id
    lectures = []
    for row in raw:
        if row.get("moduletype") != "LV":
            continue
        row_id = _text(row.get("id"))
        if not row_id:
            continue
        state = _nullable_text(row.get("state"))
        openyn = _text(row.get("openyn"))
        available_from, due_date = _period_dates(row.get("period_text"))
        late_until = _local_datetime(_text(row.get("late_until_text")))
        progress_text = _nullable_text(row.get("progress_text"))
        attendance_counted = False if "출석 미반영" in _text(row.get("row_text")) else None
        full_watch = progress_is_full(progress_text)
        if state == "F":
            completion = "complete"
        elif attendance_counted is False and full_watch:
            completion = "recorded"
        else:
            completion = "incomplete"
        lectures.append(
            {
                "entity_id": f"cnu_lecture:{course_id}:{row_id}",
                "course": {"id": course_id, "label": course_label},
                "kind": "lecture",
                "title": _text(row.get("title")) or "강의영상",
                "week": _nullable_text(row.get("weekno")),
                "sequence": _nullable_text(row.get("seqno")),
                "progress_text": progress_text,
                "duration_minutes": _duration_minutes(progress_text),
                "available_from": available_from or _nullable_text(row.get("start_date")),
                "due_date": due_date,
                "late_until": late_until,
                "media": _media(row.get("badge_text")),
                "attendance_counted": attendance_counted,
                "open": True if openyn == "Y" else False if openyn == "N" else None,
                "completion": completion,
                "provider_state": state,
            }
        )
    return lectures
