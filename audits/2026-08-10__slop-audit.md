# Slop audit — claude-profile

- **Date:** 2026-08-10
- **Window:** INCREMENTAL — no prior `audits/*__slop-audit*.md`, so commits since 2026-08-03 (25 commits, `1a32b36`..`679e06f`).
- **Scope:** silent-catch census, counter-integrity, tested-but-dead. Read-only, findings only.
- **In-window executable code:** `hooks/*.py` (`northstar_inject.py`, `protect_paths_gate.py`, `ctxdex_autoindex.py`, `decay_reminder.py`, `context_budget.py`, `reground_reminder.py`, `loop_grant_gate.py`), `hooks/tests/*.py`, `tools/context_weight.py`, `skills/graphify/scripts/*.py` (`query_reranked.py`, `measure_latency.py`, `bench_reranker.py`). Non-code (skills markdown, `parked-skills/` fonts+references, ADR docs) excluded from the three checks.

## Verdict

Clean. No CRITICAL/HIGH/MEDIUM, no LOW. No findings.

The hooks' broad `except Exception → sys.exit(0)` sites look like the swallow pattern at a glance but are correct by design and documented inline: **advisory** hooks (`northstar_inject.py:93-96,125-126`, `decay_reminder.py:28-30`, `ctxdex_autoindex.py:307,324`) MUST fail open — a broken hook must never block a user's prompt or tool call — and each carries a comment saying so. The one **security** gate inverts this: `protect_paths_gate.py` fails **closed** (`:68` unreadable payload → block, `:90` absolute backstop → block), which is the right polarity for a guard. This is exactly the discipline the audit wants; the fail-open/fail-closed split is deliberate and matched to each hook's role.

### Check 1 — silent-catch census

See appendix. Every in-window catch is *justified-and-documented*. No swallow hides a real failure that should surface.

### Check 2 — counter-integrity

`ctxdex_autoindex.py` and the bench/latency scripts emit measurements, not "N succeeded" batch tallies. `tools/context_weight.py` sums a standing-context budget and reports the total honestly (no per-item skip that silently drops from the sum — no swallow sites found). No success-total that excludes failures.

### Check 3 — tested-but-dead

`hooks/tests/test_protect_paths_gate.py`, `test_ctxdex_hook.py`, and `test_context_budget.py` drive the hooks as subprocesses (`subprocess.run([sys.executable, HOOK, ...])`) against the real hook files — they exercise the shipped entrypoints, not twins. `skills/graphify/scripts/test_query_reranked.py` tests `query_reranked.py`, the live opt-in reranker path (`GRAPHIFY_RERANK=1`). No dead-twin coverage.

## Findings

None.

## Appendix — silent-catch census (in-window, representative)

| File:line | Pattern | Classification |
|---|---|---|
| `protect_paths_gate.py:68` | `except → block (fail-closed)` | justified — correct guard polarity |
| `protect_paths_gate.py:90` | `except → block (backstop)` | justified — correct guard polarity |
| `northstar_inject.py:45,69,93,125` | `except → sys.exit(0)` | justified — advisory hook must fail open (documented) |
| `decay_reminder.py:28` | `except → sys.exit(0)` | justified — advisory, documented |
| `ctxdex_autoindex.py:125,160,307,324,327` | `except → fail open` | justified — advisory, documented |
| `query_reranked.py:232` | `except RuntimeError → stderr + return 1` | justified-and-logged (honest non-zero exit) |
