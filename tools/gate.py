#!/usr/bin/env python3
"""The local gate for this repository: run it before a push; push only when it says GATE PASS.

It runs, in this order, from the repository root:

  1. pytest over hooks/tests and tools/tests. CI's install-tests workflow runs only part of hooks/tests (the installer and
     the vendored pins) and all of tools/tests; this gate runs all of both, because the rest of hooks/tests (the closure
     audit, the path gate, the decay and northstar hooks) is exactly what CI does not cover, and a break there ships.
  2. the machine-paths guard's self-test (what .github/workflows/machine-paths.yml runs first);
  3. the machine-paths guard's scan of this repository (what that workflow runs second).

Every step runs even when an earlier one fails, so one run shows every problem. The last line of output is the verdict
(`GATE PASS ...` or `GATE FAIL ...`), and the exit status is 0 only for a pass.

Python, because every other tool here is (machine_config.py, context_weight.py, profile_setup.py), the guard it calls is, and
CI runs on Linux as well as Windows: a .ps1 would not run on the Linux runners or on a Linux machine of this profile.
Standard library only, apart from pytest, which CI pins as pytest==8.3.3.

Usage:
  python tools/gate.py              # the whole gate, from anywhere inside the repository
  python tools/gate.py --root DIR   # another checkout of this repository
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, TextIO

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
PYTEST_PIN = "pytest==8.3.3"  # the version .github/workflows/install-tests.yml installs


def default_steps(root: Path, python: str = sys.executable) -> list[tuple[str, list[str]]]:
    guard = ".github/scripts/check_machine_paths.py"
    return [
        ("pytest (hooks/tests, tools/tests)",
         [python, "-m", "pytest", "hooks/tests", "tools/tests", "-q", "-p", "no:cacheprovider"]),
        ("machine-paths guard self-test", [python, guard, "--self-test"]),
        ("machine-paths guard scan", [python, guard, "--root", str(root)]),
    ]


def preflight(root: Path, have_pytest: bool | None = None, python: str = sys.executable) -> list[tuple[str, list[str]]]:
    """A failing step, with the fix in its output, when pytest is not installed; nothing otherwise."""
    if have_pytest is None:
        have_pytest = importlib.util.find_spec("pytest") is not None
    if have_pytest:
        return []
    message = (f"pytest is not installed for {python}: run `{python} -m pip install {PYTEST_PIN}` "
               "(the version CI uses), then run the gate again.")
    return [("pytest is installed", [python, "-c", f"import sys; print({message!r}); sys.exit(1)"])]


def run_step(label: str, argv: list[str], cwd: Path, out: TextIO) -> bool:
    """Run one step, streaming its output; True when it exited 0. A step that cannot start is a failure, not a crash."""
    print(f"==> {label}", file=out, flush=True)
    started = time.monotonic()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"    could not start: {exc}", file=out, flush=True)
        print(f"<== {label}: FAILED (could not start)", file=out, flush=True)
        return False
    assert proc.stdout is not None
    for line in proc.stdout:
        out.write(line if line.endswith("\n") else line + "\n")
        out.flush()
    code = proc.wait()
    took = time.monotonic() - started
    print(f"<== {label}: {'ok' if code == 0 else f'FAILED (exit {code})'} in {took:.1f}s", file=out, flush=True)
    return code == 0


def run_steps(steps: Iterable[tuple[str, list[str]]], cwd: Path, out: TextIO,
              runner: Callable[[str, list[str], Path, TextIO], bool] = run_step) -> tuple[list[str], int]:
    """(labels of the failed steps, number of steps run). Every step runs."""
    failed, total = [], 0
    for label, argv in steps:
        total += 1
        if not runner(label, argv, cwd, out):
            failed.append(label)
    return failed, total


def main(argv: list[str] | None = None, steps: list[tuple[str, list[str]]] | None = None,
         out: TextIO | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository root (default: this clone)")
    args = ap.parse_args(argv)
    out = out or sys.stdout
    for stream in (sys.stdout, sys.stderr):  # step output is relayed as text; never die on a console codec
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    root = args.root.resolve()
    if steps is None:
        steps = default_steps(root)
        missing = preflight(root)
        if missing:  # say how to fix it instead of letting pytest's own "No module named pytest" stand in for a verdict
            steps = missing + [s for s in steps if "pytest" not in s[0]]
    failed, total = run_steps(steps, root, out)
    if total == 0:
        print("GATE FAIL: no steps ran, so nothing was checked", file=out, flush=True)
        return 1
    if failed:
        print(f"GATE FAIL: {len(failed)} of {total} steps failed: {', '.join(failed)}", file=out, flush=True)
        return 1
    print(f"GATE PASS: {total} of {total} steps passed", file=out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
