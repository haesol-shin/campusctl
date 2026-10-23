"""Selected-detail source packages, built privately before atomic publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from campusctl.envelope import CampusError
from campusctl.material_files import safe_component
from campusctl.source_resources import DetailSnapshot, ResourceReference, SourceResolution, resolve_source_resource


@dataclass(frozen=True, slots=True)
class _ResourceState:
    reference: ResourceReference
    identity: dict[str, str | None]
    contents: bytes | None
    path: str | None
    reason: str | None


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def resource_id(entity_id: str, course_id: str, reference: ResourceReference, order: int) -> str:
    if reference.kind == "attachment" and reference.provider_file_id:
        identity = ["file", course_id, reference.provider_file_id]
    else:
        identity = ["inline", entity_id, reference.kind, order]
    return hashlib.sha256(canonical_json(identity)).hexdigest()


def source_ref(url: str, provider_native_id: str | None = None) -> dict[str, str | None]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port:
        raise CampusError("fetch-failed", "Source reference could not be verified.", status="error")
    return {
        "origin": f"https://{parsed.hostname.lower()}",
        "page_path": parsed.path or "/",
        "provider_native_id": provider_native_id,
    }


def _failed() -> CampusError:
    return CampusError("fetch-failed", "Selected source could not be packaged safely.", status="error")


def _name(reference: ResourceReference) -> str:
    return safe_component(reference.original_name or "resource")


def _relative_name(name: str, resource_id_value: str, used: set[str]) -> str:
    first = safe_component(name)
    if unicodedata.normalize("NFC", first).casefold() not in used:
        used.add(unicodedata.normalize("NFC", first).casefold())
        return first
    candidate = safe_component(name, suffix="~" + resource_id_value)
    key = unicodedata.normalize("NFC", candidate).casefold()
    if key in used:
        raise CampusError("output-path-conflict", "Resource names conflict in the selected package.")
    used.add(key)
    return candidate


def _record(digest: object, name: str, contents: bytes) -> None:
    name_bytes = name.encode("utf-8")
    digest.update(len(name_bytes).to_bytes(8, "big"))
    digest.update(name_bytes)
    digest.update(len(contents).to_bytes(8, "big"))
    digest.update(contents)


def _digest(manifest: dict, content: bytes, files: dict[str, bytes]) -> str:
    canonical = dict(manifest)
    canonical.pop("retrieved_at", None)
    sha = hashlib.sha256()
    _record(sha, "package.json", canonical_json(canonical))
    _record(sha, "content.md", content)
    for name in sorted(files):
        _record(sha, name, files[name])
    return sha.hexdigest()


def _contained(root: Path, target: Path) -> bool:
    if target.is_symlink() or root.is_symlink():
        return False
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return not any(part.is_symlink() for part in (root, *target.parents) if part != Path("."))


def _verified_existing(path: Path, expected_digest: str) -> dict | None:
    if not path.is_dir() or path.is_symlink():
        return None
    try:
        manifest_path = path / "package.json"
        content_path = path / "content.md"
        if not _contained(path, manifest_path) or not _contained(path, content_path):
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        content = content_path.read_bytes()
        files = {}
        for record in manifest["resources"]:
            name = record["path"]
            member = path / name
            if not _contained(path, member) or member.resolve().is_relative_to(path.resolve()) is False:
                return None
            contents = member.read_bytes()
            if hashlib.sha256(contents).hexdigest() != record["sha256"] or len(contents) != record["size_bytes"]:
                return None
            files[name] = contents
        return manifest if _digest(manifest, content, files) == expected_digest else None
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return None


def _result(path: Path, manifest: dict, provenance: str) -> dict:
    resources = [{**record, "path": str(path / record["path"])} for record in manifest["resources"]]
    return {
        "entity_id": manifest["entity_id"],
        "completeness": manifest["completeness"],
        "path": str(path),
        "manifest_path": str(path / "package.json"),
        "content_path": str(path / "content.md"),
        "resources": resources,
        "omitted_resources": manifest["omitted_resources"],
        "provenance": {
            "provider": provenance,
            "course_id": manifest["course"]["id"],
            "source_ref": manifest["source_ref"],
            "retrieved_at": manifest["retrieved_at"],
        },
    }


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


async def build_source_package(
    page: object,
    snapshot: DetailSnapshot,
    *,
    entity_id: str,
    kind: str,
    course_id: str,
    course_label: str,
    root: Path,
    resolution: SourceResolution,
    out: Path | None = None,
) -> dict:
    """Package only already-bound selected detail; the caller holds the session lock."""
    if kind not in {"assignment", "notice"} or not entity_id or not course_id:
        raise _failed()
    root = Path(root).absolute()
    parent = (
        Path(out).absolute().parent
        if out is not None
        else root / "sources" / kind / hashlib.sha256(entity_id.encode()).hexdigest()
    )
    if out is not None and (Path(out).exists() or Path(out).is_symlink()):
        raise CampusError("output-path-conflict", "Selected output path already exists.")
    if parent.is_symlink() or any(p.is_symlink() for p in parent.parents):
        raise CampusError("output-path-conflict", "Selected output path is unsafe.")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".source-", dir=parent))
    try:
        references: dict[str, _ResourceState] = {}
        used: dict[str, set[str]] = {"images": set(), "attachments": set()}
        files: dict[str, bytes] = {}
        records: list[dict] = []
        omitted: list[dict] = []
        fragments: list[str] = []
        order = 0
        for part in snapshot.parts:
            if isinstance(part, str):
                fragments.append(part)
                continue
            order += 1
            rid = resource_id(entity_id, course_id, part, order)
            identity_url = snapshot.source_url if kind == "notice" and part.kind == "attachment" else part.source_url
            identity = source_ref(identity_url, part.provider_file_id)
            prior = references.get(rid)
            if prior is not None:
                if prior.reference != part or prior.identity != identity:
                    raise _failed()
                fragments.append(_link(part, prior.path, prior.reason))
                continue
            name = _name(part)
            # Staging already exists; resolve at this traversal point, not as an eager batch.
            contents, media_type, reason = await resolve_source_resource(
                page,
                part,
                kind=kind,
                provider_native_id=snapshot.provider_native_id,
                name=name,
                staging=staging,
                transfer=resolution.transfer,
            )
            path = None
            if reason is not None:
                omitted.append(
                    {
                        "resource_id": rid,
                        "source_ref": identity,
                        "original_name": part.original_name,
                        "media_type": part.media_type_hint,
                        "reason": reason,
                    }
                )
            else:
                assert contents is not None
                folder = "images" if part.kind == "image" else "attachments"
                path = folder + "/" + _relative_name(name, rid, used[folder])
                files[path] = contents
                record = {
                    "resource_id": rid,
                    "kind": part.kind,
                    "path": path,
                    "source_ref": identity,
                    "original_name": part.original_name,
                    "media_type": media_type,
                    "size_bytes": len(contents),
                    "sha256": hashlib.sha256(contents).hexdigest(),
                }
                if part.provider_file_id is not None:
                    record["provider_file_id"] = part.provider_file_id
                records.append(record)
            references[rid] = _ResourceState(part, identity, contents, path, reason)
            fragments.append(_link(part, path, reason))
        content = "".join(fragments).encode("utf-8")
        manifest = {
            "schema_version": 1,
            "entity_id": entity_id,
            "kind": kind,
            "course": {"id": course_id, "label": course_label},
            "source_ref": source_ref(snapshot.source_url, snapshot.provider_native_id),
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "content_path": "content.md",
            "completeness": "partial" if omitted else "complete",
            "resources": records,
            "omitted_resources": omitted,
        }
        digest = _digest(manifest, content, files)
        destination = Path(out).absolute() if out is not None else parent / digest
        if destination.exists() or destination.is_symlink():
            previous = _verified_existing(destination, digest) if out is None else None
            if previous is None or {key: val for key, val in previous.items() if key != "retrieved_at"} != {
                key: val for key, val in manifest.items() if key != "retrieved_at"
            }:
                raise CampusError("output-path-conflict", "Selected output path already exists.")
            return _result(destination, previous, resolution.provenance)
        (staging / "content.md").write_bytes(content)
        for name, contents in files.items():
            member = staging / name
            member.parent.mkdir(exist_ok=True)
            member.write_bytes(contents)
        (staging / "package.json").write_bytes(canonical_json(manifest) + b"\n")
        os.replace(staging, destination)
        return _result(destination, manifest, resolution.provenance)
    except (OSError, ValueError) as exc:
        raise _failed() from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _link(reference: ResourceReference, path: str | None, reason: str | None) -> str:
    if reason is not None:
        return f"[Omitted: {_escape(reason)} — {_escape(reference.label)}]"
    assert path is not None
    prefix = "!" if reference.kind == "image" else ""
    return f"{prefix}[{_escape(reference.label)}]({path})"
