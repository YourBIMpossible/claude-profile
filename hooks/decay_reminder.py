"""UserPromptSubmit decay-reminder hook (C2, 2026-08-08).

Re-injects the three highest-observed-decay behavioral rules on every prompt so
they stay recent deep into long sessions. Payload is deliberately limited to
formatting/reporting behavior: NO autonomy language, NO safety language — safety
must never depend on a hook firing, and autonomy nudges must never be made
"more recent" than the gate list.

Evidence base: dead file links "almost every session" (file-link-format memory),
plus the restating-settled-state and report-shape feedback memories.

Advisory only: any failure here must not affect the session, hence the blanket
guard and exit 0 on all paths.
"""
import sys

REMINDER = (
    "<decay-reminder>"
    "File links: a markdown link ONLY if the file is inside the CURRENT cwd (relative, forward slashes). "
    "Any file outside cwd, or written before a cwd change: NO markdown link - print the full absolute path as plain text "
    "AND deliver it with SendUserFile. Never assume the link opens; check cwd first. "
    "Reports: Done / Assumed / Left-Flags, no filler. "
    "Settled decisions (merged, closed, deliberately gated) stay settled - report once, never re-litigate."
    "</decay-reminder>"
)

try:
    sys.stdout.write(REMINDER)
except Exception:
    pass
sys.exit(0)
