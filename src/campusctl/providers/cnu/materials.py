"""Metadata-only enumeration of official course archive attachments."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_check_start, profile_diagnostic, profile_span
from campusctl.envelope import CampusError
from campusctl.identity import material_entity_id

from .course_context import _TOPBAR_COURSE_JS, SECTION_RESPONSE_TIMEOUT_MS
from .readiness import wait_page_ready

_MODAL = '#file_download.show, #file_download[style*="display: block"]'
_ARCHIVE_MENU = 'a[href="/std/archive"]'
_WAIT_MS = 7000
_ALLOWED_EXTENSIONS = frozenset(
    {
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
    }
)
_STORAGE_ORIGINS = frozenset({"https://dcs-learning.cnu.ac.kr", "https://dcs-lcms.cnu.ac.kr"})
_TRAILING_VIEW = re.compile(r"\s*바로보기\s*$")

_ARCHIVE_STATE_JS = (
    """/* archiveMetadataState */(expectedDocument) => {
    const table = document.querySelector('#table_list');
    const body = table && table.querySelector('#listBody');
    const counter = document.querySelector('#totalCnt strong');
    const count = counter && Number(counter.textContent.replace(/,/g, '').trim());
    const rows = body ? [...body.querySelectorAll('tr')] : [];
    const blank = document.querySelector('#listBlankDiv');
    const empty = blank && getComputedStyle(blank).display !== 'none';
    const current = document.querySelector('#topbarCurrentLecture');
    const name = current?.textContent?.replace(/\\s+/g, '').trim();
    const selected = [...document.querySelectorAll('#topbarLectureDropdown a[data-act="changeLecture"][data-courseid]')]
        .filter(link => link.textContent.replace(/\\s+/g, '').trim() === name);
    const modal = document.querySelector("""
    + json.dumps(_MODAL)
    + """);
    return {
        completed: !!table && !!body && Number.isSafeInteger(count) &&
            count >= 0 && rows.length <= Number(table.getAttribute('data-page-size')) &&
            (count !== 0 || !!empty),
        total_count: count,
        row_count: rows.length,
        page_size: Number(table && table.getAttribute('data-page-size')),
        current_page: Number(document.querySelector('#listPage .page-item.active [data-page]')?.getAttribute('data-page') || 1),
        selected_course_id: selected.length === 1 ? selected[0].getAttribute('data-courseid') : null,
        posts: rows.flatMap(row => [...row.querySelectorAll('[data-act="file"][data-boarditem_no]')].map(icon => {
            const title = row.querySelector('[data-act="detail"][data-id], [data-act="titleDetailContents"]');
            return {board_item_id: icon.getAttribute('data-boarditem_no'),
                    title: title && title.textContent};
        })),
        same_document: expectedDocument != null && document === expectedDocument,
        archive_path: location.pathname === '/std/archive',
        modal_clear: !modal && !document.querySelector('.modal-backdrop') &&
            !document.body.classList.contains('modal-open')
    };
}"""
)
_TARGETS_JS = """/* archiveMetadataTargets */({modalOnly, boardItemId}) => {
    const icons = modalOnly ? [] : [...document.querySelectorAll('#listBody tr [data-act="file"][data-boarditem_no]')]
        .filter(el => el.getAttribute('data-boarditem_no') === boardItemId);
    const root = modalOnly ? document.getElementById('file_download') :
        icons.length === 1 ? icons[0].closest('tr') : null;
    if (!root || (!modalOnly && !root.closest('#listBody'))) return [];
    return [...root.querySelectorAll('[data-act="downloadFile"], [data-act="externalFile"]')].map(el => ({
        data_id: el.getAttribute('data-id'), file_id: el.getAttribute('data-fileid'),
        text: el.innerText, url: el.getAttribute('data-url') || el.getAttribute('href'),
        media_type: el.getAttribute('data-mime'), size_bytes: el.getAttribute('data-size'),
        official: el.getAttribute('data-act') === 'downloadFile'
    }));
}"""
_CLICK_ICON_JS = """/* archiveMetadataClickIcon */(id) => {
    const icon = [...document.querySelectorAll('[data-act="file"][data-boarditem_no]')]
        .find(el => el.getAttribute('data-boarditem_no') === id);
    if (!icon) throw Error('missing archive icon');
    icon.click();
}"""
_PAGE_JS = """/* archiveMetadataPage */(number) => {
    const link = [...document.querySelectorAll('#listPage [data-page]')]
        .find(el => Number(el.getAttribute('data-page')) === number);
    if (!link) throw Error('missing archive page');
    link.click();
}"""


_CLOSE_MODAL_JS = """/* archiveMetadataCloseModal */() => {
    const modal = document.getElementById('file_download');
    if (!modal) return;
    const close = modal.querySelector('[data-bs-dismiss="modal"], .btn-close');
    if (close) close.click();
    modal.classList.remove('show'); modal.style.display = 'none';
    document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
    document.body.classList.remove('modal-open');
}"""


def _failure(code: str, course: Mapping[str, Any]) -> CampusError:
    return CampusError(
        code,
        f"Could not completely enumerate course {course['course_id']}.",
        "Check the course archive in the LMS and retry.",
        "error",
    )


def _display_name(target: Mapping[str, Any], names: Mapping[str, str]) -> str:
    text = target.get("text")
    if isinstance(text, str) and text.strip():
        return text
    file_id = target.get("data_id") or target.get("file_id")
    return names.get(file_id, "") if isinstance(file_id, str) else ""


def _target_row(
    target: Mapping[str, Any], course: Mapping[str, Any], post: Mapping[str, str], names: Mapping[str, str]
) -> dict[str, Any]:
    file_id = target.get("data_id") or target.get("file_id")
    if not isinstance(file_id, str) or not file_id.strip():
        raise _failure("item-identity-missing", course)
    file_id = file_id.strip()
    display_name = _display_name(target, names)
    filename = _TRAILING_VIEW.sub("", display_name)
    url = target.get("url")
    origin = ""
    if isinstance(url, str) and url:
        parsed = urlsplit(url)
        if parsed.scheme.lower() in {"https", "http"}:
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
        else:
            url = None  # The observed javascript:; href is an action, not a storage URL.
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media_type = target.get("media_type")
    media_type = media_type if isinstance(media_type, str) and media_type else None
    size = target.get("size_bytes")
    try:
        size = int(size) if size is not None and str(size).strip() else None
    except (ValueError, TypeError):
        size = None
    downloadable = (
        target.get("official") is True
        and bool(filename.strip())
        and extension in _ALLOWED_EXTENSIONS
        and (not url or origin in _STORAGE_ORIGINS)
        and not (media_type and media_type.lower().split(";", 1)[0].strip().startswith(("video/", "audio/")))
    )
    return {
        "entity_id": material_entity_id(str(course["course_id"]), file_id),
        "course": {"id": course["course_id"], "label": course["label"]},
        "archive_entry": {"board_item_id": post["board_item_id"], "title": post["title"]},
        "file_id": file_id,
        "display_name": display_name,
        "filename": filename,
        "media_type": media_type,
        "size_bytes": size,
        "downloadable": downloadable,
        "unavailable_reason": None
        if downloadable
        else ("official-name-unavailable" if not filename.strip() else "unsupported-media-type"),
    }


async def _step(awaitable: Any, description: str) -> Any:
    return await bounded(awaitable, PROTOCOL_TIMEOUT_SECONDS, description)


class _RequestWindow:
    """Correlate responses to requests initiated after this course's document."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self.requests: list[Any] = []
        self.responses: dict[int, Any] = {}
        self.commits: list[int] = []
        self.changed = asyncio.Event()

    def start(self) -> None:
        self.page.on("framenavigated", self._navigated)
        self.page.on("request", self._started)
        self.page.on("response", self._received)

    def close(self) -> None:
        self.page.remove_listener("framenavigated", self._navigated)
        self.page.remove_listener("request", self._started)
        self.page.remove_listener("response", self._received)

    def _navigated(self, frame: Any) -> None:
        if frame is self.page.main_frame and urlsplit(frame.url).path == "/std/archive":
            self.commits.append(len(self.requests))

    def _started(self, request: Any) -> None:
        self.requests.append(request)
        self.changed.set()

    def _received(self, response: Any) -> None:
        self.responses[id(response.request)] = response
        self.changed.set()

    def _main_frame(self, request: Any) -> bool:
        try:
            return request.frame is self.page.main_frame
        except Exception:
            return False

    @staticmethod
    def _archive_referer(request: Any) -> bool:
        try:
            value = next(value for key, value in request.headers.items() if key.lower() == "referer")
            url = urlsplit(value)
            return url.scheme == "https" and url.netloc == "dcs-learning.cnu.ac.kr" and url.path == "/std/archive"
        except (AttributeError, StopIteration, TypeError):
            return False

    def matches(self, path: str, method: str, *, after: int = 0) -> list[tuple[int, Any]]:
        return [
            (index, request)
            for index, request in enumerate(self.requests, start=1)
            if index > after
            and self._main_frame(request)
            and request.method.upper() == method
            and urlsplit(request.url).path == path
        ]

    async def wait_response(self, path: str, method: str, *, after: int = 0) -> Any:
        """Finish exactly one matching main-frame response after the action boundary."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SECTION_RESPONSE_TIMEOUT_MS / 1000

        async def completed() -> Any:
            while True:
                matches = self.matches(path, method, after=after)
                if len(matches) > 1:
                    raise ValueError("duplicate archive response")
                if matches:
                    response = self.responses.get(id(matches[0][1]))
                    if response is not None:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise TimeoutError
                        if await bounded(response.finished(), remaining, "finishing archive response") is not None:
                            raise ValueError("archive response incomplete")
                        if response.status != 200 or len(self.matches(path, method, after=after)) != 1:
                            raise ValueError("archive response failed or duplicated")
                        return response
                self.changed.clear()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait_for(self.changed.wait(), remaining)

        with profile_span("response-completion", wait_kind="response", domain="materials", page_kind="archive"):
            return await bounded(completed(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "waiting for archive response")


_ATTACH_LIST = "/api/v1/archive/getAttachFileList"


def _attachment_names(body: Any) -> dict[str, str]:
    """Extract explicit file-ID/name pairs only; never infer a URL basename."""
    if isinstance(body, dict):
        for key in ("body", "attachFileList", "files", "list", "data", "result"):
            if key in body:
                return _attachment_names(body[key])
    if not isinstance(body, list):
        raise ValueError("attachment list has no file records")
    names: dict[str, str] = {}
    for entry in body:
        if not isinstance(entry, dict):
            raise ValueError("invalid attachment list record")
        file_id = next(
            (
                entry.get(key)
                for key in ("boarditem_attach_file_no", "file_id", "fileId", "data_id", "id")
                if entry.get(key)
            ),
            None,
        )
        name = next(
            (entry.get(key) for key in ("file_name", "filename", "fileName", "file_nm", "name") if entry.get(key)),
            None,
        )
        if not isinstance(file_id, str) or not file_id.strip() or file_id in names:
            raise ValueError("missing or duplicate attachment ID")
        names[file_id] = name if isinstance(name, str) else ""
    return names


_ARCHIVE_LIST = "/api/v1/archive/list"


async def _archive_navigation(
    page: Any,
    action: Callable[[], Any],
    *,
    document: bool = True,
    expected_course_id: str | None = None,
) -> None:
    """Accept one list request originating after this archive navigation only."""
    if expected_course_id is None:
        expected_course_id = await _step(page.evaluate(_TOPBAR_COURSE_JS), "checking archive course")
    if not isinstance(expected_course_id, str) or not expected_course_id:
        raise ValueError("archive course identity unavailable")
    activity = _RequestWindow(page)
    activity.start()
    try:
        before = len(activity.requests)

        def selected(response: Any) -> bool:
            request = response.request
            records = activity.matches(_ARCHIVE_LIST, "POST", after=before)
            if not records:
                return False
            if document:
                if len(activity.commits) != 1:
                    return False
                records = [entry for entry in records if entry[0] > activity.commits[0]]
            return bool(records) and request is records[0][1] and activity._archive_referer(request)

        with profile_span("response-completion", wait_kind="response", domain="materials", page_kind="archive"):
            async with page.expect_response(
                selected, timeout=SECTION_RESPONSE_TIMEOUT_MS if document else _WAIT_MS
            ) as pending:
                with profile_span(
                    "document-commit" if document else "archive-page",
                    wait_kind="navigation" if document else "action",
                    domain="materials",
                    page_kind="archive",
                ):
                    await _step(action(), "opening archive list")
            response = await _step(pending.value, "waiting for archive list response")
        completed = await activity.wait_response(_ARCHIVE_LIST, "POST", after=before)
        if completed is not response:
            raise ValueError("archive list response changed during navigation")
        await wait_page_ready(page, "archive", expected_course_id=expected_course_id, domain="materials")
        records = activity.matches(_ARCHIVE_LIST, "POST", after=before)
        if (
            len(records) != 1
            or (document and (len(activity.commits) != 1 or records[0][0] <= activity.commits[0]))
            or not activity._archive_referer(records[0][1])
            or response.request is not records[0][1]
            or response.status != 200
        ):
            raise ValueError("archive list was not bound to this navigation")
        payload = await _step(response.json(), "parsing archive list response")
        if not isinstance(payload, (dict, list)):
            raise ValueError("archive list response was not JSON records")
    finally:
        activity.close()


async def arm_materials_capture(page: Any) -> _RequestWindow:
    """Observe the first archive list request before the section document commits."""
    capture = _RequestWindow(page)
    capture.start()
    return capture


async def open_materials_section(
    page: Any,
    action: Callable[[], Any],
    *,
    capture: _RequestWindow | None = None,
) -> None:
    """Open the archive with its observer armed before list XHRs."""

    async def navigate() -> None:
        if capture is None:
            await _archive_navigation(page, action)
        else:
            await _step(action(), "opening archive section")

    try:
        await navigate()
    except BaseException:
        if capture is not None:
            capture.close()
        raise


async def collect_materials_rows(
    page: Any,
    course: Mapping[str, Any],
    *,
    capture: _RequestWindow | None = None,
) -> list[dict[str, Any]]:
    """Collect the complete archive from its validated, committed section document."""
    try:
        if urlsplit(page.main_frame.url).path != "/std/archive":
            raise _failure("course-sync-failed", course)
        if capture is not None:
            response = await capture.wait_response(_ARCHIVE_LIST, "POST")
            records = capture.matches(_ARCHIVE_LIST, "POST")
            if (
                len(capture.commits) != 1
                or len(records) != 1
                or records[0][0] <= capture.commits[0]
                or not capture._archive_referer(records[0][1])
                or response.request is not records[0][1]
            ):
                raise _failure("course-sync-failed", course)
            payload = await _step(response.json(), "parsing archive list response")
            if not isinstance(payload, (dict, list)):
                raise _failure("course-sync-failed", course)
        await wait_page_ready(page, "archive", expected_course_id=course["course_id"], domain="materials")
        with profile_span("extract", domain="materials"):
            rows = await enumerate_archive(page, course)
        return rows
    finally:
        if capture is not None:
            capture.close()


async def _archive_state(
    page: Any,
    expected_page: int,
    expected_total: int | None = None,
    expected_course_id: str | None = None,
    *,
    expected_document: Any = None,
    observed: dict[str, dict] | None = None,
) -> dict:
    state = await _step(page.evaluate(_ARCHIVE_STATE_JS, expected_document), "observing archive table")
    if observed is not None:
        observed.clear()
        if isinstance(state, dict):
            counts = {
                name: state[source]
                for name, source in (
                    ("rendered_rows", "row_count"),
                    ("tot_cnt", "total_count"),
                    ("page_size", "page_size"),
                    ("current_page", "current_page"),
                )
                if type(state.get(source)) is int and state[source] >= 0
            }
            counts["expected_page"] = expected_page
            if expected_total is not None:
                counts["expected_total"] = expected_total
            if counts.get("page_size", 0) > 0 and "tot_cnt" in counts:
                counts["expected_rows"] = min(
                    counts["page_size"], max(0, counts["tot_cnt"] - (expected_page - 1) * counts["page_size"])
                )
            states = {}
            if type(state.get("completed")) is bool:
                states["completed"] = state["completed"]
            if expected_course_id is not None:
                states["ids_match"] = state.get("selected_course_id") == expected_course_id
            for name in ("modal_clear", "same_document"):
                if type(state.get(name)) is bool and (name != "same_document" or expected_document is not None):
                    states[name] = state[name]
            if type(state.get("archive_path")) is bool:
                states["route_match"] = state["archive_path"]
            observed.update(counts=counts, states=states)
    if (
        not isinstance(state, dict)
        or state.get("completed") is not True
        or type(state.get("total_count")) is not int
        or type(state.get("page_size")) is not int
        or type(state.get("row_count")) is not int
        or not isinstance(state.get("posts"), list)
        or state["page_size"] < 1
        or state["total_count"] < 0
        or state.get("current_page") != expected_page
        or (expected_total is not None and state["total_count"] != expected_total)
        or (expected_course_id is not None and state.get("selected_course_id") != expected_course_id)
    ):
        raise ValueError("archive table did not complete")
    expected_rows = min(state["page_size"], max(0, state["total_count"] - (expected_page - 1) * state["page_size"]))
    if state["row_count"] != expected_rows:
        raise ValueError("archive page rows incomplete")
    return state


async def _wait_archive_state(
    page: Any,
    expected_page: int,
    expected_total: int | None = None,
    expected_course_id: str | None = None,
) -> dict:
    """Wait for the bound list response to finish rendering every expected post."""
    started = profile_check_start()
    observed: dict[str, dict] | None = {} if started is not None else None

    async def complete() -> dict:
        while True:
            try:
                return await _archive_state(page, expected_page, expected_total, expected_course_id, observed=observed)
            except ValueError:
                await asyncio.sleep(0.05)

    with profile_span("page-readiness", wait_kind="readiness", domain="materials", page_kind="archive"):
        try:
            return await bounded(complete(), _WAIT_MS / 1000, "waiting for complete archive rows")
        except CampusError as error:
            if started is not None:
                profile_diagnostic(
                    "archive-state",
                    started=started,
                    bound_ns=_WAIT_MS * 1_000_000,
                    counts=observed.get("counts") if observed else None,
                    states=observed.get("states") if observed else None,
                    domain="materials",
                    page_kind="archive",
                )
            if error.code != "browser-timeout" or expected_course_id is None:
                raise
            raise _failure("course-sync-failed", {"course_id": expected_course_id}) from None


async def _select_page(page: Any, number: int) -> None:
    await _archive_navigation(page, lambda: page.evaluate(_PAGE_JS, number), document=False)


async def _post_names(
    page: Any, activity: _RequestWindow, after: int, post_id: str
) -> tuple[dict[str, str], list[dict[str, Any]], bool]:
    """Bind file controls to this post's completed attachment response when available."""
    started = profile_check_start()
    modal_controls: int | None = None
    response_items: int | None = None
    ids_match: bool | None = None
    route_match: bool | None = None

    def diagnose() -> None:
        if started is None:
            return
        counts = {}
        states = {}
        if modal_controls is not None:
            counts["modal_controls"] = modal_controls
        if response_items is not None:
            counts["response_items"] = response_items
        if ids_match is not None:
            states["ids_match"] = ids_match
        if route_match is not None:
            states["route_match"] = route_match
        profile_diagnostic(
            "modal-binding",
            started=started,
            bound_ns=_WAIT_MS * 1_000_000,
            counts=counts,
            states=states,
            domain="materials",
            page_kind="archive",
        )

    async def controls() -> list[dict[str, Any]]:
        modal = await page.evaluate(_TARGETS_JS, {"modalOnly": True, "boardItemId": post_id})
        return (
            modal
            if isinstance(modal, list) and modal
            else await page.evaluate(_TARGETS_JS, {"modalOnly": False, "boardItemId": post_id})
        )

    async def matching(names: dict[str, str]) -> list[dict[str, Any]]:
        nonlocal modal_controls, ids_match
        while True:
            targets = await controls()
            modal_controls = len(targets)
            identities = [
                target.get("data_id") or target.get("file_id") for target in targets if isinstance(target, dict)
            ]
            ids_match = (
                len(identities) == len(targets)
                and len(identities) == len(names)
                and set(identities) == names.keys()
                and len(set(identities)) == len(identities)
            )
            if ids_match:
                return targets
            await asyncio.sleep(0.05)

    if urlsplit(page.main_frame.url).path != "/std/archive":
        route_match = False
        diagnose()
        raise ValueError("attachment request not in the selected archive")
    route_match = True
    # An icon click can show the previous modal before its new XHR is dispatched.
    if not activity.matches(_ATTACH_LIST, "GET", after=after):
        await asyncio.sleep(0.1)
    requests = activity.matches(_ATTACH_LIST, "GET", after=after)
    if len(requests) > 1 or any(not activity._archive_referer(request) for _, request in requests):
        diagnose()
        raise ValueError("duplicate attachment list response")
    if requests:
        try:
            response = await activity.wait_response(_ATTACH_LIST, "GET", after=after)
            if response.request is not requests[0][1]:
                raise ValueError("attachment list response changed")
            with profile_span("attachment-list", domain="materials"):
                body = await _step(response.json(), "reading attachment list metadata")
        except CampusError as error:
            if error.code != "browser-timeout":
                raise
            diagnose()
            return {}, [], False
        except ValueError:
            diagnose()
            return {}, [], False
        if isinstance(body, dict) and isinstance(body.get("header"), dict) and body["header"].get("code") != 200:
            diagnose()
            return {}, [], False
        try:
            names = _attachment_names(body)
        except ValueError:
            targets = await controls()
            modal_controls = len(targets) if isinstance(targets, list) else None
            diagnose()
            return {}, targets, False
        else:
            response_items = len(names)
            with profile_span("page-readiness", wait_kind="readiness", domain="materials", page_kind="archive"):
                try:
                    return (
                        names,
                        await bounded(matching(names), _WAIT_MS / 1000, "matching archive file controls to response"),
                        True,
                    )
                except CampusError as error:
                    if error.code != "browser-timeout":
                        raise
                    diagnose()
        # The response did not identify the rendered controls; reload before trusting them.
        return names, [], False

    async def available() -> list[dict[str, Any]]:
        nonlocal modal_controls
        while True:
            targets = await controls()
            modal_controls = len(targets) if isinstance(targets, list) else None
            if isinstance(targets, list) and targets:
                return targets
            await asyncio.sleep(0.05)

    with profile_span("page-readiness", wait_kind="readiness", domain="materials", page_kind="archive"):
        try:
            return {}, await bounded(available(), _WAIT_MS / 1000, "waiting for archive file controls"), False
        except CampusError as error:
            if error.code != "browser-timeout":
                raise
            diagnose()
            return {}, [], False


def _request_navigation(request: Any) -> bool:
    try:
        navigation = request.is_navigation_request()
        return navigation or request.resource_type == "document"
    except (PlaywrightError, ValueError, AttributeError, TypeError):
        return True


def _request_archive_list(request: Any) -> bool:
    try:
        method = request.method
        path = urlsplit(request.url).path
    except (PlaywrightError, ValueError, AttributeError, TypeError):
        return True
    return str(method).upper() == "POST" and path == _ARCHIVE_LIST


def _retention_blocked(activity: _RequestWindow, after: int) -> bool:
    """Main-frame navigation or archive-list refresh disqualifies retention, even while pending."""
    try:
        observed = activity.requests[after:]
    except (PlaywrightError, ValueError, AttributeError, TypeError):
        return True
    for request in observed:
        try:
            main_frame = request.frame is activity.page.main_frame
        except (PlaywrightError, ValueError, AttributeError, TypeError):
            return True
        if not main_frame:
            continue
        if _request_navigation(request) or _request_archive_list(request):
            return True
    return False


async def _release_document(document: Any) -> None:
    dispose = None if document is None else getattr(document, "dispose", None)
    if not callable(dispose):
        return
    try:
        await dispose()
    except Exception:
        return


async def _capture_archive_document(
    page: Any,
    expected_page: int,
    expected_total: int,
    expected_course_id: str,
    expected_state: Mapping[str, Any],
) -> tuple[Any, bool]:
    """Retain this post's document. Capture or observation failure is a cache miss."""
    try:
        document = await _step(page.evaluate_handle("() => document"), "retaining archive document")
    except PlaywrightError:
        return None, False
    except ValueError:
        return None, False
    except CampusError as error:
        if error.code != "browser-timeout":
            raise
        return None, False
    try:
        try:
            observed = await _archive_state(
                page,
                expected_page,
                expected_total,
                expected_course_id,
                expected_document=document,
            )
        except (PlaywrightError, ValueError):
            return document, False
        except CampusError as error:
            if error.code != "browser-timeout":
                raise
            return document, False
        ready = (
            observed.get("same_document") is True
            and observed.get("archive_path") is True
            and observed.get("posts") == expected_state.get("posts")
            and observed.get("page_size") == expected_state.get("page_size")
            and observed.get("row_count") == expected_state.get("row_count")
        )
        return document, ready
    except BaseException:
        await _release_document(document)
        raise


async def _archive_unchanged(
    page: Any,
    *,
    document: Any,
    expected_page: int,
    expected_total: int,
    expected_course_id: str,
    expected_state: Mapping[str, Any],
    activity: _RequestWindow,
    after: int,
) -> bool:
    """Return true only when the closed modal left the validated archive list intact."""
    if document is None:
        return False
    state = await _archive_state(
        page,
        expected_page,
        expected_total,
        expected_course_id,
        expected_document=document,
    )
    return (
        state.get("same_document") is True
        and state.get("archive_path") is True
        and state.get("modal_clear") is True
        and state.get("posts") == expected_state.get("posts")
        and state.get("page_size") == expected_state.get("page_size")
        and state.get("row_count") == expected_state.get("row_count")
        and not _retention_blocked(activity, after)
    )


async def _restore_archive_document(page: Any, *, expected_course_id: str) -> None:
    """Restore the archive menu and validate its list response for the selected course."""
    await _archive_navigation(page, lambda: page.click(_ARCHIVE_MENU), expected_course_id=expected_course_id)


async def enumerate_archive(page: Any, course: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Enumerate every completed archive page and restore its exact post context."""
    with profile_span("dom-ready", wait_kind="selector", domain="materials", page_kind="archive"):
        await _step(page.wait_for_selector("#table_list", timeout=_WAIT_MS), "waiting for archive table")
    with profile_span("archive-page", domain="materials"):
        first = await _wait_archive_state(page, 1, expected_course_id=course["course_id"])
    total = first["total_count"]
    pages = (total + first["page_size"] - 1) // first["page_size"]
    if pages > 100:
        raise _failure("course-sync-failed", course)
    results: list[dict[str, Any]] = []
    seen_posts: set[str] = set()
    seen_files: set[str] = set()
    counted = 0
    for page_number in range(1, max(1, pages) + 1):
        with profile_span("archive-page", domain="materials"):
            if page_number > 1:
                await _select_page(page, page_number)
                state = await _wait_archive_state(page, page_number, total, expected_course_id=course["course_id"])
            else:
                state = first
        posts = state["posts"]
        counted += state["row_count"]
        for post in posts:
            if not isinstance(post, dict):
                raise _failure("course-sync-failed", course)
            post_id, title = post.get("board_item_id"), post.get("title")
            if not isinstance(post_id, str) or not post_id.strip() or post_id in seen_posts:
                raise _failure("course-sync-failed", course)
            if not isinstance(title, str) or not title.strip():
                raise _failure("course-sync-failed", course)
            seen_posts.add(post_id)
            metadata = {"board_item_id": post_id, "title": title.strip()}
            for attempt in range(2):
                activity = _RequestWindow(page)
                activity.start()
                document = None
                retry = False
                try:
                    baseline = len(activity.requests)
                    document, evidence_ready = await _capture_archive_document(
                        page, page_number, total, course["course_id"], state
                    )
                    with (
                        profile_span("modal", domain="materials", page_kind="archive"),
                        profile_span("wait", wait_kind="action", domain="materials", page_kind="archive"),
                    ):
                        await _step(page.evaluate(_CLICK_ICON_JS, post_id), "opening archive file icon")
                    try:
                        names, targets, verified = await _post_names(page, activity, baseline, post_id)
                        if not verified and attempt == 0:
                            retry = True
                        elif not verified and (names or not targets):
                            raise _failure("course-sync-failed", course)
                        else:
                            for target in targets:
                                if not isinstance(target, dict):
                                    raise _failure("course-sync-failed", course)
                                row = _target_row(target, course, metadata, names)
                                if row["file_id"] in seen_files:
                                    raise _failure("item-identity-missing", course)
                                seen_files.add(row["file_id"])
                                results.append(row)
                        requests = activity.matches(_ATTACH_LIST, "GET", after=baseline)
                        if len(requests) > 1 or any(not activity._archive_referer(request) for _, request in requests):
                            raise ValueError("attachment list changed during extraction")
                    finally:
                        with (
                            profile_span("modal", domain="materials", page_kind="archive"),
                            profile_span("wait", wait_kind="action", domain="materials", page_kind="archive"),
                        ):
                            await _step(page.evaluate(_CLOSE_MODAL_JS), "closing archive modal")
                        with profile_span("archive-restore", domain="materials", page_kind="archive"):
                            retained = False
                            if not retry:
                                try:
                                    retained = await _archive_unchanged(
                                        page,
                                        document=document if evidence_ready else None,
                                        expected_page=page_number,
                                        expected_total=total,
                                        expected_course_id=course["course_id"],
                                        expected_state=state,
                                        activity=activity,
                                        after=baseline,
                                    )
                                except (PlaywrightError, ValueError):
                                    retained = False
                                except CampusError as error:
                                    if error.code != "browser-timeout":
                                        raise
                            if not retained:
                                await _restore_archive_document(page, expected_course_id=course["course_id"])
                                if page_number > 1:
                                    await _select_page(page, page_number)
                                restored = await _wait_archive_state(
                                    page, page_number, total, expected_course_id=course["course_id"]
                                )
                                if restored["posts"] != posts or restored.get("modal_clear") is not True:
                                    raise _failure("course-sync-failed", course)
                finally:
                    activity.close()
                    await _release_document(document)
                if not retry:
                    break
    if counted != total:
        raise _failure("course-sync-failed", course)
    return results


async def sync_materials(
    config: dict[str, Any],
    root: Path,
    course_id: str | None = None,
    *,
    headless: bool = False,
) -> tuple[dict[str, Any], list[CampusError]]:
    """Collect materials through the shared course traversal."""
    from .sync_all import sync_one

    return await sync_one(config, root, "materials", course_id, headless=headless)
