---
name: slop-audit-sweep
description: >-
  Portable weekly incremental /slop-audit sweep over N repo roots given as
  arguments: revision-based window per root (baseline = head_rev of the newest
  ancestor prior report, never a date), slop_prepass.py candidates, then the
  /slop-audit method, one GRRP ledger per changed root, one line per root.
  Read-only on targets except the one audits/ report per changed root. No
  remediation tier.
---

# /slop-audit-sweep

Contract: `docs/audit-revamp-anchor.draft.md` rules 1–10 (Claude-Profile repo). Run control is the
shared `weekly-full-audit` scripts; every step is one subcommand, and each refuses an out-of-order
or contract-breaking call with exit 2. Never work around a refusal: finalize (§Failure).

`RUN` = `python <skills-dir>/weekly-full-audit/scripts/audit_run.py` · `GRRP` =
`python <skills-dir>/review-to-resolution/scripts/grrp.py` · `<sd>` = `--state-dir` · `<rid>` = the
minted run_id. `ENROLL` = `python <skills-dir>/weekly-full-audit/scripts/grrp_enroll.py`
(fills what `GRRP init` leaves blank; refuses to overwrite a different value). Every RUN call after `start` except `slug` (which takes only
`--run-id` and `--prefix`) carries `--run-id <rid> --state-dir <sd>`.

## Identity (rule 5)

- `task_id` `slop-audit-sweep` · `run_id` `slop-audit-sweep-<UTC yyyymmddThhmmssZ>-<6 hex>` (minted by `start`).
- Root name = the root's folder name, lowercase (unique per run; RUN refuses a duplicate).
- Per-root GRRP slug `slop-audit-<root name>-<date>-<6 hex>` = `RUN slug --run-id <rid> --prefix slop-audit-<root name>` (same 6 hex as the run).
- Report `<root>/audits/<date>__slop-audit[-N].md`, exclusive create, front matter carries the one root.
- Run record `<sd>/slop-audit-sweep/<rid>.json`.

## Invocation

```
/slop-audit-sweep --repo-root <r1> [--repo-root <r2> …] --state-dir <dir> [--ledger-dir <dir>]
                  [--watchdog-dir <dir>] [--tools-root <dir>] [--baseline <root name>=<rev> …]
                  [--grrp-queue <queue.yaml>] --deliver <file|none>
```

Defaults: `--tools-root` = env `CLAUDE_TOOLS_ROOT`; `--ledger-dir` = GRRP's own ledger dir (env
`GRRP_LEDGER_DIR`), outside every target; `--watchdog-dir` = env `AUDIT_WATCHDOG_DIR`, else
`~/.claude/scheduled-tasks/_watchdog`. `--deliver file` is active (owner decision D2, 2026-10-30:
delivery is the results file, step 7; the anchor's earlier D2 branch/PR option is superseded);
`gmail-draft` does not exist here.
Scope rule: do not add the BIMpossible code repo or the Add-Ins repo — `/audit` embeds this
checklist there and `/revit-functionality-audit` owns Add-Ins.

## Permission tiers (rule 1)

| Tier | May touch | Steps |
|---|---|---|
| Inspection | nothing under any target root is created, modified, moved or deleted | 2–3 |
| Artifact writes | `<root>/audits/<date>__slop-audit[-N].md` (the one in-target output) · `--ledger-dir` · `--state-dir` · `--watchdog-dir` | 0–1, 4–7 |
| Remediation | none: no fixes, no commits in any target | — |

Protected, never written: `.env`, `backend/guard.py`, any `99_Archive/`, alembic `versions/`, any
`_backups/`. Never print `.env`. Never reference ClickUp.

## Steps — the order is the contract

**0. Start.** `RUN start --task slop-audit-sweep --root <name>=<path> … --deliver <file|none> [--tools-root …] --state-dir <sd> [--watchdog-dir …]`
Every root must be a git checkout: a non-git root fails preflight (no revision baseline without git).
Exit 2 → already finalized `failed` with its heartbeat; report `failures` and stop.

**1. Baselines (rule 3), every root, in the order given.**
`RUN baseline --root <name>=<path> --report-dir <path>/audits --pattern "*__slop-audit*.md" [--override <name>=<rev>] --run-id <rid> --state-dir <sd>`
`window: unchanged` → RUN already recorded coverage `unchanged (<head_rev>)`: no ledger, no
report, next root. `incremental` → `--since <baseline_rev>`. `full` → no `--since`, and the report
says `no baseline`. Never pick a baseline by date or mtime. A prior report counts only when its run
record under `<sd>` is `ok`/`degraded`; a legacy report (no `head_rev`) or one with no, a failed or an
unfinished record gives `full` with `trust: bootstrap` (full-scope inspection; this run becomes the
baseline only if it succeeds). `--baseline` is `trust: override`, never an established baseline — the
report says which.

**2. Changed root — ledger and candidates.**
`GRRP init --slug <slug> [--dir <ledger-dir>] --objective "slop sweep <rid> <name>"` → `RUN record --ledger <ledger path>`
(the path from init's own `created:`/`existing:` line, never rebuilt from a date) →
`ENROLL brief --ledger <ledger path> --scope "<name> <window>" --boundaries "inspection tier; one audits/ report; no fixes"
--done-means "AC-1 the method ran over the window; AC-2 every finding is in the report and the ledger" --authority "report only"` →
`RUN brief-done --ledger <ledger path> --run-id <rid> --state-dir <sd>` (refuses an empty or placeholder
brief field; dispatch in step 3 is refused until it has run).
`python <tools-root>/local-audit/slop_prepass.py <path> [--since <baseline_rev>] --json` → candidate list
(candidates are leads, never findings).

**3. Method.** Before dispatch `RUN record --require <each changed root name> --run-id <rid> --state-dir <sd>`.
Then declare each agent: `RUN dispatch --key <name> --shard <id> --method <slop-audit SKILL.md> --output <evd>/<name>.md [--output <evd>/<name>.findings.json] --run-id <rid> --state-dir <sd>` (weekly step 6 contract: outputs create-only under the evidence dir, dispatch picks a random non-blank line k per method and prints `child_must_attest`, which the agent's brief copies verbatim; every Markdown output attests each declared method with `method-sha256: <path> <sha256 of the file>` and `method-line: <path> <k> <sha256 of line k, stripped of surrounding whitespace>`; `RUN check --key <name> --state returned` is refused otherwise). One `general-purpose` Agent per changed root, in parallel, inspection tier only. Its
brief: apply the `/slop-audit` method (Skill `slop-audit`: the three checks, per-language swallow
patterns, hypothesis discipline) to the window `<baseline_rev>..<head_rev>` plus the candidates; this
is a nested lens of an enrolled run, so it skips `/slop-audit`'s own GRRP entry, and it returns the
report body instead of writing a file. Unverified stays `HYPOTHESIS` (GRRP `unassessed`); nothing is
upgraded past what was demonstrated. Each agent also returns a completion plan
`{inspected:[…], remaining:[…], why, evidence:<its body file>}` (no sampling cap: what was read, what was not, why)
→ `RUN check --key <name> --state returned --inspected … --evidence <evd>/<name>.md` (evd = `RUN evidence-dir`; see weekly step 6); one that errors or does not return →
`RUN check --key <name> --state failed --reason <why>`. Remaining items record the root `lost` (partial is never
complete). `RUN wait --phase report` must exit 0 before step 4; RUN refuses the report and inspection close
while any root is pending.

**4. Report per changed root.** Body (window stated at the top; findings by severity with
`file:line`, label and verification step; silent-catch census appendix) to a temp file under `<sd>`, then
`RUN write-report --dir <path>/audits --stem <date>__slop-audit --roots <name> --in-root <name> --body-file <body> --run-id <rid> --state-dir <sd>`.
Prior reports are never moved, renamed or deleted.

**5. Close inspection** after the last root: `RUN record --inspection complete` (or `failed` → §Failure).

**6. Gates (rule 6).** Per ledger, enroll first:
`ENROLL criteria --ledger <ledger> --criteria-json <sd>/slop-audit-sweep/<rid>.<name>.criteria.json` (AC-1
`met` only when that root's coverage is `complete`, else `unmet`; AC-2 `met` with the finding count) →
`ENROLL findings --ledger <ledger> --findings-json <sd>/slop-audit-sweep/<rid>.<name>.findings.json --receiver <owner>
--next-action "verify each open finding" --acceptance "each finding confirmed, refuted or intended with evidence"
--follow-up "next sweep"` (one entry per report finding with its label; a clean root has `[]`) →
`GRRP apply <ledger>` → `GRRP gate <ledger> --json [--queue <q>]`
(exit 1/2 are verdicts; exit 3 → §Failure) → `GRRP handoff <ledger> --record`. Write one JSON object
`{"<ledger path>": <gate json>, …}` to `<sd>/slop-audit-sweep/<rid>.gate.json` (`{}` when every root
was unchanged) → `RUN record --gate-file <that file>`. Verdicts verbatim, never softened.

**6a. Outputs audit.** `RUN outputs-audit --scan-dir <path> … [--exclude <file>] --run-id <rid> --state-dir <sd>` (one `--scan-dir` per root)
lists every file created or modified since run start that is not a dispatched output, a recorded
report or ledger, or under the evidence dir. Exit 1 → each hit is recorded `defect:undeclared-outputs`
and the run can only be `degraded`. Run it before step 7 and before any finalize (§Failure).

**7. Run complete, publish, deliver.** `RUN record --run <ok|degraded>` (refused if softer than the
coverage implies) → `RUN finish [--watchdog-dir …]` (heartbeat carries every ledger) → `RUN publish`
→ by mode: `file` → `RUN deliver-file --run-id <rid> --state-dir <sd>`, which create-only writes
`<sd>/slop-audit-sweep/<rid>/RESULTS.md` and records delivery `delivered` with ref = that path
(`RUN deliver --state delivered` is refused in file mode); `none` → `RUN deliver --state skipped`.

Final message: one line per root, Critical/High first — `unchanged (<rev>)`, or
`<severity counts> · <report path> · <gate verdict>`.

## Failure (any step)

`RUN finalize --reason "<step>: <error>" --run-id <rid> --state-dir <sd> [--watchdog-dir …]` → run
`failed`, inspection `failed` if running, publication `not-published`, heartbeat finished `failed`.
Reports already written stay; they are named in the reason. Never leave a heartbeat unfinished.

## Dependencies (rule 9)

| Dependency | Owner | Discovery | Missing → |
|---|---|---|---|
| `audit_run.py`, `audit_lib.py`, `heartbeat.py` | `weekly-full-audit` skill | sibling skill dir | preflight fail |
| heartbeat schema | Workspace watchdog | `heartbeat.py` header | — |
| `local-audit/slop_prepass.py` | Claude-Tools | `--tools-root` / `CLAUDE_TOOLS_ROOT` | preflight fail |
| `/slop-audit`, `grrp.py` | Claude-Profile skills | sibling skill dirs | preflight fail |
| git | host | PATH | preflight fail |

The local Ollama second opinion is not a dependency; it stays interactive, outside this skill.

## White-only

Nothing. Watchdog display only.
