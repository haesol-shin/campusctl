from __future__ import annotations

import re
import shutil
from typing import Any, TextIO

from campusctl.command_types import Rendered, Renderers
from campusctl.text_layout import _cells, _command_hint, _text, _wrap, _wrap_simple

_QUOTED_COMMAND = re.compile(r"""(?P<quote>['"`])(?P<command>campusctl(?: [^'"`]+)+)(?P=quote)""")


def _error_sentence(error: dict[str, Any]) -> tuple[str, str]:
    message = _text(error.get("message"), "An error occurred.").rstrip(" .")
    remediation = _text(error.get("remediation"))
    if remediation:
        remediation = remediation.rstrip(" .")
        remediation = remediation[:1].lower() + remediation[1:]
        message += "; " + remediation
    code = _text(error.get("code"), "unknown-error")
    return f"{message} ({code}).", code


def _errors(errors: object, width: int, color: bool) -> list[str]:
    if not isinstance(errors, list):
        return []
    lines: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        if error.get("code") == "catalog-missing" and error.get("domain") in (
            "lectures",
            "assignments",
            "notices",
            "materials",
        ):
            domain = error["domain"]
            sentence = (
                f"{domain.capitalize()} catalog is missing; run 'campusctl sync --only {domain}' (catalog-missing)."
            )
            code = "catalog-missing"
        else:
            sentence, code = _error_sentence(error)
        matches = list(_QUOTED_COMMAND.finditer(sentence))
        commands: list[str] = []
        if matches and _cells(sentence) > width:
            commands = [match.group("command") for match in matches]
            for match in reversed(matches):
                sentence = sentence[: match.start()] + "the command below" + sentence[match.end() :]
        for line in _wrap(sentence, width):
            if color and f"({code})" in line:
                line = line.replace(f"({code})", f"\033[2m({code})\033[0m", 1)
            lines.append(line)
        lines.extend(f"  {command}" for command in commands)
    return lines


def _color_marker(line: str, color: bool) -> str:
    if not color:
        return line
    if line.startswith("[ok]"):
        return "\033[32m[ok]\033[0m" + line[4:]
    if line.startswith("[!!]"):
        return "\033[33m[!!]\033[0m" + line[4:]
    return line


def _cache_advice(result: dict[str, Any], width: int) -> list[str]:
    cache = result.get("cache")
    if not isinstance(cache, dict):
        return []
    lines = []
    if isinstance(cache.get("age_seconds"), int):
        age = cache["age_seconds"]
        lines.extend(_wrap(f"Catalog generated {age} {'second' if age == 1 else 'seconds'} ago.", width))
    if cache.get("stale"):
        lines.extend(
            _wrap(
                f"Warning: Cached data may be stale. Refresh: {cache.get('refresh_command', 'campusctl sync')}", width
            )
        )
    return lines


def _command_lines(
    command: str, result: dict[str, Any], envelope: dict[str, Any], width: int, renderers: Renderers, color: bool
) -> Rendered:
    family, _, suffix = command.partition(".")
    # Bare sync and sync.lectures share one summary. Other sync.<domain> keys use that domain.
    if command in {"sync", "sync.lectures"} and "sync" in renderers.results:
        return Rendered(renderers.results["sync"](command, result, width))
    if family == "sync" and suffix in renderers.results:
        family = suffix
    if family in renderers.responses:
        rendered = renderers.responses[family](command, result, width, envelope)
        return Rendered([_color_marker(line, color) for line in rendered.lines], rendered.hide_errors)
    if family in renderers.results:
        return Rendered(renderers.results[family](command, result, width))
    if command == "usage":
        return Rendered(_command_hint("To see commands:", "campusctl --help", width))
    if command == "version" and isinstance(result.get("version"), str):
        return Rendered(_wrap_simple(f"campusctl version {result['version']}.", width))
    return Rendered([])


def render_human(
    command: str,
    envelope: dict[str, Any],
    stream: TextIO,
    *,
    renderers: Renderers,
    width: int | None = None,
    color: bool = False,
) -> None:
    """Render a CLI response envelope as short, terminal-friendly English."""
    terminal_width = max(1, width if width is not None else shutil.get_terminal_size(fallback=(80, 24)).columns)
    result = envelope.get("result")
    if not isinstance(result, dict):
        result = {}

    rendered = _command_lines(command, result, envelope, terminal_width, renderers, color)
    lines = rendered.lines
    list_advice = command in {"lectures.list", "assignments.list", "notices.list", "materials.list"}
    course_advice = command == "courses.list" and isinstance(result.get("courses"), list)
    if list_advice or course_advice:
        lines.extend(_cache_advice(result, terminal_width))
    # A renderer that already explains the envelope's errors suppresses them here.
    error_values = [] if rendered.hide_errors else envelope.get("errors")
    if isinstance(error_values, list) and command.split(".")[0] in ("lectures", "assignments", "notices", "materials"):
        domain = command.split(".")[0]
        error_values = [
            {**error, "domain": domain} if isinstance(error, dict) and error.get("code") == "catalog-missing" else error
            for error in error_values
        ]
    errors = _errors(error_values, terminal_width, color)
    if lines and errors:
        lines.append("")
    lines.extend(errors)
    for line in lines:
        stream.write(line.rstrip() + "\n")
