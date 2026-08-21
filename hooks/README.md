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
| `protect_paths_gate.py` | PreToolUse Write\|Edit | **control** — fail-closed (C3; also locks all `NORTHSTAR*.md` except `*.draft.md`) | `~/.claude/hooks/` |
| `northstar_inject.py` | UserPromptSubmit | **advisory** — re-injects the active locked mission(s) each turn (scope-governor v3, multi-star) | `~/.claude/hooks/` |
| `skill-detection-hook.json` | PostToolUse (Revit MCP) | prompt-type, enabled 2026-08-08 | n/a (JSON in settings.json) |
| `session_closure_audit.py` + `closure_audit_repos.json` | SessionStart + SessionEnd | **advisory** — read-only closure audit: surfaces uncommitted/unpushed design docs, untracked branches, and dirty worktrees across the repos in `closure_audit_repos.json` each session, so knowledge is never lost by deleting a worktree that holds it. Local-only (git status/worktree list; no push/commit/network); fail-open. | `~/.claude/hooks/` (pinned copies of both files; this repo is the source of truth as of 2026-08-21 — update via the C1 copy procedure above) |

## Control vs advisory (from the fail-open lesson)

One **control**, fail-closed (block on its own failure — malformed payload, missing
field, any exception → exit 2): `protect_paths_gate.py` (protected paths on Write/Edit,
including every locked north-star mission file — `NORTHSTAR.md` and
`NORTHSTAR.<slug>.md`; only `*.draft.md` is writable). Everything else is advisory or
convenience — a failure there changes nothing about safety.

Scope honesty:
- **protect_paths_gate**: covers only Write/Edit. **The Bash channel (`sed -i`,
  redirection, `tee`, `Set-Content`, `python -c`) is NOT covered and is advisory-only** —
  never describe Bash-side protection as a control.
- **northstar_inject** (scope-governor v3, multi-star — 2026-08-15; full model in
  `docs/northstar-multi-design.md`): ADVISORY. A project may carry MANY north stars
  (`NORTHSTAR.md`, `NORTHSTAR.<slug>.md`); each file's own human-edited `status:` line
  (absent = active; `paused`/`closed` = inactive) decides what injects — no renames, no
  magic location. Each turn the hook re-reads from disk all ACTIVE stars in the closest
  walk-up directory holding one, and injects them + the three-doors routing rule, so the
  plan cannot drift through a rolling context summary. It does not enforce — the matching
  enforcement is protect_paths_gate blocking edits to `NORTHSTAR*.md` (only the human
  moves, pauses, closes, or activates a mission; Claude may only write `*.draft.md`,
  which the human renames to lock in). Whether an extra is a tag-along / roadmap item /
  mission-change is the model's judgment; a hook cannot classify scope, so this is not
  dressed up as a control. v2 (single hard-coded `NORTHSTAR.md`) superseded the G6
  loop-grant gate + G7 reground reminder (removed 2026-08-08); v3 removed v2's
  one-mission-per-tree limit.
