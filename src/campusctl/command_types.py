"""Records shared by command modules and their composition caller.

A command module exposes `register(subparsers)` and `dispatch(args, bindings, context)`. It may
also expose `render(command, result, width)` or `render_response(command, result, width, envelope)`,
`CAPABILITY`, `profile_scope(args)` and `command_key(args)`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple, NoReturn, TextIO

from campusctl.envelope import CampusError, Status, error_item
from campusctl.profiling import SpanRecorder

Renderer = Callable[[str, dict[str, Any], int], list[str]]


class Rendered(NamedTuple):
    """Lines of a response renderer and whether they already explain the envelope's errors."""

    lines: list[str]
    hide_errors: bool = False


# Also reads the envelope: doctor and status decide from its status and errors.
ResponseRenderer = Callable[[str, dict[str, Any], int, dict[str, Any]], Rendered]


@dataclass(frozen=True, slots=True)
class Renderers:
    """Human renderers keyed by command family, the part of a command key before the first dot."""

    results: Mapping[str, Renderer]
    responses: Mapping[str, ResponseRenderer] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandBindings:
    """Provider callables and capability data that composition binds once per invocation."""

    capabilities: dict[str, list[str]]
    discover_courses: Callable[[Any, dict[str, Any]], Awaitable[list[dict[str, Any]]]]
    play_lectures: Callable[..., Awaitable[Any]]
    sync_all: Callable[..., Awaitable[Any]]
    validate_requested_lectures: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Process inputs that local commands used to read from argparse.Namespace."""

    output_mode: str
    interactive: bool
    profile: SpanRecorder | None = None
    output: Callable[[str, dict[str, Any], TextIO], None] | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One local-command result. `errors` are already JSON objects in envelope order.

    `publish` runs only after successful human rendering and stdout flush.
    """

    result: Any
    status: str = Status.OK
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
        return CommandResult(result, status or Status.OK, tuple(errors), publish)
    if isinstance(error, list):
        return CommandResult(result, Status.PARTIAL, tuple(error_item(item) for item in error), publish)
    if error is not None:
        return CommandResult(result, error.status, (error_item(error),), publish)
    return CommandResult(result, status or Status.OK, (), publish)


def unchanged_bare_failure() -> NoReturn:
    """Keep the historical unpack failure for bare courses/lectures.

    That path is an internal TypeError today. Changing it would change the error bytes.
    """
    raise TypeError("cannot unpack non-iterable NoneType object")
