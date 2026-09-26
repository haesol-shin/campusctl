"""One selected official attachment, guarded before consuming its response body."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from campusctl.envelope import CampusError
from campusctl.providers.cnu.request_policy import (
    MAX_ATTACHMENT_BYTES,
    RequestPolicy,
    ResponseValidator,
    guard_response,
)
from campusctl.providers.cnu.ui_policy import (
    SelectedFileRequest,
    UiRequestDenied,
    UiRequestInterceptor,
    bind_selected_file_request,
    guard_ui_request,
    match_selected_file_path,
)


@dataclass(frozen=True, slots=True)
class OfficialAttachmentTarget:
    file_id: str
    parent_kind: Literal["archive", "assignment", "notice"]
    parent_id: str
    control_locator: str
    candidate_url: str | None


@dataclass(frozen=True, slots=True)
class FetchedAttachment:
    temp_path: Path
    sha256: str
    media_type: str | None
    size_bytes: int


def _blocked() -> CampusError:
    return CampusError("policy-blocked", "Selected attachment request is not approved.", status="error")


def _selected_url(
    payload: Any, target: OfficialAttachmentTarget, policy: RequestPolicy
) -> tuple[str, SelectedFileRequest]:
    if not isinstance(payload, dict):
        raise _blocked()
    header = payload.get("header")
    if not isinstance(header, dict) or header.get("code") not in (200, "200"):
        raise _blocked()
    body = payload.get("body")
    if isinstance(body, dict) and "is_cms" in body and type(body["is_cms"]) is not bool:
        raise _blocked()
    if isinstance(body, dict) and body.get("is_cms") is True:
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
        urls = ["https://dcs-lcms.cnu.ac.kr/upload" + quote(path, safe="/")]
    else:
        urls: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                try:
                    bind_selected_file_request(
                        policy.ui_policy,
                        selected_file_id=target.file_id,
                        resolved_url=value,
                        source="fileDownload-response",
                    )
                except UiRequestDenied:
                    return
                urls.append(value)
            elif isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)

        collect(payload)
    if len(urls) != 1 or (target.candidate_url is not None and target.candidate_url != urls[0]):
        raise _blocked()
    url = urls[0]
    selected = bind_selected_file_request(
        policy.ui_policy, selected_file_id=target.file_id, resolved_url=url, source="fileDownload-response"
    )
    if target.parent_kind in {"archive", "notice"}:
        for entry in policy.ui_policy.selected_file_routes:
            if entry.origin == selected.origin:
                matched = match_selected_file_path(entry.path_template, selected.path)
                if matched is not None and "board-item" in matched and matched["board-item"] != target.parent_id:
                    raise _blocked()
    return url, selected


async def fetch_official_attachment(
    page: object,
    target: OfficialAttachmentTarget,
    policy: RequestPolicy,
    temp_dir: Path,
    *,
    max_bytes: int,
    interceptor: UiRequestInterceptor | None = None,
) -> FetchedAttachment:
    """Fetch the URL returned by this selected official control's UI action.

    The caller verifies parent_kind/parent_id in the page and supplies a parent-
    scoped locator. The control must uniquely match this file ID. A candidate URL
    is only a cross-check, never provenance. PR1.1 protects the page's metadata
    POST; the temporary route suppresses its duplicate browser download. The
    context.request GET is explicitly guarded because it bypasses page.route.
    """
    if (
        not target.file_id
        or not target.parent_id
        or target.parent_kind not in {"archive", "assignment", "notice"}
        or not target.control_locator
        or type(max_bytes) is not int
        or not 0 < max_bytes <= MAX_ATTACHMENT_BYTES
    ):
        raise _blocked()
    control = page.locator(target.control_locator)
    if (
        await control.count() != 1
        or await control.get_attribute("data-id") != target.file_id
        or await control.get_attribute("data-act") != "downloadFile"
    ):
        raise _blocked()

    first_request: list[Any] = []
    provisional_urls: list[str] = []
    fatal_denials: list[UiRequestDenied] = []
    inflight: set[asyncio.Task[Any]] = set()
    bound_url: str | None = None
    response: Any = None
    temp_path: Path | None = None
    route_installed = False
    listening = False
    click_started = False
    result: FetchedAttachment | None = None
    metadata_url = "https://dcs-learning.cnu.ac.kr/api/v1/archive/fileDownload"
    parent_url: str | None = None
    selected_result: list[tuple[str, SelectedFileRequest]] = []
    metadata_failures: list[CampusError] = []

    async def resolve_metadata(request: Any, metadata_response: Any) -> tuple[str, SelectedFileRequest]:
        if not click_started or request.url != metadata_url or request.method != "POST":
            raise _blocked()
        previous = request.redirected_from
        metadata_headers = await request.all_headers()
        guard_ui_request(
            policy.ui_policy,
            request.url,
            request.method,
            metadata_headers,
            operation="materials.download",
            resource_type="fetch",
            redirected_from=previous.url if previous is not None else None,
        )
        parent = urlsplit(parent_url or "")
        referer = urlsplit(
            next((value for name, value in metadata_headers.items() if name.casefold() == "referer"), "")
        )
        if (
            (parent.scheme, parent.netloc) != ("https", "dcs-learning.cnu.ac.kr")
            or (referer.scheme, referer.netloc, referer.path) != (parent.scheme, parent.netloc, parent.path)
            or referer.fragment
            or metadata_response.status != 200
        ):
            raise _blocked()
        return _selected_url(await metadata_response.json(), target, policy)

    async def capture_metadata(request: Any, metadata_response: Any) -> None:
        nonlocal bound_url
        if not first_request or request is not first_request[0]:
            return
        try:
            url, selected = await resolve_metadata(request, metadata_response)
        except CampusError as error:
            metadata_failures.append(error)
            return
        selected_result.append((url, selected))
        bound_url = url
        assert interceptor is not None
        interceptor.bind_selected_document(selected)

    def record_duplicate() -> None:
        if policy.diagnostics is not None:
            policy.diagnostics.suppressed_count += 1
            counts = policy.diagnostics.suppressed_reasons
            counts["duplicate-download"] = counts.get("duplicate-download", 0) + 1

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
        parsed = urlsplit(request.url)
        selected_route = request.method == "GET" and any(
            f"{parsed.scheme}://{parsed.netloc}" == entry.origin
            and match_selected_file_path(entry.path_template, parsed.path) is not None
            for entry in policy.ui_policy.selected_file_routes
        )
        if click_started and selected_route:
            try:
                request_headers = await request.all_headers()
            except Exception:
                fatal_denials.append(UiRequestDenied("route"))
                await route.abort()
                return
            if any(name.casefold() == "range" for name in request_headers):
                fatal_denials.append(UiRequestDenied("range"))
            elif bound_url is None:
                provisional_urls.append(request.url)
            elif request.url == bound_url:
                record_duplicate()
            else:
                fatal_denials.append(UiRequestDenied("route"))
            await route.abort()
        else:
            await route.fallback()

    try:
        await page.wait_for_load_state("networkidle")
        page.on("request", capture)
        listening = True
        if interceptor is not None:
            interceptor.capture_response("/api/v1/archive/fileDownload", capture_metadata)
        await page.route("**/*", suppress_duplicate)
        route_installed = True
        async with page.expect_response(
            lambda observed: bool(first_request) and observed.request is first_request[0]
        ) as pending:
            parent_url = page.url
            click_started = True
            await control.click(no_wait_after=True)
        official_response = await pending.value
        if metadata_failures:
            raise metadata_failures[0]
        if selected_result:
            url, selected = selected_result[0]
        else:
            url, selected = await resolve_metadata(official_response.request, official_response)
            bound_url = url
        for provisional in provisional_urls:
            if provisional == url:
                record_duplicate()
            else:
                fatal_denials.append(UiRequestDenied("route"))
        if fatal_denials:
            raise fatal_denials[0]
        if interceptor is not None:
            interceptor.raise_if_denied()
        headers: dict[str, str] = {}
        guard_ui_request(
            policy.ui_policy,
            url,
            "GET",
            headers,
            operation="materials.download",
            resource_type="fetch",
            selected_file=selected,
            selected_file_id=target.file_id,
        )
        response = await page.context.request.get(url, headers=headers, max_redirects=0)
        if response.url != url or response.status != 200:
            raise _blocked()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        length = response_headers.get("content-length")
        validator = ResponseValidator(policy, max_bytes)
        validator.declared(length)
        media_type = response_headers.get("content-type")
        guard_response(
            policy,
            url,
            media_type,
            response_headers.get("content-disposition"),
            operation="materials.download",
            selected_file_id=target.file_id,
        )
        body = await response.body()
        if len(body) > max_bytes:
            raise CampusError("file-too-large", "Selected attachment exceeds the approved byte limit.")
        if length is not None and len(body) != int(length):
            raise CampusError("unsupported-media-type", "Selected attachment body length does not match its response.")
        view = memoryview(body)
        for offset in range(0, len(view), 64 * 1024):
            validator.feed(view[offset : offset + 64 * 1024])
        fd, name = tempfile.mkstemp(prefix=".attachment-", dir=temp_dir)
        temp_path = Path(name)
        with os.fdopen(fd, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        validator.finish(temp_path, length)
        result = FetchedAttachment(temp_path, hashlib.sha256(body).hexdigest(), media_type, len(body))
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    finally:
        if interceptor is not None:
            interceptor.stop_capture()
            interceptor.unbind_selected_document()
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
    if interceptor is not None:
        try:
            interceptor.raise_if_denied()
        except UiRequestDenied:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise
    assert result is not None
    return result
