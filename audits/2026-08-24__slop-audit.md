# Slop audit — Claude-Profile

- **Date:** 2026-08-24
- **Repo:** `{profile}`
- **Mode:** INCREMENTAL
- **Window:** commits since the previous report `audits/2026-08-17__slop-audit.md` → 12 commits (`39d08a1`..`32eaae9`), 13 changed code files
- **Checks:** silent-catch census, counter-integrity, tested-but-dead
- Read-only. Findings only — nothing was fixed.

## Summary

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 0 |
| LOW | 1 |

The hooks in this repo are unusually well-defended against this defect class — truncation,
unreadable repos, and time-budget overrun all surface as explicit `ATTENTION REQUIRED`
findings, and the `_run_concurrently` docstring states the "missing index ≠ clean" contract
outright. One gap survives that contract.

---

## HIGH-1 — `git status` failure renders as "clean. No action needed."

- **File:** `hooks/session_closure_audit.py:83` (`run_git`) → `:122` (`parse_status`) → `:438`
- **Status:** **VERIFIED**
- **Check:** silent-catch census

`run_git` returns `[]` for every failure mode — non-zero exit, `GIT_TIMEOUT` expiry,
`FileNotFoundError`, any exception (`except Exception: return []`, line 92). `parse_status`
consumes that empty list and returns its fully-defaulted dict: `branch=None`, `ahead=0`,
`behind=0`, `tracked_changed=[]`, `untracked=[]`.

That dict is not `None`, so `build_report`'s truncation guard does not fire:

```python
wt_truncated = st is None and not wt.get("unreadable")   # :431 → False
```

`audit_worktree` then finds nothing to report and the line printed is:

```
  - <path> [<branch>] — clean. No action needed.
```

A worktree whose status check **failed** is byte-identical in the report to one that is
genuinely clean. The `unreadable` path (`:267`) only covers a failure of the *first* call
(`git worktree list`); a failure of the *per-worktree* `git status` — index.lock contention
from a concurrent session, a slow repo hitting `GIT_TIMEOUT`, a transient git error — lands
in this gap. This is precisely the guarantee the hook's own header comment claims to uphold
("a truncated run must never look identical to a clean one").

**Verification performed:** loaded the module, monkeypatched `run_git` to return `[]` for
every call, and invoked the real `parse_status` + `audit_worktree`:

```
parse_status on failed git -> {'branch': None, 'detached': False, 'upstream': None,
                               'ahead': 0, 'behind': 0, 'tracked_changed': [], 'untracked': []}
findings -> []
```

Empty findings ⇒ the "clean. No action needed." branch. Hypothesis confirmed.

**Proposed fix direction (not applied):** have `run_git` distinguish failure from empty
output (return `None` on failure, `[]` on genuine empty), and have `parse_status` propagate
that as a `"status_failed": True` flag that `audit_worktree` turns into an
`ATTENTION REQUIRED` finding — the same treatment `truncated` already gets at `:293`.

---

## LOW-1 — hook-level `except Exception: pass` in `emit` / `read_event`

- **File:** `hooks/session_closure_audit.py:64`, `:73`; same pattern in
  `hooks/northstar_inject.py:70,88,136` and `hooks/ctxdex_autoindex.py:313,327`
- **Status:** HYPOTHESIS (assessed **justified-but-silent**, not a defect)
- **Check:** silent-catch census

These are the outermost fail-open boundaries of SessionStart hooks. Swallowing here is the
correct posture — a hook must never break session startup — and the repo's C1/C2/C3 pinned-
install design depends on it. Recorded for census completeness, not as a defect. The only
cost is that a hook that dies during its own emit leaves no trace at all; a stderr breadcrumb
would make silent hook death diagnosable without changing the fail-open contract.

**Verification step if promoted:** raise inside `emit` under a live SessionStart and confirm
nothing anywhere records that the hook ran and failed.

---

## Check 2 — counter-integrity

No violations found in the window.

`build_report` (`:418`–`:457`) is a positive example: `truncated_repos` and
`truncated_worktrees` are incremented at every skip site and both reach the final verdict
string, which appends *"treat this run as incomplete, not clean."* The success total cannot
silently exclude failures. `hooks/tests/test_closure_audit_worktree_convention.py` covers the
convention classifier.

## Check 3 — tested-but-dead

No violations found. The three changed test files exercise shipped entry points:

| Test file | Exercises | Production caller confirmed |
|---|---|---|
| `hooks/tests/test_protect_paths_gate.py` | `hooks/protect_paths_gate.py` via `subprocess.run` on the real file | yes — registered Write\|Edit gate |
| `hooks/tests/test_ctxdex_hook.py` | `hooks/ctxdex_autoindex.py` + the real `ctxdex` CLI | yes |
| `hooks/tests/test_closure_audit_worktree_convention.py` | `classify_worktree_location` in the shipped module | yes — called at `session_closure_audit.py:281` |

All three drive the shipped file rather than a copied twin.

---

## Appendix — silent-catch census (window)

| File:line | Pattern | Classification |
|---|---|---|
| `hooks/session_closure_audit.py:64` | `except Exception: pass` (emit) | justified-but-silent |
| `hooks/session_closure_audit.py:73` | `except Exception: pass` (read_event) | justified-but-silent |
| `hooks/session_closure_audit.py:92` | `except Exception: return []` | **swallows-a-real-failure** (HIGH-1) |
| `hooks/session_closure_audit.py:146` | `except Exception: pass` (branch.ab parse) | justified-but-silent |
| `hooks/session_closure_audit.py:225` | `except Exception: r = None` (thread runner) | justified-and-logged — `None` is read as "not checked" |
| `hooks/session_closure_audit.py:479,481` | `except SystemExit` / `except Exception` (main) | justified-but-silent — fail-open boundary |
| `hooks/northstar_inject.py:70,88,103,136,178,225,227` | broad except → `pass` | justified-but-silent — fail-open hook |
| `hooks/protect_paths_gate.py:94` | `except SystemExit` | justified — re-raise path for the fail-closed gate |
| `hooks/ctxdex_autoindex.py:125,160,313,322,327` | broad except → `pass` | justified-but-silent — fail-open hook |
| `tools/context_weight.py:106` | `except (OSError, json.JSONDecodeError) as e` | justified-and-logged |
| `skills/graphify/scripts/query_reranked.py:232` | `except RuntimeError as e` | justified-and-logged |
| `bootstrap.ps1:13` | `Get-Content -ErrorAction SilentlyContinue` | justified — absence is the expected case |
