"""Pure cell width, wrapping and truncation."""

from __future__ import annotations

import unicodedata


def _text(value: object, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    return " ".join(value.split()) or fallback


def _char_cells(character: str) -> int:
    if unicodedata.combining(character) or unicodedata.category(character) in {"Mn", "Me", "Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def _cells(value: str) -> int:
    return sum(_char_cells(character) for character in value)


def _truncate(value: str, width: int) -> str:
    value = _text(value)
    if width <= 0:
        return ""
    if _cells(value) <= width:
        return value
    if width == 1:
        return "…"
    result: list[str] = []
    used = 0
    for character in value:
        size = _char_cells(character)
        if used + size > width - 1:
            break
        result.append(character)
        used += size
    return "".join(result) + "…"


def _ascii_truncate(value: str, width: int) -> str:
    value = value.encode("ascii", errors="replace").decode("ascii")
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    leading = min(len(value) - len(value.lstrip(" ")), width)
    prefix = " " * leading
    content_width = width - leading
    if content_width <= 3:
        return prefix + "." * content_width
    return prefix + _truncate(value[leading:], content_width - 2).replace("…", "...")


def _chunks(value: str, width: int) -> list[str]:
    chunks: list[str] = []
    line = ""
    used = 0
    for character in value:
        size = _char_cells(character)
        if size > width:
            character, size = "?", 1
        if used + size > width and line:
            chunks.append(line)
            line = ""
            used = 0
        line += character
        used += size
    if line or not chunks:
        chunks.append(line)
    return chunks


def _wrap(value: str, width: int) -> list[str]:
    value = _text(value)
    width = max(1, width)
    if not value:
        return [""]
    lines: list[str] = []
    line = ""
    for word in value.split(" "):
        if _cells(word) > width:
            if line:
                lines.append(line)
                line = ""
            pieces = _chunks(word, width)
            lines.extend(pieces[:-1])
            line = pieces[-1]
            continue
        candidate = f"{line} {word}" if line else word
        if _cells(candidate) <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line or not lines:
        lines.append(line)
    return lines


def _pad(value: str, width: int) -> str:
    return value + " " * max(0, width - _cells(value))


def _command_hint(label: str, command: str, width: int) -> list[str]:
    combined = f"{label} {command}"
    if width >= 60 and _cells(combined) <= width:
        return [combined]
    return [*_wrap(label, width), f"  {command}"]


def _wrap_simple(line: str, width: int) -> list[str]:
    prefix = "Next: "
    if line.startswith(prefix) and line[len(prefix) :].startswith("campusctl "):
        return _command_hint("Next:", line[len(prefix) :], width)
    return _wrap(line, width)


def _wrap_field(label: str, value: str, width: int) -> list[str]:
    indent = "  "
    prefix = f"{indent}{label}: "
    if _cells(prefix) >= width:
        return _wrap(f"{label}: {value}", width)
    value_lines = _wrap(value, width - _cells(prefix))
    return [prefix + value_lines[0], *(indent + line for line in value_lines[1:])]


def _course_heading(label: str, width: int) -> list[str]:
    prefix = "Course: "
    if _cells(prefix) >= width:
        return _wrap(f"Course: {label}", width)
    return [prefix + _truncate(label, width - _cells(prefix))]
