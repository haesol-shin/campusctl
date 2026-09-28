"""Exercise browser CI policy in isolated pytest processes."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def test_plain(): pass\n", "no chromium-marked tests collected"),
        (
            "import pytest\npytestmark = pytest.mark.chromium\ndef test_skip(): pytest.skip('optional browser')\n",
            "marked test skipped",
        ),
    ],
)
def test_required_chromium_rejects_missing_coverage(tmp_path: Path, source: str, expected: str) -> None:
    (tmp_path / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (tmp_path / "test_policy.py").write_text(source)
    env = {**os.environ, "CAMPUSCTL_REQUIRE_CHROMIUM": "1"}
    if expected == "marked test skipped":
        browsers = tmp_path / "synthetic-browsers"
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
        with patch.dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(browsers)), sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
        executable.parent.mkdir(parents=True)
        executable.touch()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-c", str(Path(__file__).parents[1] / "pyproject.toml"), str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert expected in result.stdout + result.stderr


def test_required_chromium_rejects_missing_executable(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (tmp_path / "test_policy.py").write_text(
        "import pytest\npytestmark = pytest.mark.chromium\ndef test_browser(): pass\n"
    )
    browsers = tmp_path / "empty-browsers"
    browsers.mkdir()
    env = {**os.environ, "CAMPUSCTL_REQUIRE_CHROMIUM": "1", "PLAYWRIGHT_BROWSERS_PATH": str(browsers)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-c", str(Path(__file__).parents[1] / "pyproject.toml"), str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert "Playwright Chromium executable is missing" in result.stdout + result.stderr


def test_browser_shards_partition_all_collected_tests_once(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (tmp_path / "test_policy.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('case', range(7))\n"
        "def test_plain(case): pass\n"
        "@pytest.mark.chromium\n"
        "@pytest.mark.parametrize('case', range(6))\n"
        "def test_browser(case): pass\n"
    )
    env = {**os.environ, "CAMPUSCTL_REQUIRE_CHROMIUM": "0"}

    def collect(*options: str) -> list[str]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-c",
                str(Path(__file__).parents[1] / "pyproject.toml"),
                *options,
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return [line for line in result.stdout.splitlines() if "test_policy.py::" in line]

    all_tests = collect()
    shards = [collect("--shard-id", str(index), "--num-shards", "3") for index in range(1, 4)]
    assert len(all_tests) == 13
    assert sorted(test for shard in shards for test in shard) == sorted(all_tests)
    assert all(any("::test_browser" in test for test in shard) for shard in shards)
