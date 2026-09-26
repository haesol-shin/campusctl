from __future__ import annotations

import contextlib
import errno
import os
from collections.abc import Iterator
from pathlib import Path

from campusctl.envelope import CampusError

try:
    import fcntl

    def _lock_file(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

except ImportError:
    import msvcrt

    def _lock_file(fd: int) -> None:
        # msvcrt.locking locks bytes starting at the current offset; lock a real, stable byte at offset zero.
        if os.fstat(fd).st_size == 0:
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_file(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def exclusive_lock(path: Path | str) -> Iterator[None]:
    target = Path(path)
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise CampusError(
            "lock-unavailable",
            "The browser session lock directory could not be prepared.",
            "Check the lock path and permissions.",
        ) from None
    try:
        fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        raise CampusError(
            "lock-unavailable", "The browser session lock could not be opened.", "Check the lock path and permissions."
        ) from None
    try:
        try:
            _lock_file(fd)
        except (BlockingIOError, PermissionError):
            raise CampusError(
                "session-busy",
                "Another campusctl operation holds the browser session lock.",
                "Wait for the other operation to finish, then retry.",
                "busy",
            ) from None
        except OSError as exc:
            if exc.errno in {errno.EAGAIN, errno.EACCES, errno.EDEADLK}:
                raise CampusError(
                    "session-busy",
                    "Another campusctl operation holds the browser session lock.",
                    "Wait for the other operation to finish, then retry.",
                    "busy",
                ) from None
            raise CampusError(
                "lock-unavailable",
                "The browser session lock could not be acquired.",
                "Check the lock path and permissions.",
            ) from None
        try:
            yield
        finally:
            _unlock_file(fd)
    finally:
        os.close(fd)
