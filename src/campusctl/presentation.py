from __future__ import annotations

import re
import shutil
import unicodedata
from collections.abc import Callable
from typing import Any, TextIO

from campusctl.catalog import catalog_path
from campusctl.commands import discover_domain_modules
from campusctl.domain_catalog import domain_catalog_path
from campusctl.paths import data_dir

_DOMAIN_RENDERERS: dict[str, Callable[[str, dict[str, Any], int], list[str]]] = {
    name: module.render for name, module in discover_domain_modules().items()
}

_STATUS_WIDTH = 11
_QUOTED_COMMAND = re.compile(r"""(?P<quote>['"`])(?P<command>campusctl(?: [^'"`]+)+)(?P=quote)""")


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


def _progress_time(value: object) -> str:
    try:
        seconds = max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        seconds = 0
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes:02d}:{seconds:02d}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def render_progress(event: dict[str, Any], stream: TextIO, width: int) -> str:
    """Write one ASCII playback-progress line without a newline and return it."""
    width = max(1, width)
    event_type = event.get("type")
    if event_type == "started":
        index = _text(str(event.get("index", "?")))
        total = _text(str(event.get("total", "?")))
        prefix = f"[{index}/{total}] Playing: "
        title = _text(event.get("title"), "Untitled lecture").encode("ascii", errors="replace").decode("ascii")
        title_width = width - _cells(prefix)
        line = (
            prefix + _ascii_truncate(title, title_width) if title_width > 0 else _ascii_truncate(prefix + title, width)
        )
    elif event_type == "position":
        line = f"  {_progress_time(event.get('position_seconds'))} / {_progress_time(event.get('duration_seconds'))}"
        line = _ascii_truncate(line, width)
    elif event_type == "verifying":
        line = _ascii_truncate("  Checking completion in the LMS...", width)
    elif event_type == "finished":
        outcome = _text(event.get("outcome"), "finished")
        line = _ascii_truncate(f"  Done: {outcome}", width)
    else:
        line = ""
    stream.write(line)
    return line


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


def _pad(value: str, width: int) -> str:
    return value + " " * max(0, width - _cells(value))


def _lecture_status(lecture: dict[str, Any]) -> str:
    if lecture.get("open") is False:
        available_date = _available_date(lecture.get("available_from"))
        return f"opens {available_date[5:]}" if available_date is not None else "not open"
    completion = lecture.get("completion")
    if completion == "complete":
        return "done"
    if completion == "recorded":
        return "watched (not counted)"
    if completion != "incomplete":
        return "unknown"
    progress = lecture.get("progress_text")
    if isinstance(progress, str):
        match = re.match(r"\s*(\d+)\s*분(?:\s*(\d+)\s*초)?\s*/", progress)
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2) or 0)
            return "in progress" if minutes or seconds else "not started"
    return "unfinished"


def _course(lecture: dict[str, Any]) -> tuple[str, str]:
    course = lecture.get("course")
    if isinstance(course, dict):
        course_id = _text(course.get("id"), "unknown")
        label = _text(course.get("label"), course_id)
        return course_id, label
    return "unknown", "Unknown course"


def _due_date(lecture: dict[str, Any]) -> str:
    value = lecture.get("due_date")
    if not isinstance(value, str):
        return "-"
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?", value)
    return f"{match.group(1)} {match.group(2)}" if match else value


def _cache_timestamp(value: object) -> str:
    timestamp = _text(value)
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|\+00:00)", timestamp)
    return f"{match.group(1)} {match.group(2)} UTC" if match else timestamp


def _media(lecture: dict[str, Any]) -> str:
    media = _text(lecture.get("media"), "other")
    return "" if media == "video" else f"[{media}]"


def _available_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:$|T)", value)
    return match.group(1) if match else None


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


def _marker(ok: bool | None, label: str, color: bool) -> str:
    if ok is True:
        marker = "[ok]"
        if color:
            marker = f"\033[32m{marker}\033[0m"
    else:
        marker = "[!!]"
        if color:
            marker = f"\033[33m{marker}\033[0m"
    return f"{marker} {label}"


def _doctor(result: dict[str, Any], width: int, errors: object) -> tuple[list[str], bool]:
    checks: list[str] = []
    config = result.get("config")
    if isinstance(config, dict) and isinstance(config.get("present"), bool):
        present = config["present"]
        checks.append(
            _marker(
                present,
                "Configuration file present" if present else "Configuration file missing",
                False,
            )
        )

    credentials = result.get("credentials")
    if isinstance(credentials, dict) and "configured" in credentials:
        configured = credentials.get("configured")
        label = (
            "Credentials configured"
            if configured is True
            else "Credentials not configured"
            if configured is False
            else "Credential status unknown"
        )
        checks.append(_marker(configured is True, label, False))

    browser = result.get("browser")
    if isinstance(browser, dict):
        mode = browser.get("mode")
        if mode == "cdp":
            checks.append(
                _marker(
                    True,
                    "Using an existing browser (connection is checked when you run sync or play)",
                    False,
                )
            )
        elif mode == "local":
            if isinstance(browser.get("playwright_importable"), bool):
                installed = browser["playwright_importable"]
                checks.append(
                    _marker(installed, "Playwright available" if installed else "Playwright not installed", False)
                )
            if isinstance(browser.get("chromium_installed"), bool):
                installed = browser["chromium_installed"]
                checks.append(
                    _marker(installed, "Chromium installed" if installed else "Chromium not installed", False)
                )
            display_available = result.get("display_available")
            if isinstance(display_available, bool):
                checks.append(
                    _marker(
                        display_available,
                        "Display available" if display_available else "Display unavailable",
                        False,
                    )
                )

    materials = result.get("materials")
    if isinstance(materials, dict) and materials.get("adopt_existing") is not None:
        if materials.get("download_dir") is None:
            checks.append("[ok] Materials downloads: default")
        else:
            adoption = "on" if materials["adopt_existing"] else "off"
            checks.append(f"[ok] Materials downloads: configured (adoption {adoption})")

    catalog = result.get("catalog")
    catalog_missing = isinstance(catalog, dict) and catalog.get("present") is False
    if isinstance(catalog, dict) and isinstance(catalog.get("present"), bool):
        present = catalog["present"]
        checks.append(_marker(present, "Lecture catalog present" if present else "Lecture catalog missing", False))

    has_errors = bool(errors)
    only_catalog_missing_error = (
        isinstance(errors, list)
        and len(errors) == 1
        and isinstance(errors[0], dict)
        and errors[0].get("code") == "catalog-missing"
    )
    has_other_failures = any(check.startswith("[!!]") and check != "[!!] Lecture catalog missing" for check in checks)
    catalog_is_only_failure = (
        catalog_missing
        and only_catalog_missing_error
        and not has_other_failures
        and any(check == "[!!] Lecture catalog missing" for check in checks)
    )
    if catalog_is_only_failure:
        checks.remove("[!!] Lecture catalog missing")

    lines = [line for check in checks for line in _wrap(check, width)]
    if catalog_is_only_failure:
        lines.extend(_command_hint("Almost ready. Next:", "campusctl sync", width))
    elif not has_errors and not catalog_missing and not has_other_failures and checks:
        lines.append("Ready.")
    return lines, catalog_is_only_failure


def _color_marker(line: str, color: bool) -> str:
    if not color:
        return line
    if line.startswith("[ok]"):
        return "\033[32m[ok]\033[0m" + line[4:]
    if line.startswith("[!!]"):
        return "\033[33m[!!]\033[0m" + line[4:]
    return line


def _lectures(result: dict[str, Any], width: int) -> list[str]:
    lectures = result.get("lectures")
    if not isinstance(lectures, list):
        return []
    rows = [lecture for lecture in lectures if isinstance(lecture, dict)]
    cache = result.get("cache")
    generated_at = _cache_timestamp(cache.get("generated_at")) if isinstance(cache, dict) else ""
    count_label = "lecture" if len(rows) == 1 else "lectures"
    header = f"{len(rows)} {count_label}"
    if generated_at:
        updated = f"(updated {generated_at})"
        if _cells(f"{header} {updated}") <= width:
            lines = [f"{header} {updated}"]
        else:
            lines = _wrap(header, width) + _wrap(updated, width)
    else:
        lines = _wrap(header, width)
    if not rows:
        lines.append("")
        lines.extend(_wrap("Nothing unfinished.", width))
        lines.extend(
            _command_hint("To include completed or recorded lectures:", "campusctl lectures list --all", width)
        )
        lines.extend(_command_hint("To refresh:", "campusctl sync", width))
        return lines

    groups: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for lecture in rows:
        course_id, label = _course(lecture)
        if course_id not in groups:
            groups[course_id] = (label, [])
        groups[course_id][1].append(lecture)

    for label, group_rows in groups.values():
        lines.append("")
        lines.extend(_course_heading(label, width))
        due_width = max(3, max((_cells(_due_date(lecture)) for lecture in group_rows), default=3))
        media_width = max((_cells(_media(lecture)) for lecture in group_rows), default=0)
        has_media = media_width > 0
        available_title_width = width - 6 - due_width - _STATUS_WIDTH - (2 + media_width if has_media else 0)
        title_width = min(
            available_title_width,
            max((_cells(_text(lecture.get("title"), "(untitled lecture)")) for lecture in group_rows), default=0),
        )
        if width >= 60 and title_width > 0:
            for lecture in group_rows:
                title = _truncate(_text(lecture.get("title"), "(untitled lecture)"), title_width)
                due = _due_date(lecture)
                status = _lecture_status(lecture)
                row = f"  {_pad(title, title_width)}  {_pad(due, due_width)}  {_pad(status, _STATUS_WIDTH)}"
                if has_media:
                    row += f"  {_media(lecture)}"
                lines.append(row.rstrip())
                entity_id = _text(lecture.get("entity_id"), "unknown-entity")
                lines.append(f"    {entity_id}")
        else:
            for lecture in group_rows:
                title_text = _text(lecture.get("title"), "(untitled lecture)")
                title_width = max(0, width - _cells("  Title: "))
                title = _truncate(title_text, title_width) if title_width else title_text
                lines.extend(_wrap_field("Title", title, width))
                lines.extend(_wrap_field("Due", _due_date(lecture), width))
                lines.extend(_wrap_field("Status", _lecture_status(lecture), width))
                media = _media(lecture)
                if media:
                    lines.extend(_wrap_field("Media", media, width))
                entity_id = _text(lecture.get("entity_id"), "unknown-entity")
                lines.append(f"    {entity_id}")

    playable = next(
        (
            lecture
            for lecture in rows
            if lecture.get("open") is True
            and lecture.get("completion") == "incomplete"
            and lecture.get("media") in {"video", "youtube"}
            and isinstance(lecture.get("entity_id"), str)
            and lecture["entity_id"]
        ),
        None,
    )
    lines.append("")
    if playable is not None:
        entity_id = playable["entity_id"]
        command = f"campusctl lectures play {entity_id}"
        lines.extend(_command_hint("To play one:", command, width))
    else:
        opening_dates: list[str] = []
        for lecture in rows:
            if (
                lecture.get("open") is False
                and lecture.get("completion") == "incomplete"
                and lecture.get("media") in {"video", "youtube"}
                and isinstance(lecture.get("entity_id"), str)
                and lecture["entity_id"]
            ):
                available_date = _available_date(lecture.get("available_from"))
                if available_date is not None:
                    opening_dates.append(available_date)
        if opening_dates:
            next_opening = min(opening_dates)
            message = f"No lectures are open to play yet. Next opens: {next_opening[5:]}."
        else:
            message = "No lectures are open to play yet. Opening dates are not published yet."
        lines.extend(_wrap(message, width))
    lines.extend(_command_hint("To refresh:", "campusctl sync", width))
    return lines


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


def _status(result: dict[str, Any], width: int) -> list[str]:
    sections = (
        ("Assignments due soon", "assignments", "due_soon", "unknown_due_count", "campusctl assignments list"),
        ("Unread notices", "notices", "unread", "unknown_count", "campusctl notices list"),
        ("Open incomplete lectures", "lectures", "open_incomplete", "unknown_open_count", "campusctl lectures list"),
    )
    lines = ["Coursework status (local catalogs)"]
    for label, domain, key, unknown, command in sections:
        section = result.get(domain, {})
        entries = section.get(key, [])
        lines.append(f"{label}: {len(entries)}; unknown: {section.get(unknown, 0)}")
        for entry in entries[:3]:
            lines.append("  " + _truncate(_text(entry.get("title"), _text(entry.get("entity_id"))), width - 2))
        if len(entries) > 3:
            lines.append(f"  +{len(entries) - 3} more")
        lines.append(f"  See: {command}")
    coverage = result.get("coverage", {})
    gaps = [f"{name}: {value['state']}" for name, value in coverage.items() if value.get("state") != "available"]
    if gaps:
        lines.append("Missing coverage: " + ", ".join(gaps))
    stale = result.get("stale_courses", [])
    if stale:
        lines.append(f"Stale courses: {len(stale)}")
        lines.extend(
            "  " + _truncate(_text(item.get("label"), _text(item.get("course_id"))), width - 2) for item in stale[:3]
        )
        if len(stale) > 3:
            lines.append(f"  +{len(stale) - 3} more")
    return lines


def _courses(result: dict[str, Any], width: int) -> list[str]:
    courses = result.get("courses")
    if not isinstance(courses, list):
        return []
    rows = [course for course in courses if isinstance(course, dict)]
    count_label = "course" if len(rows) == 1 else "courses"
    lines = _wrap(f"{len(rows)} {count_label}", width)
    for number, course in enumerate(rows, 1):
        course_id = _text(course.get("course_id"), "unknown")
        label = _text(course.get("label"), course_id)
        if width < 60 or _cells(course_id) + 8 > width:
            lines.extend(_course_heading(label, width))
            lines.extend(_wrap(f"  {number}. {course_id}", width))
        else:
            label_width = width - _cells(course_id) - 8
            lines.append(f"{number}. {_pad(_truncate(label, label_width), label_width)}  {course_id}")
    return lines + _cache_advice(result, width)


def _sync(result: dict[str, Any], width: int) -> list[str]:
    if isinstance(result.get("domains"), dict):
        return [f"{name}: {entry.get('status', 'unknown')}" for name, entry in result["domains"].items()]
    if not any(key in result for key in ("courses", "lectures", "incomplete", "failed_courses")):
        return []
    courses = result.get("courses", 0)
    lectures = result.get("lectures", 0)
    incomplete = result.get("incomplete", 0)
    lines = _wrap(
        f"Synced {courses} {'course' if courses == 1 else 'courses'}, "
        f"{lectures} {'lecture' if lectures == 1 else 'lectures'}, {incomplete} unfinished.",
        width,
    )
    failed = result.get("failed_courses")
    if isinstance(failed, list):
        for course in failed:
            if not isinstance(course, dict):
                continue
            label = _text(course.get("label"), _text(course.get("course_id"), "Unknown course"))
            course_id = _text(course.get("course_id"), "unknown")
            lines.extend(_wrap(f"Failed: {label} ({course_id})", width))
    return lines


def _play(result: dict[str, Any], width: int) -> list[str]:
    items = result.get("items")
    if not isinstance(items, list):
        return []
    labels = {
        "completed": "Completed",
        "already-complete": "Already complete",
        "recorded": "Watched (not counted)",
        "unverified": "Unverified",
        "failed": "Failed",
        "not-started": "Not started",
        "unknown": "Unknown",
    }
    counts: dict[str, int] = dict.fromkeys(labels, 0)
    lines: list[str] = []
    processed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("outcome")
        outcome = value if isinstance(value, str) and value else "unknown"
        if outcome not in labels:
            labels[outcome] = outcome.replace("-", " ").capitalize()
            counts[outcome] = 0
        counts[outcome] += 1
        processed += 1
        entity_id = _text(item.get("entity_id"), "unknown-entity")
        lines.extend(_wrap(f"{labels[outcome]}:", width))
        lines.append(f"  {entity_id}")
        if outcome == "recorded":
            lines.extend(
                _wrap(
                    "The LMS row reports full watch progress. It does not count for attendance or mark the lecture complete.",
                    width,
                )
            )
    if not processed:
        lines.extend(_wrap("No lectures were played.", width))
        return lines
    summary = [f"{count} {key.replace('-', ' ')}" for key, count in counts.items() if count]
    lines.extend(_wrap("Playback: " + ", ".join(summary) + ".", width))
    return lines


def _simple(command: str, result: dict[str, Any]) -> list[str]:
    if command == "auth.set" and "configured" in result:
        configured = result.get("configured")
        if configured is True:
            return ["Password saved to the OS keyring."]
        if configured is False:
            return ["No password was saved."]
        return ["Password-save status is unknown."]
    if command == "auth.status" and any(
        key in result for key in ("provider", "configured", "backend", "helper", "check")
    ):
        provider = _text(result.get("provider"))
        configured = result.get("configured")
        if provider == "command":
            helper = _text(result.get("helper"), "unknown")
            state = (
                "is configured"
                if configured is True
                else "is not configured"
                if configured is False
                else "status is unknown"
            )
            check = (
                result.get("check")
                if result.get("check") in {"ok", "failed"}
                else "unknown"
                if "check" in result
                else "not run"
            )
            return [f"Credential helper {helper} {state}; check: {check}."]
        if provider == "keyring":
            state = "saved" if configured is True else "not saved" if configured is False else "status unknown"
            backend = _text(result.get("backend"), "unknown backend")
            return [f"Credentials are {state} in the OS keyring ({backend})."]
        return ["Credential provider status is unknown."]
    if command == "config.init" and any(key in result for key in ("created", "config_path", "next")):
        path = _text(result.get("config_path"), "the configuration path")
        created = result.get("created")
        state = "created" if created is True else "already exists" if created is False else "status is unknown"
        lines = [f"Configuration {state} at {path}."]
        next_steps = result.get("next")
        if isinstance(next_steps, list):
            lines.extend(f"Next: {_text(step)}" for step in next_steps if isinstance(step, str) and _text(step))
        return lines
    if command == "setup" and isinstance(result.get("browser"), dict):
        browser = result["browser"]
        action = browser.get("action")
        if action == "skipped-custom-executable":
            installed = browser.get("installed")
            message = (
                "Custom browser executable is available."
                if installed is True
                else "Custom browser executable is unavailable."
                if installed is False
                else "Custom browser executable availability is unknown."
            )
        elif action in {"installed", "already-installed"}:
            installed = browser.get("installed")
            if installed is True:
                message = "Chromium was installed." if action == "installed" else "Chromium is already installed."
            else:
                message = (
                    "Chromium is unavailable." if installed is False else "Chromium installation status is unknown."
                )
        else:
            actions = {
                "skipped-cdp": "Skipped Chromium installation because a CDP browser is configured.",
                "declined": "Chromium installation was declined.",
            }
            message = actions.get(action, "Browser setup status is unknown.")
        lines = [message]
        next_steps = result.get("next")
        if isinstance(next_steps, list):
            lines.extend(f"Next: {_text(step)}" for step in next_steps if isinstance(step, str) and _text(step))
        return lines
    if command == "version" and isinstance(result.get("version"), str):
        return [f"campusctl version {result['version']}."]
    return []


def render_human(
    command: str,
    envelope: dict[str, Any],
    stream: TextIO,
    *,
    width: int | None = None,
    color: bool = False,
) -> None:
    """Render a CLI response envelope as short, terminal-friendly English."""
    terminal_width = max(1, width if width is not None else shutil.get_terminal_size(fallback=(80, 24)).columns)
    result = envelope.get("result")
    if not isinstance(result, dict):
        result = {}

    domain_name, separator, suffix = command.partition(".")
    if domain_name == "sync":
        domain_name = suffix
    if separator and domain_name in _DOMAIN_RENDERERS:
        lines = _DOMAIN_RENDERERS[domain_name](command, result, terminal_width)
    elif command == "lectures.list":
        lines = _lectures(result, terminal_width)
    elif command == "courses.list":
        lines = _courses(result, terminal_width)
    elif command in {"sync", "sync.lectures"}:
        lines = _sync(result, terminal_width)
    elif command == "status":
        missing_all = (
            envelope.get("status") == "user-action"
            and any(
                isinstance(error, dict) and error.get("code") == "catalog-missing"
                for error in envelope.get("errors", [])
            )
            and not result
        )
        if missing_all:
            root = data_dir()
            paths = (
                catalog_path(root),
                *(domain_catalog_path(domain, root) for domain in ("assignments", "notices", "materials")),
            )
            absent = not any(path.exists() for path in paths)
            lines = [
                "No coursework has been synced yet. Next: campusctl sync"
                if absent
                else "No readable local catalog is available. Next: campusctl sync"
            ]
        else:
            lines = _status(result, terminal_width)
    elif command == "lectures.play":
        lines = _play(result, terminal_width)
    elif command == "doctor":
        doctor_lines, suppress_catalog_error = _doctor(result, terminal_width, envelope.get("errors"))
        lines = [_color_marker(line, color) for line in doctor_lines]
    elif command == "usage":
        lines = _command_hint("To see commands:", "campusctl --help", terminal_width)
    else:
        lines = [wrapped for line in _simple(command, result) for wrapped in _wrap_simple(line, terminal_width)]

    if command in {"lectures.list", "assignments.list", "notices.list", "materials.list"}:
        lines.extend(_cache_advice(result, terminal_width))
    error_values = envelope.get("errors")
    if command == "status" and missing_all:
        error_values = []
    if isinstance(error_values, list) and command.split(".")[0] in ("lectures", "assignments", "notices", "materials"):
        domain = command.split(".")[0]
        error_values = [
            {**error, "domain": domain} if isinstance(error, dict) and error.get("code") == "catalog-missing" else error
            for error in error_values
        ]
    if command == "doctor" and suppress_catalog_error:
        error_values = []
    errors = _errors(error_values, terminal_width, color)
    if lines and errors:
        lines.append("")
    lines.extend(errors)
    for line in lines:
        stream.write(line.rstrip() + "\n")
