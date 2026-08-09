"""Subprocess fixtures for loop_grant_gate.py (G6).

Run: python test_loop_grant_gate.py   (exit 0 = all pass, 1 = a failure)

Each case writes grant files into an isolated temp dir, points the gate at it via
LOOP_GRANT_DIR, feeds a hook payload on stdin, and asserts the exit code (0 allow /
2 block) plus, where relevant, that the wakeup counter moved.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GATE = str(Path(__file__).resolve().parent.parent / "loop_grant_gate.py")
FUTURE = "2099-01-01"
PAST = "2000-01-01"

FAILS = 0


def grant(status="active", expires=FUTURE, max_wakeups=5, grant_id="G-TEST", nested=False):
    if nested:
        return (
            "grant_id: " + grant_id + "\n"
            "status: " + status + "\n"
            "expires: " + expires + "\n"
            "goal: test\n"
            "budget:\n"
            "  max_wakeups: " + str(max_wakeups) + "\n"
        )
    return (
        "grant_id: " + grant_id + "\n"
        "status: " + status + "\n"
        "expires: " + expires + "\n"
        "max_wakeups: " + str(max_wakeups) + "\n"
    )


def run(grants, payload, tool="ScheduleWakeup", counters=None):
    """grants: {filename: text}. Returns (exit_code, tmpdir Path)."""
    tmp = Path(tempfile.mkdtemp(prefix="loopgate_"))
    for name, text in (grants or {}).items():
        (tmp / name).write_text(text, encoding="utf-8")
    for gid, n in (counters or {}).items():
        (tmp / (gid + ".wakeups")).write_text(str(n), encoding="utf-8")
    env = dict(os.environ, LOOP_GRANT_DIR=str(tmp))
    body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode()
    p = subprocess.run([sys.executable, GATE], input=body, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, tmp


def check(name, cond):
    global FAILS
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS += 1


P_WAKE = {"tool_name": "ScheduleWakeup", "tool_input": {"delaySeconds": 600}}

# 1 valid grant -> allow + counter increments
rc, tmp = run({"g.yaml": grant()}, P_WAKE)
check("valid grant allows", rc == 0)
check("valid grant increments counter", (tmp / "G-TEST.wakeups").read_text().strip() == "1")

# 2 nested budget.max_wakeups still parsed (indent-agnostic)
rc, tmp = run({"g.yaml": grant(nested=True)}, P_WAKE)
check("nested max_wakeups allows", rc == 0)

# 3 budget exhausted -> block
rc, _ = run({"g.yaml": grant(max_wakeups=3)}, P_WAKE, counters={"G-TEST": 3})
check("exhausted budget blocks", rc == 2)

# 4 one wakeup left -> allow
rc, _ = run({"g.yaml": grant(max_wakeups=3)}, P_WAKE, counters={"G-TEST": 2})
check("last wakeup allows", rc == 0)

# 5 expired -> block
rc, _ = run({"g.yaml": grant(expires=PAST)}, P_WAKE)
check("expired grant blocks", rc == 2)

# 6 status not active -> block
rc, _ = run({"g.yaml": grant(status="draft")}, P_WAKE)
check("non-active status blocks", rc == 2)

# 7 missing max_wakeups -> block
rc, _ = run({"g.yaml": "grant_id: G\nstatus: active\nexpires: " + FUTURE + "\n"}, P_WAKE)
check("missing budget blocks", rc == 2)

# 8 missing grant_id -> block
rc, _ = run({"g.yaml": "status: active\nexpires: " + FUTURE + "\nmax_wakeups: 5\n"}, P_WAKE)
check("missing grant_id blocks", rc == 2)

# 9 no grant files (empty dir) -> block
rc, _ = run({}, P_WAKE)
check("empty grants dir blocks", rc == 2)

# 10 malformed JSON stdin -> block (fail-closed)
rc, _ = run({"g.yaml": grant()}, b"{ not json", )
check("malformed stdin blocks", rc == 2)

# 11 non-ScheduleWakeup tool -> allow, no counter
rc, tmp = run({"g.yaml": grant()}, {"tool_name": "Write", "tool_input": {}})
check("non-gated tool allowed", rc == 0)
check("non-gated tool leaves counter untouched", not (tmp / "G-TEST.wakeups").exists())

# 12 missing tool_name -> block (fail-closed)
rc, _ = run({"g.yaml": grant()}, {"tool_input": {}})
check("missing tool_name blocks", rc == 2)

# 13 mix: expired + valid -> allow via the valid one
rc, tmp = run({"a.yaml": grant(expires=PAST, grant_id="OLD"),
               "b.yaml": grant(grant_id="LIVE")}, P_WAKE)
check("valid among invalid allows", rc == 0)
check("valid grant's counter moved", (tmp / "LIVE.wakeups").read_text().strip() == "1")

# 14 BOM-prefixed stdin still parses (PowerShell pipe) -> allow
rc, _ = run({"g.yaml": grant()}, ("﻿" + json.dumps(P_WAKE)).encode("utf-8"))
check("BOM stdin tolerated", rc == 0)

print()
if FAILS:
    print(str(FAILS) + " FAILED")
    sys.exit(1)
print("all loop_grant_gate fixtures passed")
