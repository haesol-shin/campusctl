from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.providers.cnu.ui_policy import (
    UiRequestDenied,
    UiRequestDiagnostics,
    UiRequestPolicy,
    install_ui_request_interceptor,
)


def policy(origin: str, operation: str) -> UiRequestPolicy:
    config = {
        "approved": True,
        "read_only_evidence": "synthetic fixture",
        "origins": [origin],
        "routes": [{"origin": origin, "path": "/api/v1/task/stdList", "operation": operation, "methods": ["POST"]}],
        "suppress": [
            {
                "name": "external-telemetry-localhost",
                "origin": "http://localhost:3000",
                "path_template": "/v1/events",
                "operation": operation,
                "methods": ["POST"],
                "reason": "telemetry",
            },
        ],
        "allowed_media": [],
        "max_bytes": None,
    }
    result = UiRequestPolicy.from_reviewed_config(config)
    assert result.approved
    return result


class Request:
    def __init__(
        self,
        url: str,
        frame: object,
        referer: str,
        *,
        method: str = "POST",
        resource_type: str = "xhr",
        extras: dict[str, str] | None = None,
    ) -> None:
        self.url, self.frame, self.method, self.resource_type = url, frame, method, resource_type
        self.headers = {"Referer": referer, **(extras or {})}
        self.redirected_from: Any = None

    async def all_headers(self) -> dict[str, str]:
        return self.headers


class Route:
    def __init__(self, request: Request, port: int) -> None:
        self.request, self.port = request, port
        self.action: str | None = None

    async def abort(self) -> None:
        self.action = "abort"

    async def continue_(self) -> None:
        self.action = "continue"
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"GET /fixture HTTP/1.1\r\nHost: fixture.invalid\r\n\r\n")
        await writer.drain()
        await reader.read()
        writer.close()
        await writer.wait_closed()


class Target:
    def __init__(self) -> None:
        self.handler: Any = None
        self.installs = 0

    async def route(self, path: str, handler: Any) -> None:
        assert path == "**/*" and self.handler is None
        self.installs += 1
        self.handler = handler

    async def unroute(self, path: str, handler: Any) -> None:
        assert path == "**/*" and self.handler == handler
        self.handler = None


def test_guard_aborts_denied_requests_before_local_server() -> None:
    async def scenario() -> None:
        arrivals = 0

        async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal arrivals
            arrivals += 1
            await reader.read(4096)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(receive, "127.0.0.1", 0)
        origin = "https://lms.example.invalid"
        target = Target()
        frame = SimpleNamespace(url=origin + "/std/course")
        diagnostics = UiRequestDiagnostics()
        guard = await install_ui_request_interceptor(
            target,
            policy(origin, "assignments.sync"),
            operation="assignments.sync",
            diagnostics=diagnostics,
        )

        async def send(request: Request) -> str:
            route = Route(request, server.sockets[0].getsockname()[1])
            await target.handler(route)
            assert route.action is not None
            return route.action

        try:
            allowed = Request(origin + "/api/v1/task/stdList", frame, frame.url)
            assert await send(allowed) == "continue"
            assert arrivals == 1
            assert (
                await send(Request("http://localhost:3000/v1/events", frame, frame.url, resource_type="fetch"))
                == "abort"
            )
            assert diagnostics.suppressed_reasons == {"telemetry": 1}
            guard.raise_if_denied()
            assert arrivals == 1
            assert (
                await send(Request(origin + "/api/v1/task/stdList", frame, frame.url, extras={"Range": "bytes=0-1"}))
                == "abort"
            )
            assert arrivals == 1
            with pytest.raises(UiRequestDenied) as caught:
                guard.raise_if_denied()
            assert caught.value.reason_code == "range"
            assert target.installs == 1
        finally:
            await guard.close()
            assert target.handler is None
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["redirect", "route"])
def test_guard_denials_are_run_wide(fault: str) -> None:
    async def scenario() -> None:
        origin = "https://lms.example.invalid"
        target = Target()
        frame = SimpleNamespace(url=origin + "/std/course")
        guard = await install_ui_request_interceptor(
            target,
            policy(origin, "assignments.sync"),
            operation="assignments.sync",
            diagnostics=UiRequestDiagnostics(),
        )
        request = Request(
            origin + ("/not-reviewed" if fault == "route" else "/api/v1/task/stdList"),
            frame,
            frame.url,
        )
        if fault == "redirect":
            request.redirected_from = SimpleNamespace(url=origin + "/std/course")
        route = Route(request, 0)
        try:
            await target.handler(route)
            assert route.action == "abort"
            with pytest.raises(UiRequestDenied) as caught:
                guard.raise_if_denied()
            assert caught.value.reason_code == fault
        finally:
            await guard.close()

    asyncio.run(scenario())
