# 0002 — Context-budget automation deferred; ctxdex is artifact retrieval, not context management

- **Status:** Accepted
- **Date:** 2026-08-07
- **Supersedes:** —

## Context

Two adjacent capabilities were built in the same period: `ctxdex` auto-capture
(durable recall of large Bash output) and a `context_budget.py` hook (automatic
truncation/rewriting of tool results in-flight). A payload-shape fix for the
latter merged to master (#3) without the lane's deferral being revisited —
technically valid, still scope expansion.

## Decision

The automatic context-management lane is deferred. #3 was reverted from master
(#6) and the live `PostToolUse` registration for `context_budget.py` was removed
from `~/.claude/settings.json`. The **fixed** implementation is retained in git
history (`d255b38`, `0e119ec`) for intentional future reintroduction — the old
buggy version is never to be restored, and the hook is never re-registered
without an explicit decision to advance the lane. `ctxdex` remains scoped to
durable artifact retrieval: it indexes as a side effect but never alters,
blocks, or rewrites what the session sees.

## Alternatives considered

- Keep #3 merged since the fix was correct — rejected: a completed merge is not implicit approval of scope.
- Revert live behavior to the buggy version — rejected: deferral means "off", not "broken".
- Delete the fixed implementation — rejected: recoverability costs nothing; the work is sound.

## Consequences

- No automatic context-budget capability is active or carried on master.
- Reintroduction has a clean starting point: history + this record.
- Context discipline stays manual (context-budget skill, LONG-TASK-HARNESS) until the lane is deliberately advanced.
