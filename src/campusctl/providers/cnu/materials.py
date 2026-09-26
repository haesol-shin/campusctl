"""Guarded, metadata-only enumeration of official course archive attachments."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded, profile_span
from campusctl.envelope import CampusError
from campusctl.identity import material_entity_id

from .course_context import SECTION_RESPONSE_TIMEOUT_MS, CourseSelection, bind_on_commit

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

_ARCHIVE_STATE_JS = """/* archiveMetadataState */() => {
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
        }))
    };
}"""
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


async def _step(awaitable: Any, guard: Any, description: str) -> Any:
    try:
        return await bounded(awaitable, PROTOCOL_TIMEOUT_SECONDS, description)
    finally:
        guard.raise_if_denied()


class _RequestWindow:
    """Correlate responses to requests initiated after this course's document."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self.requests: list[Any] = []
        self.responses: dict[int, Any] = {}
        self.pending: set[int] = set()
        self.commits: list[int] = []
        self.settled = asyncio.Event()
        self.settled.set()

    def start(self) -> None:
        self.page.on("framenavigated", self._navigated)
        self.page.on("request", self._started)
        self.page.on("response", self._received)
        self.page.on("requestfinished", self._finished)
        self.page.on("requestfailed", self._finished)

    def close(self) -> None:
        self.page.remove_listener("framenavigated", self._navigated)
        self.page.remove_listener("request", self._started)
        self.page.remove_listener("response", self._received)
        self.page.remove_listener("requestfinished", self._finished)
        self.page.remove_listener("requestfailed", self._finished)

    def _navigated(self, frame: Any) -> None:
        if frame is self.page.main_frame and urlsplit(frame.url).path == "/std/archive":
            self.commits.append(len(self.requests))

    def _started(self, request: Any) -> None:
        self.requests.append(request)
        self.pending.add(id(request))
        self.settled.clear()

    def _received(self, response: Any) -> None:
        self.responses[id(response.request)] = response

    def _finished(self, request: Any) -> None:
        self.pending.discard(id(request))
        if not self.pending:
            self.settled.set()

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

    async def idle(self, guard: Any) -> None:
        await _step(
            self.page.wait_for_load_state("networkidle", timeout=SECTION_RESPONSE_TIMEOUT_MS),
            guard,
            "waiting for archive requests",
        )
        try:
            await bounded(
                self.settled.wait(), SECTION_RESPONSE_TIMEOUT_MS / 1000, "waiting for archive request completion"
            )
        finally:
            guard.raise_if_denied()
        if self.pending:
            raise ValueError("archive requests remain pending")


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


async def _archive_navigation(page: Any, guard: Any, action: Callable[[], Any], *, document: bool = True) -> None:
    """Accept one list request originating after this archive navigation only."""
    activity = _RequestWindow(page)
    activity.start()
    try:
        await activity.idle(guard)
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

        async with page.expect_response(
            selected, timeout=SECTION_RESPONSE_TIMEOUT_MS if document else _WAIT_MS
        ) as pending:
            with profile_span("document-commit" if document else "archive-page", domain="materials"):
                await _step(action(), guard, "opening archive list")
        response = await _step(pending.value, guard, "waiting for archive list response")
        with profile_span("response-completion", domain="materials"):
            await _step(response.finished(), guard, "finishing archive list response")
        await activity.idle(guard)
        records = activity.matches(_ARCHIVE_LIST, "POST", after=before)
        if (
            len(records) != 1
            or (document and (len(activity.commits) != 1 or records[0][0] <= activity.commits[0]))
            or not activity._archive_referer(records[0][1])
            or response.request is not records[0][1]
            or response.status != 200
        ):
            raise ValueError("archive list was not bound to this navigation")
        payload = await _step(response.json(), guard, "parsing archive list response")
        if not isinstance(payload, (dict, list)):
            raise ValueError("archive list response was not JSON records")
    finally:
        activity.close()


async def arm_materials_capture(page: Any, section_guard: Any) -> _RequestWindow:
    """Observe the first archive list request before the section document commits."""
    capture = _RequestWindow(page)
    capture.start()
    try:
        await capture.idle(section_guard)
    except BaseException:
        capture.close()
        raise
    return capture


async def open_materials_section(
    page: Any,
    section_guard: Any,
    action: Callable[[], Any],
    *,
    capture: _RequestWindow | None = None,
    selection: CourseSelection | None = None,
) -> None:
    """Open the archive with its observer armed and bind before list XHRs."""

    async def navigate() -> None:
        if capture is None:
            await _archive_navigation(page, section_guard, action)
        else:
            await _step(action(), section_guard, "opening archive section")

    try:
        if selection is None:
            await navigate()
        else:
            async with bind_on_commit(
                page, section_guard, frame=page.main_frame, expected_path="/std/archive", selection=selection
            ):
                await navigate()
    except BaseException:
        if capture is not None:
            capture.close()
        raise


async def collect_materials_rows(
    page: Any,
    course: Mapping[str, Any],
    selection: CourseSelection | None,
    section_guard: Any,
    *,
    capture: _RequestWindow | None = None,
) -> list[dict[str, Any]]:
    """Collect the complete archive from its validated, committed section document."""
    try:
        section_guard.raise_if_denied()
        if selection is not None and capture is None:
            raise _failure("course-sync-failed", course)
        epoch = getattr(section_guard, "epoch", None)
        if selection is not None and (
            epoch is None
            or epoch.phase != "bound"
            or epoch.course_id != selection.course_id
            or epoch.selection_epoch != selection.epoch
            or epoch.frame is not page.main_frame
            or epoch.document_url != page.main_frame.url
        ):
            raise _failure("course-sync-failed", course)
        if (selection is not None and selection.course_id != course.get("course_id")) or urlsplit(
            page.main_frame.url
        ).path != "/std/archive":
            raise _failure("course-sync-failed", course)
        if capture is not None:
            await capture.idle(section_guard)
            records = capture.matches(_ARCHIVE_LIST, "POST")
            if (
                len(capture.commits) != 1
                or len(records) != 1
                or records[0][0] <= capture.commits[0]
                or not capture._archive_referer(records[0][1])
            ):
                raise _failure("course-sync-failed", course)
            response = capture.responses.get(id(records[0][1]))
            if response is None or response.status != 200:
                raise _failure("course-sync-failed", course)
            with profile_span("response-completion", domain="materials"):
                await _step(response.finished(), section_guard, "finishing archive list response")
            payload = await _step(response.json(), section_guard, "parsing archive list response")
            if not isinstance(payload, (dict, list)):
                raise _failure("course-sync-failed", course)
        with profile_span("extract", domain="materials", course=selection.ordinal if selection is not None else None):
            rows = await enumerate_archive(page, course, section_guard, selection=selection)
        section_guard.raise_if_denied()
        return rows
    finally:
        if capture is not None:
            capture.close()


async def _archive_state(
    page: Any,
    guard: Any,
    expected_page: int,
    expected_total: int | None = None,
    expected_course_id: str | None = None,
) -> dict:
    state = await _step(page.evaluate(_ARCHIVE_STATE_JS), guard, "observing archive table")
    if (
        not isinstance(state, dict)
        or state.get("completed") is not True
        or type(state.get("total_count")) is not int
        or type(state.get("page_size")) is not int
        or type(state.get("row_count")) is not int
        or not isinstance(state.get("posts"), list)
        or state["page_size"] < 1
        or state["total_count"] < 0
        or state["current_page"] != expected_page
        or (expected_total is not None and state["total_count"] != expected_total)
        or (expected_course_id is not None and state.get("selected_course_id") != expected_course_id)
    ):
        raise ValueError("archive table did not complete")
    expected_rows = min(state["page_size"], max(0, state["total_count"] - (expected_page - 1) * state["page_size"]))
    if state["row_count"] != expected_rows:
        raise ValueError("archive page rows incomplete")
    return state


async def _select_page(page: Any, guard: Any, number: int) -> None:
    await _archive_navigation(page, guard, lambda: page.evaluate(_PAGE_JS, number), document=False)


async def _post_names(page: Any, guard: Any, activity: _RequestWindow, after: int) -> dict[str, str]:
    await activity.idle(guard)
    if urlsplit(page.main_frame.url).path != "/std/archive":
        raise ValueError("attachment request not in the selected archive")
    requests = activity.matches(_ATTACH_LIST, "GET", after=after)
    if len(requests) > 1 or any(not activity._archive_referer(request) for _, request in requests):
        raise ValueError("duplicate attachment list response")
    if not requests:
        return {}
    response = activity.responses.get(id(requests[0][1]))
    if response is None or response.status != 200:
        raise ValueError("attachment list did not complete")
    with profile_span("attachment-list", domain="materials"):
        body = await _step(response.json(), guard, "reading attachment list metadata")
    if isinstance(body, dict) and isinstance(body.get("header"), dict) and body["header"].get("code") != 200:
        raise ValueError("attachment list response failed")
    return _attachment_names(body)


async def _restore_archive_document(page: Any, guard: Any, selection: CourseSelection | None) -> None:
    """Rebind the same selected course around an archive menu restoration."""
    if selection is None:
        await _archive_navigation(page, guard, lambda: page.click(_ARCHIVE_MENU))
        return
    guard.raise_if_denied()
    epoch = guard.epoch
    frame = page.main_frame
    if (
        epoch.phase != "bound"
        or epoch.operation != "materials.sync"
        or epoch.course_id != selection.course_id
        or epoch.selection_epoch != selection.epoch
        or epoch.frame is not frame
        or epoch.document_url != frame.url
    ):
        raise ValueError("archive restoration is not bound to the selected course")
    guard.quarantine()
    guard.activate(
        epoch.policy,
        operation="materials.sync",
        selection=selection,
        frame=frame,
        document_url=frame.url,
        navigation_path="/std/archive",
        settled=True,
    )
    async with bind_on_commit(page, guard, frame=frame, expected_path="/std/archive", selection=selection):
        await _archive_navigation(page, guard, lambda: page.click(_ARCHIVE_MENU))


async def enumerate_archive(
    page: Any, course: Mapping[str, Any], guard: Any, *, selection: CourseSelection | None = None
) -> list[dict[str, Any]]:
    """Enumerate every completed archive page and restore its exact post context."""
    with profile_span("dom-ready", domain="materials"):
        await _step(page.wait_for_selector("#table_list", timeout=_WAIT_MS), guard, "waiting for archive table")
    with profile_span("archive-page", domain="materials"):
        first = await _archive_state(page, guard, 1, expected_course_id=selection.course_id if selection else None)
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
                await _select_page(page, guard, page_number)
            state = await _archive_state(
                page, guard, page_number, total, expected_course_id=selection.course_id if selection else None
            )
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
            activity = _RequestWindow(page)
            activity.start()
            try:
                await activity.idle(guard)
                baseline = len(activity.requests)
                with profile_span("modal", domain="materials"):
                    await _step(page.evaluate(_CLICK_ICON_JS, post_id), guard, "opening archive file icon")
                try:
                    with profile_span("attachment-list", domain="materials"):
                        names = await _post_names(page, guard, activity, baseline)
                    try:
                        with profile_span("modal", domain="materials"):
                            await _step(
                                page.wait_for_selector(_MODAL, timeout=_WAIT_MS), guard, "waiting for file modal"
                            )
                    except CampusError as error:
                        if error.code != "browser-timeout":
                            raise
                        targets = []
                    else:
                        with profile_span("modal", domain="materials"):
                            targets = await _step(
                                page.evaluate(_TARGETS_JS, {"modalOnly": True, "boardItemId": post_id}),
                                guard,
                                "reading modal file controls",
                            )
                    if not targets:
                        targets = await _step(
                            page.evaluate(_TARGETS_JS, {"modalOnly": False, "boardItemId": post_id}),
                            guard,
                            "reading inline file controls",
                        )
                    if not isinstance(targets, list) or not targets:
                        # Detail navigation uses unreviewed /std/archiveView;
                        # require separate route approval before adding that fallback.
                        raise CampusError(
                            "course-sync-failed",
                            "Archive attachment controls were unresolved after modal and inline inspection.",
                            "Check the course archive in the LMS and retry.",
                            "error",
                        )
                    for target in targets:
                        if not isinstance(target, dict):
                            raise _failure("course-sync-failed", course)
                        row = _target_row(target, course, metadata, names)
                        if row["file_id"] in seen_files:
                            raise _failure("item-identity-missing", course)
                        seen_files.add(row["file_id"])
                        results.append(row)
                finally:
                    with profile_span("modal", domain="materials"):
                        await _step(page.evaluate(_CLOSE_MODAL_JS), guard, "closing archive modal")
                    with profile_span("archive-restore", domain="materials"):
                        await _restore_archive_document(page, guard, selection)
                        if page_number > 1:
                            await _select_page(page, guard, page_number)
                        restored = await _archive_state(
                            page,
                            guard,
                            page_number,
                            total,
                            expected_course_id=selection.course_id if selection else None,
                        )
                        if restored["posts"] != posts:
                            raise _failure("course-sync-failed", course)
            finally:
                activity.close()
    if counted != total:
        raise _failure("course-sync-failed", course)
    return results


async def sync_materials(
    config: dict[str, Any],
    root: Path,
    course_id: str | None = None,
    *,
    headless: bool = False,
    reviewed_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], list[CampusError]]:
    """Collect materials through the shared guarded course traversal."""
    from .sync_all import sync_one

    return await sync_one(config, root, "materials", course_id, headless=headless, reviewed_policy=reviewed_policy)
