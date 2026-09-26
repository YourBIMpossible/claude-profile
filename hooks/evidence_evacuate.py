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
  $CLAUDE_PROJECT_DIR and the payload cwd (deduplicated).
- Pre-check: list the store; call the evacuator only when some filename's
  packet id is missing from <archive>/packets/. A turn that wrote no packet
  costs one directory listing and writes nothing.
- Always exit 0 with empty stdout. A Stop hook exiting 2 would block the stop.
- Log only actions and failures, to evidence_evacuate.log beside this script.

Sweep mode (backstop for worktrees removed without a session ending in them):
  python evidence_evacuate.py --sweep <repo-root> [<repo-root> ...]
checks each root and every <root>/.claude/worktrees/* checkout.

Opt-out: EVIDENCE_EVACUATE=0.
Overrides: EVIDENCE_LIVE_ARCHIVE, EVIDENCE_EVACUATOR, EVIDENCE_EVACUATE_LOG.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ARCHIVE = "F:/Evidence-Archive/EC-LIVE"
DEFAULT_EVACUATOR = "F:/Claude-Tools/evidence-archive/evacuate_worktree.py"
PACKETS_REL = Path(".evidence-compiler") / "packets"
PACKET_ID_IN_NAME = re.compile(r"(ep_[0-9a-f]{8,64})\.json$")
EVACUATOR_TIMEOUT_S = 25
LOG_PATH = Path(os.environ.get("EVIDENCE_EVACUATE_LOG")
                or Path(__file__).with_name("evidence_evacuate.log"))


def log(msg: str) -> None:
    try:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {msg}\n")
    except OSError:
        pass


def missing_ids(checkout: Path, archive: Path) -> list[str]:
    """Packet ids present in the checkout's store but absent from the archive.

    A filename without a recognisable id counts as missing until a copy of it
    sits in the archive's quarantine, so the evacuator sees it once instead of
    this pre-check silently skipping it or re-running every turn.
    """
    store = checkout / PACKETS_REL
    if not store.is_dir():
        return []
    archived = archive / "packets"
    quarantine = archive / "quarantine"
    quarantined: set[str] | None = None
    missing: list[str] = []
    for entry in os.scandir(store):
        if not entry.name.endswith(".json"):
            continue
        m = PACKET_ID_IN_NAME.search(entry.name)
        if m is not None:
            if not (archived / f"{m.group(1)}.json").exists():
                missing.append(m.group(1))
            continue
        if quarantined is None:
            quarantined = ({q.name.split("__", 1)[-1] for q in quarantine.rglob("*__*")}
                           if quarantine.is_dir() else set())
        if entry.name not in quarantined:
            missing.append(entry.name)
    return missing


def evacuate(checkout: Path, archive: Path, evacuator: Path, why: str) -> None:
    missing = missing_ids(checkout, archive)
    if not missing:
        return
    if not evacuator.is_file():
        log(f"ERROR evacuator not found: {evacuator} ({len(missing)} packet(s) unarchived in {checkout})")
        return
    archive.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(evacuator), "evacuate", str(checkout),
           "--archive", str(archive), "--apply"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=EVACUATOR_TIMEOUT_S,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        log(f"ERROR evacuator timed out after {EVACUATOR_TIMEOUT_S}s on {checkout}")
        return
    tail = (proc.stdout.strip().splitlines() or [""])[-1][:300]
    level = "OK" if proc.returncode == 0 else f"EXIT{proc.returncode}"
    log(f"{level} {why} {checkout} new={len(missing)} :: {tail}")
    if proc.returncode != 0 and proc.stderr.strip():
        log(f"  stderr: {proc.stderr.strip()[:500]}")


def checkouts_for_payload(payload: dict) -> list[Path]:
    seen: list[Path] = []
    for raw in (os.environ.get("CLAUDE_PROJECT_DIR"), payload.get("cwd")):
        if not raw:
            continue
        p = Path(raw).resolve()
        if p not in seen:
            seen.append(p)
    return seen


def checkouts_for_sweep(roots: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in roots:
        root = Path(raw).resolve()
        found.append(root)
        wt = root / ".claude" / "worktrees"
        if wt.is_dir():
            found.extend(sorted(p for p in wt.iterdir() if p.is_dir()))
    return found


def main(argv: list[str]) -> None:
    if os.environ.get("EVIDENCE_EVACUATE", "1") == "0":
        return
    archive = Path(os.environ.get("EVIDENCE_LIVE_ARCHIVE", DEFAULT_ARCHIVE))
    evacuator = Path(os.environ.get("EVIDENCE_EVACUATOR", DEFAULT_EVACUATOR))
    if argv[:1] == ["--sweep"]:
        for checkout in checkouts_for_sweep(argv[1:]):
            evacuate(checkout, archive, evacuator, "sweep")
        return
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = str(payload.get("hook_event_name") or "hook")
    for checkout in checkouts_for_payload(payload):
        evacuate(checkout, archive, evacuator, event)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:  # fail-open: never block a stop or session end
        log(f"ERROR unhandled {type(exc).__name__}: {exc}")
    sys.exit(0)
