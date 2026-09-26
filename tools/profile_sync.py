"""Run paired, shell-free synthetic or separately authorized sync comparisons.

Results are private; this tool does not authenticate, alter browser gates, or
constitute authorization to run against a live LMS.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _argv(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("command must be a JSON argv array") from exc
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
        raise argparse.ArgumentTypeError("command must be a nonempty JSON array of nonempty strings")
    return value


def _fixture(argv: list[str]) -> bool:
    """Only local Python scripts explicitly named fixture are eligible for offline runs."""
    if len(argv) != 2 or Path(argv[0]).resolve() != Path(sys.executable).resolve():
        return False
    target = Path(argv[1])
    return target.is_file() and "fixture" in target.stem.lower() and target.suffix in {"", ".py"}


def _proc_snapshot(group: int) -> dict[int, tuple[str, int, int | None, int | None]]:
    if not Path("/proc").exists():
        return {}
    records = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            fields = stat[stat.rfind(")") + 2 :].split()
            if int(fields[2]) != group:  # pgrp
                continue
            pid = int(entry.name)
            name = stat[stat.find("(") + 1 : stat.rfind(")")].lower()
            cpu = int(fields[11]) + int(fields[12])
            rss = int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
            try:
                rollup = (entry / "smaps_rollup").read_text()
                pss = next(int(line.split()[1]) * 1024 for line in rollup.splitlines() if line.startswith("Pss:"))
            except (OSError, StopIteration, ValueError):
                pss = None
            records[pid] = (name, cpu, rss, pss)
        except (OSError, ValueError, IndexError):
            continue
    return records


def _group(name: str) -> str:
    if "chrom" in name or name in {"chrome", "headless_shell"}:
        return "chromium"
    if name in {"node", "playwright"}:
        return "driver"
    return "python"


def _run(argv: list[str]) -> dict:
    metrics = {
        key: {"cpu_seconds": None, "rss_peak_bytes": None, "pss_peak_bytes": None}
        for key in ("python", "driver", "chromium")
    }
    started = time.perf_counter_ns()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.Popen(argv, stdout=stdout_file, stderr=stderr_file, start_new_session=os.name != "nt")
        cpu_start: dict[int, int] = {}
        cpu_last: dict[int, tuple[str, int]] = {}
        while proc.poll() is None:
            snapshot = _proc_snapshot(proc.pid)
            totals = {key: {"rss": 0, "pss": 0, "pss_known": False} for key in metrics}
            for pid, (name, cpu, rss, pss) in snapshot.items():
                group = _group(name)
                cpu_start.setdefault(pid, cpu)
                cpu_last[pid] = (group, cpu)
                if rss is not None:
                    totals[group]["rss"] += rss
                if pss is not None:
                    totals[group]["pss"] += pss
                    totals[group]["pss_known"] = True
            for group, total in totals.items():
                if total["rss"]:
                    metrics[group]["rss_peak_bytes"] = max(metrics[group]["rss_peak_bytes"] or 0, total["rss"])
                if total["pss_known"]:
                    metrics[group]["pss_peak_bytes"] = max(metrics[group]["pss_peak_bytes"] or 0, total["pss"])
            time.sleep(0.005)
        proc.wait()
        wall = time.perf_counter_ns() - started
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(4_000_001)
        stderr = stderr_file.read(4_000_001)
    if cpu_last:
        ticks = os.sysconf("SC_CLK_TCK")
        for pid, (group, last) in cpu_last.items():
            previous = metrics[group]["cpu_seconds"] or 0
            metrics[group]["cpu_seconds"] = previous + max(0, last - cpu_start[pid]) / ticks
    # Do not serialize child stdout/stderr: both can contain private LMS data.
    try:
        envelope = json.loads(stdout.decode().strip())
        complete = isinstance(envelope, dict) and envelope.get("status") == "ok" and not envelope.get("errors")
    except (UnicodeDecodeError, json.JSONDecodeError):
        complete = False
    profile = None
    for line in stderr.decode(errors="replace").splitlines():
        if line.startswith("campusctl-profile: "):
            try:
                candidate = json.loads(line.removeprefix("campusctl-profile: "))
                if isinstance(candidate, dict) and candidate.get("schema_version") == 1:
                    profile = candidate
            except json.JSONDecodeError:
                pass
    if profile is not None:
        complete = complete and profile.get("outcome") == "ok" and profile.get("dropped_events") == 0
    lag = profile.get("event_loop_lag_ns") if profile else None
    if type(lag) is not int or lag < 0:
        lag = None
    return {
        "exit_code": proc.returncode,
        "complete": bool(proc.returncode == 0 and complete),
        "wall_ns": wall,
        "event_loop_lag_ns": lag,
        "processes": metrics,
    }


def _summary(rows: list[dict]) -> dict:
    complete = [row["wall_ns"] for row in rows if row["complete"]]
    return {
        "attempted": len(rows),
        "completed": len(complete),
        "failed": len(rows) - len(complete),
        "median_wall_ns": statistics.median(complete) if complete else None,
        "min_wall_ns": min(complete) if complete else None,
        "max_wall_ns": max(complete) if complete else None,
    }


def compare(baseline: list[str], candidate: list[str], *, trials: int, output: Path, population: str = "both") -> dict:
    if trials < 5:
        raise ValueError("at least five trials per arm required")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        output.chmod(0o700)
    results = {}
    for condition in ("cold", "warm") if population == "both" else (population,):
        arms: dict[str, list[dict]] = {"baseline": [], "candidate": []}
        for index in range(trials):
            order = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
            for arm in order:
                row = _run(baseline if arm == "baseline" else candidate)
                row["trial"] = index + 1
                arms[arm].append(row)
        baseline_summary = _summary(arms["baseline"])
        candidate_summary = _summary(arms["candidate"])
        gain = None
        if baseline_summary["completed"] >= 5 and candidate_summary["completed"] >= 5:
            gain = 1 - candidate_summary["median_wall_ns"] / baseline_summary["median_wall_ns"]
        results[condition] = {
            "summary": {"baseline": baseline_summary, "candidate": candidate_summary, "improvement_fraction": gain},
            "trials": arms,
        }
    report = {"schema_version": 1, "populations": results}
    destination = output / "profile-results.json"
    with os.fdopen(
        os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-command-json", required=True, type=_argv)
    parser.add_argument("--candidate-command-json", required=True, type=_argv)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--population", choices=("cold", "warm", "both"), default="both")
    parser.add_argument(
        "--allow-live", action="store_true", help="Requires separate owner authorization; does not grant it"
    )
    args = parser.parse_args(argv)
    if args.trials < 5:
        parser.error("--trials must be at least 5")
    if not args.allow_live and not all(
        _fixture(command) for command in (args.baseline_command_json, args.candidate_command_json)
    ):
        parser.error("non-fixture targets require --allow-live and separate owner authorization")
    report = compare(
        args.baseline_command_json,
        args.candidate_command_json,
        trials=args.trials,
        output=args.output,
        population=args.population,
    )
    print(
        json.dumps(
            {
                "output": str(args.output / "profile-results.json"),
                "summary": {name: value["summary"] for name, value in report["populations"].items()},
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
