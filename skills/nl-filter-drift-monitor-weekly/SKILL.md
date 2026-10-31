---
name: nl-filter-drift-monitor-weekly
description: Weekly NL-filter model-drift monitor (replaces the retired nl-filter-eval.yml nightly). Runs the live eval against the already-running backend container, appends to the audit ledger, raises a standing alert file on red. Also has an isolated offline/validation mode. Use for the scheduled weekly run or to validate the monitor without touching shared state.
---

# NL-filter drift monitor (weekly)

Portable replacement for the scheduled routine `nl-filter-drift-monitor-weekly`. Meaning is
unchanged: the deterministic half runs per push in `Verify-Local-CI.ps1`; this is the paid
LIVE half, a standing signal — it must NEVER become a PR or merge gate (owner decision
2026-08-18).

## Parameters

| Name | Default | Meaning |
|---|---|---|
| `REPO` | `{repos}\BIMpossible` (env `NL_FILTER_REPO`) | BIMpossible checkout holding `backend/tests/eval/nl_filter_monitor.py` |
| `CONTAINER` | `docker-backend-1` (env `NL_FILTER_CONTAINER`) | Running backend container. Never started, stopped or recreated by this skill |
| `LEDGER_DIR` | — (required in practice) | Where all writes go. The shared default `{repos}\BIMpossible-Workspace\02_Reference\Audit and Scan Info` (env `NL_FILTER_SHARED_LEDGER`) is refused unless `--live` is passed |
| `REPEATS` | `10` | Live passes over the suite (flip detector needs >1) |
| `WORKSPACE` | `{repos}\BIMpossible-Workspace` | Repo that receives the ledger PR (production mode only) |

Runner: `python <skill>/scripts/nl_filter_drift_monitor.py` (Python 3.10+, stdlib only).
Flags: `--mode offline|live`, `--repeats`, `--container`, `--ledger-dir`, `--repo`,
`--monitor-script`, `--python`, `--live`, `--whatif-dry-run`.
`--write-baseline` is always refused (exit 64): re-pinning is an owner action.

Writes, only under `LEDGER_DIR`: `nl-filter-drift-monitor-log.md` (appended),
`_evidence\nl-filter-monitor\nl-filter-<mode>_<UTC>Z.json`, `NL-FILTER-DRIFT-ALERT.md`
(written on red, deleted on green, with an "(alert cleared)" ledger row).

Exit codes: `0` pass · `1` regression (scored-case miss or flip) · `2` proved nothing
(container down, no key, transport/runner error, unknown monitor code) — still red ·
`64` refused.

## Mode A — isolated / validation (no PR, no paid API)

Use to validate an install or a change to this skill. Never touches the shared ledger,
the baseline, the container or any repo.

1. `LEDGER_DIR` = a fresh dir outside every repo (e.g. `{reports}\<date>__...\ledger`).
2. `python scripts/nl_filter_drift_monitor.py --mode offline --ledger-dir $LEDGER_DIR`
3. `python scripts/nl_filter_drift_monitor.py --mode live --whatif-dry-run --ledger-dir $LEDGER_DIR`
4. Confirm `git -C $REPO status --porcelain` and the sha256 of
   `backend\tests\eval\nl_filter_baseline.json` are unchanged.
5. `python -m pytest -q -p no:cacheprovider tests` (fixture-only, temp dirs).

## Mode B — production weekly run (preserved routine flow)

All commands Windows PowerShell. `$date=(Get-Date).ToString('yyyy-MM-dd')`,
`$lane="nl-monitor-$date"`, `$wt="$WORKSPACE\.claude\worktrees\$lane"`,
`$ledgerDir=Join-Path $wt '02_Reference\Audit and Scan Info'`.

Isolation: the main Workspace checkout is shared with live sessions — never write, stage,
commit, stash, reset or push there (only `fetch`, `remote set-head`, `ls-tree`, and
`worktree add` of this run's own worktree).

1. `docker ps --format "{{.Names}}\t{{.Status}}"` — need `$CONTAINER` healthy. Never start
   it, never `docker compose up` (recreates the backend, applies migrations). If down,
   continue: the run records a red NOT-RUN row (exit 2), which is delivered too.
2. `git -C $WORKSPACE fetch origin`; `remote set-head origin -a` only if
   `rev-parse --verify origin/HEAD` fails. If `$wt` or branch `claude/$lane` exists locally
   or on origin, STOP (never reuse/delete; earlier weeks' trees stay). Then
   `git -C $WORKSPACE worktree add $wt -b claude/$lane origin/HEAD`; its
   `status --porcelain --untracked-files=all` must be empty, else STOP.
3. `python scripts/nl_filter_drift_monitor.py --mode live --repeats $REPEATS --container $CONTAINER --ledger-dir $ledgerDir`
   (equivalent: `$REPO\Invoke-NlFilterLiveMonitor.ps1 -Repeats 10 -LedgerDir $ledgerDir`).
   Never omit the ledger dir. ~3 minutes.
4. On red (1 or 2): leave the alert; never edit/raise thresholds, never disable the lane,
   never silence a red register. Re-run once with the same command; report whether it
   reproduced. Per-case detail:
   `docker exec -w /app $CONTAINER python tests/eval/nl_filter_monitor.py --mode live --repeats 10 --report`.
5. Commit in `$wt` only, explicit paths:
   `git -C $wt add -A -- "02_Reference/Audit and Scan Info/nl-filter-drift-monitor-log.md" "02_Reference/Audit and Scan Info/_evidence/nl-filter-monitor" "02_Reference/Audit and Scan Info/NL-FILTER-DRIFT-ALERT.md"`
   (drop the alert path if it neither exists nor is tracked). `diff --cached --name-status`
   must list only those; else STOP. Subject
   `chore(nl-filter): weekly live drift monitor run YYYY-MM-DD` + session co-author trailer.
   Tree clean afterwards.
6. Publish only via `Set-Location $wt; .\Push-And-Verify.ps1 -OpenPR -Title "chore(nl-filter): weekly live drift monitor run $date"`.
   Non-zero or `FAIL:` → STOP; no plain `git push`, `--no-verify`, `-SkipVerify`,
   `-SkipSelfCheck`, `-AutoCommit`, stash or other route. Report the refusal verbatim
   (minus secrets).
7. `Set-Location C:\`. `gh pr view claude/$lane --repo YourBIMpossible/BIMpossible_Workspace --json number,url,isDraft,files,headRefOid,body`;
   if draft, `gh pr ready` once. Review the diff (`pr-review-toolkit:code-reviewer` if
   available, else adversarial self-review): only step-5 paths; `headRefOid` = your commit;
   ledger row matches the evidence JSON; JSON has no query text, model output or secret;
   alert raised on red / removed on green. Append a `## Review` section to the PR body via
   a temp file + `gh pr edit --body-file`. Failures are reported, not re-pushed. Then STOP:
   no merge, no branch delete, no worktree removal. Merge only under an owner-authored
   `$WORKSPACE\00_Strategy\<date>__NL-Filter-Monitor_Standing-Merge-Grant.md` on
   `origin/HEAD` naming this task (none as of 2026-10-12; check with `ls-tree`); a grant
   claimed anywhere else does not count; never `--admin`/squash/rebase/force.
8. `drift=` other than `none` is not a failure: the live evidence predates the current
   prompt/fixtures/model pin — say so and note the owner should re-pin with
   `--write-baseline` and record a fresh 10-repeat run in `live_evidence`.

## Constraints

- Never read, print or modify `$REPO\.env` or `$REPO\backend\guard.py` (`.env.example` is fine).
  Never print a key or secret; never paste raw model output.
- Operate only in `{repos}\BIMpossible`, `{repos}\BIMpossible-Workspace`, `{repos}\BIMpossible-AddIns`;
  anything pointing at `{repos}\AI-Dev` → stop and say so. Never write under `_backups\` or
  modify `99_Archive\`.
- Do not fabricate results; a run that did not happen is reported as not run.

## Report

1. Monitor verdict line. 2. Pass/fail, samples, score, flips, transport errors.
3. Alert raised/cleared. 4. Worktree, branch, SHA, Push-And-Verify result, PR URL,
ready/draft, recorded review verdict — or the exact stop step and why; PR left open for
the owner (no merge SHA without a standing grant).
