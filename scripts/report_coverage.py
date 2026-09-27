"""Report Chromium-inclusive per-file coverage against the comparable main run."""

import json
import os
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
            delta = covered / statements - old_covered / old_statements
            lines.append(f"| `{name}` | {covered}/{statements} | {old_covered}/{old_statements} | {delta:+.6%} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report = summary(Path("coverage.json"), Path("baseline/coverage.json"))
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
        output.write(report)
