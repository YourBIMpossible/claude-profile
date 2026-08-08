---
name: slop-audit
description: >-
  Repo-agnostic anti-slop code audit — silent-catch census, counter-integrity,
  tested-but-dead. Pass the repo path as the argument. Findings only, no fixes.
  Add-Ins-specific depth stays in /revit-functionality-audit.
---

# Slop audit — "reports success, quietly did nothing"

Hunts the AI-introduced defect class that reads clean in a skimmed diff: code that
swallows failure, over-counts success, or tests a dead twin. Ported from
`revit-functionality-audit`'s house-defect hunt (where it caught the same bug class
≥4 times); this version is language-generic and runs on any repo.

## Scope & args

- **Argument = repo path** (e.g. `/slop-audit F:\AI-Dev\PC-Monitor`). No argument →
  ask which repo; never guess.
- `complete` / `full` after the path → FULL scan. Default is **INCREMENTAL**:
  window = commits since the newest `audits/*__slop-audit*.md` in that repo (no
  prior report → FULL). State the window at the top of the report.
- Read-only against the target repo. Findings-report output, **no fixes in the same
  pass** — fixes are a separate explicit follow-up.
- Routing: Add-Ins → use `/revit-functionality-audit` (deeper, Revit-aware).
  BIMpossible → `/audit` already embeds this checklist; running this standalone
  there is redundant.

## The three checks

1. **Silent-catch census.** Every swallowed-failure site in the window, classified
   *justified-and-logged* / *justified-but-silent* / *swallows-a-real-failure*.
   Output is a table (appendix), not vibes.
2. **Counter-integrity.** For every loop or batch ending in "N succeeded / N done /
   all clean", trace that every `continue`, early return, and caught exception
   increments a skip-or-error counter that actually reaches the final message. A
   success total that silently excludes failures is the house defect.
3. **Tested-but-dead.** New/changed tests must exercise the SHIPPED code path —
   grep the tested functions/types for production callers. Green tests over a dead
   twin actively mislead.

**Hypothesis discipline:** every finding ships labeled `HYPOTHESIS` with a proposed
verification step (run it, test it, trace the caller) and is only promoted to
`VERIFIED` after that step runs. Severities are hypotheses too.

## Per-language swallow patterns (check 1 targets)

- **Python:** bare `except:` / `except Exception: pass`; `subprocess` return codes
  unchecked; `errors="ignore"`; `contextlib.suppress` over broad types;
  logging-at-debug what should surface.
- **TS/JS:** empty `catch {}`; floating promises (no `await`, no `.catch`);
  `.then()` chains without rejection handling; error events unsubscribed.
- **C#:** `catch { }` / `catch (Exception) { }`; `async void`; fire-and-forget
  `Task`s; event-chain hops where a dead hop looks like success.
- **PowerShell:** `-ErrorAction SilentlyContinue`; empty `catch {}`; native calls
  with `$LASTEXITCODE` never read; `try/catch` around non-terminating errors that
  never enter the catch (the 5.1 trap).

## Output contract

- Report: `<repo>\audits\YYYY-MM-DD__slop-audit.md` (create `audits\` if absent;
  same-day rerun appends `-2`). Findings by severity, each with `file:line`,
  `HYPOTHESIS|VERIFIED|REFUTED`, and its verification step. Silent-catch census
  table as an appendix.
- Chat reply: link + one line per severity count. Deliverable goes to the file,
  never pasted into chat.
