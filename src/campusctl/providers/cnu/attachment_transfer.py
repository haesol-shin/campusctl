"""One selected official attachment, bound and validated before consuming its body."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from campusctl.attachment_policy import (
    MAX_ATTACHMENT_BYTES,
    FetchedAttachment,
    OfficialAttachmentTarget,
    RequestPolicy,
    ResponseValidator,
    guard_response,
)
from campusctl.envelope import CampusError
from campusctl.providers.cnu.selected_file_policy import (
    FILE_TEMPLATES,
    SelectedFileDenied,
    SelectedFileRequest,
    bind_selected_file_request,
    match_selected_file_path,
)

# Public package provenance supplied by this provider's resolution binding.
PACKAGE_PROVENANCE = "cnu"


@dataclass(frozen=True, slots=True)
class _ResponseMetadata:
    length: str | None
    media_type: str | None
    validator: ResponseValidator


def _blocked() -> CampusError:
    return CampusError("policy-blocked", "Selected attachment request is not approved.", status="error")


def _validated_metadata_body(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise _blocked()
    header = payload.get("header")
    if not isinstance(header, dict) or header.get("code") not in (200, "200"):
        raise _blocked()
    body = payload.get("body")
    if isinstance(body, dict) and "is_cms" in body and type(body["is_cms"]) is not bool:
        raise _blocked()
    return body


def _cms_path_url(body: dict) -> str:
    if not isinstance(body.get("path"), str) or not isinstance(body.get("name"), str):
        raise _blocked()
    path, name = body["path"], body["name"]
    segments = path.split("/")
    if (
        len(segments) != 3
        or segments[0]
        or not segments[1]
        or segments[1] in {".", ".."}
        or not name
        or name in {".", ".."}
        or segments[2] != name
        or any(char in path for char in "\\%?#")
    ):
        raise _blocked()
    return "https://dcs-lcms.cnu.ac.kr/upload" + quote(path, safe="/")


def _is_relative_path_body(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and "path" in body
        and not (isinstance(body["path"], str) and body["path"].startswith(("https://", "http://")))
    )


def _relative_path_url(body: dict) -> str:
    path, name = body["path"], body.get("name")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or any(char in path for char in "\\%?#")
        or any(segment in {".", ".."} for segment in path.split("/"))
        or not isinstance(name, str)
        or not name
    ):
        raise _blocked()
    return "https://dcs-learning.cnu.ac.kr/file" + quote(path, safe="/")


def _collect_urls(value: Any, target: OfficialAttachmentTarget, urls: list[str]) -> None:
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        try:
            bind_selected_file_request(
                selected_file_id=target.file_id,
                resolved_url=value,
                source="fileDownload-response",
            )
        except SelectedFileDenied:
            return
        urls.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            _collect_urls(nested, target, urls)
    elif isinstance(value, list):
        for nested in value:
            _collect_urls(nested, target, urls)


def _guard_parent_identity(target: OfficialAttachmentTarget, selected: SelectedFileRequest) -> None:
    if target.parent_kind not in {"archive", "notice"}:
        return
    template = FILE_TEMPLATES.get(selected.origin)
    if template is None:
        return
    matched = match_selected_file_path(template, selected.path)
    if matched is not None and "board-item" in matched and matched["board-item"] != target.parent_id:
        raise _blocked()


def _selected_url(payload: Any, target: OfficialAttachmentTarget) -> str:
    body = _validated_metadata_body(payload)
    if isinstance(body, dict) and body.get("is_cms") is True:
        urls = [_cms_path_url(body)]
    elif _is_relative_path_body(body):
        urls = [_relative_path_url(body)]
    else:
        urls = []
        _collect_urls(payload, target, urls)
    if len(urls) != 1 or (target.candidate_url is not None and target.candidate_url != urls[0]):
        raise _blocked()
    url = urls[0]
    selected = bind_selected_file_request(
        selected_file_id=target.file_id,
        resolved_url=url,
        source="fileDownload-response",
    )
    _guard_parent_identity(target, selected)
    return url


def _validate_transfer_target(target: OfficialAttachmentTarget, max_bytes: int) -> None:
    if (
        not target.file_id
        or not target.parent_id
        or target.parent_kind not in {"archive", "assignment", "notice"}
        or not target.control_locator
        or type(max_bytes) is not int
        or not 0 < max_bytes <= MAX_ATTACHMENT_BYTES
    ):
        raise _blocked()


def _guard_metadata_source(
    previous: Any,
    parent_url: str | None,
    headers: dict[str, str],
    status: int,
) -> None:
    if previous is not None:
        raise _blocked()
    parent = urlsplit(parent_url or "")
    referer = urlsplit(next((value for name, value in headers.items() if name.casefold() == "referer"), ""))
    if (
        (parent.scheme, parent.netloc) != ("https", "dcs-learning.cnu.ac.kr")
        or (referer.scheme, referer.netloc, referer.path) != (parent.scheme, parent.netloc, parent.path)
        or referer.fragment
        or status != 200
    ):
        raise _blocked()


def _is_selected_route(method: str, url: str) -> bool:
    parsed = urlsplit(url)
    return method == "GET" and any(
        f"{parsed.scheme}://{parsed.netloc}" == origin and match_selected_file_path(template, parsed.path) is not None
        for origin, template in FILE_TEMPLATES.items()
    )


def _route_denial(
    request: Any,
    headers: dict[str, str],
    bound_url: str | None,
) -> CampusError | None:
    if any(name.casefold() == "range" for name in headers):
        return _blocked()
    if bound_url is not None and request.url != bound_url:
        return _blocked()
    return None


def _guard_provisional_urls(
    provisional_urls: list[str],
    url: str,
    fatal_denials: list[CampusError],
) -> None:
    denial = _blocked() if any(provisional != url for provisional in provisional_urls) else None
    if fatal_denials:
        raise fatal_denials[0]
    if denial is not None:
        raise denial


def _response_metadata(policy: RequestPolicy, max_bytes: int, headers: dict[str, str]) -> _ResponseMetadata:
    length = headers.get("content-length")
    validator = ResponseValidator(policy, max_bytes)
    validator.declared(length)
    media_type = headers.get("content-type")
    guard_response(policy, media_type, headers.get("content-disposition"))
    return _ResponseMetadata(length, media_type, validator)


def _check_response_body(body: bytes, metadata: _ResponseMetadata, max_bytes: int) -> None:
    if len(body) > max_bytes:
        raise CampusError("file-too-large", "Selected attachment exceeds the approved byte limit.")
    if metadata.length is not None and len(body) != int(metadata.length):
        raise CampusError("unsupported-media-type", "Selected attachment body length does not match its response.")


async def fetch_official_attachment(  # noqa: C901
    page: object,
    target: OfficialAttachmentTarget,
    policy: RequestPolicy,
    temp_dir: Path,
    *,
    max_bytes: int,
    metadata_path: str = "/api/v1/archive/fileDownload",
) -> FetchedAttachment:
    """Fetch the URL returned by this selected official control's UI action.

    The caller verifies parent_kind/parent_id in the page and supplies a parent-
    scoped locator. The control must uniquely match this file ID. A candidate URL
    is only a cross-check, never provenance. The temporary route suppresses only
    duplicate selected browser downloads; unrelated page requests are not filtered.
    """
    _validate_transfer_target(target, max_bytes)
    control = page.locator(target.control_locator)
    if (
        await control.count() != 1
        or await control.get_attribute("data-id") != target.file_id
        or await control.get_attribute("data-act") != "downloadFile"
    ):
        raise _blocked()

    first_request: list[Any] = []
    provisional_urls: list[str] = []
    fatal_denials: list[CampusError] = []
    inflight: set[asyncio.Task[Any]] = set()
    bound_url: str | None = None
    response: Any = None
    temp_path: Path | None = None
    route_installed = False
    listening = False
    click_started = False
    result: FetchedAttachment | None = None
    metadata_url = f"https://dcs-learning.cnu.ac.kr{metadata_path}"
    parent_url: str | None = None

    async def resolve_metadata(request: Any, metadata_response: Any) -> str:
        if not click_started or request.url != metadata_url or request.method != "POST":
            raise _blocked()
        previous = request.redirected_from
        metadata_headers = await request.all_headers()
        _guard_metadata_source(previous, parent_url, metadata_headers, metadata_response.status)
        return _selected_url(await metadata_response.json(), target)

    def capture(request: Any) -> None:
        if click_started and request.method == "POST" and request.url == metadata_url and not first_request:
            first_request.append(request)

    async def suppress_duplicate(route: Any) -> None:
        task = asyncio.current_task()
        assert task is not None
        inflight.add(task)
        try:
            await handle_selected_route(route)
        finally:
            inflight.discard(task)

    async def handle_selected_route(route: Any) -> None:
        request = route.request
        selected_route = _is_selected_route(request.method, request.url)
        if click_started and selected_route:
            try:
                request_headers = await request.all_headers()
            except Exception:
                fatal_denials.append(_blocked())
                await route.abort()
                return
            denial = _route_denial(request, request_headers, bound_url)
            if denial is not None:
                fatal_denials.append(denial)
            elif bound_url is None:
                provisional_urls.append(request.url)
            await route.abort()
        else:
            await route.fallback()

    try:
        await page.wait_for_load_state("networkidle")
        page.on("request", capture)
        listening = True
        await page.route("**/*", suppress_duplicate)
        route_installed = True
        async with page.expect_response(
            lambda observed: bool(first_request) and observed.request is first_request[0]
        ) as pending:
            parent_url = page.url
            click_started = True
            await control.click(no_wait_after=True)
        official_response = await pending.value
        url = await resolve_metadata(official_response.request, official_response)
        bound_url = url
        _guard_provisional_urls(provisional_urls, url, fatal_denials)
        response = await page.context.request.get(url, headers={}, max_redirects=0)
        if response.url != url or response.status != 200:
            raise _blocked()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        metadata = _response_metadata(policy, max_bytes, response_headers)
        body = await response.body()
        _check_response_body(body, metadata, max_bytes)
        view = memoryview(body)
        for offset in range(0, len(view), 64 * 1024):
            metadata.validator.feed(view[offset : offset + 64 * 1024])
        fd, name = tempfile.mkstemp(prefix=".attachment-", dir=temp_dir)
        temp_path = Path(name)
        with os.fdopen(fd, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        metadata.validator.finish(temp_path, metadata.length)
        result = FetchedAttachment(temp_path, hashlib.sha256(body).hexdigest(), metadata.media_type, len(body))
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    finally:
        try:
            try:
                if response is not None:
                    await response.dispose()
            finally:
                try:
                    if route_installed:
                        await page.unroute("**/*", suppress_duplicate)
                finally:
                    try:
                        if inflight:
                            await asyncio.gather(*tuple(inflight))
                    finally:
                        if listening:
                            page.remove_listener("request", capture)
        except BaseException:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise
    if fatal_denials:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise fatal_denials[0]
    assert result is not None
    return result
