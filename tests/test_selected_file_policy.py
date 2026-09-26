"""Public fixture hygiene and selected official-file URL binding."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from campusctl.providers.cnu import selected_file_policy
from campusctl.providers.cnu.selected_file_policy import (
    SelectedFileDenied,
    bind_selected_file_request,
    match_selected_file_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lms_sources"
_URL_REGEX = re.compile(r'(?i)\bhttps?://[^\s"\'<>]+')
_FORBIDDEN_KEY_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "session_id",
    "sessionid",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "private_key",
)
_SECRET_VALUE_REGEX = re.compile(
    r"(?i)(\bbearer\s+[a-z0-9._~+/=-]{8,}|\bey[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}"
    r"|-----begin [a-z ]*private key-----|\b(jsessionid|sessionid|password|passwd|api_key|apikey)\s*[=:])"
)
_FORBIDDEN_PARAM_TOKENS = ("signature", "token", "key", "sig", "expires", "secret", "auth", "session_id", "sessionid")
_FORBIDDEN_VIDEO_EXTS = (".mp4", ".m3u8", ".mpd", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv")
_FILE_BEARING_KEYS = frozenset(
    {"url", "href", "path", "filename", "original_name", "candidate_url", "saved_path", "content_path", "name"}
)
_FORBIDDEN_MEDIA_PREFIXES = ("video/", "audio/")


def assert_fixture_data_hygiene(data: Any, origin_label: str = "fixture") -> None:
    def scan(node: Any, path: str = "") -> None:
        if isinstance(node, Mapping):
            media = str(node.get("media_type") or node.get("mime") or "").strip().lower()
            file_path = str(node.get("path") or node.get("original_name") or "").strip().lower()
            if file_path.endswith(".ts") and media.startswith(_FORBIDDEN_MEDIA_PREFIXES):
                raise AssertionError(
                    f"Transport stream '.ts' with video/audio MIME '{media}' in {origin_label} at {path}"
                )
            for key, value in node.items():
                key_lower = str(key).lower()
                for token in _FORBIDDEN_KEY_TOKENS:
                    assert token not in key_lower, f"Forbidden key token '{token}' in {origin_label} at {path}.{key}"
                if key_lower in _FILE_BEARING_KEYS and isinstance(value, str):
                    value_path = urlsplit(value).path.lower() if _URL_REGEX.match(value) else value.strip().lower()
                    for ext in _FORBIDDEN_VIDEO_EXTS:
                        assert not value_path.endswith(ext), (
                            f"Forbidden video extension '{ext}' in {origin_label} at {path}.{key}: '{value}'"
                        )
                if key_lower in ("media_type", "mime") and isinstance(value, str):
                    assert not value.strip().lower().startswith(_FORBIDDEN_MEDIA_PREFIXES), (
                        f"Forbidden media type/mime '{value}' in {origin_label} at {path}.{key}"
                    )
                scan(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                scan(item, f"{path}[{index}]")
        elif isinstance(node, str):
            for match in _URL_REGEX.finditer(node):
                url = match.group(0)
                parsed = urlsplit(url)
                for ext in _FORBIDDEN_VIDEO_EXTS:
                    assert not parsed.path.lower().endswith(ext), (
                        f"Forbidden video extension '{ext}' in {origin_label} at {path}: '{url}'"
                    )
                host = parsed.hostname
                assert host and (host == "invalid" or host.endswith(".invalid")), (
                    f"Real LMS host '{host}' instead of reserved .invalid in {origin_label} at {path}: '{url}'"
                )
                for component, label in ((parsed.query, "query"), (parsed.fragment, "fragment")):
                    for param, _ in parse_qsl(component, keep_blank_values=True):
                        for token in _FORBIDDEN_PARAM_TOKENS:
                            assert token not in param.lower(), (
                                f"Forbidden {label} parameter '{param}' in URL in {origin_label} at {path}"
                            )
            assert not _SECRET_VALUE_REGEX.search(node), f"Credential-like value in {origin_label} at {path}"

    scan(data)


def test_fixture_hygiene_scans_lms_sources() -> None:
    fixture_files = list(FIXTURES.glob("*.json"))
    assert fixture_files, "No fixture files found under tests/fixtures/lms_sources"
    for fixture_file in fixture_files:
        assert_fixture_data_hygiene(json.loads(fixture_file.read_text(encoding="utf-8")), str(fixture_file))


def test_fixture_hygiene_scanner_rejects_malformed_inputs() -> None:
    with pytest.raises(AssertionError, match="Real LMS host 'lms.cnu.ac.kr'"):
        assert_fixture_data_hygiene({"doc": "See [link](https://lms.cnu.ac.kr/std/task) here"})
    for key in ("api_key", "ApiKey", "client_secret", "user_password", "Credential"):
        with pytest.raises(AssertionError, match="Forbidden key token"):
            assert_fixture_data_hygiene({key: "synthetic"})
    for value in (
        "Authorization: Bearer abcdefghijklmnop",
        "JSESSIONID=ABCDEF0123456789",
        "-----BEGIN RSA PRIVATE KEY-----",
    ):
        with pytest.raises(AssertionError, match="Credential-like value"):
            assert_fixture_data_hygiene({"note": value})
    with pytest.raises(AssertionError, match="Forbidden query parameter"):
        assert_fixture_data_hygiene({"url": "https://lms.example.invalid/task?signature=secret"})
    with pytest.raises(AssertionError, match="Forbidden fragment parameter"):
        assert_fixture_data_hygiene({"url": "https://lms.example.invalid/task#access_token=secret"})
    with pytest.raises(AssertionError, match="Forbidden media type/mime"):
        assert_fixture_data_hygiene({"mime": "video/mp4"})
    with pytest.raises(AssertionError, match="Forbidden video extension"):
        assert_fixture_data_hygiene({"filename": "movie.mp4"})
    with pytest.raises(AssertionError, match="Transport stream"):
        assert_fixture_data_hygiene({"path": "clip.ts", "media_type": "video/mp2t"})
    assert_fixture_data_hygiene({"path": "lecture-notes.ts", "media_type": "text/plain"})


@pytest.fixture
def synthetic_file_origins(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    upload_origin = "https://files.example.invalid"
    response_origin = "https://lms.example.invalid"
    monkeypatch.setattr(
        selected_file_policy,
        "FILE_TEMPLATES",
        {
            upload_origin: "/upload/{storage-id}/{encoded-filename}",
            response_origin: "/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}",
        },
    )
    return upload_origin, response_origin


def test_selected_file_binding_accepts_only_official_paths(synthetic_file_origins: tuple[str, str]) -> None:
    upload_origin, _ = synthetic_file_origins
    url = upload_origin + "/upload/synthetic/notes%20one.pdf"
    binding = bind_selected_file_request(selected_file_id="file-001", resolved_url=url, source="official-control")
    assert (binding.selected_file_id, binding.origin, binding.path, binding.source) == (
        "file-001",
        upload_origin,
        "/upload/synthetic/notes%20one.pdf",
        "official-control",
    )
    assert match_selected_file_path("/upload/{storage-id}/{encoded-filename}", binding.path) == {
        "storage-id": "synthetic",
        "encoded-filename": "notes one.pdf",
    }
    for path in (
        "/upload/synthetic/notes%2fone.pdf",
        "/upload/synthetic/%2e%2e",
        "/upload/synthetic/%252e%252e",
        "/upload/synthetic/notes.pdf/extra",
        "/upload/synthetic/notes%5cone.pdf",
        "/upload/synthetic/notes%zz.pdf",
    ):
        assert match_selected_file_path("/upload/{storage-id}/{encoded-filename}", path) is None
        with pytest.raises(SelectedFileDenied):
            bind_selected_file_request(
                selected_file_id="file-001",
                resolved_url=upload_origin + path,
                source="official-control",
            )
    for bad_url in (
        url + "?_=12",
        url + "#fragment",
        "https://outside.example.invalid/upload/synthetic/notes.pdf",
    ):
        with pytest.raises(SelectedFileDenied):
            bind_selected_file_request(selected_file_id="file-001", resolved_url=bad_url, source="official-control")
    for source in ("page", "", None):
        with pytest.raises(SelectedFileDenied):
            bind_selected_file_request(selected_file_id="file-001", resolved_url=url, source=source)


def test_selected_file_binding_requires_identity_and_official_response_provenance(
    synthetic_file_origins: tuple[str, str],
) -> None:
    _, response_origin = synthetic_file_origins
    url = response_origin + "/file/term/course/board/manager/item/notes.pdf"
    bound = bind_selected_file_request(selected_file_id="file-002", resolved_url=url, source="fileDownload-response")
    assert bound.selected_file_id == "file-002"
    assert bound.path == "/file/term/course/board/manager/item/notes.pdf"
    with pytest.raises(SelectedFileDenied):
        bind_selected_file_request(selected_file_id="", resolved_url=url, source="fileDownload-response")
    with pytest.raises(SelectedFileDenied):
        bind_selected_file_request(
            selected_file_id="file-002",
            resolved_url=url.replace("/item/", "/item/extra/"),
            source="fileDownload-response",
        )
