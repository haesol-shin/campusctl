from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu.request_policy import (
    MAX_ATTACHMENT_BYTES,
    RequestPolicy,
    ResponseValidator,
    guard_response,
)
from campusctl.providers.cnu.ui_policy import UiRequestPolicy

FIXTURE = json.loads((Path(__file__).parent / "fixtures/lms_sources/materials_responses.json").read_text())


def approved_ui() -> UiRequestPolicy:
    return UiRequestPolicy.from_reviewed_config(
        {
            "approved": True,
            "read_only_evidence": "synthetic-test-evidence",
            "origins": ["https://dcs-learning.cnu.ac.kr", "https://dcs-lcms.cnu.ac.kr"],
            "routes": [
                {
                    "origin": "https://dcs-learning.cnu.ac.kr",
                    "path": "/api/v1/archive/fileDownload",
                    "operation": "materials.download",
                    "methods": ["POST"],
                }
            ],
            "allowed_media": [],
            "max_bytes": MAX_ATTACHMENT_BYTES,
            "selected_file_routes": [
                {
                    "origin": "https://dcs-lcms.cnu.ac.kr",
                    "path_template": "/upload/{storage-id}/{encoded-filename}",
                    "operation": "materials.download",
                    "methods": ["GET"],
                },
                {
                    "origin": "https://dcs-learning.cnu.ac.kr",
                    "path_template": "/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}",
                    "operation": "materials.download",
                    "methods": ["GET"],
                },
            ],
        }
    )


def fixture_bytes(item: dict) -> bytes:
    if "hex" in item:
        return bytes.fromhex(item["hex"])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for entry in item["zip_entries"]:
            archive.writestr(entry, b"fixture")
    return output.getvalue()


def check_response(item: dict, ui: UiRequestPolicy, saved: Path) -> None:
    policy = RequestPolicy(ui, item["filename"])
    if item.get("name") == "pptx-duplicate-entry":
        with pytest.warns(UserWarning, match="Duplicate name"):
            body = fixture_bytes(item)
    else:
        body = fixture_bytes(item)
    saved.write_bytes(body)
    guard_response(
        policy,
        "synthetic-selected-url",
        item["mime"],
        None,
        operation="materials.download",
        selected_file_id="fixture-file-1",
    )
    validator = ResponseValidator(policy)
    validator.declared(str(len(body)))
    validator.feed(body[:3])
    validator.feed(body[3:])
    validator.finish(saved, str(len(body)))


def test_selected_response_mime_signature_and_limit(tmp_path: Path) -> None:
    ui = approved_ui()
    assert ui.approved
    for item in FIXTURE["responses"]:
        if item["valid"]:
            check_response(item, ui, tmp_path / "payload")
        else:
            with pytest.raises(CampusError, match="attachment"):
                check_response(item, ui, tmp_path / "payload")
    for mime in ("video/mp4", "audio/mpeg"):
        with pytest.raises(CampusError):
            guard_response(
                RequestPolicy(ui, "example.py"),
                "fixture",
                mime,
                None,
                operation="materials.download",
                selected_file_id="fixture-file-1",
            )
    validator = ResponseValidator(RequestPolicy(ui, "example.txt"))
    validator.declared(str(MAX_ATTACHMENT_BYTES))
    with pytest.raises(CampusError) as oversized:
        validator.declared(str(MAX_ATTACHMENT_BYTES + 1))
    assert oversized.value.code == "file-too-large"
    validator.size = MAX_ATTACHMENT_BYTES - 1
    validator.feed(b"x")
    assert validator.size == MAX_ATTACHMENT_BYTES
    with pytest.raises(CampusError) as streamed:
        validator.feed(b"x")
    assert streamed.value.code == "file-too-large"


def test_response_fixture_cases() -> None:
    assert {entry["name"] for entry in FIXTURE["responses"]} >= {
        "observed-pdf",
        "observed-pptx",
        "observed-zip",
        "broken-ole",
        "unobserved-docx",
        "invalid-utf8",
    }
    assert FIXTURE["get_requests"] == [
        {"headers": {}, "valid": True},
        {"headers": {"rAnGe": "bytes=0-"}, "valid": False},
    ]
