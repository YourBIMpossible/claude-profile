# Slop audit — 2026-09-13 (incremental)

**Window:** `9ec78c96de45ab5ac5151aae12aa70d532594b11..HEAD`
**Non-doc code files changed in window:**
- `tools/context_weight.py`
- `tools/tests/test_context_weight.py`

**Notable change scrutinized:** `fix(context_weight): report an unreadable MEMORY.md as UNMEASURED, not a crash (LOW-1)` — the new `try/except OSError` around the per-project MEMORY.md read (`context_weight.py:140-145`).

**Verdict: CLEAN.** No CRITICAL/HIGH/MEDIUM/LOW findings. The three mandated checks were run and the test suite was executed this session.

---

## Findings by severity

None. The window is a two-file, minimal error-handling hardening plus its regression test. All three checks passed on inspection and on execution.

---

## Check 1 — Silent-catch census

**One catch site introduced in the window:** `context_weight.py:143-145`

```python
try:
    mem_bytes = len(mem.read_bytes())
    mem_lines = mem.read_text(encoding="utf-8", errors="replace").count("\n") + 1
except OSError as e:
    unmeasured.append(f"could not read {mem} ({e})")
    continue
```

**Classification: JUSTIFIED-AND-LOGGED (surfaced).** Not a silent swallow.

- **VERIFIED — does not swallow a real failure / does not conflate unreadable with absent.** A caught read failure appends to `unmeasured`, which drives `return 2` (line 179) and prints `NOT MEASURED — … totals above are incomplete and no budget verdict is possible` (lines 174-176). It can never resolve to `CLEAN`/exit 0. The prompt's specific worry — a present-but-permission-denied file being treated the same as absent — is handled correctly: both routes land in `unmeasured` (absent → "no */memory/MEMORY.md found", line 137; unreadable → "could not read {mem} ({e})", line 144) and both force exit 2, but with distinct, attributable messages. The exception detail `({e})` is preserved in output, so a `PermissionError` vs `IsADirectoryError` is visible to the operator.
  *Verification step (run this session):* `python tools/tests/test_context_weight.py` → `OK - 14 cases`; `python -m pytest tools/tests/test_context_weight.py -q` → `14 passed`. The new case `test_unreadable_memory_md_is_unmeasured_not_a_crash` asserts `rc == 2`, `"UNMEASURED: could not read"` present, and `"CLEAN" not in out`.
- **Scope of `except OSError` is appropriate.** It brackets only the two filesystem reads for one project's index; it re-raises nothing narrower is needed for (permission-denied, is-a-directory, I/O error all subclass `OSError`). It is not a bare `except:` and does not catch programmer errors.
- **`continue` is correct.** Skipping `mem_worst = max(...)` for the unmeasured file means `total` under-counts that file, but the non-empty `unmeasured` list explicitly disclaims the total ("totals above are incomplete"), so no false CLEAN and no misleading budget verdict results.

No other swallowed-failure patterns introduced in the window (no bare `except`, no `except Exception: pass`, no unchecked subprocess, no `errors="ignore"` — the `errors="replace"` on `read_text` is a pre-existing, deliberate byte-count decode and is inside the try).

## Check 2 — Counter-integrity

No success/total *count* loop was added. The only accumulators touched are the byte accumulator `total` and the disclosure list `unmeasured` (reported via `len(unmeasured)` at line 175).

- **VERIFIED (by inspection):** every early exit in `main` appends to `unmeasured` *before* it `continue`s — the CLAUDE.md loop (line 118 then 119), the new MEMORY.md catch (line 144 then 145). No path skips a file while leaving the disclosure list untouched, so the "N input(s) missing or unreadable" count and the exit-2 verdict always reflect every skipped file. The new `continue` cannot produce a silently-incomplete "CLEAN" total.

## Check 3 — Tested-but-dead

- **VERIFIED:** the test drives the shipped entrypoint. `test_context_weight.py:84` calls `cw.main(**paths)`, i.e. the same `main()` invoked by `context_weight.py:183` (`sys.exit(main())`) when the tool is run as `python tools/context_weight.py`. There is no dead twin — `main` is the single production function, and a repo-wide search for `context_weight` callers returns only the tool, its test, and prior audit docs (no wrapper/hook re-implements the logic). The new test's `memory="unreadable"` path materializes a directory named `MEMORY.md` that matches the real `*/memory/MEMORY.md` glob and triggers the real `except OSError`, so it exercises the exact shipped branch.

---

## Appendix — Silent-catch census table

| File:line | Pattern | Classification | Reaches operator? | Note |
|---|---|---|---|---|
| `tools/context_weight.py:143-145` | `except OSError as e: unmeasured.append(...); continue` | justified-and-logged | Yes — `UNMEASURED:` line + `NOT MEASURED` summary + exit 2 | New in window (LOW-1 fix). Distinguishes unreadable from absent; cannot yield false CLEAN. |
| `tools/context_weight.py:120-123` | `except OSError as e: unmeasured.append(...)` (CLAUDE.md) | justified-and-logged | Yes | Pre-existing, unchanged in window; listed for context. |
| `tools/context_weight.py:158-161` | `except (OSError, json.JSONDecodeError): unmeasured.append(...)` (settings) | justified-and-logged | Yes | Pre-existing, unchanged in window; listed for context. |

## What was inspected
- Full window diff of both files.
- `context_weight.py:100-184` (the entire `main` verdict/accumulation body and CLI entrypoint).
- `test_context_weight.py:1-90` (module docstring, `build_estate`, `run` harness, new test).
- Repo-wide caller search for `context_weight` (only tool + test + audit docs).
- Executed both the standalone runner and pytest this session (14/14 pass).
