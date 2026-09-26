"""Fail when tracked files contain local paths, personal emails, or binary artifacts.

Generic rules only; private terms stay in the maintainer's local deny list.
Run: uv run python scripts/check_public_hygiene.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".pdf",
    ".zip", ".gz", ".tgz", ".7z", ".rar", ".tar", ".ipynb", ".ppt", ".pptx", ".doc",
    ".docx", ".xls", ".xlsx", ".hwp", ".hwpx", ".mp4", ".mov", ".mp3", ".wav", ".har",
}  # fmt: skip
RULES = [
    ("local path", re.compile(r"(?<![\w<])/(?:home|Users)/(?!<|\$|\{|user\b|username\b|runner\b)[A-Za-z0-9._-]+/")),
    (
        "local path",
        re.compile(r"[A-Za-z]:\\\\?Users\\\\?(?!<|%|\$|user\b|username\b|runneradmin\b|Public\b)[A-Za-z0-9._-]+"),
    ),
    (
        "email address",
        re.compile(
            r"\b[A-Za-z0-9._%+-]+@"
            r"(?!(?:users\.noreply\.github\.com|noreply\.github\.com|example\.(?:com|org|net)|localhost)(?![\w.-]))"
            r"(?![A-Za-z0-9.-]*\.invalid(?![\w.-]))[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        ),
    ),
]
SKIP = {"scripts/check_public_hygiene.py"}


def main() -> int:
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
    problems: list[str] = []
    for name in filter(None, files):
        if Path(name).suffix.lower() in BINARY_EXTENSIONS:
            problems.append(f"{name}: binary or document artifact")
            continue
        if name in SKIP:
            continue
        try:
            data = Path(name).read_bytes()
        except FileNotFoundError:
            continue
        if b"\0" in data:
            problems.append(f"{name}: binary content")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{name}: not UTF-8 text")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, rule in RULES:
                if rule.search(line):
                    problems.append(f"{name}:{lineno}: {label}")
    for problem in problems:
        print(problem)
    print(f"public hygiene: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
