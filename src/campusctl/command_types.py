"""Records shared by command modules and their composition caller.

A command module exposes `register(subparsers)` and `dispatch(args, bindings)`. It may also expose
`render(command, result, width)`, `CAPABILITY`, `profile_scope(args)` and `command_key(args)`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

Renderer = Callable[[str, dict[str, Any], int], list[str]]


@dataclass(frozen=True, slots=True)
class CommandBindings:
    """Provider callables and capability data that composition binds once per invocation."""

    capabilities: dict[str, list[str]]
    discover_courses: Callable[[Any, dict[str, Any]], Awaitable[list[dict[str, Any]]]]
    play_lectures: Callable[..., Awaitable[Any]]
    validate_requested_lectures: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Envelope status/errors and a publication to run after successful human output.

    `status` None means the standard `(result, error)` envelope rules apply.
    """

    result: Any
    status: str | None = None
    errors: tuple[dict[str, Any], ...] = ()
    publish: Callable[[], None] | None = None
