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
    "File links: markdown links relative to cwd, forward slashes ([name](path/file.md:line)) - "
    "never absolute backslash paths. "
    "Reports: Done / Assumed / Left-Flags, no filler. "
    "Settled decisions (merged, closed, deliberately gated) stay settled - report once, never re-litigate."
    "</decay-reminder>"
)

try:
    sys.stdout.write(REMINDER)
except Exception:
    pass
sys.exit(0)
