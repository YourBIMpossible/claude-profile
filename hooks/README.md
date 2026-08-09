# hooks — sources and installation rule

## Installation rule (C1, adopted 2026-08-08)

**Executable hooks are installed only as pinned, copied files under
`C:\Users\Zeria\.claude\hooks\`.** settings.json must never reference hook code
through the `~/.claude/skills` junction, this repo's working tree, or any other
switchable/pullable path. Rationale: a branch switch or `git pull` in this repo
must never silently change code that runs on every prompt or tool call
(hook-checkout-switch trap, 2026-08; live-symlink hazard from the operating-canon
review). Content (skills text, CLAUDE.md) may live behind the junction;
**executable code must not**.

Install procedure:
1. Source of truth lives here (`hooks/*.py`, `*.ps1`), reviewed via the normal
   PR flow.
2. Installing = a deliberate `Copy-Item` of the reviewed version to
   `C:\Users\Zeria\.claude\hooks\`, then pointing settings.json at that copy.
3. Updating a hook is the same act again — diff the source against the installed
   copy first (`git diff` won't show it; use `fc`/`Compare-Object`).
4. A pinned copy that must know the repo's location hardcodes it (see
   `session-start-pull.ps1`'s `$repoRoot`); never derive it from
   `$PSScriptRoot`/`__file__` in an installed copy.

Prompt-type hooks (JSON only, no code path — e.g. `skill-detection-hook.json`)
are exempt: they live entirely inside settings.json.

## Inventory

| Source | Event | Class | Installed copy |
|---|---|---|---|
| `session-start-pull.ps1` | SessionStart | convenience | `~/.claude/hooks/` (pinned; `$repoRoot` hardcoded) |
| `ctxdex_autoindex.py` | PostToolUse Bash | convenience | `~/.claude/hooks/` |
| `decay_reminder.py` | UserPromptSubmit | **advisory** reminder (C2) | `~/.claude/hooks/` |
| `protect_paths_gate.py` | PreToolUse Write\|Edit | **control** — fail-closed (C3) | `~/.claude/hooks/` |
| `loop_grant_gate.py` | PreToolUse ScheduleWakeup | **control** — fail-closed (G6) | `~/.claude/hooks/` |
| `reground_reminder.py` | SessionStart | **advisory** reminder (G7) | `~/.claude/hooks/` |
| `skill-detection-hook.json` | PostToolUse (Revit MCP) | prompt-type, enabled 2026-08-08 | n/a (JSON in settings.json) |

## Control vs advisory (from the fail-open lesson)

Two **controls**, both fail-closed (block on their own failure — malformed payload,
missing field, any exception → exit 2): `protect_paths_gate.py` (protected paths on
Write/Edit) and `loop_grant_gate.py` (autonomous-loop arms without a live grant).
Everything else is advisory or convenience — a failure there changes nothing about
safety.

Scope honesty for both controls:
- **protect_paths_gate**: covers only Write/Edit. **The Bash channel (`sed -i`,
  redirection, `tee`, `Set-Content`, `python -c`) is NOT covered and is advisory-only** —
  never describe Bash-side protection as a control.
- **loop_grant_gate**: covers only the `ScheduleWakeup` tool. It cannot stop a human
  manually re-issuing a `/loop`, nor autonomy that never routes through ScheduleWakeup.
  That residual is advisory. The grant store is `F:\AI-Dev\.tools\state\grants\`; grants
  are created only by `/authorize-lane` from a genuine human turn.
