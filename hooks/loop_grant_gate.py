"""PreToolUse fail-closed loop-grant gate for ScheduleWakeup (G6, 2026-08-08).

The one mechanical control of the autonomous-scope-governor design (see
BIMpossible_Workspace/00_Strategy/2026-08-08__DESIGN_autonomous-scope-governor.md).
It makes "an autonomous loop runs for days with no human in the loop" — the property
that caused the scope-runaway incident — mechanically impossible: every ScheduleWakeup
arm (first arm AND re-arm; strictest option per the approved design) must cite a live,
unexpired, in-budget grant, or it is blocked.

FAIL-CLOSED CONTRACT (same discipline as protect_paths_gate.py): any condition this
script cannot positively evaluate — malformed stdin JSON, missing grants dir, no valid
grant, an exhausted budget, an unparseable grant, any unexpected exception — exits 2
(block). A gate that can silently no-op is worse than no gate. Exit 0 = allow, exit 2 =
block (stderr is shown to Claude).

SCOPE HONESTY: this gates the ScheduleWakeup *tool*. It cannot and does not stop a human
manually re-issuing a /loop, nor any autonomy that does not route through ScheduleWakeup.
That residual is advisory, documented in hooks/README.md — do not overstate the control.

Grant files: F:\\AI-Dev\\.tools\\state\\grants\\*.yaml (override dir with env LOOP_GRANT_DIR
for tests only; production never sets it). A grant is VALID when, by an indent-agnostic
line scan, it has: grant_id, status: active, expires: YYYY-MM-DD >= today, and an integer
max_wakeups budget that its sidecar counter (<grant_id>.wakeups) has not yet reached. On
allow, the chosen grant's counter is incremented — that increment is the fire-once artifact.
"""
import datetime
import json
import os
import sys
from pathlib import Path

BLOCK_EXIT = 2
GATED_TOOL = "ScheduleWakeup"
GRANT_KEYS = ("grant_id", "status", "expires", "max_wakeups")


def grants_dir() -> Path:
    return Path(os.environ.get("LOOP_GRANT_DIR", r"F:\AI-Dev\.tools\state\grants"))


def block(msg: str) -> None:
    sys.stderr.write(
        "loop_grant_gate: BLOCKED - " + msg + "\n"
        "Autonomous/loop lanes require a live grant. Create one with /authorize-lane "
        "(it must cite a genuine human turn this session), or run this step attended.\n"
    )
    sys.exit(BLOCK_EXIT)


def parse_grant(text: str) -> dict:
    """Indent-agnostic scan for the gate-relevant keys; first occurrence wins."""
    fields: dict = {}
    for line in text.splitlines():
        s = line.strip()
        for key in GRANT_KEYS:
            if key in fields:
                continue
            if s.lower().startswith(key + ":"):
                val = s.split(":", 1)[1].strip().strip('"').strip("'")
                fields[key] = val
    return fields


def count_path(gid: str) -> Path:
    return grants_dir() / (gid + ".wakeups")


def read_count(gid: str) -> int:
    try:
        return int(count_path(gid).read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def grant_validity(text: str):
    """Return (grant_id, None) if valid, else (grant_id_or_name, reason)."""
    f = parse_grant(text)
    gid = f.get("grant_id")
    if not gid:
        return None, "no grant_id"
    if f.get("status", "").lower() != "active":
        return gid, "status is not active (" + f.get("status", "<none>") + ")"
    exp = f.get("expires", "")
    try:
        y, m, d = (int(x) for x in exp.split("-"))
        expd = datetime.date(y, m, d)
    except Exception:
        return gid, "unparseable expires (" + repr(exp) + ")"
    if expd < datetime.date.today():
        return gid, "expired " + exp
    try:
        mw = int(f.get("max_wakeups", ""))
    except Exception:
        return gid, "missing/invalid max_wakeups budget"
    used = read_count(gid)
    if used >= mw:
        return gid, "budget exhausted (" + str(used) + "/" + str(mw) + " wakeups)"
    return gid, None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception as exc:  # fail-closed
        block("could not parse hook payload from stdin (" + str(exc) + "); failing closed")
    if not isinstance(payload, dict):
        block("hook payload is not a JSON object; failing closed")

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        block("no tool_name in payload; failing closed")
    if tool_name != GATED_TOOL:
        sys.exit(0)  # not our tool; do not interfere

    gdir = grants_dir()
    if not gdir.is_dir():
        block("grants directory " + str(gdir) + " is missing; no autonomous authority exists")

    grant_files = sorted(p for p in gdir.glob("*.yaml") if p.is_file())
    if not grant_files:
        block("no grant files in " + str(gdir) + "; no active autonomous lane is authorized")

    reasons = []
    for gf in grant_files:
        try:
            text = gf.read_text(encoding="utf-8-sig")
        except Exception as exc:
            reasons.append(gf.name + ": unreadable (" + str(exc) + ")")
            continue
        gid, reason = grant_validity(text)
        if reason is None:
            # valid grant: consume one wakeup and allow
            try:
                new_count = read_count(gid) + 1
                count_path(gid).write_text(str(new_count), encoding="utf-8")
            except Exception as exc:  # fail-closed: cannot record the burn -> do not allow
                block("cannot write wakeup counter for " + gid + " (" + str(exc) + "); failing closed")
            sys.exit(0)
        reasons.append(gf.name + ": " + reason)

    block("no valid grant. " + "; ".join(reasons))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # absolute backstop: never fail open
        block("unexpected error (" + str(exc) + "); failing closed")
