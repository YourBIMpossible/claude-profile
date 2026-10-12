"""UserPromptSubmit decay-reminder hook (C2, 2026-08-08).

Re-injects two high-observed-decay reporting rules on every prompt so they stay
recent deep into long sessions. Payload is deliberately limited to
formatting/reporting behavior: NO autonomy language, NO safety language — safety
must never depend on a hook firing, and autonomy nudges must never be made
"more recent" than the gate list.

Evidence base: the restating-settled-state and report-shape feedback memories.
The file-link rule was removed 2026-10-11 (ADR 0003 context repair): it was a
cwd-conditional copy competing with CLAUDE.md, which now owns one stateless
version. Do not re-add a copy here.

Advisory only: any failure here must not affect the session, hence the blanket
guard and exit 0 on all paths.
"""
import sys

REMINDER = (
    "<decay-reminder>"
    "Reports: Done / Assumed / Left-Flags, no filler. "
    "Settled decisions (merged, closed, deliberately gated) stay settled - report once, never re-litigate."
    "</decay-reminder>"
)

try:
    sys.stdout.write(REMINDER)
except Exception:
    pass
sys.exit(0)
