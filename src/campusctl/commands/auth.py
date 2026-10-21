"""The auth command family."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from campusctl.cli_args import EnvelopeArgumentParser, _with_json
from campusctl.command_types import CommandBindings
from campusctl.config import load_config
from campusctl.credentials import helper_status, keyring_status, prompt_and_store
from campusctl.envelope import CampusError, UsageError


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    auth = subparsers.add_parser("auth", help="save or check credentials")
    _with_json(auth)
    auth_commands = auth.add_subparsers(dest="auth_command", parser_class=EnvelopeArgumentParser)
    auth_set = auth_commands.add_parser("set", help="save a keyring password")
    _with_json(auth_set)
    auth_status = auth_commands.add_parser("status", help="inspect credential status")
    _with_json(auth_status)
    auth_status.add_argument("--check", action="store_true", help="run the configured helper and check its response")


def dispatch(args: argparse.Namespace, bindings: CommandBindings) -> tuple[Any, CampusError | None]:
    del bindings
    if args.auth_command is None:
        raise UsageError("an auth command is required")
    config = load_config()
    if args.auth_command == "set":
        prompt_and_store(config, stdin_isatty=sys.stdin.isatty())
        return {"provider": "keyring", "configured": True}, None
    provider = config.get("credentials", {}).get("provider", "keyring")
    if provider == "command":
        backend, configured, check_status = helper_status(config, check=args.check)
        credentials = {"provider": "command", "helper": backend, "configured": configured}
        if args.check:
            credentials["check"] = check_status
            if check_status == "failed":
                return credentials, CampusError(
                    "credential-helper-failed",
                    "The credential helper failed its status check.",
                    "Check the configured helper and account, then retry.",
                    "error",
                )
    else:
        try:
            backend, configured = keyring_status(config)
        except CampusError as error:
            return {"provider": "keyring", "configured": None}, error
        credentials = {"provider": "keyring", "backend": backend, "configured": configured}
        if args.check and not configured:
            return credentials, CampusError(
                "credentials-not-configured",
                "No saved credentials are available for this account.",
                "Run 'campusctl auth set' to save credentials.",
                "user-action",
            )
    return credentials, None
