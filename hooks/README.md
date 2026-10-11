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
| `northstar_inject.py` | SessionStart (`startup\|resume\|clear`) + UserPromptSubmit | **advisory** — injects the active locked mission(s) at session start and re-injects them each turn (scope-governor v3, multi-star) | `~/.claude/hooks/` |
| `skill-detection-hook.json` | PostToolUse (Revit MCP) | prompt-type, enabled 2026-08-08 | n/a (JSON in settings.json) |
| `evidence_evacuate.py` | Stop + SessionEnd | **convenience** — Evidence Compiler measurement plan S0.1: copies any EC packet in `$CLAUDE_PROJECT_DIR`/payload `cwd` `.evidence-compiler/packets/` that `F:/Evidence-Archive/EC-LIVE` lacks, via `F:/Claude-Tools/evidence-archive/evacuate_worktree.py --apply` (sha256-verified, never deletes/overwrites, local-only). A packet counts as archived only when a byte-identical copy is in `packets/` or the evacuator's quarantine (a partial copy is retried). No-op = one listing + one stat per packet (a packet is hashed only against a same-size archive copy). One shared 25 s budget covers the pre-check hashing and evacuator calls across checkouts (a checkout it cannot finish checking is a surfaced deferral); one checkout's failure never skips the rest. Always exit 0 (a Stop hook exiting 2 would block the stop); any failure is surfaced as a `systemMessage`, not just logged; a SessionEnd failure is also appended as its own entry under `evidence_evacuate.pending/` and shown by the next Stop, which deletes only the entries it has printed (an interrupted Stop keeps them; an unreadable entry is shown, never dropped). SessionEnd also sweeps the repo root + every `<root>/.claude/worktrees/*` (the `--sweep` backstop's caller); `--sweep <repo-root>...` exits 1 on failure. Opt-out `EVIDENCE_EVACUATE=0`. Self-test: `python hooks/tests/test_evidence_evacuate.py` or `pytest hooks/tests` (needs the real evacuator; `EVIDENCE_TEST_EVACUATOR` overrides its path, a missing one fails). | `~/.claude/hooks/` |
| `graphify_hint.py` | SessionStart (`startup\|resume\|clear\|compact`) + SubagentStart + UserPromptSubmit | **advisory** — resolves the canonical graphify graph for the payload `cwd` (cwd walk, repo subfolders like `backend\`, worktree → main checkout; pinned copy of `skills/graphify/scripts/resolve_graph.py`) and, only when one resolves, injects the graph path, the logged wrapper command, and when to prefer it over Grep/Read. No graph → no output. Skips shell-less subagent types. UserPromptSubmit is a fallback for harness paths that fire no SessionStart (desktop forks — 2026-10-05 funnel session 07542a44 — and missed `SessionStart:compact`): after a graph resolves, it scans `transcript_path` back to the last `compact_boundary` and injects the session hint only if no `hook_additional_context` attachment there carries `<graphify-graph>` (no graph → transcript never read; unreadable transcript → inject). Diagnostic log: one JSONL record per invocation in `~/.claude/hooks/logs/graphify_hint.jsonl` (`GRAPHIFY_HINT_LOG_DIR` overrides), rotated to one `.1` at 1 MiB (≤ ~2 MiB total); fields `ts, event, source, agent_type, session_id, cwd, resolved, graph, transcript_read, suppressed_prior_hint, emitted, skipped, error` — never prompt text, transcript content, or exception messages (type + errno/char offset only); separate from the graphify query log; best-effort, written after emission. Fail-open, exit 0. Opt-out `GRAPHIFY_HINT=0`. Self-test (incl. resolver parity): `python hooks/tests/test_graphify_hint.py`. | `~/.claude/hooks/` |
| `session_closure_audit.py` + `closure_audit_repos.json` | SessionStart + SessionEnd | **advisory** — read-only closure audit: surfaces uncommitted/unpushed design docs, untracked branches, and dirty worktrees across the repos in `closure_audit_repos.json` each session, so knowledge is never lost by deleting a worktree that holds it. Local-only (git status/worktree list; no push/commit/network); fail-open. | `~/.claude/hooks/` (pinned copies of both files; this repo is the source of truth as of 2026-08-21 — update via the C1 copy procedure above) |
| `Show-AuditAlerts.ps1` | SessionStart (`startup\|resume\|clear`) | **advisory** — shows open audit-watchdog alerts at session start (reads `~/.claude/scheduled-tasks/_watchdog/{status,alerts}.json`; always exits 0). **Pinned copy of an upstream file** owned by BIMpossible-Workspace (`system/scheduled-tasks/audit-watchdog/`), pinned here under C1 on 2026-10-11 (F13b); byte-identical to upstream `5ef28dd`, sha256 `fef201c4736dc341670972fd6e78c863deac868f8cd3c9c9e2511fb75337a7c6`. Its only reference to the Workspace checkout is the text of the acknowledge command it prints. Updating = re-pin deliberately (see "Pinned copies of upstream files"); this repo does not edit it. Self-test: `pytest hooks/tests/test_bootstrap_install.py -k AuditAlerts`. | `~/.claude/hooks/` |

## Pinned copies of upstream files

`Show-AuditAlerts.ps1` is owned upstream (BIMpossible-Workspace audit-watchdog); the file
here is a reviewed, byte-identical pin, and bootstrap installs it like any other hook. A
machine that still registers the upstream-checkout path has that registration replaced by
the pinned one on the next bootstrap run (`$supersededHookPaths` in `bootstrap.ps1`), so the
hook runs once.

Re-pin when upstream changes (deliberate, reviewed act):
1. `fc` / `Compare-Object` the upstream file against `hooks/Show-AuditAlerts.ps1`.
2. Copy the upstream bytes over `hooks/Show-AuditAlerts.ps1`, update the sha256 and upstream
   commit in the inventory row, run `pytest hooks/tests/test_bootstrap_install.py`, open a PR.
3. After merge, run `bootstrap.ps1` (or `Copy-Item` that one file to `~/.claude/hooks/`).

Rollback of the 2026-10-11 pin (F13b): restore `~/.claude/settings.json` from
`settings.json.pre-f13b-backup-20261011` in `~/.claude/`, which re-registers the upstream
path; deleting `~/.claude/hooks/Show-AuditAlerts.ps1` is optional. Reverting this PR alone
does not touch a machine already bootstrapped.

## Registered outside this repo — shared temporary pinning exception

Two hooks are registered in the local `~/.claude/settings.json` and are **deliberately not
pinned here**. The evidence-capture subsystem is owned by Claude-Tools (`evidence-capture/`);
Claude-Profile neither copies nor modifies it. This is a documented, temporary exception to
C1 — not acceptance of the risk below.

| Live registration | Event | Timeout | Runs from |
|---|---|---|---|
| `F:/Claude-Tools/evidence-capture/capture_start.py` | UserPromptSubmit | 5 s | Claude-Tools checkout |
| `F:/Claude-Tools/evidence-capture/capture_end.py` | Stop | 25 s | Claude-Tools checkout |

Installation / execution contract (read from the code, 2026-10-11):
- Each script is a thin fail-open wrapper (exit 0, prints nothing) that does
  `sys.path.insert(0, <its own directory>)` and imports `pipeline` and `store`, which in turn
  import the rest of the subsystem (about 20 modules, ~10k lines). The wrapper cannot run
  from a copy by itself: **the installation unit is the whole `evidence-capture/` directory**,
  and the C1 pinned-copy mechanism (one file per hook under `~/.claude/hooks/`) does not fit it.
- The first `try` (the imports) returns 0 without logging. A checkout switch or edit that
  breaks an import therefore disables capture **silently**; only later failures reach the
  capture log.
- Anything that changes the Claude-Tools working tree (branch switch, pull, edit) changes the
  code that runs on every prompt and every Stop. Opt-outs exist (`EVIDENCE_CAPTURE=0`,
  `EVIDENCE_HOOK=0`).
- `bootstrap.ps1` does not install them and keeps them on re-run (covered by
  `test_upstream_audit_alert_registration_is_superseded_capture_hooks_are_kept`); a fresh
  machine would not get them. The upstream README still says "Nothing here registers a hook";
  that is stale relative to this machine and is Claude-Tools' to correct.

Nothing here changes either registration. Candidate resolutions (smallest first) are in the
Self Governance exception packet; any of them needs a grant covering the capture subsystem.

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
