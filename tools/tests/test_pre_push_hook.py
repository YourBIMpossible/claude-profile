"""Tests for .githooks/pre-push (the local run of the CI gate, tools/gate.py).

Each case builds a throwaway repository holding this checkout's hook, gate and scanner, plus one passing test in
each folder the gate runs pytest over, activates the hook through core.hooksPath, and runs a real `git push` to a
local bare remote, so git itself invokes the hook (under Git for Windows' sh on Windows). A `python3` shim first on
PATH makes the hook run the gate with this interpreter, which has pytest. Fixture paths are assembled from pieces so
that this file holds no literal machine path (it is scanned too).

Run: python -m pytest tools/tests -q   (or directly: python tools/tests/test_pre_push_hook.py)
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = ".githooks/pre-push"
SCANNER = ".github/scripts/check_machine_paths.py"
GATE = "tools/gate.py"
PASSING_TEST = "def test_ok():\n    assert True\n"
FAILING_TEST = "def test_red():\n    assert 1 == 2\n"
# Fails when a `git -c` given to the push leaks through the hook into the gate's processes.
CONFIG_PROBE = "import os\n\ndef test_no_push_config():\n    assert 'GIT_CONFIG_PARAMETERS' not in os.environ\n"

BS = "\\"
DRIVE_PATH = "Q" + ":" + BS + "work" + BS + "repo" + BS + "notes.md"  # synthetic drive letter

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


SHIM_PATH = {}


def run(root: Path, *args: str, env: dict = None):
    base = dict(os.environ)
    if SHIM_PATH:
        base["PATH"] = SHIM_PATH["dir"] + os.pathsep + base.get("PATH", "")
    for name in ("BIMP_PREPUSH_BYPASS", "BIMP_SECRETS_BYPASS"):
        base.pop(name, None)
    proc = subprocess.run(list(args), cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**base, **(env or {})})
    return proc.returncode, (proc.stdout + proc.stderr).replace("\r\n", "\n")


def git(root: Path, *args: str) -> str:
    rc, out = run(root, "git", *args)
    assert rc == 0, f"git {' '.join(args)} failed ({rc}):\n{out}"
    return out


def commit(root: Path, rel: str, text: str, msg: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


def remote_has(src: Path, branch: str) -> bool:
    rc, _ = run(src.parent, "git", "--git-dir", "origin.git", "rev-parse", "-q", "--verify", f"refs/heads/{branch}")
    return rc == 0


@pytest.fixture
def src(tmp_path: Path):
    """A repo with the hook, the gate and the scanner, a bare origin, core.hooksPath set, and a clean first commit."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    SHIM_PATH["dir"] = str(shim_dir)
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    git(src, "config", "user.name", "Hook Test")
    git(src, "config", "user.email", "hook-test@example.invalid")
    git(src, "config", "core.hooksPath", ".githooks")
    for rel in (HOOK, SCANNER, GATE, "tools/machine_config.py"):
        dest = src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest)
    for folder in ("hooks/tests", "tools/tests", "skills/graphify/scripts"):
        (src / folder).mkdir(parents=True, exist_ok=True)
        (src / folder / f"test_{folder.replace('/', '_')}.py").write_text(PASSING_TEST, encoding="utf-8")
    hook = src / HOOK
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    git(tmp_path, "init", "-q", "--bare", "origin.git")
    git(src, "remote", "add", "origin", str(tmp_path / "origin.git"))
    commit(src, "README.md", "Reports go to {reports}/x.md.\n", "clean base")
    yield src
    SHIM_PATH.clear()


def test_clean_push_passes(src: Path) -> None:
    rc, out = run(src, "git", "push", "-q", "origin", "main")
    assert rc == 0, out
    assert remote_has(src, "main")
    assert "GATE PASS: 5 of 5 steps passed" in out, out
    assert "pre-push: gate passed" in out, out


def test_git_c_on_the_push_does_not_reach_the_gate(src: Path) -> None:
    git(src, "switch", "-q", "-c", "gitc")
    commit(src, "tools/tests/test_probe.py", CONFIG_PROBE, "add a config probe")
    rc, out = run(src, "git", "-c", "gate.probe=1", "push", "-q", "origin", "gitc")
    assert rc == 0 and remote_has(src, "gitc"), out
    assert "GATE PASS: 5 of 5 steps passed" in out, out


def test_drive_path_commit_is_blocked_with_the_ci_report(src: Path) -> None:
    git(src, "switch", "-q", "-c", "drive")
    commit(src, "docs/notes.md", f"See {DRIVE_PATH} for details.\n", "add drive path")
    _, ci_report = run(src, sys.executable, SCANNER)
    ci_report = ci_report.strip()
    rc, out = run(src, "git", "push", "-q", "origin", "drive")
    assert rc != 0, out
    assert not remote_has(src, "drive")
    assert "drive-path" in ci_report and ci_report in out, f"expected:\n{ci_report}\n--- got:\n{out}"
    assert "GATE FAIL: 1 of 5 steps failed: machine-paths guard scan" in out, out
    assert "pre-push: BLOCKED" in out, out


def test_committed_failing_test_is_blocked(src: Path) -> None:
    git(src, "switch", "-q", "-c", "red")
    commit(src, "tools/tests/test_red.py", FAILING_TEST, "add a failing test")
    rc, out = run(src, "git", "push", "-q", "origin", "red")
    assert rc != 0 and not remote_has(src, "red"), out
    assert "test_red" in out and "GATE FAIL: 1 of 5 steps failed: pytest tools/tests" in out, out
    assert "pre-push: BLOCKED" in out, out


def test_working_copy_fix_of_a_failing_test_does_not_unblock_it(src: Path) -> None:
    git(src, "switch", "-q", "-c", "red")
    commit(src, "tools/tests/test_red.py", FAILING_TEST, "add a failing test")
    (src / "tools" / "tests" / "test_red.py").write_text(PASSING_TEST, encoding="utf-8")
    rc, out = run(src, "git", "push", "-q", "origin", "red")
    assert rc != 0 and not remote_has(src, "red"), out


def test_a_commit_without_the_gate_is_blocked(src: Path) -> None:
    git(src, "switch", "-q", "-c", "nogate")
    git(src, "rm", "-q", GATE)
    git(src, "commit", "-q", "-m", "drop the gate")
    rc, out = run(src, "git", "push", "-q", "origin", "nogate")
    assert rc != 0 and not remote_has(src, "nogate"), out
    assert "has no tools/gate.py" in out and "pre-push: BLOCKED" in out, out


def test_working_copy_fix_does_not_unblock_the_pushed_commit(src: Path) -> None:
    git(src, "switch", "-q", "-c", "drive")
    commit(src, "docs/notes.md", f"See {DRIVE_PATH} for details.\n", "add drive path")
    (src / "docs" / "notes.md").write_text("See {repos}/repo/notes.md for details.\n", encoding="utf-8")
    rc, out = run(src, "git", "push", "-q", "origin", "drive")
    assert rc != 0 and not remote_has(src, "drive"), out


def test_staged_machine_path_does_not_block_a_clean_commit(src: Path) -> None:
    git(src, "switch", "-q", "-c", "dirty")
    commit(src, "docs/clean.md", "Nothing machine-specific.\n", "clean change")
    (src / "docs" / "scratch.md").write_text(f"local only {DRIVE_PATH}\n", encoding="utf-8")
    git(src, "add", "docs/scratch.md")
    rc, out = run(src, "git", "push", "-q", "origin", "dirty")
    assert rc == 0 and remote_has(src, "dirty"), out


def test_bypass_passes_and_is_logged(src: Path) -> None:
    git(src, "switch", "-q", "-c", "drive")
    commit(src, "docs/notes.md", f"See {DRIVE_PATH} for details.\n", "add drive path")
    rc, out = run(src, "git", "push", "-q", "origin", "drive", env={"BIMP_PREPUSH_BYPASS": "hook test"})
    assert rc == 0 and remote_has(src, "drive"), out
    common = Path(git(src, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    assert "hook test" in (common / "prepush-bypass.log").read_text(encoding="utf-8")


def test_branch_deletion_is_not_scanned(src: Path) -> None:
    git(src, "push", "-q", "origin", "main:gone")
    rc, out = run(src, "git", "push", "-q", "origin", ":gone")
    assert rc == 0 and not remote_has(src, "gone"), out
    assert "running the gate" not in out, out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
