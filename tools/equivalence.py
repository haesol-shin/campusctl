"""Compare committed revisions offline: uv run --locked python tools/equivalence.py
--parent REF --candidate REF --scratch DIR [--self-test] [--scenarios FILE].

The scratch directory must exist; only a newly allocated disposable child is removed.
Scenario modules provide cases() and prepare(root, case, actions), a context manager.
Cases contain name, argv, width and expected exit; optional env/input/TTY fields.
prepare yields JSON-serializable fake state and appends ordered external actions.
Extend those external-boundary fakes for each changed path; never stub its owners.
Reports contain counts, not captured output. Equality proves only the covered paths.
Use the locked project environment on Linux and trusted revisions/scenarios only:
external-I/O guards are not a security sandbox. cli.main(argv) must return an int.
--self-test deliberately exits 1; an ordinary equal comparison exits 0.
"""

import argparse
import datetime
import hashlib
import importlib.util
import io
import json
import os
import platform
import random
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from equivalence_cases import Case

FROZEN_EPOCH = 1767323045.0


@dataclass(frozen=True, slots=True)
class Capture:
    stdout: bytes
    stderr: bytes
    code: int
    state: bytes
    artifacts: tuple[tuple[str, bytes], ...]


def differences(parent: Capture, candidate: Capture) -> list[str]:
    return [name for name in Capture.__dataclass_fields__ if getattr(parent, name) != getattr(candidate, name)]


def load_scenarios(path: Path):
    spec = importlib.util.spec_from_file_location("equivalence_scenarios", path)
    if spec is None or spec.loader is None:
        raise ValueError("Scenario module is not loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export_revision(repo: Path, revision: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", revision], capture_output=True, check=True
    )
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as source:
        source.extractall(destination, filter="data")


def child_environment(root: Path) -> dict[str, str]:
    # An allowlist prevents inherited output, credential, proxy and platform settings leaking in.
    return {
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "temp"),
        "CAMPUSCTL_CONFIG_DIR": str(root / "config"),
        "CAMPUSCTL_DATA_DIR": str(root / "data"),
        "XDG_CONFIG_HOME": str(root / "home" / "config"),
        "XDG_DATA_HOME": str(root / "home" / "data"),
        "XDG_CACHE_HOME": str(root / "home" / "cache"),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def capture(tree: Path, root: Path, scenario: Path, case: "Case") -> Capture:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    for name in ("home", "temp", "config", "data"):
        (root / name).mkdir()
    state_path = root.parent / "state.json"
    state_path.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--child", str(tree), str(root), str(scenario)],
        input=json.dumps(case).encode(),
        capture_output=True,
        cwd=root,
        env=child_environment(root),
        timeout=60,
    )
    if not state_path.is_file():
        raise RuntimeError(f"Comparison child failed before capture (exit {result.returncode})")
    artifacts = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            artifacts.append((relative, b"link\0" + os.readlink(path).encode()))
        elif path.is_file():
            artifacts.append((relative, b"file\0" + path.read_bytes()))
        elif path.is_dir():
            artifacts.append((relative + "/", b""))
        else:
            raise RuntimeError("Unsupported artifact type")
    return Capture(result.stdout, result.stderr, result.returncode, state_path.read_bytes(), tuple(artifacts))


class FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls.fromtimestamp(FROZEN_EPOCH, datetime.UTC)
        return value.replace(tzinfo=None) if tz is None else value.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return cls.now().replace(tzinfo=None)


class Determinism:
    """Own independent, counted entropy streams and virtual elapsed time per child."""

    def __init__(self):
        self.counts = Counter()
        self.elapsed = 0.0

    def take(self, stream: str, size: int) -> bytes:
        self.counts[stream] += 1
        seed = f"{stream}:{self.counts[stream]}".encode()
        return hashlib.shake_256(seed).digest(size)

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds

    def install(self) -> None:
        datetime.datetime = FrozenDatetime
        platform.platform = lambda: "Linux-offline"
        platform.python_version = lambda: "3.12.0"
        sys.platform = "linux"
        time.time = lambda: FROZEN_EPOCH + self.elapsed
        time.monotonic = lambda: self.elapsed
        time.perf_counter = lambda: self.elapsed
        time.time_ns = lambda: int(time.time() * 1_000_000_000)
        time.monotonic_ns = lambda: int(self.elapsed * 1_000_000_000)
        time.perf_counter_ns = time.monotonic_ns
        time.sleep = self.sleep
        uuid.uuid4 = lambda: uuid.UUID(bytes=self.take("uuid", 16), version=4)
        os.urandom = lambda size: self.take("urandom", size)
        secrets.token_bytes = lambda size=None: self.take("secrets", 32 if size is None else size)
        tempfile._RandomNameSequence.__next__ = lambda _: self.take("tempname", 8).hex()
        random.seed(0)


def deny_external(event: str, args: tuple, violations: list[str]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
        violations.append(event)
        raise RuntimeError("External I/O is forbidden in offline scenarios")


def run_child(tree: Path, root: Path, scenario: Path) -> int:
    case: Case = json.load(sys.stdin)
    sys.path.insert(0, str(tree / "src"))
    sys.dont_write_bytecode = True
    deterministic = Determinism()
    deterministic.install()
    violations = []
    sys.addaudithook(lambda event, args: deny_external(event, args, violations))
    module = load_scenarios(scenario)
    actions = []
    sys.stdin = io.StringIO(case.get("input", ""))
    sys.stdin.isatty = lambda: case.get("stdin_tty", False)
    sys.stdout.isatty = lambda: case.get("stdout_tty", False)
    os.environ["COLUMNS"] = str(case["width"])
    os.environ["LINES"] = "24"
    os.environ.update(case.get("env", {}))
    with module.prepare(root, case, actions) as fake_state:
        if case.get("invoke") == "play_lectures":
            code = module.invoke_play(case)
        else:
            from campusctl import cli

            code = cli.main(case["argv"])
    if violations:
        raise RuntimeError("Scenario attempted forbidden external I/O")
    for name, imported in tuple(sys.modules.items()):
        if (name == "campusctl" or name.startswith("campusctl.")) and not Path(
            imported.__file__
        ).resolve().is_relative_to(tree / "src"):
            raise RuntimeError("Product import escaped the selected revision")
    (root.parent / "state.json").write_text(
        json.dumps(
            {
                "actions": actions,
                "fake_state": fake_state,
                "entropy": dict(deterministic.counts),
                "elapsed": deterministic.elapsed,
            }
        ),
        encoding="utf-8",
    )
    return code


def compare_revisions(
    repo: Path, workspace: Path, *, parent: str, candidate: str, scenario: Path, self_test: bool
) -> int:
    trees = [workspace / "parent", workspace / "candidate"]
    for revision, tree in zip((parent, candidate), trees, strict=True):
        export_revision(repo, revision, tree)
    locks = [(tree / "uv.lock").read_bytes() for tree in trees]
    if locks[0] != locks[1] or locks[0] != (repo / "uv.lock").read_bytes():
        raise ValueError("Parent, candidate and active environment must share uv.lock")
    cases: list[Case] = load_scenarios(scenario).cases()
    if not cases:
        raise ValueError("At least one scenario is required")
    mismatches = 0
    statuses = Counter()
    injected = False
    for case in cases:
        baseline = capture(trees[0], workspace / "sandbox", scenario, case)
        proposed = capture(trees[1], workspace / "sandbox", scenario, case)
        statuses[baseline.code] += 1
        if baseline.code != case["expected"]:
            raise RuntimeError(f"Baseline case {case['name']} did not reach expected exit {case['expected']}")
        if self_test and not injected:
            proposed = replace(proposed, stdout=proposed.stdout + b"deliberate-mismatch")
            injected = True
        fields = differences(baseline, proposed)
        if fields:
            mismatches += 1
            print(f"mismatch: {case['name']}: {','.join(fields)}")
    print(f"cases={len(cases)} mismatches={mismatches} exits={dict(sorted(statuses.items()))}")
    print(f"byte/artifact/action/state equality={'FAIL' if mismatches else 'PASS'} self_test={injected}")
    return int(mismatches != 0)


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        return run_child(*(Path(value) for value in sys.argv[2:]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--scenarios", type=Path, default=Path(__file__).with_name("equivalence_cases.py"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    repo = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    with tempfile.TemporaryDirectory(prefix="equivalence-", dir=args.scratch.resolve()) as temporary:
        return compare_revisions(
            repo,
            Path(temporary),
            parent=args.parent,
            candidate=args.candidate,
            scenario=args.scenarios.resolve(),
            self_test=args.self_test,
        )


if __name__ == "__main__":
    raise SystemExit(main())
