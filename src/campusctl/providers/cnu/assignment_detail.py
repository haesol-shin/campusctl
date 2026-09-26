"""Read one selected assignment detail from an already guarded CNU browser page."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import PROTOCOL_TIMEOUT_SECONDS, bounded
from campusctl.envelope import CampusError
from campusctl.identity import assignment_entity_id
from campusctl.source_package import DetailSnapshot, ResourceReference

from .assignments import EXTRACT_COURSE_CONTEXT_JS
from .attachment_transfer import OfficialAttachmentTarget
from .course_context import SECTION_RESPONSE_TIMEOUT_MS, _css_string, open_course_section, prepare_course_section

_ORIGIN = "https://dcs-learning.cnu.ac.kr"
_TASK_ID = re.compile(r"TB_L_REPORT[0-9]+\Z")
_DETAIL_PATHS = ("/api/v1/task/detail", "/api/v1/task/stdDetail")
_DETAIL_TITLE = ".card-body h4"

# Only readable brief nodes are traversed; the submission panel and modal are never selected.
EXTRACT_ASSIGNMENT_DETAIL_JS = r"""() => {
    const title = document.querySelector('.card-body h4');
    if (!title) return null;
    const body = title.closest('.card-body');
    if (!body) return null;
    const parts = [];
    const emit = (value) => { if (value) parts.push(value); };
    const visit = (node) => {
        if (node.nodeType === Node.TEXT_NODE) {
            emit({kind: 'text', value: node.textContent.replace(/\s+/g, ' ')});
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const tag = node.tagName.toLowerCase();
        if (['script', 'style', 'template', 'button', 'input', 'select', 'textarea', 'form'].includes(tag)
            || node.hidden || node.getAttribute('aria-hidden') === 'true') return;
        if (tag === 'video' || tag === 'audio') {
            const src = node.getAttribute('src') || node.querySelector('source')?.getAttribute('src') || '';
            const label = (node.getAttribute('title') || node.getAttribute('aria-label') || tag).trim();
            const name = src ? src.split(/[?#]/)[0].split('/').pop() || null : null;
            const fullUrl = src ? new URL(src, document.URL).href : document.URL;
            emit({kind: tag, url: fullUrl, label: label || tag, name});
            return;
        }
        if (tag === 'img') {
            const src = node.getAttribute('src');
            if (src) emit({kind: 'image', url: new URL(src, document.URL).href,
                label: node.getAttribute('alt') || 'image', name: src.split(/[?#]/)[0].split('/').pop() || null});
            return;
        }
        if (tag === 'a') {
            const label = (node.innerText || node.textContent || '').trim();
            const fileId = node.getAttribute('data-act') === 'downloadFile' && node.getAttribute('data-id');
            if (fileId) {
                const href = node.getAttribute('href');
                emit({kind: 'attachment', file_id: fileId, label,
                    name: node.getAttribute('data-name') || label,
                    url: href && /^https?:\/\//i.test(href) ? href : null});
            } else {
                for (const child of node.childNodes) {
                    if (child.nodeType === Node.ELEMENT_NODE && child.tagName.toLowerCase() === 'img') visit(child);
                }
                if (label) emit({kind: 'ordinary-link', label});
            }
            return;
        }
        if (/^h[1-6]$/.test(tag)) emit({kind: 'prefix', value: '\n' + '#'.repeat(Number(tag[1])) + ' '});
        else if (tag === 'li') emit({kind: 'prefix', value: '\n- '});
        else if (tag === 'br') emit({kind: 'prefix', value: '\n'});
        for (const child of node.childNodes) visit(child);
        if (/^(h[1-6]|p|div|ul|ol|li|blockquote)$/.test(tag)) emit({kind: 'prefix', value: '\n'});
    };
    for (const child of body.childNodes) visit(child);
    return {parts, page_task_id: body.closest('[data-id^="TB_L_REPORT"]')?.getAttribute('data-id') || null};
}"""


def _failed(message: str = "The selected assignment detail could not be verified.") -> CampusError:
    return CampusError("fetch-failed", message, status="error")


def _wrong_task() -> CampusError:
    return CampusError(
        "entity-unknown",
        "The opened assignment does not match the selected task.",
        "Sync assignments again and select the current full ID.",
        "user-action",
    )


def _response_identity(payload: Any) -> str:
    """Require a native task identity in each independent detail response."""
    pending = [payload]
    found: set[str] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            header = value.get("header")
            if isinstance(header, dict) and str(header.get("code")) not in {"200", "0"}:
                raise _failed()
            for key, item in value.items():
                normalized = key.casefold().replace("_", "") if isinstance(key, str) else ""
                if normalized in {"taskid", "reportid"} or (
                    normalized == "id" and isinstance(item, str) and _TASK_ID.fullmatch(item)
                ):
                    if not isinstance(item, str) or not _TASK_ID.fullmatch(item):
                        raise _wrong_task()
                    found.add(item)
                elif isinstance(item, (dict, list)):
                    pending.append(item)
        elif isinstance(value, list):
            pending.extend(item for item in value if isinstance(item, (dict, list)))
    if len(found) != 1:
        raise _wrong_task()
    return found.pop()


def _markdown_text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>!|])", r"\\\1", value)


def _detail_parts(raw: Any, *, task_id: str, source_url: str) -> tuple[str | ResourceReference, ...]:
    if not isinstance(raw, dict) or not isinstance(raw.get("parts"), list):
        raise _failed()
    observed = raw.get("page_task_id")
    if observed is not None and observed != task_id:
        raise _wrong_task()
    parts: list[str | ResourceReference] = []
    for entry in raw["parts"]:
        if not isinstance(entry, dict):
            raise _failed()
        kind = entry.get("kind")
        if kind in {"text", "prefix"}:
            value = entry.get("value")
            if not isinstance(value, str):
                raise _failed()
            parts.append(_markdown_text(value) if kind == "text" else value)
        elif kind == "ordinary-link":
            label = entry.get("label")
            if not isinstance(label, str):
                raise _failed()
            parts.append(_markdown_text(label) + " [link URL omitted]")
        elif kind == "image":
            url, label = entry.get("url"), entry.get("label")
            if not isinstance(url, str) or not isinstance(label, str):
                raise _failed()
            parts.append(ResourceReference("image", url, entry.get("name"), None, label, None, None))
        elif kind == "attachment":
            file_id, label, name, candidate = (entry.get(field) for field in ("file_id", "label", "name", "url"))
            if not isinstance(file_id, str) or not file_id or not isinstance(label, str) or not isinstance(name, str):
                raise _failed()
            if candidate is not None and not isinstance(candidate, str):
                raise _failed()
            locator = f'a[data-act="downloadFile"][data-id={_css_string(file_id)}]'
            target = OfficialAttachmentTarget(file_id, "assignment", task_id, locator, candidate)
            parts.append(ResourceReference("attachment", candidate or source_url, name, None, label, file_id, target))
        elif kind in {"video", "audio"}:
            url, label = entry.get("url"), entry.get("label")
            if not isinstance(url, str) or not isinstance(label, str):
                raise _failed()
            parts.append(ResourceReference(kind, url, entry.get("name"), f"{kind}/*", label, None, None))
        else:
            raise _failed()
    if not parts or not any(isinstance(part, str) and part.strip() for part in parts):
        raise _failed()
    return tuple(parts)


async def capture_assignment_detail(page: Any, config: dict[str, Any], selected_row: dict[str, Any]) -> DetailSnapshot:
    """Open the selected catalog task via ordinary UI and bind both detail responses before any byte transfer.

    The caller authenticates once, installs the assignments.fetch interceptor, and keeps
    the session/guard active across this adapter and the package builder.
    """
    task_id = selected_row.get("task_id")
    course = selected_row.get("course")
    course_id = course.get("id") if isinstance(course, dict) else None
    if (
        not isinstance(task_id, str)
        or not _TASK_ID.fullmatch(task_id)
        or not isinstance(course_id, str)
        or not course_id
        or selected_row.get("entity_id") != assignment_entity_id(course_id, task_id)
    ):
        raise _wrong_task()
    await bounded(page.wait_for_load_state("networkidle"), PROTOCOL_TIMEOUT_SECONDS, "settling CNU course requests")
    await prepare_course_section(page, config, course_id, "task")
    await open_course_section(page, "task")
    await bounded(
        page.wait_for_selector('a[data-act="detail"][data-id]', state="attached"),
        PROTOCOL_TIMEOUT_SECONDS,
        "waiting for CNU task rows",
    )
    active_course = await bounded(
        page.evaluate(EXTRACT_COURSE_CONTEXT_JS), PROTOCOL_TIMEOUT_SECONDS, "checking the selected CNU task course"
    )
    if active_course != course_id:
        raise _wrong_task()
    selector = f'a[data-act="detail"][data-id={_css_string(task_id)}]'
    selected = page.locator(selector)
    if await bounded(selected.count(), PROTOCOL_TIMEOUT_SECONDS, "binding the selected task row") != 1:
        raise _wrong_task()
    if (
        await bounded(selected.get_attribute("data-id"), PROTOCOL_TIMEOUT_SECONDS, "checking the selected task ID")
        != task_id
    ):
        raise _wrong_task()

    def matches(path: str):
        def match(response: Any) -> bool:
            url = urlsplit(response.url)
            return (
                f"{url.scheme}://{url.netloc}" == _ORIGIN
                and url.path == path
                and response.request.method.upper() == "POST"
            )

        return match

    async def block_automatic_images(route: Any) -> None:
        # The package builder classifies and transfers images only after detail identity is bound.
        if route.request.resource_type == "image":
            await route.abort()
        else:
            await route.fallback()

    await bounded(
        page.route("**/*", block_automatic_images), PROTOCOL_TIMEOUT_SECONDS, "preventing unbound image requests"
    )
    try:
        async with (
            page.expect_response(matches(_DETAIL_PATHS[0]), timeout=SECTION_RESPONSE_TIMEOUT_MS) as detail_info,
            page.expect_response(matches(_DETAIL_PATHS[1]), timeout=SECTION_RESPONSE_TIMEOUT_MS) as std_info,
        ):
            await bounded(selected.click(), PROTOCOL_TIMEOUT_SECONDS, "opening the selected CNU task")
        for info in (detail_info, std_info):
            response = await bounded(info.value, PROTOCOL_TIMEOUT_SECONDS, "waiting for CNU task detail")
            if (
                response.status != 200
                or await bounded(response.finished(), PROTOCOL_TIMEOUT_SECONDS, "finishing CNU task detail") is not None
            ):
                raise _failed()
            identity = _response_identity(
                await bounded(response.json(), PROTOCOL_TIMEOUT_SECONDS, "verifying CNU task detail identity")
            )
            if identity != task_id:
                raise _wrong_task()
        source_url = page.url
        source = urlsplit(source_url)
        if f"{source.scheme}://{source.netloc}" != _ORIGIN or source.path != "/std/taskView":
            raise _failed()
        await bounded(
            page.wait_for_selector(_DETAIL_TITLE, state="visible"),
            PROTOCOL_TIMEOUT_SECONDS,
            "waiting for selected CNU task content",
        )
        raw = await bounded(
            page.evaluate(EXTRACT_ASSIGNMENT_DETAIL_JS), PROTOCOL_TIMEOUT_SECONDS, "reading selected CNU task brief"
        )
        return DetailSnapshot(source_url, task_id, _detail_parts(raw, task_id=task_id, source_url=source_url))
    finally:
        await page.unroute("**/*", block_automatic_images)
