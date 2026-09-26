"""Reject tracked binary artifacts, NUL bytes, and non-UTF-8 text.

Text leak rules and path exceptions are configured in .gitleaks-public.toml.
Run: uv run python scripts/check_public_hygiene.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".pdf",
    ".zip", ".gz", ".tgz", ".7z", ".rar", ".tar", ".ipynb", ".ppt", ".pptx", ".doc",
    ".docx", ".xls", ".xlsx", ".hwp", ".hwpx", ".mp4", ".mov", ".mp3", ".wav", ".har",
}  # fmt: skip


def allowed_paths() -> list[re.Pattern[str]]:
    with Path(".gitleaks-public.toml").open("rb") as config:
        paths = tomllib.load(config).get("allowlist", {}).get("paths", [])
    return [re.compile(path) for path in paths]


def main() -> int:
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
    exceptions = allowed_paths()
    problems: list[str] = []
    for name in filter(None, files):
        if any(rule.search(name) for rule in exceptions):
            continue
        if Path(name).suffix.lower() in BINARY_EXTENSIONS:
            problems.append(f"{name}: binary or document artifact")
            continue
        try:
            data = Path(name).read_bytes()
        except FileNotFoundError:
            continue
        if b"\0" in data:
            problems.append(f"{name}: binary content")
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{name}: not UTF-8 text")
    for problem in problems:
        print(problem)
    print(f"public hygiene: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
