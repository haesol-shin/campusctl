import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "tools" / "profile_sync.py"


def fixture(tmp_path, name, *, delay, status="ok", exit_code=0):
    script = tmp_path / f"{name}-fixture.py"
    script.write_text(
        "import json,sys,time\n"
        f"time.sleep({delay})\n"
        f"print(json.dumps({{'status': '{status}'}}))\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, str(script)]


def command(baseline, candidate, output, *flags):
    return [
        sys.executable,
        str(HARNESS),
        "--baseline-command-json",
        json.dumps(baseline),
        "--candidate-command-json",
        json.dumps(candidate),
        "--trials",
        "5",
        "--output",
        str(output),
        *flags,
    ]


def test_fixture_comparison_is_interleaved_private_and_separates_populations(tmp_path):
    baseline = fixture(tmp_path, "baseline", delay=0.06)
    candidate = fixture(tmp_path, "candidate", delay=0.015)
    output = tmp_path / "private"
    run = subprocess.run(command(baseline, candidate, output), capture_output=True, text=True, check=True)
    result = json.loads((output / "profile-results.json").read_text())
    assert run.returncode == 0
    for population in ("cold", "warm"):
        details = result["populations"][population]
        summaries = details["summary"]
        assert summaries["baseline"]["completed"] == 5
        assert summaries["candidate"]["completed"] == 5
        assert 0.15 < summaries["improvement_fraction"] < 0.9
        for arm in ("baseline", "candidate"):
            assert summaries[arm]["min_wall_ns"] <= summaries[arm]["median_wall_ns"] <= summaries[arm]["max_wall_ns"]
            assert [row["trial"] for row in details["trials"][arm]] == [1, 2, 3, 4, 5]
            assert all(row["event_loop_lag_ns"] is None for row in details["trials"][arm])
            assert all(row["processes"]["chromium"]["cpu_seconds"] is None for row in details["trials"][arm])
    assert str(baseline[1]) not in (output / "profile-results.json").read_text()
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o700
        assert (output / "profile-results.json").stat().st_mode & 0o777 == 0o600


def test_failed_trials_cannot_contribute_to_improvement(tmp_path):
    baseline = fixture(tmp_path, "baseline", delay=0.005)
    candidate = fixture(tmp_path, "candidate", delay=0.001, status="partial", exit_code=4)
    output = tmp_path / "private"
    subprocess.run(command(baseline, candidate, output, "--population", "cold"), check=True, capture_output=True)
    record = json.loads((output / "profile-results.json").read_text())["populations"]["cold"]
    assert record["summary"]["candidate"]["completed"] == 0
    assert record["summary"]["candidate"]["failed"] == 5
    assert record["summary"]["improvement_fraction"] is None
    assert {trial["exit_code"] for trial in record["trials"]["candidate"]} == {4}


@pytest.mark.parametrize("flags", [("--trials", "4"), ()])
def test_rejects_incomplete_or_nonfixture_commands(tmp_path, flags):
    baseline = fixture(tmp_path, "baseline", delay=0)
    candidate = fixture(tmp_path, "candidate", delay=0)
    if flags:
        argv = command(baseline, candidate, tmp_path / "private")
        argv[argv.index("--trials") + 1] = "4"
    else:
        argv = command([sys.executable, "-c", "print('not a fixture')"], candidate, tmp_path / "private")
    run = subprocess.run(argv, capture_output=True, text=True)
    assert run.returncode == 2
    assert not (tmp_path / "private").exists()
