# 2026-08-06 — Harness tuning session (closed)

Mandate: stop the context/compaction bleed — a session was compacting 4-5× inside one
action. Everything below is shipped; this file exists for the open verification and the
queue of things this session deliberately did NOT handle.

## Shipped (commit 1a32b36, local only — not pushed)

- `LONG-TASK-HARNESS.md` — phases, anchor docs, checkpoints, session lifecycle, intake
  discipline. Triggered by a pointer line in `F:\AI-Dev\CLAUDE.md`.
- `skills/close` — on-demand closeout audit (`/close status` read-only, `/close` executes).
- `skills/tdd` — replaces the uninstalled superpowers TDD skill.
- 10 design/brand skills parked to `parked-skills/` (~0.8k/session).
- `F:\AI-Dev\CLAUDE.md` — act-then-report default, four CONFIRM gates, ~10-line report
  cap, deliverables-to-files, context-discipline bullet, anti-wander bullet.
- `settings.json` — `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 150000 → 200000.
- Memories: `context-slimdown-2026-08-06`, `long-task-harness-adopted`,
  `feedback-act-then-report`.

## Why it appeared suddenly (not recorded elsewhere)

Threshold effect, three factors at once: the 150k ceiling was set (likely by a recent
token-optimization lane — unproven), the startup floor drifted ~50k → 68k on plugin/skill
creep, and the Aug 4-5 marathon sessions were the heaviest workload yet. Working room per
cycle = ceiling − floor, so ~82k. Historical baseline for comparison: 8 compactions total
across the 25 biggest prior sessions.

## Open — verification

- [ ] Next fresh session should report ~49-58k of 200k at startup. If it reads materially
      higher, the floor did not drop and the slimdown needs re-measuring.

## Queue — found, NOT handled (belongs to other lanes / the owner)

- ~~`hooks/context_budget.py` + `hooks/context_budget.error.log` are uncommitted WIP in this
  shared tree.~~ Resolved 2026-08-07, commit 0e119ec — see
  `notes/2026-08-07-context-budget-hook-fix.md`. Known gap still open: Agent (subagent)
  results bypass the spill hook — mitigation is behavioral (subagents write findings to a
  file, return ≤10 lines + path).
- `skills/context-budget/`, `skills/dashboard-update/`, `skills/revit-sheet-view-manager/`
  are untracked in this tree — same lane question, not this session's to commit.
- Commit `1a32b36` is unpushed. Pushing leaves the machine = CONFIRM gate; owner's call.
- Connector disconnects in app settings (~3.3k of deferred MCP tool names) — user-side.
- SkillSpector could watch `settings.json` for drift. Offered, not taken.
