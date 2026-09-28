"""Prepare a release from changelog fragments and synchronized version references."""

import argparse
import datetime as dt
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADINGS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
FRAGMENT_NAME_PATTERN = re.compile(r"^(?:[0-9]+|direct)-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
VERSION_FILES = {
    "pyproject.toml": [(r'(?m)^(version = ")([0-9]+\.[0-9]+\.[0-9]+)("$)', False)],
    "src/campusctl/__init__.py": [(r'(?m)^(__version__ = ")([0-9]+\.[0-9]+\.[0-9]+)("$)', False)],
    "docs/installation.md": [
        (r"(Install the pinned `v)([0-9]+\.[0-9]+\.[0-9]+)(` release)", False),
        (r"(campusctl@v)([0-9]+\.[0-9]+\.[0-9]+)(\s|$)", False),
    ],
    "docs/agent-skill.md": [(r"(requires campusctl )([0-9]+\.[0-9]+\.[0-9]+)( or newer)", False)],
    "skills/campusctl/SKILL.md": [
        (r"(blob/v)([0-9]+\.[0-9]+\.[0-9]+)(/docs/contracts/cli\.md)", False),
        (r"(is below `)([0-9]+\.[0-9]+\.[0-9]+)(`)", False),
        (r"(This skill describes the v)([0-9]+\.[0-9]+\.[0-9]+)( release surface)", False),
    ],
    "docs/contracts/cli.md": [
        (r"(?m)^(# campusctl v)([0-9]+\.[0-9]+)( command-line contract$)", True),
        (r'("version": ")([0-9]+\.[0-9]+\.[0-9]+)(")', False),
        (r'("tool_version":")([0-9]+\.[0-9]+\.[0-9]+)(")', False),
    ],
}


def versions(root: Path = ROOT) -> tuple[str, dict[str, tuple[str, bool]]]:
    """Return project version and checked document/version references."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    refs: dict[str, tuple[str, bool]] = {}
    for name, patterns in VERSION_FILES.items():
        text = (root / name).read_text(encoding="utf-8")
        for pattern, minor in patterns:
            hits = re.findall(pattern, text)
            if len(hits) != 1:
                raise ValueError(f"{name}: expected one version reference matching {pattern}, found {len(hits)}")
            refs[f"{name}: {pattern}"] = hits[0][1], minor
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    refs["uv.lock: campusctl"] = (
        next(package["version"] for package in lock["package"] if package["name"] == "campusctl"),
        False,
    )
    return project, refs


def check_versions(root: Path = ROOT) -> str:
    project, refs = versions(root)
    mismatches = {
        name: value
        for name, (value, minor) in refs.items()
        if value != (project.rsplit(".", 1)[0] if minor else project)
    }
    if mismatches:
        details = ", ".join(f"{name}={value}" for name, value in mismatches.items())
        raise ValueError(f"version references disagree with {project}: {details}")
    return project


def parse_fragment(text: str, name: str) -> dict[str, list[str]]:
    """Only exact level-two category headings with nonempty bullets are accepted."""
    groups: dict[str, list[str]] = {}
    heading = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("## ") and line[3:] in HEADINGS and line[3:] not in groups:
            heading = line[3:]
            groups[heading] = []
        elif heading and line.startswith("- ") and line[2:].strip():
            groups[heading].append(line)
        else:
            raise ValueError(f"malformed changelog fragment {name}: {line!r}")
    if not groups or any(not bullets for bullets in groups.values()):
        raise ValueError(f"malformed changelog fragment {name}: missing heading or bullet")
    return groups


def fold_fragments(root: Path = ROOT) -> tuple[str, list[Path]]:
    files = sorted(path for path in (root / "changelog.d").glob("*.md") if path.name != "README.md")
    if not files:
        raise ValueError("no changelog fragments found")
    groups: dict[str, list[str]] = {heading: [] for heading in HEADINGS}
    for path in files:
        if not FRAGMENT_NAME_PATTERN.fullmatch(path.name):
            raise ValueError(
                f"invalid changelog fragment filename: {path.name} "
                "(must be '<issue-number>-<slug>.md' or 'direct-<slug>.md')"
            )
        for heading, bullets in parse_fragment(path.read_text(encoding="utf-8"), path.name).items():
            groups[heading].extend(bullets)
    body = "\n\n".join(f"### {heading}\n" + "\n".join(bullets) for heading, bullets in groups.items() if bullets)
    return body, files


def next_version(current: str, requested: str) -> str:
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", requested):
        target = requested
    else:
        major, minor, patch = map(int, current.split("."))
        if requested == "major":
            target = f"{major + 1}.0.0"
        elif requested == "minor":
            target = f"{major}.{minor + 1}.0"
        elif requested == "patch":
            target = f"{major}.{minor}.{patch + 1}"
        else:
            raise ValueError(f"invalid version: {requested}")
    if tuple(map(int, target.split("."))) <= tuple(map(int, current.split("."))):
        raise ValueError(f"release version {target} must exceed {current}")
    return target


def prepare(root: Path, requested: str, date: str) -> str:
    current = check_versions(root)
    target = next_version(current, requested)
    date = dt.date.fromisoformat(date).isoformat()
    body, fragments = fold_fragments(root)
    changelog = root / "CHANGELOG.md"
    original = changelog.read_text(encoding="utf-8")
    if not original.startswith("# Changelog\n") or re.search(rf"(?m)^## \[{re.escape(target)}\]", original):
        raise ValueError("changelog header missing or release already present")
    first_entry = re.search(r"(?m)^## \[[0-9]+\.[0-9]+\.[0-9]+\] - ", original)
    if not first_entry:
        raise ValueError("changelog has no release entries")
    for name, patterns in VERSION_FILES.items():
        path = root / name
        text = path.read_text(encoding="utf-8")
        for pattern, minor in patterns:
            replacement = target.rsplit(".", 1)[0] if minor else target
            text, count = re.subn(pattern, lambda match, value=replacement: match[1] + value + match[3], text)
            if count != 1:
                raise ValueError(f"{name}: expected one version reference for {pattern}")
        path.write_text(text, encoding="utf-8")
    subprocess.run(["uv", "lock"], cwd=root, check=True)
    changelog.write_text(
        original[: first_entry.start()] + f"## [{target}] - {date}\n\n{body}\n\n" + original[first_entry.start() :],
        encoding="utf-8",
    )
    for fragment in fragments:
        fragment.unlink()
    check_versions(root)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="X.Y.Z, major, minor, or patch")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="release date (YYYY-MM-DD)")
    parser.add_argument("--check", action="store_true", help="verify all version references without writing files")
    args = parser.parse_args()
    try:
        if args.check:
            if args.version:
                parser.error("--check takes no version")
            print(f"Version references agree: {check_versions()}")
        elif args.version:
            print(f"Prepared release {prepare(ROOT, args.version, args.date)}")
        else:
            parser.error("version is required unless --check is used")
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"release preparation failed: {error}\n")


if __name__ == "__main__":
    main()
