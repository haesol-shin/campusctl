from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from campusctl import cli
from campusctl.commands import notices
from campusctl.domain_catalog import write_domain_catalog

STAMP = "2026-09-25T03:04:05Z"
FULL_ID = "cnu_notice:course-a:2026-09-01 12%3A00:12345678901234567890"
PUBLIC_ROW_KEYS = {
    "entity_id",
    "legacy_key",
    "course",
    "kind",
    "title",
    "date",
    "status",
    "is_unread",
    "posted_date",
    "author_role",
    "author",
    "view_count",
    "has_attachments",
}
ROWS = [
    {
        "entity_id": FULL_ID,
        "native_id": "TB_L_BOARDITEM7001",
        "legacy_key": "Example Course_2026-09-01 12:00_12345678901234567890",
        "course": {"id": "course-a", "label": "Example Course"},
        "kind": "notice",
        "title": "Assignment information",
        "date": "2026-09-01 12:00",
        "status": "읽지않음",
        "is_unread": True,
        "posted_date": None,
        "author_role": None,
        "author": None,
        "view_count": None,
        "has_attachments": None,
    },
    {
        "entity_id": "cnu_notice:course-b:2026-09-02 12%3A00:2",
        "native_id": "TB_L_BOARDITEM7002",
        "legacy_key": "Other Course_2026-09-02 12:00_2",
        "course": {"id": "course-b", "label": "Other Course"},
        "kind": "notice",
        "title": "Course details",
        "date": "2026-09-02 12:00",
        "status": "읽음",
        "is_unread": False,
        "posted_date": "2026-09-02",
        "author_role": "instructor",
        "has_attachments": False,
        "author": "Example Author",
        "view_count": 14,
    },
]


def _catalog(root: Path, *, state: str = "known", failed: list[dict[str, str]] | None = None) -> None:
    write_domain_catalog(
        "notices",
        {
            "generated_at": STAMP,
            "enrollment_state": state,
            "courses": [
                {"course_id": "course-a", "label": "Example Course", "class_no": None},
                {"course_id": "course-b", "label": "Other Course", "class_no": None},
            ],
            "failed_courses": failed or [],
            "notices": ROWS,
        },
        root / "catalog" / "notices.json",
    )


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = cli.main(argv)
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def test_discovered_notice_capability_and_human_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(tmp_path)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "human")
    assert cli.CAPABILITIES["notices"] == ["list"]
    assert cli.CAPABILITIES["sync"].count("notices") == 1

    code, doctor = _invoke(["doctor", "--json"], capsys)
    assert code in (0, 2)  # doctor also checks local configuration and browser readiness
    assert doctor["result"]["capabilities"]["notices"] == ["list"]
    assert doctor["result"]["capabilities"]["sync"].count("notices") == 1
    assert "policy" not in doctor["result"]["capabilities"]

    assert cli.main(["notices", "list", "--course", "course-a"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "Assignment information" in output.out
    assert FULL_ID in output.out
    assert "Other Course" not in output.out
    assert "TB_L_BOARDITEM7001" not in output.out


def test_cached_notice_list_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _catalog(
        tmp_path,
        state="unknown",
        failed=[{"course_id": "course-b", "label": "Other Course", "reason": "course-sync-failed"}],
    )
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("cached list must not load config"))

    code, payload = _invoke(["notices", "list", "--course", "course-a", "--json"], capsys)
    assert code == 0
    assert payload["schema_version"] == 1
    assert payload["status"] == "ok" and payload["errors"] == []
    result = payload["result"]
    assert set(result["notices"][0]) == PUBLIC_ROW_KEYS
    assert result["notices"][0]["entity_id"] == FULL_ID
    code, payload = _invoke(["notices", "list", "--json"], capsys)
    assert code == 0
    assert all(set(row) == PUBLIC_ROW_KEYS for row in payload["result"]["notices"])
    assert result["cache"]["generated_at"] == STAMP
    assert result["cache"]["stale"] is True
    assert result["cache"]["failed_courses"][0]["course_id"] == "course-b"
    code, payload = _invoke(["--json", "notices", "list", "--course", "missing"], capsys)
    assert code == 2 and payload["errors"][0]["code"] == "course-id-required"


def test_missing_notice_catalog_reports_sync_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    code, payload = _invoke(["notices", "list", "--json"], capsys)
    assert code == 2
    assert payload["status"] == "user-action"
    assert payload["errors"] == [
        {
            "code": "catalog-missing",
            "message": f"Notices catalog not found: {tmp_path / 'catalog' / 'notices.json'}.",
            "remediation": "Run 'campusctl sync --only notices' to create it.",
        }
    ]


def test_notice_human_full_ids_and_staleness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = [{"course_id": "course-b", "label": "Other Course", "reason": "course-sync-failed"}]
    _catalog(tmp_path, state="unknown", failed=failed)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    lines = notices.render("notices.list", notices.dispatch(cli.build_parser().parse_args(["notices", "list"]))[0], 40)
    text = "\n".join(lines)
    readable = " ".join(text.split())
    assert "Enrollment unknown" in text
    assert "Other Course" in text and "course-sync-failed" in text
    assert f"    {FULL_ID}" in lines
    assert "Assignment information" in readable and "2026-09-01 12:00" in readable
    assert "읽지않음" in readable and "unread" in readable
    assert "Author: unknown" in readable and "Views: unknown" in readable and "Attachments: unknown" in readable
    assert "Author: Example Author" in readable and "Views: 14" in readable and "Attachments: no" in readable
    filtered = notices.dispatch(cli.build_parser().parse_args(["notices", "list", "--course", "missing"]))[0]
    empty_text = "\n".join(notices.render("notices.list", filtered, 40))
    assert "0 notices" in empty_text and "Enrollment unknown" in empty_text
    assert "Other Course" in " ".join(empty_text.split())
    sync = {
        "courses": 1,
        "notices": 2,
        "failed_courses": failed,
        "catalog": {"generated_at": STAMP, "enrollment_state": "unknown"},
    }
    sync_text = "\n".join(notices.render("sync.notices", sync, 40))
    assert "1 course" in sync_text and "2 notices" in sync_text
    assert "lectures" not in sync_text and "Enrollment unknown" in sync_text
    assert "Other Course" in " ".join(sync_text.split()) and "course-sync-failed" in sync_text
    assert capsys.readouterr().out == ""


def test_notice_human_wraps_wide_and_unbroken_titles_by_terminal_cells() -> None:
    from campusctl.presentation import _cells

    rows = [
        {**ROWS[0], "title": "가" * 25, "entity_id": FULL_ID},
        {**ROWS[1], "title": "A" * 50},
    ]
    result = {
        "cache": {"generated_at": STAMP, "enrollment_state": "known", "failed_courses": []},
        "notices": rows,
    }
    lines = notices.render("notices.list", result, 40)
    assert f"    {FULL_ID}" in lines
    assert all(_cells(line) <= 40 for line in lines if not line.lstrip().startswith("cnu_notice:"))
    compact = "".join(lines).replace(" ", "")
    assert "가" * 25 in compact
    assert "A" * 50 in compact
