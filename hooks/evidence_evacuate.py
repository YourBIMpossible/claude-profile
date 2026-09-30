#!/usr/bin/env python3
"""Evidence packet evacuation — Stop + SessionEnd hook (EC measurement plan S0.1).

Evidence Compiler writes one packet per prompt into the checkout's own
.evidence-compiler/packets/. Those stores die with their worktree and are
pruned at retention.max_packets (250). This hook copies every packet that the
live archive does not hold yet into EVIDENCE_LIVE_ARCHIVE, using the
Claude-Tools evacuator (sha256-verified, exclusive create, never deletes or
overwrites, one receipt per copying run).

Contract:
- Input: the hook JSON on stdin (cwd, hook_event_name). Checked stores are
  $CLAUDE_PROJECT_DIR and the payload cwd (deduplicated). On SessionEnd the
  hook also sweeps the owning repo root and its other .claude/worktrees/*
  checkouts (the backstop for a lane whose own session never ended cleanly).
- Pre-check: a packet counts as archived only when a byte-identical copy
  exists: <archive>/packets/<id>.json, or the evacuator's quarantine entry
  <archive>/quarantine/*/<sha12>__<filename>. A missing, partial or differing
  copy sends the checkout to the evacuator again. A turn that wrote no packet
  costs one listing plus one hash per packet and writes nothing.
- One shared time budget (TOTAL_BUDGET_S, under the 30 s hook timeout) covers
  every checkout, including the pre-check hashing; a checkout the budget
  cannot reach or finish checking is a deferred failure.
- A failure in one checkout is logged and the rest are still processed.
- Exit 0 always (a Stop hook exiting 2 would block the stop). stdout is empty
  on success; on any failure it carries {"systemMessage": ...} so the user
  sees the unarchived packets instead of only the log.
- Log actions and failures to evidence_evacuate.log beside this script.

Sweep mode (backstop for worktrees removed without a session ending in them):
  python evidence_evacuate.py --sweep <repo-root> [<repo-root> ...]
checks each root and every <root>/.claude/worktrees/* checkout; exits 1 and
prints the failures when any checkout could not be fully archived.

Opt-out: EVIDENCE_EVACUATE=0.
Overrides: EVIDENCE_LIVE_ARCHIVE, EVIDENCE_EVACUATOR, EVIDENCE_EVACUATE_LOG,
EVIDENCE_EVACUATE_BUDGET_S (seconds, default TOTAL_BUDGET_S).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

DEFAULT_ARCHIVE = "F:/Evidence-Archive/EC-LIVE"
DEFAULT_EVACUATOR = "F:/Claude-Tools/evidence-archive/evacuate_worktree.py"
PACKETS_REL = Path(".evidence-compiler") / "packets"
PACKET_ID_IN_NAME = re.compile(r"(ep_[0-9a-f]{8,64})\.json$")
TOTAL_BUDGET_S = 25.0      # hook timeout in settings is 30 s
MIN_CALL_S = 2.0           # below this an evacuator call is deferred, not started
LOG_PATH = Path(os.environ.get("EVIDENCE_EVACUATE_LOG")
                or Path(__file__).with_name("evidence_evacuate.log"))


def log(msg: str) -> None:
    try:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {msg}\n")
    except OSError:
        pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_copy(copy: Path, size: int, digest: str) -> bool:
    try:
        return copy.stat().st_size == size and _sha256(copy) == digest
    except FileNotFoundError:
        return False


class BudgetSpent(Exception):
    """The shared budget ran out before every store file was checked."""

    def __init__(self, found: int) -> None:
        super().__init__(found)
        self.found = found


def missing_ids(checkout: Path, archive: Path,
                remaining: Callable[[], float] | None = None) -> list[str]:
    """Store files with no byte-identical copy in the archive.

    A file named with a packet id is archived when packets/<id>.json holds the
    same bytes; anything else (malformed, id mismatch, no id) is archived when
    the evacuator's quarantine holds <sha12>__<filename> with the same bytes.
    A partial, empty or differing copy is not archived, so the evacuator runs
    again and reports it.

    With `remaining`, the budget is checked before each file is hashed and
    BudgetSpent is raised once it drops below MIN_CALL_S, so hashing a large
    store can never run the hook past its timeout.
    """
    store = checkout / PACKETS_REL
    if not store.is_dir():
        return []
    archived = archive / "packets"
    quarantine = archive / "quarantine"
    missing: list[str] = []
    for entry in os.scandir(store):
        if not entry.name.endswith(".json") or not entry.is_file():
            continue
        if remaining is not None and remaining() < MIN_CALL_S:
            raise BudgetSpent(len(missing))
        size = entry.stat().st_size
        digest = _sha256(Path(entry.path))
        m = PACKET_ID_IN_NAME.search(entry.name)
        if m is not None and _same_copy(archived / f"{m.group(1)}.json", size, digest):
            continue
        if quarantine.is_dir() and any(
                _same_copy(q, size, digest)
                for q in quarantine.glob(f"*/{digest[:12]}__{entry.name}")):
            continue
        missing.append(m.group(1) if m is not None else entry.name)
    return missing


class Run:
    """Shared deadline and failure list for one hook or sweep invocation."""

    def __init__(self, budget_s: float) -> None:
        self.deadline = time.monotonic() + budget_s
        self.failures: list[str] = []

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def fail(self, msg: str) -> None:
        log(f"ERROR {msg}")
        self.failures.append(msg)


def evacuate(checkout: Path, archive: Path, evacuator: Path, why: str, run: Run) -> None:
    try:
        missing = missing_ids(checkout, archive, run.remaining)
    except BudgetSpent as exc:
        run.fail(f"deferred: time budget spent while checking {checkout} "
                 f"({exc.found} packet(s) unarchived so far, rest unchecked)")
        return
    if not missing:
        return
    if not evacuator.is_file():
        run.fail(f"evacuator not found: {evacuator} ({len(missing)} packet(s) unarchived in {checkout})")
        return
    budget = run.remaining()
    if budget < MIN_CALL_S:
        run.fail(f"deferred: time budget spent before {checkout} ({len(missing)} packet(s) unarchived)")
        return
    archive.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(evacuator), "evacuate", str(checkout),
           "--archive", str(archive), "--apply"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=budget,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        run.fail(f"evacuator timed out after {budget:.1f}s on {checkout}")
        return
    tail = (proc.stdout.strip().splitlines() or [""])[-1][:300]
    if proc.returncode == 0:
        log(f"OK {why} {checkout} new={len(missing)} :: {tail}")
        return
    run.fail(f"evacuator exit {proc.returncode} ({why}) on {checkout} :: {tail}")
    if proc.stderr.strip():
        log(f"  stderr: {proc.stderr.strip()[:500]}")


def evacuate_all(checkouts: list[Path], archive: Path, evacuator: Path, why: str,
                 run: Run) -> None:
    for checkout in checkouts:
        try:
            evacuate(checkout, archive, evacuator, why, run)
        except Exception as exc:  # one bad checkout must not skip the others
            run.fail(f"{type(exc).__name__} on {checkout}: {exc}")


def checkouts_for_payload(payload: dict) -> list[Path]:
    seen: list[Path] = []
    for raw in (os.environ.get("CLAUDE_PROJECT_DIR"), payload.get("cwd")):
        if not raw:
            continue
        p = Path(raw).resolve()
        if p not in seen:
            seen.append(p)
    return seen


def repo_root(checkout: Path) -> Path:
    """The main checkout owning a .claude/worktrees/<lane> path, else checkout."""
    parts = checkout.parts
    for i in range(len(parts) - 2, 0, -1):
        if parts[i].lower() == "worktrees" and parts[i - 1].lower() == ".claude":
            return Path(*parts[:i - 1])
    return checkout


def checkouts_for_sweep(roots: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in roots:
        root = Path(raw).resolve()
        found.append(root)
        wt = root / ".claude" / "worktrees"
        if wt.is_dir():
            found.extend(sorted(p for p in wt.iterdir() if p.is_dir()))
    return found


def system_message(failures: list[str]) -> str:
    head = f"Evidence evacuation: {len(failures)} failure(s); packets may be unarchived."
    detail = "; ".join(failures[:3]) + (f"; +{len(failures) - 3} more" if len(failures) > 3 else "")
    return f"{head} {detail} (log: {LOG_PATH})"


def main(argv: list[str]) -> int:
    if os.environ.get("EVIDENCE_EVACUATE", "1") == "0":
        return 0
    archive = Path(os.environ.get("EVIDENCE_LIVE_ARCHIVE", DEFAULT_ARCHIVE))
    evacuator = Path(os.environ.get("EVIDENCE_EVACUATOR", DEFAULT_EVACUATOR))
    run = Run(float(os.environ.get("EVIDENCE_EVACUATE_BUDGET_S", TOTAL_BUDGET_S)))
    if argv[:1] == ["--sweep"]:
        evacuate_all(checkouts_for_sweep(argv[1:]), archive, evacuator, "sweep", run)
        for msg in run.failures:
            print(f"FAIL {msg}")
        return 1 if run.failures else 0
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = str(payload.get("hook_event_name") or "hook")
    checkouts = checkouts_for_payload(payload)
    if event == "SessionEnd":
        roots = sorted({str(repo_root(c)) for c in checkouts})
        checkouts += [c for c in checkouts_for_sweep(roots) if c not in checkouts]
    evacuate_all(checkouts, archive, evacuator, event, run)
    if run.failures:
        print(json.dumps({"systemMessage": system_message(run.failures)}))
    return 0


if __name__ == "__main__":
    sweep = sys.argv[1:2] == ["--sweep"]
    try:
        code = main(sys.argv[1:])
    except Exception as exc:  # fail-open: never block a stop or session end
        log(f"ERROR unhandled {type(exc).__name__}: {exc}")
        if sweep:
            print(f"FAIL unhandled {type(exc).__name__}: {exc}")
            sys.exit(1)
        print(json.dumps({"systemMessage":
                          f"Evidence evacuation hook crashed: {type(exc).__name__} (log: {LOG_PATH})"}))
        sys.exit(0)
    sys.exit(code if sweep else 0)
