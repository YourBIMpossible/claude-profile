#!/usr/bin/env python3
"""Portable runner for the NL-filter drift monitor (replaces the scheduled routine
`nl-filter-drift-monitor-weekly`'s direct call to Invoke-NlFilterLiveMonitor.ps1).

Same ledger contract as the PowerShell wrapper, plus an offline mode and guard rails:

  * --mode offline  runs the deterministic, keyless evaluator on the host
                    (`nl_filter_monitor.py --mode offline`); no container, no API.
  * --mode live     runs the paid evaluator inside the already-running backend
                    container (`docker exec ... --mode live`). Never starts it.

Under --ledger-dir it writes exactly three things:
  nl-filter-drift-monitor-log.md            (appended, one row per run)
  _evidence/nl-filter-monitor/*.json        (the run's machine-readable summary)
  NL-FILTER-DRIFT-ALERT.md                  (written on red, deleted on green)

Exit codes: 0 pass, 1 regression, 2 not-run (anything that proved nothing),
64 refused (forbidden flag, bad usage, or the shared ledger without --live).
--write-baseline is always refused: re-pinning is an owner action, never a monitor run.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

EXIT_PASS, EXIT_REGRESSION, EXIT_NOT_RUN, EXIT_REFUSED = 0, 1, 2, 64


def _repos_root() -> Path:
    """{repos}, the folder holding the repo clones, from this profile's machine config
    (tools/machine_config.py); without it, the folder holding this profile clone."""
    profile = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(profile / "tools"))
    try:
        import machine_config
        return Path(machine_config.load(profile)["repos_root"])
    except (ImportError, KeyError, ValueError, OSError):
        return profile.parent
    finally:
        sys.path.pop(0)


REPOS_ROOT = _repos_root()
DEFAULT_REPO = os.environ.get("NL_FILTER_REPO", str(REPOS_ROOT / "BIMpossible"))
DEFAULT_CONTAINER = os.environ.get("NL_FILTER_CONTAINER", "docker-backend-1")
SHARED_LEDGER = os.environ.get(
    "NL_FILTER_SHARED_LEDGER",
    str(REPOS_ROOT / "BIMpossible-Workspace" / "02_Reference" / "Audit and Scan Info"),
)
MONITOR_REL = Path("backend") / "tests" / "eval" / "nl_filter_monitor.py"
IN_CONTAINER_JSON = "/tmp/nl-filter-live.json"

LEDGER_NAME = "nl-filter-drift-monitor-log.md"
ALERT_NAME = "NL-FILTER-DRIFT-ALERT.md"
EVIDENCE_REL = Path("_evidence") / "nl-filter-monitor"

LEDGER_HEADER = "\n".join([
    "# NL-filter drift monitor - live run ledger",
    "",
    "Append-only record of the NL-filter drift monitor (audit follow-up ARCH-3C,",
    "`audit-resolution_2026-09-12.md`). Written by `nl_filter_drift_monitor.py` /",
    "`Invoke-NlFilterLiveMonitor.ps1`. Every run appends one row, green or red. A red run",
    "also leaves `NL-FILTER-DRIFT-ALERT.md` beside this file.",
    "",
    "| When (UTC) | Commit | Repeats | Samples | Score | Flips | Transport | Verdict | Summary |",
    "|---|---|---|---|---|---|---|---|---|",
])


class Refusal(Exception):
    """A request this runner will not execute (exit 64)."""


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))


def _utc(fmt: str) -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime(fmt)


def _run(cmd: Sequence[str], *, cwd: str | None = None, env: dict[str, str] | None = None,
         timeout: int = 1800) -> tuple[int, str]:
    proc = subprocess.run(list(cmd), cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _commit(repo: Path) -> str:
    try:
        rc, out = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=30)
        return out.strip() if rc == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def map_exit(rc: int) -> int:
    """0 and 1 are verdicts; every other code means the run proved nothing."""
    return rc if rc in (EXIT_PASS, EXIT_REGRESSION) else EXIT_NOT_RUN


def _verdict_line(text: str, mode: str, rc: int) -> str:
    for ln in text.splitlines():
        if ln.startswith("nl-filter-monitor "):
            return ln.strip()
    return f"nl-filter-monitor mode={mode} verdict=UNPARSEABLE (exit {rc})"


def run_offline(monitor: Path, json_out: Path, python: str) -> tuple[int, str, str]:
    if not monitor.is_file():
        why = f"monitor script not found at {monitor} - nothing was checked"
        return EXIT_NOT_RUN, f"nl-filter-monitor mode=offline verdict=NOT-RUN ({why})", why
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    # Run from backend/ (the script's grandparent's parent), as Verify-Local-CI does.
    cwd = str(monitor.parent.parent.parent)
    rc, text = _run([python, str(monitor), "--mode", "offline", "--json", str(json_out)],
                    cwd=cwd, env=env)
    print(text.rstrip())
    rc = map_exit(rc)
    return rc, _verdict_line(text, "offline", rc), ("" if rc == 0 else f"exit {rc} - see the summary")


def _container_running(container: str) -> bool:
    try:
        rc, out = _run(["docker", "inspect", "-f", "{{.State.Running}}", container], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0 and out.strip() == "true"


def run_live(container: str, repeats: int, json_out: Path, commit: str) -> tuple[int, str, str]:
    if not _container_running(container):
        why = (f"container '{container}' is not running - the live evaluator never "
               "executed, so nothing was checked")
        return EXIT_NOT_RUN, f"nl-filter-monitor mode=live verdict=NOT-RUN ({why})", why
    # Clear a stale in-container artifact first so a dead run can never file last
    # run's numbers as this run's evidence (same rule as the PowerShell wrapper).
    _run(["docker", "exec", container, "rm", "-f", IN_CONTAINER_JSON], timeout=60)
    rc, text = _run(["docker", "exec", "-w", "/app", "-e", f"NL_FILTER_EVAL_COMMIT={commit}",
                     container, "python", "tests/eval/nl_filter_monitor.py", "--mode", "live",
                     "--repeats", str(repeats), "--json", IN_CONTAINER_JSON])
    print(text.rstrip())
    rc = map_exit(rc)
    cp_rc, _ = _run(["docker", "cp", f"{container}:{IN_CONTAINER_JSON}", str(json_out)], timeout=120)
    if cp_rc != 0 or not json_out.is_file():
        print(f"    WARN: could not copy the JSON summary out of the container (docker cp exit {cp_rc})")
    return rc, _verdict_line(text, "live", rc), ("" if rc == 0 else f"exit {rc} - see the summary")


def _load_summary(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def record(ledger_dir: Path, *, stamp: str, commit: str, repeats: int | str, rc: int,
           line: str, why: str, summary_path: Path) -> None:
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger = ledger_dir / LEDGER_NAME
    alert = ledger_dir / ALERT_NAME
    if not ledger.exists():
        ledger.write_text(LEDGER_HEADER + "\n", encoding="utf-8")
    s = _load_summary(summary_path) if summary_path.is_file() else None
    short = commit[:12] if commit else "(unknown)"
    samples = s.get("sample_count", "-") if s else "-"
    score = f"{float(s['score']):.3f}" if s and isinstance(s.get("score"), (int, float)) else "-"
    flips = s.get("flips", "-") if s else "-"
    terr = s.get("transport_errors", "-") if s else "-"
    verdict = {0: "PASS", 1: "**FAIL (regression)**"}.get(rc, "**RED (did not run)**")
    ref = f"`{summary_path.name}`" if summary_path.is_file() else "(none)"
    note = f" - {why}" if why else ""
    rows = [f"| {stamp} | `{short}` | {repeats} | {samples} | {score} | {flips} | {terr} | {verdict} | {ref}{note} |"]
    if rc != 0:
        alert.write_text("\n".join([
            "# NL-FILTER DRIFT ALERT", "",
            f"- **Raised:** {stamp}", f"- **Commit:** `{commit}`", f"- **Verdict line:** `{line}`", "",
            why, "",
            "The NL-filter eval did not come back clean. This file is the standing signal: it",
            "stays until a later green run clears it.", "",
            "## What to do", "",
            "1. Read the JSON summary named in the last row of `nl-filter-drift-monitor-log.md`.",
            "2. Reproduce: `docker exec -w /app docker-backend-1 python tests/eval/nl_filter_monitor.py --mode live --repeats 10 --report`",
            "3. A genuine prompt/fixture change is not drift: the OWNER re-pins with `--write-baseline`",
            "   and records a fresh `--repeats 10` run in `live_evidence`. This runner never re-pins.",
            "4. Do NOT raise the threshold or disable the lane to clear this. Never silence a red register.",
            "",
        ]) + "\n", encoding="utf-8")
        print(f"\n    RED: standing alert written to {alert}")
    elif alert.exists():
        alert.unlink()
        rows.append(f"| {_utc('%Y-%m-%dT%H:%M:%SZ')} | `{short}` | - | - | - | - | - | (alert cleared) | "
                    "the standing alert from a previous red run was cleared by this green run |")
        print("    Cleared the standing alert from a previous red run.")
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    print(f"\n    Ledger: {ledger}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="nl_filter_drift_monitor",
                                 description="NL-filter drift monitor runner (offline | live).")
    ap.add_argument("--mode", choices=("offline", "live"), default="offline")
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    ap.add_argument("--ledger-dir", default=SHARED_LEDGER)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="BIMpossible checkout root")
    ap.add_argument("--monitor-script", default="", help="override path to nl_filter_monitor.py")
    ap.add_argument("--python", default=sys.executable, help="interpreter for offline mode")
    ap.add_argument("--live", action="store_true",
                    help="acknowledge writing the SHARED default ledger (refused without it)")
    ap.add_argument("--whatif-dry-run", action="store_true", help="print the plan; run and write nothing")
    return ap


def _forbidden(argv: Sequence[str]) -> bool:
    return any(a.lower().lstrip("-").replace("-", "") == "writebaseline" or a.startswith("--write-baseline=")
               for a in argv)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if _forbidden(argv):
            raise Refusal("--write-baseline is refused: re-pinning the baseline is an owner action, "
                          "never a monitor run")
        try:
            args = build_parser().parse_args(argv)
        except SystemExit as exc:
            if exc.code in (0, None):
                return EXIT_PASS
            raise Refusal("bad usage") from None
        if args.repeats < 1:
            raise Refusal("--repeats must be >= 1")
        ledger_dir = Path(args.ledger_dir)
        if _norm(ledger_dir) == _norm(SHARED_LEDGER) and not args.live:
            raise Refusal(f"refusing the shared default ledger {SHARED_LEDGER} without --live; "
                          "pass --ledger-dir <isolated dir>")
    except Refusal as exc:
        print(f"nl-filter-drift-monitor: REFUSED - {exc}", file=sys.stderr)
        return EXIT_REFUSED

    repo = Path(args.repo)
    monitor = Path(args.monitor_script) if args.monitor_script else repo / MONITOR_REL
    if args.whatif_dry_run:
        if args.mode == "live":
            print(f'DRY RUN: would exec "{args.container}" -> tests/eval/nl_filter_monitor.py '
                  f"--mode live --repeats {args.repeats}; ledger {ledger_dir}")
        else:
            print(f"DRY RUN: would run {monitor} --mode offline; ledger {ledger_dir}")
        return EXIT_PASS

    commit = _commit(repo)
    stamp = _utc("%Y-%m-%dT%H:%M:%SZ")
    evidence = ledger_dir / EVIDENCE_REL
    json_out = evidence / f"nl-filter-{args.mode}_{_utc('%Y-%m-%dT%H%M%S')}Z.json"
    try:
        evidence.mkdir(parents=True, exist_ok=True)
        if args.mode == "offline":
            rc, line, why = run_offline(monitor, json_out, args.python)
        else:
            rc, line, why = run_live(args.container, args.repeats, json_out, commit)
    except (OSError, subprocess.SubprocessError) as exc:
        rc = EXIT_NOT_RUN
        why = f"runner error: {type(exc).__name__} - the evaluator did not run to a verdict"
        line = f"nl-filter-monitor mode={args.mode} verdict=RUNNER-ERROR (exit 2)"
        print(line)
    try:
        record(ledger_dir, stamp=stamp, commit=commit,
               repeats=args.repeats if args.mode == "live" else "offline",
               rc=rc, line=line, why=why, summary_path=json_out)
    except OSError as exc:
        print(f"nl-filter-drift-monitor: run completed (exit {rc}) but its record could not be "
              f"written: {type(exc).__name__}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
