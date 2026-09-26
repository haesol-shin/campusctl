from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

_HOOKS = ("register", "dispatch", "render", "sync")
_EXPORTS = (*_HOOKS, "CAPABILITY")


class DomainDiscoveryError(RuntimeError):
    """A domain command module is incomplete or has an invalid capability."""

    def __init__(self) -> None:
        super().__init__("A domain command module is incomplete or invalid.")


def _nonempty_strings(value: object) -> bool:
    return (
        isinstance(value, list) and bool(value) and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _valid_capability(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"commands"}:
        return False
    commands = value["commands"]
    return _nonempty_strings(commands) and len(set(commands)) == len(commands)


def discover_domain_modules() -> dict[str, ModuleType]:
    """Import and validate sorted command modules."""
    names = sorted(item.name for item in pkgutil.iter_modules(__path__))
    modules: dict[str, ModuleType] = {}
    for name in names:
        try:
            module = importlib.import_module(f"{__name__}.{name}")
        except Exception:
            raise DomainDiscoveryError() from None

        present = [export for export in _EXPORTS if export in vars(module)]
        if not present:
            continue
        if len(present) != len(_EXPORTS):
            raise DomainDiscoveryError()

        capability = module.CAPABILITY
        if not _valid_capability(capability):
            raise DomainDiscoveryError()
        if not all(callable(getattr(module, hook)) for hook in _HOOKS):
            raise DomainDiscoveryError()
        modules[name] = module
    return modules
