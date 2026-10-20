import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "tools" / "equivalence.py"
SPEC = importlib.util.spec_from_file_location("equivalence", HARNESS)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize("field,value", [
    ("stdout", b"{\"a\": 1}\n"), ("stderr", b"warning\r\n"), ("code", 75),
    ("state", b'{"actions":["close","open"]}'),
    ("artifacts", (("receipt.json", b"file\0changed"),)),
])
def test_comparator_rejects_each_observable_difference(field, value):
    baseline = module.Capture(b'{"a":1}\n', b"warning\n", 0, b'{"actions":["open","close"]}',
                              (("receipt.json", b"file\0original"),))
    assert module.differences(baseline, replace(baseline, **{field: value})) == [field]
    assert module.differences(baseline, baseline) == []


def test_process_isolation_reset_entropy_and_artifact_bytes(tmp_path):
    tree = tmp_path / "revision"
    package = tree / "src" / "campusctl"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text('''
import os, secrets, sys, tempfile, uuid
from pathlib import Path
from datetime import datetime, UTC
counter = 0
def main(argv):
    global counter
    counter += 1
    root = Path(os.environ["CAMPUSCTL_DATA_DIR"])
    print(counter, datetime.now(UTC).isoformat(), sys.stdout.isatty())
    print("warning", file=sys.stderr)
    values = [uuid.uuid4().hex, uuid.uuid4().hex, secrets.token_hex(8)]
    fd, name = tempfile.mkstemp(dir=root)
    os.write(fd, b"payload")
    os.close(fd)
    (root / "receipt.json").write_text(str(values))
    return 0
''')
    scenario = tmp_path / "scenarios.py"
    scenario.write_text('''
from contextlib import contextmanager
@contextmanager
def prepare(root, case, actions):
    assert not (root / "data" / "receipt.json").exists()
    actions.extend([["session", "open"], ["session", "close"]])
    yield
''')
    case = {"argv": [], "width": 48}
    sandbox = tmp_path / "sandbox"
    first = module.capture(tree, sandbox, scenario, case)
    second = module.capture(tree, sandbox, scenario, case)
    assert module.differences(first, second) == []
    assert first.stdout == b"1 2026-01-02T03:04:05+00:00 False\n"
    assert first.stderr == b"warning\n"
    state = json.loads(first.state)
    assert state["entropy"] == {"uuid": 2, "secrets": 1, "tempname": 1}
    receipt = next(data for name, data in first.artifacts if name == "data/receipt.json")
    assert receipt.split(b"'")[1] != receipt.split(b"'")[3]
    assert state["actions"] == [["session", "open"], ["session", "close"]]


def test_child_crash_cannot_pass_as_matching_output(tmp_path):
    tree = tmp_path / "revision"
    (tree / "src").mkdir(parents=True)
    scenario = tmp_path / "broken.py"
    scenario.write_text("raise RuntimeError('scenario failed')")
    with pytest.raises(RuntimeError, match="failed before capture"):
        module.capture(tree, tmp_path / "sandbox", scenario, {"argv": [], "width": 100})


def test_forbidden_external_attempt_cannot_be_hidden_by_cli_error_handling(tmp_path):
    tree = tmp_path / "revision"
    package = tree / "src" / "campusctl"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text('''
import socket
def main(argv):
    try:
        socket.create_connection(("example.invalid", 443))
    except Exception:
        return 0
''')
    scenario = tmp_path / "scenarios.py"
    scenario.write_text('''
from contextlib import contextmanager
@contextmanager
def prepare(root, case, actions):
    yield {}
''')
    with pytest.raises(RuntimeError, match="failed before capture"):
        module.capture(tree, tmp_path / "sandbox", scenario, {"argv": [], "width": 100})


def test_lock_mismatch_rejects_comparison_before_cases(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "uv.lock").write_bytes(b"parent")
    subprocess.run(["git", "-C", str(repo), "add", "uv.lock"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Synthetic", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "test(equivalence): create synthetic baseline", "-m", "- Add a synthetic lockfile"],
                   check=True, capture_output=True)
    (repo / "uv.lock").write_bytes(b"different active lock")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="share uv.lock"):
        module.compare_revisions(repo, workspace, parent="HEAD", candidate="HEAD", scenario=tmp_path / "unused.py",
                                 self_test=False)
