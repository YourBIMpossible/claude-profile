#!/usr/bin/env python3
"""Deterministic finishing steps for the portable next-workspace-sync-weekly skill.

Runs, against a given state dir:
  1. queue_store.py --state-dir DIR          (validate both tiers; exit 0 required)
  2. render_queue.py --state-dir DIR --generated-on DATE           (writes QUEUE.md)
  3. render_queue.py --state-dir DIR --generated-on DATE --check   (byte-compare; exit 0 required)
then reports the watermark delta (`reconciled:` block) between a baseline queue.yaml and the
current one. Never commits, pushes, or touches any mirror.

Refuses the canonical store unless --live. Canonical location: --canonical-state-dir, else env
NEXT_CANONICAL_STATE_DIR. A `.canonical` marker file in a state dir also marks it canonical.

Exit codes: 0 ok, 1 a step failed, 2 refused / bad arguments.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_REFUSED = 2


@dataclass
class StepResult:
    name: str
    argv: list[str]
    exit_code: int
    output_tail: str


@dataclass
class RunReport:
    state_dir: str
    generated_on: str
    live: bool
    steps: list[StepResult] = field(default_factory=list)
    watermark_delta: dict[str, Any] = field(default_factory=dict)
    sha256_after: dict[str, str] = field(default_factory=dict)
    ok: bool = False


def _norm(p: Path) -> str:
    return os.path.normcase(str(p.resolve()))


def is_canonical(state_dir: Path, canonical: str | None) -> bool:
    if (state_dir / ".canonical").exists():
        return True
    return bool(canonical) and _norm(state_dir) == _norm(Path(canonical))  # type: ignore[arg-type]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "<absent>"


def load_watermarks(queue_yaml: Path) -> dict[str, Any]:
    data = yaml.safe_load(queue_yaml.read_text(encoding="utf-8")) or {}
    marks = data.get("reconciled") or {}
    if not isinstance(marks, dict):
        raise ValueError(f"{queue_yaml}: 'reconciled' is not a mapping")
    return marks


def watermark_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        proj: {"before": before.get(proj), "after": after.get(proj)}
        for proj in sorted(set(before) | set(after))
        if before.get(proj) != after.get(proj)
    }


def run_step(name: str, argv: list[str]) -> StepResult:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-5:])
    return StepResult(name, argv, proc.returncode, tail)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", required=True, type=Path)
    ap.add_argument("--tools-dir", type=Path, help="dir holding queue_store.py/render_queue.py (default: state dir)")
    ap.add_argument("--generated-on", default=_dt.date.today().isoformat())
    ap.add_argument("--baseline", type=Path, help="pre-sync queue.yaml for the watermark delta (default: queue.yaml before steps)")
    ap.add_argument("--canonical-state-dir", default=os.environ.get("NEXT_CANONICAL_STATE_DIR"))
    ap.add_argument("--live", action="store_true", help="permit running against the canonical store")
    ap.add_argument("--json-out", type=Path)
    ns = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    state: Path = ns.state_dir
    queue = state / "queue.yaml"
    if not queue.is_file():
        print(f"REFUSED: {queue} not found", file=sys.stderr)
        return EXIT_REFUSED
    if is_canonical(state, ns.canonical_state_dir) and not ns.live:
        print(f"REFUSED: {state} is the canonical state dir; pass --live to run against it", file=sys.stderr)
        return EXIT_REFUSED
    try:
        _dt.date.fromisoformat(ns.generated_on)
    except ValueError:
        print(f"REFUSED: --generated-on {ns.generated_on!r} is not YYYY-MM-DD", file=sys.stderr)
        return EXIT_REFUSED

    tools: Path = ns.tools_dir or state
    try:
        before = load_watermarks(ns.baseline or queue)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"REFUSED: cannot read baseline watermarks: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    py, sd = sys.executable, str(state)
    report = RunReport(state_dir=sd, generated_on=ns.generated_on, live=ns.live)
    plan = [
        ("validate", [py, str(tools / "queue_store.py"), "--state-dir", sd]),
        ("render", [py, str(tools / "render_queue.py"), "--state-dir", sd, "--generated-on", ns.generated_on]),
        ("check", [py, str(tools / "render_queue.py"), "--state-dir", sd, "--generated-on", ns.generated_on, "--check"]),
    ]
    for name, cmd in plan:
        res = run_step(name, cmd)
        report.steps.append(res)
        last = res.output_tail.splitlines()[-1] if res.output_tail else ""
        print(f"[{name}] exit={res.exit_code} :: {last}")
        if res.exit_code != 0:
            break

    try:
        report.watermark_delta = watermark_delta(before, load_watermarks(queue))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"watermark read failed: {exc}", file=sys.stderr)
        report.watermark_delta = {"error": str(exc)}
    report.sha256_after = {n: sha256(state / n) for n in ("queue.yaml", "QUEUE.md", "queue-archive.yaml")}
    report.ok = len(report.steps) == len(plan) and all(s.exit_code == 0 for s in report.steps)

    print("watermark delta:", json.dumps(report.watermark_delta, default=str) if report.watermark_delta else "none")
    print("RESULT:", "OK" if report.ok else "FAILED")
    if ns.json_out:
        ns.json_out.write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    return EXIT_OK if report.ok else EXIT_STEP_FAILED


if __name__ == "__main__":
    sys.exit(main())
