"""Safe, no-clobber material publication and private verified retry receipts."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import ntpath
import os
import re
import secrets
import stat
import sys
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, NamedTuple

from campusctl.envelope import CampusError

_MAX_COMPONENT_BYTES = 200
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I)
_PUBLISH_TEMP_PREFIX = ".material-"
_PUBLISH_TEMP_SUFFIX = ".tmp"
_PUBLISH_TEMP_TOKEN_BYTES = 16
_PUBLISH_TEMP_NAME_LENGTH = len(_PUBLISH_TEMP_PREFIX) + 2 * _PUBLISH_TEMP_TOKEN_BYTES + len(_PUBLISH_TEMP_SUFFIX)


class PublishedAttachment(NamedTuple):
    path: Path
    outcome: Literal["saved", "reused"]


def _windows_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _prefix(text: str, budget: int, char_budget: int | None = None) -> str:
    used = 0
    chars = 0
    result = []
    for character in text:
        length = len(character.encode("utf-8"))
        char_length = _windows_length(character) if char_budget is not None else 0
        if used + length > budget or (char_budget is not None and chars + char_length > char_budget):
            break
        result.append(character)
        used += length
        chars += char_length
    return "".join(result)


def _safe_component(name: str, suffix: str, max_chars: int | None = None) -> str:
    if not isinstance(name, str) or not isinstance(suffix, str) or not re.fullmatch(r"[~a-zA-Z0-9_-]*", suffix):
        raise ValueError("invalid output component or suffix")
    basename = unicodedata.normalize("NFC", name.replace("\\", "/").split("/")[-1])
    basename = "".join("_" if ord(ch) < 32 or ord(ch) == 127 or ch in '<>:"|?*' else ch for ch in basename)
    basename = basename.rstrip(" .")
    if not basename or basename in {".", ".."} or _DEVICE.fullmatch(basename):
        basename = "resource"
    dot = basename.rfind(".")
    stem, extension = (basename[:dot], basename[dot:]) if dot > 0 else (basename, "")
    stem = stem or "resource"
    first = stem[0]
    remaining = _MAX_COMPONENT_BYTES - len(suffix.encode()) - len(first.encode("utf-8"))
    char_remaining = None if max_chars is None else max_chars - _windows_length(suffix + first)
    if remaining < 0:
        raise ValueError("suffix exceeds output name limit")
    if char_remaining is not None and char_remaining < 0:
        raise _path_too_long()
    extension = _prefix(extension, remaining, char_remaining).rstrip(".")
    remaining -= len(extension.encode("utf-8"))
    if char_remaining is not None:
        char_remaining -= _windows_length(extension)
    result = (
        _prefix(
            stem,
            remaining + len(first.encode("utf-8")),
            None if char_remaining is None else char_remaining + _windows_length(first),
        )
        + suffix
        + extension
    )
    return unicodedata.normalize("NFC", result)


def safe_component(name: str, *, suffix: str = "") -> str:
    """Return one NFC basename, with suffix and extension inside a 200-byte bound."""
    return _safe_component(name, suffix)


def _path_too_long() -> CampusError:
    return CampusError(
        "output-path-too-long",
        "Output directory leaves insufficient room for the attachment filename.",
        "Choose a shorter output directory with --out.",
        "user-action",
    )


def _home_dir() -> Path:
    return Path.home()


def _windows_downloads() -> Path:
    """Ask the shell for the redirected Downloads known folder."""
    folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le)
    destination = ctypes.c_wchar_p()
    try:
        shell = ctypes.windll.shell32
        shell.SHGetKnownFolderPath.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(destination)) != 0:
            raise OSError("Downloads known folder unavailable")
        try:
            if not destination.value:
                raise OSError("Downloads known folder empty")
            return Path(destination.value)
        finally:
            free = ctypes.windll.ole32.CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = None
            free(destination)
    except (AttributeError, OSError):
        return Path(os.environ.get("USERPROFILE") or _home_dir()) / "Downloads"


def default_download_dir() -> Path:
    """Return the platform Downloads folder's campusctl directory."""
    if sys.platform == "win32":
        downloads = _windows_downloads()
    elif sys.platform == "darwin":
        downloads = _home_dir() / "Downloads"
    else:
        home = _home_dir()
        downloads = home / "Downloads"
        config_home = os.environ.get("XDG_CONFIG_HOME")
        config_dir = Path(config_home) if config_home and Path(config_home).is_absolute() else home / ".config"
        try:
            config = (config_dir / "user-dirs.dirs").read_text(encoding="utf-8")
        except OSError:
            pass
        else:
            for line in config.splitlines():
                match = re.fullmatch(r'\s*XDG_DOWNLOAD_DIR\s*=\s*"([^"]*)"\s*(?:#.*)?', line)
                if match:
                    value = match.group(1).replace("${HOME}", str(home)).replace("$HOME", str(home))
                    if value and Path(value).is_absolute():
                        downloads = Path(value)
                    break
    return downloads / "campusctl"


_ENV_REF = re.compile(r"\$([A-Za-z_][A-Za-z_0-9]*)|\$\{([A-Za-z_][A-Za-z_0-9]*)\}|%([A-Za-z_][A-Za-z_0-9]*)%")
_SCAN_ENTRIES = 10_000
_SCAN_BYTES = 1_000_000_000


def _layout_invalid(key: str) -> CampusError:
    return CampusError(
        "config-invalid",
        f"Invalid configuration (key: {key}).",
        "Edit the named configuration key using the documented format.",
        "user-action",
    )


def _layout_component(raw: str, key: str, *, course_label: bool = False) -> str:
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or raw.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", raw)
        or any(unicodedata.category(ch) == "Cc" for ch in raw)
        or any(part in {".", ".."} for part in re.split(r"[/\\]", raw))
        or (not course_label and ("/" in raw or "\\" in raw))
    ):
        raise _layout_invalid(key)
    return safe_component(raw.replace("/", "_").replace("\\", "_") if course_label else raw)


def _expand_layout(template: str) -> str:
    if template.startswith("~"):
        if not template.startswith(("~/", "~\\")):
            raise _layout_invalid("materials.download_dir")
        template = str(_home_dir()) + template[1:]

    def substitute(match: re.Match[str]) -> str:
        if match.group(3) and sys.platform != "win32":
            raise _layout_invalid("materials.download_dir")
        value = os.environ.get(match.group(1) or match.group(2) or match.group(3))
        if not value:
            raise _layout_invalid("materials.download_dir")
        if ("/" in value or "\\" in value) and (
            match.start() != 0
            or (match.end() < len(template) and template[match.end()] not in "/\\")
            or not (value.startswith("/") if sys.platform != "win32" else ntpath.isabs(value))
        ):
            raise _layout_invalid("materials.download_dir")
        return value

    expanded = _ENV_REF.sub(substitute, template)
    if "$" in expanded or (sys.platform == "win32" and "%" in expanded):
        raise _layout_invalid("materials.download_dir")
    return expanded


def _layout_parts(template: str) -> tuple[str, list[str]]:
    from campusctl.config import _material_component

    expanded = _expand_layout(template)
    if sys.platform == "win32":
        import ntpath

        anchor, remainder = ntpath.splitdrive(expanded)
        if not anchor or not remainder.startswith(("/", "\\")):
            raise _layout_invalid("materials.download_dir")
        parts = re.split(r"[/\\]", remainder[1:])
        prefix = anchor + "\\"
    else:
        if not expanded.startswith("/"):
            raise _layout_invalid("materials.download_dir")
        parts = expanded[1:].split("/")
        prefix = "/"
    if expanded.rstrip("/\\") == prefix.rstrip("/\\"):
        return prefix, []
    if not parts or any(not part for part in parts):
        raise _layout_invalid("materials.download_dir")
    for index, part in enumerate(parts):
        if part in ("{course}", "{course_id}", "{semester}"):
            continue
        if "{" in part or "}" in part:
            raise _layout_invalid("materials.download_dir")
        _material_component(part, "materials.download_dir", Path("config.toml"))
        parts[index] = unicodedata.normalize("NFC", part)
    return prefix, parts


def check_download_environment(config: dict[str, Any]) -> None:
    template = config.get("materials", {}).get("download_dir")
    if template is not None:
        _layout_parts(template)


def resolve_download_layout(config: dict[str, Any], row: dict[str, Any], out: Path | None) -> tuple[Path, Path | None]:
    """Resolve a validated course destination without creating any directories."""
    if out is not None:
        return Path(os.path.abspath(out.expanduser())), None
    settings = config.get("materials", {})
    template = settings.get("download_dir")
    if template is None:
        return default_download_dir() / safe_component(row["course"]["label"]), None
    prefix, parts = _layout_parts(template)
    values = {
        "{course}": ("materials.download_dir", row["course"]["label"], True),
        "{course_id}": ("materials.download_dir", row["course"]["id"], False),
        "{semester}": ("materials.semester", settings.get("semester"), False),
    }
    resolved: list[str] = []
    course_root = None
    for part in parts:
        if part in values:
            key, value, label = values[part]
            part = _layout_component(value, key, course_label=label)
        resolved.append(part)
        if settings.get("adopt_existing") and len(resolved) == next(
            (i + 1 for i, component in enumerate(parts) if component in ("{course}", "{course_id}")), -1
        ):
            course_root = Path(prefix, *resolved)
    destination = Path(prefix, *resolved)
    if (
        course_root is not None
        and len(parts) - next(i + 1 for i, part in enumerate(parts) if part in ("{course}", "{course_id}")) > 4
    ):
        raise _layout_invalid("materials.download_dir")
    return destination, course_root


def _conflict() -> CampusError:
    return CampusError(
        "output-path-conflict",
        "Output path is not a safe regular file or directory.",
        "Choose another output directory or filename.",
        "user-action",
    )


def _directory_identity(path: Path) -> tuple[int, int]:
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except OSError:
            raise _conflict() from None
        if not stat.S_ISDIR(info.st_mode):
            raise _conflict()
        if os.name == "nt" and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise _conflict()
    info = path.lstat()
    return info.st_dev, info.st_ino


def _check_directory(path: Path, *, create: bool = False) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    if create and os.name != "nt":
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            parent_fd = os.open(absolute.anchor, flags)
            try:
                for component in absolute.parts[1:]:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(component, mode=0o700, dir_fd=parent_fd)
                    child_fd = os.open(component, flags, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = child_fd
            finally:
                os.close(parent_fd)
        except OSError:
            raise _conflict() from None
    elif create:
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current /= component
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                raise _conflict() from None
            _directory_identity(current)
    _directory_identity(absolute)
    return absolute


@contextmanager
def _pinned_directory(directory: Path):
    identity = _directory_identity(directory)
    if os.name == "nt":
        yield None, identity
        if _directory_identity(directory) != identity:
            raise _conflict()
    else:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(directory, flags)
        except OSError:
            raise _conflict() from None
        try:
            if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != identity:
                raise _conflict()
            yield fd, identity
            if _directory_identity(directory) != identity:
                raise _conflict()
        finally:
            os.close(fd)


def prepare_output_dir(root: Path, entity_id: str, out: Path | None = None) -> Path:
    """Create the requested directory privately (default is keyed by the full ID)."""
    if not entity_id:
        raise ValueError("entity ID is required")
    directory = out if out is not None else root / "materials" / hashlib.sha256(entity_id.encode("utf-8")).hexdigest()
    if (
        sys.platform == "win32"
        and _windows_length(os.path.abspath(directory.expanduser())) + 1 + _PUBLISH_TEMP_NAME_LENGTH > 250
    ):
        raise _path_too_long()
    return _check_directory(directory, create=True)


def _open_regular(path: Path | str, *, dir_fd: int | None = None) -> int:
    descriptor = {} if dir_fd is None else {"dir_fd": dir_fd}
    before = os.stat(path, follow_symlinks=False, **descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise _conflict()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, **descriptor)
    try:
        opened = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False, **descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise _conflict()
    except BaseException:
        os.close(fd)
        raise
    return fd


def _digest_fd(fd: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(fd, "rb", closefd=False) as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _digest_file(path: Path) -> tuple[int, str]:
    fd = _open_regular(path)
    try:
        return _digest_fd(fd)
    finally:
        os.close(fd)


def _same_bytes(source_fd: int, right: Path | str, *, dir_fd: int | None = None) -> bool:
    right_fd = _open_regular(right, dir_fd=dir_fd)
    try:
        if os.fstat(source_fd).st_size != os.fstat(right_fd).st_size:
            return False
        os.lseek(source_fd, 0, os.SEEK_SET)
        while True:
            lhs = os.read(source_fd, 1024 * 1024)
            rhs = os.read(right_fd, 1024 * 1024)
            if lhs != rhs:
                return False
            if not lhs:
                return True
    finally:
        os.close(right_fd)


def _verify_temp_identity(source_fd: int, name: Path | str, *, dir_fd: int | None) -> None:
    """Refuse a name swapped away from the exact inode hashed via source_fd."""
    try:
        opened = os.fstat(source_fd)
        named = os.stat(name, follow_symlinks=False, **({} if dir_fd is None else {"dir_fd": dir_fd}))
    except OSError:
        raise _conflict() from None
    if (opened.st_dev, opened.st_ino, opened.st_size) != (named.st_dev, named.st_ino, named.st_size):
        raise _conflict()


def _candidates(directory: Path, name: str, digest: str) -> tuple[Path, Path | None]:
    if sys.platform == "win32":
        available = 250 - _windows_length(str(directory)) - 1
        primary = directory / _safe_component(name, "", available)
        try:
            alternate = directory / _safe_component(name, "~" + digest, available)
        except CampusError as error:
            if error.code != "output-path-too-long":
                raise
            alternate = None
    else:
        primary = directory / safe_component(name)
        alternate = directory / safe_component(name, suffix="~" + digest)
    return primary, alternate


def _copy_verified(source: int, destination: int, size_bytes: int, sha256: str) -> None:
    """Hash exactly the bytes written to the private publication inode."""
    digest = hashlib.sha256()
    copied = 0
    while chunk := os.read(source, 1024 * 1024):
        copied += len(chunk)
        if copied > size_bytes:
            raise _conflict()
        view = memoryview(chunk)
        while view:
            written = os.write(destination, view)
            if written <= 0:
                raise _conflict()
            view = view[written:]
        digest.update(chunk)
    if copied != size_bytes or digest.hexdigest() != sha256:
        raise _conflict()
    os.fsync(destination)


def publish_attachment(
    temp_path: Path, output_dir: Path, filename: str, sha256: str, size_bytes: int
) -> PublishedAttachment:
    """Copy a same-directory transfer into an exclusive, byte-verified inode.

    The open source inode cannot change the bytes later linked into the final path:
    publication links only the new private temp whose copied bytes were hashed.
    """
    temp_path = Path(temp_path)
    directory = _check_directory(output_dir)
    if temp_path.parent.absolute() != directory:
        raise _conflict()
    with _pinned_directory(directory) as (dir_fd, identity):
        name = temp_path.name
        source: int | None = None
        copied: int | None = None
        copy_name: str | None = None
        copy_created = False
        copy_identity: tuple[int, int] | None = None
        source_identity: tuple[int, int] | None = None
        try:
            source = _open_regular(name if dir_fd is not None else temp_path, dir_fd=dir_fd)
            info = os.fstat(source)
            source_identity = info.st_dev, info.st_ino
            if (
                not isinstance(sha256, str)
                or not _DIGEST.fullmatch(sha256)
                or type(size_bytes) is not int
                or size_bytes <= 0
            ):
                raise _conflict()
            try:
                primary, alternate = _candidates(directory, filename, sha256)
            except (ValueError, TypeError):
                raise _conflict() from None
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            for _ in range(8):
                copy_name = _PUBLISH_TEMP_PREFIX + secrets.token_hex(_PUBLISH_TEMP_TOKEN_BYTES) + _PUBLISH_TEMP_SUFFIX
                copy_path = directory / copy_name
                target = copy_name if dir_fd is not None else copy_path
                try:
                    copied = os.open(target, flags, 0o600, **({} if dir_fd is None else {"dir_fd": dir_fd}))
                except FileExistsError:
                    continue
                except OSError:
                    raise _conflict() from None
                copy_created = True
                info = os.fstat(copied)
                copy_identity = info.st_dev, info.st_ino
                break
            else:
                raise _conflict()
            try:
                _copy_verified(source, copied, size_bytes, sha256)
            except OSError:
                raise _conflict() from None
            _verify_temp_identity(source, name if dir_fd is not None else temp_path, dir_fd=dir_fd)
            for index, candidate in enumerate((primary, alternate)):
                if candidate is None:
                    raise _path_too_long()
                if dir_fd is None and _directory_identity(directory) != identity:
                    raise _conflict()
                occupied = [
                    entry
                    for entry in os.listdir(dir_fd if dir_fd is not None else directory)
                    if unicodedata.normalize("NFC", entry).casefold() == candidate.name.casefold()
                ]
                if occupied:
                    if len(occupied) != 1:
                        raise _conflict()
                    existing = occupied[0] if dir_fd is not None else directory / occupied[0]
                    if occupied[0] != candidate.name:
                        if index == 0:
                            continue
                        raise _conflict()
                    try:
                        identical = _same_bytes(copied, existing, dir_fd=dir_fd)
                    except OSError:
                        raise _conflict() from None
                    if identical:
                        return PublishedAttachment(candidate, "reused")
                    if index == 0:
                        continue
                    raise _conflict()
                copy_stat = os.fstat(copied)
                source_path = copy_name if dir_fd is not None else copy_path
                try:
                    named = os.stat(
                        source_path, follow_symlinks=False, **({} if dir_fd is None else {"dir_fd": dir_fd})
                    )
                except OSError:
                    raise _conflict() from None
                if not stat.S_ISREG(named.st_mode) or (named.st_dev, named.st_ino) != (
                    copy_stat.st_dev,
                    copy_stat.st_ino,
                ):
                    raise _conflict()
                try:
                    if dir_fd is None:
                        os.link(copy_path, candidate, follow_symlinks=False)
                    else:
                        os.link(copy_name, candidate.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
                except OSError:
                    raise _conflict() from None
                destination_path = candidate.name if dir_fd is not None else candidate
                try:
                    published = os.stat(
                        destination_path, follow_symlinks=False, **({} if dir_fd is None else {"dir_fd": dir_fd})
                    )
                except OSError:
                    raise _conflict() from None
                if not stat.S_ISREG(published.st_mode) or (published.st_dev, published.st_ino) != (
                    copy_stat.st_dev,
                    copy_stat.st_ino,
                ):
                    # If the source name was swapped during link, remove only the
                    # link to that observed inode, never another publisher's path.
                    with contextlib.suppress(OSError):
                        linked_source = os.stat(
                            source_path, follow_symlinks=False, **({} if dir_fd is None else {"dir_fd": dir_fd})
                        )
                        current = os.stat(
                            destination_path, follow_symlinks=False, **({} if dir_fd is None else {"dir_fd": dir_fd})
                        )
                        if (linked_source.st_dev, linked_source.st_ino) == (current.st_dev, current.st_ino) == (
                            published.st_dev,
                            published.st_ino,
                        ) and (dir_fd is not None or _directory_identity(directory) == identity):
                            os.unlink(destination_path, **({} if dir_fd is None else {"dir_fd": dir_fd}))
                    raise _conflict()
                if dir_fd is None and _directory_identity(directory) != identity:
                    raise _conflict()
                if dir_fd is not None:
                    os.fsync(dir_fd)
                return PublishedAttachment(candidate, "saved")
            raise _conflict()
        finally:
            if copied is not None:
                os.close(copied)
            if source is not None:
                os.close(source)
            if copy_created and copy_name is not None and copy_identity is not None:
                with contextlib.suppress(OSError):
                    if dir_fd is not None or _directory_identity(directory) == identity:
                        copy_path = copy_name if dir_fd is not None else directory / copy_name
                        current = os.stat(
                            copy_path, follow_symlinks=False, **({} if dir_fd is None else {"dir_fd": dir_fd})
                        )
                        if (current.st_dev, current.st_ino) == copy_identity:
                            os.unlink(copy_path, **({} if dir_fd is None else {"dir_fd": dir_fd}))
            if source_identity is not None:
                with contextlib.suppress(OSError):
                    if dir_fd is not None or _directory_identity(directory) == identity:
                        current = os.stat(
                            name if dir_fd is not None else temp_path,
                            follow_symlinks=False,
                            **({} if dir_fd is None else {"dir_fd": dir_fd}),
                        )
                        if (current.st_dev, current.st_ino) == source_identity:
                            os.unlink(
                                name if dir_fd is not None else temp_path,
                                **({} if dir_fd is None else {"dir_fd": dir_fd}),
                            )


def _scan_conflict() -> CampusError:
    return CampusError(
        "output-path-conflict",
        "Course material scan could not safely verify existing files within its limits.",
        "Remove unsafe entries or narrow the course directory and retry.",
        "user-action",
    )


def adopt_attachment(
    temp_path: Path, output_dir: Path, course_root: Path, filename: str, size_bytes: int, sha256: str
) -> PublishedAttachment | None:
    """Compare a verified transfer with bounded, pinned existing course files."""
    if os.name == "nt":
        raise _scan_conflict()
    try:
        directory = _check_directory(output_dir)
        root = _check_directory(course_root)
        if not directory.is_relative_to(root) or len(directory.relative_to(root).parts) > 4:
            raise _scan_conflict()
        primary, _ = _candidates(directory, filename, sha256)
        with _pinned_directory(root) as (root_fd, _):
            assert root_fd is not None
            candidates: list[tuple[Path, tuple[str, str, str]]] = []
            entries = 0

            def walk(fd: int, relative: Path, depth: int) -> None:
                nonlocal entries
                for name in sorted(os.listdir(fd)):
                    entries += 1
                    if entries > _SCAN_ENTRIES:
                        raise _scan_conflict()
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    path = relative / name
                    if stat.S_ISLNK(info.st_mode):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if depth < 4:
                            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                            try:
                                if (os.fstat(child).st_dev, os.fstat(child).st_ino) != (info.st_dev, info.st_ino):
                                    raise _scan_conflict()
                                walk(child, path, depth + 1)
                                named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                                if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                                    raise _scan_conflict()
                            finally:
                                os.close(child)
                    elif (
                        stat.S_ISREG(info.st_mode)
                        and unicodedata.normalize("NFC", name).casefold() == primary.name.casefold()
                    ):
                        text = path.as_posix()
                        normalized = unicodedata.normalize("NFC", text)
                        candidates.append((root / path, (normalized.casefold(), normalized, text)))

            walk(root_fd, Path(), 0)
            exact = [item for item in candidates if item[0] == primary]
            inside = sorted(
                (item for item in candidates if item[0].parent == directory and item[0] != primary),
                key=lambda item: item[1],
            )
            outside = sorted((item for item in candidates if item[0].parent != directory), key=lambda item: item[1])
            read_bytes = 0

            def candidate_read(fd: int, count: int) -> bytes:
                nonlocal read_bytes
                if count > _SCAN_BYTES - read_bytes:
                    raise _scan_conflict()
                data = os.read(fd, count)
                read_bytes += len(data)
                if len(data) != count:
                    raise _scan_conflict()
                return data

            with _pinned_directory(directory) as (source_dir_fd, _):
                source_fd = _open_regular(temp_path.name, dir_fd=source_dir_fd)
                try:
                    _verify_temp_identity(source_fd, temp_path.name, dir_fd=source_dir_fd)
                    if os.fstat(source_fd).st_size != size_bytes or _digest_fd(source_fd) != (size_bytes, sha256):
                        raise _scan_conflict()
                    _verify_temp_identity(source_fd, temp_path.name, dir_fd=source_dir_fd)
                    for candidate, _ in exact + inside + outside:
                        with _pinned_directory(candidate.parent) as (candidate_dir_fd, _):
                            fd = _open_regular(candidate.name, dir_fd=candidate_dir_fd)
                            try:
                                if os.fstat(fd).st_size != size_bytes:
                                    continue
                                sample = min(size_bytes, 64 * 1024)
                                os.lseek(fd, 0, os.SEEK_SET)
                                os.lseek(source_fd, 0, os.SEEK_SET)
                                identical = candidate_read(fd, sample) == os.read(source_fd, sample)
                                if identical:
                                    end_offset = sample if size_bytes <= 128 * 1024 else size_bytes - sample
                                    os.lseek(fd, end_offset, os.SEEK_SET)
                                    os.lseek(source_fd, end_offset, os.SEEK_SET)
                                    remaining = size_bytes - end_offset
                                    identical = candidate_read(fd, remaining) == os.read(source_fd, remaining)
                                if identical and size_bytes > 128 * 1024:
                                    os.lseek(fd, 0, os.SEEK_SET)
                                    os.lseek(source_fd, 0, os.SEEK_SET)
                                    while os.lseek(fd, 0, os.SEEK_CUR) < size_bytes:
                                        remaining = size_bytes - os.lseek(fd, 0, os.SEEK_CUR)
                                        chunk = candidate_read(fd, min(1024 * 1024, remaining))
                                        if chunk != os.read(source_fd, len(chunk)):
                                            identical = False
                                            break
                                if os.fstat(fd).st_size != size_bytes:
                                    raise _scan_conflict()
                                _verify_temp_identity(fd, candidate.name, dir_fd=candidate_dir_fd)
                                if identical:
                                    _verify_temp_identity(source_fd, temp_path.name, dir_fd=source_dir_fd)
                                    return PublishedAttachment(
                                        candidate, "reused" if candidate.parent == directory else "adopted"
                                    )
                            finally:
                                os.close(fd)
                finally:
                    os.close(source_fd)
    except (OSError, CampusError) as error:
        if isinstance(error, CampusError) and error.code == "output-path-too-long":
            raise
        raise _scan_conflict() from None
    return None


def receipts_path(root: Path) -> Path:
    return root / "materials" / "receipts.json"


def _url_bearing(value: str) -> bool:
    return re.search(r"[A-Za-z][A-Za-z0-9+.-]*://", value) is not None


def _valid_receipt_item(item: Any, version: int) -> bool:
    fields = {"entity_id", "output_dir", "path", "size_bytes", "sha256", "observed_mime"}
    if not isinstance(item, dict) or set(item) != (fields | {"course_root"} if version == 2 else fields):
        return False
    entity_id, output_dir, path, digest, mime = (
        item["entity_id"],
        item["output_dir"],
        item["path"],
        item["sha256"],
        item["observed_mime"],
    )
    return (
        isinstance(entity_id, str)
        and bool(entity_id)
        and not _url_bearing(entity_id)
        and isinstance(mime, (str, type(None)))
        and (mime is None or not _url_bearing(mime))
        and isinstance(output_dir, str)
        and Path(output_dir).is_absolute()
        and isinstance(path, str)
        and Path(path).is_absolute()
        and ".." not in Path(path).parts
        and (
            (Path(path).parent == Path(output_dir) and (version == 1 or item["course_root"] is None))
            or (
                version == 2
                and isinstance(item["course_root"], str)
                and Path(item["course_root"]).is_absolute()
                and ".." not in Path(item["course_root"]).parts
                and Path(path).is_relative_to(Path(item["course_root"]))
                and 1 <= len(Path(path).relative_to(Path(item["course_root"])).parts) <= 5
            )
        )
        and isinstance(digest, str)
        and _DIGEST.fullmatch(digest) is not None
        and type(item["size_bytes"]) is int
        and item["size_bytes"] > 0
    )


def _read_receipts(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise _conflict()
    try:
        fd = _open_regular(path)
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            data = json.load(source)
    except FileNotFoundError:
        return []
    except (OSError, ValueError, UnicodeError):
        raise CampusError(
            "output-path-conflict",
            "Materials receipts cannot be read safely.",
            "Check the private materials directory.",
            "user-action",
        ) from None
    if (
        not isinstance(data, dict)
        or data.get("schema_version") not in (1, 2)
        or not isinstance(data.get("items"), list)
        or not all(_valid_receipt_item(item, data["schema_version"]) for item in data["items"])
    ):
        raise _conflict()
    return data["items"]


def _receipt_file(path: Path, directory: Path, size: int, digest: str) -> bool:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or ".." in directory.parts
        or not path.is_relative_to(directory)
        or not 1 <= len(path.relative_to(directory).parts) <= 5
        or not _DIGEST.fullmatch(digest)
        or type(size) is not int
        or size <= 0
    ):
        return False
    try:
        with _pinned_directory(path.parent) as (dir_fd, _):
            fd = _open_regular(path.name if dir_fd is not None else path, dir_fd=dir_fd)
            try:
                measured = _digest_fd(fd)
                _verify_temp_identity(fd, path.name if dir_fd is not None else path, dir_fd=dir_fd)
                return measured == (size, digest)
            finally:
                os.close(fd)
    except (OSError, CampusError):
        return False


def verified_receipt(
    root: Path, entity_id: str, output_dir: Path, course_root: Path | None = None
) -> dict[str, Any] | None:
    """Return only a byte-verified receipt for the current destination and course root."""
    directory = _check_directory(output_dir)
    receipt_file = receipts_path(root)
    _check_directory(receipt_file.parent, create=True)
    for item in _read_receipts(receipt_file):
        if item.get("entity_id") != entity_id or item.get("output_dir") != str(directory):
            continue
        path_string = item.get("path")
        digest = item.get("sha256")
        if (
            isinstance(path_string, str)
            and isinstance(digest, str)
            and (
                _receipt_file(Path(path_string), directory, item.get("size_bytes"), digest)
                if item.get("course_root") is None
                else course_root is not None
                and item["course_root"] == str(course_root)
                and _receipt_file(Path(path_string), course_root, item.get("size_bytes"), digest)
            )
        ):
            return item
    return None


def write_receipt(
    root: Path,
    entity_id: str,
    output_dir: Path,
    path: Path,
    size_bytes: int,
    sha256: str,
    observed_mime: str | None,
    course_root: Path | None = None,
) -> None:
    """Atomically record only a verified publication under the session lock."""
    directory = _check_directory(output_dir)
    if (
        not entity_id
        or not isinstance(observed_mime, (str, type(None)))
        or _url_bearing(entity_id)
        or (observed_mime is not None and _url_bearing(observed_mime))
        or (course_root is None and Path(path).parent != directory)
        or (course_root is not None and (not directory.is_relative_to(course_root) or Path(path).parent == directory))
        or not _receipt_file(Path(path), course_root if course_root is not None else directory, size_bytes, sha256)
    ):
        raise _conflict()
    target = receipts_path(root)
    parent = _check_directory(target.parent, create=True)
    items = [
        item
        for item in _read_receipts(target)
        if (item.get("entity_id"), item.get("output_dir")) != (entity_id, str(directory))
    ]
    items.append(
        {
            "entity_id": entity_id,
            "output_dir": str(directory),
            "path": str(path),
            "size_bytes": size_bytes,
            "sha256": sha256,
            "observed_mime": observed_mime,
            "course_root": str(course_root) if course_root is not None else None,
        }
    )
    temp_name = ".receipts." + secrets.token_hex(16) + ".tmp"
    try:
        with _pinned_directory(parent) as (dir_fd, identity):
            temp = temp_name if dir_fd is not None else parent / temp_name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            fd = os.open(temp, flags, 0o600, **({} if dir_fd is None else {"dir_fd": dir_fd}))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as destination:
                    json.dump(
                        {
                            "schema_version": 2,
                            "items": [{**item, "course_root": item.get("course_root")} for item in items],
                        },
                        destination,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    destination.write("\n")
                    destination.flush()
                    os.fsync(destination.fileno())
                if dir_fd is None and _directory_identity(parent) != identity:
                    raise _conflict()
                try:
                    existing = os.stat(
                        target.name if dir_fd is not None else target,
                        follow_symlinks=False,
                        **({} if dir_fd is None else {"dir_fd": dir_fd}),
                    )
                except FileNotFoundError:
                    pass
                else:
                    if not stat.S_ISREG(existing.st_mode):
                        raise _conflict()
                if dir_fd is None:
                    os.replace(temp, target)
                else:
                    os.replace(temp_name, target.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                if dir_fd is not None:
                    os.fsync(dir_fd)
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(temp, **({} if dir_fd is None else {"dir_fd": dir_fd}))
    except OSError:
        raise CampusError(
            "output-path-conflict",
            "Materials receipt could not be saved atomically.",
            "Check the private data directory and retry.",
            "user-action",
        ) from None
