"""Records shared by command modules and their composition caller.

A command module exposes `register(subparsers)` and `dispatch(args, bindings, context)`. It may
also expose `render(command, result, width)`, `CAPABILITY`, `profile_scope(args)` and
`command_key(args)`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

from campusctl.envelope import CampusError, error_item
from campusctl.profiling import SpanRecorder

Renderer = Callable[[str, dict[str, Any], int], list[str]]


@dataclass(frozen=True, slots=True)
class CommandBindings:
    """Provider callables and capability data that composition binds once per invocation."""

    capabilities: dict[str, list[str]]
    discover_courses: Callable[[Any, dict[str, Any]], Awaitable[list[dict[str, Any]]]]
    play_lectures: Callable[..., Awaitable[Any]]
    validate_requested_lectures: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Process inputs that local commands used to read from argparse.Namespace."""

    output_mode: str
    interactive: bool
    profile: SpanRecorder | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One local-command result. `errors` are already JSON objects in envelope order.

    `publish` runs only after successful human rendering and stdout flush.
    """

    result: Any
    status: str = "ok"
    errors: tuple[dict[str, Any], ...] = ()
    publish: Callable[[], None] | None = None


def command_result(
    result: Any = None,
    error: CampusError | list[CampusError] | None = None,
    *,
    status: str | None = None,
    errors: Sequence[Mapping[str, Any]] | None = None,
    publish: Callable[[], None] | None = None,
) -> CommandResult:
    """Convert a workflow `(result, error)` into one JSON-ready result.

    Prebuilt error objects, such as status rows that also carry `domain`, pass through
    `errors` unchanged. CampusError values are converted here, once, via `error_item`.
    """
    if errors is not None:
        return CommandResult(result, status or "ok", tuple(errors), publish)
    if isinstance(error, list):
        return CommandResult(result, "partial", tuple(error_item(item) for item in error), publish)
    if error is not None:
        return CommandResult(result, error.status, (error_item(error),), publish)
    return CommandResult(result, status or "ok", (), publish)


def unchanged_bare_failure() -> NoReturn:
    """Keep the historical unpack failure for bare courses/lectures.

    That path is an internal TypeError today. Changing it would change the error bytes.
    """
    raise TypeError("cannot unpack non-iterable NoneType object")
