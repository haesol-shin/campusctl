"""Unit tests for source package builder, resource references, and publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campusctl.envelope import CampusError
from campusctl.providers.cnu.attachment_transfer import FetchedAttachment, OfficialAttachmentTarget
from campusctl.providers.cnu.request_policy import RequestPolicy
from campusctl.providers.cnu.ui_policy import UiRequestPolicy
from campusctl.source_package import (
    DetailSnapshot,
    ResourceReference,
    _relative_name,
    build_source_package,
    resource_id,
    source_ref,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lms_sources" / "package_schema.json"


class FakeResponse:
    def __init__(self, url: str, status: int, headers: dict[str, str], body: bytes):
        self.url = url
        self.status = status
        self.headers = headers
        self._body = body

    async def body(self) -> bytes:
        return self._body

    async def dispose(self) -> None:
        pass


class FakeRequestContext:
    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[str, FakeResponse] = {}

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"url": url, **kwargs})
        if url in self.responses:
            return self.responses[url]
        return FakeResponse(url, 404, {}, b"not found")


class FakePage:
    def __init__(self):
        self.context = SimpleNamespace(request=FakeRequestContext())


def make_policy(
    *, approved: bool = True, origins: tuple[str, ...] = ("https://dcs-learning.cnu.ac.kr",)
) -> RequestPolicy:
    raw = {
        "approved": approved,
        "read_only_evidence": "owner-held sanitized policy evidence (2026-09-25)",
        "origins": list(origins),
        "routes": [
            {
                "origin": "https://dcs-learning.cnu.ac.kr",
                "path": "/upload/diagram.png",
                "operation": "assignments.fetch",
                "methods": ["GET"],
            },
            {
                "origin": "https://dcs-learning.cnu.ac.kr",
                "path": "/api/v1/archive/fileDownload",
                "operation": "assignments.fetch",
                "methods": ["POST"],
            },
        ],
        "allowed_media": [],
        "max_bytes": 200_000_000,
        "static_asset_origins": ["https://dcs-learning.cnu.ac.kr"],
        "static_resource_types": ["image", "script", "stylesheet", "font"],
        "suppress": [],
        "selected_file_routes": [],
    }
    ui = UiRequestPolicy.from_reviewed_config(raw)
    return RequestPolicy(ui, "assignment")


def test_resource_ids_and_query_free_source_refs() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    entity_id = fixture["manifest"]["entity_id"]
    course_id = fixture["manifest"]["course"]["id"]

    ref_attachment = ResourceReference(
        kind="attachment",
        source_url="https://lms.example.invalid/std/task?token=secret#frag",
        original_name="synthetic-attachment.txt",
        media_type_hint="text/plain",
        label="attachment label",
        provider_file_id="file-synthetic-package-001",
        official_target=None,
    )
    with pytest.raises(AttributeError):
        ref_attachment.kind = "other"  # type: ignore

    snapshot = DetailSnapshot(
        source_url="https://lms.example.invalid/std/task?view=detail#section",
        provider_native_id="task-synthetic-package-001",
        parts=("\n", ref_attachment),
    )
    with pytest.raises(AttributeError):
        snapshot.parts = ()  # type: ignore

    clean_source = source_ref(ref_attachment.source_url, ref_attachment.provider_file_id)
    assert clean_source == {
        "origin": "https://lms.example.invalid",
        "page_path": "/std/task",
        "provider_native_id": "file-synthetic-package-001",
    }
    assert "token" not in str(clean_source)
    assert "frag" not in str(clean_source)

    file_rid = resource_id(entity_id, course_id, ref_attachment, order=1)
    assert file_rid == fixture["expected"]["included_file_resource_id"]

    ref_inline = ResourceReference(
        kind="image",
        source_url="https://external.example.invalid/synthetic/image.png?sig=leak#here",
        original_name="synthetic-image.png",
        media_type_hint="image/png",
        label="synthetic image",
        provider_file_id=None,
        official_target=None,
    )
    inline_rid = resource_id(entity_id, course_id, ref_inline, order=2)
    assert inline_rid == fixture["expected"]["omitted_inline_resource_id"]

    ref_inline_repeat = ResourceReference(
        kind="image",
        source_url="https://external.example.invalid/synthetic/image.png?sig=leak#here",
        original_name="synthetic-image.png",
        media_type_hint="image/png",
        label="synthetic image",
        provider_file_id=None,
        official_target=None,
    )
    inline_rid_order_3 = resource_id(entity_id, course_id, ref_inline_repeat, order=3)
    assert inline_rid_order_3 != inline_rid


def test_atomic_manifest_digest_collisions_and_reuse(tmp_path: Path) -> None:
    used: set[str] = set()
    name1 = _relative_name("file.txt", "1111", used)
    name2 = _relative_name("FILE.TXT", "2222", used)
    assert name1 == "file.txt"
    assert name2.startswith("FILE~2222")

    long_stem = "a" * 250 + ".pdf"
    truncated = _relative_name(long_stem, "3333", set())
    assert len(truncated.encode("utf-8")) <= 200
    assert truncated.endswith(".pdf")

    page = FakePage()
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    image_url = "https://dcs-learning.cnu.ac.kr/upload/diagram.png"
    page.context.request.responses[image_url] = FakeResponse(
        image_url, 200, {"content-type": "image/png", "content-length": str(len(png_bytes))}, png_bytes
    )

    policy = make_policy()
    ref = ResourceReference(
        kind="image",
        source_url=image_url,
        original_name="diagram.png",
        media_type_hint="image/png",
        label="diagram",
        provider_file_id=None,
        official_target=None,
    )
    snapshot = DetailSnapshot(
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        provider_native_id="task-101",
        parts=("Brief title\n", ref),
    )

    root = tmp_path / "data"
    result1 = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id="cnu_assignment:course-1:task-101",
            kind="assignment",
            course_id="course-1",
            course_label="Course 1",
            root=root,
            policy=policy,
        )
    )
    assert Path(result1["path"]).is_dir()
    manifest1 = json.loads(Path(result1["manifest_path"]).read_text(encoding="utf-8"))
    retrieved_at_1 = manifest1["retrieved_at"]

    result2 = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id="cnu_assignment:course-1:task-101",
            kind="assignment",
            course_id="course-1",
            course_label="Course 1",
            root=root,
            policy=policy,
        )
    )
    assert result2["path"] == result1["path"]
    manifest2 = json.loads(Path(result2["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest2["retrieved_at"] == retrieved_at_1

    explicit_out = tmp_path / "custom_out"
    result_out = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id="cnu_assignment:course-1:task-101",
            kind="assignment",
            course_id="course-1",
            course_label="Course 1",
            root=root,
            policy=policy,
            out=explicit_out,
        )
    )
    assert result_out["path"] == str(explicit_out)
    with pytest.raises(CampusError) as exc_info:
        asyncio.run(
            build_source_package(
                page,
                snapshot,
                entity_id="cnu_assignment:course-1:task-101",
                kind="assignment",
                course_id="course-1",
                course_label="Course 1",
                root=root,
                policy=policy,
                out=explicit_out,
            )
        )
    assert exc_info.value.code == "output-path-conflict"


def test_guarded_resources_and_policy_partial(tmp_path: Path) -> None:
    page = FakePage()
    policy = make_policy()

    ref_external = ResourceReference(
        kind="image",
        source_url="https://external.example.invalid/image.png",
        original_name="external.png",
        media_type_hint="image/png",
        label="external image",
        provider_file_id=None,
        official_target=None,
    )
    ref_video = ResourceReference(
        kind="video",
        source_url="https://dcs-learning.cnu.ac.kr/lecture.mp4",
        original_name="lecture.mp4",
        media_type_hint="video/mp4",
        label="video lecture",
        provider_file_id=None,
        official_target=None,
    )
    snapshot = DetailSnapshot(
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        provider_native_id="task-102",
        parts=("Description\n", ref_external, "\n", ref_video),
    )

    root = tmp_path / "data"
    result = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id="cnu_assignment:course-1:task-102",
            kind="assignment",
            course_id="course-1",
            course_label="Course 1",
            root=root,
            policy=policy,
        )
    )

    assert result["completeness"] == "policy-filtered"
    assert len(result["omitted_resources"]) == 2
    assert result["omitted_resources"][0]["reason"] == "external-origin"
    assert result["omitted_resources"][1]["reason"] == "unsupported-media-type"
    assert len(page.context.request.requests) == 0

    content_text = Path(result["content_path"]).read_text(encoding="utf-8")
    assert "[Omitted: external-origin — external image]" in content_text
    assert "[Omitted: unsupported-media-type — video lecture]" in content_text

    corrupt_url = "https://dcs-learning.cnu.ac.kr/upload/diagram.png"
    page.context.request.responses[corrupt_url] = FakeResponse(
        corrupt_url, 200, {"content-type": "image/png"}, b"NOT_A_PNG"
    )
    ref_corrupt = ResourceReference(
        kind="image",
        source_url=corrupt_url,
        original_name="diagram.png",
        media_type_hint="image/png",
        label="corrupt image",
        provider_file_id=None,
        official_target=None,
    )
    snapshot_corrupt = DetailSnapshot(
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        provider_native_id="task-103",
        parts=(ref_corrupt,),
    )
    out_dir = tmp_path / "out_corrupt"
    with pytest.raises(CampusError) as exc_info:
        asyncio.run(
            build_source_package(
                page,
                snapshot_corrupt,
                entity_id="cnu_assignment:course-1:task-103",
                kind="assignment",
                course_id="course-1",
                course_label="Course 1",
                root=root,
                policy=policy,
                out=out_dir,
            )
        )
    assert exc_info.value.code == "unsupported-media-type"
    assert not out_dir.exists()


def test_selected_attachment_and_image_original_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    image_url = "https://dcs-learning.cnu.ac.kr/upload/diagram.png"
    page.context.request.responses[image_url] = FakeResponse(image_url, 200, {"content-type": "image/png"}, png_bytes)

    policy = make_policy()
    root = tmp_path / "data"

    wrong_target = OfficialAttachmentTarget(
        file_id="file-1",
        parent_kind="assignment",
        parent_id="wrong-task-id",
        control_locator="a[data-id='file-1']",
        candidate_url=None,
    )
    ref_wrong = ResourceReference(
        kind="attachment",
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        original_name="document.pdf",
        media_type_hint="application/pdf",
        label="attachment doc",
        provider_file_id="file-1",
        official_target=wrong_target,
    )
    snapshot_wrong = DetailSnapshot(
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        provider_native_id="task-104",
        parts=(ref_wrong,),
    )
    with pytest.raises(CampusError) as exc_info:
        asyncio.run(
            build_source_package(
                page,
                snapshot_wrong,
                entity_id="cnu_assignment:course-1:task-104",
                kind="assignment",
                course_id="course-1",
                course_label="Course 1",
                root=root,
                policy=policy,
            )
        )
    assert exc_info.value.code == "policy-blocked"

    pdf_bytes = b"%PDF-1.4\nvalid attachment content"
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    async def mock_fetch_attachment(*_args: Any, **kwargs: Any) -> FetchedAttachment:
        temp_dir = _args[3]
        temp_file = temp_dir / ".temp_fetched_file"
        temp_file.write_bytes(pdf_bytes)
        return FetchedAttachment(temp_file, pdf_sha256, "application/pdf", len(pdf_bytes))

    import campusctl.source_package as sp_module

    monkeypatch.setattr(sp_module, "fetch_official_attachment", mock_fetch_attachment)
    monkeypatch.setattr(sp_module, "_approved_attachment_route", lambda policy, kind: "/api/v1/archive/fileDownload")

    correct_target = OfficialAttachmentTarget(
        file_id="file-1",
        parent_kind="assignment",
        parent_id="task-104",
        control_locator="a[data-id='file-1']",
        candidate_url=None,
    )
    ref_correct = ResourceReference(
        kind="attachment",
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        original_name="document.pdf",
        media_type_hint="application/pdf",
        label="attachment doc",
        provider_file_id="file-1",
        official_target=correct_target,
    )
    ref_image = ResourceReference(
        kind="image",
        source_url=image_url,
        original_name="diagram.png",
        media_type_hint="image/png",
        label="diagram image",
        provider_file_id=None,
        official_target=None,
    )
    snapshot_ok = DetailSnapshot(
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        provider_native_id="task-104",
        parts=("Heading\n", ref_image, "\nDownload: ", ref_correct),
    )

    result_ok = asyncio.run(
        build_source_package(
            page,
            snapshot_ok,
            entity_id="cnu_assignment:course-1:task-104",
            kind="assignment",
            course_id="course-1",
            course_label="Course 1",
            root=root,
            policy=policy,
        )
    )
    assert result_ok["completeness"] == "complete"
    assert len(result_ok["resources"]) == 2
    pkg_dir = Path(result_ok["path"])
    content_md = (pkg_dir / "content.md").read_text(encoding="utf-8")
    assert "![diagram image](images/diagram.png)" in content_md
    assert "[attachment doc](attachments/document.pdf)" in content_md
    assert (pkg_dir / "attachments" / "document.pdf").read_bytes() == pdf_bytes
    assert (pkg_dir / "images" / "diagram.png").read_bytes() == png_bytes


def test_policy_exclusions_never_request_bytes(tmp_path: Path) -> None:
    page = FakePage()
    policy = make_policy()
    root = tmp_path / "data"

    ref_video = ResourceReference(
        kind="video",
        source_url="https://dcs-learning.cnu.ac.kr/video.mp4",
        original_name="video.mp4",
        media_type_hint="video/mp4",
        label="excluded video",
        provider_file_id=None,
        official_target=None,
    )
    snapshot = DetailSnapshot(
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        provider_native_id="task-105",
        parts=(ref_video,),
    )
    result = asyncio.run(
        build_source_package(
            page,
            snapshot,
            entity_id="cnu_assignment:course-1:task-105",
            kind="assignment",
            course_id="course-1",
            course_label="Course 1",
            root=root,
            policy=policy,
        )
    )
    assert result["completeness"] == "policy-filtered"
    assert len(page.context.request.requests) == 0


def test_allowed_get_vs_range_header() -> None:
    raw = {
        "approved": True,
        "read_only_evidence": "evidence",
        "origins": ["https://dcs-learning.cnu.ac.kr"],
        "routes": [
            {
                "origin": "https://dcs-learning.cnu.ac.kr",
                "path": "/std/task",
                "operation": "assignments.fetch",
                "methods": ["GET"],
            },
        ],
        "allowed_media": [],
        "max_bytes": 200_000_000,
        "static_asset_origins": ["https://dcs-learning.cnu.ac.kr"],
        "static_resource_types": ["script"],
        "suppress": [],
        "selected_file_routes": [],
    }
    from campusctl.providers.cnu.ui_policy import UiRequestDenied, guard_ui_request

    ui = UiRequestPolicy.from_reviewed_config(raw)
    headers_ok: dict[str, str] = {"Accept": "*/*"}
    guard_ui_request(
        ui,
        "https://dcs-learning.cnu.ac.kr/std/task",
        "GET",
        headers_ok,
        operation="assignments.fetch",
        resource_type="document",
    )

    headers_range: dict[str, str] = {"range": "bytes=0-100"}
    with pytest.raises(UiRequestDenied) as exc_info:
        guard_ui_request(
            ui,
            "https://dcs-learning.cnu.ac.kr/std/task",
            "GET",
            headers_range,
            operation="assignments.fetch",
            resource_type="document",
        )
    assert exc_info.value.reason_code == "range"

    headers_range_cap: dict[str, str] = {"Range": "bytes=0-100"}
    with pytest.raises(UiRequestDenied) as exc_info2:
        guard_ui_request(
            ui,
            "https://dcs-learning.cnu.ac.kr/std/task",
            "GET",
            headers_range_cap,
            operation="assignments.fetch",
            resource_type="document",
        )
    assert exc_info2.value.reason_code == "range"


def test_failed_transfer_never_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    policy = make_policy()
    root = tmp_path / "data"

    import campusctl.source_package as sp_module

    async def mock_failing_fetch(*_args: Any, **kwargs: Any) -> FetchedAttachment:
        raise CampusError("download-failed", "Simulated network failure.")

    monkeypatch.setattr(sp_module, "fetch_official_attachment", mock_failing_fetch)
    monkeypatch.setattr(sp_module, "_approved_attachment_route", lambda policy, kind: "/api/v1/archive/fileDownload")

    target = OfficialAttachmentTarget(
        file_id="file-1",
        parent_kind="assignment",
        parent_id="task-106",
        control_locator="a[data-id='file-1']",
        candidate_url=None,
    )
    ref = ResourceReference(
        kind="attachment",
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        original_name="document.pdf",
        media_type_hint="application/pdf",
        label="doc",
        provider_file_id="file-1",
        official_target=target,
    )
    snapshot = DetailSnapshot(
        source_url="https://dcs-learning.cnu.ac.kr/std/taskView",
        provider_native_id="task-106",
        parts=(ref,),
    )
    destination = tmp_path / "published_never"
    with pytest.raises(CampusError):
        asyncio.run(
            build_source_package(
                page,
                snapshot,
                entity_id="cnu_assignment:course-1:task-106",
                kind="assignment",
                course_id="course-1",
                course_label="Course 1",
                root=root,
                policy=policy,
                out=destination,
            )
        )
    assert not destination.exists()
    sources_dir = root / "sources"
    if sources_dir.exists():
        assert not any(sources_dir.rglob("package.json"))


def test_latched_guard_denial_blocks_publication(tmp_path: Path) -> None:
    class DeniedGuard:
        def raise_if_denied(self) -> None:
            raise CampusError("policy-blocked", "An LMS request was blocked by the reviewed UI policy.")

    snapshot = DetailSnapshot(
        source_url="https://lms.invalid/std/taskView",
        provider_native_id="task-101",
        parts=("Brief title\n",),
    )
    root = tmp_path / "data"
    with pytest.raises(CampusError) as exc_info:
        asyncio.run(
            build_source_package(
                FakePage(),
                snapshot,
                entity_id="cnu_assignment:course-1:task-101",
                kind="assignment",
                course_id="course-1",
                course_label="Course 1",
                root=root,
                policy=make_policy(),
                interceptor=DeniedGuard(),
            )
        )
    assert exc_info.value.code == "policy-blocked"
    assert not any(path.name == "package.json" for path in root.rglob("*"))
