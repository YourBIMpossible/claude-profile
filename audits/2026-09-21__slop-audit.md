# Slop audit — {profile} — 2026-09-21

**Window:** incremental, commits since the prior report (`audits/2026-09-13__slop-audit.md`, commit `0352b9d`) → HEAD `08a3bbf`.
**Checks:** silent-catch census · counter-integrity · tested-but-dead. Read-only; findings only, no fixes.

## Code in window

Non-doc source touched (docs/`.md`/notes excluded as non-auditable for this pass):

- `tools/context_weight.py` — full v1→v2 rewrite (scenario-aware standing-context budget).
- `tools/tests/test_context_weight.py` — rewritten to match (28 cases).
- `hooks/decay_reminder.py` — one-line reminder-string edit (file-link rule). No logic.

## Verdict: CLEAN — 0 findings

No swallowed failure, no over-counting success total, no tested-but-dead twin.

Notably, `context_weight.py` is itself a *fix* for this audit's house-defect class rather than a source of it:

- **Counter-integrity (inverted correctly):** `scenario_total()` returns `None` (reported as `INCOMPLETE`) whenever any required source is `MISSING`/`UNMEASURED`; it never sums an unmeasured input as `0` and never reports "clean" over a partial measurement (`run_scan`, exit-code escalation at `context_weight.py:721`). The v1 defect this replaces (2026-08-31 MEDIUM-1: every input optional → all-absent totalled 0 and printed CLEAN) is explicitly carried forward as a regression guard.
- **Tested-but-dead: refuted.** The CLI entry `main()` calls `run_scan()`/`render_report()` — the exact functions the 28 tests drive. Verified green this run: `python tools/tests/test_context_weight.py` → `OK - 28 cases` (exit 0). Tests pass throwaway `tempfile` estates, never the real `~/.claude`.
- **Silent-catch census:** see appendix. Two broad-but-benign catches, both over strictly optional/decorative paths (git-sha metadata, local trend history); neither can mask a required-source failure or inflate a success verdict.

## Appendix — silent-catch census

| file:line | pattern | class | note |
|---|---|---|---|
| `tools/context_weight.py:333` | `except Exception: pass` → `return None` (`_git_sha`) | justified-but-silent | git short-SHA is decorative metadata on a history record; absence is a valid `None`, not a masked failure. |
| `tools/context_weight.py:770` | `except OSError: return []` (`_read_history`) | justified-but-silent | unreadable local (gitignored) trend file degrades to "no prior history → baseline"; non-gating, cannot affect the budget verdict. |
| `tools/context_weight.py:769` | `except json.JSONDecodeError: continue` (`_read_history`) | justified-and-commented | a corrupt history line is skipped per its own inline comment; trend-only, non-gating. |
| `tools/context_weight.py:319` | `except (OSError, json.JSONDecodeError)` (`_enabled_plugins`) | justified | error is *returned* and surfaced as an `UNMEASURED` required source → forces exit 2, never silent. |

All silent-catch sites are over optional/non-gating paths; the required-source measurement path fails loud by construction.
