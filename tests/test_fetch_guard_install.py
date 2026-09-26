"""The fetch commands must install and honor the reviewed request guard before touching detail pages."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.commands import assignments, notices
from campusctl.envelope import CampusError

ROW = {"entity_id": "cnu_notice:course-1:item-1", "course": {"id": "course-1", "label": "Course 1"}}


class Guard:
    def __init__(self, denied: bool) -> None:
        self.denied = denied

    def raise_if_denied(self) -> None:
        if self.denied:
            raise CampusError("policy-blocked", "blocked")


def _patch_session(monkeypatch: pytest.MonkeyPatch, guard: Guard, calls: list[str]) -> None:
    @contextlib.asynccontextmanager
    async def open_session(*args: Any, **kwargs: Any):
        yield SimpleNamespace(page=object())

    async def ensure_logged_in(*args: Any) -> None:
        calls.append("login")

    async def install(page: object, policy: Any, *, operation: str, diagnostics: Any) -> Guard:
        calls.append(f"guard:{operation}")
        return guard

    async def capture(*args: Any, **kwargs: Any) -> str:
        calls.append("capture")
        return "snapshot"

    async def build(page: object, snapshot: str, **kwargs: Any) -> dict[str, Any]:
        calls.append("build")
        assert kwargs["interceptor"] is guard
        return {"status": "ok"}

    monkeypatch.setattr("campusctl.browser.open_session", open_session)
    monkeypatch.setattr("campusctl.providers.cnu.login.ensure_logged_in", ensure_logged_in)
    monkeypatch.setattr("campusctl.providers.cnu.ui_policy.install_ui_request_interceptor", install)
    monkeypatch.setattr("campusctl.providers.cnu.notice_detail.capture_notice_detail", capture)
    monkeypatch.setattr("campusctl.providers.cnu.assignment_detail.capture_assignment_detail", capture)
    monkeypatch.setattr("campusctl.source_package.build_source_package", build)


@pytest.mark.parametrize(
    ("fetch", "operation"),
    [(notices._fetch_notice, "notices.fetch"), (assignments._fetch_assignment, "assignments.fetch")],
)
def test_fetch_installs_guard_before_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetch: Any, operation: str
) -> None:
    calls: list[str] = []
    _patch_session(monkeypatch, Guard(denied=False), calls)
    assert asyncio.run(fetch({}, tmp_path, ROW)) == {"status": "ok"}
    assert calls == ["login", f"guard:{operation}", "capture", "build"]


@pytest.mark.parametrize("fetch", [notices._fetch_notice, assignments._fetch_assignment])
def test_fetch_denial_after_detail_stops_before_packaging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetch: Any
) -> None:
    calls: list[str] = []
    _patch_session(monkeypatch, Guard(denied=True), calls)
    with pytest.raises(CampusError) as exc_info:
        asyncio.run(fetch({}, tmp_path, ROW))
    assert exc_info.value.code == "policy-blocked"
    assert "build" not in calls
