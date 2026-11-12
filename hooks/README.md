# hooks — sources and installation rule

## Where hooks run (C1, revised 2026-10-22 by docs/adr/0005-hooks-run-from-the-clone.md)

**Hooks run straight from this clone** (`<clone>/hooks/*.py`, `<clone>/vendor/<unit>/`),
registered by absolute path in `~/.claude/settings.json`. No file in the repo names a
machine path: `settings.template.json` uses `__PROFILE_ROOT__` and `__PYTHON__`, which
`tools/profile_setup.py` fills from the clone's own location and the machine config
(`~/.claude/profile-machine.json`, `tools/machine_config.py`). Hooks keep their runtime
state (logs, pending records, locks) under `~/.claude/hooks/`, never in the clone.

How a change reaches a machine: merge to the default branch. At the next session start,
`session_start.py` fast-forwards the clone (changed hook code runs from then on) and
re-applies the settings merge (a changed registration applies from the session after).
No installer run. The original C1 rule pinned copies under `~/.claude/hooks` so that a
branch switch or `git pull` could not silently change code that runs on every prompt;
its replacement guard is that the clone tracks the merged default branch only:
- feature work happens in worktrees (`.claude/worktrees/<lane>`), never in the clone itself;
- `session_start.py` puts a warning into the session when the clone is on another branch
  or has uncommitted changes under `hooks/`, `vendor/`, `tools/` or the template;
- the only automatic update is `git pull --ff-only` of what was reviewed and merged.

Installing is `bootstrap.py` (all platforms; `bootstrap.ps1` on Windows calls it and adds
REA). It refuses before writing anything when the template registers a hook missing from
this clone, leaves an unresolved `__TOKEN__`, or the existing settings.json cannot be
merged safely. A new hook must be registered in the template in the same change —
`hooks/tests/test_bootstrap_install.py` fails when a hook ships unregistered. A handler
may carry `"platforms": ["win32"]` (sys.platform prefixes); it is dropped elsewhere.

Prompt-type hooks (JSON only, no code path — e.g. `skill-detection-hook.json`)
live entirely inside settings.json.

## Inventory

| Source | Event | Class | Runs from |
|---|---|---|---|
| `session_start.py` | SessionStart (`startup\|resume\|clear\|compact`; runs first) | convenience — `git pull --ff-only` of this clone under a non-blocking lock (losers log `pull skipped (in flight)`), then the settings re-sync, then the machine roots and clone warnings as session context; compact only re-prints the roots. Never blocks, always exit 0; record `~/.claude/hooks/last-run.log`. Opt-outs `CLAUDE_PROFILE_PULL=0`, `CLAUDE_PROFILE_SYNC=0`. Replaces `session-start-pull.ps1` and the Linux `session-start-pull.sh` port. | clone |
| `ctxdex_autoindex.py` | PostToolUse Bash | convenience | clone |
| `decay_reminder.py` | UserPromptSubmit | **advisory** reminder (C2) | clone |
| `protect_paths_gate.py` | PreToolUse Write\|Edit\|MultiEdit\|NotebookEdit | **control** — fail-closed (C3; live `NORTHSTAR*.md` edits raise an owner approval prompt, `*.draft.md` free) | clone |
| `ship_guard.py` | PreToolUse Bash\|PowerShell + the MCP merge, auto-merge and GitHub file-write tools | **control** — fail-closed. Replaces required checks (owner ruling 2026-11-02: no GitHub settings): refuses `gh pr merge`, the REST merge endpoint and MCP merges unless every check on the PR head is green; GraphQL merges, auto-merge on, and MCP file writes to GitHub always. Refuses `git push` in a repository whose `.githooks/pre-push` carries `ship-guard: gate` unless its `core.hooksPath` is that `.githooks` (fix: `python tools/git_gate.py`), and everywhere `--no-verify` / `commit -n`, `-c core.hooksPath=`, a `git config` that redirects or unsets it, and the `BIMP_PREPUSH_BYPASS` / `BIMP_SECRETS_BYPASS` variables. Self-test: `pytest hooks/tests/test_ship_guard.py`. | clone |
| `northstar_inject.py` | SessionStart (`startup\|resume\|clear`) + UserPromptSubmit | **advisory** — injects the active locked mission(s) at session start and re-injects them each turn (scope-governor v3, multi-star) | clone |
| `skill-detection-hook.json` | PostToolUse (Revit MCP) | prompt-type, enabled 2026-08-08 | n/a (JSON in settings.json) |
| `evidence_evacuate.py` | Stop + SessionEnd | **convenience** — Evidence Compiler measurement plan S0.1: copies any EC packet in `$CLAUDE_PROJECT_DIR`/payload `cwd` `.evidence-compiler/packets/` that `{repos}/Evidence-Archive/EC-LIVE` lacks, via `{tools}/evidence-archive/evacuate_worktree.py --apply` (sha256-verified, never deletes/overwrites, local-only). A packet counts as archived only when a byte-identical copy is in `packets/` or the evacuator's quarantine (a partial copy is retried). No-op = one listing + one stat per packet (a packet is hashed only against a same-size archive copy). One shared 25 s budget covers the pre-check hashing and evacuator calls across checkouts (a checkout it cannot finish checking is a surfaced deferral); one checkout's failure never skips the rest. Always exit 0 (a Stop hook exiting 2 would block the stop); any failure is surfaced as a `systemMessage`, not just logged; a SessionEnd failure is also appended as its own entry under `evidence_evacuate.pending/` and shown by the next Stop, which deletes only the entries it has printed (an interrupted Stop keeps them; an unreadable entry is shown, never dropped). SessionEnd also sweeps the repo root + every `<root>/.claude/worktrees/*` (the `--sweep` backstop's caller); `--sweep <repo-root>...` exits 1 on failure. Opt-out `EVIDENCE_EVACUATE=0`. Self-test: `python hooks/tests/test_evidence_evacuate.py` or `pytest hooks/tests` (needs the real evacuator; `EVIDENCE_TEST_EVACUATOR` overrides its path, a missing one fails). | clone |
| `verify_stop.py` | Stop | **advisory** — CP-VERIFY-STOP-HOOK. Reads the current turn from `transcript_path` (after the last genuine user prompt, sidechains excluded; last 4 MiB) and prints one `systemMessage` only when the turn changed code (Write/Edit/MultiEdit/NotebookEdit on a non-doc path, or a writing Bash/PowerShell command naming a code-extension file; `.md`/`.txt`/`.log`/`.yaml` and session-scratchpad files are exempt), no check command (pytest, npm test, dotnet build, tsc, ruff, …) ran after the last change, and the final message claims completion without disclosing the gap ("not verified", "untested"). Never blocks: no exit 2, no `decision` (blocking risks stop loops and fights act-then-report); `stop_hook_active` → silent. Regex heuristic, proportional by design: Q&A, read-only, doc-only and honest "not verified" turns stay silent. Replayed over the 150 most recent local transcripts on 2026-10-28: fired on 3, each a `sed -i` on data in a command that also named a script. Fail-open, exit 0, one `verify_stop:` stderr line on error. Opt-out `VERIFY_STOP_HOOK=0`. Self-test: `python hooks/tests/test_verify_stop.py`. | clone |
| `graphify_hint.py` | SessionStart (`startup\|resume\|clear\|compact`) + SubagentStart + UserPromptSubmit | **advisory** — resolves the canonical graphify graph for the payload `cwd` (cwd walk, repo subfolders like `backend\`, worktree → main checkout; pinned copy of `skills/graphify/scripts/resolve_graph.py`) and, only when one resolves, injects the graph path, the logged wrapper command, and when to prefer it over Grep/Read. No graph → no output. Skips subagents without a shell: built-in `claude-code-guide`/`statusline-setup`, and custom agents whose definition (`.claude/agents/*.md` from cwd upward, then `~/.claude/agents`) grants neither Bash nor PowerShell; no definition found → hint kept. UserPromptSubmit is a fallback for harness paths that fire no SessionStart (desktop forks — 2026-10-05 funnel session 07542a44 — and missed `SessionStart:compact`): after a graph resolves, it scans `transcript_path` back to the last `compact_boundary` and injects the session hint only if no `hook_additional_context` attachment there carries `<graphify-graph>` (no graph → transcript never read; unreadable transcript → inject). Diagnostic log: one JSONL record per invocation in `~/.claude/hooks/logs/graphify_hint.jsonl` (`GRAPHIFY_HINT_LOG_DIR` overrides), rotated to one `.1` at 1 MiB (≤ ~2 MiB total); fields `ts, event, source, agent_type, session_id, cwd, resolved, graph, transcript_read, suppressed_prior_hint, emitted, skipped, error` — never prompt text, transcript content, or exception messages (type + errno/char offset only); separate from the graphify query log; best-effort, written after emission. Fail-open, exit 0. Opt-out `GRAPHIFY_HINT=0`. Self-test (incl. resolver parity): `python hooks/tests/test_graphify_hint.py`. | clone |
| `session_closure_audit.py` + `closure_audit_repos.json` | SessionStart + SessionEnd | **advisory** — read-only closure audit (entries name repos by machine root, e.g. `{repos}/BIMpossible`; a repo this machine lacks is skipped unless the entry is `required`; optional `platform` scopes an entry to one OS): surfaces uncommitted/unpushed design docs, untracked branches, and dirty worktrees across the repos in `closure_audit_repos.json` each session, so knowledge is never lost by deleting a worktree that holds it. Local-only (git status/worktree list; no push/commit/network); fail-open. | clone (both files) |
| `Show-AuditAlerts.ps1` | SessionStart (`startup\|resume\|clear`) | **advisory** — shows open audit-watchdog alerts at session start (reads `~/.claude/scheduled-tasks/_watchdog/{status,alerts}.json`; always exits 0). **Pinned copy of an upstream file** owned by BIMpossible-Workspace (`system/scheduled-tasks/audit-watchdog/`), pinned here under C1 on 2026-10-11 (F13b); byte-identical to upstream `357cd9f` (re-pinned 2026-11-12, OD-274: 102 h stale threshold), sha256 `6a85b6ce2683204919c245c353b432b506e23926a2f61b765f11aef96bffe38e`. Its only reference to the Workspace checkout is the text of the acknowledge command it prints. Updating = re-pin deliberately (see "Pinned copies of upstream files"); this repo does not edit it. Self-test: `pytest hooks/tests/test_bootstrap_install.py -k AuditAlerts`. | clone |

## Pinned copies of upstream files

`Show-AuditAlerts.ps1` (Windows only) is owned upstream (BIMpossible-Workspace
audit-watchdog); the file here is a reviewed, byte-identical pin and runs from the clone
like any other hook. A machine that still registers the upstream-checkout path (any path
ending `/audit-watchdog/Show-AuditAlerts.ps1`) has that registration replaced by the
template's (`SUPERSEDED_SUFFIXES` in `tools/profile_setup.py`), so the hook runs once.

Re-pin when upstream changes (deliberate, reviewed act):
1. Compare the upstream file against `hooks/Show-AuditAlerts.ps1`.
2. Copy the upstream bytes over `hooks/Show-AuditAlerts.ps1`, update the sha256 and upstream
   commit in the inventory row, run `pytest hooks/tests/test_bootstrap_install.py`, open a PR.
3. Merge; every machine runs the new pin from its next session start.

## Pinned upstream unit — evidence-capture (option C, 2026-10-11)

`capture_start.py` (UserPromptSubmit, 5 s) and `capture_end.py` (Stop, 25 s) belong to the
Claude-Tools evidence-capture subsystem, which **Claude-Tools still owns and develops**. Their
unit is the whole directory (the wrappers import ~20 sibling modules plus
`evidence-footprints/shell_paths.py`), so it is pinned whole under `vendor/` and the template
registers `vendor/evidence-capture/capture_*.py` in this clone. Legacy registrations of a
claude-tools checkout's `evidence-capture/capture_*.py`, or of the copies earlier bootstraps
installed under `~/.claude/hooks/evidence-capture/`, are superseded, so each hook runs once and
never follows the Claude-Tools checkout. Config (`~/.claude/evidence-capture.json`), stores and
evidence are not copied and not changed.

Provenance (upstream commit, `tool_sha256`, per-file sha256), the re-pin procedure and
rollback: `vendor/README.md`. Running inside this clone, manifests record this clone's commit
as `tool_commit`; `tool_sha256` identifies the pin. Tests: `CleanInstall` in
`hooks/tests/test_bootstrap_install.py` (registration once, imports resolve from `vendor/`,
`tool_sha256`, a real dry capture from the vendored unit).

No `context_budget` hook is registered, live or in the template: its registration was
removed on 2026-08-07 (`docs/adr/0002-defer-context-budget-automation.md`).

## Control vs advisory (from the fail-open lesson)

Two **controls**, fail-closed (block on its own failure — malformed payload, missing
field, any exception → exit 2): `ship_guard.py` (see its row above) and `protect_paths_gate.py` (protected paths on Write/Edit/MultiEdit/NotebookEdit,
plus an owner-approval prompt on every live north-star mission file — `NORTHSTAR.md` and
`NORTHSTAR.<slug>.md`; `*.draft.md` is free; since 2026-10-10, see below). Everything else is advisory or
convenience — a failure there changes nothing about safety.

Scope honesty:
- **ship_guard**: binds Claude sessions only (the owner's web-UI merge and own terminal are untouched, by design), and sees only what the command text shows: an alias, a script file or an encoded command that pushes or merges is not seen. The pre-push hook, the gate it runs (`tools/gate.py`) and CI are the layers behind it.
- **protect_paths_gate**: covers only the file-writing tools Write, Edit, MultiEdit and
  NotebookEdit (one matcher in `settings.template.json`). **The Bash channel (`sed -i`,
  redirection, `tee`, `Set-Content`, `python -c`) is NOT covered and is advisory-only** —
  never describe Bash-side protection as a control.
- **northstar_inject** (scope-governor v3, multi-star — 2026-08-15; full model in
  `docs/northstar-multi-design.md`): ADVISORY. A project may carry MANY north stars
  (`NORTHSTAR.md`, `NORTHSTAR.<slug>.md`); each file's own owner-approved `status:` line
  (absent = active; `paused`/`closed` = inactive) decides what injects — no renames, no
  magic location. Each turn the hook re-reads from disk all ACTIVE stars in the closest
  walk-up directory holding one, and injects them + the three-doors routing rule, so the
  plan cannot drift through a rolling context summary. Fail-open sites (an unreadable
  active star, an unexpected error) still exit 0 but write one `northstar_inject:` line to
  stderr naming the file or exception type. It does not enforce — the matching
  enforcement is protect_paths_gate turning every Write/Edit on a live `NORTHSTAR*.md`
  into an owner approval prompt (Claude edits, the owner approves; `*.draft.md` is
  free). The prompt fires only in permission modes where it is reliable (`ASK_MODES`
  in the gate: default, acceptEdits, plan, auto); in bypassPermissions, dontAsk, or an
  unknown/missing mode the edit is hard-blocked instead. acceptEdits has not been
  live-tested. Bash writes are not covered. Whether an extra is a tag-along / roadmap item /
  mission-change is the model's judgment; a hook cannot classify scope, so this is not
  dressed up as a control. v2 (single hard-coded `NORTHSTAR.md`) superseded the G6
  loop-grant gate + G7 reground reminder (removed 2026-08-08); v3 removed v2's
  one-mission-per-tree limit.
