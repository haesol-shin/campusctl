"""Bind a selected LMS download to one exact official file response URL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urlsplit

from campusctl.envelope import CampusError

FILE_TEMPLATES = {
    "https://dcs-lcms.cnu.ac.kr": "/upload/{storage-id}/{encoded-filename}",
    "https://dcs-learning.cnu.ac.kr": "/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}",
}


class SelectedFileDenied(CampusError):
    """The selected file did not resolve to one expected official URL."""

    def __init__(self) -> None:
        super().__init__(
            "policy-blocked", "Selected attachment URL does not match its official file control.", status="error"
        )


@dataclass(frozen=True, slots=True)
class SelectedFileRequest:
    selected_file_id: str
    origin: str
    path: str
    source: Literal["official-control", "fileDownload-response"]


def match_selected_file_path(path_template: str, path: str) -> dict[str, str] | None:
    """Match an official file template without decoding a slash or normalizing a path."""
    if path_template not in FILE_TEMPLATES.values() or not isinstance(path, str):
        return None
    template_parts, parts = path_template.split("/"), path.split("/")
    if len(parts) != len(template_parts):
        return None
    captures: dict[str, str] = {}
    for expected, segment in zip(template_parts, parts, strict=True):
        if expected.startswith("{") and expected.endswith("}"):
            if not segment or re.search(r"%(?![0-9a-fA-F]{2})", segment):
                return None
            decoded = unquote(segment)
            if (
                not decoded
                or decoded in {".", ".."}
                or "/" in decoded
                or "\\" in decoded
                or re.search(r"%[0-9a-fA-F]{2}", decoded)
                or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            ):
                return None
            captures[expected[1:-1]] = decoded
        elif expected != segment:
            return None
    return captures


def bind_selected_file_request(
    *,
    selected_file_id: str,
    resolved_url: str,
    source: Literal["official-control", "fileDownload-response"],
) -> SelectedFileRequest:
    """Bind one response URL to the clicked file ID, never to a whole URL template."""
    if not isinstance(resolved_url, str):
        raise SelectedFileDenied()
    try:
        parsed = urlsplit(resolved_url)
    except ValueError as exc:
        raise SelectedFileDenied() from exc
    origin = f"{parsed.scheme}://{parsed.netloc}"
    template = FILE_TEMPLATES.get(origin)
    if (
        not isinstance(selected_file_id, str)
        or not selected_file_id
        or source not in {"official-control", "fileDownload-response"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or template is None
        or match_selected_file_path(template, parsed.path) is None
    ):
        raise SelectedFileDenied()
    return SelectedFileRequest(selected_file_id, origin, parsed.path, source)
