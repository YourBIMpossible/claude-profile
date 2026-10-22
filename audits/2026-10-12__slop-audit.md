# Slop audit — Claude-Profile — 2026-10-12

**Mode:** incremental, read-only.
**Window:** `219b2a2..a3e15da` (commits after the 2026-10-05 report; HEAD `a3e15da`, merge of PR #35).
**Code in window:** `skills/review-to-resolution/scripts/grrp.py` (new, 507), `hooks/graphify_hint.py` (+191), `hooks/Show-AuditAlerts.ps1` (new, 87), `hooks/northstar_inject.py`, `hooks/decay_reminder.py`, `hooks/session_closure_audit.py` (prompt/action strings only), `tools/context_weight.py` (+57), and tests: `hooks/tests/test_bootstrap_install.py`, `test_closure_audit_artifact_guidance.py` (new), `test_decay_reminder.py`, `test_graphify_hint.py`, `test_northstar_inject.py`, `skills/review-to-resolution/tests/test_enrollment_rollout.py` (new), `test_review_to_resolution_conformance.py` (new), `tools/tests/test_context_weight.py`. `graphify_hint.py`, `decay_reminder.py` and `Show-AuditAlerts.ps1` were not in the brief's list but changed in the window, so they are covered too.

**Scope note — vendor:** `vendor/evidence-capture/*` and `vendor/evidence-footprints/*` were not audited in depth (audited separately at `{tools}`).
**Drift note:** all 30 vendored files differ from the `{tools}` HEAD (`8a21f0a`) *blobs*, but only in line endings. They are identical once CR is stripped, and byte-identical to the clean `{tools}` working tree. `vendor/README.md` documents this: the pin is `8a21f0a`, and `vendor/**` is `-text` so CRLF working-tree bytes are stored. The installed `~/.claude/hooks/evidence-capture` and `evidence-footprints` are byte-identical to `vendor/`. **No drift that matters.**

**Result:** 0 CRITICAL · 0 HIGH · 1 MEDIUM · 5 LOW.

Probes ran from a scratch dir (`%TEMP%\claude\slop1012`) with `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and `GRAPHIFY_HINT_LOG_DIR` redirected. In-scope pytest (grrp suites + decay/northstar/graphify/closure-audit hook tests) gave 90 passed, 30 subtests. `test_context_weight.py` was run from a scratch copy: pytest 31 passed, script mode `OK - 28 cases` (see SA-2).

## Findings

### MEDIUM

**SA-1 — `skills/review-to-resolution/scripts/grrp.py:113-135` — VERIFIED (run).**
A grrp record opened with a near-miss fence is silently dropped, and the gate then reports `complete` / `closeable: true`. The near misses are `` ``` grrp-finding `` (space), `` ```GRRP-finding `` (case), or `~~~grrp-finding`.
The malformed-fence guard tests the literal substring `` ```grrp `` (`:127`, `:133`). Each of these variants matches `ORDINARY_FENCE_RE` but not that substring. The parser then treats it as an ordinary example fence and skips its contents without an error (`:128-129`). This contradicts the parse docstring (`:104`, "any line mentioning a ```grrp fence must be an exact, closed block") and the fail-closed intent at `:121`.
**Ran:** a ledger with one met criterion plus an open `original` finding gives `incomplete`. The same finding under `` ``` grrp-finding ``, `` ```GRRP-finding `` or `~~~grrp-finding` gives `complete True [] [] []`. Only the indented variant is caught (`invalid`).
**Verify fix:** flag any fence opener whose info string matches `(?i)^\s*grrp` (after stripping the backtick/tilde run) as malformed. Add these three cases to the conformance suite.

### LOW

**SA-2 — `tools/tests/test_context_weight.py:441-470` — VERIFIED (run).**
Three tests are defined after `if __name__ == "__main__": sys.exit(main())`: `test_memory_index_at_25kb_truncation_fails`, `test_memory_index_at_200_lines_truncation_fails` and `test_advisory_trend_growth_does_not_change_exit_code`. In script mode, the documented runner (header `:13`, "Run: python tools/tests/test_context_weight.py") never registers them, and it still prints `OK - 28 cases`. pytest collects all 31.
The skipped tests pin the only hard gate left after the 2026-09-30 contract change (MEMORY.md truncation), so the script-mode "OK" overstates coverage of exactly that gate.
**Verify fix:** move the three tests above `main()`. Script mode should then report 31.

**SA-3 — `tools/context_weight.py:680` — VERIFIED (static).**
`_build_history_record` maps exit 0 to `status: "clean"`. Since exit 1 was retired (`:595`), an ADVISORY run (per-source WARN/NEAR-LIMIT, per-skill WARN, unlisted plugin, trend growth) is written to `context_weight.history.jsonl` as `clean`, while the printed VERDICT says ADVISORY (`:847`). The `advisory` flag computed at `:625` never reaches the record.
Today, nothing reads `status` back (`_compare_trend` compares bytes only), so the impact is a mislabeled trend log.
**Verify fix:** record `"advisory"` (or `status: "advisory"`) from `result["advisory"]`, and assert it in `test_14`.

**SA-4 — `skills/review-to-resolution/scripts/grrp.py:105, 380, 495-501` — VERIFIED (run).**
A ledger that is not UTF-8 crashes `gate --json` with a `UnicodeDecodeError` traceback, no JSON, and **exit 1**. Exit 1 is the documented code for `incomplete` (`:15`, SKILL.md §5). It fails loud, and callers are told to branch on JSON `closeable`, so this is a collision, not a false pass. A shell caller that branches on the exit code reads a crash as "incomplete".
**Ran:** `printf 'grrp: v1\n\xff\xfe bad\n'` → rc=1, 0 bytes stdout.
**Verify fix:** catch `UnicodeDecodeError`/`OSError` in `main` and return 3 (`invalid`).

**SA-5 — `grrp.py:180-289` vs `references/ledger-format.md:28-29` and `SKILL.md` §5 — VERIFIED (run).**
The docs overclaim what `validate`/`gate` enforce:
- `ledger-format.md` lists `lane`, `blocks` and `evidence` as **Required** finding keys. A finding missing them validates: `lane` defaults to `parent` (`:236`), and a missing `blocks` reads as none.
- SKILL.md §5 says `complete` needs "each resolved finding's review/integration/deployment/verification/cleanup obligations [to be] done or n/a with a reason". The gate only rejects `pending` obligations (`:341-344`). A `resolved`/`confirmed_defect` finding with no `obligations` key at `execution_state: identified` gates `complete`. The suite's default fixture (`test_review_to_resolution_conformance.py:52-56`, `obligations: ""`) relies on this.
**Ran:** `resolved_no_obligations` → `complete True`.
This is not a false pass against the code's own rules (obligations are listed as Optional in `ledger-format.md:29`), but the two documents disagree with each other and with the gate.
**Verify fix:** pick one contract. Either enforce the required keys and the obligations of a resolved finding, or reword §5 and the Required list.

**SA-6 — `grrp.py:475` (metrics), `hooks/northstar_inject.py:178-185, 228-231` — VERIFIED (static).** Two minor items:
- (a) `metrics.evidence_state_errors` counts any validation error containing "without" or "claims". That includes "met without evidence", "enhancement executed without scope_approval" and "spawned sub-lanes without may_delegate", so the pilot metric overcounts. Ran: 3/3 non-evidence-state messages counted. `metrics` also always gates against the real `DEFAULT_QUEUE`, with no `--queue` override.
- (b) In-window commit `569e870` made `northstar_inject`'s per-turn line "Never edit a north-star file yourself by any tool (Bash included)" the **only** coverage of the Bash channel, because `protect_paths_gate.py` is registered on `Write|Edit` only (confirmed in the live `settings.json`, and the gate's own SCOPE HONESTY note says so). The hook's fail-open sites drop that line with no log or trace:
  - `:230` top-level `except Exception: sys.exit(0)`;
  - `:180-185`, where every ACTIVE star is unreadable, so it exits with no injection and not even the no-star nudge.
  The fail-open is justified, but it silently disables the only Bash-side guard text.
  **Verify:** make an active `NORTHSTAR.x.md` unreadable (deny ACL) → the hook emits nothing. A one-line stderr or log record would make it observable.

## Coverage

- **Silent-catch census:** every changed hunk in the in-scope Python and PowerShell was censused (appendix). Only grrp SA-1 swallows a real failure. All other sites are justified fail-open; the silent ones are listed.
- **Counter-integrity:**
  - `grrp gate`: every `validate` error forces `invalid`; every unmet criterion, unassessed finding and open original finding reaches `incomplete`; transfers are reported separately via `transfers_ok`/`closeable`, as SKILL.md documents. The holes are SA-1 (a dropped record) and SA-5 (docs vs gate).
  - `context_weight`: the exit contract is consistent; SA-3 is a label issue.
  - `graphify_hint`: every path sets `skipped`/`emitted`/`error` in its JSONL record. The live log shows the fallback working: 587 UserPromptSubmit suppressions, 46 emits, 27 of them on `FileNotFoundError`, which is logged and injected.
- **Tested-but-dead:** clean.
  - Every changed hook test loads or executes the repo file under `hooks/`. The installed `~/.claude/hooks` copies of `graphify_hint`, `northstar_inject`, `decay_reminder`, `session_closure_audit` and `Show-AuditAlerts.ps1` are SHA-256-identical to HEAD, and all are registered in the live `settings.json` (graphify_hint on SessionStart, SubagentStart and UserPromptSubmit).
  - The grrp tests import or execute `skills/review-to-resolution/scripts/grrp.py`. Production callers (`/review-all`, `/close`, `review-to-resolution`) invoke `~/.claude/skills/.../grrp.py`, and `~/.claude/skills` is a junction to `{profile}\skills`, so it is the same file.
  - `test_bootstrap_install.py`'s new vendor tests assert byte-identity of the installed units and run the installed `capture.py dry`.
- **Not run:** `test_bootstrap_install.py`, which needs a full sandbox bootstrap under PowerShell; it was reviewed statically only. `Show-AuditAlerts.ps1` was reviewed statically only.

## Appendix — silent-catch census

| file:line | pattern | classification | note |
|---|---|---|---|
| grrp.py:113-135 | near-miss fence treated as ordinary fence, contents skipped | swallows-a-real-failure | SA-1 |
| grrp.py:121-123, 133-134, 140-141, 152-155 | parse anomalies → `led.errors` | justified-and-logged | force `invalid` |
| grrp.py:173-175 | repeated obligation keeps not-done | justified-and-logged | also a validation error (:257-259) |
| grrp.py:302-303 | queue unreadable → reason string | justified-and-logged | `transfers_unacknowledged`; `closeable` false |
| grrp.py:105 | `read_text` unguarded | justified-but-silent → loud crash | SA-4 (exit 1 collides with `incomplete`) |
| grrp.py:495-497 | missing ledger → stderr, rc 3 | justified-and-logged | |
| graphify_hint.py:308-309 | `except OSError` on transcript read | justified-and-logged | `rec.error`, injects hint (fail toward hint) |
| graphify_hint.py:273-274 `except ValueError: return None` | malformed transcript line skipped | justified-but-silent | worst case: a duplicate hint |
| graphify_hint.py:250, 263 SCAN_MAX_BYTES cap → False | scan cap silently ends the scan | justified-but-silent | injects; the cap is not recorded in the log |
| graphify_hint.py:338-339 `except OSError: pass` (rotate) | rotation failure | justified-but-silent | appends to the oversize file |
| graphify_hint.py:342-343 `except Exception: pass` | log write failure | justified-but-silent | documented best-effort |
| graphify_hint.py:370-371 `except Exception` | → `rec.error = sanitize_error(e)` | justified-and-logged | advisory; no hint emitted |
| Show-AuditAlerts.ps1:80 | `try { Add-Content deliveries.log } catch { }` | justified-but-silent | the documented "delivery record" (:17) can be lost without a trace |
| Show-AuditAlerts.ps1:83-85 | read failure → visible systemMessage line | justified-and-logged | |
| Show-AuditAlerts.ps1:86 | `try { Out-Hook } catch { }` | justified-but-silent | the last resort; nowhere left to report |
| Show-AuditAlerts.ps1:51, 65 | `Get-Content -Raw` (ANSI under PS 5.1) | justified-but-silent (latent) | non-ASCII alert text would show as mojibake; display only (the prior L2 pattern) |
| northstar_inject.py:178-185 | unreadable active star → `continue`, then exit with no output | justified-but-silent | SA-6(b); unchanged lines, guard weight added in window |
| northstar_inject.py:228-231 | top-level fail-open | justified-but-silent | SA-6(b) |
| decay_reminder.py:28 | blanket `except Exception` | justified-but-silent | unchanged; payload strings only in window |
| session_closure_audit.py:371-392 | action strings only | n/a | no new catch sites |
| context_weight.py:595 | exit 0 for advisory | justified-and-logged | VERDICT: ADVISORY printed; history mislabel SA-3 |
