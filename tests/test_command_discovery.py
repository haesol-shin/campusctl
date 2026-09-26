from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from campusctl import cli, commands
from campusctl.commands import DomainDiscoveryError, discover_domain_modules
from campusctl.envelope import UsageError

_APPROVED_CAPABILITY = """
CAPABILITY = {
    "commands": ["list"],
    "policy": {
        "approved": True,
        "read_only_evidence": "reviewed-artifact",
        "origins": ["https://lms.example"],
        "routes": [{"origin": "https://lms.example", "path": "/course", "operation": "sample.sync", "methods": ["GET"]}],
        "allowed_media": [],
        "max_bytes": None,
    },
}
"""


def _module_source(*, capability: str = _APPROVED_CAPABILITY, hooks: str | None = None) -> str:
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
    return f"{capability}\n{hook_source}"


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


def test_complete_unapproved_module_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    disabled = """
CAPABILITY = {
    "commands": ["list"],
    "policy": {
        "approved": False,
        "read_only_evidence": None,
        "origins": [],
        "routes": [],
        "allowed_media": [],
        "max_bytes": None,
        "notes": "Not reviewed",
    },
}
"""
    _install_modules(monkeypatch, tmp_path, {"sample": _module_source(capability=disabled)})

    assert discover_domain_modules() == {}


@pytest.mark.parametrize(
    "policy",
    [
        {"approved": False},
        {
            "approved": True,
            "read_only_evidence": "synthetic",
            "origins": ["https://lms.example.invalid"],
            "routes": [
                {
                    "origin": "https://lms.example.invalid",
                    "path": "/course",
                    "operation": "sample.sync",
                    "methods": ["GET"],
                }
            ],
            "allowed_media": [],
        },
    ],
)
def test_incomplete_policy_schema_raises_even_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict[str, object],
) -> None:
    source = _module_source(capability=f"CAPABILITY = {{'commands': ['list'], 'policy': {policy!r}}}")
    _install_modules(monkeypatch, tmp_path, {"sample": source})
    with pytest.raises(DomainDiscoveryError):
        discover_domain_modules()


def test_partial_and_malformed_modules_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_modules(monkeypatch, tmp_path, {"sample": "def register(_subparsers):\n    pass\n"})

    with pytest.raises(DomainDiscoveryError):
        discover_domain_modules()


def test_malformed_capability_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    malformed = 'CAPABILITY = {"commands": ["list"], "policy": {"approved": "yes"}}\n'
    _install_modules(monkeypatch, tmp_path, {"sample": _module_source(capability=malformed)})

    with pytest.raises(DomainDiscoveryError):
        discover_domain_modules()


def test_approved_module_with_invalid_origin_pin_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    invalid_origin = """
CAPABILITY = {
    "commands": ["list"],
    "policy": {
        "approved": True,
        "read_only_evidence": "reviewed-artifact",
        "origins": ["not-a-url"],
        "routes": [{"origin": "not-a-url", "path": "/course", "operation": "sample.sync", "methods": ["GET"]}],
        "allowed_media": [],
        "max_bytes": None,
    },
}
"""
    _install_modules(monkeypatch, tmp_path, {"sample": _module_source(capability=invalid_origin)})

    with pytest.raises(DomainDiscoveryError):
        discover_domain_modules()


@pytest.mark.parametrize(
    "overrides",
    [{"origins": [None]}, {"static_resource_types": [[]]}],
    ids=["null-origin", "unhashable-static-type"],
)
def test_disabled_policy_with_malformed_entries_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, overrides: dict[str, object]
) -> None:
    policy = {
        "approved": False,
        "read_only_evidence": None,
        "origins": [],
        "routes": [],
        "allowed_media": [],
        "max_bytes": None,
        **overrides,
    }
    capability = f"CAPABILITY = {{'commands': ['list'], 'policy': {policy!r}}}\n"
    _install_modules(monkeypatch, tmp_path, {"sample": _module_source(capability=capability)})

    with pytest.raises(DomainDiscoveryError):
        discover_domain_modules()


def test_extended_reviewed_policy_registers_and_malformed_new_key_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    origin = "https://dcs-learning.cnu.ac.kr"
    selected_origin = "https://dcs-lcms.cnu.ac.kr"
    policy = {
        "approved": True,
        "read_only_evidence": "synthetic-reviewed-route",
        "origins": [origin, selected_origin],
        "routes": [
            {
                "origin": origin,
                "path": "/properties/messages.properties",
                "operation": "sample.sync",
                "methods": ["GET"],
                "query": {"_": "cachebuster"},
            },
        ],
        "suppress": [
            {
                "name": "panopto-script",
                "origin": origin,
                "path_template": "/js/common/panopto-{hash}.js",
                "operation": "sample.sync",
                "methods": ["GET"],
                "reason": "media-integration",
            },
        ],
        "static_asset_origins": [origin],
        "static_resource_types": ["script", "stylesheet", "font", "image"],
        "selected_file_routes": [
            {
                "origin": selected_origin,
                "path_template": "/upload/{storage-id}/{encoded-filename}",
                "operation": "materials.download",
                "methods": ["GET"],
            },
        ],
        "allowed_media": [],
        "max_bytes": None,
    }
    capability = {"commands": ["list"], "policy": policy}
    names = _install_modules(
        monkeypatch, tmp_path, {"sample": _module_source(capability=f"CAPABILITY = {capability!r}")}
    )
    try:
        registered = discover_domain_modules()["sample"]
        registered.CAPABILITY["policy"]["static_resource_types"] = ["media"]
        with pytest.raises(DomainDiscoveryError):
            discover_domain_modules()
    finally:
        for name in names:
            sys.modules.pop(name, None)


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
