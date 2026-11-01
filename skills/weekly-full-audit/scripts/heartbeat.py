"""Portable heartbeat writer for the audit watchdog.

Schema owner: BIMpossible-Workspace, system/scheduled-tasks/audit-watchdog
(Write-AuditHeartbeat.ps1 writes it, Watch-ScheduledAudits.ps1 reads it). A
schema change there is a change here. One JSON file per run:

    <watchdog-dir>/heartbeats/<task>/<run_id>.json
    {task, run_id, started_at, finished_at, outcome, ledgers, note}

started_at / finished_at are UTC ISO-8601 with a Z suffix; outcome is null
until finish, then ok|degraded|failed and describes the RUN, never the
findings (the watchdog judges findings from the ledgers via the GRRP gate).
Extra keys written here and ignored by the watchdog: task_id, remediation,
preliminary. UTF-8 without BOM.

<task> is the watchdog folder name. It defaults to the skill task_id; pass
--watchdog-task to write under a name the watchdog already monitors (fixture
parity tests only until the Phase 3 cutover).

Phases:
  start        create the file (fails if it exists)
  progress     PRELIMINARY note on an unfinished run (rule 6); never an outcome
  finish       set finished_at, outcome, ledgers, note
  remediation  record the remediation outcome on a finished run; outcome unchanged
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_lib import (  # noqa: E402
    REMEDIATION,
    AuditError,
    atomic_write_json,
    iso_utc,
    parse_run_id,
    read_json,
    utcnow,
)

OUTCOMES = ("ok", "degraded", "failed")
PRELIMINARY = "PRELIMINARY — not gated"


def default_watchdog_dir() -> Path:
    return Path.home() / ".claude" / "scheduled-tasks" / "_watchdog"


def heartbeat_path(watchdog_dir: Path, task: str, run_id: str) -> Path:
    if not task or any(c in task for c in "/\\:") or task in (".", ".."):
        raise AuditError(f"bad watchdog task name {task!r}")
    return watchdog_dir / "heartbeats" / task / f"{run_id}.json"


def start(watchdog_dir: Path, task_id: str, run_id: str, task: str | None = None) -> Path:
    parse_run_id(run_id)
    path = heartbeat_path(watchdog_dir, task or task_id, run_id)
    if path.exists():
        raise AuditError(f"heartbeat already started: {path}")
    hb: dict[str, Any] = {
        "task": task or task_id,
        "run_id": run_id,
        "started_at": iso_utc(utcnow()),
        "finished_at": None,
        "outcome": None,
        "ledgers": [],
        "note": "",
        "task_id": task_id,
        "remediation": "not-run",
        "preliminary": False,
    }
    atomic_write_json(path, hb)
    return path


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AuditError(f"no heartbeat for this run: {path}")
    hb = read_json(path)
    if not isinstance(hb, dict):
        raise AuditError(f"heartbeat is not an object: {path}")
    return hb


def progress(watchdog_dir: Path, task_id: str, run_id: str, note: str, task: str | None = None) -> Path:
    path = heartbeat_path(watchdog_dir, task or task_id, run_id)
    hb = _load(path)
    if hb.get("finished_at"):
        raise AuditError("run already finished; progress notes are pre-finish only")
    hb["note"] = f"{PRELIMINARY}: {note}"
    hb["preliminary"] = True
    atomic_write_json(path, hb)
    return path


def finish(
    watchdog_dir: Path,
    task_id: str,
    run_id: str,
    outcome: str,
    ledgers: Sequence[str] = (),
    note: str = "",
    remediation: str = "not-run",
    task: str | None = None,
) -> Path:
    if outcome not in OUTCOMES:
        raise AuditError(f"outcome must be one of {OUTCOMES}")
    if remediation not in REMEDIATION:
        raise AuditError(f"remediation must be one of {REMEDIATION}")
    path = heartbeat_path(watchdog_dir, task or task_id, run_id)
    hb = _load(path)
    if hb.get("finished_at"):
        raise AuditError(f"heartbeat already finished: {path}")
    hb.update(
        finished_at=iso_utc(utcnow()),
        outcome=outcome,
        ledgers=[str(Path(p).resolve()) for p in ledgers if str(p).strip()],
        note=note,
        remediation=remediation,
        preliminary=False,
    )
    atomic_write_json(path, hb)
    return path


def remediation_update(
    watchdog_dir: Path, task_id: str, run_id: str, remediation: str, note: str = "", task: str | None = None
) -> Path:
    if remediation not in REMEDIATION:
        raise AuditError(f"remediation must be one of {REMEDIATION}")
    path = heartbeat_path(watchdog_dir, task or task_id, run_id)
    hb = _load(path)
    if not hb.get("finished_at"):
        raise AuditError("remediation is recorded only after the run finished (rule 7)")
    hb["remediation"] = remediation
    if note:
        hb["note"] = f"{hb.get('note') or ''} | remediation: {note}".strip(" |")
    atomic_write_json(path, hb)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", required=True, help="skill task_id")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--phase", required=True, choices=("start", "progress", "finish", "remediation"))
    ap.add_argument("--run", choices=OUTCOMES, help="run outcome (finish)")
    ap.add_argument("--remediation", choices=REMEDIATION, default=None)
    ap.add_argument("--ledger", action="append", default=[], help="ledger path; repeatable")
    ap.add_argument("--note", default="")
    ap.add_argument("--watchdog-dir", type=Path, default=None)
    ap.add_argument("--watchdog-task", default=None, help="watchdog folder name override")
    a = ap.parse_args(argv)
    wd = a.watchdog_dir or Path(os.environ.get("AUDIT_WATCHDOG_DIR") or default_watchdog_dir())
    try:
        if a.phase == "start":
            p = start(wd, a.task, a.run_id, a.watchdog_task)
        elif a.phase == "progress":
            p = progress(wd, a.task, a.run_id, a.note, a.watchdog_task)
        elif a.phase == "finish":
            if not a.run:
                raise AuditError("--run is required with --phase finish")
            p = finish(wd, a.task, a.run_id, a.run, a.ledger, a.note, a.remediation or "not-run", a.watchdog_task)
        else:
            if not a.remediation:
                raise AuditError("--remediation is required with --phase remediation")
            p = remediation_update(wd, a.task, a.run_id, a.remediation, a.note, a.watchdog_task)
    except (AuditError, OSError, json.JSONDecodeError) as exc:
        print(f"heartbeat error: {exc}", file=sys.stderr)
        return 2
    print(f"heartbeat {a.phase}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
