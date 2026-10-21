"""Replace load_config in every module that binds it for command dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from campusctl import cli
from campusctl.commands import auth, course_inputs, lectures, setup, sync


def patch_load_config(monkeypatch: pytest.MonkeyPatch, loader: Callable[..., Any]) -> None:
    for module in (cli, auth, course_inputs, lectures, setup, sync):
        monkeypatch.setattr(module, "load_config", loader)
