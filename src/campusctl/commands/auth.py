"""The auth command family."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import CommandBindings, CommandContext, CommandResult, command_result
from campusctl.config import load_config
from campusctl.credentials import helper_status, keyring_status, prompt_and_store
from campusctl.envelope import CampusError, UsageError
from campusctl.text_layout import _text, _wrap_simple


@dataclass(frozen=True, slots=True)
class _AuthCredentials:
    """Fixed auth result. Absent backend, helper and check keys stay omitted."""

    provider: str
    configured: bool | None
    backend: str | None = None
    helper: str | None = None
    check: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"provider": self.provider}
        if self.helper is not None:
            payload["helper"] = self.helper
        elif self.backend is not None:
            payload["backend"] = self.backend
        payload["configured"] = self.configured
        if self.check is not None:
            payload["check"] = self.check
        return payload


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    auth = subparsers.add_parser("auth", help="save or check credentials")
    _with_json(auth)
    auth_commands = auth.add_subparsers(dest="auth_command", parser_class=EnvelopeArgumentParser)
    auth_set = auth_commands.add_parser("set", help="save a keyring password")
    _with_json(auth_set)
    auth_status = auth_commands.add_parser("status", help="inspect credential status")
    _with_json(auth_status)
    auth_status.add_argument("--check", action="store_true", help="run the configured helper and check its response")


def _set(config: dict[str, Any]) -> CommandResult:
    prompt_and_store(config, stdin_isatty=sys.stdin.isatty())
    return command_result(_AuthCredentials("keyring", True).to_json())


def _status(args: argparse.Namespace, config: dict[str, Any]) -> CommandResult:
    provider = config.get("credentials", {}).get("provider", "keyring")
    if provider == "command":
        backend, configured, check_status = helper_status(config, check=args.check)
        credentials = _AuthCredentials(
            "command", configured, helper=backend, check=check_status if args.check else None
        )
        if args.check and check_status == "failed":
            return command_result(
                credentials.to_json(),
                CampusError(
                    "credential-helper-failed",
                    "The credential helper failed its status check.",
                    "Check the configured helper and account, then retry.",
                    "error",
                ),
            )
        return command_result(credentials.to_json())
    try:
        backend, configured = keyring_status(config)
    except CampusError as error:
        return command_result(_AuthCredentials("keyring", None).to_json(), error)
    credentials = _AuthCredentials("keyring", configured, backend=backend)
    if args.check and not configured:
        return command_result(
            credentials.to_json(),
            CampusError(
                "credentials-not-configured",
                "No saved credentials are available for this account.",
                "Run 'campusctl auth set' to save credentials.",
                "user-action",
            ),
        )
    return command_result(credentials.to_json())


def dispatch(args: argparse.Namespace, bindings: CommandBindings, context: CommandContext) -> CommandResult:
    del bindings, context
    if args.auth_command is None:
        raise UsageError("an auth command is required")
    config = load_config()
    if args.auth_command == "set":
        return _set(config)
    return _status(args, config)


def _summary(command: str, result: dict[str, Any]) -> list[str]:
    if command == "auth.set" and "configured" in result:
        configured = result.get("configured")
        if configured is True:
            return ["Password saved to the OS keyring."]
        if configured is False:
            return ["No password was saved."]
        return ["Password-save status is unknown."]
    if command == "auth.status" and any(
        key in result for key in ("provider", "configured", "backend", "helper", "check")
    ):
        provider = _text(result.get("provider"))
        configured = result.get("configured")
        if provider == "command":
            helper = _text(result.get("helper"), "unknown")
            state = (
                "is configured"
                if configured is True
                else "is not configured"
                if configured is False
                else "status is unknown"
            )
            check = (
                result.get("check")
                if result.get("check") in {"ok", "failed"}
                else "unknown"
                if "check" in result
                else "not run"
            )
            return [f"Credential helper {helper} {state}; check: {check}."]
        if provider == "keyring":
            state = "saved" if configured is True else "not saved" if configured is False else "status unknown"
            backend = _text(result.get("backend"), "unknown backend")
            return [f"Credentials are {state} in the OS keyring ({backend})."]
        return ["Credential provider status is unknown."]
    return []


def render(command: str, result: dict[str, Any], width: int) -> list[str]:
    return [wrapped for line in _summary(command, result) for wrapped in _wrap_simple(line, width)]
