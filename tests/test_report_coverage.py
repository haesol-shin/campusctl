"""Exercise coverage reporting on isolated Git histories and coverage reports."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "report_coverage.py"
SPEC = importlib.util.spec_from_file_location("report_coverage", SCRIPT)
report_coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report_coverage)


def test_zero_statement_modules_have_no_ratio_and_do_not_hide_other_deltas(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    baseline = tmp_path / "baseline.json"
    candidate.write_text(
        json.dumps(
            {
                "files": {
                    "src/pkg/__init__.py": {"summary": {"covered_lines": 0, "num_statements": 0}},
                    "src/pkg/empty_after.py": {"summary": {"covered_lines": 0, "num_statements": 0}},
                    "src/pkg/logic.py": {"summary": {"covered_lines": 3, "num_statements": 4}},
                }
            }
        )
    )
    baseline.write_text(
        json.dumps(
            {
                "files": {
                    "src/pkg/__init__.py": {"summary": {"covered_lines": 0, "num_statements": 0}},
                    "src/pkg/empty_after.py": {"summary": {"covered_lines": 1, "num_statements": 2}},
                    "src/pkg/logic.py": {"summary": {"covered_lines": 2, "num_statements": 4}},
                }
            }
        )
    )

    report = report_coverage.summary(candidate, baseline)

    assert "| `src/pkg/__init__.py` | 0/0 | 0/0 | — (zero statements; no comparable ratio) |" in report
    assert "| `src/pkg/empty_after.py` | 0/0 | 1/2 | — (zero statements; no comparable ratio) |" in report
    assert "| `src/pkg/logic.py` | 3/4 | 2/4 | +25.000000% |" in report


def test_diff_coverage_reports_only_changed_executable_lines(tmp_path: Path, monkeypatch) -> None:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src" / "campusctl"
    source.mkdir(parents=True)
    (source / "logic.py").write_text("old = 1\nkeep = 2\nremove = 3\n")
    (source / "renamed.py").write_text("original = 1\n")
    (source / "empty.py").write_text("# unchanged\n")
    (tmp_path / "README").write_text("old\n")
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")

    (source / "logic.py").write_text("new = 1\nkeep = 2\nadded = 4\n")
    (source / "renamed.py").rename(source / "moved.py")
    (source / "moved.py").write_text("original = 1\nnew_line = 2\n")
    (source / "empty.py").write_text("# changed comment\n")
    (source / "fresh.py").write_text("fresh = 1\n")
    (tmp_path / "README").write_text("new\n")
    git("add", "-A")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "changes")

    changed = report_coverage.changed_lines(base)
    assert changed == {
        "src/campusctl/logic.py": {1, 3},
        "src/campusctl/moved.py": {2},
        "src/campusctl/empty.py": {1},
        "src/campusctl/fresh.py": {1},
    }
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps({"files": {
        "src/campusctl/logic.py": {
            "summary": {"covered_lines": 2, "num_statements": 3},
            "executed_lines": [1, 2], "missing_lines": [3],
        },
        "src/campusctl/moved.py": {
            "summary": {"covered_lines": 1, "num_statements": 2},
            "executed_lines": [1], "missing_lines": [2],
        },
        "src/campusctl/empty.py": {
            "summary": {"covered_lines": 0, "num_statements": 0},
            "executed_lines": [], "missing_lines": [],
        },
        "src/campusctl/fresh.py": {
            "summary": {"covered_lines": 1, "num_statements": 1},
            "executed_lines": [1], "missing_lines": [],
        },
    }}))
    report = report_coverage.diff_summary(candidate, changed)
    assert "| `src/campusctl/logic.py` | 2 | 1 | 3 |" in report
    assert "| `src/campusctl/moved.py` | 1 | 0 | 2 |" in report
    assert "| `src/campusctl/fresh.py` | 1 | 1 | — |" in report
    assert "| `src/campusctl/empty.py` | 0 | 0 | — |" in report
    assert "README" not in report
    job_summary = tmp_path / "job-summary.md"
    subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": "pull_request",
            "PR_BASE_SHA": base,
            "GITHUB_STEP_SUMMARY": str(job_summary),
        },
        check=True,
    )
    rendered = job_summary.read_text()
    assert "Comparable main baseline missing" in rendered
    assert "| `src/campusctl/logic.py` | 2 | 1 | 3 |" in rendered


def test_deleted_lines_have_no_new_side_statement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src" / "campusctl"
    source.mkdir(parents=True)
    module = source / "deleted.py"
    module.write_text("first = 1\nsecond = 2\n")

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    module.write_text("first = 1\n")
    git("add", "-A")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "delete")
    assert report_coverage.changed_lines(base) == {"src/campusctl/deleted.py": set()}
