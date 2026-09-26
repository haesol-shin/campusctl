"""Compare shell-free, matched sync invocations; live work requires separate authorization.

Each cold/warm run requires explicit per-arm setup commands. Only this file's
built-in synthetic fixture modes run without --allow-live. Raw child output and
catalog records never enter the private report.
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
from collections import Counter
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
    if len(argv) not in {5, 6} or Path(argv[0]).resolve() != Path(sys.executable).resolve():
        return False
    if Path(argv[1]).resolve() != Path(__file__).resolve():
        return False
    if len(argv) == 5:
        return argv[2] == "--fixture-setup" and argv[4] in {"cold", "warm"}
    return (
        argv[2] == "--fixture-process"
        and argv[4] in {"cold", "warm"}
        and argv[5] in {"slow", "fast", "failed", "mismatch"}
    )


def _fixture_main(argv: list[str]) -> int:
    if not _fixture([sys.executable, str(Path(__file__).resolve()), *argv]):
        return 2
    marker = Path(argv[1])
    root = Path(os.environ["CAMPUSCTL_DATA_DIR"])
    catalog = root / "catalog" / "notices.json"
    if argv[0] == "--fixture-setup":
        marker.write_text(argv[2], encoding="ascii")
        catalog.unlink(missing_ok=True)
        return 0
    if not marker.is_file() or marker.read_text(encoding="ascii") != argv[2]:
        return 3
    time.sleep(0.06 if argv[3] == "slow" else 0.015)
    if argv[3] == "failed":
        print(json.dumps({"status": "partial", "result": {}, "errors": [{"code": "fixture-failure"}]}))
        return 4
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": str(time.time_ns()),
                "enrollment_state": "known",
                "courses": [{"course_id": "course.invalid"}],
                "failed_courses": [],
                "notices": [
                    {
                        "entity_id": "item.invalid",
                        "course": {"id": "course.invalid"},
                        "value": 1 if argv[3] != "mismatch" else 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "result": {
                    "notices": 1,
                    "catalog": {"generated_at": str(time.time_ns()), "generation_id": str(time.time_ns())},
                },
                "errors": [],
            }
        )
    )
    return 0


def _proc_snapshot(group: int) -> dict[int, tuple[str, int, int, int | None]]:
    if not Path("/proc").exists():
        return {}
    records = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            fields = stat[stat.rfind(")") + 2 :].split()
            if int(fields[2]) != group:
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


def _safe_profile(profile: object) -> dict | None:
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        return None
    from campusctl.profiling import COUNT_NAMES, DOMAINS, PHASES

    counts = profile.get("counts")
    if "routes" in profile:
        return None
    spans = profile.get("spans")
    if not isinstance(counts, dict) or not isinstance(spans, list) or len(spans) > 4096:
        return None
    if set(counts) != COUNT_NAMES or any(type(value) is not int or value < 0 for value in counts.values()):
        return None
    keys = {"phase", "domain", "course", "window", "failed", "count", "inclusive_ns", "exclusive_ns"}
    for span in spans:
        if not isinstance(span, dict) or set(span) != keys or span["phase"] not in PHASES:
            return None
        if span["domain"] is not None and span["domain"] not in DOMAINS:
            return None
        if any(type(span[key]) is not int or span[key] < 1 for key in ("course", "window") if span[key] is not None):
            return None
        if type(span["failed"]) is not bool or any(
            type(span[key]) is not int or span[key] < 0 for key in ("count", "inclusive_ns", "exclusive_ns")
        ):
            return None
        if span["exclusive_ns"] > span["inclusive_ns"]:
            return None
    lag = profile.get("event_loop_lag_ns")
    if lag is not None and (type(lag) is not int or lag < 0):
        return None
    dropped = profile.get("dropped_events")
    if type(dropped) is not int or dropped < 0 or profile.get("outcome") not in {"ok", "failed"}:
        return None
    return {
        "spans": spans,
        "counts": counts,
        "event_loop_lag_ns": lag,
        "dropped_events": dropped,
        "outcome": profile["outcome"],
    }


def _run(argv: list[str], *, data_root: Path | None = None, domains: tuple[str, ...] = ()) -> dict:
    metrics = {
        key: {"cpu_seconds": None, "rss_peak_bytes": None, "pss_peak_bytes": None}
        for key in ("python", "driver", "chromium")
    }
    started = time.perf_counter_ns()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.Popen(
            argv,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=os.name != "nt",
            env={**os.environ, **({"CAMPUSCTL_DATA_DIR": str(data_root)} if data_root is not None else {})},
        )
        cpu_start: dict[int, int] = {}
        cpu_last: dict[int, tuple[str, int]] = {}
        while proc.poll() is None:
            snapshot = _proc_snapshot(proc.pid)
            totals = {key: {"rss": 0, "pss": 0, "pss_known": False} for key in metrics}
            for pid, (name, cpu, rss, pss) in snapshot.items():
                group = _group(name)
                cpu_start.setdefault(pid, cpu)
                cpu_last[pid] = (group, cpu)
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
            metrics[group]["cpu_seconds"] = (metrics[group]["cpu_seconds"] or 0) + max(0, last - cpu_start[pid]) / ticks
    result = None
    try:
        if len(stdout) <= 4_000_000:
            envelope = json.loads(stdout.decode().strip())
            if (
                isinstance(envelope, dict)
                and envelope.get("status") == "ok"
                and envelope.get("errors") == []
                and isinstance(envelope.get("result"), dict)
            ):
                result = envelope["result"]
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    profile = None
    if len(stderr) <= 4_000_000:
        for line in stderr.decode(errors="replace").splitlines():
            if line.startswith("campusctl-profile: "):
                try:
                    profile = _safe_profile(json.loads(line.removeprefix("campusctl-profile: ")))
                except json.JSONDecodeError:
                    profile = None
    complete = proc.returncode == 0 and result is not None
    if profile is not None:
        complete = complete and profile["outcome"] == "ok" and profile["dropped_events"] == 0
    catalogs = None
    if complete and data_root is not None and domains:
        catalogs = _catalogs(data_root, domains)
        complete = catalogs is not None
    return {
        "exit_code": proc.returncode,
        "complete": complete,
        "wall_ns": wall,
        "event_loop_lag_ns": profile["event_loop_lag_ns"] if profile else None,
        "profile": profile,
        "processes": metrics,
        "_result": (result, catalogs),
    }


def _catalogs(root: Path, domains: tuple[str, ...]) -> dict | None:
    """Load authoritative published catalogs in the selected arm's isolated data root."""
    from campusctl.catalog import read_catalog
    from campusctl.domain_catalog import read_domain_catalog

    catalogs = {}
    for domain in domains:
        path = root / "catalog" / f"{domain}.json"
        try:
            catalog = read_catalog(path) if domain == "lectures" else read_domain_catalog(domain, path)
        except Exception:
            return None
        catalogs[domain] = {
            key: value for key, value in catalog.items() if key not in {"generated_at", "generation_id"}
        }
    return catalogs


def _normalize(value):
    if isinstance(value, dict):
        return {
            key: _normalize(item)
            for key, item in sorted(value.items())
            if key not in {"generated_at", "generation_id", "as_of", "refreshed_at", "synced_at"}
        }
    if isinstance(value, tuple):
        return tuple(_normalize(item) for item in value)
    if isinstance(value, list):
        return sorted((_normalize(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value


def _record_counts(value) -> Counter:
    """Count full normalized records, never persist their content or identities."""
    if isinstance(value, dict):
        if "entity_id" in value:
            return Counter({json.dumps(value, sort_keys=True, ensure_ascii=False): 1})
        records = Counter()
        for child in value.values():
            records.update(_record_counts(child))
        return records
    if isinstance(value, list):
        records = Counter()
        for child in value:
            records.update(_record_counts(child))
        return records
    return Counter()


def _comparison(left, right) -> dict:
    left_result, left_catalogs = left
    right_result, right_catalogs = right
    normalized_left = _normalize((left_result, left_catalogs))
    normalized_right = _normalize((right_result, right_catalogs))
    a, b = _record_counts(_normalize(left_catalogs)), _record_counts(_normalize(right_catalogs))
    return {
        "equivalent": normalized_left == normalized_right,
        "baseline_only_records": sum((a - b).values()),
        "candidate_only_records": sum((b - a).values()),
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


def compare(
    baseline: list[str],
    candidate: list[str],
    *,
    trials: int,
    output: Path,
    population: str,
    baseline_setup: list[str],
    candidate_setup: list[str],
    baseline_data_dir: Path,
    candidate_data_dir: Path,
    domains: tuple[str, ...],
) -> dict:
    if trials < 5 or population not in {"cold", "warm"} or not baseline_setup or not candidate_setup:
        raise ValueError("five trials, a population, and per-arm setup commands are required")
    if not domains or any(domain not in {"lectures", "assignments", "notices", "materials"} for domain in domains):
        raise ValueError("at least one known catalog domain required")
    if baseline_data_dir.resolve() == candidate_data_dir.resolve():
        raise ValueError("baseline and candidate require isolated data directories")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        output.chmod(0o700)
    arms: dict[str, list[dict]] = {"baseline": [], "candidate": []}
    pairs = []
    for index in range(trials):
        pair: dict[str, dict] = {}
        order = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        for arm in order:
            setup = baseline_setup if arm == "baseline" else candidate_setup
            root = baseline_data_dir if arm == "baseline" else candidate_data_dir
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            setup_run = _run(setup, data_root=root)
            if setup_run["exit_code"] != 0:
                row = {
                    "exit_code": setup_run["exit_code"],
                    "complete": False,
                    "wall_ns": None,
                    "event_loop_lag_ns": None,
                    "profile": None,
                    "processes": None,
                    "_result": None,
                }
            else:
                row = _run(baseline if arm == "baseline" else candidate, data_root=root, domains=domains)
            row["trial"] = index + 1
            arms[arm].append(row)
            pair[arm] = row
        comparison = (
            _comparison(pair["baseline"]["_result"], pair["candidate"]["_result"])
            if all(row["complete"] for row in pair.values())
            else {"equivalent": False, "baseline_only_records": 0, "candidate_only_records": 0}
        )
        comparison["trial"] = index + 1
        pairs.append(comparison)
        for row in pair.values():
            row.pop("_result")
    summaries = {arm: _summary(rows) for arm, rows in arms.items()}
    gain = None
    if all(summary["completed"] == trials for summary in summaries.values()) and all(
        pair["equivalent"] for pair in pairs
    ):
        gain = 1 - summaries["candidate"]["median_wall_ns"] / summaries["baseline"]["median_wall_ns"]
    report = {
        "schema_version": 1,
        "populations": {
            population: {
                "summary": {
                    **summaries,
                    "improvement_fraction": gain,
                    "baseline_only_records": sum(pair["baseline_only_records"] for pair in pairs),
                    "candidate_only_records": sum(pair["candidate_only_records"] for pair in pairs),
                    "mismatched_pairs": sum(not pair["equivalent"] for pair in pairs),
                },
                "pairs": pairs,
                "trials": arms,
            }
        },
    }
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
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in {"--fixture-process", "--fixture-setup"}:
        return _fixture_main(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-command-json", required=True, type=_argv)
    parser.add_argument("--candidate-command-json", required=True, type=_argv)
    parser.add_argument("--baseline-setup-command-json", required=True, type=_argv)
    parser.add_argument("--candidate-setup-command-json", required=True, type=_argv)
    parser.add_argument("--baseline-data-dir", required=True, type=Path)
    parser.add_argument("--candidate-data-dir", required=True, type=Path)
    parser.add_argument("--domains", required=True, help="Comma-separated requested catalog domains")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--population", choices=("cold", "warm"), required=True)
    parser.add_argument(
        "--allow-live", action="store_true", help="Requires separate owner authorization; does not grant it"
    )
    args = parser.parse_args(argv)
    if args.trials < 5:
        parser.error("--trials must be at least 5")
    commands = (
        args.baseline_command_json,
        args.candidate_command_json,
        args.baseline_setup_command_json,
        args.candidate_setup_command_json,
    )
    if not args.allow_live and not all(_fixture(command) for command in commands):
        parser.error("arbitrary scripts require --allow-live and separate owner authorization")
    try:
        report = compare(
            args.baseline_command_json,
            args.candidate_command_json,
            trials=args.trials,
            output=args.output,
            population=args.population,
            baseline_setup=args.baseline_setup_command_json,
            candidate_setup=args.candidate_setup_command_json,
            baseline_data_dir=args.baseline_data_dir,
            candidate_data_dir=args.candidate_data_dir,
            domains=tuple(args.domains.split(",")),
        )
    except ValueError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "output": str(args.output / "profile-results.json"),
                "summary": report["populations"][args.population]["summary"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
