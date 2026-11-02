#!/usr/bin/env python3
"""git_gate.py - turn on the local CI gate (the versioned git hooks) in this clone and the claude-tools clone, 2026-11-02.

The gate is two hooks in each repo's .githooks/, activated by core.hooksPath = .githooks:
  pre-push    runs the repo's CI gate (tools/gate.py: the tests and scanners CI runs) on every pushed commit, so
              code that would fail CI does not leave the machine;
  pre-commit  gitleaks scan of the staged diff. In this profile it BLOCKS when gitleaks is missing (CP-4).

Because of that pre-commit rule, turning the hooks on where gitleaks is missing would stop every commit in the
clone. So gitleaks comes first: when none is found (GITLEAKS_BIN, PATH, ~/.claude/bin), the pinned release is
downloaded from GitHub, checked against the SHA-256 pinned below, and installed as ~/.claude/bin/gitleaks[.exe] -
the place both pre-commit hooks and the claude-tools gate look. Only then is core.hooksPath set. When gitleaks
cannot be provisioned (offline, unsupported platform, checksum mismatch), the hooks are NOT turned on and the run
fails loudly; commits keep working, and pushes from Claude sessions are refused by hooks/ship_guard.py until this
succeeds.

The pre-push gate runs pytest with the Python the hook finds (python3, python, then py -3, the first that is 3.9+).
When that Python has no pytest, every push would be refused, so the pinned pytest (the version CI installs) is
installed into it with pip, and the gate counts as off until it is.

bootstrap.py calls this as its step 6. On its own (the per-machine enable step after a gate change):
  python tools/git_gate.py            provision gitleaks if needed, then set core.hooksPath in both clones
  python tools/git_gate.py --check    report only, change nothing
Exit 0 only when the gate is on in every clone that ships it.

CLAUDE_PROFILE_GITLEAKS_BASE_URL replaces the GitHub release URL (a mirror; tests point it at a local folder).
The pinned checksums apply whatever the source.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TOOLS_DIR = Path(__file__).resolve().parent
PROFILE_ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
import machine_config  # noqa: E402

HOOKS_DIR = ".githooks"
GITLEAKS_VERSION = "8.18.4"
BASE_URL_ENV = "CLAUDE_PROFILE_GITLEAKS_BASE_URL"
DEFAULT_BASE_URL = f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}"
# From gitleaks_8.18.4_checksums.txt of the release (the CI workflows install the same version).
ASSETS = {
    ("windows", "x64"): ("gitleaks_8.18.4_windows_x64.zip",
                         "9ba442ca7dda19885a2e569f43a127289feeb2b5fb0dfa251dafd277f4a0ba91"),
    ("linux", "x64"): ("gitleaks_8.18.4_linux_x64.tar.gz",
                       "ba6dbb656933921c775ee5a2d1c13a91046e7952e9d919f9bac4cec61d628e7d"),
    ("linux", "arm64"): ("gitleaks_8.18.4_linux_arm64.tar.gz",
                         "bf5f7f466ebfade1296c8bd32cf7d3f592c2aa78836aa9980ffbe2cadca7a861"),
    ("darwin", "x64"): ("gitleaks_8.18.4_darwin_x64.tar.gz",
                        "1a69e5666b13cd374889cbcb1939ed1573b63b551251283d5d2329a53cf58e2f"),
    ("darwin", "arm64"): ("gitleaks_8.18.4_darwin_arm64.tar.gz",
                          "a480d8593acd8215b22402cf0f3f88b01dcd3610c63b5391db640f7767e62104"),
}
DOWNLOAD_TIMEOUT = 60
PYTEST_PIN = "pytest==8.3.3"  # what .github/workflows/install-tests.yml installs and tools/gate.py names
PIP_TIMEOUT = 300


class GateError(Exception):
    """The gate cannot be turned on; the message says why and what to do."""


def exe_name() -> str:
    return "gitleaks.exe" if os.name == "nt" else "gitleaks"


def installed_path() -> Path:
    """Where this module installs gitleaks; the pre-commit hooks and the claude-tools gate look here too."""
    return machine_config.claude_dir() / "bin" / exe_name()


def find_gitleaks() -> Optional[Path]:
    """The gitleaks the hooks will run, in their order: GITLEAKS_BIN, PATH, ~/.claude/bin. None when absent."""
    env = os.environ.get("GITLEAKS_BIN")
    if env:
        return Path(env) if Path(env).is_file() else None
    on_path = shutil.which("gitleaks")
    if on_path:
        return Path(on_path)
    installed = installed_path()
    return installed if installed.is_file() else None


def platform_key(system: Optional[str] = None, machine: Optional[str] = None) -> tuple:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return ("windows" if system.startswith("win") else system, arch)


def _fetch(url: str) -> bytes:
    if "://" not in url:  # a plain folder (tests, offline mirror)
        return Path(url).read_bytes()
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp:  # noqa: S310 - https or file only
        return resp.read()


def _extract(asset: str, data: bytes) -> bytes:
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return zf.read("gitleaks.exe")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        member = tf.extractfile("gitleaks")
        if member is None:
            raise KeyError("gitleaks")
        return member.read()


def provision(dest: Optional[Path] = None, key: Optional[tuple] = None) -> Path:
    """Download the pinned gitleaks, verify it, install it at `dest`. Raises GateError on any failure."""
    dest = dest or installed_path()
    key = key or platform_key()
    if key not in ASSETS:
        raise GateError(f"no pinned gitleaks {GITLEAKS_VERSION} build for {key[0]}/{key[1]}; install gitleaks "
                        "8.18.x yourself and put it on PATH or in GITLEAKS_BIN, then run this again.")
    asset, sha = ASSETS[key]
    base = os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
    url = base.rstrip("/") + "/" + asset
    try:
        data = _fetch(url)
    except Exception as exc:  # noqa: BLE001 - network, file and HTTP errors all mean "not provisioned"
        raise GateError(f"could not download {url} ({type(exc).__name__}: {exc}); run this again when the "
                        "machine is online.") from exc
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        raise GateError(f"{asset} failed its checksum (expected {sha}, got {got}); nothing was installed.")
    try:
        binary = _extract(asset, data)
    except (KeyError, OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise GateError(f"{asset} does not contain the gitleaks binary ({exc}).") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".gitleaks-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(binary)
        os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(tmp, dest)
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        raise GateError(f"could not install gitleaks at {dest} ({exc}).") from exc
    try:
        version = subprocess.run([str(dest), "version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError(f"installed {dest}, but it does not run ({exc}).") from exc
    if GITLEAKS_VERSION not in version:
        raise GateError(f"installed {dest}, but `gitleaks version` printed {version!r}, not {GITLEAKS_VERSION}.")
    return dest


def ensure_gitleaks() -> tuple:
    """(path, what happened). Raises GateError when gitleaks is absent and cannot be provisioned."""
    found = find_gitleaks()
    if found:
        return found, f"gitleaks: {found}"
    if os.environ.get("GITLEAKS_BIN"):
        raise GateError(f"GITLEAKS_BIN is set to {os.environ['GITLEAKS_BIN']}, which is not a file. Fix or unset it.")
    path = provision()
    return path, f"gitleaks {GITLEAKS_VERSION}: installed {path} (checksum verified)"


def hook_python() -> Optional[list]:
    """The Python the pre-push hook runs the gate with, probed in the hook's order; None when there is none."""
    for name, extra in (("python3", []), ("python", []), ("py", ["-3"])):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            probe = subprocess.run([exe, *extra, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"],
                                   capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return [exe, *extra]
    return None


def ensure_pytest(check_only: bool = False, python: Optional[list] = None) -> tuple:
    """(report line, ok): ok when the hook's Python has pytest, installing the pinned one unless `check_only`."""
    python = python or hook_python()
    if not python:
        return "pytest: NO PYTHON 3.9+ for the pre-push hook (python3, python, py -3); every push is refused.", False
    shown = " ".join(python)

    def has_pytest() -> bool:
        try:
            return subprocess.run([*python, "-c", "import pytest"], capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    if has_pytest():
        return f"pytest: available to {shown}", True
    fix = f"{shown} -m pip install {PYTEST_PIN}"
    if check_only:
        return f"pytest: NOT INSTALLED for {shown}, so the pre-push gate refuses every push. Fix: {fix}", False
    try:
        pip = subprocess.run([*python, "-m", "pip", "install", "--disable-pip-version-check", PYTEST_PIN],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=PIP_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"pytest: could not run pip for {shown} ({exc}). Fix: {fix}", False
    if pip.returncode != 0 or not has_pytest():
        tail = (pip.stderr or pip.stdout).strip().splitlines()[-1:] or [""]
        return f"pytest: pip could not install {PYTEST_PIN} for {shown} ({tail[0]}). Fix: {fix}", False
    return f"pytest: installed {PYTEST_PIN} for {shown}", True


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)


def _same(a: Path, b: Path) -> bool:
    norm = lambda p: os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")  # noqa: E731
    return norm(a) == norm(b) or norm(a.resolve()) == norm(b.resolve())


@dataclass
class HooksPlan:
    """core.hooksPath in one clone: "set" (unset now), "already" (.githooks) or "foreign" (left alone)."""
    clone: Path
    action: str
    current: str


def plan_hooks(clone: Path) -> Optional[HooksPlan]:
    """None when `clone` is not the top of a git work tree that ships .githooks/pre-push, or git is missing."""
    if not (clone / HOOKS_DIR / "pre-push").is_file() or shutil.which("git") is None:
        return None
    try:
        top = _git(clone, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or not _same(Path(top.stdout.strip()), clone):
            return None
        current = _git(clone, "config", "--local", "--get", "core.hooksPath").stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not current:
        return HooksPlan(clone, "set", "")
    resolved = Path(current) if Path(current).is_absolute() else clone / current
    return HooksPlan(clone, "already" if _same(resolved, clone / HOOKS_DIR) else "foreign", current)


def default_clones(tools_root: Optional[str] = None) -> list:
    if tools_root is None:
        try:
            tools_root = machine_config.load(PROFILE_ROOT).get("tools_root") or ""
        except machine_config.ConfigError:
            tools_root = ""
    return [PROFILE_ROOT] + ([Path(tools_root)] if tools_root else [])


def describe_plan(plan: HooksPlan) -> str:
    return f"{plan.clone}: core.hooksPath " + {"set": f"-> {HOOKS_DIR}", "already": "already correct",
                                               "foreign": f"= {plan.current} (left alone)"}[plan.action]


def enable(plans: list, check_only: bool = False) -> tuple:
    """Turn the gate on for `plans`. Returns (report lines, ok): ok is True only when it is on in every clone."""
    lines, ok = [], True
    pending = [p for p in plans if p.action == "set"]
    gitleaks = None
    try:
        if check_only:
            found = find_gitleaks()
            lines.append(f"gitleaks: {found}" if found else "gitleaks: NOT FOUND (GITLEAKS_BIN, PATH, ~/.claude/bin)")
            gitleaks = found
        elif pending or not find_gitleaks():
            gitleaks, line = ensure_gitleaks()
            lines.append(line)
        else:
            gitleaks = find_gitleaks()
            lines.append(f"gitleaks: {gitleaks}")
    except GateError as exc:
        lines.append(f"gitleaks: NOT AVAILABLE - {exc}")
    for plan in plans:
        if plan.action == "already":
            lines.append(f"git gate on in {plan.clone} (core.hooksPath = {plan.current})")
        elif plan.action == "foreign":
            ok = False
            lines.append(f"GIT GATE OFF in {plan.clone}: core.hooksPath = {plan.current} was set by someone else and "
                         f"is left alone. To turn the gate on: git -C \"{plan.clone}\" config core.hooksPath {HOOKS_DIR}")
        elif check_only:
            ok = False
            lines.append(f"GIT GATE OFF in {plan.clone}: core.hooksPath is not set.")
        elif gitleaks is None:
            ok = False
            lines.append(f"GIT GATE OFF in {plan.clone}: not turned on, because its pre-commit hook would block every "
                         "commit without gitleaks. Fix the gitleaks line above, then run: python "
                         f"\"{PROFILE_ROOT / 'tools' / 'git_gate.py'}\"")
        else:
            proc = _git(plan.clone, "config", "--local", "core.hooksPath", HOOKS_DIR)
            if proc.returncode != 0:
                ok = False
                lines.append(f"GIT GATE OFF in {plan.clone}: git config failed: {proc.stderr.strip()}")
            else:
                lines.append(f"Turned on the git gate in {plan.clone} (core.hooksPath = {HOOKS_DIR})")
    if gitleaks is None and any(p.action == "already" for p in plans):
        ok = False
        lines.append("WARNING: gitleaks is missing, so the profile's pre-commit hook blocks every commit. Fix the "
                     "gitleaks line above.")
    return lines, ok


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    ap.add_argument("--tools-root", help="the claude-tools clone (default: from the machine config)")
    opts = ap.parse_args(argv)
    plans = [p for p in map(plan_hooks, default_clones(opts.tools_root)) if p is not None]
    if not plans:
        print("git gate: no clone here ships .githooks/pre-push; nothing to do.")
        return 0
    lines, ok = enable(plans, check_only=opts.check)
    pytest_line, pytest_ok = ensure_pytest(check_only=opts.check)
    lines.append(pytest_line)
    ok = ok and pytest_ok
    for line in lines:
        print(line)
    print("git gate: ON" if ok else "git gate: NOT ON - see the lines above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
