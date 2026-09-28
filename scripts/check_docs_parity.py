"""Check the six public English/Korean documentation pairs without external dependencies.

Run: python3 scripts/check_docs_parity.py [repository-root]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

PAIRS = (
    ("README.md", "README.ko.md"),
    ("docs/installation.md", "docs/installation.ko.md"),
    ("docs/configuration.md", "docs/configuration.ko.md"),
    ("docs/troubleshooting.md", "docs/troubleshooting.ko.md"),
    ("docs/agent-skill.md", "docs/agent-skill.ko.md"),
    ("docs/usage.md", "docs/usage.ko.md"),
)
FENCE_OPEN = re.compile(r"^[ \t]{0,3}(?P<mark>`{3,}|~{3,})(?P<info>.*)$")
HEADING = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)\s*$")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
HTML_TAG = re.compile(r"<[^>]+>")
HTML_LINK = re.compile(r"\b(?:href|src)\s*=\s*(['\"])(.*?)\1")
HTML_ANCHOR = re.compile(r"\b(?:id|name)\s*=\s*(['\"])(.*?)\1")
EXTERNAL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:|^//")
LINE_ANCHOR = re.compile(r"^L([1-9]\d*)(?:-L([1-9]\d*))?$")
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


@dataclass(frozen=True)
class Heading:
    level: int
    line: int
    text: str


@dataclass(frozen=True)
class Fence:
    language: str
    content: str
    line: int


@dataclass(frozen=True)
class Link:
    destination: str
    line: int


@dataclass
class Page:
    first_line: int
    first_text: str
    headings: list[Heading]
    fences: list[Fence]
    links: list[Link]
    anchors: set[str]
    unclosed_fence: int | None


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _heading_slug(text: str) -> str:
    # GitHub heading anchors retain Unicode letters/numbers, spaces, hyphens and underscores.
    return "".join(char for char in text.lower() if char.isalnum() or char in " -_").replace(" ", "-")


def _parse(path: Path) -> Page:
    lines = _text(path).splitlines(keepends=True)
    first_line, first_text = next(
        ((number, raw.rstrip("\n")) for number, raw in enumerate(lines, 1) if raw.strip()),
        (0, ""),
    )
    headings: list[Heading] = []
    fences: list[Fence] = []
    links: list[Link] = []
    anchors: set[str] = set()
    slug_counts: Counter[str] = Counter()
    fence_mark = ""
    fence_length = 0
    fence_language = ""
    fence_line = 0
    fence_body: list[str] = []

    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if fence_mark:
            closing = line.strip(" \t")
            if len(closing) >= fence_length and set(closing) == {fence_mark}:
                fences.append(Fence(fence_language, "".join(fence_body), fence_line))
                fence_mark = ""
                fence_body = []
            else:
                fence_body.append(raw)
            continue

        opener = FENCE_OPEN.match(line)
        if opener:
            mark = opener.group("mark")
            fence_mark = mark[0]
            fence_length = len(mark)
            info = opener.group("info").strip()
            fence_language = info.split()[0] if info else ""
            fence_line = number
            continue

        heading = HEADING.match(line)
        if heading:
            text = heading.group(2).rstrip(" #")
            headings.append(Heading(len(heading.group(1)), number, text))
            slug = _heading_slug(text)
            suffix = slug_counts[slug]
            anchors.add(f"{slug}-{suffix}" if suffix else slug)
            slug_counts[slug] += 1

        for match in MARKDOWN_LINK.finditer(line):
            destination = match.group(1).strip("<>")
            links.append(Link(destination, number))
        for tag in HTML_TAG.finditer(line):
            for match in HTML_LINK.finditer(tag.group()):
                links.append(Link(match.group(2), number))
            for match in HTML_ANCHOR.finditer(tag.group()):
                anchors.add(match.group(2))

    return Page(first_line, first_text, headings, fences, links, anchors, fence_line if fence_mark else None)


def _problem(pair: str, path: str, line: int, kind: str, detail: str) -> str:
    return f"{pair}: {path}:{line}: {kind}: {detail}"


def _switcher(page: Page, pair: str, path: str, expected: str, *, english: bool) -> list[str]:
    if english:
        pattern = rf"\*\*English\*\*\s*\|\s*\[한국어\]\({re.escape(expected)}\)"
    else:
        pattern = rf"\[English\]\({re.escape(expected)}\)\s*\|\s*\*\*한국어\*\*"
    if re.fullmatch(pattern, page.first_text) is None:
        return [
            _problem(
                pair,
                path,
                page.first_line,
                "switcher",
                f"first visible line must mark the current language and link to {expected!r}",
            )
        ]
    return []


def _ordinary_links(page: Page, switch_target: str) -> list[Link]:
    links = []
    skipped = False
    for link in page.links:
        if not skipped and link.line == page.first_line and link.destination == switch_target:
            skipped = True
        else:
            links.append(link)
    return links


def _split_local(destination: str) -> tuple[str, str]:
    path, _, fragment = destination.partition("#")
    path = path.partition("?")[0]
    return path, fragment


def _normalized_destination(destination: str) -> str:
    if EXTERNAL.match(destination):
        return destination
    path, sep, rest = destination.partition("#")
    before_query, query_sep, query = path.partition("?")
    if before_query.endswith(".ko.md"):
        before_query = before_query[: -len(".ko.md")] + ".md"
    return before_query + query_sep + query + sep + rest


def _validate_links(root: Path, path: str, page: Page, pair: str, anchor_cache: dict[Path, set[str]]) -> list[str]:
    problems = []
    source = root / path
    for link in page.links:
        destination = link.destination
        if EXTERNAL.match(destination):
            continue
        local_path, fragment = _split_local(destination)
        local_path = unquote(local_path)
        if not local_path:
            target = source
        elif local_path.startswith("/"):
            target = root / local_path.lstrip("/")
        else:
            target = source.parent / local_path
        target = target.resolve()
        if not target.is_relative_to(root) or not target.exists():
            problems.append(_problem(pair, path, link.line, "link-missing-file", repr(destination)))
            continue
        if not fragment:
            continue
        if not target.is_file():
            problems.append(_problem(pair, path, link.line, "link-missing-anchor", repr(destination)))
            continue
        anchor = unquote(fragment)
        try:
            line_anchor = LINE_ANCHOR.fullmatch(anchor)
            if line_anchor:
                first = int(line_anchor.group(1))
                last = int(line_anchor.group(2) or first)
                line_count = len(_text(target).splitlines())
                valid = first <= last <= line_count
            elif target.suffix.lower() == ".md":
                if target not in anchor_cache:
                    anchor_cache[target] = _parse(target).anchors
                valid = anchor in anchor_cache[target]
            else:
                valid = False
        except UnicodeDecodeError:
            problems.append(_problem(pair, path, link.line, "link-invalid-utf8", repr(destination)))
            continue
        if not valid:
            problems.append(_problem(pair, path, link.line, "link-missing-anchor", repr(destination)))
    return problems


def _compare_headings(english: Page, korean: Page, pair: str, ko_path: str) -> list[str]:
    problems = []
    if len(english.headings) != len(korean.headings):
        problems.append(
            _problem(
                pair,
                ko_path,
                korean.headings[-1].line if korean.headings else 1,
                "heading-count",
                f"English has {len(english.headings)}, Korean has {len(korean.headings)}",
            )
        )
    for index, (en, ko) in enumerate(zip(english.headings, korean.headings, strict=False), 1):
        if en.level != ko.level:
            problems.append(
                _problem(
                    pair,
                    ko_path,
                    ko.line,
                    "heading-level",
                    f"heading {index}: English level {en.level}, Korean level {ko.level}",
                )
            )
    return problems


def _compare_fences(english: Page, korean: Page, pair: str, ko_path: str) -> list[str]:
    problems = []
    if len(english.fences) != len(korean.fences):
        problems.append(
            _problem(
                pair,
                ko_path,
                korean.fences[-1].line if korean.fences else 1,
                "fence-count",
                f"English has {len(english.fences)}, Korean has {len(korean.fences)}",
            )
        )
    for index, (en, ko) in enumerate(zip(english.fences, korean.fences, strict=False), 1):
        if en.language != ko.language:
            problems.append(
                _problem(
                    pair,
                    ko_path,
                    ko.line,
                    "fence-language",
                    f"block {index}: English {en.language!r}, Korean {ko.language!r}",
                )
            )
        if en.content != ko.content:
            en_lines = en.content.splitlines(keepends=True)
            ko_lines = ko.content.splitlines(keepends=True)
            offset = next(
                (
                    position
                    for position in range(max(len(en_lines), len(ko_lines)))
                    if en_lines[position : position + 1] != ko_lines[position : position + 1]
                ),
                0,
            )
            problems.append(
                _problem(pair, ko_path, ko.line + offset + 1, "fence-content", f"block {index} differs from English")
            )
    return problems


def _compare_links(english: list[Link], korean: list[Link], pair: str, en_path: str, ko_path: str) -> list[str]:
    en_links = [(link, _normalized_destination(link.destination)) for link in english]
    ko_links = [(link, _normalized_destination(link.destination)) for link in korean]
    en_counts = Counter(destination for _, destination in en_links)
    ko_counts = Counter(destination for _, destination in ko_links)
    problems = []
    for destination, count in sorted((en_counts - ko_counts).items()):
        line = next(link.line for link, value in en_links if value == destination)
        problems.append(
            _problem(pair, en_path, line, "link-target", f"{destination!r} appears {count} extra time(s) in English")
        )
    for destination, count in sorted((ko_counts - en_counts).items()):
        line = next(link.line for link, value in ko_links if value == destination)
        problems.append(
            _problem(pair, ko_path, line, "link-target", f"{destination!r} appears {count} extra time(s) in Korean")
        )
    return problems


def _orphans(root: Path) -> list[str]:
    expected = {korean for _, korean in PAIRS}
    problems = []
    for directory, children, files in os.walk(root):
        children[:] = sorted(child for child in children if child not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".ko.md"):
                path = (Path(directory) / name).relative_to(root).as_posix()
                if path not in expected:
                    problems.append(_problem("inventory", path, 1, "orphan-ko", "not one of the six public pairs"))
    return problems


def check(root: Path) -> int:
    root = root.resolve()
    problems = []
    anchor_cache: dict[Path, set[str]] = {}
    for en_path, ko_path in PAIRS:
        pair = f"{en_path} <-> {ko_path}"
        en_file, ko_file = root / en_path, root / ko_path
        for name, file in ((en_path, en_file), (ko_path, ko_file)):
            if not file.is_file():
                problems.append(_problem(pair, name, 0, "missing-counterpart", "file does not exist"))
        if not en_file.is_file() or not ko_file.is_file():
            continue
        pages: dict[str, Page] = {}
        for name, file in ((en_path, en_file), (ko_path, ko_file)):
            try:
                pages[name] = _parse(file)
            except UnicodeDecodeError:
                problems.append(_problem(pair, name, 0, "invalid-utf8", "documentation must be UTF-8"))
        if len(pages) != 2:
            continue
        english, korean = pages[en_path], pages[ko_path]
        anchor_cache[en_file.resolve()] = english.anchors
        anchor_cache[ko_file.resolve()] = korean.anchors
        problems.extend(_switcher(english, pair, en_path, ko_file.name, english=True))
        problems.extend(_switcher(korean, pair, ko_path, en_file.name, english=False))
        if english.unclosed_fence is not None:
            problems.append(_problem(pair, en_path, english.unclosed_fence, "fence-unclosed", "missing closing fence"))
        if korean.unclosed_fence is not None:
            problems.append(_problem(pair, ko_path, korean.unclosed_fence, "fence-unclosed", "missing closing fence"))
        problems.extend(_compare_headings(english, korean, pair, ko_path))
        problems.extend(_compare_fences(english, korean, pair, ko_path))
        problems.extend(_validate_links(root, en_path, english, pair, anchor_cache))
        problems.extend(_validate_links(root, ko_path, korean, pair, anchor_cache))
        problems.extend(
            _compare_links(
                _ordinary_links(english, ko_file.name),
                _ordinary_links(korean, en_file.name),
                pair,
                en_path,
                ko_path,
            )
        )
    problems.extend(_orphans(root))
    for problem in problems:
        print(problem)
    if problems:
        print(f"docs parity: {len(problems)} problem(s) across {len(PAIRS)} pairs")
        return 1
    print(f"docs parity: OK ({len(PAIRS)} pairs)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    return check(parser.parse_args().root)


if __name__ == "__main__":
    sys.exit(main())
