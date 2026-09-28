from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from campusctl.envelope import CampusError
from campusctl.material_files import (
    adopt_attachment,
    default_download_dir,
    prepare_output_dir,
    publish_attachment,
    resolve_download_layout,
    safe_component,
    verified_receipt,
    write_receipt,
)


def _temp(directory: Path, content: bytes) -> Path:
    fd, name = tempfile.mkstemp(dir=directory, prefix=".transfer-", suffix=".tmp")
    with os.fdopen(fd, "wb") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())
    return Path(name)


def _publish(directory: Path, filename: str, content: bytes):
    return publish_attachment(
        _temp(directory, content), directory, filename, hashlib.sha256(content).hexdigest(), len(content)
    )


def test_default_download_dir_linux_xdg_and_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(material_files, "_home_dir", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert default_download_dir() == tmp_path / "Downloads" / "campusctl"
    config = tmp_path / ".config" / "user-dirs.dirs"
    config.parent.mkdir()
    config.write_text('XDG_DOCUMENTS_DIR="$HOME/Documents"\nXDG_DOWNLOAD_DIR="$HOME/My Downloads"\n')
    assert default_download_dir() == tmp_path / "My Downloads" / "campusctl"
    config.write_text('XDG_DOWNLOAD_DIR="${HOME}/Downloads elsewhere"\n')
    assert default_download_dir() == tmp_path / "Downloads elsewhere" / "campusctl"
    custom = tmp_path / "custom-config"
    custom.mkdir()
    (custom / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Redirected"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(custom))
    assert default_download_dir() == tmp_path / "Redirected" / "campusctl"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-config")
    assert default_download_dir() == tmp_path / "Downloads elsewhere" / "campusctl"


def test_default_download_dir_macos_and_windows_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    from campusctl import material_files

    monkeypatch.setattr(material_files, "_home_dir", lambda: tmp_path)
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="darwin"))
    assert default_download_dir() == tmp_path / "Downloads" / "campusctl"
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    redirected = tmp_path / "redirected"
    buffer = ctypes.create_unicode_buffer(str(redirected))

    def known_folder(_guid: object, _flags: int, _token: object, output: object) -> int:
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(buffer)
        return 0

    def free(_value: object) -> None:
        pass

    monkeypatch.setattr(
        material_files.ctypes,
        "windll",
        SimpleNamespace(
            shell32=SimpleNamespace(SHGetKnownFolderPath=known_folder), ole32=SimpleNamespace(CoTaskMemFree=free)
        ),
        raising=False,
    )
    assert default_download_dir() == redirected / "campusctl"

    def missing_folder(_guid: object, _flags: int, _token: object, _output: object) -> int:
        return -1

    monkeypatch.setattr(material_files.ctypes.windll.shell32, "SHGetKnownFolderPath", missing_folder)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    assert default_download_dir() == tmp_path / "profile" / "Downloads" / "campusctl"


def test_windows_full_path_bound_collision_and_short_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    # Keep the synthetic directory at a fixed Windows path budget regardless
    # of the runner's temporary-root length.
    component_length = 150 - len(str(tmp_path)) - 1
    if component_length < 1:
        pytest.skip("runner temporary root already exceeds the synthetic Windows path budget")
    directory = prepare_output_dir(tmp_path, "windows-path", tmp_path / ("x" * component_length))
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    available = 250 - material_files._windows_length(str(directory)) - 1
    original = "B" * 180 + ".pdf"
    first = _publish(directory, original, b"first")
    assert first.path.name == "B" * (available - 4) + ".pdf"
    assert material_files._windows_length(str(first.path)) == 250
    assert _publish(directory, original, b"first").outcome == "reused"
    second = _publish(directory, original, b"second")
    assert second.path.name.endswith("~" + hashlib.sha256(b"second").hexdigest() + ".pdf")
    assert material_files._windows_length(str(second.path)) <= 250
    assert first.path.read_bytes() == b"first"
    assert second.path.read_bytes() == b"second"

    short = prepare_output_dir(tmp_path, "windows-path", tmp_path / "out")
    longer = _publish(short, original, b"third")
    assert len(longer.path.name) > len(first.path.name)


def test_windows_directory_reserves_private_publication_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    directory = tmp_path / ("x" * (220 - len(str(tmp_path)) - 1))
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    assert material_files._windows_length(str(directory)) == 220
    assert material_files._PUBLISH_TEMP_NAME_LENGTH == 46
    with pytest.raises(CampusError) as failure:
        prepare_output_dir(tmp_path, "windows-path", directory)
    assert failure.value.code == "output-path-too-long"
    assert not directory.exists()
    boundary = prepare_output_dir(tmp_path, "windows-path", tmp_path / ("y" * (203 - len(str(tmp_path)) - 1)))
    result = _publish(boundary, "report.pdf", b"safe")
    assert result.path.read_bytes() == b"safe"
    assert material_files._windows_length(str(result.path)) <= 250
    assert material_files._windows_length(str(boundary)) + 1 + material_files._PUBLISH_TEMP_NAME_LENGTH == 250


def test_windows_short_room_preserves_primary_until_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    directory = prepare_output_dir(tmp_path, "windows-path", tmp_path / ("x" * (200 - len(str(tmp_path)))))
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    first = _publish(directory, "report.pdf", b"first")
    assert first.path.read_bytes() == b"first"
    with pytest.raises(CampusError) as failure:
        _publish(directory, "report.pdf", b"second")
    assert failure.value.code == "output-path-too-long"
    assert first.path.read_bytes() == b"first"
    assert not list(directory.glob(".transfer-*"))
    assert not list(directory.glob(".material-*"))


def test_windows_unicode_full_path_uses_utf16_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    directory = prepare_output_dir(tmp_path, "windows-path", tmp_path / "unicode")
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    result = _publish(directory, "😀" * 130 + ".pdf", b"unicode")
    assert result.path.name.endswith(".pdf")
    assert material_files._windows_length(str(result.path)) <= 250


def test_windows_directory_too_long_requests_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    directory = tmp_path / ("x" * (249 - len(str(tmp_path))))
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    with pytest.raises(CampusError) as failure:
        prepare_output_dir(tmp_path, "windows-path", directory)
    assert failure.value.code == "output-path-too-long"
    assert failure.value.status == "user-action"
    assert "--out" in failure.value.remediation
    assert not directory.exists()
    short = prepare_output_dir(tmp_path, "windows-path", tmp_path / "out")
    assert _publish(short, "report.pdf", b"data").path.read_bytes() == b"data"


def test_safe_names_and_collision_boundaries() -> None:
    assert safe_component("../../CON.txt") == "resource"
    assert safe_component(r"..\LPT9.pptx") == "resource"
    assert safe_component("AUX") == "resource"
    assert safe_component("src/.hidden") == ".hidden"
    assert safe_component("\\folder\\report. .") == "report"
    assert safe_component("path/file<>:|?*\x00.txt") == "file_______.txt"
    assert safe_component("path/e\u0301.pdf") == "é.pdf"
    assert safe_component("a" * 220 + ".pdf") == "a" * 196 + ".pdf"
    for unsafe in (".", "..", "CON", "NUL.txt", "COM1", "LPT1.doc"):
        assert safe_component(unsafe) == "resource"
    assert safe_component("name ") == safe_component("name.") == "name"
    assert len(safe_component("a" + "." + "x" * 198).encode()) == 200
    long_extension = safe_component("a" + "." + "文" * 100, suffix="~" + "b" * 64)
    assert long_extension.startswith("a~" + "b" * 64)
    assert len(long_extension.encode()) <= 200
    digest = "a" * 64
    name = safe_component("x" * 250 + "." + "文" * 100, suffix="~" + digest)
    assert "~" + digest in name and name.startswith("x")
    assert len(name.encode("utf-8")) <= 200
    assert unicodedata.is_normalized("NFC", name)
    with pytest.raises(ValueError):
        safe_component("ok", suffix="/escape")


def test_publish_collision_and_symlink_safety(tmp_path: Path) -> None:
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:course:file", tmp_path / "out")
    assert directory.is_absolute() and directory.is_dir()
    content = b"%PDF- first file"
    first = _publish(directory, "e\u0301.pdf", content)
    assert first.outcome == "saved" and first.path.read_bytes() == content
    assert _publish(directory, "é.pdf", content).outcome == "reused"
    other = b"%PDF- secondfile"
    alternate = _publish(directory, "é.pdf", other)
    assert alternate.path.name == "é~" + hashlib.sha256(other).hexdigest() + ".pdf"
    assert alternate.outcome == "saved" and first.path.read_bytes() == content
    assert _publish(directory, "é.pdf", other).path == alternate.path
    conflicting = b"%PDF- thirdfile"
    (directory / ("conflict.pdf")).write_bytes(b"unrelated")
    (directory / ("conflict~" + hashlib.sha256(conflicting).hexdigest() + ".pdf")).write_bytes(b"unrelated")
    with pytest.raises(CampusError) as failure:
        _publish(directory, "conflict.pdf", conflicting)
    assert failure.value.code == "output-path-conflict"
    assert not list(directory.glob(".transfer-*"))
    (directory / "CASE.pdf").write_bytes(b"other")
    case = _publish(directory, "case.pdf", content)
    assert case.outcome == "saved"
    assert case.path.name == "case~" + hashlib.sha256(content).hexdigest() + ".pdf"
    assert (directory / "CASE.pdf").read_bytes() == b"other"
    symlink = directory / "linked.pdf"
    try:
        symlink.symlink_to(first.path)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    else:
        with pytest.raises(CampusError) as failure:
            _publish(directory, "linked.pdf", content)
        assert failure.value.code == "output-path-conflict"
    linked_dir = tmp_path / "linked-dir"
    try:
        linked_dir.symlink_to(directory, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    else:
        with pytest.raises(CampusError) as failure:
            prepare_output_dir(tmp_path, "other", linked_dir)
        assert failure.value.code == "output-path-conflict"
    assert first.path.read_bytes() == content


def test_fifo_at_output_name_is_rejected_without_opening(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX FIFO")
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:fifo")
    fifo = directory / "sample.pdf"
    os.mkfifo(fifo)
    with pytest.raises(CampusError) as failure:
        _publish(directory, "sample.pdf", b"safe")
    assert failure.value.code == "output-path-conflict"
    assert fifo.is_fifo()
    assert not list(directory.glob(".transfer-*.tmp"))
    assert not list(directory.glob(".material-*.tmp"))


def test_truncated_casefold_collision(tmp_path: Path) -> None:
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:course:long", tmp_path / "out")
    original = "A" * 196 + ".PDF"
    assert len(safe_component(original).encode()) == 200
    first = _publish(directory, original, b"first")
    second = _publish(directory, "a" * 196 + ".pdf", b"second")
    assert first.path.read_bytes() == b"first"
    digest = hashlib.sha256(b"second").hexdigest()
    assert second.path.name.endswith("~" + digest + ".pdf")
    assert second.path.name.casefold() != first.path.name.casefold()
    assert _publish(directory, "a" * 196 + ".pdf", b"second").path == second.path
    if os.name == "nt":
        from campusctl import material_files

        assert material_files._windows_length(str(second.path)) <= 250
    else:
        assert second.path.name == safe_component("a" * 196 + ".pdf", suffix="~" + digest)


def test_rejected_external_temp_and_swapped_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:swap")
    external = _temp(tmp_path, b"untouched")
    with pytest.raises(CampusError):
        publish_attachment(external, directory, "sample.pdf", hashlib.sha256(b"untouched").hexdigest(), 9)
    assert external.read_bytes() == b"untouched"
    external.unlink()
    if os.name == "nt":
        return

    outside = tmp_path / "outside"
    outside.mkdir()
    moved = tmp_path / "moved"
    from campusctl import material_files

    original_copy = material_files._copy_verified

    def swap_during_copy(source: int, destination: int, size_bytes: int, sha256: str) -> None:
        original_copy(source, destination, size_bytes, sha256)
        directory.rename(moved)
        directory.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(material_files, "_copy_verified", swap_during_copy)
    with pytest.raises(CampusError):
        _publish(directory, "sample.pdf", b"full")
    assert not list(outside.iterdir())
    assert not list(moved.glob(".transfer-*"))


def test_receipt_url_malformed_permissions_and_symlink(tmp_path: Path) -> None:
    entity = "cnu_lms_material:synthetic:receipt"
    directory = prepare_output_dir(tmp_path, entity)
    saved = _publish(directory, "sample.pdf", b"verified")
    digest = hashlib.sha256(b"verified").hexdigest()
    receipt_file = tmp_path / "materials" / "receipts.json"
    with pytest.raises(CampusError):
        write_receipt(tmp_path, entity, directory, saved.path, 8, digest, "https://invalid.example/?key=secret")
    assert not receipt_file.exists()
    receipt_file.write_text("{bad json")
    with pytest.raises(CampusError):
        verified_receipt(tmp_path, entity, directory)
    receipt_file.write_text(
        json.dumps({"schema_version": 1, "items": [{"path": "https://invalid.example/?token=bad"}]})
    )
    with pytest.raises(CampusError):
        verified_receipt(tmp_path, entity, directory)
    receipt_file.unlink()
    write_receipt(tmp_path, entity, directory, saved.path, 8, digest, None)
    if os.name != "nt":
        assert receipt_file.stat().st_mode & 0o777 == 0o600
        target = tmp_path / "original-receipt"
        receipt_file.rename(target)
        receipt_file.symlink_to(target)
        with pytest.raises(CampusError):
            verified_receipt(tmp_path, entity, directory)
        receipt_file.unlink()
        ancestor = tmp_path / "linked-parent"
        ancestor.symlink_to(directory.parent, target_is_directory=True)
        with pytest.raises(CampusError):
            prepare_output_dir(tmp_path, entity, ancestor / "new-dir")
        assert not (directory.parent / "new-dir").exists()
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        with pytest.raises(CampusError) as failure:
            prepare_output_dir(tmp_path, entity, blocker / "new-dir")
        assert failure.value.code == "output-path-conflict"


def test_receipt_with_path_punctuation(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("question mark in directory name requires POSIX")
    entity = "cnu_lms_material:synthetic:punctuation"
    directory = prepare_output_dir(tmp_path, entity, tmp_path / "out?#&")
    result = _publish(directory, "file.pdf", b"safe")
    digest = hashlib.sha256(b"safe").hexdigest()
    write_receipt(tmp_path, entity, directory, result.path, 4, digest, None)
    assert verified_receipt(tmp_path, entity, directory)["path"] == str(result.path)


def test_temp_swap_after_hash_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:temp-swap")
    from campusctl import material_files

    original_copy = material_files._copy_verified

    def swap_after_copy(source: int, destination: int, size_bytes: int, sha256: str) -> None:
        original_copy(source, destination, size_bytes, sha256)
        for temp in directory.glob(".transfer-*.tmp"):
            temp.unlink()
            temp.write_bytes(b"changed")

    monkeypatch.setattr(material_files, "_copy_verified", swap_after_copy)
    with pytest.raises(CampusError) as failure:
        _publish(directory, "sample.pdf", b"safe")
    assert failure.value.code == "output-path-conflict"
    assert not (directory / "sample.pdf").exists()
    # Windows refuses unlinking an open transfer inode; POSIX permits its
    # replacement, which must remain untouched by cleanup.
    remaining = [p.read_bytes() for p in directory.glob(".transfer-*.tmp")]
    assert remaining == ([] if os.name == "nt" else [b"changed"])


def test_copied_inode_survives_same_size_source_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:mutated")
    from campusctl import material_files

    original_copy = material_files._copy_verified

    def mutate_source(source: int, destination: int, size_bytes: int, sha256: str) -> None:
        original_copy(source, destination, size_bytes, sha256)
        for transfer in directory.glob(".transfer-*.tmp"):
            with transfer.open("r+b") as changed:
                changed.write(b"X")

    monkeypatch.setattr(material_files, "_copy_verified", mutate_source)
    original = b"correct bytes"
    saved = _publish(directory, "sample.pdf", original)
    assert saved.path.read_bytes() == original
    assert hashlib.sha256(saved.path.read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest()
    assert not list(directory.glob(".material-*.tmp"))


def test_owned_temp_cleanup_for_invalid_metadata_and_copy_failure(tmp_path: Path) -> None:
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:cleanup")
    invalid = _temp(directory, b"bytes")
    with pytest.raises(CampusError):
        publish_attachment(invalid, directory, "sample.pdf", "not-a-digest", 5)
    assert not invalid.exists()
    mismatched = _temp(directory, b"bytes")
    with pytest.raises(CampusError):
        publish_attachment(mismatched, directory, "sample.pdf", hashlib.sha256(b"different").hexdigest(), 5)
    assert not mismatched.exists()
    assert not list(directory.glob(".material-*.tmp"))
    assert not (directory / "sample.pdf").exists()


def test_publication_temp_collision_is_not_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:temp-collision")
    occupied = directory / (".material-" + "0" * 32 + ".tmp")
    occupied.write_bytes(b"unrelated")
    names = iter(("0" * 32, "1" * 32))
    monkeypatch.setattr(material_files.secrets, "token_hex", lambda _count: next(names))
    saved = _publish(directory, "sample.pdf", b"safe bytes")
    assert saved.path.read_bytes() == b"safe bytes"
    assert occupied.read_bytes() == b"unrelated"
    assert list(directory.glob(".material-*.tmp")) == [occupied]


def test_publication_temp_swap_cannot_publish_unchecked_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        pytest.skip("renaming an open private temp is a POSIX-only injection")
    from campusctl import material_files

    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:temp-swap-link")
    original_link = material_files.os.link

    def swap_then_link(source: str, destination: str, **kwargs: object) -> None:
        fd = kwargs["src_dir_fd"]
        os.rename(source, "moved-private-copy", src_dir_fd=fd, dst_dir_fd=fd)
        replacement = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        with os.fdopen(replacement, "wb") as wrong:
            wrong.write(b"unchecked")
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(material_files.os, "link", swap_then_link)
    with pytest.raises(CampusError) as failure:
        _publish(directory, "sample.pdf", b"verified")
    assert failure.value.code == "output-path-conflict"
    assert not (directory / "sample.pdf").exists()
    assert (directory / "moved-private-copy").read_bytes() == b"verified"
    assert not list(directory.glob(".transfer-*.tmp"))


def test_windows_no_clobber_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    from campusctl import material_files

    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:windows-race")

    @contextmanager
    def windows_branch(path: Path):
        yield None, material_files._directory_identity(path)

    def competing_link(_source: Path, destination: Path, **_kwargs: object) -> None:
        destination.write_bytes(b"competing")
        raise FileExistsError

    monkeypatch.setattr(material_files, "_pinned_directory", windows_branch)
    monkeypatch.setattr(material_files.os, "link", competing_link)
    with pytest.raises(CampusError) as failure:
        _publish(directory, "sample.pdf", b"safe")
    assert failure.value.code == "output-path-conflict"
    assert (directory / "sample.pdf").read_bytes() == b"competing"


def test_verified_receipt_and_interrupted_temp(tmp_path: Path) -> None:
    entity_id = "cnu_lms_material:synthetic:attachment"
    directory = prepare_output_dir(tmp_path, entity_id)
    content = b"%PDF- safe synthetic file"
    result = _publish(directory, "example.pdf", content)
    digest = hashlib.sha256(content).hexdigest()
    assert verified_receipt(tmp_path, entity_id, directory) is None
    write_receipt(tmp_path, entity_id, directory, result.path, len(content), digest, None)
    receipt = verified_receipt(tmp_path, entity_id, directory)
    assert receipt is not None and receipt["path"] == str(result.path) and receipt["observed_mime"] is None
    document = json.loads((tmp_path / "materials" / "receipts.json").read_text())
    assert document["schema_version"] == 2 and len(document["items"]) == 1
    assert document["items"][0]["course_root"] is None
    assert verified_receipt(tmp_path, "cnu_lms_material:synthetic:different", directory) is None
    different_dir = prepare_output_dir(tmp_path, entity_id, tmp_path / "elsewhere")
    assert verified_receipt(tmp_path, entity_id, different_dir) is None
    result.path.write_bytes(content[:5] + b"X" + content[6:])
    assert verified_receipt(tmp_path, entity_id, directory) is None
    result.path.write_bytes(content)
    interrupted = _temp(directory, b"partial")
    assert verified_receipt(tmp_path, entity_id, directory) is not None
    assert _publish(directory, "example.pdf", content).outcome == "reused"
    interrupted.unlink()
    assert not list(directory.glob(".transfer-*"))
    if os.name != "nt":
        result.path.unlink()
        result.path.symlink_to(tmp_path / "materials" / "receipts.json")
        assert verified_receipt(tmp_path, entity_id, directory) is None
        with pytest.raises(CampusError) as failure:
            write_receipt(tmp_path, entity_id, directory, result.path, len(content), digest, "application/x-pdf")
        assert failure.value.code == "output-path-conflict"


def test_interrupted_publication_never_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        pytest.skip("dir_fd race injection requires POSIX")
    directory = prepare_output_dir(tmp_path, "cnu_lms_material:synthetic:race")
    partial = _temp(directory, b"truncated")
    with pytest.raises(CampusError):
        publish_attachment(partial, directory, "sample.pdf", hashlib.sha256(b"full").hexdigest(), 4)
    assert not partial.exists() and not (directory / "sample.pdf").exists()

    original_link = os.link

    def competing_link(source: str, destination: str, **kwargs: object) -> None:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=kwargs["dst_dir_fd"])
        with os.fdopen(fd, "wb") as target:
            target.write(b"other writer")
        original_link(source, destination, **kwargs)

    monkeypatch.setattr("campusctl.material_files.os.link", competing_link)
    content = b"complete"
    with pytest.raises(CampusError) as failure:
        _publish(directory, "sample.pdf", content)
    assert failure.value.code == "output-path-conflict"
    assert (directory / "sample.pdf").read_bytes() == b"other writer"
    assert not list(directory.glob(".transfer-*"))


def test_receipt_update_failure_does_not_claim_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entity_id = "cnu_lms_material:synthetic:attachment"
    directory = prepare_output_dir(tmp_path, entity_id)
    result = _publish(directory, "example.pdf", b"valid bytes")
    digest = hashlib.sha256(b"valid bytes").hexdigest()
    monkeypatch.setattr(
        "campusctl.material_files.os.replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("failed"))
    )
    with pytest.raises(CampusError) as failure:
        write_receipt(tmp_path, entity_id, directory, result.path, 11, digest, "text/plain")
    assert failure.value.code == "output-path-conflict"
    assert result.path.read_bytes() == b"valid bytes"
    assert verified_receipt(tmp_path, entity_id, directory) is None
    assert not list((tmp_path / "materials").glob(".receipts.*.tmp"))


def test_layout_validation_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl.config import validate_config

    row = {"course": {"id": "course-a", "label": "Course/Section"}}
    monkeypatch.setenv("CAMPUSCTL_TEST_DIR", str(tmp_path))
    settings = {
        "materials": {
            "download_dir": "$CAMPUSCTL_TEST_DIR/{semester}/{course}/materials",
            "semester": "2026-2",
            "adopt_existing": True,
        }
    }
    validate_config(settings, path=tmp_path / "config.toml")
    destination, root = resolve_download_layout(settings, row, None)
    assert destination == tmp_path / "2026-2" / "Course_Section" / "materials"
    assert root == destination.parent
    assert resolve_download_layout(settings, row, tmp_path / "override") == (tmp_path / "override", None)
    for bad in ("CON.txt", "notes."):
        template = {"materials": {"download_dir": str(tmp_path / bad / "{course}")}}
        with pytest.raises(CampusError) as failure:
            validate_config(template, path=tmp_path / "config.toml")
        assert failure.value.code == "config-invalid"
        monkeypatch.setenv("CAMPUSCTL_TEST_BAD", bad)
        template["materials"]["download_dir"] = str(tmp_path / "$CAMPUSCTL_TEST_BAD" / "{course}")
        validate_config(template, path=tmp_path / "config.toml")
        with pytest.raises(CampusError) as failure:
            resolve_download_layout(template, row, None)
        assert failure.value.code == "config-invalid"
    for invalid in ("relative/{course}", str(tmp_path / "{unknown}"), str(tmp_path / "../{course}")):
        with pytest.raises(CampusError):
            resolve_download_layout({"materials": {"download_dir": invalid}}, row, None)


@pytest.mark.skipif(os.name == "nt", reason="Windows adoption fails closed without pinned reparse-point checks")
@pytest.mark.parametrize("size", (127 * 1024, 192 * 1024))
def test_adoption_comparison_tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    from campusctl import material_files

    course = prepare_output_dir(tmp_path, "course", tmp_path / "course")
    destination = prepare_output_dir(tmp_path, "course", course / "materials")
    archive = prepare_output_dir(tmp_path, "course", course / "archive")
    content = b"A" * size
    digest = hashlib.sha256(content).hexdigest()
    transfer = _temp(destination, content)
    candidate = archive / "sample.pdf"
    candidate.write_bytes(content[:-1] + b"B")
    monkeypatch.setattr(material_files, "_SCAN_BYTES", 2 * 64 * 1024)
    assert adopt_attachment(transfer, destination, course, candidate.name, size, digest) is None
    if size > 128 * 1024:
        candidate.write_bytes(content[: size // 2] + b"B" + content[size // 2 + 1 :])
        monkeypatch.setattr(material_files, "_SCAN_BYTES", size + 2 * 64 * 1024)
        assert adopt_attachment(transfer, destination, course, candidate.name, size, digest) is None
    candidate.write_bytes(content)
    monkeypatch.setattr(material_files, "_SCAN_BYTES", size + 2 * 64 * 1024)
    result = adopt_attachment(transfer, destination, course, candidate.name, size, digest)
    assert result is not None and result.path == candidate and result.outcome == "adopted"
    assert result.path.read_bytes() == content


@pytest.mark.skipif(os.name == "nt", reason="Windows adoption fails closed without pinned reparse-point checks")
def test_same_directory_case_variant_and_receipt_migration(tmp_path: Path) -> None:
    course = prepare_output_dir(tmp_path, "course", tmp_path / "course")
    destination = prepare_output_dir(tmp_path, "course", course / "materials")
    content = b"synthetic attachment"
    digest = hashlib.sha256(content).hexdigest()
    existing = destination / "SAMPLE.PDF"
    existing.write_bytes(content)
    transfer = _temp(destination, content)
    reused = adopt_attachment(transfer, destination, course, "sample.pdf", len(content), digest)
    assert reused is not None and reused.path == existing and reused.outcome == "reused"
    entity_id = "cnu_lms_material:course-a:file-1"
    receipt_path = tmp_path / "materials" / "receipts.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "entity_id": entity_id,
                        "output_dir": str(destination),
                        "path": str(existing),
                        "size_bytes": len(content),
                        "sha256": digest,
                        "observed_mime": None,
                    }
                ],
            }
        )
    )
    assert verified_receipt(tmp_path, entity_id, destination) is not None
    write_receipt(tmp_path, "cnu_lms_material:course-a:file-2", destination, existing, len(content), digest, None)
    data = json.loads(receipt_path.read_text())
    assert data["schema_version"] == 2 and len(data["items"]) == 2
    assert all(item["course_root"] is None for item in data["items"])


@pytest.mark.skipif(os.name == "nt", reason="Windows adoption fails closed without pinned reparse-point checks")
def test_adoption_scan_bounds_and_symlink_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import material_files

    course = prepare_output_dir(tmp_path, "course", tmp_path / "course")
    destination = prepare_output_dir(tmp_path, "course", course / "materials")
    content = b"A" * (192 * 1024)
    digest = hashlib.sha256(content).hexdigest()
    transfer = _temp(destination, content)
    sibling = prepare_output_dir(tmp_path, "course", course / "archive")
    candidate = sibling / "sample.pdf"
    candidate.write_bytes(content)
    monkeypatch.setattr(material_files, "_SCAN_BYTES", 2 * 64 * 1024 + len(content) - 1)
    with pytest.raises(CampusError) as error:
        adopt_attachment(transfer, destination, course, candidate.name, len(content), digest)
    assert error.value.code == "output-path-conflict"
    monkeypatch.setattr(material_files, "_SCAN_BYTES", 1_000_000_000)
    monkeypatch.setattr(material_files, "_SCAN_ENTRIES", 2)
    with pytest.raises(CampusError) as error:
        adopt_attachment(transfer, destination, course, candidate.name, len(content), digest)
    assert error.value.code == "output-path-conflict"
    monkeypatch.setattr(material_files, "_SCAN_ENTRIES", 10_000)
    if os.name != "nt":
        candidate.unlink()
        candidate.symlink_to(transfer)
        assert adopt_attachment(transfer, destination, course, candidate.name, len(content), digest) is None


def test_template_expansion_on_macos_and_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campusctl import config, material_files

    if os.name != "nt":
        row = {"course": {"id": "course-a", "label": "Sample"}}
        monkeypatch.setattr(material_files, "_home_dir", lambda: tmp_path)
        monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="darwin"))
        assert resolve_download_layout({"materials": {"download_dir": "~/School/{course}"}}, row, None) == (
            tmp_path / "School" / "Sample",
            None,
        )
    monkeypatch.setattr(config, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(material_files, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("CAMPUSCTL_FOLDER", "School")
    template = "C:/%CAMPUSCTL_FOLDER%/{course_id}/files"
    config.validate_config({"materials": {"download_dir": template}}, path=tmp_path / "config.toml")
    prefix, parts = material_files._layout_parts(template)
    assert prefix == "C:\\" and parts == ["School", "{course_id}", "files"]
    monkeypatch.setenv("CAMPUSCTL_FOLDER", "CON.txt")
    with pytest.raises(CampusError) as failure:
        material_files._layout_parts(template)
    assert failure.value.code == "config-invalid"
