from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from campusctl import __version__, catalog, cli, credentials, lock, paths
from campusctl.config import ConfigError, load_config
from campusctl.envelope import CampusError, emit, make_envelope
from campusctl.paths import config_dir, config_path, data_dir

SENTINEL = "SENTINEL_SECRET_DO_NOT_LEAK"


def _config_text(*, credential_provider: str = "keyring", username: str = "sample-account") -> str:
    return textwrap.dedent(
        f"""\
        provider = "cnu"

        [account]
        username = "{username}"

        [credentials]
        provider = "{credential_provider}"
        """
    )


def _helper(tmp_path: Path, source: str) -> list[str]:
    path = tmp_path / "credential-helper.py"
    path.write_text(source, encoding="utf-8")
    return [sys.executable, str(path)]


def _command_config(command: list[str], *, username: str = "sample-account") -> dict[str, object]:
    return {"account": {"username": username}, "credentials": {"provider": "command", "command": command}}


def _assert_safe_failure(error: CampusError) -> None:
    output = io.StringIO()
    exit_code = emit(error=error, stream=output)
    envelope = output.getvalue()
    parsed = json.loads(envelope)
    assert exit_code == 1 if error.status == "error" else exit_code in {2, 75}
    assert SENTINEL not in str(error)
    assert SENTINEL not in envelope
    assert SENTINEL not in json.dumps(parsed)


def _fake_keyring(
    monkeypatch: pytest.MonkeyPatch, backend: object, *, password: str | None = None, failure: Exception | None = None
):
    module = ModuleType("keyring")
    module.get_keyring = lambda: backend  # type: ignore[attr-defined]

    def get_password(service: str, account: str) -> str | None:
        if failure:
            raise failure
        return password

    def set_password(service: str, account: str, value: str) -> None:
        if failure:
            raise failure

    module.get_password = get_password  # type: ignore[attr-defined]
    module.set_password = set_password  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    return module


def test_envelope_has_utc_timestamp_and_status_exit_codes() -> None:
    envelope = make_envelope(result={"value": 1})
    assert envelope["schema_version"] == 1
    assert envelope["tool"] == "campusctl"
    assert envelope["tool_version"] == __version__
    stamp = datetime.fromisoformat(envelope["generated_at"].replace("Z", "+00:00"))
    assert stamp.tzinfo == UTC
    assert {
        status: emit(status=status, stream=io.StringIO())
        for status in ("ok", "partial", "error", "user-action", "busy")
    } == {
        "ok": 0,
        "partial": 1,
        "error": 1,
        "user-action": 2,
        "busy": 75,
    }


def test_usage_error_is_enveloped_without_echoing_invalid_argument(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main([SENTINEL, "--json"])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert exit_code == 2
    assert captured.err == ""
    assert envelope["status"] == "user-action"
    assert envelope["errors"][0]["code"] == "usage-error"
    assert SENTINEL not in captured.out + captured.err


def test_unknown_exception_reports_only_class_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "doctor_result", lambda override=None: (_ for _ in ()).throw(RuntimeError(SENTINEL)))
    assert cli.main(["doctor", "--json"]) == 1
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    assert envelope["errors"][0]["code"] == "internal"
    assert envelope["errors"][0]["message"] == "RuntimeError"
    assert SENTINEL not in output.out + output.err


@pytest.mark.parametrize(
    ("mode", "expected_exit", "expected_status"),
    [("ok", 0, "ok"), ("error", 1, "error"), ("partial", 1, "partial")],
)
def test_cli_json_stdout_is_utf8_through_a_redirected_pipe(
    mode: str, expected_exit: int, expected_status: str, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    source_root = str(root / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (source_root, env.get("PYTHONPATH"))))
    driver = textwrap.dedent(
        """\
        import sys
        from campusctl import cli
        from campusctl.envelope import CampusError

        mode = sys.argv[1]
        if mode == "ok":
            cli._dispatch = lambda _: ({"title": "강의"}, None)
            args = ["courses", "list", "--json"]
        elif mode == "error":
            cli._dispatch = lambda _: (None, CampusError("smoke", "테스트", None, "error"))
            args = ["courses", "list", "--json"]
        else:
            cli._dispatch = lambda _: ({"title": "강의"}, [CampusError("smoke", "테스트")])
            args = ["sync", "--json"]
        raise SystemExit(cli.main(args))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", driver, mode],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == expected_exit
    assert result.stderr == b""
    envelope = json.loads(result.stdout.decode("utf-8"))
    assert envelope["status"] == expected_status
    if mode != "error":
        assert envelope["result"]["title"] == "강의"
    else:
        assert envelope["errors"][0]["message"] == "테스트"


def test_private_paths_use_environment_overrides_and_mode_0700(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf = tmp_path / "private-conf"
    data = tmp_path / "private-data"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(data))
    assert config_dir(create=True) == conf
    assert data_dir(create=True) == data
    assert conf.is_dir()
    assert data.is_dir()
    assert config_path() == conf / "config.toml"
    if os.name != "nt":
        assert conf.stat().st_mode & 0o777 == 0o700
        assert data.stat().st_mode & 0o777 == 0o700

    existing = tmp_path / "existing"
    existing.mkdir()
    if os.name != "nt":
        existing.chmod(0o750)
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(existing))
    assert config_dir(create=True) == existing
    if os.name != "nt":
        assert existing.stat().st_mode & 0o777 == 0o750


def test_relative_xdg_paths_fall_back_to_home_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAMPUSCTL_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CAMPUSCTL_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-config")
    monkeypatch.setenv("XDG_DATA_HOME", "relative-data")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(paths.sys, "platform", "linux")
    assert config_dir() == tmp_path / ".config" / "campusctl"
    assert data_dir() == tmp_path / ".local" / "share" / "campusctl"


def test_windows_config_and_data_dirs_use_appdata_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roaming = tmp_path / "Roaming Profile"
    local = tmp_path / "Local Profile"
    monkeypatch.delenv("CAMPUSCTL_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CAMPUSCTL_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    assert config_dir(create=True) == roaming / "campusctl"
    assert data_dir(create=True) == local / "campusctl"
    assert (roaming / "campusctl").is_dir()
    assert (local / "campusctl").is_dir()


def test_doctor_reports_unknown_display_availability_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "_playwright_diagnostics", lambda _path: (True, True))

    result, error = cli.doctor_result()

    assert result["display_available"] is None
    assert error is not None
    assert error.code == "config-missing"


def test_config_missing_and_invalid_are_safe_user_actions(tmp_path: Path) -> None:
    missing = tmp_path / "config.toml"
    with pytest.raises(ConfigError) as error:
        load_config(missing)
    assert error.value.code == "config-missing"
    assert str(missing) in error.value.remediation
    assert "campusctl config init" in error.value.remediation

    invalid = tmp_path / "invalid.toml"
    invalid.write_text(f'provider = "{SENTINEL}"\n[credentials]\nprovider = ["{SENTINEL}"]\n', encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_config(invalid)
    assert error.value.code == "config-invalid"
    assert SENTINEL not in str(error.value)


def test_materials_doctor_reports_settings_without_creating_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from campusctl.config import validate_config
    from campusctl.presentation import _doctor

    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(tmp_path / "data"))
    config = config_path()
    config.parent.mkdir(parents=True)
    monkeypatch.setenv("CAMPUSCTL_TEST_DEST", str(tmp_path / "destination"))
    config.write_text('[materials]\ndownload_dir = "$CAMPUSCTL_TEST_DEST/{course}/materials"\nadopt_existing = true\n')
    result, _ = cli.doctor_result()
    assert result["materials"] == {
        "download_dir": "$CAMPUSCTL_TEST_DEST/{course}/materials",
        "adopt_existing": True,
        "semester": None,
    }
    assert not (tmp_path / "destination").exists()
    lines, _ = _doctor(result, 100, [])
    assert "[ok] Materials downloads: configured (adoption on)" in lines
    monkeypatch.delenv("CAMPUSCTL_TEST_DEST")
    invalid, error = cli.doctor_result()
    assert error is not None and error.code == "config-invalid"
    assert invalid["materials"] == {"download_dir": None, "adopt_existing": None, "semester": None}
    assert not (tmp_path / "destination").exists()
    with pytest.raises(ConfigError):
        validate_config({"materials": {"download_dir": "{semester}", "semester": ""}}, path=config)


def test_secure_keyring_backend_reads_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = type("SecureBackend", (), {"priority": 1})()
    _fake_keyring(monkeypatch, backend, password="test-password")
    assert credentials.get_credentials({"account": {"username": "sample-account"}}) == (
        "sample-account",
        "test-password",
    )


@pytest.mark.parametrize("backend_name", ["WinVaultKeyring", "ChainerBackend"])
def test_windows_credential_manager_backend_is_accepted(monkeypatch: pytest.MonkeyPatch, backend_name: str) -> None:
    vault = type("WinVaultKeyring", (), {"__module__": "keyring.backends.Windows", "priority": 5})()
    backend = (
        type("ChainerBackend", (), {"priority": 5, "backends": [vault]})()
        if backend_name == "ChainerBackend"
        else vault
    )
    _fake_keyring(monkeypatch, backend, password="test-password")

    assert credentials.get_credentials({"account": {"username": "sample-account"}}) == (
        "sample-account",
        "test-password",
    )


@pytest.mark.parametrize(
    ("backend", "code"),
    [
        (type("FailKeyring", (), {"priority": 1})(), "credential-backend-insecure"),
        (type("NullKeyring", (), {"priority": 1})(), "credential-backend-insecure"),
        (type("PlaintextKeyring", (), {"priority": 1})(), "credential-backend-insecure"),
        (type("FileKeyring", (), {"__module__": "keyrings.alt.file", "priority": 1})(), "credential-backend-insecure"),
        (type("UnavailableKeyring", (), {"priority": 0})(), "credential-backend-unavailable"),
    ],
)
def test_keyring_rejects_insecure_or_unavailable_backends(
    monkeypatch: pytest.MonkeyPatch, backend: object, code: str
) -> None:
    _fake_keyring(monkeypatch, backend, password=SENTINEL)
    with pytest.raises(CampusError) as error:
        credentials.get_credentials({"account": {"username": "sample-account"}})
    assert error.value.code == code
    _assert_safe_failure(error.value)


def test_keyring_rejects_chainer_with_nested_insecure_file_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    file_backend = type("FileKeyring", (), {"__module__": "keyrings.alt.file", "priority": 1})()
    nested_chain = type("ChainerBackend", (), {"priority": 1, "backends": [file_backend]})()

    class FakeClassProperty:
        def __init__(self, value: list[object]) -> None:
            self.value = value

        def __get__(self, _instance: object, _owner: type | None = None) -> list[object]:
            return self.value

    class FakeChainerBackend:
        priority = 1
        backends = FakeClassProperty([type("SecureBackend", (), {"priority": 1})(), nested_chain])

    _fake_keyring(monkeypatch, FakeChainerBackend())
    with pytest.raises(CampusError) as error:
        credentials.get_credentials({"account": {"username": "sample-account"}})
    assert error.value.code == "credential-backend-insecure"
    _assert_safe_failure(error.value)


def test_keyring_backend_failure_never_leaks_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = type("SecureBackend", (), {"priority": 1})()
    _fake_keyring(monkeypatch, backend, failure=RuntimeError(SENTINEL))
    with pytest.raises(CampusError) as error:
        credentials.get_credentials({"account": {"username": "sample-account"}})
    assert error.value.code == "credential-backend-unavailable"
    _assert_safe_failure(error.value)


def test_windows_script_helpers_require_an_explicit_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    for suffix in ("ps1", "cmd", "bat"):
        script_path = rf"C:\Program Files\campusctl\helper.{suffix}"
        with pytest.raises(CampusError) as error:
            credentials._helper_command(_command_config([script_path]))
        assert error.value.code == "credential-helper-invalid"

        interpreter = "powershell.exe" if suffix == "ps1" else "cmd.exe"
        arguments = ["-NoProfile", "-File", script_path] if suffix == "ps1" else ["/d", "/c", script_path]
        assert credentials._helper_command(_command_config([interpreter, *arguments]))[0] == interpreter


def test_command_helper_argv_preserves_executable_path_with_spaces(tmp_path: Path) -> None:
    helper_dir = tmp_path / "helper directory with spaces"
    helper_dir.mkdir()
    source = textwrap.dedent(
        """\
        import json
        print(json.dumps({"username": "sample-account", "password": "test-password"}))
        """
    )
    helper = _helper(helper_dir, source)
    assert credentials.get_credentials(_command_config(helper)) == ("sample-account", "test-password")


@pytest.mark.skipif(os.name != "nt", reason="Windows process-group behavior")
def test_windows_helper_starts_in_a_new_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(
        tmp_path,
        "import json\nprint(json.dumps({'username': 'sample-account', 'password': 'test-password'}))\n",
    )
    real_popen = credentials.subprocess.Popen
    creation_flags: list[int] = []

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        creation_flags.append(kwargs["creationflags"])
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(credentials.subprocess, "Popen", recording_popen)
    assert credentials.get_credentials(_command_config(helper)) == ("sample-account", "test-password")
    assert creation_flags[0] & subprocess.CREATE_NEW_PROCESS_GROUP


def test_command_helper_success_and_username_fallback(tmp_path: Path) -> None:
    helper = _helper(
        tmp_path,
        "import json\nprint(json.dumps({'username': 'helper-account', 'password': 'test-password'}))\n",
    )
    assert credentials.get_credentials(_command_config(helper, username="helper-account")) == (
        "helper-account",
        "test-password",
    )
    config = {"credentials": {"provider": "command", "command": helper}}
    assert credentials.get_credentials(config) == ("helper-account", "test-password")


@pytest.mark.parametrize(
    ("source", "timeout", "expected_code"),
    [
        (
            f"import sys\nprint({SENTINEL!r})\nprint({SENTINEL!r}, file=sys.stderr)\nsys.exit(9)\n",
            None,
            "credential-helper-failed",
        ),
        (
            f"import time\nprint({SENTINEL!r}, flush=True)\ntime.sleep(2)\n",
            1,
            "credential-helper-failed",
        ),
        (f"print({SENTINEL!r})\n", None, "credential-helper-failed"),
        (
            f"import json\nprint(json.dumps({{'username': 'different-account', 'password': {SENTINEL!r}}}))\n",
            None,
            "credential-helper-failed",
        ),
    ],
)
def test_helper_failure_paths_redact_stdout_stderr_and_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    timeout: float | None,
    expected_code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    helper = _helper(tmp_path, source)
    if timeout is not None:
        monkeypatch.setattr(credentials, "HELPER_TIMEOUT_SECONDS", timeout)
    with pytest.raises(CampusError) as error:
        credentials.get_credentials(_command_config(helper))
    assert error.value.code == expected_code
    _assert_safe_failure(error.value)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


@pytest.mark.skipif(os.name != "posix", reason="process-group behavior is POSIX-specific")
def test_helper_timeout_kills_child_holding_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_path = tmp_path / "child.pid"
    child_source = "import time; time.sleep(60)"
    helper = _helper(
        tmp_path,
        textwrap.dedent(
            f"""\
            import json
            import subprocess
            import sys

            child = subprocess.Popen([sys.executable, "-c", {child_source!r}])
            with open({str(pid_path)!r}, "w", encoding="utf-8") as pid_file:
                pid_file.write(str(child.pid))
            print(json.dumps({{"username": "sample-account", "password": "test-password"}}), flush=True)
            """
        ),
    )
    monkeypatch.setattr(credentials, "HELPER_TIMEOUT_SECONDS", 1)
    with pytest.raises(CampusError) as error:
        credentials.get_credentials(_command_config(helper))
    assert error.value.code == "credential-helper-failed"
    _assert_safe_failure(error.value)

    child_pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while True:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        if sys.platform.startswith("linux"):
            stat_path = Path(f"/proc/{child_pid}/stat")
            try:
                if stat_path.read_text(encoding="utf-8").split(") ", 1)[1][0] == "Z":
                    break
            except FileNotFoundError:
                break
        if time.monotonic() >= deadline:
            pytest.fail("credential helper child remained alive after timeout")
        time.sleep(0.1)


def _windows_process_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(pid) in result.stdout


def _assert_windows_process_stopped(pid: int) -> None:
    deadline = time.monotonic() + 10
    while _windows_process_exists(pid):
        if time.monotonic() >= deadline:
            pytest.fail(f"credential helper process {pid} remained alive after timeout")
        time.sleep(0.1)


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree behavior")
@pytest.mark.parametrize("helper_exits", [False, True])
def test_windows_helper_timeout_kills_descendants_holding_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_exits: bool
) -> None:
    child_pid_path = tmp_path / "child.pid"
    helper_pid_path = tmp_path / "helper.pid"
    child_source = "import time; time.sleep(60)"
    helper = _helper(
        tmp_path,
        textwrap.dedent(
            f"""\
            import json
            import os
            import subprocess
            import sys
            import time

            child = subprocess.Popen([sys.executable, "-c", {child_source!r}])
            with open({str(child_pid_path)!r}, "w", encoding="utf-8") as pid_file:
                pid_file.write(str(child.pid))
            with open({str(helper_pid_path)!r}, "w", encoding="utf-8") as pid_file:
                pid_file.write(str(os.getpid()))
            print(json.dumps({{"username": "sample-account", "password": "test-password"}}), flush=True)
            {"pass" if helper_exits else "time.sleep(60)"}
            """
        ),
    )
    monkeypatch.setattr(credentials, "HELPER_TIMEOUT_SECONDS", 2)
    windows_job = credentials._WindowsJob
    child_started_before_job_assignment = False

    def delay_job_until_child_starts(process: subprocess.Popen[bytes]) -> credentials._WindowsJob:
        nonlocal child_started_before_job_assignment
        deadline = time.monotonic() + 2
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_started_before_job_assignment = child_pid_path.exists()
        return windows_job(process)

    monkeypatch.setattr(credentials, "_WindowsJob", delay_job_until_child_starts)

    started = time.monotonic()
    with pytest.raises(CampusError) as error:
        credentials.get_credentials(_command_config(helper))

    assert time.monotonic() - started < 8
    assert error.value.code == "credential-helper-failed"
    assert error.value.message == "The credential helper timed out."
    assert not child_started_before_job_assignment
    helper_pid = int(helper_pid_path.read_text(encoding="utf-8"))
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    try:
        _assert_windows_process_stopped(helper_pid)
        _assert_windows_process_stopped(child_pid)
    finally:
        for pid in (helper_pid, child_pid):
            if _windows_process_exists(pid):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )


@pytest.mark.parametrize(("stream", "limit"), [("stdout", 64 * 1024), ("stderr", 4 * 1024)])
def test_helper_output_overflow_fails_safely(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stream: str, limit: int
) -> None:
    repetitions = limit // len(SENTINEL) + 1
    helper = _helper(
        tmp_path,
        f"import sys\nsys.{stream}.buffer.write({SENTINEL.encode()!r} * {repetitions})\nsys.{stream}.flush()\n",
    )
    with pytest.raises(CampusError) as error:
        credentials.get_credentials(_command_config(helper))
    assert error.value.code == "credential-helper-failed"
    assert error.value.message == "The credential helper exceeded its output limit."
    _assert_safe_failure(error.value)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_helper_status_does_not_execute_unless_check_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(tmp_path, "raise SystemExit(0)\n")
    config = _command_config(helper)
    monkeypatch.setattr(credentials.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("helper was run"))
    name, configured, check = credentials.helper_status(config, check=False)
    assert name == Path(helper[0]).name
    assert configured is True
    assert check is None


def test_command_auth_set_never_stores_password() -> None:
    with pytest.raises(CampusError) as error:
        credentials.prompt_and_store({"credentials": {"provider": "command"}}, stdin_isatty=False)
    assert error.value.code == "auth-command-provider"


def test_auth_set_requires_tty() -> None:
    with pytest.raises(CampusError) as error:
        credentials.prompt_and_store({"credentials": {"provider": "keyring"}}, stdin_isatty=False)
    assert error.value.code == "auth-tty-required"


def test_auth_set_prompts_and_stores_only_in_secure_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = type("SecureBackend", (), {"priority": 1})()
    module = _fake_keyring(monkeypatch, backend)
    stored: dict[str, str] = {}
    module.set_password = lambda service, account, value: stored.update(
        service=service, account=account, password=value
    )  # type: ignore[attr-defined]
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: "test-password")
    credentials.prompt_and_store(
        {"account": {"username": "sample-account"}, "credentials": {"provider": "keyring"}},
        stdin_isatty=True,
    )
    assert stored == {
        "service": "campusctl:cnu",
        "account": "sample-account",
        "password": "test-password",
    }


def test_auth_set_backend_failure_redacts_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backend = type("SecureBackend", (), {"priority": 1})()
    _fake_keyring(monkeypatch, backend, failure=RuntimeError(SENTINEL))
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: SENTINEL)
    with pytest.raises(CampusError) as error:
        credentials.prompt_and_store(
            {"account": {"username": "sample-account"}, "credentials": {"provider": "keyring"}},
            stdin_isatty=True,
        )
    assert error.value.code == "credential-backend-unavailable"
    _assert_safe_failure(error.value)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_auth_status_check_reports_only_ok_or_failed(tmp_path: Path) -> None:
    good = _helper(
        tmp_path,
        "import json\nprint(json.dumps({'username': 'sample-account', 'password': 'test-password'}))\n",
    )
    _, configured, check = credentials.helper_status(_command_config(good), check=True)
    assert configured is True
    assert check == "ok"

    bad = _helper(tmp_path, f"print({SENTINEL!r}, flush=True)\nraise SystemExit(1)\n")
    _, configured, check = credentials.helper_status(_command_config(bad), check=True)
    assert configured is True
    assert check == "failed"


@pytest.mark.parametrize(
    ("source", "expected_exit", "expected_status", "expected_check", "expected_error"),
    [
        (
            "import json\nprint(json.dumps({'username': 'sample-account', 'password': 'test-password'}))\n",
            0,
            "ok",
            "ok",
            None,
        ),
        (
            f"import sys\nprint({SENTINEL!r})\nprint({SENTINEL!r}, file=sys.stderr)\nraise SystemExit(1)\n",
            1,
            "error",
            "failed",
            "credential-helper-failed",
        ),
    ],
)
def test_auth_status_check_cli_envelopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    expected_exit: int,
    expected_status: str,
    expected_check: str,
    expected_error: str | None,
) -> None:
    conf = tmp_path / "config"
    conf.mkdir()
    helper = _helper(tmp_path, source)
    (conf / "config.toml").write_text(
        _config_text(credential_provider="command") + f"command = {json.dumps(helper)}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    assert cli.main(["auth", "status", "--check", "--json"]) == expected_exit
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    result = envelope["result"]
    assert output.err == ""
    assert envelope["status"] == expected_status
    assert result["provider"] == "command"
    assert result["configured"] is True
    assert result["check"] == expected_check
    assert result["helper"] == Path(sys.executable).name
    if expected_error:
        assert envelope["errors"][0]["code"] == expected_error
    else:
        assert envelope["errors"] == []
    assert SENTINEL not in output.out + output.err


def test_auth_status_keyring_failure_preserves_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conf = tmp_path / "config"
    conf.mkdir()
    (conf / "config.toml").write_text(_config_text(), encoding="utf-8")
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))

    def unavailable(config: dict[str, object]) -> tuple[str, bool]:
        raise CampusError(
            "credential-backend-unavailable",
            "Credentials could not be read from the configured secure backend.",
            "Check the operating-system credential backend.",
        )

    monkeypatch.setattr(cli, "keyring_status", unavailable)
    assert cli.main(["auth", "status", "--check", "--json"]) == 2
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    assert output.err == ""
    assert envelope["status"] == "user-action"
    assert envelope["result"] == {"provider": "keyring", "configured": None}
    assert envelope["errors"][0]["code"] == "credential-backend-unavailable"


def test_auth_status_missing_keyring_password_only_fails_with_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conf = tmp_path / "config"
    conf.mkdir()
    (conf / "config.toml").write_text(_config_text(), encoding="utf-8")
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    monkeypatch.setattr(cli, "keyring_status", lambda _config: ("SecureBackend", False))

    assert cli.main(["auth", "status", "--check", "--json"]) == 2
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "user-action"
    assert checked["result"] == {
        "provider": "keyring",
        "backend": "SecureBackend",
        "configured": False,
    }
    assert checked["errors"][0]["code"] == "credentials-not-configured"
    assert checked["errors"][0]["remediation"] == "Run 'campusctl auth set' to save credentials."

    assert cli.main(["auth", "status", "--json"]) == 0
    unchecked = json.loads(capsys.readouterr().out)
    assert unchecked["status"] == "ok"
    assert unchecked["result"]["configured"] is False


def test_sync_rejects_unsupported_domain(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sync", "--only", "no-such-domain", "--json"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["errors"][0]["code"] == "unsupported-domain"


def test_doctor_missing_config_is_read_only_and_reports_contract_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "config"
    data = tmp_path / "data"
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(data))
    monkeypatch.setattr(cli, "_playwright_diagnostics", lambda path: (True, False))
    result, error = cli.doctor_result()
    assert error is not None and error.code == "config-missing"
    assert result["config"] == {"path": str(conf / "config.toml"), "present": False}
    assert result["data_dir"] == str(data)
    assert result["browser"]["playwright_importable"] is True
    assert result["playback"]["supported_speeds"] == [1.0, 1.25, 1.5]
    assert result["capabilities"] == cli.CAPABILITIES
    assert result["capabilities"]["sync"][0] == "lectures"
    assert result["capabilities"]["lectures"] == ["list", "play"]
    assert not conf.exists()
    assert not data.exists()


@pytest.mark.parametrize(
    ("playwright_importable", "chromium_installed"),
    [(False, False), (True, False)],
)
def test_doctor_uses_one_code_for_missing_playwright_or_chromium(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, playwright_importable: bool, chromium_installed: bool
) -> None:
    conf = tmp_path / "config"
    conf.mkdir()
    data = tmp_path / "data"
    helper = _helper(tmp_path, "raise SystemExit(0)\n")
    (conf / "config.toml").write_text(
        _config_text(credential_provider="command") + f"command = {json.dumps(helper)}\n",
        encoding="utf-8",
    )
    catalog.write_catalog({"schema_version": 1, "courses": [], "lectures": []}, catalog.catalog_path(data))
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(data))
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(
        cli,
        "_playwright_diagnostics",
        lambda _path: (playwright_importable, chromium_installed),
    )

    result, error = cli.doctor_result()

    assert result["browser"]["playwright_importable"] is playwright_importable
    assert result["browser"]["chromium_installed"] is chromium_installed
    assert error is not None
    assert error.code == "browser-not-installed"
    assert error.status == "user-action"
    assert error.remediation == "Run 'campusctl setup' to install the browser."


def test_doctor_does_not_run_command_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf = tmp_path / "config"
    conf.mkdir()
    data = tmp_path / "data"
    helper_marker = tmp_path / "helper-ran"
    helper = _helper(tmp_path, f"from pathlib import Path\nPath({str(helper_marker)!r}).touch()\n")
    (conf / "config.toml").write_text(
        _config_text(credential_provider="command") + f"command = {json.dumps(helper)}\n",
        encoding="utf-8",
    )
    catalog.write_catalog(
        {"schema_version": 1, "courses": [], "lectures": []},
        catalog.catalog_path(data),
    )
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(data))
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(cli, "_playwright_diagnostics", lambda path: (True, True))
    result, error = cli.doctor_result()
    assert error is None
    assert result["credentials"] == {"provider": "command", "configured": True}
    assert not helper_marker.exists()


def test_doctor_headless_override_reports_support_without_display_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("display readiness diagnostic applies on Linux")
    conf = tmp_path / "config"
    conf.mkdir()
    data = tmp_path / "data"
    helper = _helper(tmp_path, "raise SystemExit(0)\n")
    (conf / "config.toml").write_text(
        _config_text(credential_provider="command") + f"command = {json.dumps(helper)}\n",
        encoding="utf-8",
    )
    catalog.write_catalog({"schema_version": 1, "courses": [], "lectures": []}, catalog.catalog_path(data))
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(data))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(cli, "_playwright_diagnostics", lambda path: (True, True))
    assert cli.main(["--headless", "doctor", "--json"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["result"]["browser"]["headless"] is True
    assert response["result"]["browser"]["headless_support"]["materials.download"] is True
    assert response["result"]["display_available"] is False
    assert cli.main(["--headed", "doctor", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "display-unavailable"


def test_doctor_reports_missing_catalog_as_actionable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf = tmp_path / "config"
    conf.mkdir()
    data = tmp_path / "data"
    helper = _helper(tmp_path, "raise SystemExit(0)\n")
    (conf / "config.toml").write_text(
        _config_text(credential_provider="command") + f"command = {json.dumps(helper)}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CAMPUSCTL_CONFIG_DIR", str(conf))
    monkeypatch.setenv("CAMPUSCTL_DATA_DIR", str(data))
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(cli, "_playwright_diagnostics", lambda path: (True, True))
    result, error = cli.doctor_result()
    assert error is not None and error.code == "catalog-missing"
    assert error.remediation == "Run 'campusctl sync --only lectures' to create it."
    assert result["catalog"] == {"present": False, "generated_at": None}
    assert not data.exists()


def test_cli_version_uses_json_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version", "--json"]) == 0
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    assert output.err == ""
    assert envelope["result"] == {"version": __version__}


@pytest.mark.parametrize("args", [["--help"], ["--json", "--help"]])
def test_cli_help_is_human_readable_and_returns_success(capsys: pytest.CaptureFixture[str], args: list[str]) -> None:
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.startswith("usage: campusctl")
    assert "Campus LMS control CLI" in output.out
    assert "--version" in output.out


def test_catalog_invalid_utf8_is_a_safe_user_action(tmp_path: Path) -> None:
    target = tmp_path / "invalid-utf8.json"
    target.write_bytes(b"\xff")
    with pytest.raises(CampusError) as error:
        catalog.read_catalog(target)
    assert error.value.code == "catalog-invalid"
    assert error.value.status == "user-action"
    assert error.value.message == f"The lecture catalog at {target} could not be read as valid JSON."
    assert error.value.remediation == "Run 'campusctl sync --only lectures' to rebuild the catalog."


def test_catalog_write_is_atomic_and_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "catalog" / "lectures.json"
    value = {
        "schema_version": 1,
        "generated_at": "2026-01-02T03:04:05Z",
        "courses": [{"course_id": "course-1", "label": "Course", "class_no": None}],
        "lectures": [],
    }
    assert catalog.write_catalog(value, target) == target
    saved = catalog.read_catalog(target)
    assert len(saved.pop("generation_id")) == 32
    assert saved == value
    assert list(target.parent.glob("*.tmp")) == []
    assert list(target.parent.glob(".*.tmp")) == []


def test_catalog_unknown_schema_has_sync_remediation(tmp_path: Path) -> None:
    target = tmp_path / "lectures.json"
    target.write_text(json.dumps({"schema_version": 2, "courses": [], "lectures": []}), encoding="utf-8")
    with pytest.raises(CampusError) as error:
        catalog.read_catalog(target)
    assert error.value.code == "catalog-schema-unsupported"
    assert "campusctl sync --only lectures" in error.value.remediation


def test_partial_catalog_merge_preserves_failed_course_records() -> None:
    previous = {
        "schema_version": 1,
        "generated_at": "old",
        "courses": [
            {"course_id": "course-failed", "label": "Old failed", "class_no": None},
            {"course_id": "course-ok", "label": "Old ok", "class_no": None},
            {"course_id": "course-other", "label": "Other", "class_no": None},
        ],
        "lectures": [
            {"entity_id": "failed-old", "course": {"id": "course-failed"}},
            {"entity_id": "ok-old", "course": {"id": "course-ok"}},
            {"entity_id": "other-old", "course": {"id": "course-other"}},
        ],
    }
    merged = catalog.merge_catalog(
        previous,
        [
            {"course_id": "course-failed", "label": "New failed", "class_no": None},
            {"course_id": "course-ok", "label": "New ok", "class_no": None},
        ],
        [{"entity_id": "ok-new", "course": {"id": "course-ok"}}],
        failed_course_ids={"course-failed"},
    )
    courses_by_id = {course["course_id"]: course for course in merged["courses"]}
    lectures_by_id = {lecture["entity_id"]: lecture for lecture in merged["lectures"]}
    assert courses_by_id["course-failed"]["label"] == "Old failed"
    assert courses_by_id["course-ok"]["label"] == "New ok"
    assert courses_by_id["course-other"]["label"] == "Other"
    assert "failed-old" in lectures_by_id and "ok-old" not in lectures_by_id
    assert "ok-new" in lectures_by_id and "other-old" in lectures_by_id


def test_lock_contention_is_nonblocking_across_processes_and_can_be_reacquired(tmp_path: Path) -> None:
    lock_path = tmp_path / "session.lock"
    ready_path = tmp_path / "lock-held"
    release_path = tmp_path / "release-lock"
    script = textwrap.dedent(
        """\
        import sys
        import time
        from pathlib import Path
        from campusctl.lock import exclusive_lock

        lock_path, ready_path, release_path = map(Path, sys.argv[1:])
        with exclusive_lock(lock_path):
            ready_path.write_text("ready", encoding="utf-8")
            deadline = time.monotonic() + 30
            while not release_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not release_path.exists():
                raise SystemExit("parent did not release child lock")
        """
    )
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root / "src"), env.get("PYTHONPATH"))))
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path), str(ready_path), str(release_path)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 15
    try:
        while not ready_path.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"lock holder exited early: {stdout!r} {stderr!r}")
            if time.monotonic() >= deadline:
                pytest.fail("lock holder did not acquire the lock")
            time.sleep(0.05)
        with pytest.raises(CampusError) as error, lock.exclusive_lock(lock_path):
            pass
        assert error.value.code == "session-busy"
    finally:
        release_path.touch()
        process.communicate(timeout=15)

    assert process.returncode == 0
    for _ in range(2):
        with lock.exclusive_lock(lock_path):
            pass


def test_session_lock_is_released_when_operation_fails(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "session.lock"
    with pytest.raises(RuntimeError, match="synthetic operation failure"), lock.exclusive_lock(target):
        raise RuntimeError("synthetic operation failure")

    # Another process must be able to take the same OS lock, not just this thread.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(Path(__file__).resolve().parents[1] / "src"), env.get("PYTHONPATH")))
    )
    contender = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from campusctl.lock import exclusive_lock; "
            "from pathlib import Path; "
            "with_lock = exclusive_lock(Path(sys.argv[1])); "
            "with_lock.__enter__(); with_lock.__exit__(None, None, None)",
            str(target),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert contender.returncode == 0, contender.stderr
