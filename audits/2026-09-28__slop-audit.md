# Slop audit — Claude-Profile — 2026-09-28

**Mode:** incremental (scheduled `slop-audit-weekly`).
**Window:** `7d5a78b..cf80336` — commits after the 2026-09-21 report (6 commits).
Code in window: `hooks/evidence_evacuate.py` (+156), `hooks/tests/test_evidence_evacuate.py` (+130).
All other commits are docs (SYSTEM-RULES, WORKING-STYLE, graphify skill notes, Revit-Ops rename).

**Result:** 0 CRITICAL · 0 HIGH · 1 MEDIUM · 5 LOW.

The hook is fail-open by design (a Stop hook exiting 2 would block the stop) and says so in
its docstring. Every failure path writes an `ERROR`/`EXITn` line to its log. The deployed copy
at `~/.claude/hooks/evidence_evacuate.py` is byte-identical to the repo, and it is wired into
`~/.claude/settings.json` for both Stop and SessionEnd. The test runs the real Claude-Tools
evacuator, not a stub.

## MEDIUM

### M1 — A partial or failed archive copy counts as archived, so the packet is never retried — VERIFIED
`hooks/evidence_evacuate.py:74` decides a packet is archived with `(archived / f"{id}.json").exists()`.
But the evacuator (Claude-Tools `evidence-archive/evacuate_worktree.py:162-184`) leaves its
target file in place when it errors or is interrupted.
**Failure scenario:**
- The hook kills the evacuator at its 25 s timeout (`:96-101`) after `open(target,"xb")` but
  before the write finishes. Or the evacuator's post-write hash check fails.
- A 0-byte or truncated `packets/<id>.json` stays in the archive.
- From then on, `missing_ids` returns `[]` on every Stop, so the evacuator never runs again for
  that packet.
- When the worktree is removed, the real packet is lost. The only trace is one log line (see L1).

**Ran (scratchpad simulation, audit agent):** planted a 0-byte `packets/ep_…json` next to a
valid source. The evacuator itself flags `conflict` / `safe_to_remove:false` (exit 1), but the
hook's `missing_ids` returns `[]`.
**Verify fix:** the pre-check should require a hash-matching or receipt-verified copy, or the
evacuator should write to a temp name and rename only after verifying.

## LOW

### L1 — Evacuation failures only reach a log that no one reads — VERIFIED
`hooks/evidence_evacuate.py:89-106`. A missing evacuator, a timeout, or a non-zero evacuator
exit is logged to `hooks/evidence_evacuate.log` and nowhere else. Nothing reads that log
(no closeout, `/close`, or health check references it).
**Failure scenario:** someone moves Claude-Tools `evidence-archive/evacuate_worktree.py` or
the evacuator starts exiting non-zero. Every turn then logs `ERROR`, but the archive stops
growing without anyone noticing. Packets die with their worktrees, which is the loss the hook
exists to prevent.
**Verify:** `grep -rn evidence_evacuate.log` across Claude-Profile/Claude-Tools skills and hooks.
Zero readers confirms it. **Ran 2026-09-28:** only the hook itself references the log file; no reader exists. The failure itself is correctly fail-open; the gap is that nothing
reports it.

### L2 — The `--sweep` backstop has no scheduled caller — VERIFIED
`hooks/evidence_evacuate.py:20-22, 136-139`. The docstring calls sweep mode the backstop for
worktrees removed without a session ending in them. The test covers it
(`hooks/tests/test_evidence_evacuate.py:118`). But no scheduled task under
`~/.claude/scheduled-tasks/` and no script in Claude-Tools invokes `--sweep`.
**Failure scenario:** a worktree is removed outside a session. Its packets are lost even
though the documented backstop exists and is tested.
**Verify:** `schtasks /query /fo LIST | findstr /i evacuate` plus a check of `mcp__scheduled-tasks`
list. If neither shows a caller, this is tested-but-dead (by omission). **Ran 2026-09-28:** no `schtasks` entry matches `evacu`, and `~/.claude/scheduled-tasks/` has no reference. No caller exists.

### L3 — A packet whose filename id and JSON id disagree re-runs the evacuator every turn — VERIFIED
`hooks/evidence_evacuate.py:72-75` compared with the evacuator's `classify` (`:141-145`).
A file named `…ep_B.json` whose JSON says `ep_A`, or a malformed file with an `ep_` name, is
quarantined. So `packets/ep_B.json` never appears, and every Stop spawns the evacuator again and
writes a new receipt. **Ran (simulation):** 3 hooks produced 3 receipts, each logged `OK`. There
is no data loss; the cost is one subprocess per turn plus receipt churn.

### L4 — The hook can outrun its own 30 s timeout — HYPOTHESIS
`~/.claude/settings.json` gives the hook 30 s. In a worktree session, `CLAUDE_PROJECT_DIR` and
the payload `cwd` differ. The hook evacuates both, one after the other, with 25 s each
(`:147-148`), so the worst case is 50 s. The harness then kills the hook and the second store
is skipped that turn. It self-heals on the next Stop. A kill during a write can cause M1.
**Verify:** time two large stores back to back.

### L5 — One bad checkout aborts the rest of `--sweep` — HYPOTHESIS
`:137-138`. An exception in one checkout (for example a `PermissionError` from `os.scandir`)
escapes to the top-level fail-open handler (`:154`), and the remaining checkouts are skipped.
This is moot today because nothing calls sweep (L2).
**Verify:** run sweep with an unreadable `packets/` directory in the first of two worktrees.

## Checks

- **Counter-integrity:** no success total. The `OK`/`EXITn` log level is derived from
  `returncode` (`:103`), and a non-zero exit is not reported as OK. Clean.
- **Tested-but-dead:** main path — clean (shipped copy = repo, real evacuator in test). Sweep — see L2.

## Silent-catch census (appendix)

| file:line | pattern | classification | note |
|---|---|---|---|
| hooks/evidence_evacuate.py:51 | `except OSError: pass` in `log()` | justified-but-silent | a log-write failure has nowhere else to go |
| hooks/evidence_evacuate.py:89-91 | evacuator missing → return | justified-and-logged | fail-open; see L1 |
| hooks/evidence_evacuate.py:99-101 | `TimeoutExpired` → return | justified-and-logged | 25 s cap; see L1 |
| hooks/evidence_evacuate.py:103-106 | non-zero exit → log only | justified-and-logged | returncode checked; see L1 |
| hooks/evidence_evacuate.py:74 | file existence treated as archived | swallows-a-real-failure | M1 |
| hooks/evidence_evacuate.py:142-143 | bad stdin JSON → `{}` | justified-but-silent | falls back to `$CLAUDE_PROJECT_DIR`; still evacuates |
| hooks/evidence_evacuate.py:154-155 | top-level `except Exception` | justified-and-logged | documented fail-open |


## Disposition ledger (2026-09-29)

| Finding | Disposition | Where |
|---|---|---|
| M1 | FIXED: a packet counts as archived only when a byte-identical copy exists. The evacuator also publishes through staging plus a no-overwrite link, so no partial file can reach `packets/` | PR #17, merged `a81a5a5`; claude-tools PR #14 |
| L1 | FIXED: any failure is surfaced as a `systemMessage` while the hook stays fail-open with exit 0; `--sweep` exits 1 and prints failures | PR #17 |
| L2 | FIXED: SessionEnd sweeps the repo root and every `.claude/worktrees/*` sibling, with no new scheduled configuration | PR #17 |
| L3 | FIXED: a hash-matching quarantine entry `<sha12>__<name>` settles the file, ending receipt churn | PR #17 |
| L4 | FIXED: one shared 25 s budget across checkouts; an unreached checkout is a recorded deferral | PR #17 |
| L5 | FIXED: a failure in one checkout is recorded and the remaining checkouts are still processed | PR #17 |

Installed: the pinned `~/.claude/hooks/evidence_evacuate.py` was reinstalled using bootstrap step 5a (backup, then byte copy of the committed source). Its hash matches `a81a5a5`. The Stop and SessionEnd registrations with a 30 s timeout are unchanged. The N2/N3 follow-up (PR #18) is installed only after it merges.

**Re-check on changed paths:** all 28 original findings are gone (silent-catch census, counter-integrity and tested-but-dead re-run on the changed paths; 331 tests pass). The re-check found three new LOW gaps in the fixes themselves: N1, a failed staged write left a `.part` in `staging/` (claude-tools PR #15, CI green); N2, a SessionEnd `systemMessage` may never be shown; and N3, the hook's pre-check hashed every packet outside the time budget (both claude-profile PR #18, 33/33 tests, the pre-change hook fails 5). Both PRs are open and await the owner's merge. Outside the audited list, `evidence-capture/pipeline.py` `record_pending` still swallows `OSError`; that code is local-only and is queued for the owner.
