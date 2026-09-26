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

FIXTURE = json.loads((Path(__file__).parent / "fixtures/lms_sources/materials_responses.json").read_text())


def fixture_bytes(item: dict) -> bytes:
    if "hex" in item:
        return bytes.fromhex(item["hex"])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for entry in item["zip_entries"]:
            archive.writestr(entry, b"fixture")
    return output.getvalue()


def check_response(item: dict, saved: Path) -> None:
    policy = RequestPolicy(item["filename"])
    if item.get("name") == "pptx-duplicate-entry":
        with pytest.warns(UserWarning, match="Duplicate name"):
            body = fixture_bytes(item)
    else:
        body = fixture_bytes(item)
    saved.write_bytes(body)
    guard_response(policy, item["mime"], None)
    validator = ResponseValidator(policy)
    validator.declared(str(len(body)))
    validator.feed(body[:3])
    validator.feed(body[3:])
    validator.finish(saved, str(len(body)))


def test_selected_response_mime_signature_and_limit(tmp_path: Path) -> None:
    for item in FIXTURE["responses"]:
        if item["valid"]:
            check_response(item, tmp_path / "payload")
        else:
            with pytest.raises(CampusError, match="attachment"):
                check_response(item, tmp_path / "payload")
    for mime in ("video/mp4", "audio/mpeg"):
        with pytest.raises(CampusError):
            guard_response(RequestPolicy("example.py"), mime, None)
    validator = ResponseValidator(RequestPolicy("example.txt"))
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
