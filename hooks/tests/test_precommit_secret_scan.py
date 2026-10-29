"""Tests for .githooks/pre-commit, the staged-diff gitleaks scan (CP-4).

Runs the real POSIX sh hook against a throwaway git repository with a staged file. gitleaks
itself is replaced by a fake script (GITLEAKS_BIN) with a chosen exit code, or made
unreachable by running the hook with a PATH that holds only git and sh, so the outcome
never depends on whether this machine has gitleaks installed.

Contract under test:
  - gitleaks missing, or failing to run   -> commit BLOCKED (exit 1) with a clear message
  - gitleaks clean (exit 0)                -> commit allowed
  - gitleaks finding (exit 77)             -> commit BLOCKED
  - BIMP_SECRETS_BYPASS="<reason>"         -> commit allowed, reason logged

Run: python -m pytest hooks/tests/test_precommit_secret_scan.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".githooks" / "pre-commit"
SH = shutil.which("sh")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(not (SH and GIT), reason="needs sh and git on PATH")


def _minimal_path() -> str:
    """A PATH holding only the folders of git and sh (plus Git for Windows' usr/bin for coreutils)."""
    dirs: list[str] = []
    for exe in (GIT, SH):
        assert exe is not None
        d = str(Path(exe).resolve().parent)
        if d not in dirs:
            dirs.append(d)
    if os.name == "nt":
        git_root = Path(GIT).resolve().parent.parent  # <Git>/cmd or <Git>/mingw64/bin -> <Git>
        for extra in (git_root / "usr" / "bin", git_root.parent / "usr" / "bin"):
            if extra.is_dir() and str(extra) not in dirs:
                dirs.append(str(extra))
    return os.pathsep.join(dirs)


@pytest.fixture()
def staged_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@example.invalid"]):
        subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True)
    (repo / "notes.txt").write_text("nothing secret here\n", encoding="utf-8")
    subprocess.run([GIT, "add", "notes.txt"], cwd=repo, check=True, capture_output=True)
    return repo


def fake_gitleaks(tmp_path: Path, exit_code: int) -> str:
    fake = tmp_path / "fake-bin" / "gitleaks"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    return fake.as_posix()


def run_hook(repo: Path, *, gitleaks_bin: str | None = None, bypass: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in {"GITLEAKS_BIN", "BIMP_SECRETS_BYPASS"}}
    env["PATH"] = _minimal_path()
    if gitleaks_bin is not None:
        env["GITLEAKS_BIN"] = gitleaks_bin
    if bypass is not None:
        env["BIMP_SECRETS_BYPASS"] = bypass
    return subprocess.run([SH, HOOK.as_posix()], cwd=repo, env=env, capture_output=True, text=True, timeout=60)


def test_minimal_path_has_no_gitleaks() -> None:
    """Precondition for the 'missing' case: the hook's PATH really cannot find gitleaks."""
    path = _minimal_path()
    assert shutil.which("gitleaks", path=path) is None, f"gitleaks reachable on {path}; the missing-case test is void"


def test_missing_gitleaks_blocks_with_clear_message(staged_repo: Path) -> None:
    proc = run_hook(staged_repo)
    assert proc.returncode == 1, proc.stderr
    assert "gitleaks not found" in proc.stderr
    assert "BLOCKED" in proc.stderr
    assert "BIMP_SECRETS_BYPASS" in proc.stderr  # the message names the audited way out


def test_gitleaks_bin_pointing_nowhere_blocks(staged_repo: Path, tmp_path: Path) -> None:
    proc = run_hook(staged_repo, gitleaks_bin=(tmp_path / "no-such-gitleaks").as_posix())
    assert proc.returncode == 1, proc.stderr
    assert "BLOCKED" in proc.stderr


def test_gitleaks_runtime_failure_blocks(staged_repo: Path, tmp_path: Path) -> None:
    proc = run_hook(staged_repo, gitleaks_bin=fake_gitleaks(tmp_path, 3))
    assert proc.returncode == 1, proc.stderr
    assert "failed to run (exit 3)" in proc.stderr
    assert "BLOCKED" in proc.stderr
    assert "BIMP_SECRETS_BYPASS" in proc.stderr


def test_clean_scan_allows(staged_repo: Path, tmp_path: Path) -> None:
    proc = run_hook(staged_repo, gitleaks_bin=fake_gitleaks(tmp_path, 0))
    assert proc.returncode == 0, proc.stderr


def test_finding_blocks(staged_repo: Path, tmp_path: Path) -> None:
    proc = run_hook(staged_repo, gitleaks_bin=fake_gitleaks(tmp_path, 77))
    assert proc.returncode == 1, proc.stderr
    assert "staged secret" in proc.stderr


def test_explicit_bypass_allows_and_logs(staged_repo: Path) -> None:
    proc = run_hook(staged_repo, bypass="gitleaks not installed on this box yet")
    assert proc.returncode == 0, proc.stderr
    log = staged_repo / ".git" / "secrets-bypass.log"
    assert "gitleaks not installed on this box yet" in log.read_text(encoding="utf-8")
