from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cnu_request_policy import FIXTURE

from campusctl.envelope import CampusError
from campusctl.providers.cnu.attachment_transfer import OfficialAttachmentTarget, fetch_official_attachment
from campusctl.providers.cnu.request_policy import RequestPolicy
from campusctl.providers.cnu.selected_file_policy import SelectedFileDenied, bind_selected_file_request

URL = "https://dcs-lcms.cnu.ac.kr/upload/storage-1/example.pdf"
PARENT_URL = "https://dcs-learning.cnu.ac.kr/std/archive"
BODY = b"%PDF-1.4\nfixture bytes"
TARGET = OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", URL)
CMS_URL = "https://dcs-lcms.cnu.ac.kr/upload/storage-1/example%20file.pdf"
CMS_PAYLOAD = {
    "header": {"code": 200},
    "body": {"path": "/storage-1/example file.pdf", "name": "example file.pdf", "is_cms": True},
}


class Control:
    def __init__(self, owner: FakePage, attrs: dict[str, str]):
        self.owner = owner
        self.attrs = attrs

    async def count(self) -> int:
        return 1

    async def get_attribute(self, name: str) -> str | None:
        return self.attrs.get(name)

    async def click(self, *, no_wait_after: bool) -> None:
        assert no_wait_after
        assert self.owner.idle and self.owner.route_handler is not None
        request = MetadataRequest(self.owner.referer)
        for listener in self.owner.listeners:
            listener(request)
        response = MetadataResponse(request, self.owner.payload)
        if self.owner.pending.predicate(response):
            self.owner.pending.value.set_result(response)
        if self.owner.duplicate_url:
            route = DuplicateRoute(self.owner.duplicate_url, self.owner.duplicate_headers)
            await self.owner.route_handler(route)
            assert route.aborted
        unrelated = DuplicateRoute("https://other.invalid/static/course.js")
        await self.owner.route_handler(unrelated)
        assert unrelated.fell_back and not unrelated.aborted


class MetadataRequest:
    url = "https://dcs-learning.cnu.ac.kr/api/v1/archive/fileDownload"
    method = "POST"
    redirected_from = None

    def __init__(self, referer: str | None):
        self.referer = referer

    async def all_headers(self) -> dict[str, str]:
        return {"Referer": self.referer} if self.referer is not None else {}


class MetadataResponse:
    status = 200

    def __init__(self, request: MetadataRequest, payload: object):
        self.request = request
        self.payload = payload

    async def json(self) -> object:
        return self.payload


class ResponseWaiter:
    def __init__(self, predicate: object):
        self.predicate = predicate
        self.value: asyncio.Future[MetadataResponse] = asyncio.get_running_loop().create_future()

    async def __aenter__(self) -> ResponseWaiter:
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass


class DuplicateRoute:
    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        headers_ready: asyncio.Event | None = None,
        resource_type: str = "document",
    ):
        async def all_headers() -> dict[str, str]:
            if headers_ready is not None:
                await headers_ready.wait()
            return headers or {}

        self.request = SimpleNamespace(
            url=url, method="GET", resource_type=resource_type, redirected_from=None, all_headers=all_headers
        )
        self.aborted = False
        self.fell_back = False

    async def abort(self) -> None:
        self.aborted = True

    async def fallback(self) -> None:
        self.fell_back = True


class FakePage:
    def __init__(
        self,
        response: Response,
        attrs: dict[str, str],
        payload: object,
        duplicate_url: str | None,
        referer: str | None,
        duplicate_headers: dict[str, str] | None,
        late_url: str | None,
        unroute_error: bool,
        delayed_headers: bool,
    ):
        self.url = PARENT_URL
        self.control = Control(self, attrs)
        self.context = SimpleNamespace(request=Request(response))
        self.context.request.owner = self
        self.payload = payload
        self.duplicate_url = duplicate_url
        self.duplicate_headers = duplicate_headers
        self.late_url = late_url
        self.referer = referer
        self.unroute_error = unroute_error
        self.delayed_headers = delayed_headers
        self.headers_ready = asyncio.Event()
        self.late_tasks: list[asyncio.Task[None]] = []
        self.listeners: list[object] = []
        self.route_handler: object | None = None
        self.pending: ResponseWaiter | None = None
        self.idle = False

    def locator(self, selector: str) -> Control:
        assert selector == TARGET.control_locator
        return self.control

    async def wait_for_load_state(self, state: str) -> None:
        assert state == "networkidle"
        self.idle = True

    def on(self, event: str, callback: object) -> None:
        assert event == "request"
        self.listeners.append(callback)

    def remove_listener(self, event: str, callback: object) -> None:
        assert event == "request"
        self.listeners.remove(callback)

    async def route(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*"
        self.route_handler = handler

    async def unroute(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*" and self.route_handler is handler
        self.route_handler = None
        self.headers_ready.set()
        if self.unroute_error:
            raise RuntimeError("synthetic unroute failure")

    def expect_response(self, predicate: object) -> ResponseWaiter:
        self.pending = ResponseWaiter(predicate)
        return self.pending


class Response:
    def __init__(self, *, url: str = URL, status: int = 200, headers: dict[str, str] | None = None, body: bytes = BODY):
        self.url, self.status = url, status
        self.headers = (
            headers if headers is not None else {"Content-Type": "application/x-pdf", "Content-Length": str(len(body))}
        )
        self._body = body
        self.reads = 0
        self.disposed = False

    async def body(self) -> bytes:
        self.reads += 1
        return self._body

    async def dispose(self) -> None:
        self.disposed = True


class Request:
    def __init__(self, response: Response):
        self.response = response
        self.calls: list[tuple[str, dict[str, str], int]] = []
        self.owner: FakePage | None = None

    async def get(self, url: str, *, headers: dict[str, str], max_redirects: int) -> Response:
        self.calls.append((url, headers, max_redirects))
        if self.owner is not None and self.owner.late_url is not None:
            route = DuplicateRoute(
                self.owner.late_url, headers_ready=self.owner.headers_ready if self.owner.delayed_headers else None
            )
            if self.owner.delayed_headers:
                task = asyncio.create_task(self.owner.route_handler(route))
                self.owner.late_tasks.append(task)
                await asyncio.sleep(0)
                assert not task.done()
            else:
                await self.owner.route_handler(route)
                assert route.aborted
        return self.response


def page(
    response: Response,
    *,
    payload: object = None,
    duplicate_url: str | None = None,
    referer: str | None = PARENT_URL,
    duplicate_headers: dict[str, str] | None = None,
    late_url: str | None = None,
    unroute_error: bool = False,
    delayed_headers: bool = False,
    **attrs: str,
) -> FakePage:
    data = {"data-id": "file-1", "data-act": "downloadFile"}
    data.update(attrs)
    official_payload = {"header": {"code": 200}, "url": URL} if payload is None else payload
    if isinstance(official_payload, dict):
        official_payload = {"header": {"code": 200}, **official_payload}
    return FakePage(
        response,
        data,
        official_payload,
        duplicate_url,
        referer,
        duplicate_headers,
        late_url,
        unroute_error,
        delayed_headers,
    )


def test_selected_guarded_bounded_transfer(tmp_path: Path) -> None:
    asyncio.run(_selected_guarded_bounded_transfer(tmp_path))


async def _selected_guarded_bounded_transfer(tmp_path: Path) -> None:
    policy = RequestPolicy("example.pdf")
    response = Response()
    browser = page(response)
    fetched = await fetch_official_attachment(browser, TARGET, policy, tmp_path, max_bytes=200_000_000)
    assert fetched.temp_path.read_bytes() == BODY
    assert fetched.size_bytes == len(BODY)
    assert fetched.sha256 == hashlib.sha256(BODY).hexdigest()
    assert fetched.media_type == "application/x-pdf"
    assert browser.context.request.calls == [(URL, {}, 0)]
    assert response.disposed
    fetched.temp_path.unlink()
    candidate_page = page(Response(), duplicate_url=URL)
    selected = await fetch_official_attachment(candidate_page, TARGET, policy, tmp_path, max_bytes=200_000_000)
    assert candidate_page.context.request.calls == [(URL, {}, 0)]
    assert candidate_page.route_handler is None
    assert not candidate_page.listeners
    selected.temp_path.unlink()
    for changed in ({"data-id": "foreign"}, {"data-act": "other"}):
        browser = page(Response(), **changed)
        with pytest.raises(CampusError):
            await fetch_official_attachment(browser, TARGET, policy, tmp_path, max_bytes=200_000_000)
        assert not browser.context.request.calls
    assert not list(tmp_path.iterdir())


def test_response_failures_leave_no_temp_and_do_not_read_declared_oversize(tmp_path: Path) -> None:
    asyncio.run(_response_failures(tmp_path))


async def _response_failures(tmp_path: Path) -> None:
    policy = RequestPolicy("example.pdf")
    for response, expected_reads in (
        (Response(status=FIXTURE["redirect_response"]["status"]), 0),
        (Response(headers={"Content-Type": "application/x-pdf", "Content-Length": "200000001"}), 0),
        (Response(headers={"Content-Type": "application/x-pdf", "Content-Length": "1"}), 1),
        (Response(body=b"invalid"), 1),
        (Response(url=URL + "/redirected"), 0),
    ):
        with pytest.raises(CampusError):
            await fetch_official_attachment(page(response), TARGET, policy, tmp_path, max_bytes=200_000_000)
        assert response.reads == expected_reads
        assert response.disposed
        assert not list(tmp_path.iterdir())


def test_invalid_official_target_blocks_as_operational_policy_error(tmp_path: Path) -> None:
    target = OfficialAttachmentTarget("", "archive", "post-1", "#official-file", None)
    policy = RequestPolicy("example.pdf")
    with pytest.raises(CampusError) as denial:
        asyncio.run(fetch_official_attachment(object(), target, policy, tmp_path, max_bytes=200_000_000))
    assert denial.value.code == "policy-blocked"
    assert denial.value.status == "error"
    assert not list(tmp_path.iterdir())


def test_official_response_is_only_url_provenance(tmp_path: Path) -> None:
    asyncio.run(_provenance_failures(tmp_path))


async def _provenance_failures(tmp_path: Path) -> None:
    policy = RequestPolicy("example.pdf")
    other_origin = "https://dcs-learning.cnu.ac.kr/file/term-1/course-1/board/manager-1/post-1/example.pdf"
    wrong_post = "https://dcs-learning.cnu.ac.kr/file/term-1/course-1/board/manager-1/foreign-post/example.pdf"
    for target, payload in (
        (OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", URL + "-foreign"), {"url": URL}),
        (OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", other_origin), {"url": URL}),
        (TARGET, {}),
        (TARGET, {"urls": [URL, other_origin]}),
        (OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", None), {"url": wrong_post}),
    ):
        browser = page(Response(), payload=payload)
        with pytest.raises(CampusError):
            await fetch_official_attachment(browser, target, policy, tmp_path, max_bytes=200_000_000)
        assert not browser.context.request.calls
        assert browser.route_handler is None
        assert not browser.listeners
        assert not list(tmp_path.iterdir())
    correct = page(
        Response(url=other_origin), payload={"result": {"download": other_origin}}, duplicate_url=other_origin
    )
    matching_target = OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", None)
    saved = await fetch_official_attachment(correct, matching_target, policy, tmp_path, max_bytes=200_000_000)
    assert correct.context.request.calls == [(other_origin, {}, 0)]
    saved.temp_path.unlink()
    foreign_get = page(Response(), duplicate_url=other_origin)
    with pytest.raises(CampusError):
        await fetch_official_attachment(foreign_get, TARGET, policy, tmp_path, max_bytes=200_000_000)
    assert not foreign_get.context.request.calls
    assert not list(tmp_path.iterdir())
    for bad_referer in (None, PARENT_URL + "/other", "https://other.invalid/std/archive"):
        browser = page(Response(), referer=bad_referer)
        with pytest.raises(CampusError):
            await fetch_official_attachment(browser, TARGET, policy, tmp_path, max_bytes=200_000_000)
        assert not browser.context.request.calls
        assert browser.route_handler is None
    ranged = page(Response(), duplicate_url=URL, duplicate_headers={"rAnGe": "bytes=0-"})
    with pytest.raises(CampusError) as denied:
        await fetch_official_attachment(ranged, TARGET, policy, tmp_path, max_bytes=200_000_000)
    assert denied.value.code == "policy-blocked"
    assert not ranged.context.request.calls
    late_foreign = page(Response(), late_url=other_origin)
    with pytest.raises(CampusError):
        await fetch_official_attachment(late_foreign, TARGET, policy, tmp_path, max_bytes=200_000_000)
    assert late_foreign.context.request.calls == [(URL, {}, 0)]
    assert not list(tmp_path.iterdir())
    delayed = page(Response(), late_url=other_origin, delayed_headers=True)
    with pytest.raises(CampusError):
        await fetch_official_attachment(delayed, TARGET, policy, tmp_path, max_bytes=200_000_000)
    assert delayed.late_tasks and all(task.done() for task in delayed.late_tasks)
    assert not list(tmp_path.iterdir())
    failing = page(Response(status=302), late_url=other_origin, delayed_headers=True)
    with pytest.raises(CampusError):
        await fetch_official_attachment(failing, TARGET, policy, tmp_path, max_bytes=200_000_000)
    assert failing.late_tasks and all(task.done() for task in failing.late_tasks)
    assert not list(tmp_path.iterdir())


def test_cms_response_binds_selected_transfer_without_page_interceptor(tmp_path: Path) -> None:
    async def scenario() -> None:
        browser = page(Response(url=CMS_URL), payload=CMS_PAYLOAD, duplicate_url=CMS_URL)
        target = OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", None)
        fetched = await fetch_official_attachment(
            browser, target, RequestPolicy("example file.pdf"), tmp_path, max_bytes=200_000_000
        )
        assert browser.context.request.calls == [(CMS_URL, {}, 0)]
        assert fetched.temp_path.read_bytes() == BODY
        fetched.temp_path.unlink()

        for payload in (
            {"header": {"code": 403}, "body": CMS_PAYLOAD["body"]},
            {"header": {"code": 200}, "body": {**CMS_PAYLOAD["body"], "name": "other.pdf"}},
            {"header": {"code": 200}, "body": {**CMS_PAYLOAD["body"], "path": "/elsewhere/example file.pdf"}},
            {"header": {"code": 200}, "body": {**CMS_PAYLOAD["body"], "path": "/storage-1/../example file.pdf"}},
        ):
            browser = page(Response(url=CMS_URL), payload=payload)
            with pytest.raises(CampusError) as denied:
                await fetch_official_attachment(
                    browser,
                    OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", CMS_URL),
                    RequestPolicy("example file.pdf"),
                    tmp_path,
                    max_bytes=200_000_000,
                )
            assert denied.value.code == "policy-blocked"
            assert not browser.context.request.calls
        browser = page(Response(url=CMS_URL), payload=CMS_PAYLOAD, duplicate_url=URL)
        with pytest.raises(CampusError):
            await fetch_official_attachment(
                browser, target, RequestPolicy("example file.pdf"), tmp_path, max_bytes=200_000_000
            )
        assert not browser.context.request.calls
        assert not list(tmp_path.iterdir())

    asyncio.run(scenario())


def test_non_cms_relative_archive_response_binds_and_downloads(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = "/term-1/course-1/board/manager-1/post-1/stored.pdf"
        url = "https://dcs-learning.cnu.ac.kr/file" + path
        payload = {"header": {"code": 200}, "body": {"is_full": True, "path": path, "name": "example.pdf"}}
        target = OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", url)
        browser = page(Response(url=url), payload=payload, duplicate_url=url, late_url=url)
        fetched = await fetch_official_attachment(
            browser, target, RequestPolicy("example.pdf"), tmp_path, max_bytes=200_000_000
        )
        assert browser.context.request.calls == [(url, {}, 0)]
        assert fetched.temp_path.read_bytes() == BODY
        assert browser.route_handler is None
        fetched.temp_path.unlink()

        for stored_name, encoded_name in (
            ("stored notes.pdf", "stored%20notes.pdf"),
            ("자료.pdf", "%EC%9E%90%EB%A3%8C.pdf"),
        ):
            named_path = path.replace("stored.pdf", stored_name)
            named_url = url.replace("stored.pdf", encoded_name)
            named_payload = {
                "header": {"code": 200},
                "body": {"is_full": True, "path": named_path, "name": "example.pdf"},
            }
            named_target = OfficialAttachmentTarget("file-1", "archive", "post-1", "#official-file", named_url)
            named_browser = page(
                Response(url=named_url), payload=named_payload, duplicate_url=named_url, late_url=named_url
            )
            named_file = await fetch_official_attachment(
                named_browser, named_target, RequestPolicy("example.pdf"), tmp_path, max_bytes=200_000_000
            )
            assert named_browser.context.request.calls == [(named_url, {}, 0)]
            assert named_file.temp_path.read_bytes() == BODY
            named_file.temp_path.unlink()

        unavailable = page(Response(url=url, status=404), payload=payload)
        with pytest.raises(CampusError) as denied:
            await fetch_official_attachment(
                unavailable, target, RequestPolicy("example.pdf"), tmp_path, max_bytes=200_000_000
            )
        assert denied.value.code == "policy-blocked"
        assert unavailable.context.request.calls == [(url, {}, 0)]
        assert not list(tmp_path.iterdir())

        for bad_path, name in (
            (path.replace("/post-1/", "/../"), "example.pdf"),
            (path.replace("/post-1/", "/%2e%2e/"), "example.pdf"),
            (path.replace("/post-1/", "/other-post/"), "example.pdf"),
            (path, ""),
            (path, None),
        ):
            bad_payload = {"header": {"code": 200}, "body": {"is_full": True, "path": bad_path}}
            if name is not None:
                bad_payload["body"]["name"] = name
            browser = page(Response(url=url), payload=bad_payload)
            with pytest.raises(CampusError) as denied:
                await fetch_official_attachment(
                    browser, target, RequestPolicy("example.pdf"), tmp_path, max_bytes=200_000_000
                )
            assert denied.value.code == "policy-blocked"
            assert not browser.context.request.calls
            assert not list(tmp_path.iterdir())

    asyncio.run(scenario())


def test_invalid_bodies_and_dispose_failure_leave_no_temp(tmp_path: Path) -> None:
    asyncio.run(_invalid_bodies(tmp_path))


async def _invalid_bodies(tmp_path: Path) -> None:
    from test_cnu_request_policy import fixture_bytes

    cases = (
        ("example.pdf", "application/x-pdf", fixture_bytes({"zip_entries": ["file.txt"]})),
        ("example.docx", "application/x-unmapped", fixture_bytes({"zip_entries": ["other.xml"]})),
        ("example.hwp", None, bytes.fromhex("d0cf11e000000000")),
        ("example.hwp", None, bytes.fromhex("d0cf11e0")),
        ("example.txt", "text/plain", bytes.fromhex("fffe")),
        ("example.pdf", "video/mp4", BODY),
        ("example.pdf", "audio/mpeg", BODY),
        ("example.pdf", "application/x-pdf", b""),
    )
    for filename, mime, body in cases:
        response = Response(
            headers={"Content-Type": mime, "Content-Length": str(len(body))}
            if mime
            else {"Content-Length": str(len(body))},
            body=body,
        )
        try:
            await fetch_official_attachment(
                page(response), TARGET, RequestPolicy(filename), tmp_path, max_bytes=200_000_000
            )
        except CampusError:
            pass
        else:
            pytest.fail(f"Unapproved {filename}/{mime} response was accepted")
        assert not list(tmp_path.iterdir())
    underreported = Response(headers={"Content-Type": "application/x-pdf", "Content-Length": "6"})
    with pytest.raises(CampusError) as oversized:
        await fetch_official_attachment(
            page(underreported), TARGET, RequestPolicy("example.pdf"), tmp_path, max_bytes=8
        )
    assert oversized.value.code == "file-too-large"
    assert not list(tmp_path.iterdir())
    empty_without_length = Response(headers={"Content-Type": "application/x-pdf"}, body=b"")
    with pytest.raises(CampusError):
        await fetch_official_attachment(
            page(empty_without_length),
            TARGET,
            RequestPolicy("example.pdf"),
            tmp_path,
            max_bytes=200_000_000,
        )
    assert not list(tmp_path.iterdir())
    failing_dispose = Response()

    async def fail_dispose() -> None:
        raise RuntimeError("synthetic dispose failure")

    failing_dispose.dispose = fail_dispose
    browser = page(failing_dispose)
    with pytest.raises(RuntimeError, match="dispose failure"):
        await fetch_official_attachment(browser, TARGET, RequestPolicy("example.pdf"), tmp_path, max_bytes=200_000_000)
    assert browser.route_handler is None
    assert not list(tmp_path.iterdir())
    cancelled = Response()

    async def cancel_dispose() -> None:
        raise asyncio.CancelledError()

    cancelled.dispose = cancel_dispose
    with pytest.raises(asyncio.CancelledError):
        await fetch_official_attachment(
            page(cancelled), TARGET, RequestPolicy("example.pdf"), tmp_path, max_bytes=200_000_000
        )
    assert not list(tmp_path.iterdir())
    unroute_failure = page(Response(), unroute_error=True)
    with pytest.raises(RuntimeError, match="unroute failure"):
        await fetch_official_attachment(
            unroute_failure, TARGET, RequestPolicy("example.pdf"), tmp_path, max_bytes=200_000_000
        )
    assert not unroute_failure.listeners
    assert not list(tmp_path.iterdir())


def test_selected_file_url_binding_rejects_unapproved_paths() -> None:
    for origin, selected in zip(
        ("https://dcs-lcms.cnu.ac.kr", "https://dcs-learning.cnu.ac.kr"), FIXTURE["selected_urls"], strict=True
    ):
        selected_url = origin + selected["valid"]
        binding = bind_selected_file_request(
            selected_file_id="file-1", resolved_url=selected_url, source="official-control"
        )
        assert binding.origin == origin and binding.path == selected["valid"] and binding.selected_file_id == "file-1"
        for invalid in selected["invalid"]:
            with pytest.raises(SelectedFileDenied):
                bind_selected_file_request(
                    selected_file_id="file-1", resolved_url=origin + invalid, source="official-control"
                )
