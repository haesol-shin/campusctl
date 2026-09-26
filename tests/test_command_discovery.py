from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from campusctl import cli, commands
from campusctl.commands import DomainDiscoveryError, discover_domain_modules
from campusctl.envelope import UsageError


def _module_source(*, hooks: str | None = None) -> str:
    hook_source = (
        hooks
        or """
def register(subparsers):
    domain = subparsers.add_parser("sample")
    leaves = domain.add_subparsers(dest="sample_command")
    leaves.add_parser("list")

def dispatch(args):
    return {"command": args.sample_command}, None

async def sync(config, root, course_id, *, headless=False):
    return {"headless": headless}, []

def render(command, result, width):
    return [f"sample-render:{command}:{result['value']}:{width}"]
"""
    )
    return "CAPABILITY = {'commands': ['list']}\n" + hook_source


def _install_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, modules: dict[str, str]) -> list[str]:
    monkeypatch.setattr(commands, "__path__", [str(tmp_path)])
    names = []
    for name, source in modules.items():
        (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
        full_name = f"{commands.__name__}.{name}"
        sys.modules.pop(full_name, None)
        names.append(full_name)
    importlib.invalidate_caches()
    return names


def test_helper_module_without_domain_exports_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_modules(monkeypatch, tmp_path, {"_helpers": "VALUE = 1\n"})

    assert discover_domain_modules() == {}


def test_partial_domain_module_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_modules(monkeypatch, tmp_path, {"sample": "def register(_subparsers):\n    pass\n"})

    with pytest.raises(DomainDiscoveryError):
        discover_domain_modules()


@pytest.mark.parametrize("commands_value", [None, [], ["list", "list"], [123]])
def test_invalid_command_capability_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, commands_value: object
) -> None:
    source = _module_source().replace("['list']", repr(commands_value), 1)
    _install_modules(monkeypatch, tmp_path, {"sample": source})
    with pytest.raises(DomainDiscoveryError):
        discover_domain_modules()


def test_no_registered_domains_preserve_lecture_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import commands

    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(commands, "discover_domain_modules", lambda: {})
            importlib.reload(cli)
            assert cli._DOMAIN_MODULES == {}
            assert cli.CAPABILITIES == {
                "sync": ["lectures", "assignments", "notices", "materials"],
                "lectures": ["list", "play"],
                "status": ["local"],
            }
            with pytest.raises(UsageError):
                cli.build_parser().parse_args(["assignments", "list"])
    finally:
        importlib.reload(cli)


def test_discovered_domain_renderer_routing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from campusctl import presentation

    modules = {"sample": _module_source()}
    names: list[str] = []
    try:
        with monkeypatch.context() as patcher:
            names = _install_modules(patcher, tmp_path, modules)
            importlib.reload(presentation)
            renderers = presentation._DOMAIN_RENDERERS
            assert "sample" in renderers
            for command in ("sample.list", "sync.sample"):
                output = []
                presentation.render_human(
                    command,
                    {"status": "ok", "result": {"value": "ready"}, "errors": []},
                    _WriteStream(output),
                    width=72,
                )
                assert output == [f"sample-render:{command}:ready:72\n"]
    finally:
        for name in names:
            sys.modules.pop(name, None)
        importlib.reload(presentation)


class _WriteStream:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def write(self, value: str) -> int:
        self.lines.append(value)
        return len(value)
