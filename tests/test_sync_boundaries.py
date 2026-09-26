from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.providers.cnu.course_context import CourseSelection
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
        "routes": [
            {"origin": origin, "path": path, "operation": operation, "methods": [method]}
            for path, method in (
                ("/std/myLecture", "GET"),
                ("/std/lecture", "GET"),
                ("/api/v1/course/addSessionCourseInfo", "POST"),
                ("/std/course", "GET"),
                ("/std/task", "GET"),
                ("/api/v1/task/stdList", "POST"),
            )
        ],
        "allowed_media": [],
        "max_bytes": None,
    }
    result = UiRequestPolicy.from_reviewed_config(config)
    assert result.approved
    return result


def selection(**changes: Any) -> CourseSelection:
    fields = {
        "course_id": "synthetic-course-1",
        "roster_course_id": "synthetic-course-1",
        "topbar_course_id": "synthetic-course-1",
        "ordinal": 1,
        "epoch": 1,
        "response_identity": 2,
        "document_identity": 3,
        "response_status": 200,
        "response_body": {
            "header": {"code": 200},
            "body": {"result": "Y", "data": {"course_id": "synthetic-course-1"}},
        },
        "response_count": 1,
        "document_committed": True,
    }
    fields.update(changes)
    return CourseSelection.from_response(**fields)


@pytest.mark.parametrize(
    "changes",
    [
        {"roster_course_id": "other"},
        {"topbar_course_id": "other"},
        {"response_body": {"header": {"code": 200}, "body": {"result": "Y", "data": {"course_id": "other"}}}},
        {"response_count": 2},
        {"pending": True},
        {"document_committed": False},
        {"response_identity": 3},
    ],
)
def test_selection_rejects_unbound_and_incomplete_response(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        selection(**changes)


def test_selection_contains_no_payload_and_is_immutable() -> None:
    selected = selection()
    assert selected.response_identity == 2 and selected.document_identity == 3
    with pytest.raises(FrozenInstanceError):
        selected.course_id = "other"  # type: ignore[misc]


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
        self.wait: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None

    async def all_headers(self) -> dict[str, str]:
        if self.entered is not None:
            self.entered.set()
        if self.wait is not None:
            await self.wait.wait()
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


def test_epoch_transition_aborts_old_and_bad_requests_before_local_server() -> None:
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
        port = server.sockets[0].getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        target = Target()
        frame = SimpleNamespace(url=origin + "/std/course")
        old_url = origin + "/std/course"
        new_url = origin + "/std/task"
        guard = await install_ui_request_interceptor(
            target,
            policy(origin, "assignments.sync"),
            operation="assignments.sync",
            diagnostics=UiRequestDiagnostics(),
            require_selection=False,
        )
        selected = selection()
        guard.bind_selection(selected, frame=frame, document_url=old_url)

        async def send(request: Request) -> str:
            route = Route(request, port)
            await target.handler(route)
            assert route.action is not None
            return route.action

        try:
            good = Request(origin + "/api/v1/task/stdList", frame, old_url)
            assert await send(good) == "continue"
            assert arrivals == 1
            late = Request(origin + "/api/v1/task/stdList", frame, old_url)
            late.wait, late.entered = asyncio.Event(), asyncio.Event()
            pending = asyncio.create_task(send(late))
            await late.entered.wait()
            guard.quarantine()
            with pytest.raises(UiRequestDenied):
                guard.activate(
                    policy(origin, "notices.sync"),
                    operation="notices.sync",
                    selection=selected,
                    frame=frame,
                    document_url=old_url,
                    navigation_path="/std/task",
                    settled=False,
                )
            guard.activate(
                policy(origin, "notices.sync"),
                operation="notices.sync",
                selection=selected,
                frame=frame,
                document_url=old_url,
                navigation_path="/std/task",
                settled=True,
            )
            assert await send(Request(new_url, frame, old_url, method="GET", resource_type="document")) == "continue"
            frame.url = new_url
            guard.bind_document(frame=frame, document_url=new_url, selection=selected)
            assert await send(Request(origin + "/api/v1/task/stdList", frame, new_url)) == "continue"
            assert arrivals == 3
            assert await send(Request(origin + "/api/v1/task/stdList", frame, old_url)) == "abort"
            assert arrivals == 3
            late.wait.set()
            assert await pending == "abort"
            with pytest.raises(UiRequestDenied):
                guard.raise_if_denied()
            assert arrivals == 3
            assert target.installs == 1
        finally:
            await guard.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["referer", "frame", "range", "redirect", "precommit", "pending"])
def test_bound_request_denials_are_run_wide(fault: str) -> None:
    async def scenario() -> None:
        origin = "https://lms.example.invalid"
        target = Target()
        frame = object()
        guard = await install_ui_request_interceptor(
            target,
            policy(origin, "assignments.sync"),
            operation="assignments.sync",
            diagnostics=UiRequestDiagnostics(),
            require_selection=fault == "precommit",
        )
        if fault != "precommit":
            guard.bind_selection(selection(), frame=frame, document_url=origin + "/std/course")
        request = Request(
            origin + "/api/v1/task/stdList",
            object() if fault == "frame" else frame,
            origin + ("/std/task" if fault == "referer" else "/std/course"),
            extras={"Range": "bytes=0-1"} if fault == "range" else None,
        )
        if fault == "redirect":
            request.redirected_from = SimpleNamespace(url=origin + "/std/course")
        if fault == "pending":
            guard.quarantine()
        route = Route(request, 0)
        try:
            await target.handler(route)
            assert route.action == "abort"
            with pytest.raises(UiRequestDenied):
                guard.raise_if_denied()
        finally:
            await guard.close()

    asyncio.run(scenario())


def test_preselection_window_and_duplicate_are_bound_before_local_server() -> None:
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
        origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        roster_url = origin + "/std/myLecture"
        frame = SimpleNamespace(url=roster_url)
        target = Target()
        guard = await install_ui_request_interceptor(
            target,
            policy(origin, "assignments.sync"),
            operation="assignments.sync",
            diagnostics=UiRequestDiagnostics(),
            require_selection=True,
        )

        async def send(request: Request) -> str:
            route = Route(request, server.sockets[0].getsockname()[1])
            await target.handler(route)
            assert route.action is not None
            return route.action

        try:
            with pytest.raises(UiRequestDenied):
                guard.bind_selection(selection(), frame=frame, document_url=origin + "/std/lecture")
            guard.arm_selection(frame=frame, document_url=roster_url)
            assert await send(Request(origin + "/api/v1/course/addSessionCourseInfo", frame, roster_url)) == "continue"
            assert (
                await send(Request(origin + "/std/lecture", frame, roster_url, method="GET", resource_type="document"))
                == "continue"
            )
            frame.url = origin + "/std/lecture"
            first = selection()
            guard.bind_selection(first, frame=frame, document_url=origin + "/std/lecture")
            assert guard.epoch.selection_epoch == first.epoch
            guard.quarantine()
            frame.url = roster_url
            second_epoch = guard.arm_selection(frame=frame, document_url=roster_url)
            assert second_epoch.selection_epoch == first.epoch + 1
            with pytest.raises(UiRequestDenied):
                guard.bind_selection(first, frame=frame, document_url=origin + "/std/lecture")
            assert await send(Request(origin + "/api/v1/course/addSessionCourseInfo", frame, roster_url)) == "continue"
            assert await send(Request(origin + "/api/v1/course/addSessionCourseInfo", frame, roster_url)) == "abort"
            assert arrivals == 3
            with pytest.raises(UiRequestDenied):
                guard.raise_if_denied()
        finally:
            await guard.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
