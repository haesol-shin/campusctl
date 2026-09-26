from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO

from campusctl import __version__

EXIT_CODES = {"ok": 0, "partial": 1, "error": 1, "user-action": 2, "busy": 75}


@dataclass(eq=False)
class CampusError(Exception):
    code: str
    message: str
    remediation: str | None = None
    status: str = "user-action"

    def __post_init__(self) -> None:
        if self.status not in {"user-action", "error", "busy"}:
            raise ValueError("Invalid CampusError status")
        Exception.__init__(self, self.message)


class UsageError(CampusError):
    def __init__(self, message: str) -> None:
        super().__init__("usage-error", message, "Run 'campusctl --help' for usage.", "user-action")


def make_envelope(
    *, status: str = "ok", result: Any = None, errors: list[dict[str, Any]] | None = None
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
        envelope_status = status or "ok"
        errors = []
    envelope = make_envelope(status=envelope_status, result=result, errors=errors)
    write_json(envelope, stream=stream)
    return EXIT_CODES[envelope_status]
