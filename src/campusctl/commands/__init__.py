from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from campusctl.providers.cnu.ui_policy import UiRequestPolicy

_HOOKS = ("register", "dispatch", "render", "sync")
_EXPORTS = (*_HOOKS, "CAPABILITY")


class DomainDiscoveryError(RuntimeError):
    """A domain command module is incomplete or has an invalid capability."""

    def __init__(self) -> None:
        super().__init__("A domain command module is incomplete or invalid.")


def _nonempty_strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and bool(item.strip()) for item in value)


def _valid_capability(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"commands", "policy"}:
        return False
    commands = value["commands"]
    policy = value["policy"]
    if not _nonempty_strings(commands) and commands != []:
        return False
    if len(set(commands)) != len(commands):
        return False
    if not UiRequestPolicy.validate_reviewed_config(policy):
        return False
    if policy["approved"]:
        parsed_policy = UiRequestPolicy.from_reviewed_config(policy)
        if not parsed_policy.approved or not parsed_policy.origins or not parsed_policy.routes:
            return False
    return True


def discover_domain_modules() -> dict[str, ModuleType]:
    """Import and validate sorted command modules, returning only approved domains."""
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
        if capability["policy"]["approved"] is False:
            continue
        if not all(callable(getattr(module, hook)) for hook in _HOOKS):
            raise DomainDiscoveryError()
        modules[name] = module
    return modules
