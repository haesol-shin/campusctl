from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import urllib.parse
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from campusctl.browser_options import preflight_browser_mode
from campusctl.envelope import CampusError
from campusctl.lock import exclusive_lock
from campusctl.paths import data_dir as default_data_dir
from campusctl.paths import ensure_private_dir

PROTOCOL_TIMEOUT_SECONDS: float = 10.0
CLEANUP_TIMEOUT_SECONDS: float = 5.0
MAX_CDP_RESPONSE_BYTES = 64 * 1024
NORMAL_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Tests may supply a fake async_playwright-compatible factory without importing Playwright.
PLAYWRIGHT_FACTORY: Callable[[], Any] | None = None
_PRE_BROWSER_CHECK: ContextVar[Callable[[], None] | None] = ContextVar("pre_browser_check", default=None)

_UNSET = object()
_SYNC_PROFILE: ContextVar[Any | None] = ContextVar("sync_profile", default=None)


@dataclass
class _SessionLabels:
    """Explicit label snapshot for event callbacks. Not a task-local ContextVar view."""

    domain: str | None = None
    course: int | None = None
    page_kind: str | None = None
    active_document: int | None = None
    recorder: Any = None


@dataclass
class _InheritedLabels:
    domain: Any = _UNSET
    course: Any = _UNSET
    page_kind: Any = _UNSET
    wait_kind: Any = _UNSET
    document: Any = _UNSET


_INHERITED: ContextVar[_InheritedLabels | None] = ContextVar("profile_inherited_labels", default=None)
_SESSION_LABELS: ContextVar[_SessionLabels | None] = ContextVar("profile_session_labels", default=None)
_DOCUMENT_PATHS = {
    "/std/myLecture": "roster",
    "/std/todo": "todo",
    "/std/lecture": "course-entry",
    "/std/course": "lecture",
    "/std/task": "assignments",
    "/std/taskView": "assignment-detail",
    "/std/notice": "notices",
    "/std/noticeDetail": "notice-detail",
    "/std/archive": "archive",
    "/user/login": "login",
}


@contextmanager
def profile_context(recorder: Any) -> Iterator[None]:
    token = _SYNC_PROFILE.set(recorder)
    try:
        yield
    finally:
        _SYNC_PROFILE.reset(token)


def current_profile() -> Any | None:
    recorder = _SYNC_PROFILE.get()
    if recorder is not None:
        return recorder
    session = _SESSION_LABELS.get()
    if session is not None:
        return session.recorder
    return None


def _pick_label(explicit: Any, inherited: Any, snapshot: Any, *, in_session: bool) -> Any:
    if explicit is not _UNSET:
        return explicit
    if in_session:
        return snapshot
    if inherited is not _UNSET:
        return inherited
    return None


@contextmanager
def profile_labels(
    *,
    domain: Any = _UNSET,
    course: Any = _UNSET,
    page_kind: Any = _UNSET,
    wait_kind: Any = _UNSET,
    document: Any = _UNSET,
) -> Iterator[None]:
    """Inherit domain and course ordinals. Explicit null clears; omitted fields inherit."""
    parent = _INHERITED.get() or _InheritedLabels()
    resolved = _InheritedLabels(
        domain=parent.domain if domain is _UNSET else domain,
        course=parent.course if course is _UNSET else course,
        page_kind=parent.page_kind if page_kind is _UNSET else page_kind,
        wait_kind=parent.wait_kind if wait_kind is _UNSET else wait_kind,
        document=parent.document if document is _UNSET else document,
    )
    token = _INHERITED.set(resolved)
    session = _SESSION_LABELS.get()
    previous = None
    if session is not None:
        previous = (session.domain, session.course, session.page_kind)
        if domain is not _UNSET:
            session.domain = domain
        if course is not _UNSET:
            session.course = course
        if page_kind is not _UNSET:
            session.page_kind = page_kind
    try:
        yield
    finally:
        _INHERITED.reset(token)
        if session is not None and previous is not None:
            session.domain, session.course, session.page_kind = previous


def profile_span(
    phase: str,
    *,
    domain: Any = _UNSET,
    course: Any = _UNSET,
    window: int | None = None,
    page_kind: Any = _UNSET,
    wait_kind: Any = _UNSET,
    document: Any = _UNSET,
) -> Any:
    recorder = current_profile()
    if recorder is None:
        return nullcontext()
    inherited = _INHERITED.get() or _InheritedLabels()
    session = _SESSION_LABELS.get()
    in_session = session is not None
    if document is not _UNSET:
        resolved_document = document
    elif inherited.document is not _UNSET:
        resolved_document = inherited.document
    elif session is not None:
        resolved_document = session.active_document
    else:
        resolved_document = None
    resolved_wait = (
        wait_kind if wait_kind is not _UNSET else None if inherited.wait_kind is _UNSET else inherited.wait_kind
    )
    return recorder.span(
        phase,
        domain=_pick_label(
            domain, inherited.domain, None if session is None else session.domain, in_session=in_session
        ),
        course=_pick_label(
            course, inherited.course, None if session is None else session.course, in_session=in_session
        ),
        window=window,
        page_kind=_pick_label(
            page_kind, inherited.page_kind, None if session is None else session.page_kind, in_session=in_session
        ),
        wait_kind=resolved_wait,
        document=resolved_document,
    )


def profile_check_start() -> int | None:
    recorder = current_profile()
    return None if recorder is None else recorder.check_start()


def profile_diagnostic(
    check: str,
    *,
    started: int | None,
    bound_ns: int,
    counts: dict[str, int] | None = None,
    states: dict[str, bool | str] | None = None,
    domain: Any = _UNSET,
    course: Any = _UNSET,
    page_kind: Any = _UNSET,
) -> None:
    if started is None:
        return
    recorder = current_profile()
    if recorder is None:
        return
    inherited = _INHERITED.get() or _InheritedLabels()
    session = _SESSION_LABELS.get()
    in_session = session is not None
    recorder.diagnostic(
        check,
        started=started,
        bound_ns=bound_ns,
        counts=counts,
        states=states,
        domain=_pick_label(domain, inherited.domain, None if session is None else session.domain, in_session=in_session),
        course=_pick_label(course, inherited.course, None if session is None else session.course, in_session=in_session),
        page_kind=_pick_label(
            page_kind, inherited.page_kind, None if session is None else session.page_kind, in_session=in_session
        ),
    )


def profile_count(name: str, amount: int = 1) -> None:
    recorder = current_profile()
    if recorder is not None:
        recorder.count(name, amount)


def _request_identity(request: Any) -> int:
    impl = getattr(request, "_impl_obj", None)
    if impl is None and hasattr(request, "_object"):
        impl = request._object
    return id(request if impl is None else impl)


def _blank_document(url: str) -> bool:
    return url == "about:blank" or url.startswith("about:blank#") or url == "about:blank/"


def _document_page_kind(url: str) -> str:
    return _DOCUMENT_PATHS.get(urllib.parse.urlsplit(url).path, "other")


def _main_document_request(page: Any, request: Any) -> bool:
    if getattr(request, "resource_type", None) != "document":
        return False
    navigation = getattr(request, "is_navigation_request", None)
    if callable(navigation) and navigation() is False:
        return False
    frame = getattr(request, "frame", None)
    if frame is None or frame is not getattr(page, "main_frame", None):
        return False
    url = getattr(request, "url", "")
    return isinstance(url, str) and not _blank_document(url)


class _DocumentTimeline:
    """Passive main-frame document rows. Request objects are kept only for correlation."""

    def __init__(self, recorder: Any, page: Any, labels: _SessionLabels) -> None:
        self.recorder = recorder
        self.page = page
        self.labels = labels
        self._ordinals: dict[int, int] = {}
        self._bindings: list[tuple[str, Callable[..., Any], Any]] = []
        self._closed = False

    def attach(self) -> None:
        attached: list[tuple[str, Callable[..., Any], Any]] = []
        try:
            for event, handler in (
                ("request", self._on_request),
                ("response", self._on_response),
                ("requestfailed", self._on_failed),
                ("framenavigated", self._on_frame),
            ):
                self.page.on(event, handler)
                attached.append((event, handler, self.page))
            commit = self._bind_commit_event()
            if commit is not None:
                attached.append(commit)
        except Exception:
            self._detach(attached)
            raise
        self._bindings = attached

    def _bind_commit_event(self) -> tuple[str, Callable[..., Any], Any] | None:
        frame = getattr(self.page, "main_frame", None)
        impl = getattr(frame, "_impl_obj", None)
        emitter = getattr(impl, "_event_emitter", None) if impl is not None else None
        if emitter is None or not hasattr(emitter, "on") or not hasattr(emitter, "remove_listener"):
            return None
        emitter.on("navigated", self._on_impl_navigated)
        return ("navigated", self._on_impl_navigated, emitter)

    def detach(self) -> None:
        self._detach(self._bindings)
        self._bindings = []

    @staticmethod
    def _detach(bindings: list[tuple[str, Callable[..., Any], Any]]) -> None:
        for event, handler, target in bindings:
            with suppress(Exception):
                target.remove_listener(event, handler)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.recorder.close_documents()
        self.labels.active_document = None
        self._ordinals.clear()

    def _on_request(self, request: Any) -> None:
        if self._closed or not _main_document_request(self.page, request):
            return
        ordinal = self.recorder.open_document(
            page_kind=_document_page_kind(getattr(request, "url", "")),
            domain=self.labels.domain,
            course=self.labels.course,
        )
        if ordinal is None:
            self.labels.active_document = None
            return
        self._ordinals[_request_identity(request)] = ordinal
        self.labels.active_document = ordinal

    def _on_response(self, response: Any) -> None:
        """Correlate only. A response, including a late one, never commits a document."""
        return

    def _on_failed(self, request: Any) -> None:
        if self._closed:
            return
        ordinal = self._ordinals.pop(_request_identity(request), None)
        if ordinal is None:
            return
        self.recorder.fail_document(ordinal)
        if self.labels.active_document == ordinal:
            self.labels.active_document = None

    def _commit(self, request: Any) -> None:
        if request is None or self._closed:
            return
        ordinal = self._ordinals.pop(_request_identity(request), None)
        if ordinal is None:
            return
        self.recorder.commit_document(ordinal)

    def _on_impl_navigated(self, event: Any) -> None:
        if self._closed or not isinstance(event, dict) or event.get("error"):
            return
        document = event.get("newDocument")
        if not isinstance(document, dict):
            return
        self._commit(document.get("request"))

    def _on_frame(self, frame: Any) -> None:
        if (
            self._closed
            or frame is not getattr(self.page, "main_frame", None)
            or getattr(frame, "same_document", False)
        ):
            return
        self._commit(getattr(frame, "committed_request", None))


@contextmanager
def pre_browser_check(check: Callable[[], None]) -> Iterator[None]:
    """Scope a local-selection check to the next browser session lock."""
    token = _PRE_BROWSER_CHECK.set(check)
    try:
        yield
    finally:
        _PRE_BROWSER_CHECK.reset(token)


@dataclass(slots=True)
class _SessionLockLease:
    path: Path
    owner: asyncio.Task[Any]
    live: bool = True


_OWNED_SESSION_LOCK: ContextVar[_SessionLockLease | None] = ContextVar("owned_session_lock", default=None)
_ACTIVE_SESSION: ContextVar[bool] = ContextVar("active_browser_session", default=False)


def _session_lock_path(config: dict[str, Any], data_dir: Path | None) -> Path:
    root = ensure_private_dir(Path(data_dir).expanduser()) if data_dir is not None else default_data_dir(create=True)
    browser_config = config.get("browser", {})
    return Path(browser_config["lock_path"]).expanduser() if browser_config.get("lock_path") else root / "session.lock"


@contextmanager
def session_lock(config: dict[str, Any], *, data_dir: Path | None = None) -> Iterator[None]:
    """Own the single browser lock across browser cleanup and catalog publication.

    Pass the same config and data_dir to open_session inside this scope. A nested
    acquisition is prohibited; open_session recognizes this scope instead.
    """
    path = _session_lock_path(config, data_dir)
    owner = asyncio.current_task()
    if owner is None:
        raise RuntimeError("browser session lock requires an async task")
    inherited = _OWNED_SESSION_LOCK.get()
    if inherited is not None and inherited.live and inherited.owner is owner:
        raise RuntimeError("browser session lock already owned")
    with profile_span("lock"), exclusive_lock(path):
        lease = _SessionLockLease(path, owner)
        token = _OWNED_SESSION_LOCK.set(lease)
        try:
            yield
        finally:
            lease.live = False
            _OWNED_SESSION_LOCK.reset(token)


@dataclass
class BrowserSession:
    page: Any
    context: Any
    mode: str
    sso_popups: set[Any] | None = None
    sso_pending: set[asyncio.Task[Any]] | None = None


async def settle_sso_popups(session: BrowserSession, *, domain: str | None = None, timeout: float = 8.0) -> None:
    """Allow roster SSO popups to finish before course traversal."""
    with profile_span("sso-settle", domain=domain, wait_kind="timeout"):
        profile_count("sso_settles")
        try:
            async with asyncio.timeout(timeout):
                if session.sso_pending:
                    await asyncio.gather(*tuple(session.sso_pending))
                popups = session.sso_popups
                if popups:

                    async def wait_for_close(popup: Any) -> None:
                        if not popup.is_closed():
                            await popup.wait_for_event("close")

                    await asyncio.gather(*(wait_for_close(popup) for popup in tuple(popups)))
        except Exception:
            pass


def _roster_page_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "dcs-learning.cnu.ac.kr" and parts.path == "/std/myLecture"


def _track_sso_popups(context: Any) -> tuple[set[Any], set[asyncio.Task[Any]], Callable[[], None]]:
    popups: set[Any] = set()
    pending: set[asyncio.Task[Any]] = set()

    def inspect(candidate: Any) -> None:
        async def inspect_opener() -> None:
            try:
                opener = await candidate.opener()
                if opener is not None and _roster_page_url(opener.url):
                    popups.add(candidate)
            except Exception:
                pass

        task = asyncio.create_task(inspect_opener())
        pending.add(task)
        task.add_done_callback(pending.discard)

    context.on("page", inspect)

    def remove() -> None:
        context.remove_listener("page", inspect)
        for task in pending:
            task.cancel()

    return popups, pending, remove


async def bounded(awaitable: Any, seconds: float, what: str) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except TimeoutError:
        raise CampusError(
            "browser-timeout",
            f"Timed out while {what}.",
            "Check the browser and try again.",
            "error",
        ) from None


async def close_resource(resource: Any) -> None:
    """Best-effort, bounded cleanup for a Playwright page or context."""
    if resource is None:
        return
    with suppress(Exception), profile_span("wait", wait_kind="timeout"):
        await bounded(resource.close(), CLEANUP_TIMEOUT_SECONDS, "closing a browser resource")


async def _read_http_response(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readuntil(b"\r\n\r\n")
    if len(header) > MAX_CDP_RESPONSE_BYTES:
        raise ValueError("CDP response is too large")
    lines = header[:-4].split(b"\r\n")
    status = lines[0].split()
    if len(status) < 2 or status[1] != b"200":
        raise ValueError("CDP endpoint returned an unsuccessful response")

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" not in line:
            raise ValueError("CDP endpoint returned invalid headers")
        name, value = line.split(b":", 1)
        headers[name.decode("ascii", "ignore").lower()] = value.strip().decode("ascii", "ignore")

    body_limit = MAX_CDP_RESPONSE_BYTES - len(header)
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = bytearray()
        wire_size = 0
        while True:
            line = await reader.readline()
            wire_size += len(line)
            if not line.endswith(b"\r\n") or wire_size > body_limit:
                raise ValueError("CDP response is too large or malformed")
            try:
                chunk_size = int(line[:-2].split(b";", 1)[0], 16)
            except ValueError:
                raise ValueError("CDP endpoint returned an invalid chunk") from None
            if chunk_size == 0:
                while True:
                    trailer = await reader.readline()
                    wire_size += len(trailer)
                    if wire_size > body_limit:
                        raise ValueError("CDP response is too large")
                    if trailer == b"\r\n":
                        return bytes(body)
                    if not trailer:
                        raise ValueError("CDP endpoint returned an incomplete response")
            if wire_size + chunk_size + 2 > body_limit:
                raise ValueError("CDP response is too large")
            body.extend(await reader.readexactly(chunk_size))
            terminator = await reader.readexactly(2)
            if terminator != b"\r\n":
                raise ValueError("CDP endpoint returned an invalid chunk")
            wire_size += chunk_size + len(terminator)

    if "content-length" in headers:
        try:
            content_length = int(headers["content-length"])
        except ValueError:
            raise ValueError("CDP endpoint returned an invalid content length") from None
        if content_length < 0 or content_length > body_limit:
            raise ValueError("CDP response is too large")
        return await reader.readexactly(content_length)

    chunks = bytearray()
    while True:
        chunk = await reader.read(min(8192, body_limit - len(chunks) + 1))
        if not chunk:
            return bytes(chunks)
        chunks.extend(chunk)
        if len(chunks) > body_limit:
            raise ValueError("CDP response is too large")


async def _fetch_cdp_response(endpoint: str) -> tuple[dict[str, Any], urllib.parse.SplitResult]:
    parts = urllib.parse.urlsplit(endpoint)
    if parts.scheme.lower() != "http" or not parts.hostname or parts.username or parts.password:
        raise ValueError("CDP endpoint must be an HTTP URL")

    port = parts.port or 80
    host_header = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if port != 80:
        host_header += f":{port}"
    path = urllib.parse.quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        query = urllib.parse.quote(parts.query, safe="/?@!$&'()*+,;=:-._~%")
        path += f"?{query}"
    reader, writer = await asyncio.open_connection(parts.hostname, port)
    try:
        request = f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
        writer.write(request.encode("ascii"))
        await writer.drain()
        raw = await _read_http_response(reader)
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CDP endpoint returned invalid JSON")
    return payload, parts


async def resolve_cdp_ws_url(endpoint: str) -> str:
    """Resolve a CDP WebSocket URL using a bounded, size-limited HTTP request."""
    with profile_span("wait", wait_kind="timeout"):
        payload, endpoint_parts = await asyncio.wait_for(
            _fetch_cdp_response(endpoint), timeout=PROTOCOL_TIMEOUT_SECONDS
        )
    ws_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url.startswith(("ws://", "wss://")):
        raise ValueError("CDP endpoint did not provide a WebSocket URL")

    endpoint_port = endpoint_parts.port
    if endpoint_port is None and "9223" in endpoint:
        endpoint_port = 9223
    if endpoint_port and f":{endpoint_port}" not in ws_url:
        ws_url = ws_url.replace("127.0.0.1", f"127.0.0.1:{endpoint_port}")
    return ws_url


async def apply_normal_user_agent(page: Any) -> None:
    """Normalize only the page's User-Agent using the standard CDP override."""
    client = await bounded(
        page.context.new_cdp_session(page), PROTOCOL_TIMEOUT_SECONDS, "creating a browser protocol session"
    )
    await bounded(
        client.send("Network.setUserAgentOverride", {"userAgent": NORMAL_CHROME_USER_AGENT}),
        PROTOCOL_TIMEOUT_SECONDS,
        "applying the browser User-Agent",
    )


def _browser_not_installed() -> CampusError:
    return CampusError(
        "browser-not-installed",
        "Playwright or its Chromium browser is not installed.",
        "Run 'campusctl setup' to install the browser.",
        "user-action",
    )


def _endpoint_unreachable() -> CampusError:
    return CampusError(
        "browser-endpoint-unreachable",
        "The configured browser endpoint could not be reached.",
        "Check browser.cdp_endpoint and ensure the browser's CDP endpoint is available.",
        "user-action",
    )


def _playwright_manager() -> Any:
    if PLAYWRIGHT_FACTORY is not None:
        return PLAYWRIGHT_FACTORY()
    from playwright.async_api import async_playwright

    return async_playwright()


def _check_display() -> None:
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise CampusError(
            "display-unavailable",
            "No graphical display is available for a visible browser session.",
            "Configure a display or run the command with 'xvfb-run'.",
            "user-action",
        )


def _check_chromium(playwright: Any, executable_path: str | None) -> None:
    executable = executable_path or getattr(playwright.chromium, "executable_path", None)
    if executable is not None:
        try:
            installed = Path(executable).is_file()
        except (OSError, TypeError, ValueError):
            installed = False
        if not installed:
            raise _browser_not_installed()


def chromium_installed(executable_path: str | None = None) -> bool:
    """Check for a usable managed or configured Chromium executable."""
    try:
        context_manager = importlib.import_module("playwright.sync_api._context_manager")
        playwright = context_manager.PlaywrightContextManager().start()
        try:
            _check_chromium(playwright, executable_path)
            return True
        finally:
            playwright.stop()
    except Exception:
        return False


def _consume_cleanup_result(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        with suppress(BaseException):
            task.exception()


async def _bounded_cleanup(awaitable: Any) -> None:
    task = asyncio.ensure_future(awaitable)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CLEANUP_TIMEOUT_SECONDS
    with profile_span("wait", wait_kind="timeout"):
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=CLEANUP_TIMEOUT_SECONDS)
        except TimeoutError:
            task.cancel()
            task.add_done_callback(_consume_cleanup_result)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is None or not current.cancelling():
                return
            remaining = deadline - loop.time()
            if remaining > 0:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
                except BaseException:
                    task.cancel()
                    task.add_done_callback(_consume_cleanup_result)
            else:
                task.cancel()
                task.add_done_callback(_consume_cleanup_result)
            raise
        except Exception:
            pass


async def _stop_playwright(manager: Any, playwright: Any) -> None:
    exit_context = getattr(manager, "__aexit__", None)
    if exit_context is not None:
        await _bounded_cleanup(exit_context(None, None, None))
        return
    stop = getattr(manager, "stop", None) or getattr(playwright, "stop", None)
    if stop is not None:
        await _bounded_cleanup(stop())


@asynccontextmanager
async def open_session(
    config: dict[str, Any],
    *,
    data_dir: Path | None = None,
    headless: bool | None = None,
    operation: str | None = None,
    require_owned_page: bool = False,
) -> AsyncIterator[BrowserSession]:
    if operation is not None:
        headless = preflight_browser_mode(config, operation, override=headless)
    elif headless is None:
        # Legacy callers have no operation to gate; config cannot opt them in.
        headless = False
    browser_config = config.get("browser", {})
    endpoint = browser_config.get("cdp_endpoint")
    mode = "cdp" if endpoint else "local"
    if headless and mode == "cdp":
        raise CampusError(
            "headless-unavailable",
            "Headless mode cannot be used with a configured CDP browser.",
            "Use a local browser for headless mode.",
            "user-action",
        )
    root = ensure_private_dir(Path(data_dir).expanduser()) if data_dir is not None else default_data_dir(create=True)
    lock_path = _session_lock_path(config, data_dir)

    lease = _OWNED_SESSION_LOCK.get()
    owns_lock = lease is not None and lease.live and lease.owner is asyncio.current_task()
    if owns_lock and _ACTIVE_SESSION.get():
        raise RuntimeError("browser session already active")
    if owns_lock and lease.path != lock_path:
        raise RuntimeError("browser session lock does not match the owned lock")
    with (
        profile_span("lock") if not owns_lock else nullcontext(),
        nullcontext() if owns_lock else exclusive_lock(lock_path),
    ):
        check = _PRE_BROWSER_CHECK.get()
        if check is not None:
            check()
        if mode == "local" and not headless:
            _check_display()

        label_state = _SessionLabels(recorder=current_profile())
        label_token = _SESSION_LABELS.set(label_state)
        timeline: _DocumentTimeline | None = None
        manager = None
        playwright = None
        context = None
        page = None
        owns_page = False
        sso_tracking = None
        primary_error: BaseException | None = None
        try:
            with profile_span("playwright"):
                try:
                    manager = _playwright_manager()
                except ImportError:
                    raise _browser_not_installed() from None
                playwright = await bounded(manager.start(), PROTOCOL_TIMEOUT_SECONDS, "starting Playwright")

            if mode == "cdp":
                try:
                    ws_url = await bounded(
                        resolve_cdp_ws_url(endpoint),
                        PROTOCOL_TIMEOUT_SECONDS,
                        "resolving the browser endpoint",
                    )
                    with profile_span("launch-connect", wait_kind="timeout"):
                        browser = await bounded(
                            playwright.chromium.connect_over_cdp(ws_url, timeout=PROTOCOL_TIMEOUT_SECONDS * 1000),
                            PROTOCOL_TIMEOUT_SECONDS,
                            "connecting to the browser endpoint",
                        )
                except Exception:
                    raise _endpoint_unreachable() from None

                contexts = browser.contexts
                if not contexts:
                    raise CampusError(
                        "browser-endpoint-unreachable",
                        "The configured browser endpoint did not provide a usable browser context.",
                        "Ensure the browser exposes its default CDP context, then retry.",
                        "user-action",
                    )
                context = contexts[0]
                pages = context.pages
                if require_owned_page or not pages:
                    # Combined sync owns a fresh page even when CDP has parked tabs.
                    # Cleanup closes this page, leaving parked tabs untouched.
                    with profile_span("launch-connect", wait_kind="timeout"):
                        page = await bounded(context.new_page(), PROTOCOL_TIMEOUT_SECONDS, "opening a browser page")
                    owns_page = True
                else:
                    page = pages[0]
            else:
                _check_chromium(playwright, browser_config.get("executable_path"))
                profile_dir = root / "profile" / str(config.get("provider", "cnu"))
                ensure_private_dir(profile_dir.parent)
                try:
                    with profile_span("launch-connect", wait_kind="timeout"):
                        context = await bounded(
                            playwright.chromium.launch_persistent_context(
                                user_data_dir=str(profile_dir),
                                headless=headless,
                                executable_path=browser_config.get("executable_path"),
                            ),
                            PROTOCOL_TIMEOUT_SECONDS,
                            "launching the local browser",
                        )
                except CampusError:
                    raise
                except Exception:
                    raise CampusError(
                        "browser-launch-failed",
                        "The local browser could not be started.",
                        "Check the browser installation and browser.executable_path setting.",
                        "error",
                    ) from None
                pages = context.pages
                if pages:
                    page = pages[0]
                else:
                    with profile_span("launch-connect", wait_kind="timeout"):
                        page = await bounded(context.new_page(), PROTOCOL_TIMEOUT_SECONDS, "opening a browser page")

            if operation in {
                "lectures.sync",
                "assignments.sync",
                "notices.sync",
                "materials.sync",
                "materials.download",
                "assignments.fetch",
                "notices.fetch",
            }:
                sso_tracking = _track_sso_popups(context)
            recorder = current_profile()
            if recorder is not None and getattr(recorder, "enabled", False) and hasattr(page, "on"):
                timeline = _DocumentTimeline(recorder, page, label_state)
                timeline.attach()
            try:
                with profile_span("user-agent"):
                    await apply_normal_user_agent(page)
            except CampusError:
                raise
            except Exception:
                raise CampusError(
                    "browser-session-failed",
                    "The browser session could not be prepared.",
                    "Check the browser installation and try again.",
                    "error",
                ) from None

            active_token = _ACTIVE_SESSION.set(True)
            try:
                yield BrowserSession(
                    page=page,
                    context=context,
                    mode=mode,
                    sso_popups=sso_tracking[0] if sso_tracking else None,
                    sso_pending=sso_tracking[1] if sso_tracking else None,
                )
            finally:
                _ACTIVE_SESSION.reset(active_token)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                if timeline is not None:
                    timeline.close()
                with profile_span("teardown"):
                    try:
                        try:
                            if sso_tracking is not None:
                                sso_tracking[2]()
                            if timeline is not None:
                                timeline.detach()
                            if mode == "local":
                                await close_resource(context)
                            elif owns_page:
                                await close_resource(page)
                        finally:
                            await _stop_playwright(manager, playwright)
                    except BaseException:
                        if primary_error is None:
                            raise
            finally:
                _SESSION_LABELS.reset(label_token)
