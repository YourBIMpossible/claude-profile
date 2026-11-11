"""tools/git_gate.py: what it does when it cannot tell (slop audit 2026-11-09, rows SA09-2, SA09-3, SA09-6).

The module's promise is "exit 0 only when the gate is on in every clone that ships it". These tests pin the three
places where a failure used to read as "nothing to do" or leave a bad install behind:
  - a clone whose state git cannot report (git not on PATH, timeout, failing rev-parse) is planned as "unknown"
    and fails the run;
  - gitleaks is verified before it is installed, so a binary that does not run, or is the wrong version, never lands;
  - an unreadable machine config fails the run instead of dropping the claude-tools clone from the plan.

Run: python -m pytest tools/tests -q
"""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import git_gate  # noqa: E402
import machine_config  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def make_clone(path: Path, init: bool = True) -> Path:
    (path / ".githooks").mkdir(parents=True)
    (path / ".githooks" / "pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    if init:
        subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


def timing_out(clone, *args):
    raise subprocess.TimeoutExpired(["git", *args], 30)


# --- SA09-2: a clone git cannot report on --------------------------------------------------------------------------

@needs_git
def test_a_clone_that_ships_the_gate_is_planned(tmp_path):
    assert git_gate.plan_hooks(make_clone(tmp_path / "clone")).action == "set"
    assert git_gate.plan_hooks(make_clone(tmp_path / "copy", init=False)) is None  # a plain copy: no repository


@needs_git
def test_a_git_timeout_is_unknown_and_not_nothing_to_do(tmp_path, monkeypatch):
    clone = make_clone(tmp_path / "clone")
    monkeypatch.setattr(git_gate, "_git", timing_out)
    plan = git_gate.plan_hooks(clone)
    assert plan is not None and plan.action == "unknown" and "TimeoutExpired" in plan.current
    assert "cannot be read" in git_gate.describe_plan(plan)
    lines, ok = git_gate.enable([plan], check_only=True)
    assert not ok and any("GIT GATE UNKNOWN" in line and str(clone) in line for line in lines)


@needs_git
def test_a_failing_rev_parse_inside_a_repository_is_unknown(tmp_path, monkeypatch):
    clone = make_clone(tmp_path / "clone")
    failed = subprocess.CompletedProcess(["git"], 128, "", "fatal: detected dubious ownership in repository\n")
    monkeypatch.setattr(git_gate, "_git", lambda clone, *args: failed)
    plan = git_gate.plan_hooks(clone)
    assert plan is not None and plan.action == "unknown" and "dubious ownership" in plan.current


@needs_git
def test_enable_does_not_write_to_a_clone_it_cannot_read(tmp_path, monkeypatch):
    clone = make_clone(tmp_path / "clone")
    monkeypatch.setattr(git_gate, "find_gitleaks", lambda: Path("gitleaks"))
    lines, ok = git_gate.enable([git_gate.HooksPlan(clone, "unknown", "TimeoutExpired")])
    assert not ok
    unset = subprocess.run(["git", "-C", str(clone), "config", "--local", "--get", "core.hooksPath"],
                           capture_output=True, text=True)
    assert unset.stdout.strip() == ""


@needs_git
def test_main_fails_when_the_only_clone_cannot_be_read(tmp_path, monkeypatch, capsys):
    clone = make_clone(tmp_path / "clone")
    monkeypatch.setattr(git_gate, "PROFILE_ROOT", clone)
    monkeypatch.setattr(git_gate, "_git", timing_out)
    monkeypatch.setattr(git_gate, "find_gitleaks", lambda: Path("gitleaks"))
    monkeypatch.setattr(git_gate, "ensure_pytest", lambda check_only=False: ("pytest: available", True))
    assert git_gate.main(["--check", "--tools-root", ""]) == 1
    out = capsys.readouterr().out
    assert "nothing to do" not in out and "GIT GATE UNKNOWN" in out and "git gate: NOT ON" in out


@needs_git
def test_git_missing_is_unknown_and_not_nothing_to_do(tmp_path, monkeypatch, capsys):
    # CP#87 review, R3: with git off PATH a clone that ships the gate used to be dropped, and main exited 0.
    clone = make_clone(tmp_path / "clone")
    copy = make_clone(tmp_path / "copy", init=False)
    real_which = shutil.which
    monkeypatch.setattr(git_gate.shutil, "which",
                        lambda name, *args, **kwargs: None if name == "git" else real_which(name, *args, **kwargs))
    plan = git_gate.plan_hooks(clone)
    assert plan is not None and plan.action == "unknown" and "not on PATH" in plan.current
    assert git_gate.plan_hooks(copy) is None  # control: a plain copy of the files is still not a clone
    monkeypatch.setattr(git_gate, "PROFILE_ROOT", clone)
    monkeypatch.setattr(git_gate, "find_gitleaks", lambda: Path("gitleaks"))
    monkeypatch.setattr(git_gate, "ensure_pytest", lambda check_only=False: ("pytest: available", True))
    assert git_gate.main(["--check", "--tools-root", ""]) == 1
    out = capsys.readouterr().out
    assert "nothing to do" not in out and "GIT GATE UNKNOWN" in out and "git gate: NOT ON" in out


# --- SA09-3: gitleaks is verified before it is installed -----------------------------------------------------------

@pytest.fixture()
def release(tmp_path, monkeypatch):
    """A local "release" folder holding one asset whose pinned checksum matches; returns the install destination."""
    data = b"archive bytes"
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "asset.zip").write_bytes(data)
    monkeypatch.setenv(git_gate.BASE_URL_ENV, str(mirror))
    monkeypatch.setattr(git_gate, "ASSETS", {("test", "x64"): ("asset.zip", hashlib.sha256(data).hexdigest())})
    monkeypatch.setattr(git_gate, "_extract", lambda asset, data: b"not a program")
    return tmp_path / "bin" / git_gate.exe_name()


def answers(monkeypatch, stdout: str) -> list:
    """Make the version probe print `stdout`; returns the list of programs that were probed."""
    probed = []

    def fake_run(argv, **kwargs):
        probed.append(Path(argv[0]))
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(git_gate.subprocess, "run", fake_run)
    return probed


def leftovers(dest: Path) -> list:
    return [p.name for p in dest.parent.iterdir() if p.name != dest.name] if dest.parent.is_dir() else []


def test_a_binary_that_does_not_run_is_not_installed(release):
    with pytest.raises(git_gate.GateError, match="does not run"):
        git_gate.provision(release, ("test", "x64"))
    assert not release.exists() and leftovers(release) == []


def test_a_binary_of_the_wrong_version_is_not_installed(release, monkeypatch):
    answers(monkeypatch, "8.0.0")
    with pytest.raises(git_gate.GateError, match="8.0.0"):
        git_gate.provision(release, ("test", "x64"))
    assert not release.exists() and leftovers(release) == []


def test_a_failed_verification_keeps_the_gitleaks_already_installed(release, monkeypatch):
    release.parent.mkdir(parents=True)
    release.write_bytes(b"the old one")
    answers(monkeypatch, "8.0.0")
    with pytest.raises(git_gate.GateError):
        git_gate.provision(release, ("test", "x64"))
    assert release.read_bytes() == b"the old one" and leftovers(release) == []


def test_a_verified_binary_is_installed(release, monkeypatch):
    probed = answers(monkeypatch, f"{git_gate.GITLEAKS_VERSION}\n")
    assert git_gate.provision(release, ("test", "x64")) == release
    assert release.read_bytes() == b"not a program" and leftovers(release) == []
    assert probed and probed[0] != release  # the probe ran on the download, before it took the final name


def test_a_checksum_mismatch_installs_nothing(release, monkeypatch):
    monkeypatch.setattr(git_gate, "ASSETS", {("test", "x64"): ("asset.zip", "0" * 64)})
    with pytest.raises(git_gate.GateError, match="failed its checksum"):
        git_gate.provision(release, ("test", "x64"))
    assert not release.exists() and leftovers(release) == []


# --- SA09-6: an unreadable machine config --------------------------------------------------------------------------

def test_an_unreadable_machine_config_fails_instead_of_dropping_the_tools_clone(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "profile-machine.json"
    bad.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv(machine_config.CONFIG_ENV, str(bad))
    with pytest.raises(git_gate.GateError, match="machine config"):
        git_gate.default_clones()
    assert git_gate.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "machine config" in out and "git gate: NOT ON" in out
    assert git_gate.default_clones("") == [git_gate.PROFILE_ROOT]  # an explicit root never reads the config
