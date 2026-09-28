from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from campusctl import cli, config_init, credentials, setup
from campusctl.envelope import CampusError, error_item, make_envelope, write_json


class Stream(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


class BinaryStdout:
    def __init__(self, *, tty: bool) -> None:
        self.buffer = io.BytesIO()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def _normalize_generated_at(value: bytes) -> bytes:
    return re.sub(rb'"generated_at":"[^"]+"', b'"generated_at":"<timestamp>"', value)


def _streams(monkeypatch: pytest.MonkeyPatch, *, stdin_tty: bool, stdout_tty: bool) -> tuple[Stream, Stream]:
    stdin = Stream(tty=stdin_tty)
    stdout = Stream(tty=stdout_tty)
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.delenv("CAMPUSCTL_OUTPUT", raising=False)
    return stdin, stdout


@pytest.mark.parametrize("kind", ["success", "campus-error", "partial"])
def test_json_bytes_match_direct_envelope_writer(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    stdout_tty = kind != "campus-error"
    stdin = Stream(tty=False)
    stdout = BinaryStdout(tty=stdout_tty)
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.delenv("CAMPUSCTL_OUTPUT", raising=False)

    result: dict[str, Any] | None = {"title": "강의"}
    errors: list[CampusError] = []
    if kind == "success":
        argv = ["doctor", "--json"]
        error: CampusError | list[CampusError] | None = None
        status = "ok"
        expected_exit = 0
    elif kind == "campus-error":
        argv = ["doctor"]
        error = CampusError("sample-error", "A safe error.", status="error")
        status = "error"
        expected_exit = 1
    else:
        argv = ["sync", "--json"]
        error = [CampusError("course-sync-failed", "One course failed.")]
        errors = error
        result = {"courses": 1, "lectures": 2, "incomplete": 1, "failed_courses": []}
        status = "partial"
        expected_exit = 1

    monkeypatch.setattr(cli, "_dispatch", lambda _args: (result, error))

    assert cli.main(argv) == expected_exit
    expected_text = io.StringIO()
    write_json(
        make_envelope(
            status=status,
            result=result,
            errors=[error_item(item) for item in errors]
            if errors
            else [error_item(error)]
            if isinstance(error, CampusError)
            else [],
        ),
        stream=expected_text,
    )
    assert _normalize_generated_at(stdout.buffer.getvalue()) == _normalize_generated_at(
        expected_text.getvalue().encode("utf-8")
    )


@pytest.mark.parametrize(
    ("stdout_tty", "output", "argv", "human"),
    [
        (True, None, ["--version"], True),
        (False, None, ["--version"], False),
        (False, "human", ["--version"], True),
        (True, "json", ["--version"], False),
        (True, "invalid", ["--version"], True),
        (False, "invalid", ["--version"], False),
        (True, "human", ["--version", "--json"], False),
    ],
)
def test_output_mode_precedence(
    monkeypatch: pytest.MonkeyPatch,
    stdout_tty: bool,
    output: str | None,
    argv: list[str],
    human: bool,
) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=stdout_tty)
    if output is not None:
        monkeypatch.setenv("CAMPUSCTL_OUTPUT", output)

    assert cli.main(argv) == 0
    assert stdout.getvalue().startswith("campusctl version ") is human
    assert stdout.getvalue().startswith("{") is not human


def test_argparse_error_is_readable_in_human_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=True)

    assert cli.main(["not-a-command"]) == 2
    assert "Invalid command-line usage" in stdout.getvalue()
    assert "campusctl --help" in stdout.getvalue()
    assert not stdout.getvalue().startswith("{")


def test_json_option_abbreviation_is_usage_error_and_never_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr("builtins.input", lambda *_args: pytest.fail("abbreviated option prompted"))

    assert cli.main(["config", "init", "--username", "student-id", "--j"]) == 2
    assert "Invalid command-line usage" in stdout.getvalue()
    assert not (config_dir / "config.toml").exists()


def test_human_stdout_is_prepared_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    events: list[str] = []
    monkeypatch.setattr(cli, "prepare_stdout_for_text", lambda: events.append("prepare"))

    def dispatch(_args: Any) -> tuple[dict[str, str], None]:
        events.append("dispatch")
        return {"title": "ready"}, None

    monkeypatch.setattr(cli, "_dispatch", dispatch)

    assert cli.main(["doctor"]) == 0
    assert events == ["prepare", "dispatch"]


def test_keyboard_interrupt_is_readable_and_returns_130_in_human_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    monkeypatch.setattr(cli, "_dispatch", lambda _args: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert cli.main(["doctor"]) == 130
    assert stdout.getvalue() == "Interrupted.\n"


def test_keyboard_interrupt_propagates_in_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    monkeypatch.setattr(cli, "_dispatch", lambda _args: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        cli.main(["doctor", "--json"])


def test_unexpected_exception_has_no_traceback_in_human_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    monkeypatch.setattr(
        cli,
        "_dispatch",
        lambda _args: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
    )

    assert cli.main(["doctor"]) == 1
    output = stdout.getvalue()
    assert "RuntimeError" in output
    assert "Traceback" not in output
    assert "sensitive detail" not in output


def test_setup_does_not_prompt_without_terminal_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=True)
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "missing-config"))
    calls: list[tuple[dict[str, Any] | None, object, object]] = []

    def fake_setup(config: dict[str, Any] | None, *, confirm: object, out: object) -> dict[str, Any]:
        calls.append((config, confirm, out))
        return {"browser": {"installed": True, "action": "already-installed"}, "next": ["campusctl sync"]}

    monkeypatch.setattr(setup, "run_setup", fake_setup)
    monkeypatch.setattr("builtins.input", lambda *_args: pytest.fail("setup prompted without terminal stdin"))

    assert cli.main(["setup"]) == 0
    assert calls == [(None, None, stdout)]


def test_setup_returns_config_error_for_invalid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('provider = "invalid"\n', encoding="utf-8")
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(setup, "run_setup", lambda *_args, **_kwargs: pytest.fail("setup ran for invalid config"))

    assert cli.main(["setup", "--json"]) == 2
    envelope = json.loads(stdout.getvalue())
    assert envelope["errors"][0]["code"] == "config-invalid"


@pytest.mark.parametrize(
    ("save_password", "browser_ready", "setup_answer", "expected_next"),
    [
        (False, True, None, "campusctl auth set"),
        (True, False, "n", "campusctl setup"),
        (True, False, "", "campusctl sync"),
        (True, True, None, "campusctl sync"),
    ],
)
def test_config_init_next_step_tracks_password_and_browser_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    save_password: bool,
    browser_ready: bool,
    setup_answer: str | None,
    expected_next: str,
) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    answers = iter(["student-id", "" if save_password else "n"] + ([setup_answer] if setup_answer is not None else []))
    prompts: list[str] = []

    def input_answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", input_answer)
    monkeypatch.setattr(config_init, "prompt_and_store", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(config_init, "chromium_installed", lambda _path=None: browser_ready)

    def fake_setup(_config: dict[str, Any], *, confirm: Any, out: object) -> dict[str, Any]:
        assert confirm is config_init._confirm_setup
        answer = confirm("Install Chromium? [Y/n]")
        return {
            "browser": {"installed": answer, "action": "installed" if answer else "declined"},
            "next": ["campusctl sync"] if answer else [],
        }

    if not browser_ready:
        monkeypatch.setattr(config_init, "run_setup", fake_setup)

    assert cli.main(["config", "init"]) == 0
    assert stdout.getvalue().splitlines()[-1] == f"Next: {expected_next}"
    if not browser_ready:
        assert prompts[-1] == "Install Chromium? [Y/n] "


def test_invalid_output_environment_falls_back_before_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    monkeypatch.setenv("CAMPUSCTL_OUTPUT", "pretty")

    assert cli.main(["--bad-option"]) == 2
    assert stdout.getvalue().startswith("{")
    assert json.loads(stdout.getvalue())["errors"][0]["code"] == "usage-error"


def test_usage_error_uses_json_when_flag_follows_invalid_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=True)

    assert cli.main(["not-a-command", "--json"]) == 2
    assert json.loads(stdout.getvalue())["errors"][0]["code"] == "usage-error"


def test_partial_result_and_error_use_human_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=True)
    monkeypatch.setattr(
        cli,
        "_dispatch",
        lambda _args: (
            {"courses": 1, "lectures": 2, "incomplete": 1, "failed_courses": []},
            [CampusError("course-sync-failed", "One course could not be synced.")],
        ),
    )

    assert cli.main(["sync"]) == 1
    output = stdout.getvalue()
    assert "Synced 1 course, 2 lectures, 1 unfinished." in output
    assert "course-sync-failed" in output
    assert not output.startswith("{")


def test_auth_set_prompts_with_tty_stdin_in_json_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=True, stdout_tty=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        chr(10).join(['provider = "cnu"', "[account]", 'username = "student-id"', ""]),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(config_dir))
    prompts: list[str] = []
    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        credentials.getpass,
        "getpass",
        lambda prompt: prompts.append(prompt) or "test-password",
    )
    monkeypatch.setattr(
        credentials,
        "store_keyring_password",
        lambda _config, password: stored.append(("student-id", password)),
    )

    assert cli.main(["auth", "set", "--json"]) == 0
    envelope = json.loads(stdout.getvalue())
    assert envelope["result"] == {"provider": "keyring", "configured": True}
    assert prompts == ["LMS password: "]
    assert stored == [("student-id", "test-password")]


def _batch_items(*outcomes: str) -> list[dict[str, Any]]:
    items = []
    for index, outcome in enumerate(outcomes, start=1):
        item: dict[str, Any] = {"entity_id": f"cnu_assignment:course-1:task-{index}", "outcome": outcome}
        if outcome in {"completed", "partial"}:
            item["source_package"] = {
                "entity_id": item["entity_id"],
                "completeness": "partial" if outcome == "partial" else "complete",
                "path": f"/data/sources/{item['entity_id']}",
                "content_path": f"/data/sources/{item['entity_id']}/content.md",
                "omitted_resources": [],
            }
        if outcome in {"partial", "failed"}:
            item["reason_code"] = "fetch-failed"
        items.append(item)
    return items


@pytest.mark.parametrize("command", ["assignments", "notices"])
@pytest.mark.parametrize("output", ["json", "human"])
def test_fetch_batch_emitter_keeps_first_failure_status(
    monkeypatch: pytest.MonkeyPatch, command: str, output: str
) -> None:
    cases = [
        (
            [
                CampusError("login-action-required", "Terms are required.", status="user-action"),
                CampusError("fetch-failed", "Detail failed.", status="error"),
                CampusError("session-busy", "Lock occupied.", status="busy"),
            ],
            _batch_items("failed", "failed", "not-started"),
            "user-action",
            2,
        ),
        (
            [
                CampusError("fetch-failed", "Detail failed.", status="error"),
                CampusError("login-action-required", "Terms are required.", status="user-action"),
            ],
            _batch_items("failed", "failed"),
            "error",
            1,
        ),
        (
            [
                CampusError("session-busy", "Lock occupied.", status="busy"),
                CampusError("fetch-failed", "Detail failed.", status="error"),
            ],
            _batch_items("failed", "not-started"),
            "busy",
            75,
        ),
        (
            [CampusError("fetch-failed", "Detail failed.", status="error")],
            _batch_items("completed", "failed"),
            "partial",
            1,
        ),
    ]
    for errors, items, status, exit_code in cases:
        _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=output == "human")
        monkeypatch.setattr(cli, "_dispatch", lambda _args, items=items, errors=errors: ({"items": items}, errors))
        argv = [command, "fetch", "one", "two"]
        if output == "json":
            argv.append("--json")
        assert cli.main(argv) == exit_code
        text = stdout.getvalue()
        if output == "json":
            envelope = json.loads(text)
            assert envelope["status"] == status
            assert [item["code"] for item in envelope["errors"]] == [error.code for error in errors]
            assert envelope["result"]["items"] == items
        else:
            assert not text.startswith("{")
            assert errors[0].code in text
            assert items[-1]["entity_id"] in text

    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    ordinary = [CampusError("course-sync-failed", "One course failed.", status="user-action")]
    monkeypatch.setattr(
        cli,
        "_dispatch",
        lambda _args: ({"courses": 1, "lectures": 0, "incomplete": 0, "failed_courses": []}, ordinary),
    )
    assert cli.main(["sync", "--json"]) == 1
    preserved = json.loads(stdout.getvalue())
    assert preserved["status"] == "partial"
    assert preserved["errors"][0]["code"] == "course-sync-failed"


def test_help_groups_commands_and_resolves_paths_at_render_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path / "data"))
    assert cli.main(["--help"]) == 0
    help_text = stdout.getvalue()
    assert help_text.index("Get started:") < help_text.index("Everyday:") < help_text.index("Settings:")
    for command in (
        "setup",
        "doctor",
        "status",
        "sync",
        "lectures",
        "assignments",
        "notices",
        "materials",
        "courses",
        "config",
        "auth",
    ):
        assert re.search(rf"^\s+{command}\s+\S", help_text, re.MULTILINE)
    assert f"Config: {(tmp_path / 'config' / 'config.toml').resolve()}" in help_text
    assert f"Data: {(tmp_path / 'data').resolve()}" in help_text
    assert "Examples:\n  campusctl setup\n  campusctl sync\n  campusctl status" in help_text
    assert "Docs: https://github.com/haesol-shin/campusctl/blob/main/docs/usage.md" in help_text
    assert "CAMPUSCTL_OUTPUT=human|json" in help_text
    assert help_text.index("CAMPUSCTL_OUTPUT=human|json") < help_text.index("Are you an AI agent?")
    assert "https://raw.githubusercontent.com/haesol-shin/campusctl/main/docs/agent-guide.md" in help_text
    assert "SKIP if the campusctl skill is already loaded. Otherwise read" in help_text
    assert "https://raw.githubusercontent.com/haesol-shin/campusctl/main/skills/campusctl/SKILL.md" in help_text
    assert not (tmp_path / "data").exists()

    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    assert cli.main(["sync", "--help"]) == 0
    assert "DOMAIN[,DOMAIN...]" in stdout.getvalue()
    assert "lectures, assignments, notices, materials" in stdout.getvalue()
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    assert cli.main(["materials", "download", "--help"]) == 0
    assert "[ID]" in stdout.getvalue()
    assert "--json      emit the JSON response envelope" in stdout.getvalue()


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["assignments", "list", "--help"], ("--refresh", "--course", "--json")),
        (["notices", "fetch", "--help"], ("ID [ID ...]", "--out DIR", "--json")),
        (["assignments", "fetch", "--help"], ("ID [ID ...]", "--out DIR", "--json")),
        (["materials", "list", "--help"], ("--refresh", "--course", "--json")),
        (["lectures", "play", "--help"], ("ID [ID ...]", "--speed", "--json")),
    ],
)
def test_domain_help_describes_actions(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: tuple[str, ...]
) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    assert cli.main(argv) == 0
    help_text = stdout.getvalue()
    assert all(item in help_text for item in expected)
    assert "emit the JSON response envelope" in help_text


@pytest.mark.parametrize("catalog_exists", [False, True])
def test_empty_status_human_distinguishes_unreadable_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog_exists: bool
) -> None:
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=True)
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    if catalog_exists:
        catalog = tmp_path / "catalog" / "lectures.json"
        catalog.parent.mkdir()
        catalog.write_text("not-json", encoding="utf-8")
    assert cli.main(["status"]) == 2
    text = stdout.getvalue()
    assert text.startswith(
        "No readable local catalog is available. Next: campusctl sync\n"
        if catalog_exists
        else "No coursework has been synced yet. Next: campusctl sync\n"
    )
    assert "unknown:" not in text and "See:" not in text

    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    assert cli.main(["status", "--json"]) == 2
    assert json.loads(stdout.getvalue())["errors"][0]["code"] == "catalog-missing"


@pytest.mark.parametrize("domain", ["lectures", "assignments", "notices"])
def test_missing_catalog_human_hides_path_but_json_retains_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path))
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=True)
    assert cli.main([domain, "list"]) == 2
    assert f"{domain.capitalize()} catalog is missing" in stdout.getvalue()
    assert str(tmp_path) not in stdout.getvalue()
    _, stdout = _streams(monkeypatch, stdin_tty=False, stdout_tty=False)
    assert cli.main([domain, "list", "--json"]) == 2
    assert str(tmp_path) in json.loads(stdout.getvalue())["errors"][0]["message"]
