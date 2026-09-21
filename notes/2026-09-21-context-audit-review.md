# Context audit — status review, 2026-09-21

Scope: what the audit line (manual audit 2026-08-08 + `tools/context_weight.py`) has
done, where it stands, and how the scan could be better. Source: the 08-08 note, the
script and its git history, and one run today. **Nothing was cleaned in today's
session** — the only action was running the weekly scan (read-only).

## 1. Where we started (2026-08-08 manual audit)

Standing context measured then: ~25.5 KB / ~6,400 tokens.

| Item | Size then |
|---|---|
| Skill descriptions (16 personal) | 8,769 chars |
| CLAUDE.md (global + AI-Dev) | 6,667 chars |
| MEMORY.md index (56 files) | 10,084 chars |

Findings: the new tools (ctxdex, ctxcheck, skillspector, context-budget, hooks) cost
only ~650 tokens net. The real overweight was four Revit skill descriptions
(3,971 chars = 45% of description budget), unused plugins (hookify,
cowork-plugin-management), and a growing MEMORY.md.

## 2. What was cleaned (per the 08-08 note, "EXECUTED same day", commit 2454b7d)

1. Four Revit skill descriptions rewritten to trigger + sibling routing (~700 tok).
2. hookify + cowork-plugin-management disabled. Verified today: enabled plugins are
   exactly the 6 in `PLUGIN_ALLOWLIST`.
3. Consolidate-memory pass on MEMORY.md — recorded as executed; see §3, it has regrown.
4. Earlier baseline (08-06): superpowers / pr-review-toolkit / plugin-dev uninstalled.

Then the scan was automated (`context_weight.py`, weekly scheduled task) and hardened:
`b640675` fixed stale paths, `87d86bf` made a missing input FAIL instead of reading as
zero, `e93f1aa` repointed to canonical roots, `01e6c1b` made an unreadable MEMORY.md
UNMEASURED instead of a crash. `44622eb` recalibrated two thresholds (see §4).

## 3. Where we are now (today's run: exit 0, "CLEAN")

Total 43,481 B (~10,870 tok), vs 25.5 KB on 08-08 — **+70%**.

| Item | Now | 08-08 |
|---|---|---|
| Personal skill descriptions (15 skills) | 4,558 | 8,769 (down: trimming held) |
| CLAUDE.md (Claude-Profile + BIMpossible) | 19,029 (limit 20,000) | 6,667 |
| MEMORY.md worst-case (derived: total − others) | ~19,894 (warn 20,000) | 10,084 |

CLEAN only because everything sits just under thresholds:
- CLAUDE.md is 971 B from WARN; MEMORY.md is ~106 B from WARN. Both nearly doubled+ since 08-08.
- The task file's stated target is ≤32 KB; we are at 43.5 KB and pass because
  `TOTAL_WARN` was raised to 56,000 on 2026-08-24 (owner-approved). The written target
  and the enforced threshold disagree by 24 KB.

## 4. Weaknesses in the scan (how to improve it)

1. **Blind spots — the biggest.** It counts 15 personal skills only. This session's
   skill list shows ~50 (anthropic-skills ×10, claude-md-management, code-review,
   loop, schedule, claude-api, artifact-*, etc.). Their descriptions load every
   session and are unmeasured. Also unmeasured: MCP server instructions and the
   deferred-tool name list (~250 names), agent-type descriptions, the
   `~/.claude/CLAUDE.md` wrapper, and **SessionStart hook output** (closure-audit
   emitted ~47–51 KB, persisted to a file; only a ~2 KB preview enters context, but it
   is per-session and untracked).
2. **Threshold drift.** `TOTAL_WARN` and `CLAUDEMD_WARN` were raised rather than the
   content trimmed. Add a trend check: WARN if total grows >10% versus the last run.
3. **No history.** Append each run to `context_weight.log.jsonl` (date, per-source
   bytes). "CLEAN" then shows slope, not just a snapshot.
4. **Proximity, not just breach.** WARN at 90% of any ceiling (CLAUDE.md and MEMORY.md
   would both fire today).
5. **Scope mismatch.** BIMpossible/CLAUDE.md loads only in that repo, but is summed
   into every session's total. Report per-scenario (global-only vs in-BIMpossible).
   Also reported MEMORY.md is the max across projects, not the session's actual one.
6. **Bytes vs tokens.** `bytes//4` is a rough proxy; fine for trend, weak for absolute.
7. **Weekly-task output.** Exit 0 hides the near-limit state. Print the top 3 items
   by size and % of ceiling even when clean.

## 5. What is left

| # | Item | Action |
|---|---|---|
| 1 | MEMORY.md ~19.9 KB, regrowing | Run `/consolidate-memory` (user's call) |
| 2 | CLAUDE.md 19 KB (was 6.7 KB) | Trim: the global file is now long; move rationale to on-demand docs |
| 3 | Plugin/MCP/agent descriptions unmeasured | Extend scan (§4.1) before trusting "CLEAN" |
| 4 | 32 KB target vs 56 KB threshold | Decide which is real; align file/task text |
| 5 | Untracked `skills/schedule-run/`, `audits/2026-09-13__slop-audit.md` in Claude-Profile | Not touched here; outside scope |

Assumed: MEMORY.md figure is derived by subtraction (script does not print it).
