"""Compare shell-free, matched sync invocations; live work requires separate authorization.

Each cold/warm run requires explicit per-arm setup commands. Only this file's
built-in synthetic fixture modes run without --allow-live. Raw child output and
catalog records never enter the private report.
"""

import argparse
import hashlib
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
    if argv and argv[0] == "--fixture-overhead":
        return _overhead_fixture(argv[1:])
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


def _accepted_profile(profile: dict | None) -> bool:
    """Command success accepts a profile even when a handled wait span failed."""
    return profile is None or (profile.get("outcome") == "ok" and profile.get("dropped_events") == 0)


def _exact_int(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _safe_profile(profile: object) -> dict | None:
    if not isinstance(profile, dict):
        return None
    from campusctl.profiling import COUNT_NAMES, DOMAINS, END_REASONS, PAGE_KINDS, PHASES, WAIT_KINDS

    top = {
        "schema_version",
        "run",
        "mode",
        "scope",
        "outcome",
        "wall_ns",
        "spans",
        "documents",
        "coverage",
        "counts",
        "dropped_events",
        "event_loop_lag_ns",
    }
    if set(profile) != top or profile.get("schema_version") != 2:
        return None
    if profile.get("mode") not in {"headed", "headless"} or profile.get("outcome") not in {"ok", "failed"}:
        return None
    if not _exact_int(profile.get("run"), minimum=1) or not _exact_int(profile.get("wall_ns")):
        return None
    if not _exact_int(profile.get("dropped_events")):
        return None
    scope = profile.get("scope")
    if not isinstance(scope, list) or any(item not in DOMAINS or type(item) is not str for item in scope):
        return None
    lag = profile.get("event_loop_lag_ns")
    if lag is not None and not _exact_int(lag):
        return None
    counts = profile.get("counts")
    spans = profile.get("spans")
    documents = profile.get("documents")
    coverage = profile.get("coverage")
    if (
        not isinstance(counts, dict)
        or not isinstance(spans, list)
        or not isinstance(documents, list)
        or not isinstance(coverage, dict)
        or set(counts) != COUNT_NAMES
        or any(not _exact_int(value) for value in counts.values())
        or len(spans) > 4096
        or len(documents) > 4096
        or set(coverage) != {"lock_ns", "covered_ns", "unattributed_ns"}
        or any(not _exact_int(coverage[key]) for key in ("lock_ns", "covered_ns", "unattributed_ns"))
        or coverage["covered_ns"] + coverage["unattributed_ns"] != coverage["lock_ns"]
    ):
        return None
    span_order = (
        "phase",
        "domain",
        "course",
        "window",
        "page_kind",
        "wait_kind",
        "document",
        "failed",
        "count",
        "inclusive_ns",
        "exclusive_ns",
    )
    safe_spans = []
    for span in spans:
        if not isinstance(span, dict) or set(span) != set(span_order) or span["phase"] not in PHASES:
            return None
        if span["domain"] is not None and span["domain"] not in DOMAINS:
            return None
        if span["page_kind"] is not None and span["page_kind"] not in PAGE_KINDS:
            return None
        if span["wait_kind"] is not None and span["wait_kind"] not in WAIT_KINDS:
            return None
        if any(
            span[key] is not None and not _exact_int(span[key], minimum=1) for key in ("course", "window", "document")
        ):
            return None
        if type(span["failed"]) is not bool or not _exact_int(span["count"], minimum=1):
            return None
        if any(not _exact_int(span[key]) for key in ("inclusive_ns", "exclusive_ns")):
            return None
        if span["exclusive_ns"] > span["inclusive_ns"]:
            return None
        safe_spans.append({key: span[key] for key in span_order})
    document_order = (
        "document",
        "page_kind",
        "domain",
        "course",
        "start_ns",
        "commit_ns",
        "end_ns",
        "end_reason",
    )
    safe_documents = []
    previous_ordinal = 0
    previous_start = -1
    ordinals: set[int] = set()
    for row in documents:
        if not isinstance(row, dict) or set(row) != set(document_order):
            return None
        ordinal = row["document"]
        if not _exact_int(ordinal, minimum=1) or ordinal <= previous_ordinal or ordinal in ordinals:
            return None
        if row["page_kind"] not in PAGE_KINDS or row["end_reason"] not in END_REASONS:
            return None
        if row["domain"] is not None and row["domain"] not in DOMAINS:
            return None
        if row["course"] is not None and not _exact_int(row["course"], minimum=1):
            return None
        if not _exact_int(row["start_ns"]) or not _exact_int(row["end_ns"]) or row["end_ns"] < row["start_ns"]:
            return None
        if row["start_ns"] < previous_start:
            return None
        commit = row["commit_ns"]
        if commit is not None and (not _exact_int(commit) or commit < row["start_ns"] or commit > row["end_ns"]):
            return None
        if row["end_reason"] == "failed" and commit is not None:
            return None
        previous_ordinal = ordinal
        previous_start = row["start_ns"]
        ordinals.add(ordinal)
        safe_documents.append({key: row[key] for key in document_order})
    if any(span["document"] is not None and span["document"] not in ordinals for span in safe_spans):
        return None
    return {
        "schema_version": 2,
        "run": profile["run"],
        "mode": profile["mode"],
        "scope": list(scope),
        "outcome": profile["outcome"],
        "wall_ns": profile["wall_ns"],
        "spans": safe_spans,
        "documents": safe_documents,
        "coverage": {key: coverage[key] for key in ("lock_ns", "covered_ns", "unattributed_ns")},
        "counts": {name: counts[name] for name in sorted(COUNT_NAMES)},
        "dropped_events": profile["dropped_events"],
        "event_loop_lag_ns": lag,
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
    complete = proc.returncode == 0 and result is not None and _accepted_profile(profile)
    catalogs = None
    if complete and data_root is not None and domains:
        catalogs = _catalogs(data_root, domains)
        complete = catalogs is not None
    attribution = None
    if profile is not None:
        from campusctl.profiling import attribute_profile

        attribution = attribute_profile(profile)
    return {
        "exit_code": proc.returncode,
        "complete": complete,
        "wall_ns": wall,
        "event_loop_lag_ns": profile["event_loop_lag_ns"] if profile else None,
        "profile": profile,
        "attribution": attribution,
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


def _overhead_shape(shape: str) -> tuple[int, int, int, int]:
    if shape == "observed":
        return 7, 68, 21, 800_000
    if shape == "cpu":
        return 2, 16, 4, 1_200_000
    raise ValueError("unknown overhead shape")


def _burn(rounds: int) -> int:
    token = b"fixture-overhead"
    for _round in range(rounds):
        token = hashlib.sha256(token).digest()
    return token[0]


def _overhead_fixture(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"off", "on"} or argv[1] not in {"observed", "cpu"}:
        return 2
    mode, shape = argv
    courses, documents, restores, burn_rounds = _overhead_shape(shape)
    encoded = json.dumps({"rows": [{"n": index, "text": "fixture-row-" + ("x" * 32)} for index in range(40)]})
    from campusctl.profiling import SpanRecorder

    recorder = (
        None
        if mode == "off"
        else SpanRecorder(enabled=True, mode="headed", scope=("lectures", "assignments", "notices", "materials"))
    )
    digest = _burn(burn_rounds)
    if recorder is None:
        digest = _overhead_pass(digest, encoded, courses, documents, restores, None, mode)
    else:
        with recorder.span("lock"):
            digest = _overhead_pass(digest, encoded, courses, documents, restores, recorder, mode)
    if recorder is not None:
        recorder.close_documents()
        finished = recorder.finish()
        committed = 0 if finished is None else sum(row["commit_ns"] is not None for row in finished["documents"])
        restore_count = (
            0
            if finished is None
            else sum(span["count"] for span in finished["spans"] if span["phase"] == "archive-restore")
        )
        if finished is None or finished["dropped_events"] or committed != documents or restore_count != restores:
            return 4
    print(
        json.dumps(
            {
                "status": "ok",
                "result": {"courses": courses, "documents": documents, "restores": restores, "digest": digest},
                "errors": [],
            }
        )
    )
    return 0


def _shares(total: int, parts: int) -> list[int]:
    base, extra = divmod(total, parts)
    return [base + (1 if index < extra else 0) for index in range(parts)]


def _overhead_pass(digest, encoded, courses, documents, restores, recorder, mode) -> int:
    doc_shares = _shares(documents, courses)
    restore_shares = _shares(restores, courses)
    for course, doc_count, restore_count in zip(range(1, courses + 1), doc_shares, restore_shares, strict=True):
        parsed = json.loads(encoded)
        digest += len(parsed["rows"]) + course
        for document in range(doc_count):
            parsed = json.loads(encoded)
            digest += parsed["rows"][document % len(parsed["rows"])]["n"]
            token = encoded.encode()
            for _round in range(6):
                token = hashlib.sha256(token).digest()
            digest += token[0]
            if mode == "on" and recorder is not None:
                ordinal = recorder.open_document(page_kind="archive", domain="materials", course=course)
                if ordinal is not None:
                    recorder.commit_document(ordinal)
        for _restore in range(restore_count):
            digest += 1 + parsed["rows"][0]["n"]
            if recorder is None or mode != "on":
                continue
            with recorder.span(
                "archive-restore",
                domain="materials",
                course=course,
                page_kind="archive",
                wait_kind="load",
            ):
                pass
    return digest


def _host_conditions() -> dict:
    import platform

    return {
        "platform": sys.platform,
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "browser_mode": "headed",
    }


def _spread(values: list[int]) -> float | None:
    if not values:
        return None
    middle = statistics.median(values)
    if middle <= 0:
        return None
    return (max(values) - min(values)) / middle


def _paired_ratio(left: list[dict], right: list[dict]) -> dict:
    if len(left) != len(right) or any(
        not row["complete"] or row["result"] != other["result"] for row, other in zip(left, right, strict=True)
    ):
        return {"verdict": "rejected", "ratio": None, "dispersion": None}
    if any(row["dropped_events"] not in {None, 0} for row in (*left, *right)):
        return {"verdict": "rejected", "ratio": None, "dispersion": None}
    left_walls = [row["wall_ns"] for row in left]
    right_walls = [row["wall_ns"] for row in right]
    left_median = statistics.median(left_walls)
    right_median = statistics.median(right_walls)
    if not left_median:
        return {"verdict": "rejected", "ratio": None, "dispersion": None}
    ratio = (right_median - left_median) / left_median
    dispersion = max(_spread(left_walls) or 0, _spread(right_walls) or 0)
    # Compare medians. Jitter that leaves a median above 5% is unresolved, not a pass.
    if ratio > 0.05 and dispersion > 0.05:
        verdict = "unresolved"
    elif ratio > 0.05:
        verdict = "fail"
    else:
        verdict = "pass"
    return {
        "verdict": verdict,
        "ratio": ratio,
        "dispersion": dispersion,
        "left_median_ns": left_median,
        "right_median_ns": right_median,
    }


def measure_overhead(*, trials: int = 5) -> dict:
    """Time whole fixture processes. A noisy ratio is unresolved, not a pass."""
    if trials < 5:
        raise ValueError("five trials are required")
    harness = [sys.executable, str(Path(__file__).resolve()), "--fixture-overhead"]
    shapes = {}
    for shape in ("observed", "cpu"):
        arms = {mode: [] for mode in ("off", "on")}
        for index in range(trials):
            order = ("off", "on") if index % 2 == 0 else ("on", "off")
            for mode in order:
                row = _run([*harness, mode, shape])
                result = None if row["_result"] is None else row["_result"][0]
                arms[mode].append(
                    {
                        "complete": row["complete"],
                        "wall_ns": row["wall_ns"],
                        "dropped_events": None if row["profile"] is None else row["profile"]["dropped_events"],
                        "result": result,
                        "attribution": row["attribution"],
                    }
                )
        shapes[shape] = {
            "off_vs_on": _paired_ratio(arms["off"], arms["on"]),
            "attribution": next((row["attribution"] for row in reversed(arms["on"]) if row["attribution"]), None),
        }
    return {"schema_version": 1, "host": _host_conditions(), "shapes": shapes}


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in {"--fixture-process", "--fixture-setup", "--fixture-overhead"}:
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
