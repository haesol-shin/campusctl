from __future__ import annotations

import io
from typing import Any

from campusctl import cli, composition
from campusctl.presentation import render_human


def test_registration_order_is_parser_order() -> None:
    usage = cli.build_parser().format_usage()

    assert "{config,doctor,auth,setup,sync,status,courses,lectures,assignments,materials,notices}" in usage


def test_capabilities_keep_published_key_order_and_values() -> None:
    assert composition.CAPABILITIES == {
        "sync": ["lectures", "assignments", "notices", "materials"],
        "lectures": ["list", "play"],
        "status": ["local"],
        "assignments": ["list", "fetch"],
        "materials": ["list", "download"],
        "notices": ["list", "fetch"],
    }
    assert list(composition.CAPABILITIES) == ["sync", "lectures", "status", "assignments", "materials", "notices"]


def test_injected_renderer_handles_domain_and_sync_commands() -> None:
    def sample(command: str, result: dict[str, Any], width: int) -> list[str]:
        return [f"sample:{command}:{result['value']}:{width}"]

    for command in ("sample.list", "sync.sample"):
        stream = io.StringIO()
        render_human(
            command,
            {"status": "ok", "result": {"value": "ready"}, "errors": []},
            stream,
            renderers={"sample": sample},
            width=72,
        )
        assert stream.getvalue() == f"sample:{command}:ready:72\n"
