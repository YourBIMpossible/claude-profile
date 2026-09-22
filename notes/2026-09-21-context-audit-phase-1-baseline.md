# Context audit — Phase 1 baseline, 2026-09-21

Tool: `tools/context_weight.py`, schema v2. Measurement only — nothing in
this run trims, edits, or otherwise touches any content source. This
document is the required baseline report for Phase 1 of the context-budget
audit improvement program; it supersedes the v1 single-total numbers in
[2026-09-21-context-audit-review.md](2026-09-21-context-audit-review.md)
and the original [2026-08-08-context-audit.md](2026-08-08-context-audit.md).

## 1. What changed in the tool (why v1's numbers were wrong)

v1 reported one flattened total and derived MEMORY.md "by subtraction"
(worst-case guess, never a direct read). v2 fixes three defects:

1. **MEMORY.md is now measured directly**, per project, never derived.
2. **Two independent scenario totals** — `global_session` (loads in every
   session, everywhere) and `bimpossible_session` (global_session + what
   additionally loads inside `F:\BIMpossible`) — so BIMpossible's own
   CLAUDE.md/MEMORY.md are no longer charged to every session.
3. **A full coverage inventory**, not just "15 personal skills measured,
   everything else invisible." Every source is one of MEASURED / UNMEASURED
   / NOT_APPLICABLE / MISSING; a *required* source that's UNMEASURED or
   MISSING blocks a clean verdict — an *optional/hosted* one (harness-
   injected content this tool has no file to read) is visible in the
   inventory but never gates.

Three budget lines, not one: 32,000B design target (informational only,
never a gate or deletion quota), 45,000B warning, 56,000B hard ceiling —
per scenario.

## 2. Scenarios measured

- `global_session` — every session, everywhere.
- `bimpossible_session` — global_session plus BIMpossible's own CLAUDE.md
  and MEMORY.md, which load only inside `F:\BIMpossible`.

## 3. Source inventory

6 measured, 6 unmeasured (all optional/hosted — see §5), 0 missing, 0
not-applicable.

| Source | Bytes | % of its ceiling | Scenario(s) |
|---|---|---|---|
| global memory index (Claude-Profile MEMORY.md) | 15,272 (97 lines) | 61% of 25,000 | global_session (corrected §12; originally listed as "both") |
| BIMpossible project memory index | 14,827 (59 lines) | 59% of 25,000 | bimpossible_session |
| project CLAUDE.md (F:\BIMpossible\CLAUDE.md) | 12,202 | 61% of 20,000 | bimpossible_session |
| global CLAUDE.md (wrapper 763B + profile 8,399B) | 9,162 | 46% of 20,000 | both |
| personal skill descriptions (15 skills) | 4,558 | 46% of 10,000 | both |
| enabled-plugin skill descriptions (1 skill / 6 plugins) | 338 | 6% of 6,000 | both |

## 4. Live baseline totals (this run)

**Corrected 2026-09-21 by the pre-Phase-2 load-model verification pass —
see §12.** The original run below counted Claude-Profile's MEMORY.md
(15,272B) into `bimpossible_session`. That was an unverified assumption,
not a measurement; §12 checked it against evidence and found it false. The
figures in this table are the corrected ones from the second, verified
run.

| Scenario | Total | % of 56,000B ceiling | Status |
|---|---|---|---|
| `global_session` | 29,330 B (~7,332 tok) | 52% | **CLEAN** |
| `bimpossible_session` | 41,087 B (~10,271 tok) | 73% | **CLEAN** (exceeds the 32,000B design target — informational only, not a gate) |

A session working inside `F:\BIMpossible` does not start over any real
budget line: it exceeds only the informational 32KB design target (73% of
the 56KB hard ceiling, well under the 45KB warning line). The original
101%/FAIL reading in this section — kept below in §12 as superseded, not
deleted — double-counted Claude-Profile's MEMORY.md into a scenario it
never actually loads into; see §12 for the full evidence trail.

Exit code: **0 (CLEAN)**, confirmed by a live re-run after the correction
(§12, step 3).

## 5. Top three measured contributors

1. Claude-Profile MEMORY.md — 15,272B (~3,818 tok) — 61% of ceiling — global_session only (corrected §12; originally listed as "both scenarios")
2. BIMpossible MEMORY.md — 14,827B (~3,706 tok) — 59% of ceiling — bimpossible_session
3. BIMpossible CLAUDE.md — 12,202B (~3,050 tok) — 61% of ceiling — bimpossible_session

All three of the biggest single costs are BIMpossible-scoped or a MEMORY.md
index — exactly the two categories v1 could never measure honestly (see §1).

## 6. Unmeasured / not-applicable

All 6 unmeasured sources are **optional** (never gate the verdict) and are
harness/client-injected content this offline tool has no local file to
read:

| Source | Why unmeasured |
|---|---|
| plugin/hosted skill catalog beyond local | Skills in the available-skills list with no local SKILL.md or cached plugin (anthropic-skills bundle: docs, docx, pptx, xlsx, pdf, code-review, simplify, dataviz, claude-api, artifact-design, cowork-plugin-management, and others). Shipped by the client itself. |
| MCP server instructions | Per-connected-server instruction blocks; size depends on session-local connector state. |
| deferred-tool catalog | ~200–250 deferred tool names + schemas fetched via ToolSearch; harness-injected at runtime. |
| agent-type descriptions | The Agent tool's available-agent-types listing; harness-injected. |
| SessionStart closure-audit: injected preview | The hook (`session_closure_audit.py`) emits raw, untruncated text; truncation to a preview happens client-side, after the hook returns — not local repo configuration. See §7 for the evidence behind treating this as UNMEASURED rather than a fixed byte count. |
| SessionStart closure-audit: full backing artifact | Written to an ephemeral per-session path outside repo control; excluded from every scenario total because — per the preview finding — it is not what actually loads into standing context. |

0 missing, 0 not-applicable this run.

## 7. SessionStart preview-vs-artifact: the evidence

`session_closure_audit.py` (read in full for Phase 1) has no local
truncation logic — it writes raw UTF-8 straight to `sys.stdout.buffer`
wrapped in `<closure-audit>...</closure-audit>`. Across sessions observed
during this audit, the artifact the hook actually produced ran 46.7KB,
50.7KB, and 50KB, while the text that entered the model's context was
consistently truncated to roughly the first 2KB ("Preview (first N KB)").
Since the truncation point isn't derivable from anything in this repo, it
is reported as UNMEASURED with the pattern documented, rather than
asserted as a fixed number the tool can't actually verify. The full
artifact is excluded from every scenario total for the same reason: it is
not what loads into standing context, so counting it would overstate the
real cost.

## 8. Trend / history status

**First run under schema v2 — this is the baseline.** No prior v2-schema
history existed before this run; `tools/context_weight.history.jsonl`
(gitignored, machine-local) now holds a record from the original run
(`bimpossible_session_bytes: 56359`, pre-correction) and a second record
from the corrected re-run (§12) with `bimpossible_session_bytes: 41087`.
The gap between them is the scenario-membership fix, not real growth or
shrinkage of any tracked content — the tool's >10% growth-only trend check
did not (and should not) flag it, since it only fires on increases. Anyone
reading this history file directly should treat the first record as
measured under the pre-correction (incorrect) scenario model. All future
runs compare against the most recent prior record and warn at >10% growth,
either on a scenario total or on any of the six principal per-source keys.

## 9. Content-quality classification

Applied to every measured contributor (§3) and every unmeasured source
judged likely to be high-impact if it could be measured (§6). Taxonomy:
`keep_always_on` / `keep_but_deduplicate` / `defer_action_triggered` /
`scope_by_project` / `scope_by_path_or_tool` / `consolidate` / `retire` /
`runtime_required` / `unmeasured`.

| Source | Classification | Note |
|---|---|---|
| Claude-Profile MEMORY.md (15,272B) | **consolidate** | Largest single contributor and growing fastest (10,084B on 2026-08-08 → 15,272B now, +51%); a `/consolidate-memory` pass was already flagged as owner's-call open work in the 2026-09-21 review note. |
| BIMpossible MEMORY.md (14,827B) | **consolidate** + `scope_by_project` | Already correctly scoped (bimpossible_session only, per v2's fix) — but also large enough to be a consolidation candidate in its own right. |
| BIMpossible CLAUDE.md (12,202B) | `scope_by_project` | Correctly scoped already; size (61% of its own 20,000B ceiling) was mistakenly read as the cause of a bimpossible_session FAIL in the original run — that FAIL was a measurement error, corrected in §12, not this file's fault. Still a trim candidate on its own ceiling, not a scoping problem. |
| global CLAUDE.md, wrapper+profile (9,162B) | `keep_always_on` | Needed in every session; well under its ceiling (46%). No action indicated by this measurement alone. |
| personal skill descriptions (4,558B / 15 skills) | `keep_always_on` | Trigger/routing text, already trimmed per the 2026-08-08 audit; well under ceiling (46%). |
| enabled-plugin skill descriptions (338B) | `keep_always_on` | Trivial cost (6% of ceiling). |
| plugin/hosted skill catalog beyond local | `runtime_required` / `unmeasured` | Harness-shipped; not a repo-level lever. Real cost, permanently invisible to an offline tool — the biggest standing blind spot per the 2026-09-21 review (§4.1 there). |
| MCP server instructions | `runtime_required` / `unmeasured` | Session-connector-dependent; not a fixed cost to measure or budget against. |
| deferred-tool catalog | `runtime_required` / `unmeasured` | Harness-injected; same category. |
| agent-type descriptions | `runtime_required` / `unmeasured` | Harness-injected; same category. |
| SessionStart injected preview (~2KB pattern) | `runtime_required` / `unmeasured` | See §7. Real, recurring, per-session cost with no local source of truth for its exact size. |
| SessionStart backing artifact | `unmeasured`, excluded by design | Not standing context (§7) — correctly excluded from every total, not merely unmeasured. |

No source in this run was classified `keep_but_deduplicate`,
`defer_action_triggered`, `scope_by_path_or_tool`, or `retire` — those
categories describe problems this baseline didn't find evidence of (no
observed duplication between the wrapper and profile CLAUDE.md content, no
tool/path-scoped source that should be but isn't, nothing recorded as
already scheduled for removal).

## 10. Preserve list (identified, not modified — Phase 1 is read-only)

Per the task's explicit boundary, the following were located and read for
context but are unmodified and out of scope for this or any Phase-1 work:

- **Canonical global autonomy policy and stop-gate list** —
  `F:\Claude-Profile\CLAUDE.md`'s "Stop only for: …" block (irreversible
  actions, anything leaving the machine, guarded files, another session's
  live work, scope creep, mission-lock changes).
- **Authorization/approval boundaries** — the harness system prompt's
  Prohibited / Explicit-permission-required / Regular action categories.
- **Security/credential constraints** — `~/.claude/settings.json`'s
  `autoMode.environment` / `soft_deny` block: BIMpossible protected
  branches, secrets locations, sensitive remote targets, soft-deny rules
  for `git push` on BIMpossible's `main` and `docker compose up -d`
  without `--build`.
- **Destructive-action / deployment safeguards** — the `protect_paths_gate.py`
  PreToolUse hook on Write|Edit.
- **Tenant/hub isolation** — not evaluated in this pass; no scope in the
  measured estate touches it.
- **Active user preferences / unresolved decisions** — the `F:\AI-Dev`
  deferred-legacy silent-by-default gate; the file-link-delivery rule
  fixed 2026-09-21 (repo CLAUDE.md + memory; the live hook copy at
  `C:\Users\Zeria\.claude\hooks\decay_reminder.py` still needs the same
  fix applied directly — flagged separately, not part of Phase 1); any
  north-star `status:` lifecycle lines (none present in this repo).

None of the above were read for the purpose of changing them — only to
confirm this audit's file-touch boundary didn't cross into them.

## 11. What Phase 1 does not do

No content was trimmed, consolidated, retired, or otherwise remediated.
§9's classifications are findings for a human decision, not actions taken.
Per the task boundary, Phase 2 (remediation) does not start from this
document — it starts from a separate, explicit go-ahead.

## 12. Load-model verdict — pre-Phase-2 verification, 2026-09-21

Before any Phase 2 memory cleanup could be justified by §4's original
FAIL reading, this pass checked the one assumption that reading rested
on: that `F:\Claude-Profile\MEMORY.md` actually loads into a Claude Code
session rooted at `F:\BIMpossible`. It does not begin, and did not
perform, Phase 2 remediation itself.

**Verdict: disproven.** Claude-Profile's MEMORY.md does not load into a
BIMpossible-rooted session. §4 and §5 above have been corrected
accordingly; nothing else in this report changed as a result.

**Evidence.** None of the five acceptable evidence types for cross-project
loading were found, and the memory system's actual scoping mechanism was
positively identified:

1. **No config names the file as global/user memory.**
   `C:\Users\Zeria\.claude\CLAUDE.md` (the always-loaded wrapper) contains
   exactly one `@`-import — `@F:/Claude-Profile/CLAUDE.md` — and no
   reference to any `MEMORY.md` path anywhere in its text.
   `F:\Claude-Profile\CLAUDE.md` (what that import pulls in) likewise
   contains no reference to any `MEMORY.md` path. `F:\BIMpossible\CLAUDE.md`
   contains zero `@`-import lines and zero occurrences of the string
   "MEMORY" (checked directly, `grep -n MEMORY` / `grep -n '^@'`, both
   empty). `~/.claude/settings.json` contains zero occurrences of the
   string "memory" (case-insensitive grep, empty); its one
   `Claude-Profile`-related entry is `permissions.additionalDirectories`,
   which grants filesystem *access* to that path for tool calls — not a
   content-injection or memory-loading directive (this is exactly the
   "directory name" / "file existence" non-proof the task's evidence
   standard excludes).
2. **No local hook reads or injects Profile memory into every session.**
   Every SessionStart/UserPromptSubmit/PostToolUse hook registered in
   `~/.claude/settings.json` was checked by source
   (`session-start-pull.ps1`, `northstar_inject.py`,
   `session_closure_audit.py`, `ctxdex_autoindex.py`): zero occurrences of
   `MEMORY.md`, `memory/MEMORY`, `memory_path`, "user memory", or "profile
   memory" in any of them.
3. **No `@import`/include expands Profile memory into BIMpossible's
   always-loaded sources** — covered by evidence item 1 above (no
   `@`-import of any `MEMORY.md` path exists anywhere in the checked
   files).
4. **A documented local runtime contract does exist — and it establishes
   the opposite of cross-loading.** This very session's own system prompt
   (the harness's "auto memory" section) states the memory system lives at
   `C:\Users\Zeria\.claude\projects\<encoded-session-cwd>\memory\` — keyed
   to *this session's own* encoded working directory, not to a fixed
   global path. That is confirmed structurally: `~/.claude/projects/`
   holds one directory per distinct encoded working directory this
   machine has used (`F--Claude-Profile`, `F--BIMpossible`, and many
   others), and `F--Claude-Profile/memory/MEMORY.md` (15,272B, ~70 topic
   files, all Claude-Profile/tooling-specific) and
   `F--BIMpossible/memory/MEMORY.md` (14,827B, ~55 topic files, all
   BIMpossible-specific) are two independently populated files with zero
   filename overlap — the observable behavior of per-project isolation,
   not shared/global loading.
5. **No directly observable runtime artifact records the file as loaded
   in a BIMpossible session** — none was found; this is why the verdict is
   "disproven" (evidence against loading, from items 1–4 above) rather
   than "unmeasured" (no evidence either way).

**Scenario treatment.**
- `global_session`: unchanged, 29,330B, CLEAN (52% of the 56,000B
  ceiling). Claude-Profile's MEMORY.md correctly stays in this scenario —
  it does load in a Claude-Profile-rooted session (this one).
- `bimpossible_session` (measured): 56,359 − 15,272 = **41,087B**, status
  recalculated: 32,000B design target exceeded (informational only, never
  a gate — see §1) but the 45,000B warning line and the 56,000B hard
  ceiling are both **not** breached → **CLEAN**.
- No conditional total is shown (that display is only for Outcome C,
  unmeasured — this is Outcome B, disproven, so no such figure applies).
- Fix applied: `tools/context_weight.py`, the `global_memory_bytes`
  source's `scenarios` tuple changed from `SCENARIOS` (both) to `(GLOBAL,)`
  — one line, plus an explanatory comment citing this section. No other
  source, threshold, or scenario definition changed.

**Remaining uncertainty.** Both memory indexes remain legitimate
consolidation candidates on size and growth alone (§9) — Claude-Profile's
MEMORY.md grew +51% between 2026-08-08 and this baseline, and a
`/consolidate-memory` pass was already flagged as owner's-call open work
independent of this finding. What this pass withdraws is only the *hard-
ceiling-breach* claim for `bimpossible_session`; it does not claim either
file is well-sized, and does not recommend against a future consolidation
pass on its own merits. This verification also did not re-examine the six
UNMEASURED/optional harness-injected sources in §6 (plugin/hosted skill
catalog, MCP instructions, deferred-tool catalog, agent-type descriptions,
SessionStart preview/artifact) — those remain out of scope for this pass,
unchanged from §6.
