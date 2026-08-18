# Audit — BIMpossible\CLAUDE.md

Date: 2026-08-06 · Auditor: Claude (harness-tuning session) · Target: `F:\AI-Dev\BIMpossible\CLAUDE.md`
Size: 227 lines / 18.2 KB · Read-only audit — **no edits made to the BIMpossible repo.**

## Verdict

The file is high quality in content and wrong in composition. Almost nothing in it is
*false*; the problem is that roughly half of it is **completed-status reporting and CI
policy reference**, not standing orders. Every line loads on every BIMpossible session
regardless of what the session is doing — a frontend tweak pays for 40 lines of scanner
gate policy, a Revit-link task pays for the firm-alias migration history.

Target shape: **~100 lines of true standing orders**, with status and reference material
moved to the workspace docs this file already links to. That is a ~55% cut with zero loss
of enforceable rules.

The test applied to every section: *would a session that violates this line produce broken
or unsafe work?* If yes → standing order, keep. If it merely informs → move to a linked doc.

## Section-by-section

| Lines | Section | Verdict | Note |
|---|---|---|---|
| 3–7 | Protected files (`.env`, `guard.py`) | **KEEP verbatim** | Safety rail, 5 lines. Highest value/line in the file. |
| 9–16 | Write-back rules (Phase 3+) | **KEEP** | Authorization + tenancy invariant. Load-bearing. |
| 18–32 | APS hub isolation | **TRIM 15→10** | Rule (18–28) is essential. Lines 29–32 ("As of 2026-08-06 the /aps surface is wired…") are status → PHASE-STATUS ledger. Keep the test-file pin. |
| 34–37 | Phase numbering | **TRIM 4→2** | The Phase≠Wave key and the VOID list are load-bearing. The full 12-phase map is reference data already in the canonical guide it links to. |
| 39–40 | General (personal IP, no telemetry) | **KEEP** | 2 lines, absolute constraint. |
| 42–43 | Working style → WORKING-STYLE.md | **DELETE** | Superseded 2026-08-06: those rules now load globally from `claude-profile\CLAUDE.md`. See "Cross-file duplication" below. |
| 45–46 | Backend layering pointer | **KEEP** | Correct pattern — one line of rule, one link. |
| 48–61 | Frontend deploy / Refresh-Frontend.ps1 | **KEEP, tighten ~20%** | Incident-driven (2026-05-26 deploy gap; `--no-deps` dependency chain). The "never call it done until rebuilt" line prevents a recurring false-verified failure. |
| 63–66 | Superpowers gating | **DELETE — dead** | superpowers/pr-review-toolkit/plugin-dev were uninstalled 2026-08-06. This instructs sessions about skills that no longer exist. |
| 68–72 | Debugging & sequencing discipline | **TRIM 5→3** | "Observe before theorize" and the handoff-doc rule are keepers. "Stop and re-plan" now duplicates a global rule; `bim-investigate` carries its own iron law. |
| 74–84 | Automatic agent gates (table) | **KEEP** | Mechanical, unambiguous, table-shaped. Best-designed section in the file. ⚠ verify the three agents still exist — see anomalies. |
| 86–133 | Firm-parameter neutrality | **TRIM 48→12** | **Largest single win.** The rule (no firm literals; register in `param_aliases.py`/`clientRules.ts`; ratchet gate enforces) is ~10 lines. The other ~38 are finished-migration status: 105 sites/15 modules, baseline 126→21, the history blockquote, the no-deferral rule, and three separate "as of 2026-08-06 this is COMPLETE" statements. That belongs in the linked plan doc. |
| 135–142 | Spec library test hygiene | **KEEP, tighten 8→5** | Real trap (a category with no library today gains one next wave). Sentinels are the actionable part. |
| 144–227 | GitHub workflow | **SPLIT 84→~22** | **Second-largest win.** Daily rules stay: main protected, branch→PR→squash, required checks, Push-And-Verify, stacked-PR rebase trap, SMB-sandbox warning, `feat/*` single-owner rule, model-page `ssr:false` perf rule. CI *gate policy* moves out: pip-audit, trivy, semgrep, npm-audit, Actions allowlist, security-tooling-wave summary (~45 lines) — needed only when touching CI, and the section already links to `BIMpossible_GitHubWorkflow.md`. |

## The four biggest wins

1. **Firm-neutrality status → linked plan doc.** −36 lines. The stack is complete; the file says so three times.
2. **CI gate policy → GitHubWorkflow.md.** −45 lines. Reference material, consulted rarely, loaded always.
3. **Delete Superpowers gating.** −4 lines, and it removes an instruction that is now actively wrong.
4. **APS/phase status lines → PHASE-STATUS.md.** −7 lines. Status has a ledger; CLAUDE.md is not it.

## Cross-file duplication

- **Worktree rule appears three times**: here (174–179), in `claude-profile\CLAUDE.md` (global house
  rule), and in `BIMpossible_Workspace\WORKFLOW.md` (canonical). Keep the canonical + the global
  one; the repo copy can shrink to the branch-naming specifics that are genuinely BIMpossible-only.
- **Working style appears twice**: `F:\AI-Dev\system\WORKING-STYLE.md` (2026-05-30, referenced by
  both BIMpossible and AI-Server) now overlaps the global "How to work with me" section. Not
  contradictory, but two homes for one policy is how drift starts. Recommend folding WORKING-STYLE
  into the global section and deleting both pointers.
- **GitHub workflow appears three times**: here, `BIMpossible_GitHubWorkflow.md`, `WORKFLOW.md`.

## Anomalies to verify (not fixed here)

- **Line 55 carries a future date**: "(discovered 2026-08-07)" — tomorrow. The same future-stamp
  appeared on a memory file today. Likely a UTC-vs-local clock offset rather than an error, but if
  something is writing tomorrow's date into dated rules, dated rules stop being trustworthy.
- **Three agents referenced, none verifiable from here**: `backend-endpoint-reviewer`,
  `migration-reviewer`, `typescript-api-reviewer` (lines 80–82). They would live in
  `BIMpossible\.claude\agents\` and only register when a session's cwd is that repo. If any has
  been renamed or removed, that table silently instructs sessions to run nothing. Confirm from a
  BIMpossible session.
- **Two repo-scoped skills exist** (`bimpossible-breach-chain`, `synthetic-concurrency-audit`) that
  this file never mentions. Not a defect — but if a session is meant to reach for them at specific
  moments, the file is the place that would say so.

## What was not changed

Nothing. This is analysis only; the BIMpossible repo was not modified, and per the shared-tree
rule no branch or commit was made there. Executing this audit is a separate, repo-scoped job —
it should run from a BIMpossible worktree so the edit lands on a branch with a PR.
