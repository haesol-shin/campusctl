"""Golden assignment identities and fields for a generic legacy parser.

The legacy mapping is represented by synthetic positional arguments; these tests
do not import a separate production package or open a private ledger.
"""

import pytest
from test_assignments_provider import fixture_rows

from campusctl.identity import assignment_entity_id
from campusctl.providers.cnu.assignments import parse_assignment_rows

# The expected legacy parser arguments remain independent of parser output.
LEGACY_CASES = (
    (
        0,
        {"course_id": "course-alpha", "label": "Synthetic Course Alpha"},
        ("course-alpha", "Synthetic Course Alpha", "TB_L_REPORT101", "Draft report", "2026-10-02 23:59", True),
    ),
    (
        1,
        {"course_id": "course-beta", "label": "Synthetic Course Beta"},
        ("course-beta", "Synthetic Course Beta", "TB_L_REPORT102", "Hidden exercise", None, True),
    ),
)


def test_assignment_native_fields_match_legacy_mapping():
    """The stable ID uses native course/task IDs, not the course label or title."""
    rows = fixture_rows()
    first = parse_assignment_rows([rows[0]], LEGACY_CASES[0][1])[0]
    second = parse_assignment_rows([rows[1]], LEGACY_CASES[1][1])[0]
    assert first["entity_id"] == "cnu_assignment:course-alpha:TB_L_REPORT101"
    assert assignment_entity_id("course-beta", first["task_id"]) == "cnu_assignment:course-beta:TB_L_REPORT101"
    assert assignment_entity_id("course-alpha", second["task_id"]) == "cnu_assignment:course-alpha:TB_L_REPORT102"
    assert second["entity_id"] == assignment_entity_id(second["course"]["id"], second["task_id"])
    # Delimiter-bearing native components must not create ambiguous identities.
    assert assignment_entity_id("course:alpha", first["task_id"]) == "cnu_assignment:course%3Aalpha:TB_L_REPORT101"


@pytest.mark.parametrize(
    "fixture_index,course,expected_upsert_args", LEGACY_CASES, ids=("submitted-with-due", "submitted-without-due")
)
def test_submitted_and_null_due_legacy_mapping(fixture_index, course, expected_upsert_args):
    """Freeze parsed fixture rows against the legacy parser's positional API."""
    row = parse_assignment_rows([fixture_rows()[fixture_index]], course)[0]
    assert set(row) == {"entity_id", "task_id", "course", "kind", "title", "due_date", "is_submitted"}
    assert set(row["course"]) == {"id", "label"}
    assert row["kind"] == "assignment"
    assert row["entity_id"] == assignment_entity_id(row["course"]["id"], row["task_id"])
    assert (
        row["course"]["id"],
        row["course"]["label"],
        row["task_id"],
        row["title"],
        row["due_date"],
        row["is_submitted"],
    ) == expected_upsert_args
    assert type(row["is_submitted"]) is bool
    assert row["due_date"] is None or isinstance(row["due_date"], str)
