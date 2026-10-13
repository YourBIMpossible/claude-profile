"""UserPromptSubmit hook: re-inject the locked NORTHSTAR mission(s) every turn.

Autonomous-scope-governor v3 (2026-08-15). v2 assumed exactly ONE mission per
project (a single hard-coded NORTHSTAR.md, closest-wins). v3 supports a SET of
concurrent north stars so parallel/sequential workflows each carry their own
locked mission without renaming files or fighting over one canonical path.

WHY THIS EXISTS (unchanged from v2)
  The scope-runaway failure happened because the "plan" lived inside rolling
  context summaries. Over many autonomous cycles the summary got reworded until
  the reworded plan WAS the new (wrong) plan - the model edited its own orders
  and then followed them faithfully. The fix: keep the mission on DISK in
  owner-approved files and re-read them from disk on every prompt. A summary can
  never be the source of truth for what we are building.

THE v3 MODEL
  - A north star is any file named NORTHSTAR.md or NORTHSTAR.<slug>.md sitting
    at a project level (same walk-up as before, closest level wins).
  - Lifecycle lives INSIDE the file as an owner-approved line near the top:
        status: active | paused | closed
    No status line means ACTIVE (so every legacy NORTHSTAR.md keeps working).
    Activation/pause/close/switch = Claude edits one line and the owner
    approves it at the gate's prompt. No renames, no moves, no magic directory.
  - Drafts (NORTHSTAR.<slug>.draft.md / NORTHSTAR.draft.md) are written and
    revised freely. Making a draft final (saving it as NORTHSTAR.<slug>.md,
    which activates it) is the one step that needs the owner's approval - the
    gate's prompt. Since 2026-10-10 Claude does the save; the owner approves.
  - Injection: the closest walk-up directory holding at least one ACTIVE star
    wins, and ALL of its active stars inject. (A worktree lane's own star still
    shadows the portfolio star, exactly like v2's closest-wins.)

SCOPE HONESTY (the C3 lesson)
  This is ADVISORY, not a control. It reminds; it does not enforce. The matching
  ENFORCEMENT is protect_paths_gate.py turning every Write/Edit on a live
  NORTHSTAR*.md (anything except *.draft.md / *.template.md) into an owner
  approval prompt, so the model cannot silently rewrite, pause, or close a
  mission even if it ignores this reminder (Bash writes are NOT covered).
  The three-doors ROUTING is the
  model's judgment - a hook cannot classify scope, and pretending it can would
  be a fail-open control. Never overclaim what this hook does.

  Never fails a prompt: any read/parse problem exits 0 (inject nothing).
  Output is written as raw UTF-8 bytes (see emit()), so a mission file may
  contain arrows/emoji/smart-quotes without tripping the cp1252 console trap.
"""
import os
import re
import sys
from pathlib import Path

MAX_WALK_UP = 6  # cwd + up to 6 parents; closest level with an active star wins

# NORTHSTAR.md or NORTHSTAR.<slug>.md (slug = anything); drafts and templates
# are not missions (both are also the gate's writable exemptions)
STAR_NAME_RE = re.compile(r"^northstar(\..+)?\.md$", re.IGNORECASE)
NON_STAR_SUFFIXES = (".draft.md", ".template.md")

# status line: near the top of the file, e.g. "status: paused" (optionally
# bolded or inside --- frontmatter); absence of the line means active
STATUS_RE = re.compile(r"^\s*\**status\**\s*[:=]\s*([A-Za-z-]+)", re.IGNORECASE)
INACTIVE_WORDS = {"paused", "closed", "done", "archived", "inactive"}
STATUS_SCAN_LINES = 20


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


def note(msg: str) -> None:
    """One stderr line for a fail-open site, so a dropped injection is observable
    (exit stays 0; stdout, the injected text, is untouched). Never raises."""
    try:
        sys.stderr.write(f"northstar_inject: {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


def star_status(f: Path) -> str:
    """Return 'active' or the inactive word from the file's status line.
    Unreadable or unparseable -> 'active' (over-injecting a mission is safer
    than silently dropping one)."""
    try:
        with f.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for _ in range(STATUS_SCAN_LINES):
                line = fh.readline()
                if not line:
                    break
                m = STATUS_RE.match(line)
                if m:
                    word = m.group(1).lower()
                    return word if word in INACTIVE_WORDS else "active"
    except Exception:
        pass
    return "active"


def stars_in(d: Path) -> "list[Path]":
    """All non-draft north-star files directly in d, sorted by name."""
    out = []
    try:
        for p in d.iterdir():
            name = p.name.lower()
            if not STAR_NAME_RE.match(name) or name.endswith(NON_STAR_SUFFIXES):
                continue
            if p.is_file():
                out.append(p)
    except OSError:
        pass
    return sorted(out, key=lambda p: p.name.lower())


def find_stars() -> "tuple[list[Path], list[tuple[Path, str]]]":
    """Walk up from cwd. Return (active, inactive):
    - active: all active stars in the CLOSEST directory that has at least one
      (empty if no directory has an active star).
    - inactive: (path, status) for every paused/closed star seen during the
      walk, up to and including the winning directory."""
    here = Path(os.getcwd())
    inactive: "list[tuple[Path, str]]" = []
    for d in [here, *list(here.parents)[:MAX_WALK_UP]]:
        active_here = []
        for f in stars_in(d):
            st = star_status(f)
            if st == "active":
                active_here.append(f)
            else:
                inactive.append((f, st))
        if active_here:
            return active_here, inactive
    return [], inactive


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
    active, inactive = find_stars()

    if not active:
        # No live mission. Nudge ONCE at session start (never per-turn nag).
        # Mirrors CLAUDE.md's "state the assumed milestone and proceed" rule;
        # asking the owner to draft a mission contradicted it (2026-10-11).
        if event == "SessionStart":
            if inactive:
                names = ", ".join(f"{p.name} ({st})" for p, st in inactive)
                emit(
                    "<northstar-status>\n"
                    "No ACTIVE north star, but inactive ones exist: " + names + "\n"
                    "Do not work toward them unless the user's request names one;\n"
                    "reactivating is an edit to that file's 'status:' line that the\n"
                    "owner approves at the gate's prompt (never via Bash). Otherwise\n"
                    "proceed as in CLAUDE.md: state the milestone you are assuming in\n"
                    "your first line and work within the request's scope. An assumed\n"
                    "milestone grants no authority beyond the request; escalate only\n"
                    "consequential ambiguity.\n"
                    "</northstar-status>"
                )
            else:
                emit(
                    "<northstar-setup>\n"
                    "No north star in this project. Do not stop to ask for or draft a\n"
                    "mission: as CLAUDE.md says, state the milestone you are assuming in\n"
                    "your first line and proceed within the request's scope. An assumed\n"
                    "milestone grants no authority beyond the request; escalate only\n"
                    "consequential ambiguity. Draft NORTHSTAR.<slug>.draft.md only if the\n"
                    "user asks for a north star; saving it as NORTHSTAR.<slug>.md makes\n"
                    "it active, and the gate's prompt is the owner's approval.\n"
                    "</northstar-setup>"
                )
        sys.exit(0)

    blocks = []
    unreadable = []
    for f in active:
        try:
            text = f.read_text(encoding="utf-8-sig").strip()
        except Exception as e:
            unreadable.append(f"{f.name} ({type(e).__name__})")
            continue  # advisory: skip an unreadable star, keep the rest
        if text:
            blocks.append((f, text))
    if unreadable:
        note("skipped unreadable active north star(s): " + ", ".join(unreadable)
             + ("" if blocks else "; nothing injected"))
    if not blocks:
        sys.exit(0)

    paused_note = ""
    local_inactive = [(p, st) for p, st in inactive if p.parent == blocks[0][0].parent]
    if local_inactive:
        paused_note = (
            "Inactive here (NOT injected, do not work toward them): "
            + ", ".join(f"{p.name} ({st})" for p, st in local_inactive) + "\n"
        )

    plural = "s" if len(blocks) > 1 else ""
    out = (
        "<northstar>\n"
        f"LOCKED MISSION{plural.upper() or ''} - {len(blocks)} active north star file{plural}, "
        "re-read from disk every turn.\n"
        "You may edit these files only with the owner's approval: the gate turns any\n"
        "Write/Edit on NORTHSTAR*.md into an approval prompt (*.draft.md is free).\n"
        "Work toward the active mission(s) below.\n"
        "Route anything extra through three doors and record it in WORKLOG.md:\n"
        "  1) small AND serves an active mission -> do it, log under Done.\n"
        "  2) bigger, worth doing, but a branch-off -> log under Roadmap, keep going.\n"
        "  3) would change a mission itself -> log under Needs your call using the\n"
        "     plain box; keep working the current mission(s) until the human answers.\n"
        "To change a mission: say in one or two plain lines what changes and why,\n"
        "then make the edit and let the owner approve the prompt. Do not also ask\n"
        "in chat, and never route around the prompt by any tool (Bash included).\n"
        "Never abandon, merge, pause, or close a mission on your own\n"
        "initiative - pausing/closing/switching is an edit to that file's\n"
        "'status:' line that the owner approves.\n"
        "A NEW direction that deserves its own mission is not a conflict anymore:\n"
        "draft NORTHSTAR.<slug>.draft.md beside these, show it, and on approval\n"
        "save it as NORTHSTAR.<slug>.md. To REPLACE a mission, draft the\n"
        "replacement the same way. Never treat a draft as active.\n"
        + paused_note
        + "".join(
            f"----- {f.name} (active) -----\n{text}\n" for f, text in blocks
        )
        + "</northstar>"
    )
    emit(out)
    sys.exit(0)


def run_fail_open() -> None:
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        note(f"failed open ({type(e).__name__}); nothing injected")
        sys.exit(0)  # advisory hook: fail open, never break a prompt


if __name__ == "__main__":
    run_fail_open()
