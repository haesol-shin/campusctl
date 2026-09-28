"""Behavioral mutations for the public English/Korean documentation gate."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_docs_parity.py"
PAIRED_FILES = (
    "README.md",
    "README.ko.md",
    "docs/installation.md",
    "docs/installation.ko.md",
    "docs/configuration.md",
    "docs/configuration.ko.md",
    "docs/troubleshooting.md",
    "docs/troubleshooting.ko.md",
    "docs/agent-skill.md",
    "docs/agent-skill.ko.md",
    "docs/usage.md",
    "docs/usage.ko.md",
)
LINK_TARGETS = (
    "LICENSE",
    ".github/workflows/ci.yml",
    "docs/contracts/cli.md",
    "docs/contracts/assignments.md",
    "docs/contracts/notices.md",
    "docs/contracts/materials.md",
)


def _snapshot(root: Path) -> Path:
    for name in (*PAIRED_FILES, *LINK_TARGETS):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_repository_pairs_pass() -> None:
    result = _run(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK (6 pairs)" in result.stdout


@pytest.mark.parametrize("missing", ["docs/usage.md", "docs/usage.ko.md"])
def test_missing_counterpart_fails(tmp_path: Path, missing: str) -> None:
    root = _snapshot(tmp_path)
    (root / missing).unlink()
    result = _run(root)
    assert result.returncode != 0
    assert f"{missing}:0: missing-counterpart" in result.stdout


def test_changed_heading_level_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    _replace(root / "README.ko.md", "## 자주 묻는 질문", "# 자주 묻는 질문")
    result = _run(root)
    assert result.returncode != 0
    assert "README.ko.md:" in result.stdout and "heading-level" in result.stdout


def test_changed_fenced_command_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    _replace(
        root / "README.ko.md",
        "uv tool run --from playwright playwright install-deps chromium",
        "uv tool run --from playwright playwright install-deps firefox",
    )
    result = _run(root)
    assert result.returncode != 0
    assert "README.ko.md:" in result.stdout and "fence-content" in result.stdout


def test_changed_link_target_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    _replace(root / "README.ko.md", "[설치](docs/installation.ko.md)", "[설치](docs/troubleshooting.ko.md)")
    result = _run(root)
    assert result.returncode != 0
    assert "README.ko.md:" in result.stdout and "link-target" in result.stdout


def test_broken_switcher_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    _replace(root / "README.ko.md", "[English](README.md) | **한국어**", "[English](README.ko.md) | **한국어**")
    result = _run(root)
    assert result.returncode != 0
    assert "README.ko.md:1: switcher" in result.stdout


def test_same_broken_anchor_in_both_languages_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    _replace(root / "README.md", "docs/configuration.md#paths", "docs/configuration.md#missing-anchor")
    _replace(root / "README.ko.md", "docs/configuration.ko.md#paths", "docs/configuration.ko.md#missing-anchor")
    result = _run(root)
    assert result.returncode != 0
    assert "link-missing-anchor" in result.stdout
    assert "link-target" not in result.stdout


def test_same_missing_file_in_both_languages_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    _replace(root / "README.md", "[Usage](docs/usage.md)", "[Usage](docs/absent.md)")
    _replace(root / "README.ko.md", "[사용 안내](docs/usage.ko.md)", "[사용 안내](docs/absent.ko.md)")
    result = _run(root)
    assert result.returncode != 0
    assert "link-missing-file" in result.stdout
    assert "link-target" not in result.stdout


def test_orphan_korean_page_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    (root / "docs" / "orphan.ko.md").write_text("# Orphan\n", encoding="utf-8")
    result = _run(root)
    assert result.returncode != 0
    assert "docs/orphan.ko.md:1: orphan-ko" in result.stdout


@pytest.mark.parametrize(
    "replacement",
    [
        "| 남은 강의 보기 | [help](docs/usage.ko.md) |",
        "| 남은 강의 보기 | ![guide](docs/usage.ko.md) |",
        '| 남은 강의 보기 | <img src="docs/usage.ko.md"> |',
    ],
)
def test_links_in_tables_and_images_are_compared(tmp_path: Path, replacement: str) -> None:
    root = _snapshot(tmp_path)
    _replace(root / "README.ko.md", "| 남은 강의 보기 | `campusctl lectures list` |", replacement)
    result = _run(root)
    assert result.returncode != 0
    assert "README.ko.md:" in result.stdout and "link-target" in result.stdout


def test_same_invalid_line_anchor_in_both_languages_fails(tmp_path: Path) -> None:
    root = _snapshot(tmp_path)
    for name in ("docs/installation.md", "docs/installation.ko.md"):
        _replace(root / name, "#L212-L247", "#L9999-L10000")
    result = _run(root)
    assert result.returncode != 0
    assert "link-missing-anchor" in result.stdout
    assert "link-target" not in result.stdout
