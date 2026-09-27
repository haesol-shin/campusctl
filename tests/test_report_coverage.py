"""Keep report-only coverage comparisons usable for statement-free modules."""

import importlib.util
import json
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
