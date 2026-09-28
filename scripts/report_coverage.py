"""Report Chromium-inclusive per-file coverage against the comparable main run."""

import ast
import json
import os
import re
import subprocess
from pathlib import Path


def fractions(path: Path) -> dict[str, tuple[int, int]]:
    data = json.loads(path.read_text())
    return {
        name: (entry["summary"]["covered_lines"], entry["summary"]["num_statements"])
        for name, entry in data["files"].items()
    }


def summary(candidate: Path, baseline: Path) -> str:
    current = fractions(candidate)
    lines = ["### Chromium-inclusive per-file coverage", "", "Report-only; no coverage threshold.", ""]
    if not baseline.is_file():
        lines.extend(["Comparable main baseline missing (Ubuntu x86_64, Python 3.12, Chromium).", ""])
        return "\n".join(lines)
    main = fractions(baseline)
    lines.extend(
        ["| Module | PR covered/statements | Main covered/statements | Delta |", "| --- | ---: | ---: | ---: |"]
    )
    for name in sorted(current.keys() | main.keys()):
        if name not in main:
            lines.append(f"| `{name}` | {current[name][0]}/{current[name][1]} | added | — |")
        elif name not in current:
            lines.append(f"| `{name}` | removed | {main[name][0]}/{main[name][1]} | — |")
        else:
            covered, statements = current[name]
            old_covered, old_statements = main[name]
            if statements and old_statements:
                delta = covered / statements - old_covered / old_statements
                change = f"{delta:+.6%}"
            else:
                change = "— (zero statements; no comparable ratio)"
            lines.append(f"| `{name}` | {covered}/{statements} | {old_covered}/{old_statements} | {change} |")
    return "\n".join(lines) + "\n"


def changed_lines(base: str) -> dict[str, set[int]]:
    """Return new-side line numbers for added and modified source hunks."""
    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", base], capture_output=True, text=True, check=True
    ).stdout.strip()
    patch = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "--no-ext-diff",
            "--unified=0",
            "--find-renames",
            merge_base,
            "HEAD",
            "--",
            "src/campusctl/",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    result: dict[str, set[int]] = {}
    filename: str | None = None
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            filename = None
            in_hunk = False
        elif not in_hunk and line.startswith("+++ "):
            name = line[4:]
            if name.startswith('"'):
                # Git quotes filenames containing whitespace, with C-style escapes.
                name = ast.literal_eval(name)
            filename = name[2:] if name.startswith("b/src/campusctl/") else None
        elif filename is not None and (match := re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)):
            in_hunk = True
            start = int(match[1])
            count = int(match[2]) if match[2] is not None else 1
            result.setdefault(filename, set()).update(range(start, start + count))
    return result


def diff_report(candidate: Path, changed: dict[str, set[int]]) -> dict[str, dict[str, list[int]]]:
    """Keep the complete statement intersections for the coverage artifact."""
    files = json.loads(candidate.read_text())["files"]
    result: dict[str, dict[str, list[int]]] = {}
    for name in sorted(changed):
        entry = files.get(name)
        if entry is None:
            continue
        missing = set(entry["missing_lines"]) & changed[name]
        executed = set(entry["executed_lines"]) & changed[name]
        result[name] = {
            "changed_statements": sorted(missing | executed),
            "covered_lines": sorted(executed),
            "uncovered_lines": sorted(missing),
        }
    return result


def diff_summary(files: dict[str, dict[str, list[int]]]) -> str:
    """Show bounded statement intersections, linking truncation to the full artifact."""
    lines = [
        "### PR diff coverage",
        "",
        "Report-only; reviewers should request tests for risky uncovered lines.",
        "",
        "| Module | Changed statements | Covered | Uncovered lines |",
        "| --- | ---: | ---: | --- |",
    ]
    omitted = 0
    for name, entry in files.items():
        uncovered = entry["uncovered_lines"]
        if len(lines) >= 46:
            omitted += 1
            continue
        shown = ", ".join(map(str, uncovered[:30]))
        if len(uncovered) > 30:
            shown += f", … (+{len(uncovered) - 30} more)"
        lines.append(
            f"| `{name.replace('|', '&#124;')}` | {len(entry['changed_statements'])} | "
            f"{len(entry['covered_lines'])} | {shown or '—'} |"
        )
    if omitted:
        lines.append(
            f"\n{omitted} more modules omitted; download `diff-coverage.json` from the `browser-coverage` artifact for all lines."
        )
    elif any(len(entry["uncovered_lines"]) > 30 for entry in files.values()):
        lines.append("\nDownload `diff-coverage.json` from the `browser-coverage` artifact for all uncovered lines.")
    elif len(lines) == 6:
        lines.append("| No changed executable statements | 0 | 0 | — |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report = summary(Path("coverage.json"), Path("baseline/coverage.json"))
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        try:
            diff = diff_report(Path("coverage.json"), changed_lines(os.environ["PR_BASE_SHA"]))
            Path("diff-coverage.json").write_text(json.dumps({"files": diff}, indent=2) + "\n")
            report += "\n" + diff_summary(diff)
        except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
            report += f"\n### PR diff coverage\n\nUnavailable: {type(exc).__name__}; see coverage artifact.\n"
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
        output.write(report)
