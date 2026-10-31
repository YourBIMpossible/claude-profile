# Anti-Slop Code Audit — {profile}

**Date:** 2026-09-07
**Mode:** Incremental (read-only, findings only — no fixes, no commits)
**Window:** commits since the last report `audits/2026-08-31__slop-audit.md`, i.e. `--since="2026-08-31"`. Four commits touch the audited files:

| Commit | Subject |
|---|---|
| `87d86bf` | fix(context-weight): treat a missing measurement input as FAIL, not zero |
| `6a0ab7e` | fix(graphify): harden reranked-query telemetry — corpus parity, truthful timing, visible failures |
| `e93f1aa` | fix: read canonical Claude profile roots (touches context_weight paths) |
| `cfb95f3` | test(ctxdex): move hook self-test scratch root off the retired {repos}\AI-Dev root |

**Files audited (current contents, per task scope):**
- `skills/graphify/scripts/query_reranked.py`
- `tools/context_weight.py`
- `tools/tests/test_context_weight.py`
- `hooks/tests/test_ctxdex_hook.py`

**Checks run:** (1) silent-catch census, (2) counter-integrity, (3) tested-but-dead.

Every finding below is a HYPOTHESIS with a concrete verification step; a finding is marked VERIFIED only where the step was executed this pass. Severities are themselves hypotheses.

---

## Summary of counts

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | 2 |

No CRITICAL, HIGH, or MEDIUM findings. The window's changes are careful and, on the three checks that matter here, sound: timing is real, failures are surfaced, a missing input can no longer be scored clean, and both test suites drive the shipped code paths (verified by running them this pass).

---

## Check 1 — Silent-catch census

**Verdict: no swallowed real failures introduced or present.** Every catch site in the changed code either surfaces the failure (stderr / return code / an UNMEASURED line) or is a justified fail-open decode / test-teardown. The commit `6a0ab7e` specifically *removes* the one prior silent `except Exception: pass` in `query_reranked.py` and replaces it with a rate-limited stderr diagnostic.

**Timing-truthfulness verification (task-flagged):** VERIFIED by reading `rerank_query` (query_reranked.py:199–221) and `main` (:336–351).
- `retrieval_ms` is a real `time.perf_counter()` span around the two `graphify query` subprocess calls (target budget + pool, incl. TRUNCATED retry).
- `rerank_ms` is a real span around the in-process BM25 scoring + splice.
- `duration_ms` is total wrapper wall time from `main`.
- The only zero is the `node_cap == 0` branch (:204–206), which returns `rerank_ms=0.0` — honest, not faked: scoring genuinely did not run because there were no nodes, and `retrieval_ms` is still the true elapsed. Docstrings (:185–196, :265–276) explicitly disclaim equivalence to stock's traversal-only `duration_ms`. No zeroed/fabricated timing.

**Failure-visibility verification (task-flagged):** VERIFIED.
- Telemetry write failure → `_warn_telemetry_failure` (:227–246) prints one stderr line per unique exception signature per process. Not swallowed.
- Query subprocess failure → `run_query_raw` raises `RuntimeError` (:136–140); `main` catches it, prints to stderr, returns 1 (:348–350). Honest non-zero exit.
- Single-record logging: children run with `GRAPHIFY_QUERY_LOG_DISABLE=1` (:125); the passthrough branch (:330–333) deliberately does *not* set it so stock graphify logs itself. No double-log, no dropped log.

Full site table in the appendix.

---

## Check 2 — Counter-integrity (`context_weight.py`)

**Verdict: the commit's claim holds. VERIFIED** by reading every verdict-producing branch and by running the test suite (13/13 pass).

The exit/verdict is `return 2 if (fails or unmeasured) else (1 if warns else 0)` (:175), and the printed banner (:170–174) prints `CLEAN` **only** when `unmeasured` is empty AND there are no fails/warns. Traced sources of `unmeasured`:

- absent skills dir / no `SKILL.md` (:65–66, :81–82, via `skill_descriptions`)
- CLAUDE.md missing or unreadable (:118, :123)
- projects dir missing / no `*/memory/MEMORY.md` (:131–137)
- `settings.json` unreadable/malformed (:154–157)

Any one of these forces exit 2 and suppresses `CLEAN`; the printed `total` is explicitly labelled incomplete ("no budget verdict is possible", :171–172). The b640675 regression — everything absent totals 0, clears every threshold, prints CLEAN/exit 0 — is closed: `test_everything_missing_never_reports_clean` asserts exit 2, five UNMEASURED lines, and no CLEAN, and it passes. A missing input can no longer be silently scored as pass/zero, and the aggregate reflects the gap.

One LOW robustness gap in this same area — see LOW-1.

---

## Check 3 — Tested-but-dead

**Verdict: no dead twins. Both suites exercise the shipped path. VERIFIED** by running each and by tracing production callers.

- `test_context_weight.py` imports `context_weight` and calls `cw.main(**paths)` and `cw.skill_descriptions` directly — the same module and the same `main()` reached by the `__main__` guard (:178–179). `main`'s parameters exist only so tests pass a throwaway estate; the no-arg production call resolves the module constants. **Not dead.** Ran this pass: `OK - 13 cases`.
- `test_ctxdex_hook.py` invokes the real shipped hook by path (`parents[1]/ctxdex_autoindex.py`) as a subprocess with JSON on stdin, exactly as Claude Code would, and asserts against ctxdex's real DB. It depends on `{tools}/ctxdex/ctxdex.py`, which exists. **Not dead.** Ran this pass: `39/39 checks passed`.
- Production caller of `query_reranked.py`: `skills/graphify/references/query.md:75` routes a BFS query through it when `GRAPHIFY_RERANK=1` (documented by commit `6a0ab7e`'s sibling doc change), and `bench_reranker.py` / `measure_latency.py` drive the same wrapper. **Live, not dead.**
- Production caller of `context_weight.py`: invoked directly as a CLI tool (`python tools/context_weight.py`, weekly per its docstring). No orphaned entry point.

---

## Findings

### LOW-1 — `context_weight.py`: a present-but-unreadable `MEMORY.md` crashes instead of reporting UNMEASURED
**Status: HYPOTHESIS.** `tools/context_weight.py:140–141`

`mem.read_bytes()` and `mem.read_text(...)` inside the MEMORY.md loop are **not** wrapped, unlike the sibling CLAUDE.md read (:120–123, wrapped in `try/except OSError → unmeasured`) and the settings read (:149–157). If a globbed `MEMORY.md` exists but is unreadable (permissions, lock, replaced by a directory), the tool raises an uncaught `OSError` and exits with a traceback rather than appending an `UNMEASURED:` line.

Counter-integrity is *not* violated — a crash is fail-loud, so it can never masquerade as `CLEAN`/0 — hence LOW, not higher. It is an error-handling inconsistency: the same class of fault is a clean UNMEASURED for CLAUDE.md/settings but a stack trace for MEMORY.md.

**Verification step:** in a temp estate, create `projects/proj/memory/MEMORY.md` as a *directory* (or chmod-000 the file on a POSIX run), then call `cw.main(**paths)`; observe an uncaught `OSError`/`IsADirectoryError` instead of an `UNMEASURED` line and exit 2. (Not executed this pass — Windows read-permission simulation is unreliable in-test.)

### LOW-2 — corpus-parity claim not fully verifiable from this repo
**Status: HYPOTHESIS.** `skills/graphify/scripts/query_reranked.py:277–303`

Commit `6a0ab7e` claims the reranked record's `corpus` is canonicalized "exactly like the stock CLI (`cli.py` query path)". The wrapper does a reasonable canonicalization (`--graph` wins, else `GRAPHIFY_OUT/graph.json`, made absolute against the child `cwd`, then `.resolve()`), which is internally consistent. But the stock CLI lives in the installed `graphify` package outside this repo, so byte-for-byte parity of the corpus key could not be confirmed here. If stock resolves differently (e.g. no `.resolve()`, or a different default filename), stock and reranked records would still split into two corpus groups — the exact bug the commit set out to fix.

**Verification step:** read the installed `graphify/cli.py` query path and `graphify/paths.py`, and confirm the corpus string it passes to `querylog.log_query` matches `str(Path(GRAPHIFY_OUT)/"graph.json").resolve()` for the default case and `Path(args.graph).resolve()` for the explicit case. (Not executed — package source out of audit scope.)

---

## Appendix — Silent-catch census

| Site | Pattern | Classification | Note |
|---|---|---|---|
| `skills/graphify/scripts/query_reranked.py:134` | `subprocess.run(..., errors="replace")` | justified-and-logged | decode of child stdout; non-zero exit still raises (`:136`). Not a swallow. |
| `query_reranked.py:158` | `if "TRUNCATED" in stdout` retry | n/a (not a catch) | retries at bigger budget; honest. |
| `query_reranked.py:304` | `except Exception as exc: _warn_telemetry_failure(exc)` | justified-and-logged | **replaced** the old silent `pass`; emits one rate-limited stderr line per signature, query result still stands. |
| `query_reranked.py:327` | `sys.stdout.reconfigure(errors="replace")` | justified-and-logged | output-encoding hardening, not error handling. |
| `query_reranked.py:348` | `except RuntimeError as e: print(...); return 1` | justified-and-logged | honest non-zero exit on query failure. |
| `tools/context_weight.py:73` | `read_text(errors="replace")` | justified-and-logged | tolerant decode of a SKILL.md body; measures the description regardless. |
| `context_weight.py:75` | `except OSError as e: return out, ["could not read ..."]` | justified-and-logged | surfaces as UNMEASURED; forces exit 2. Early-returns, abandoning later skills, but the incomplete-total banner fires. |
| `context_weight.py:118` | `if not p.is_file(): unmeasured.append(...)` | justified-and-logged | missing CLAUDE.md named + flagged. |
| `context_weight.py:120–123` | `except OSError as e: unmeasured.append(...)` | justified-and-logged | unreadable CLAUDE.md flagged. |
| `context_weight.py:131–137` | missing projects dir / empty glob → `unmeasured` | justified-and-logged | flagged, no silent 0. |
| `context_weight.py:140–141` | `mem.read_bytes()/read_text()` **unwrapped** | (not a catch) | **LOW-1** — throws uncaught on a present-but-unreadable MEMORY.md; fail-loud, so no counter-integrity breach, but inconsistent with the wrapped siblings. |
| `context_weight.py:141` | `read_text(errors="replace")` | justified-and-logged | tolerant line-count decode. |
| `context_weight.py:154–157` | `except (OSError, json.JSONDecodeError) as e: unmeasured.append(...)` | justified-and-logged | plugin check didn't run → UNMEASURED (correctly not a WARN). |
| `hooks/tests/test_ctxdex_hook.py:269–273` | `except OSError: pass` (rmdir teardown) | justified-but-silent | test cleanup of a temp tool-results dir; harmless. |
| `test_ctxdex_hook.py:350–354` | `except OSError: pass` (rmdir teardown) | justified-but-silent | same, second fixture tree. |

No `except:`/`except Exception: pass`, no unchecked subprocess return codes on the production path, no broad `contextlib.suppress`, and no debug-only logging of a failure that should surface.
