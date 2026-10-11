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
2. Installing = running `bootstrap.ps1`, which copies every reviewed hook here to
   `C:\Users\Zeria\.claude\hooks\` (backing up any differing installed copy) and
   merges settings.json with `settings.template.json`, whose registrations
   point only at those copies (`__CLAUDE_HOOKS_DIR__`). A new hook must be
   registered in the template in the same change —
   `hooks/tests/test_bootstrap_install.py` fails when a hook ships unregistered.
   Before writing anything, bootstrap refuses when a hook source has uncommitted
   changes (only reviewed code is pinned; `-AllowUncommittedHooks` overrides) or
   when the template registers a hook missing from `hooks/`, and it never writes a
   settings.json with an unresolved `__TOKEN__`.
3. Updating a hook is the same act again — diff the source against the installed
   copy first (`git diff` won't show it; use `fc`/`Compare-Object`). A manual
   `Copy-Item` of one reviewed file remains fine for a single-hook update.
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
| `evidence_evacuate.py` | Stop + SessionEnd | **convenience** — Evidence Compiler measurement plan S0.1: copies any EC packet in `$CLAUDE_PROJECT_DIR`/payload `cwd` `.evidence-compiler/packets/` that `F:/Evidence-Archive/EC-LIVE` lacks, via `F:/Claude-Tools/evidence-archive/evacuate_worktree.py --apply` (sha256-verified, never deletes/overwrites, local-only). A packet counts as archived only when a byte-identical copy is in `packets/` or the evacuator's quarantine (a partial copy is retried). No-op = one listing + one stat per packet (a packet is hashed only against a same-size archive copy). One shared 25 s budget covers the pre-check hashing and evacuator calls across checkouts (a checkout it cannot finish checking is a surfaced deferral); one checkout's failure never skips the rest. Always exit 0 (a Stop hook exiting 2 would block the stop); any failure is surfaced as a `systemMessage`, not just logged; a SessionEnd failure is also appended as its own entry under `evidence_evacuate.pending/` and shown by the next Stop, which deletes only the entries it has printed (an interrupted Stop keeps them; an unreadable entry is shown, never dropped). SessionEnd also sweeps the repo root + every `<root>/.claude/worktrees/*` (the `--sweep` backstop's caller); `--sweep <repo-root>...` exits 1 on failure. Opt-out `EVIDENCE_EVACUATE=0`. Self-test: `python hooks/tests/test_evidence_evacuate.py` or `pytest hooks/tests` (needs the real evacuator; `EVIDENCE_TEST_EVACUATOR` overrides its path, a missing one fails). | `~/.claude/hooks/` |
| `graphify_hint.py` | SessionStart (`startup\|resume\|clear\|compact`) + SubagentStart + UserPromptSubmit | **advisory** — resolves the canonical graphify graph for the payload `cwd` (cwd walk, repo subfolders like `backend\`, worktree → main checkout; pinned copy of `skills/graphify/scripts/resolve_graph.py`) and, only when one resolves, injects the graph path, the logged wrapper command, and when to prefer it over Grep/Read. No graph → no output. Skips shell-less subagent types. UserPromptSubmit is a fallback for harness paths that fire no SessionStart (desktop forks — 2026-10-05 funnel session 07542a44 — and missed `SessionStart:compact`): after a graph resolves, it scans `transcript_path` back to the last `compact_boundary` and injects the session hint only if no `hook_additional_context` attachment there carries `<graphify-graph>` (no graph → transcript never read; unreadable transcript → inject). Diagnostic log: one JSONL record per invocation in `~/.claude/hooks/logs/graphify_hint.jsonl` (`GRAPHIFY_HINT_LOG_DIR` overrides), rotated to one `.1` at 1 MiB (≤ ~2 MiB total); fields `ts, event, source, agent_type, session_id, cwd, resolved, graph, transcript_read, suppressed_prior_hint, emitted, skipped, error` — never prompt text, transcript content, or exception messages (type + errno/char offset only); separate from the graphify query log; best-effort, written after emission. Fail-open, exit 0. Opt-out `GRAPHIFY_HINT=0`. Self-test (incl. resolver parity): `python hooks/tests/test_graphify_hint.py`. | `~/.claude/hooks/` |
| `session_closure_audit.py` + `closure_audit_repos.json` | SessionStart + SessionEnd | **advisory** — read-only closure audit: surfaces uncommitted/unpushed design docs, untracked branches, and dirty worktrees across the repos in `closure_audit_repos.json` each session, so knowledge is never lost by deleting a worktree that holds it. Local-only (git status/worktree list; no push/commit/network); fail-open. | `~/.claude/hooks/` (pinned copies of both files; this repo is the source of truth as of 2026-08-21 — update via the C1 copy procedure above) |

## Registered outside this repo (observed 2026-10-10; open, not fixed)

Comparing the installed `~/.claude/settings.json` with `settings.template.json` on
2026-10-10 found 17 live hook registrations against 14 in the template. The 14 match
the inventory above. Three are registered only in the local settings, with no source,
template entry, or pinned copy here:

| Live registration | Event | Runs from |
|---|---|---|
| `F:/BIMpossible-Workspace/system/scheduled-tasks/audit-watchdog/Show-AuditAlerts.ps1` | SessionStart (`startup\|resume\|clear`) | BIMpossible-Workspace checkout |
| `F:/Claude-Tools/evidence-capture/capture_start.py` | UserPromptSubmit | Claude-Tools checkout |
| `F:/Claude-Tools/evidence-capture/capture_end.py` | Stop | Claude-Tools checkout |

Consequences, stated as observed:
- They run from switchable git checkouts, which the C1 rule above forbids: a branch
  switch or pull in either repo changes code that runs every session or turn.
- `bootstrap.ps1` does not install them. Its merge keeps registrations the template
  does not own, so they survive on this machine, but a fresh machine never gets them.

Open decisions (owner): for each, pin it here under C1 (source + template entry +
copy), keep it deliberately unpinned and record that exception, or remove it. Until
decided, the C1 rule is not met for these three; nothing here has changed their
registration or behaviour.

No `context_budget` hook is registered, live or in the template: its registration was
removed on 2026-08-07 (`docs/adr/0002-defer-context-budget-automation.md`).

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
