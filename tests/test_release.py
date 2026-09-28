"""Release-preparation behavior and failure cases."""

import importlib.util
import shutil
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
SPEC = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def test_fold_merges_headings_in_filename_order(tmp_path: Path) -> None:
    fragments = tmp_path / "changelog.d"
    fragments.mkdir()
    (fragments / "22-second.md").write_text("## Added\n- Second feature\n\n## Fixed\n- A fix\n", encoding="utf-8")
    (fragments / "11-first.md").write_text("## Added\n- First feature\n", encoding="utf-8")
    (fragments / "direct-third.md").write_text("## Added\n- Direct feature\n", encoding="utf-8")
    (fragments / "README.md").write_text("Instructions", encoding="utf-8")

    body, consumed = release.fold_fragments(tmp_path)

    assert body == "### Added\n- First feature\n- Second feature\n- Direct feature\n\n### Fixed\n- A fix"
    assert [path.name for path in consumed] == ["11-first.md", "22-second.md", "direct-third.md"]


@pytest.mark.parametrize(
    ("filename", "text"),
    [
        ("pr-12-feature.md", "## Added\n- PR prefix is rejected\n"),
        ("123.added.md", "## Added\n- Dot separator is rejected\n"),
        ("direct.md", "## Added\n- Missing slug is rejected\n"),
        ("direct-.md", "## Added\n- Empty slug is rejected\n"),
        ("direct-BadSlug.md", "## Added\n- Uppercase is rejected\n"),
        ("direct-bad_slug.md", "## Added\n- Underscore is rejected\n"),
        ("direct-bad.md", "## Added\n- Valid\nUnexpected text\n"),
        ("direct-empty.md", "## Fixed\n- \n"),
        ("direct-heading.md", "## Removed\n- Gone\n## Added\n"),
        ("direct-unknown.md", "## Improvements\n- New\n"),
        ("wrong.type.md", "## Added\n- New\n"),
    ],
)
def test_malformed_fragments_are_rejected(tmp_path: Path, filename: str, text: str) -> None:
    fragments = tmp_path / "changelog.d"
    fragments.mkdir()
    (fragments / filename).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="fragment"):
        release.fold_fragments(tmp_path)


@pytest.mark.parametrize(
    ("original", "stale"),
    [
        ("blob/v{version}/", "blob/v0.0.0/"),
        ("This skill describes the v{version} release surface", "This skill describes the v0.0.0 release surface"),
    ],
)
def test_check_versions_detects_stale_reference(tmp_path: Path, original: str, stale: str) -> None:
    for name in (*release.VERSION_FILES, "uv.lock"):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(release.ROOT / name, dest)
    version = release.check_versions(release.ROOT)
    assert release.check_versions(tmp_path) == version
    skill = tmp_path / "skills/campusctl/SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8").replace(original.format(version=version), stale),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="version references disagree"):
        release.check_versions(tmp_path)


def test_check_versions_detects_stale_lockfile(tmp_path: Path) -> None:
    for name in (*release.VERSION_FILES, "uv.lock"):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(release.ROOT / name, dest)
    version = release.check_versions(release.ROOT)
    assert release.check_versions(tmp_path) == version
    lock = tmp_path / "uv.lock"
    stale_text = lock.read_text(encoding="utf-8").replace(
        f'name = "campusctl"\nversion = "{version}"',
        'name = "campusctl"\nversion = "0.0.0"',
    )
    lock.write_text(stale_text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"version references disagree.*uv\.lock: campusctl"):
        release.check_versions(tmp_path)
