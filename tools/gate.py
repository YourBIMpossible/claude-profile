#!/usr/bin/env python3
"""The local gate for this repository: the pre-push hook runs it, CI runs it, and a push goes out only on GATE PASS.

It runs, from the repository root:

  1. pytest over hooks/tests, over tools/tests and over skills/graphify/scripts. CI's install-tests workflow (manual
     dispatch only, the clean-runner audit) runs this gate on Linux and on Windows, so the gate is the one list of
     what CI checks; a test added under those folders is checked on the pushing machine and in CI with no second list
     to update. The few tests that drive a tool from
     the claude-tools clone (NEEDS_CLAUDE_TOOLS) are left out, with a line saying so, where there is no such clone.
  2. the machine-paths guard's self-test (what .github/workflows/machine-paths.yml runs first);
  3. the machine-paths guard's scan of this repository (what that workflow runs second).

The steps run in parallel, each with its own temporary directory (so two pytest runs never share a basetemp), and
their output is buffered: a passing step prints one line, a failing step prints all of its output (`--verbose` prints
every step's output). Every step runs even when another fails, so one run shows every problem. The last line of
output is the verdict (`GATE PASS ...` or `GATE FAIL ...`), and the exit status is 0 only for a pass.

Python, because every other tool here is (machine_config.py, context_weight.py, profile_setup.py), the guard it calls is, and
CI runs on Linux as well as Windows: a .ps1 would not run on the Linux runners or on a Linux machine of this profile.
Standard library only, apart from pytest, which CI pins as pytest==8.3.3.

Usage:
  python tools/gate.py              # the whole gate, from anywhere inside the repository
  python tools/gate.py --verbose    # the same, printing every step's output (what CI runs)
  python tools/gate.py --root DIR   # another checkout of this repository
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
PYTEST_PIN = "pytest==8.3.3"  # the version .github/workflows/install-tests.yml installs
PYTEST_DIRS = ("hooks/tests", "tools/tests", "skills/graphify/scripts")
# Tests that drive a tool from the claude-tools clone, each with the variable that is its own override of that tool's
# location. They fail hard when the tool is missing (by design: never a silent skip), so the gate leaves them out, and
# says so, only where this machine has no claude-tools clone and no override: CI, or a machine that never cloned it.
NEEDS_CLAUDE_TOOLS = {
    "hooks/tests/test_ctxdex_hook.py": "CTXDEX_DIR",
    "hooks/tests/test_ctxdex_hook_isolation.py": "CTXDEX_DIR",
    "hooks/tests/test_evidence_evacuate.py": "EVIDENCE_TEST_EVACUATOR",
}


@dataclass
class Result:
    label: str
    ok: bool
    output: str
    seconds: float
    status: str


def claude_tools_root(root: Path) -> str:
    """This machine's claude-tools clone, as the tests themselves resolve it (tools/machine_config.py); "" for none."""
    spec = importlib.util.spec_from_file_location("gate_machine_config", root / "tools" / "machine_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load(root).get("tools_root") or ""


def not_runnable_here(tools_root: str, environ: dict | None = None) -> list[str]:
    """The NEEDS_CLAUDE_TOOLS tests this machine cannot run: no claude-tools clone and no override of the location."""
    environ = os.environ if environ is None else environ
    if tools_root:
        return []
    return [test for test, override in NEEDS_CLAUDE_TOOLS.items() if not environ.get(override)]


def default_steps(root: Path, python: str = sys.executable,
                  skip: list[str] | tuple[str, ...] = ()) -> list[tuple[str, list[str]]]:
    guard = ".github/scripts/check_machine_paths.py"
    steps = [(f"pytest {d}", [python, "-m", "pytest", d, "-q", "-p", "no:cacheprovider",
                              *(f"--ignore={t}" for t in skip if t.startswith(d + "/"))])
             for d in PYTEST_DIRS]
    return steps + [
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


def run_step(label: str, argv: list[str], cwd: Path, scratch: Path) -> Result:
    """Run one step to completion with its output captured. A step that cannot start is a failure, not a crash."""
    started = time.monotonic()
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch))
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        return Result(label, False, f"could not start: {exc}\n", time.monotonic() - started, "FAILED (could not start)")
    status = "ok" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
    return Result(label, proc.returncode == 0, proc.stdout, time.monotonic() - started, status)


def run_steps(steps: list[tuple[str, list[str]]], cwd: Path, out: TextIO, verbose: bool = False,
              runner: Callable[[str, list[str], Path, Path], Result] = run_step) -> tuple[list[str], int]:
    """(labels of the failed steps, number of steps run). Every step runs; results print in step order."""
    if not steps:
        return [], 0
    with tempfile.TemporaryDirectory(prefix="gate-") as scratch_root, \
            ThreadPoolExecutor(max_workers=min(8, len(steps), os.cpu_count() or 1)) as pool:
        futures = [pool.submit(runner, label, argv, cwd, Path(scratch_root) / str(i))
                   for i, (label, argv) in enumerate(steps)]
        failed = []
        for future in futures:
            result = future.result()
            if verbose or not result.ok:
                print(f"==> {result.label}", file=out)
                text = result.output
                out.write(text if not text or text.endswith("\n") else text + "\n")
            print(f"<== {result.label}: {result.status} in {result.seconds:.1f}s", file=out, flush=True)
            if not result.ok:
                failed.append(result.label)
    return failed, len(steps)


def main(argv: list[str] | None = None, steps: list[tuple[str, list[str]]] | None = None,
         out: TextIO | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository root (default: this clone)")
    ap.add_argument("--verbose", action="store_true", help="print every step's output, not only a failing step's")
    args = ap.parse_args(argv)
    out = out or sys.stdout
    for stream in (sys.stdout, sys.stderr):  # step output is relayed as text; never die on a console codec
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    root = args.root.resolve()
    if steps is None:
        skip = not_runnable_here(claude_tools_root(root))
        for test in skip:
            print(f"not run here (no claude-tools clone, {NEEDS_CLAUDE_TOOLS[test]} unset): {test}", file=out)
        steps = default_steps(root, skip=skip)
        missing = preflight(root)
        if missing:  # say how to fix it instead of letting pytest's own "No module named pytest" stand in for a verdict
            steps = missing + [s for s in steps if "pytest" not in s[0]]
    failed, total = run_steps(steps, root, out, verbose=args.verbose)
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
