---
name: revit-functionality-audit
description: >-
  On-demand code-correctness/behavior audit of the Revit ADD-INS repo
  (F:\AI-Dev\Add-Ins) — the Add-Ins counterpart of /audit, which covers only the
  BIMpossible web repo. Default = incremental since the last report in audits/;
  "complete" = full top-to-bottom. Behavior only — UI/UX belongs to
  /revit-tool-review, which routes its CODE findings here. Findings-report output,
  no fixes in the same pass.
---

# Revit Add-Ins Functionality Audit

**Scope:** `F:\AI-Dev\Add-Ins` — `BIMpossible.RevitLink` (Commands, ModelHealth, Assistant,
FamilyFixer, Shared, App/ribbon), `BIMpossible.ModelQA.Core`, the six discipline add-ins,
all test suites, CI/deploy layer. **Not** the BIMpossible web repo (that's `/audit`) and
**not** UI/UX quality (that's `/revit-tool-review` — it routes CODE findings here).

## Mode
- Default **INCREMENTAL**: window = every commit since the newest
  `audits/*__*audit-report*.md`'s stated end-commit (or its date). State the window
  (`<sha>..origin/main`, N commits) at the top of the report.
- `complete` / `full` in args → **FULL** top-to-bottom, same method, no window.
- A prepared briefing for a window supersedes self-scoping — check `audits/` for a
  `*audit-PLAN*` doc covering the window first (e.g.
  `2026-07-25__incremental-audit-PLAN-since-521e612.md`) and execute it as written.

## Non-negotiable method (each rule exists because its absence shipped a wrong audit)

1. **Phase 0 — build-truth gate.** Build BOTH TFMs (`net48`, `net8.0-windows`) from the
   audited ref before any static claim. The 07-12 audit's only CRITICAL was a "net48 build
   break" that one clean build disproved. Also record whether CI builds
   `BIMpossible.RevitLink.csproj` itself; if not, re-raise it.
2. **Hypothesis discipline.** Every finding ships labeled `HYPOTHESIS` with a proposed
   verification step (build / test / live repro) and is only promoted to `VERIFIED` after
   that step runs. Severities are hypotheses too — the last audit's false positives were
   its highest-severity rows.
3. **Unit-scoped parallel readers.** Split the window into audit units by subsystem, sized
   by risk (new subsystems and selection/scope churn first). Each reader answers three
   standing questions for its unit: *what does this code do on failure, who gets told, and
   does any success counter include the skipped items?*
4. **The house-defect hunt — "reports success, quietly did nothing"** (recurred ≥4 times
   after the last audit closed; assume it shipped again):
   - **Silent-catch census:** every `catch {}` / `catch { /*…*/ }` in the window, classified
     *justified-and-logged* / *justified-but-silent* / *swallows-a-real-failure*. Output is
     a table (appendix), not vibes.
   - **Counter-integrity:** for every loop ending in "N succeeded/done", trace that every
     `continue` / early-return / caught exception increments a skip-or-error counter that
     reaches the final message. (The Retag Rooms bug was one `continue` that didn't.)
   - **Async/event chain tracing:** for async and event-driven flows (`async void`,
     `ExternalEvent.Raise()`, watchers, SSE/streams), walk the chain hop-by-hop asking
     "if this hop dies, what does the user see?" An `async void` exception vaporizes; a
     dropped `Raise()` silently serves stale data; a broken stream can look identical to a
     completed one.
5. **Tested-but-dead check (H-10):** new/changed tests must exercise the SHIPPED code path
   — grep the tested types for production callers. Green tests over a dead twin actively
   mislead.
6. **Static vs live split, stated per finding.** TFM/API-version behavior, Revit runtime
   behavior (transactions, dialogs, events), and anything Phase-0-adjacent must be
   verified by building/running — never by reading. List the live-in-Revit checks as a
   numbered owner-runnable script in the report.
7. **Read-only.** No fixes, no deploys in the audit pass. Fixes are a separate, explicit
   follow-up (matching the existing report → resolution-doc convention).

## Output contract (match the house pattern in `audits/`)
- `audits/<YYYY-MM-DD>__incremental-audit-<window>.md` — findings by severity
  (CRITICAL/HIGH/MEDIUM/LOW/INFO), each with file:line, `HYPOTHESIS|VERIFIED|REFUTED`
  status, and its verification step. Cross-cutting themes up top (§0 style).
- Appendices: silent-catch census table; the live-check script; carry-forward status of
  the prior resolution doc's DEFERRED items.
- On close (separate pass): a `*__audit-resolution.md` companion where EVERY finding gets
  a decision — fixed / won't-fix-with-reason / deferred-with-reason / false-positive.
  No fuzzy rows. False positives are recorded prominently so nobody "re-fixes" them.

## Standing context
- Prior full audit pair: `audits/2026-07-12__audit-report-full.md` + `__audit-resolution.md`
  (CLOSED 2026-07-14; its lessons are baked into the method above).
- Its closing truth, kept: **"The audit is not a correctness guarantee. Trust what you see
  in Revit over what a report says."**
