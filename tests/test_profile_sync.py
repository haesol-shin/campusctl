import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "tools" / "profile_sync.py"
SPEC = importlib.util.spec_from_file_location("profile_sync", HARNESS)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fixture(marker, condition, speed):
    return [sys.executable, str(HARNESS), "--fixture-process", str(marker), condition, speed]


def setup(marker, condition):
    return [sys.executable, str(HARNESS), "--fixture-setup", str(marker), condition]


def command(marker, output, *, population="cold", candidate="fast", baseline="slow"):
    return [
        sys.executable,
        str(HARNESS),
        "--baseline-command-json",
        json.dumps(fixture(marker, population, baseline)),
        "--candidate-command-json",
        json.dumps(fixture(marker, population, candidate)),
        "--baseline-setup-command-json",
        json.dumps(setup(marker, population)),
        "--candidate-setup-command-json",
        json.dumps(setup(marker, population)),
        "--baseline-data-dir",
        str(output.parent / (population + "-baseline")),
        "--candidate-data-dir",
        str(output.parent / (population + "-candidate")),
        "--domains",
        "notices",
        "--trials",
        "5",
        "--population",
        population,
        "--output",
        str(output),
    ]


def test_fixture_comparison_requires_explicit_condition_and_validates_records(tmp_path):
    for population in ("cold", "warm"):
        marker, output = tmp_path / "marker", tmp_path / population
        run = subprocess.run(command(marker, output, population=population), capture_output=True, text=True, check=True)
        summary = json.loads(run.stdout)["summary"]
        assert summary["baseline"]["completed"] == summary["candidate"]["completed"] == 5
        assert summary["improvement_fraction"] is not None
        for arm in ("baseline", "candidate"):
            assert summary[arm]["min_wall_ns"] <= summary[arm]["median_wall_ns"] <= summary[arm]["max_wall_ns"]
        report = json.loads((output / "profile-results.json").read_text())
        details = report["populations"][population]
        assert all(pair["equivalent"] for pair in details["pairs"])
        assert all(row["event_loop_lag_ns"] is None for row in details["trials"]["candidate"])
        assert all(row["processes"]["chromium"]["cpu_seconds"] is None for row in details["trials"]["baseline"])
        assert str(marker) not in (output / "profile-results.json").read_text()
        if os.name != "nt":
            assert output.stat().st_mode & 0o777 == 0o700
            assert (output / "profile-results.json").stat().st_mode & 0o777 == 0o600


def test_known_trial_durations_define_median_and_spread():
    rows = [{"complete": True, "wall_ns": value} for value in (9, 5, 7, 11, 3)] + [{"complete": False, "wall_ns": 1}]
    assert module._summary(rows) == {
        "attempted": 6,
        "completed": 5,
        "failed": 1,
        "median_wall_ns": 7,
        "min_wall_ns": 3,
        "max_wall_ns": 11,
    }


def test_mismatch_prevents_gain_and_only_reports_safe_counts(tmp_path):
    report = module.compare(
        fixture(tmp_path / "marker", "cold", "slow"),
        fixture(tmp_path / "marker", "cold", "mismatch"),
        trials=5,
        output=tmp_path / "private",
        population="cold",
        baseline_setup=setup(tmp_path / "marker", "cold"),
        candidate_setup=setup(tmp_path / "marker", "cold"),
        baseline_data_dir=tmp_path / "baseline-data",
        candidate_data_dir=tmp_path / "candidate-data",
        domains=("notices",),
    )
    summary = report["populations"]["cold"]["summary"]
    assert summary["baseline"]["completed"] == summary["candidate"]["completed"] == 5
    assert summary["improvement_fraction"] is None
    assert summary["mismatched_pairs"] == 5
    assert summary["baseline_only_records"] == summary["candidate_only_records"] == 5
    assert "item.invalid" not in (tmp_path / "private" / "profile-results.json").read_text()


def test_failed_candidate_never_scores_even_with_five_successful_pairs(tmp_path, monkeypatch):
    calls = 0

    def run(_argv, **_kwargs):
        nonlocal calls
        calls += 1
        if calls % 2:
            return {"exit_code": 0, "complete": False, "wall_ns": 0, "_result": None}  # setup exits normally
        trial = calls // 4
        candidate_failed = calls % 4 == 0 and trial == 5
        return {
            "exit_code": 4 if candidate_failed else 0,
            "complete": not candidate_failed,
            "wall_ns": 1 if calls % 4 == 0 else 10,
            "_result": None if candidate_failed else ({"notices": 1}, {"notices": {"notices": []}}),
        }

    monkeypatch.setattr(module, "_run", run)
    report = module.compare(
        ["baseline"],
        ["candidate"],
        trials=6,
        output=tmp_path / "private",
        population="warm",
        baseline_setup=["setup"],
        candidate_setup=["setup"],
        baseline_data_dir=tmp_path / "baseline-data",
        candidate_data_dir=tmp_path / "candidate-data",
        domains=("notices",),
    )
    summary = report["populations"]["warm"]["summary"]
    assert summary["candidate"]["completed"] == 5
    assert summary["candidate"]["failed"] == 1
    assert summary["improvement_fraction"] is None


def test_profile_metrics_are_validated_before_persistence(tmp_path, monkeypatch):
    valid = {
        "schema_version": 1,
        "outcome": "ok",
        "dropped_events": 0,
        "counts": {"course_selections": 7, "documents": 2, "sso_settles": 0},
        "spans": [
            {
                "phase": "extract",
                "domain": "notices",
                "course": 1,
                "window": None,
                "failed": False,
                "count": 1,
                "inclusive_ns": 10,
                "exclusive_ns": 8,
            }
        ],
        "event_loop_lag_ns": 3,
    }
    assert module._safe_profile(valid)["counts"]["course_selections"] == 7
    assert module._safe_profile({**valid, "routes": {"https://secret.invalid": 1}}) is None
    assert module._safe_profile({**valid, "spans": [{**valid["spans"][0], "domain": "secret.invalid"}]}) is None
    original = module._run

    def run(argv, **kwargs):
        row = original(argv, **kwargs)
        if "--fixture-process" in argv:
            row["profile"] = module._safe_profile(valid)
        return row

    monkeypatch.setattr(module, "_run", run)
    report = module.compare(
        fixture(tmp_path / "marker", "cold", "slow"),
        fixture(tmp_path / "marker", "cold", "fast"),
        trials=5,
        output=tmp_path / "private",
        population="cold",
        baseline_setup=setup(tmp_path / "marker", "cold"),
        candidate_setup=setup(tmp_path / "marker", "cold"),
        baseline_data_dir=tmp_path / "baseline-data",
        candidate_data_dir=tmp_path / "candidate-data",
        domains=("notices",),
    )
    assert report["populations"]["cold"]["trials"]["baseline"][0]["profile"]["counts"]["course_selections"] == 7


def test_catalog_records_not_counts_and_generation_timestamps_not_identity(tmp_path):
    baseline = {"notices": 1, "catalog": {"generated_at": "2026-01-01", "generation_id": "one"}}
    candidate = {"notices": 1, "catalog": {"generated_at": "2026-02-01", "generation_id": "two"}}
    row = {"entity_id": "item.invalid", "course": {"id": "course.invalid"}, "read": False}
    first = {"notices": {"notices": [row], "courses": [], "failed_courses": [], "generated_at": "first"}}
    second = {"notices": {"notices": [row], "courses": [], "failed_courses": [], "generated_at": "second"}}
    assert module._comparison((baseline, first), (candidate, second))["equivalent"] is True
    different = {"notices": {"notices": [{**row, "read": True}], "courses": [], "failed_courses": []}}
    mismatch = module._comparison((baseline, first), (candidate, different))
    assert mismatch == {"equivalent": False, "baseline_only_records": 1, "candidate_only_records": 1}


@pytest.mark.parametrize("change", ["nonfixture", "missing_setup", "few_trials"])
def test_rejects_unsafe_or_uncontrolled_runs(tmp_path, change):
    argv = command(tmp_path / "marker", tmp_path / "private")
    if change == "nonfixture":
        path = tmp_path / "live-fixture.py"
        path.write_text("print('not an offline fixture')")
        argv[argv.index("--baseline-command-json") + 1] = json.dumps([sys.executable, str(path)])
    elif change == "missing_setup":
        pos = argv.index("--baseline-setup-command-json")
        del argv[pos : pos + 2]
    else:
        argv[argv.index("--trials") + 1] = "4"
    result = subprocess.run(argv, capture_output=True, text=True)
    assert result.returncode == 2
    assert not (tmp_path / "private").exists()
