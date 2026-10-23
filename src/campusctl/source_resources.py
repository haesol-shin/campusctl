"""Selected-detail resource records and lazy byte resolution."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from campusctl.attachment_policy import (
    ALLOWED_EXTENSIONS,
    MAX_ATTACHMENT_BYTES,
    FetchedAttachment,
    OfficialAttachmentTarget,
    RequestPolicy,
)
from campusctl.envelope import CampusError

OfficialTransfer = Callable[..., Awaitable[FetchedAttachment]]


@dataclass(frozen=True, slots=True)
class SourceResolution:
    """Official transfer and package provenance bound once per invocation."""

    transfer: OfficialTransfer
    provenance: str


@dataclass(frozen=True, slots=True)
class ResourceReference:
    kind: str
    source_url: str
    original_name: str | None
    media_type_hint: str | None
    label: str
    provider_file_id: str | None
    official_target: OfficialAttachmentTarget | None


@dataclass(frozen=True, slots=True)
class DetailSnapshot:
    source_url: str
    provider_native_id: str | None
    parts: tuple[str | ResourceReference, ...]


def _failed() -> CampusError:
    return CampusError("fetch-failed", "Selected source could not be packaged safely.", status="error")


def _blocked() -> CampusError:
    return CampusError("fetch-failed", "Selected attachment does not match its official file control.", status="error")


def _image_allowed(reference: ResourceReference) -> bool:
    parsed = urlsplit(reference.source_url)
    return reference.kind == "image" and parsed.path.lower().endswith((".png", ".jpg", ".jpeg"))


async def _image_bytes(page: object, reference: ResourceReference) -> tuple[bytes, str]:
    url = reference.source_url
    response = await page.context.request.get(url, max_redirects=0)
    try:
        if response.status != 200 or response.url != url:
            raise _blocked()
        headers = {key.casefold(): value for key, value in response.headers.items()}
        length = headers.get("content-length")
        if length is not None and (
            not length.isascii() or not length.isdecimal() or int(length) > MAX_ATTACHMENT_BYTES
        ):
            raise CampusError("file-too-large", "Selected image exceeds the approved byte limit.")
        mime = (headers.get("content-type") or "").split(";", 1)[0].casefold()
        extension = urlsplit(url).path.rsplit(".", 1)[-1].lower()
        signatures = {
            "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
            "jpg": ("image/jpeg", b"\xff\xd8\xff"),
            "jpeg": ("image/jpeg", b"\xff\xd8\xff"),
        }
        expected_mime, signature = signatures[extension]
        if mime != expected_mime:
            raise CampusError("unsupported-media-type", "Selected image type is not approved.")
        body = await response.body()
        if (
            not body.startswith(signature)
            or not body
            or len(body) > MAX_ATTACHMENT_BYTES
            or (length is not None and len(body) != int(length))
        ):
            raise CampusError("unsupported-media-type", "Selected image content is not approved.")
        return body, mime
    finally:
        await response.dispose()


async def resolve_source_resource(
    page: object,
    reference: ResourceReference,
    *,
    kind: str,
    provider_native_id: str | None,
    name: str,
    staging: Path,
    transfer: OfficialTransfer,
) -> tuple[bytes | None, str | None, str | None]:
    """Resolve one new reference after staging exists; repeated IDs are not passed here."""
    reason = None
    contents = None
    media_type = None
    if reference.kind == "image":
        if not _image_allowed(reference):
            reason = "unsupported-media-type"
        else:
            contents, media_type = await _image_bytes(page, reference)
    elif reference.kind == "attachment":
        if kind == "notice":
            reason = "unverified-notice-attachment"
        elif name.rsplit(".", 1)[-1].casefold() not in ALLOWED_EXTENSIONS:
            reason = "unsupported-media-type"
        else:
            target = reference.official_target
            if (
                target is None
                or reference.provider_file_id != target.file_id
                or target.parent_kind != kind
                or target.parent_id != provider_native_id
            ):
                raise _blocked()
            fetched = await transfer(
                page,
                target,
                RequestPolicy(name),
                staging,
                max_bytes=MAX_ATTACHMENT_BYTES,
            )
            try:
                contents = fetched.temp_path.read_bytes()
                if len(contents) != fetched.size_bytes or hashlib.sha256(contents).hexdigest() != fetched.sha256:
                    raise _failed()
                media_type = fetched.media_type
            finally:
                fetched.temp_path.unlink(missing_ok=True)
    else:
        reason = "unsupported-media-type"
    return contents, media_type, reason
