# Slop audit — F:\Claude-Profile

**Run:** 2026-08-31 (scheduled weekly, incremental)
**Window:** commits since the previous report `audits/2026-08-24__slop-audit.md` — 12 commits (`79f22c2`..`0c9052c`)
**Scope:** the three checks (silent-catch census, counter-integrity, tested-but-dead) over changed code only.
**Read-only.** Findings, no fixes.

## Changed surface in the window

| File | Change |
|---|---|
| `tools/context_weight.py` | path fix (b640675) + threshold recalibration (44622eb) |
| `hooks/session_closure_audit.py` + `hooks/tests/test_closure_audit_status_failure.py` | fix for last week's HIGH (0b8ee30) |
| `parked-skills/design-system/scripts/generate-slide.py` | HTML-escaping fix pulled from upstream (afa0097) |
| `skills/**/SKILL.md`, `parked-skills/**/SKILL.md`, `skills-provenance-report.md` | prose/frontmatter only — out of scope for the three checks |

---

## MEDIUM-1 — `context_weight.py` reports "CLEAN — within all budgets" when it measured nothing

**Status: VERIFIED**
`tools/context_weight.py:53,86,95` — every input is absent-tolerant with no signal:

- `skill_descriptions()` returns `[]` when `SKILLS_DIR` is missing (`:53`)
- `CLAUDE_MDS` entries that don't exist contribute 0 and are never reported (`:86`)
- `PROJECTS_DIR.glob("*/memory/MEMORY.md")` yielding nothing is indistinguishable from "all indexes are small" (`:95`)

Zero inputs therefore produce `total = 0`, which is below every threshold, so the tool prints
the success line and exits 0.

**Verification run (this audit):** loaded the module, repointed `SKILLS_DIR`, `PROJECTS_DIR` and
`CLAUDE_MDS` at nonexistent paths, called `main()`:

```
standing context: 0 bytes (~0 tokens) | descriptions 0 | CLAUDE.md 0 | skills 0
CLEAN — within all budgets
exit code: 0
```

**Why this one matters more than usual:** it has already happened in this file. Commit `b640675`
("context_weight: fix stale lookup paths") records that `MEMORY_MD` was hardcoded to
`projects/F--BIMpossible/memory/MEMORY.md`, "nonexistent for current sessions" — i.e. the memory
budget silently contributed 0 for an unknown period while the tool kept reporting clean. The path
was fixed; the *silence* that hid it was not. The tool's entire job is catching drift, and its
inputs are absolute Windows paths in a repo estate that was re-rooted twice this month
(`F:\AI-Dev` extraction, `F:\Claude-Tools` split).

**Current state is healthy** — the live run reports 48104 bytes / 24 skills and all three
`CLAUDE.md` paths resolve — so this is a latent hole, not an active miss.

**Proposed fix (not applied):** treat a missing input as a FAIL, not a zero. Concretely: exit 2 if
`SKILLS_DIR` is not a directory, if any `CLAUDE_MDS` entry does not exist, or if the `MEMORY.md`
glob matches nothing — each with the path in the message.

---

## Check 3 — tested-but-dead: PASS (no dead twin)

`hooks/tests/test_closure_audit_status_failure.py` loads `hooks/session_closure_audit.py` by path
and monkeypatches `run_git`, so it exercises the real functions.

The relevant risk in this repo is the pinned-install topology: `~/.claude/hooks/*.py` are the
copies the harness actually executes, and the repo copy is what the tests import. **Verified
byte-identical for all five installed hooks:**

| Hook | repo vs `~/.claude/hooks` |
|---|---|
| `session_closure_audit.py` | identical |
| `protect_paths_gate.py` | identical |
| `northstar_inject.py` | identical |
| `decay_reminder.py` | identical |
| `ctxdex_autoindex.py` | identical |

No dead twin. Last week's fix is live, and this session's own `SessionStart` closure-audit output
confirms the hook is running.

---

## Check 3b — `generate-slide.py` escaping: complete, no partial-fix gap

The usual slop pattern after a pulled security fix is partial coverage. Checked every
interpolation site in `parked-skills/design-system/scripts/generate-slide.py`:

- 0 remaining `{data.get(...)}` interpolations outside `_e()` / `_safe_url()`
- no un-escaped loop-variable interpolations into HTML
- the two remaining raw interpolations are safe: `{title}` is escaped at the call site
  (`:652`, `title=escape(str(title))`) and `{int(b.get('value', 0))}` is coerced to `int` (`:564`)

Note: this script lives in `parked-skills/`, which is not surfaced to any session — the fix has no
live blast radius either way.

---

## Appendix — silent-catch census (changed files in window)

| Site | Pattern | Classification |
|---|---|---|
| `tools/context_weight.py:110` | `except (OSError, json.JSONDecodeError)` around `settings.json` | justified-and-logged (appends a WARN) |
| `tools/context_weight.py:53` | `if not SKILLS_DIR.is_dir(): return []` | **swallows a real failure** — MEDIUM-1 |
| `tools/context_weight.py:86` | `if p.is_file()` skip | **swallows a real failure** — MEDIUM-1 |
| `tools/context_weight.py:95` | empty glob | **swallows a real failure** — MEDIUM-1 |
| `hooks/session_closure_audit.py:92,99` | `return None` on non-zero exit / any exception | justified-and-logged (caller emits ATTENTION REQUIRED) — this is last week's fix |
| `generate-slide.py` | none | — |

## Severity summary

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 1 |
| LOW | 0 |
