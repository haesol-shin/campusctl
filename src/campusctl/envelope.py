from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TextIO

from campusctl import __version__


class Status(StrEnum):
    """Envelope statuses. Members equal their strings, so JSON and plain-string callers are unchanged."""

    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"
    USER_ACTION = "user-action"
    BUSY = "busy"


EXIT_CODES = {Status.OK: 0, Status.PARTIAL: 1, Status.ERROR: 1, Status.USER_ACTION: 2, Status.BUSY: 75}
# What one failure can carry; ok and partial describe a whole result.
FAILURE_STATUSES = frozenset({Status.USER_ACTION, Status.ERROR, Status.BUSY})


@dataclass(eq=False)
class CampusError(Exception):
    code: str
    message: str
    remediation: str | None = None
    status: str = Status.USER_ACTION

    def __post_init__(self) -> None:
        if self.status not in FAILURE_STATUSES:
            raise ValueError("Invalid CampusError status")
        Exception.__init__(self, self.message)


class UsageError(CampusError):
    def __init__(self, message: str) -> None:
        super().__init__("usage-error", message, "Run 'campusctl --help' for usage.", "user-action")


def resolve_status(committed: bool, errors: Sequence[CampusError]) -> str:
    """Status of a workflow result: partial when it committed something despite errors, else the first error's."""
    if not errors:
        return Status.OK
    return Status.PARTIAL if committed else errors[0].status


def make_envelope(
    *, status: str = Status.OK, result: Any = None, errors: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tool": "campusctl",
        "tool_version": __version__,
        "status": status,
        "result": {} if result is None else result,
        "errors": [] if errors is None else errors,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def error_item(error: CampusError) -> dict[str, str | None]:
    return {"code": error.code, "message": error.message, "remediation": error.remediation}


def prepare_stdout_for_text() -> None:
    """Ensure human output can encode UTF-8 on Windows consoles."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")


def write_json(value: Any, *, stream: TextIO | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    if stream is not None:
        stream.write(text)
        return
    output = sys.stdout
    binary = getattr(output, "buffer", None)
    if binary is not None:
        binary.write(text.encode("utf-8"))
        return
    reconfigure = getattr(output, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    output.write(text)


def emit(
    *,
    result: Any = None,
    error: CampusError | None = None,
    status: str | None = None,
    stream: TextIO | None = None,
) -> int:
    if error is not None:
        envelope_status = error.status
        errors = [error_item(error)]
    else:
        envelope_status = status or Status.OK
        errors = []
    envelope = make_envelope(status=envelope_status, result=result, errors=errors)
    write_json(envelope, stream=stream)
    return EXIT_CODES[envelope_status]
