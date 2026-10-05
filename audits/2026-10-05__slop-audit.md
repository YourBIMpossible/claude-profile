# Slop audit — Claude-Profile — 2026-10-05

**Mode:** incremental.
**Window:** `cf80336..1d2d2e5` — 14 commits after the 2026-09-28 report's window. Five of them (#17, #18, #20, #21, #22) remediate that report or follow up on it.
Code in window: `bootstrap.ps1` (+323), `hooks/evidence_evacuate.py` (+297), `hooks/graphify_hint.py` (new, 216), `settings.template.json` (+155), `skills/graphify/scripts/query_reranked.py` (+118), `resolve_graph.py` (new, 149), `bench_reranker.py` / `measure_latency.py` (1 line each), and tests: `hooks/tests/test_bootstrap_install.py`, `test_evidence_evacuate.py`, `test_graphify_hint.py`, `skills/graphify/scripts/test_resolve_graph.py`, `test_query_reranked.py`.
The other commits change only docs, audits, `.gitignore` and `CLAUDE.md`.

**Result:** 0 CRITICAL · 0 HIGH · 0 MEDIUM · 4 LOW.

All probes ran in a throwaway clone at `1d2d2e5`, under `%TEMP%\claude\slop-cp`, with `USERPROFILE` and `APPDATA` redirected to sandbox folders. Bootstrap never ran against the real `~/.claude`. The test suites pass in the clone: 68 passed (evidence, graphify hint, resolver, wrapper, including the live graphify integration class) and 21 passed plus 6 subtests (bootstrap install).

The remediation of the 2026-09-28 findings holds. Every M1/L1–L5 fix is in the shipped code (`evidence_evacuate.py:110-149`, `:167-207`, `:315-348`). The N2/N3 follow-ups (#18) are merged, and the installed copy is byte-identical to the repo. None of the new findings is silent: each one either fails loudly or is latent.

## LOW

### L1 — A refused bootstrap has already rewritten the CLAUDE.md stub, the skills junction and every pinned hook — VERIFIED (run)
`bootstrap.ps1:414-436` (the refusals added in #22) runs after the writes at `:297-368`.
The script's own rule (`:233-234`, `:255`) is to refuse "before writing anything, so the machine is left untouched rather than half-configured". The settings.json refusals come after steps 1, 2 and 5a have already written.
**Ran:** in a sandbox home, planted an old `hooks/decay_reminder.py` and `CLAUDE.md` and a malformed `settings.json` (`{not json`), then ran the clone's bootstrap. It exited 1 with "refusing to replace it". By then it had:
- rewritten the CLAUDE.md stub (backed up);
- created the skills junction;
- installed all 9 hook copies (the old one backed up).

`settings.json` itself was untouched, which is what `test_malformed_settings_is_refused_untouched` and `test_empty_settings_is_refused_untouched` assert. Neither test asserts that the hooks or the stub were left untouched.
**Impact:** the failure is loud (exit 1). But the machine is left half-updated: the new hook code runs against the old registrations, so a hook the new template adds is installed and never registered. The message only talks about settings.json, so a reader can assume nothing changed.
**Verify fix:** move the existing-settings validation (`:414-436`) above step 1. Then extend the refusal tests to assert that the hooks directory and `CLAUDE.md` are byte-unchanged.

### L2 — Under PowerShell 5.1, a non-ASCII character in a template is written into settings.json as mojibake — VERIFIED (run), latent
`bootstrap.ps1:264`, `:371` and `:473` read `settings.template.json` and the MCP template with `Get-Content -Raw`. In PowerShell 5.1 that decodes a BOM-less file with the ANSI code page, the exact hazard `Read-Utf8Text` (`:27-32`) was added for. That helper is used only for the existing files.
**Ran:** added an em dash to the clone's prompt-hook text, then did a fresh sandbox bootstrap (`powershell.exe` 5.1). The written `settings.json` holds `\xe2€”` in place of `—`, and the bootstrap reported success.
**Today:** both templates are pure ASCII (checked with `grep -P '[^\x00-\x7F]'`), so nothing is corrupted yet. The repo's docs use em dashes freely, so the first one that lands in the prompt hook will corrupt silently. The prompt-ownership comparison (`:184`) then compares mojibake with mojibake, so nothing flags it.
**Verify fix:** read both templates with `Read-Utf8Text`, and add a non-ASCII prompt fixture to `test_bootstrap_install.py`.

### L3 — The evacuator's output is decoded with the console code page, so a successful copy can be reported as an AttributeError failure — VERIFIED (run)
`hooks/evidence_evacuate.py:187-192`. The child gets `PYTHONIOENCODING=utf-8`, but `subprocess.run(..., text=True)` decodes in the parent with the locale encoding (cp1252 here; UTF-8 mode is off).
In cp1252, the bytes 0x81/0x8D/0x8F/0x90/0x9D are undefined. If one appears in the output, the reader thread dies and `proc.stdout` is `None`, so `:192` raises `AttributeError`. `evacuate_all` (`:206`) catches that and reports it as a failure.
**Ran:** used a fake evacuator that prints `‐` and exits 0. The hook emitted `systemMessage` "1 failure(s) … AttributeError … 'NoneType' object has no attribute 'strip'" and logged `ERROR` for an evacuation that had succeeded.
**Impact:** this is the opposite of slop: a false alarm, not a hidden failure. The real evacuator only prints ASCII literals plus paths (`evacuate_worktree.py:318-329`), so it fires only on a non-ASCII checkout or archive path.
**Verify fix:** pass `encoding="utf-8", errors="replace"` to the `subprocess.run` call.

### L4 — Two telemetry labels overstate what they count — VERIFIED (static)
- `hooks/evidence_evacuate.py:194`: `OK … new={len(missing)}` logs the pre-check count of candidates, not the number the evacuator copied. Quarantined files and files already present are counted as "new". The log is the only consumer.
- `skills/graphify/scripts/query_reranked.py:28-29`: the docstring says "every query run through this wrapper writes exactly ONE query-log record". In fact the passthrough logs only when `rc == 0` (`:401`), and the rerank path logs nothing on `RuntimeError` (`:424-426`). `test_failed_query_logs_nothing` pins that behaviour. It matches stock graphify, which also logs only successes (`graphify/cli.py:1311`), so no data is lost relative to stock. But the stated contract claims a failure count that the log does not have.

**Verify:** reword both. Alternatively, parse the evacuator's `files:` counts line into the OK record.

## Checks

- **Counter-integrity:**
  - `bootstrap.ps1`: the settings plan (`:118-131`) counts every change record, and the `Kept N … hook(s)` lines count the handlers actually preserved.
  - `evidence_evacuate.py`: every failure path goes through `Run.fail` into the `systemMessage` or the sweep exit code. A budget deferral is a recorded failure (`:170-182`), not a silent skip.
  - Clean apart from the L4 labels.
- **Tested-but-dead:**
  - All five test files exercise shipped code. `test_evidence_evacuate.py` runs the repo hook against the real Claude-Tools evacuator. Its pytest wrappers `assert` the collected `check()` failures (`:522-525`), so a FAIL is not just printed. `test_bootstrap_install.py` runs the real `bootstrap.ps1` in a sandbox home. `test_graphify_hint.py` loads the repo hook and cross-checks its resolver copy against `resolve_graph.resolve`. `resolve_graph` has a production caller (`query_reranked.py:60-62`).
  - **Deployed:** every hook in `~/.claude/hooks/` matches its repo hash. The exception is `session-start-pull.ps1`, which differs only in the bootstrap-pinned `$repoRoot` line (`diff` = 1 line, intended, C1). The live `~/.claude/settings.json` registers every template hook on its template event and matcher, including `graphify_hint.py` (SessionStart and SubagentStart) and `evidence_evacuate.py` (Stop and SessionEnd). It keeps the two Claude-Tools capture hooks as unowned.
  - A sandbox bootstrap over a copy of the live settings.json: 0 added, 0 removed, 7 changed. Every change is a `/hooks/*` path rewrite to the sandbox hooks directory. No keys were dropped, and the two unowned hooks were kept.
- **bench/measure:** they now pass `--no-rerank` explicitly, so the new `GRAPHIFY_RERANK` env gate cannot contaminate the stock arm. Clean.

## Silent-catch census (appendix)

| file:line | pattern | classification | note |
|---|---|---|---|
| bootstrap.ps1:271 | `Get-Command git -ErrorAction SilentlyContinue` | justified-and-logged | no git → the dirty-hook check is skipped; the revision prints as `unknown (no git)` at :346 |
| bootstrap.ps1:273-274 | `git status` + `$LASTEXITCODE` | justified-and-logged | exit code checked, throws |
| bootstrap.ps1:285 | `git rev-parse` without `$LASTEXITCODE` check | justified-but-silent | a failure yields null and `.Trim()` throws; loud but cryptic |
| bootstrap.ps1:264, 371, 473 | `Get-Content -Raw` (ANSI in 5.1) | swallows-a-real-failure (latent) | L2 |
| bootstrap.ps1:387-391, 421-425, 482-486, 507-511, 530-534 | `try { ConvertFrom-Json } catch { throw }` | justified-and-logged | rethrows; PS 5.1 ConvertFrom-Json is terminating (confirmed by probe L1) |
| bootstrap.ps1:414-436 | refusal after earlier writes | swallows-a-real-failure (partial) | L1 |
| hooks/evidence_evacuate.py:83-84 | `except OSError: pass` in `log()` | justified-but-silent | unchanged from the prior report |
| hooks/evidence_evacuate.py:98-99 | `_size` FileNotFoundError → None | justified-but-silent | the candidate vanished, so not archived; the evacuator runs |
| hooks/evidence_evacuate.py:170-173 | `BudgetSpent` → deferred | justified-and-logged | `Run.fail` reaches the systemMessage |
| hooks/evidence_evacuate.py:189-191 | `TimeoutExpired` | justified-and-logged | `Run.fail` |
| hooks/evidence_evacuate.py:206-207 | per-checkout `except Exception` | justified-and-logged | `Run.fail`; L3 lands here as a false failure |
| hooks/evidence_evacuate.py:255-256 | `write_pending` OSError | justified-and-logged | logs ERROR; the systemMessage is still printed this turn |
| hooks/evidence_evacuate.py:271-272, 277-278, 290-291, 309-310 | FileNotFoundError races | justified-but-silent | entry renamed or removed by a concurrent Stop |
| hooks/evidence_evacuate.py:279-283, 292-296 | unreadable pending dir/entry | justified-and-logged | shown as a failure, never deleted |
| hooks/evidence_evacuate.py:311-312 | unlink OSError | justified-and-logged | the entry repeats rather than vanishes |
| hooks/evidence_evacuate.py:328-331 | bad stdin JSON → `{}` | justified-but-silent | falls back to `$CLAUDE_PROJECT_DIR` |
| hooks/evidence_evacuate.py:355-366 | top-level `except Exception` | justified-and-logged | fail-open, with a systemMessage |
| hooks/graphify_hint.py:214-215 | top-level `except Exception: pass` | justified-but-silent | advisory hook, documented (:32); no log, so a resolver bug makes the hint disappear without a trace |
| hooks/graphify_hint.py:67-68, 76-77, 105-106, 156-157 | OSError → None / "unknown" | justified-but-silent | no worktree mapping or build date; mirrors resolve_graph |
| skills/graphify/scripts/resolve_graph.py:64-65, 73-74, 101-102 | OSError → None | justified-but-silent | the CLI exits 1 on None |
| skills/graphify/scripts/query_reranked.py:344-355 | `_passthrough` relays rc/stdout/stderr | justified-and-logged | byte-identical relay; the rc is returned |
| skills/graphify/scripts/query_reranked.py:401 | log only on rc==0 | justified-but-silent | matches stock; docstring overclaims (L4) |
| skills/graphify/scripts/query_reranked.py:424-426 | `except RuntimeError` → stderr, rc 1 | justified-and-logged | |

## Disposition (2026-10-05)

All four findings are fixed in branch `claude/slop1005-bootstrap-hardening` (one PR). Nothing is deferred.

| Finding | Disposition | Proof |
|---|---|---|
| L1 | FIXED: `bootstrap.ps1` now runs every check before its first write: hook preflight, template rendering, existing `settings.json` validation, and existing `claude_desktop_config.json` parsing. The checks are: parses as JSON, not empty, top level is an object, Profile-owned keys are objects, and no wrong-case owned key such as `Hooks` (Claude Code reads keys case-sensitively, so a merged `Hooks` would silently disable every hook). The writes come after all checks, in order: CLAUDE.md stub, skills junction, pinned hooks, settings.json, desktop config. An empty existing object (`{}`) no longer trips a false "would drop top-level keys" refusal. | `RefusalLeavesProfileUntouched` in `hooks/tests/test_bootstrap_install.py` covers 10 settings shapes plus an unparseable desktop config. Each one exits non-zero, and SHA-256 snapshots of `~/.claude` and `%APPDATA%\Claude` (CLAUDE.md, the skills directory, installed hooks, settings.json, desktop config) are identical before and after, with no backups. Against the old bootstrap, all of them fail. |
| L2 | FIXED: both templates are read with `Read-Utf8Text`. Writes stay UTF-8 without BOM. | `Preflight.test_non_ascii_template_text_round_trips` puts `é — ‐` in the prompt hook and the MCP template, then runs under `powershell.exe` 5.1 and under `pwsh`. Under 5.1 the old bootstrap writes mojibake. |
| L3 | FIXED: `subprocess.run(..., encoding="utf-8", errors="replace")` in `hooks/evidence_evacuate.py`. | `test_non_ascii_output_is_success` uses a fake evacuator that prints a `lane‐é` path. The hook exits 0 with no systemMessage and logs one OK record. Before the fix it reported an `AttributeError` failure. |
| L4 | FIXED (relabel, no new logging): the OK record's `new=` is now `precheck_unarchived=`, with a comment that the evacuator's own summary is the tail. The log was its only consumer. The `query_reranked.py` docstring now says only successful queries are logged. No failure-path logging was added. | Same test asserts the new label. |

Not installed: the deployed copies under `~/.claude` are unchanged by this PR. The owner reinstalls with bootstrap after merge.
