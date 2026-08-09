"""UserPromptSubmit hook: re-inject the locked NORTHSTAR mission every turn.

Autonomous-scope-governor v2 (2026-08-08). Replaces reground_reminder.py.

WHY THIS EXISTS
  The scope-runaway failure happened because the "plan" lived inside rolling
  context summaries. Over many autonomous cycles the summary got reworded until
  the reworded plan WAS the new (wrong) plan - the model edited its own orders
  and then followed them faithfully. The fix: keep the mission on DISK in
  NORTHSTAR.md, human-owned, and re-read it from disk on every prompt. A summary
  can no longer be the source of truth for what we are building.

WHAT IT DOES
  - Looks for NORTHSTAR.md in the current directory, then walks up to find one.
  - If found, injects its text plus the standing three-doors routing rule.
  - If none is found, injects nothing (opt-in per project).

SCOPE HONESTY (the C3 lesson)
  This is ADVISORY, not a control. It reminds; it does not enforce. The matching
  ENFORCEMENT is protect_paths_gate.py blocking Write/Edit on NORTHSTAR.md, so
  the model cannot rewrite the mission even if it ignores this reminder. The
  three-doors ROUTING (tag-along vs roadmap vs mission-change) is the model's
  judgment - a hook cannot classify scope, and pretending it can would be a
  fail-open control. Never overclaim what this hook does.

  Never fails a prompt: any read/parse problem exits 0 (inject nothing).
  Output is written as raw UTF-8 bytes (see emit()), so a mission file may
  contain arrows/emoji/smart-quotes without tripping the cp1252 console trap.
"""
import os
import sys
from pathlib import Path

MAX_WALK_UP = 6  # cwd + up to 6 parents; closest NORTHSTAR wins


def emit(text: str) -> None:
    """Write UTF-8 bytes directly. The console may be cp1252; a mission file can
    hold arrows/emoji/smart-quotes outside it, and a plain stdout.write would
    raise UnicodeEncodeError (then get swallowed by the fail-open backstop, so
    the mission would silently never inject). Bypass the text layer entirely."""
    try:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        pass


def find_northstar() -> "Path | None":
    here = Path(os.getcwd())
    candidates = [here, *list(here.parents)[:MAX_WALK_UP]]
    for d in candidates:
        f = d / "NORTHSTAR.md"
        try:
            if f.is_file():
                return f
        except OSError:
            continue
    return None


def read_event() -> str:
    """Return the hook_event_name from the payload, or '' if unreadable."""
    try:
        import json
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if isinstance(payload, dict):
            return str(payload.get("hook_event_name") or "")
    except Exception:
        pass
    return ""


def main() -> None:
    event = read_event()
    f = find_northstar()
    if f is None:
        # No mission locked yet. Nudge ONCE at session start (never per-turn nag)
        # so Claude offers to draft one from the user's first real instruction.
        if event == "SessionStart":
            emit(
                "<northstar-setup>\n"
                "No NORTHSTAR.md in this project yet. If this session is real work,\n"
                "ask the user what we are building and DRAFT a NORTHSTAR.md for them\n"
                "(write it as NORTHSTAR.draft.md - you cannot save the real file; the\n"
                "human renames it to NORTHSTAR.md to lock it in). Keep the draft to a\n"
                "few plain sentences: mission, what done looks like, what is off-limits.\n"
                "</northstar-setup>"
            )
        sys.exit(0)
    try:
        mission = f.read_text(encoding="utf-8-sig").strip()
    except Exception:
        sys.exit(0)  # advisory: never block a prompt on a read error
    if not mission:
        sys.exit(0)
    out = (
        "<northstar>\n"
        "LOCKED MISSION for this project, re-read from " + str(f) + " every turn.\n"
        "You cannot edit this file (a gate blocks it). Work toward this mission.\n"
        "Route anything extra through three doors and record it in WORKLOG.md:\n"
        "  1) small AND serves the mission -> do it, log under Done.\n"
        "  2) bigger, worth doing, but a branch-off -> log under Roadmap, keep going.\n"
        "  3) would change the mission itself -> log under Needs your call using the\n"
        "     plain box; keep working the current mission until the human answers.\n"
        "Never edit NORTHSTAR.md yourself. Never abandon the mission on your own.\n"
        "----- mission text -----\n"
        + mission + "\n"
        "</northstar>"
    )
    emit(out)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory hook: fail open, never break a prompt
