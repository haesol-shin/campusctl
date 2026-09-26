"""Selected attachment response metadata and incremental content validation."""

from __future__ import annotations

import codecs
import zipfile
from dataclasses import dataclass
from pathlib import Path

from campusctl.envelope import CampusError

MAX_ATTACHMENT_BYTES = 200_000_000
ALLOWED_EXTENSIONS = frozenset(
    [
        "pdf",
        "ppt",
        "pptx",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "hwp",
        "hwpx",
        "txt",
        "md",
        "png",
        "jpg",
        "zip",
        "ipynb",
        "py",
        "c",
        "cpp",
        "java",
        "js",
        "sql",
    ]
)
_OBSERVED = {
    "pdf": "application/x-pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_HEADERS = {
    "pdf": b"%PDF-",
    "pptx": b"PK\x03\x04",
    "zip": b"PK\x03\x04",
    "docx": b"PK\x03\x04",
    "xlsx": b"PK\x03\x04",
    "hwpx": b"PK\x03\x04",
    "doc": _OLE,
    "xls": _OLE,
    "ppt": _OLE,
    "hwp": _OLE,
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
}
_TEXT = ALLOWED_EXTENSIONS - _HEADERS.keys()
_REQUIRED_ZIP_ENTRY = {
    "pptx": "[Content_Types].xml",
    "docx": "[Content_Types].xml",
    "xlsx": "[Content_Types].xml",
    "hwpx": "mimetype",
}


@dataclass(frozen=True, slots=True)
class RequestPolicy:
    filename: str

    @property
    def extension(self) -> str:
        return self.filename.rsplit(".", 1)[-1].casefold() if "." in self.filename else ""


def _unsupported() -> CampusError:
    return CampusError("unsupported-media-type", "Selected attachment type or content is not approved.")


def _too_large() -> CampusError:
    return CampusError("file-too-large", "Selected attachment exceeds the approved byte limit.")


def guard_response(
    policy: RequestPolicy,
    media_type: str | None,
    content_disposition: str | None,
) -> None:
    """Check selected response metadata without trusting its proposed filename."""
    if (
        not isinstance(policy.filename, str)
        or policy.extension not in ALLOWED_EXTENSIONS
        or any(sep in policy.filename for sep in ("/", "\\"))
    ):
        raise _unsupported()
    if content_disposition is not None and ("\r" in content_disposition or "\n" in content_disposition):
        raise _unsupported()
    mime = media_type.strip().casefold() if isinstance(media_type, str) else None
    if mime and mime.startswith(("video/", "audio/")):
        raise _unsupported()
    expected = _OBSERVED.get(policy.extension)
    if expected is not None and mime != expected:
        raise _unsupported()


class ResponseValidator:
    """Incremental byte/UTF-8 validation, with final ZIP directory inspection."""

    def __init__(self, policy: RequestPolicy, max_bytes: int = MAX_ATTACHMENT_BYTES) -> None:
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_ATTACHMENT_BYTES:
            raise _too_large()
        if policy.extension not in ALLOWED_EXTENSIONS:
            raise _unsupported()
        self.extension = policy.extension
        self.max_bytes = max_bytes
        self.size = 0
        self.prefix = b""
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict") if self.extension in _TEXT else None

    def declared(self, content_length: str | None) -> None:
        if content_length is None:
            return
        if not content_length.isascii() or not content_length.isdecimal():
            raise _unsupported()
        if int(content_length) > self.max_bytes:
            raise _too_large()
        if int(content_length) == 0:
            raise _unsupported()

    def feed(self, chunk: bytes | memoryview) -> None:
        if not isinstance(chunk, (bytes, memoryview)):
            raise _unsupported()
        if self.size + len(chunk) > self.max_bytes:
            raise _too_large()
        self.size += len(chunk)
        header = _HEADERS.get(self.extension, b"")
        if len(self.prefix) < len(header):
            self.prefix += bytes(chunk[: len(header) - len(self.prefix)])
        if self.decoder is not None:
            if 0 in chunk:
                raise _unsupported()
            try:
                self.decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                raise _unsupported() from exc
        elif len(self.prefix) >= len(header) and not self.prefix.startswith(header):
            raise _unsupported()

    def finish(self, path: Path, declared_length: str | None = None) -> None:
        if not self.size or (declared_length is not None and self.size != int(declared_length)):
            raise _unsupported()
        header = _HEADERS.get(self.extension)
        if header is not None and not self.prefix.startswith(header):
            raise _unsupported()
        if self.decoder is not None:
            try:
                self.decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise _unsupported() from exc
        required = _REQUIRED_ZIP_ENTRY.get(self.extension)
        if required is not None:
            try:
                with zipfile.ZipFile(path) as archive:
                    if sum(info.filename == required for info in archive.infolist()) != 1:
                        raise _unsupported()
            except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
                raise _unsupported() from exc
