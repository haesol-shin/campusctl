from __future__ import annotations

import contextlib
import getpass
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Any

from campusctl.envelope import CampusError

KEYRING_SERVICE = "campusctl:cnu"
HELPER_TIMEOUT_SECONDS = 30
HELPER_STDOUT_LIMIT = 64 * 1024
HELPER_STDERR_LIMIT = 4 * 1024
_HELPER_REAP_TIMEOUT_SECONDS = 1


class _WindowsJob:
    """Contain a Windows helper and its descendants so close kills the process tree."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL

        class ThreadEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", ctypes.c_long),
                ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
            ]

        kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
        kernel32.Thread32Next.restype = wintypes.BOOL
        kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
        kernel32.ResumeThread.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        self._thread_entry_type = ThreadEntry32

        self._kernel32 = kernel32
        self._wintypes = wintypes
        self._ctypes = ctypes
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                self._handle,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.AssignProcessToJobObject(self._handle, wintypes.HANDLE(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        snapshot = self._kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot in (None, self._ctypes.c_void_p(-1).value):
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            entry = self._thread_entry_type()
            entry.dwSize = self._ctypes.sizeof(entry)
            if not self._kernel32.Thread32First(snapshot, self._ctypes.byref(entry)):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            minimum_entry_size = self._thread_entry_type.th32OwnerProcessID.offset + self._ctypes.sizeof(
                self._wintypes.DWORD
            )
            while True:
                if entry.dwSize < minimum_entry_size:
                    raise OSError("The Windows helper's primary thread details are incomplete.")
                if entry.th32OwnerProcessID == process.pid:
                    thread = self._kernel32.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise self._ctypes.WinError(self._ctypes.get_last_error())
                    try:
                        previous_suspend_count = self._kernel32.ResumeThread(thread)
                        if previous_suspend_count == 0xFFFFFFFF:
                            raise self._ctypes.WinError(self._ctypes.get_last_error())
                        if previous_suspend_count != 1:
                            raise OSError("The Windows helper's primary thread was not suspended.")
                        return
                    finally:
                        self._kernel32.CloseHandle(thread)
                if not self._kernel32.Thread32Next(snapshot, self._ctypes.byref(entry)):
                    break
            raise OSError("The Windows helper's primary thread could not be found.")
        finally:
            self._kernel32.CloseHandle(snapshot)

    def terminate(self) -> bool:
        return bool(self._handle and self._kernel32.TerminateJobObject(self._handle, 1))

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _raise(code: str, message: str, remediation: str, status: str = "user-action") -> None:
    raise CampusError(code, message, remediation, status) from None


def _keyring_module() -> Any:
    try:
        import keyring
    except Exception:
        _raise(
            "credential-backend-unavailable",
            "No supported credential backend is available.",
            "Install and configure a secure operating-system keyring backend.",
        )
    return keyring


def _contains_insecure_backend(backend: Any) -> bool:
    pending = [backend]
    inspected: set[int] = set()
    while pending:
        candidate = pending.pop()
        if id(candidate) in inspected:
            continue
        inspected.add(id(candidate))

        backend_type = candidate if isinstance(candidate, type) else type(candidate)
        module = backend_type.__module__.lower()
        name = backend_type.__name__.lower()
        identity = f"{module}.{name}"
        if any(part in identity for part in ("fail", "null", "plaintext")) or (
            "keyrings.alt" in module and "file" in identity
        ):
            return True

        try:
            priority = candidate.priority
        except Exception:
            _raise(
                "credential-backend-unavailable",
                "No supported credential backend is available.",
                "Install and configure a secure operating-system keyring backend.",
            )
        if isinstance(priority, bool) or not isinstance(priority, int | float) or priority < 1:
            _raise(
                "credential-backend-unavailable",
                "No supported credential backend is available.",
                "Install and configure a secure operating-system keyring backend.",
            )

        declares_backends = any("backends" in base.__dict__ for base in backend_type.__mro__)
        if "chainer" in name or "chainer" in module or declares_backends:
            try:
                delegated = candidate.backends
            except Exception:
                _raise(
                    "credential-backend-unavailable",
                    "No supported credential backend is available.",
                    "Install and configure a secure operating-system keyring backend.",
                )
            if not isinstance(delegated, list | tuple):
                _raise(
                    "credential-backend-unavailable",
                    "No supported credential backend is available.",
                    "Install and configure a secure operating-system keyring backend.",
                )
            pending.extend(delegated)
    return False


def _backend(keyring: Any) -> Any:
    try:
        backend = keyring.get_keyring()
        priority = backend.priority
    except Exception:
        _raise(
            "credential-backend-unavailable",
            "No supported credential backend is available.",
            "Install and configure a secure operating-system keyring backend.",
        )
    if isinstance(priority, bool) or not isinstance(priority, int | float) or priority < 1:
        _raise(
            "credential-backend-unavailable",
            "No supported credential backend is available.",
            "Install and configure a secure operating-system keyring backend.",
        )
    if _contains_insecure_backend(backend):
        _raise(
            "credential-backend-insecure",
            "The selected credential backend is not secure enough for credentials.",
            "Configure an operating-system credential backend with protected storage.",
        )
    return backend


def _terminate_helper(process: subprocess.Popen[bytes], windows_job: _WindowsJob | None = None) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            with contextlib.suppress(OSError):
                process.kill()
    elif os.name == "nt":
        if windows_job is None:
            with contextlib.suppress(OSError):
                process.kill()
        else:
            if not windows_job.terminate():
                with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=_HELPER_REAP_TIMEOUT_SECONDS,
                        check=False,
                        shell=False,
                    )
            windows_job.close()
    else:
        with contextlib.suppress(OSError):
            process.kill()

    try:
        process.wait(timeout=_HELPER_REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_HELPER_REAP_TIMEOUT_SECONDS)


def _capture_bounded(
    stream: Any,
    limit: int,
    output: bytearray,
    overflow: threading.Event,
    failed: threading.Event,
    completed: threading.Event,
) -> None:
    try:
        while True:
            chunk = stream.read1(min(8192, limit - len(output) + 1))
            if not chunk:
                return
            remaining = limit - len(output)
            output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()
                return
    except Exception:
        failed.set()
    finally:
        try:
            stream.close()
        except Exception:
            failed.set()
        finally:
            completed.set()


def _username(config: dict[str, Any]) -> str:
    username = config.get("account", {}).get("username")
    if not isinstance(username, str) or not username:
        _raise(
            "credentials-username-missing",
            "A username is required for this credential provider.",
            "Set account.username in the configuration file.",
        )
    return username


def _helper_command(config: dict[str, Any]) -> list[str]:
    command = config.get("credentials", {}).get("command")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        _raise(
            "credential-helper-invalid",
            "The credential helper is not configured correctly.",
            "Set credentials.command to an absolute executable path and optional arguments.",
        )
    if sys.platform == "win32" and PureWindowsPath(command[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        _raise(
            "credential-helper-invalid",
            "A Windows script helper must be started by an explicit interpreter.",
            "Use an argv list beginning with powershell.exe -File or cmd.exe /c, followed by the script path.",
        )
    return command


def _run_helper(config: dict[str, Any]) -> tuple[str, str]:
    command = _helper_command(config)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    if os.name == "nt":
        creationflags |= 0x00000004  # CREATE_SUSPENDED
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name == "posix",
            creationflags=creationflags,
        )
    except (OSError, ValueError, UnicodeError):
        _raise(
            "credential-helper-failed",
            "The credential helper could not be run.",
            "Check the configured helper and retry.",
            "error",
        )

    windows_job = None
    if os.name == "nt":
        try:
            windows_job = _WindowsJob(process)
            windows_job.resume(process)
        except Exception:
            _terminate_helper(process, windows_job)
            _raise(
                "credential-helper-failed",
                "The credential helper could not be safely isolated.",
                "Check the Windows process security policy and retry.",
                "error",
            )
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    capture_failed = threading.Event()
    completed = [threading.Event(), threading.Event()]
    threads = [
        threading.Thread(
            target=_capture_bounded,
            args=(process.stdout, HELPER_STDOUT_LIMIT, stdout, overflow, capture_failed, completed[0]),
            daemon=True,
        ),
        threading.Thread(
            target=_capture_bounded,
            args=(process.stderr, HELPER_STDERR_LIMIT, stderr, overflow, capture_failed, completed[1]),
            daemon=True,
        ),
    ]
    started: list[threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started.append(thread)
    except RuntimeError:
        _terminate_helper(process, windows_job)
        for thread in started:
            thread.join(_HELPER_REAP_TIMEOUT_SECONDS)
        _raise(
            "credential-helper-failed",
            "The credential helper could not be run.",
            "Check the configured helper and retry.",
            "error",
        )

    deadline = time.monotonic() + HELPER_TIMEOUT_SECONDS
    timed_out = False
    while True:
        if overflow.is_set() or capture_failed.is_set():
            break
        if process.poll() is not None and all(event.is_set() for event in completed):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        overflow.wait(min(remaining, 0.02))

    if timed_out or overflow.is_set() or capture_failed.is_set():
        _terminate_helper(process, windows_job)
        for thread in threads:
            thread.join(_HELPER_REAP_TIMEOUT_SECONDS)
    elif windows_job is not None:
        windows_job.close()
    if timed_out:
        _raise(
            "credential-helper-failed",
            "The credential helper timed out.",
            "Check the helper and retry.",
            "error",
        )
    if overflow.is_set():
        _raise(
            "credential-helper-failed",
            "The credential helper exceeded its output limit.",
            "Check the helper and retry.",
            "error",
        )
    if capture_failed.is_set():
        _raise(
            "credential-helper-failed",
            "The credential helper could not be run.",
            "Check the configured helper and retry.",
            "error",
        )
    if process.returncode != 0:
        _raise(
            "credential-helper-failed",
            f"The credential helper exited with code {process.returncode}.",
            "Check the helper and retry.",
            "error",
        )
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError, TypeError):
        _raise(
            "credential-helper-failed",
            "The credential helper returned invalid data.",
            "Return JSON containing username and password fields.",
            "error",
        )
    if not isinstance(data, dict):
        _raise(
            "credential-helper-failed",
            "The credential helper returned invalid data.",
            "Return JSON containing username and password fields.",
            "error",
        )
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        _raise(
            "credential-helper-failed",
            "The credential helper returned invalid data.",
            "Return non-empty username and password string fields.",
            "error",
        )
    configured_username = config.get("account", {}).get("username")
    if configured_username and username != configured_username:
        _raise(
            "credential-helper-failed",
            "The credential helper returned a username that does not match the configured account.",
            "Check the helper account and account.username setting.",
            "error",
        )
    return username, password


def get_credentials(config: dict[str, Any]) -> tuple[str, str]:
    provider = config.get("credentials", {}).get("provider", "keyring")
    if provider == "command":
        return _run_helper(config)
    username = _username(config)
    keyring = _keyring_module()
    _backend(keyring)
    try:
        password = keyring.get_password(KEYRING_SERVICE, username)
    except Exception:
        _raise(
            "credential-backend-unavailable",
            "Credentials could not be read from the configured secure backend.",
            "Check the operating-system credential backend.",
        )
    if not isinstance(password, str) or not password:
        _raise(
            "credentials-not-configured",
            "No saved password is available for this account.",
            "Run 'campusctl auth set' to save credentials.",
        )
    return username, password


def store_keyring_password(config: dict[str, Any], password: str) -> None:
    username = _username(config)
    keyring = _keyring_module()
    _backend(keyring)
    try:
        keyring.set_password(KEYRING_SERVICE, username, password)
    except Exception:
        _raise(
            "credential-backend-unavailable",
            "Credentials could not be saved to the configured secure backend.",
            "Check the operating-system credential backend.",
        )


def keyring_status(config: dict[str, Any]) -> tuple[str, bool]:
    keyring = _keyring_module()
    backend = _backend(keyring)
    username = config.get("account", {}).get("username")
    if not username:
        return type(backend).__name__, False
    try:
        configured = bool(keyring.get_password(KEYRING_SERVICE, username))
    except Exception:
        _raise(
            "credential-backend-unavailable",
            "Credentials could not be checked in the configured secure backend.",
            "Check the operating-system credential backend.",
        )
    return type(backend).__name__, configured


def helper_status(config: dict[str, Any], *, check: bool) -> tuple[str, bool, str | None]:
    command = _helper_command(config)
    basename = Path(command[0]).name
    if not check:
        return basename, True, None
    try:
        _run_helper(config)
    except CampusError:
        return basename, True, "failed"
    return basename, True, "ok"


def prompt_and_store(config: dict[str, Any], *, stdin_isatty: bool) -> None:
    if config.get("credentials", {}).get("provider", "keyring") != "keyring":
        _raise(
            "auth-command-provider",
            "This configuration uses a credential helper; campusctl does not store its password.",
            "Configure the helper to provide credentials and use 'campusctl auth status --check'.",
        )
    if not stdin_isatty:
        _raise(
            "auth-tty-required",
            "Saving a password requires an interactive terminal.",
            "Run 'campusctl auth set' from a terminal.",
        )
    password = getpass.getpass("LMS password: ")
    if not password:
        _raise("auth-empty-password", "The password cannot be empty.", "Run 'campusctl auth set' and enter a password.")
    store_keyring_password(config, password)
    # Drop the local reference as soon as the backend has accepted the value.
    password = ""
