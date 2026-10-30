---
name: weekly-full-audit
description: >-
  Portable weekly FULL audit of a code repo plus its workspace clone (optional
  Add-Ins root): ctxcheck first, lenses via /review-all deep, persona-agent refute +
  proving tests, breach-chain overlay, hygiene checkers, one GRRP-gated report with
  revision-based change-since-last-run. All roots are arguments. Inspection is
  read-only on targets; artifact writes go only to declared outputs; remediation is
  a separate stage after publication (`--handoff` only; autofix dropped, owner
  decision D1). Delivery is a RESULTS.md file (D2).
---

# /weekly-full-audit

Contract: `docs/audit-revamp-anchor.draft.md` rules 1–10 (Claude-Profile repo). Run control is
`scripts/audit_run.py`; every step below is one of its subcommands, and each subcommand
refuses an out-of-order or contract-breaking call with exit 2. Never work around a refusal:
finalize the run as failed (§Failure) and report the refusal text.

`ENROLL` = `python <this-skill-dir>/scripts/grrp_enroll.py` (weekly-full-audit skill dir). It
writes the parts of the GRRP contract `init` leaves blank and refuses (exit 2) any write that would
overwrite a different value; it never upgrades a label or marks a criterion met without evidence.

Below, `RUN` means `python <this-skill-dir>/scripts/audit_run.py` and `GRRP` means
`python <skills-dir>/review-to-resolution/scripts/grrp.py`. `<sd>` is `--state-dir`, `<rid>` the
minted run_id. Every RUN call after `start` except `slug` (which takes only
`--run-id` and `--prefix`) carries `--run-id <rid> --state-dir <sd>`.

## Identity (rule 5)

- `task_id` `weekly-full-audit` · `run_id` `weekly-full-audit-<UTC yyyymmddThhmmssZ>-<6 hex>` (minted by `start`, never by hand).
- GRRP slug `weekly-full-audit-<date>-<6 hex>` (`RUN slug --run-id <rid>`).
- Run record `<sd>/weekly-full-audit/<rid>.json` · heartbeat `<watchdog-dir>/heartbeats/<task>/<rid>.json`.
- Report `<out>/weekly-full-audit_<date>[-N].md`, exclusive create; a collision takes the next `-N`, never overwrites.

## Invocation

```
/weekly-full-audit --repo-root <code-repo> --workspace-root <workspace-clone> [--addins-root <addins>]
                   --state-dir <dir> --out <dir> [--ledger-dir <dir>] [--watchdog-dir <dir>]
                   [--tools-root <dir>] [--checker-dir <dir>] [--baseline <root>=<rev> …]
                   [--hyg8-live-root <dir>] [--hyg8-baseline-root <dir>] [--grrp-queue <queue.yaml>]
                   [--checklist <file>|none] [--run-log-dir <dir>] --deliver <file|none> [--handoff]
```

Defaults: `--tools-root` = env `CLAUDE_TOOLS_ROOT`; `--checker-dir` = `<workspace-root>/scripts`;
`--ledger-dir` = `<workspace-root>/02_Reference/Audit and Scan Info/grrp`; `--watchdog-dir` = env
`AUDIT_WATCHDOG_DIR`, else `~/.claude/scheduled-tasks/_watchdog`; `--grrp-queue` = env `GRRP_QUEUE`
(GRRP's own default otherwise); `--checklist` = `<workspace-root>/02_Reference/Audit and Scan
Info/BIMpossible_Verification_Checklist.md` (preflight fails when the default or given file is
missing; `start` echoes the resolved path as `tools.checklist`); `--run-log-dir` = `<workspace-root>/01_BuildLog`. An isolated validation run
passes `--checklist none` (step 10 is recorded `NOT RUN (isolated validation)`) and a
`--run-log-dir` outside every target.
Activation (owner decisions 2026-10-30): `--deliver file` (D2: delivery is the results file, step 14)
or `--deliver none`; `gmail-draft` is refused (no Gmail, no branch/PR — the anchor's earlier D2
branch/PR option is superseded). `--autofix` is dropped (D1) and never activated. The records trail
is the RESULTS.md path plus the run log and the GRRP ledger. Root names inside RUN are `repo`, `workspace`, `addins`.

## Permission tiers (rule 1)

| Tier | May touch | Steps |
|---|---|---|
| Inspection | nothing under any target root is created, modified, moved or deleted | 2–8 |
| Artifact writes | `--out` report · `--ledger-dir` · `--state-dir` · `--watchdog-dir` heartbeat · `<workspace-root>/01_BuildLog/` run log · Checklist rows (append) | 0–1, 9–14 |
| Remediation (default off) | `--handoff`: `audit-resolution` on the published report (`--autofix` dropped, D1) | 15 |

Protected, never written by any tier: `.env`, `backend/guard.py`, any `99_Archive/`, alembic
`versions/`, any `_backups/`. Never print `.env`. Never reference ClickUp.

## Steps — the order is the contract

**0. Start.**
`RUN start --task weekly-full-audit --root repo=<repo-root> --root workspace=<workspace-root> [--root addins=<addins-root>] --deliver <file|none> [--handoff] [--tools-root …] [--checker-dir …] --state-dir <sd> [--watchdog-dir …]`
Exit 2 → the run is already finalized `failed` with its heartbeat; report the `failures` list and
stop. Exit 0 → keep `run_id`, `slug`, `coverage`, `personas` from its JSON. It opened inspection,
wrote the run record and the heartbeat, and pre-recorded every absent optional capability as
`NOT RUN (<name> not present)`, `autofix` as `NOT RUN (flag off: --autofix not activated, owner
decision D1)` (dropped; never activated), `checklist` as NOT RUN under `--checklist none`, and, without
`--handoff`, `HANDOFF PENDING: --handoff not given` (this degrades the run by design). An
unreachable required root does not fail the run: it is recorded `root-<name>=lost:Access: …` (degraded);
only when no required root is reachable does `start` fail. Then, per started root,
`RUN dirty-check --root <path>` and keep its `lines` — step 11 repeats it to prove inspection left
every target root as found. The `preflight` and `personas` subcommands are diagnostics only (`start` embeds both). Evidence dir: `RUN evidence-dir --run-id <rid> --state-dir <sd>` creates and prints
`<sd>/weekly-full-audit/<rid>_evidence/`; it holds every lens's evidence, findings and checker output for
this run (never reused across runs). The `check` subcommand refuses evidence or artifacts outside it.

**1. Ledger.** `GRRP init --slug <slug> --dir <ledger-dir> --objective "weekly full audit <rid>"`
then `GRRP batch-init --slug <slug> --dir <ledger-dir>`. Take each path from that command's own
`created:`/`existing:` line — never rebuild it from a date (init names it by the local date,
batch-init by the UTC date; the run's own date is `run_date` in the run record). `RUN record --ledger <ledger path>`.
Run brief: `ENROLL brief --ledger <ledger path> --scope "<root names and windows, filled after step 2>"
--boundaries "inspection + artifact tiers only; no remediation unless --handoff" --done-means "AC-1 every
lens/check ran over its scope; AC-2 every finding is in the report and the ledger" --authority "report
only; findings route to the owner"` (run it after step 2 so Scope names the windows), then
`RUN brief-done --ledger <ledger path> --run-id <rid> --state-dir <sd>`: it refuses an empty or
placeholder Scope/Boundaries/Done means/Authority, and every dispatch (step 6) is refused
until it has run. Order: step 1 ledger → step 2 → ENROLL brief → brief-done → step 3.

**2. Baselines (rule 3).** For each root that started: 
`RUN baseline --root <name>=<path> --report-dir <out> --pattern "weekly-full-audit_*.md" [--override <name>=<rev>] --run-id <rid> --state-dir <sd>`
The baseline is the `head_rev` of the newest *ancestor* prior report by front matter whose run
record exists under `<sd>` with run `ok` or `degraded` — never the newest file by date or name, and
never a revision inferred from a report's date. A legacy report (no `head_rev`), or one with no,
a failed or an unfinished run record, is no baseline: RUN returns `full` with `trust: bootstrap`,
the run inspects full scope, and its own report becomes the baseline only if this run succeeds.
`--override` is recorded as `trust: override` (an operator choice, not an established baseline);
say so in CHANGE-SINCE-LAST-RUN. Use its `window`:
`incremental` → range `<baseline_rev>..<head_rev>`; `full` (no baseline) → range
`4b825dc642cb6eb9a060e54bf8d69288fbee4904..<head_rev>` (the empty tree) and say `no baseline` in
CHANGE-SINCE-LAST-RUN; `unchanged` → that root contributes no new findings.

**3. ctxcheck (CTX-).** Skill `ctxcheck`, or `python <tools-root>/ctxcheck/ctxcheck.py run <repo-root> --json`.
FAIL claims → CTX- findings and the `**ctxcheck:**` header line; WARNs → `## ctxcheck drift`, each
WARN re-checked against the tree (confirmed / stale-claim / false-positive, with the file:line read)
and the docs-drift severity count carried into the header line. Write one `{id, verdict: CONFIRMED|REFUTED}`
per WARN with a JSON serializer, then `RUN ctxcheck-line --ctxcheck-json <evd>/ctxcheck.json --verdicts <json> --run-id <rid> --state-dir <sd>`: it refuses unless verdicts = WARN count, prints the header line
(`… (WARN-confirmed n)`) and records it; `write-report` refuses a body lacking that line (and the
hygiene line) verbatim.

**4. HYG-8 drift.** If `hyg8` coverage was not pre-recorded as NOT RUN:
`python <this-skill-dir>/scripts/hyg8_drift.py --baseline-root <hyg8-baseline-root> [--live-root <hyg8-live-root>] --json`
(baseline default `<workspace-root>/system/scheduled-tasks/_live`; live default `~/.claude/scheduled-tasks`).
Exit 1 → a HYG-8 finding per drifted task. Exit 2 → `RUN record --coverage "hyg8=lost:<stderr>"`.
Read-only: it never refreshes baselines.

**5. Baseline artifacts.** Read (never edit) the prior report chosen in step 2, the Checklist and
resolved files, the foundation audit and `security-review-full_*.md` under `--workspace-root`.

**6. Lenses — `/review-all deep --range <range>` on `--repo-root`** (rule 10: it owns blind
dispatch and convergence for SEC/RE/ARCH/CQ/FE/PERF; no Workflow tool). Before dispatch register every
required child: `RUN record --require sec re arch cq fe perf hyg slop ws [addins] --run-id <rid> --state-dir <sd>`.
Declare every child before it starts: `RUN dispatch --key <k> --shard <id> --method <ref file> … --output <evd>/<file> … --run-id <rid> --state-dir <sd>` (methods hashed at dispatch; outputs create-only under the evidence dir — a child never picks its own location). Dispatch picks a random non-blank line k per method and prints the lines in `child_must_attest`. The child's brief names those exact outputs and methods and copies `child_must_attest` verbatim, and each Markdown output carries, per declared method, one `method-sha256: <path> <sha256 of the file>` line and one `method-line: <path> <k> <sha256 of line k, stripped of surrounding whitespace>` line (the file hash proves it was hashed; the line hash proves line k was read; the transcript stays the secondary proof). `RUN check --key <k> --state returned` is refused while any shard of the key lacks a declared output, an attestation, a matching file hash, or a matching line-k hash, or when evidence/artifact/findings is not a declared output.
Each child returns through `RUN check --key <k> --state returned --inspected … --remaining … --why <why> --evidence <evd>/<k>.md --artifact <evd>/<file> --findings <evd>/<k>.findings.json`
(`--remaining`/`--why`/`--artifact`/`--findings` optional; arguments, so paths are
serialized by RUN, never hand-written JSON; `--completion-file` is accepted only if written by a serializer and
an invalid escape is refused with a diagnostic, never repaired). Evidence and every declared artifact must exist,
be non-empty and lie under the evidence dir; findings must parse with id/label/evidence, valid labels, unique ids.
This is a per-check completion plan that
replaces any sampling cap: it names what was read, what was not and why. A child that errors or does
not return → `RUN check --key <k> --state failed --reason <why>`. Remaining items record the check
`lost` ("partial: N remaining"); partial is never complete. `RUN wait --phase convergence` must exit 0
before convergence; RUN refuses convergence, refutation, proving tests, the report and inspection close
while any required check is pending (a child returning early never releases the parent).
Workspace lens (`ws`): HYG/ARCH over `<workspace-root>` on its own window — scripts, system/, docs —
and the workspace commit-before-edit rule (edits to governed workspace files must follow a commit);
inspection here is read-only and makes no commits, so the rule is checked against the window's history,
never exercised. FE includes a `first-time-user` discovery pass (first-run path, empty states, error
copy) alongside the matrix. Fold
`<repo-root>/.claude/commands/security-review-full.md` into SEC when present. If `/review-all` opens
its own ledger, `RUN record --ledger <its path>`. A lens that does not return →
`RUN record --coverage "<lens>=lost:<why>"`. Lens references: `references/` (copies; `SOURCE.md` pins
the originals, which are never edited).

| Lens | Reference | Refute persona |
|---|---|---|
| SEC- | references/security-audit.md | security-engineer |
| RE- | references/reliability-audit.md | qa-engineer |
| ARCH- | references/google-audit.md | tech-lead |
| CQ- | references/perp-audit.md | tech-lead |
| FE- | references/perp-audit.md, audit-matrix.md | first-time-user |
| PERF- | references/performance-audit.md | qa-engineer |
| HYG- | references/hygiene-audit.md | — |
| SLOP- | references/slop-audit.md | — |
| Add-Ins | that root's `revit-functionality-audit` skill | bim-manager |

**7. Slop (rule 4).** For the newest `<repo-root>/audits/*__slop-audit*.md`:
`RUN reuse-check --sweep-report <report> --state-dir <sweep state-dir> --root <sweep root name>=<repo-root> --weekly-baseline <repo baseline_rev; omit when full>`
(the sweep names each root by its folder name). Exit 0 → reuse its findings verbatim. Exit 1, or no
sweep report (the code repo is excluded from the sweep by design) → run Skill `slop-audit` nested on the window (it skips
its own GRRP entry as a nested lens). `slop` NOT RUN when its tool is absent.
Add-Ins (only if `addins` started): `/revit-functionality-audit` nested on that root over the
window step 2 chose (`full` → full scope; never "last N commits"). Its build checks need a build
and are approval-gated, not host-gated: unless the run was started with an owner-approved build,
`RUN record --coverage "addins-build=not-run-approval:Add-Ins build not authorized for this run"`.

**8. Refute + proving tests.** `RUN wait --phase refutation` (then `--phase proving` before the
qa pass) must exit 0. One Agent per converged Critical/High finding, job = refute, using the
persona from step 6 when `start` listed it; when the persona is absent use `general-purpose` and
note `persona absent` (local-only agents are not portable). Unrefuted but undemonstrated →
HYPOTHESIS, never upgraded. One `qa-engineer` pass over the survivors → "required proving tests".
Breach-chain overlay: nested `<repo-root>/.claude/skills/bimpossible-breach-chain` when present, its
own artifact. Hygiene: `RUN hygiene-line --run-id <rid> --state-dir <sd>` runs G3/G4 in the repo and workspace
roots (cwd = each root) and prints the exact `**Docs-hygiene (HYG-13):**` line — copy it verbatim;
never hand-write the marks. `hygiene-line` measures each root at its inspected revision (the
`head_rev` step 2 recorded): in place when the working tree is that revision, else in a temporary
detached checkout of it; with no recorded revision it measures the working tree and says so in the
result's `note`. This supersedes the "working tree at HEAD" wording in part (2) below for the
checker run itself; part (2)'s ban on re-running against an older commit for a baseline stands.
`## G4 docs-control`: all 7 parts, the Add-Ins row-set when `addins`
started, HYG-14 lag and duplicate entries, and removal evidence for every dropped row. The parts, verbatim from the live prompt:
(1) Metrics table: totalDocWords / docFileCount / largestDocWords / docToCodeRatio, each as `value / ceiling (headroom %, headroom words, mode)`. Mark a metric whose headroom is below 2% as TIGHT. Mark ✗ only for a `mode: block` check over its ceiling. A report-mode check over its line shows `OVER (report)` and is never ✗.
(2) Change since previous run: compare the current checker output with the previous `weekly-full-audit_*.md`'s `## G4 docs-control` metrics table (the previous recorded run is the ONLY baseline). Match each row-set to the same repo and config in that table. Runs before 2026-10-05 carry BIMpossible rows only. If no previous report carries a given row-set, write `baseline unavailable` for that row-set's delta and record only its current values — never reconstruct a historical value by recounting, checking out, or re-running the checker against an older commit; the checker measures the working tree at HEAD and nothing else. With a baseline: net delta in words and files and words/day; then gross added vs removed from `git log --numstat -M --format=%h -- '*.md'` over the window (line counts, not G4 words), naming the 3 largest additions and the 3 largest removals with whether each removal was a move into an excluded `_archive` path (rename) or a real deletion/trim.
(3) Largest-document outliers: top 5 counted files by words; any pair of files sharing a title prefix whose combined words exceed that row-set's largestDocWords ceiling (a ceiling dodged by splitting is reported, not accepted). Size signal is per configuration, never a fixed word count: read each row-set's own largestDocWords ceiling from its checker output (do not hardcode it), and classify every counted doc as `OVER` (> ceiling), `NEAR` (≥ 80% of ceiling) or OK, printed as `NEAR <path> <words> / <ceiling> (<pct>%)`. Separate informational growth signal: the counted doc with the largest word gain since the previous recorded run (same baseline rule as (2); `baseline unavailable` otherwise). OK/NEAR/OVER and the growth signal are informational only — no exit code, CI, receipt, merge or delivery effect; only the checker's own block-mode result (1) can be ✗. For each `NEAR` doc give a next action set by the kind of doc:
- a ledger: "check its own retention rule ran (Checklist: HYG-14 lag)";
- a `…_resolved-<period>.md` file: "start the next period file per HYG-14; never raise the cap";
- a contract or runbook: "split by topic, leave a pointer".
Never recommend "trim".
(4) Metric-driven removal evidence: for every commit in the window whose `*.md` diff shrinks a COUNTED file by more than 30% or more than 100 words (from that commit's own diff, `git show <sha> -- <path>`), where that file carries no closed/superseded/archived status marker, report commit, file, words before → after, and quote the kinds of lines removed (paths, line numbers, test names, counts = evidence loss); treat a commit message mentioning budget/headroom/G4, or the previous recorded run having reported headroom below 2%, as confirming that the removal served the metric (no G4 value is ever attributed to a historical commit). Flag each as HYG- finding `HYG-G4-TRIM` (severity Medium: information loss caused by a control). Zero such commits is a required explicit statement, not silence.
(5) Counterproductive-control signal: report the checker's CURRENT pass/fail at HEAD only, and whether the previous recorded run reported TIGHT or BREACHED; state whether any PR in the window was merged without a docs-hygiene-green receipt while G4 was the only failing lane (from the receipts and PR bodies, not from re-running the checker). Do not claim G4 state for any commit other than HEAD.
(6) One-line verdict: HEALTHY / TIGHT / BREACHED / COUNTERPRODUCTIVE-BEHAVIOUR-OBSERVED. It is the worst across all row-sets; name the row-set that set it. Report (5)'s pass/fail per row-set. The verdict line comes with a `Drift:` line naming anything unusual: a growth step > 2× the prior 4-week average, a new top-level counted directory, or a new excludePath. BREACHED means a block-mode breach. A report-mode over drives TIGHT or a `Drift:` line, never BREACHED. The audit never turns a report-only metric into a red G4. Drift is REPORTED for the owner; this run never redesigns the control in response.
(7) HYG-14 backstop. Scope: only the rows of the live Checklist's § Weekly security review findings, which is the section HYG-14 governs. Feature-verification and policy sections are out of scope. Classify each row by its status word (HYG-14's terminal list), never by its icon. Write two lines:
- `HYG-14 lag: N terminal finding rows still in that section`. If N > 0 on this run and the previous recorded run, add a `Drift:` line naming the rows.
- `HYG-14 duplicates: N IDs in both the live Checklist and any resolved file` (every `BIMpossible_Verification_Checklist_resolved-*.md`). If N > 0, add a `Drift:` line naming the IDs on the first run, because a duplicate is an incomplete resolution, not lag.
This audit never moves or deletes rows itself.
`## Startup context` from
`python <this-skill-dir>/scripts/startup_context.py --repo <repo-root> [--repo <workspace-root>] [--repo <addins-root>]`.
G4 and Startup context are report-only and never trigger a fix.

**9. Report.** `RUN wait --phase report` must exit 0 (`write-report` enforces it). Write the body to a temp file under `<sd>`, then
`RUN write-report --dir <out> --stem weekly-full-audit_<date> --body-file <body> --run-id <rid> --state-dir <sd>`
(add `--in-root workspace` when `<out>` lies inside the workspace root). It prepends front matter
(`task_id, run_id, roots{identity, path_arg, baseline_rev, head_rev, window}`) and exclusive-creates
the `-N` name. Then `node <checker-dir>/check-audit-report-header.mjs --file <report>`; exit 1 →
fix the body and write again (a new `-N` file; the bad one stays and is named in the report).
Body: title prefix `DOCS DRIFT — ` on any ctxcheck FAIL and `RUN DEGRADED — ` on any lost lens;
header lines `**ctxcheck:**` and `**Docs-hygiene (HYG-13):**` within the first 15 lines; sections
executive summary (top findings, the top breach chain and its cheapest breaking control), CHANGE-SINCE-LAST-RUN (per root `baseline_rev..head_rev`), coverage gaps (every
non-complete coverage entry, verbatim), findings by lens, `## ctxcheck drift`, `## G4 docs-control`,
`## Startup context`, state-machine gaps, required proving tests, what's solid.
Prior reports are never moved, archived or deleted.

**10. Checklist.** Append a row (open ⚠️) per new Medium+ SEC finding to `tools.checklist` from the
`start` JSON (never under `_backups/`). `--checklist none` → no write; `start` already recorded it NOT RUN.

**11. Close inspection.** `RUN dirty-check --root <path>` per started root; output differing from
step 0 → a `root-<name>=lost:modified during inspection` coverage entry and a §Failure note. `RUN record --coverage "<lens>=complete" … --inspection complete`, naming only lenses
that actually completed (or `--inspection failed` → §Failure). A `lost` or `handoff-pending` entry cannot
be re-marked `complete`; RUN refuses it.

**12. GRRP gate (rule 6).** Enroll first:
`ENROLL criteria --ledger <ledger> --criteria-json <sd>/weekly-full-audit/<rid>.criteria.json` — AC-1
`met` only when every coverage entry is `complete`/`unchanged` (evidence: the coverage list), else
`unmet` naming the lost/degraded entries; AC-2 `met` with the finding count in both. Then
`ENROLL findings --ledger <ledger> --findings-json <sd>/weekly-full-audit/<rid>.findings.json
--receiver <owner> --next-action "verify each open finding" --acceptance "each finding confirmed,
refuted or intended with evidence" --follow-up "next weekly run"`: one entry per report finding, with
its report label (HYPOTHESIS → unassessed, CONFIRMED → confirmed_defect, both open + transferred;
REFUTED/INTENDED → resolved with the why). Then `GRRP apply <ledger>` → `GRRP gate <ledger> --json [--queue <q>] > <sd>/weekly-full-audit/<rid>.gate.json`
(exit 1/2 are verdicts, not errors; exit 3 → §Failure) → `RUN record --gate-file <that file>`.
`GRRP handoff <ledger> --record`. Then finalize the batch manifest from step 1:
`ENROLL batch --manifest <manifest> --runs "weekly-full-audit <rid>" --follow-through "<receiver> verifies
each open finding; next weekly run re-checks" --authority "report only; no remediation unless --handoff"
--authority-gaps none --archive-evidence <sd> --add-ledger W=<ledger> --gate W=<that gate file>
--report W=<report> --receiver <owner> --end now` (one `--add-ledger/--gate/--report` set per ledger
the run recorded, e.g. `/review-all`'s own). It derives each ledger's items from its own gate
verdict — a gate item (`check: ledger <id> complete`, open unless the gate is complete), one
transferred obligation carrying every unacknowledged transfer, deferral and portfolio-open entry,
and the report artifact — and never writes `owner_accepted`. Then `GRRP batch <manifest> --record`.
Three outcomes stay distinct: exit 3 `invalid` = an implementation failure (§Failure); exit 1
`incomplete` / 2 `blocked` = a valid batch with open findings or criteria (the normal result of a
run that found defects — inspection complete ≠ remediation complete); exit 0 `complete`. Print the
verdict verbatim; never soften it.
Run log: `<run-log-dir>/<run_date>__weekly-full-audit-run[-N].md` (next free `-N`; `run_date` from the run record).

**12a. Outputs audit.** `RUN outputs-audit --scan-dir <repo-root> --scan-dir <workspace-root> [--scan-dir <addins-root>] --scan-dir <out> [--exclude <file>] --run-id <rid> --state-dir <sd>`
(`--exclude` once per file the run legitimately wrote that is not on the record, e.g. the run log
and batch manifest). It lists every file created or modified since run start that is not a
dispatched output, a recorded artifact or ledger, the Checklist or under the evidence dir. Exit 1 →
each hit is recorded `defect:undeclared-outputs` and the run can only be `degraded`; name the files
in the run log. Run it before step 13 and before any finalize (§Failure).

**13. Run complete (rule 7).** `RUN record --run <ok|degraded>`; it refuses an outcome softer than
the coverage implies. Then `RUN finish [--watchdog-dir …]` (heartbeat `finished`, remediation `not-run`).

**Final chat summary** (after step 13): report path, ctxcheck line, severity counts, top 5 findings,
change since last run, the biggest risk, and the GRRP batch verdict verbatim.

**14. Publish + deliver.** `RUN publish` (refused before run outcome and gate), then by mode:
- `file` (D2, 2026-10-30): `RUN deliver-file --run-id <rid> --state-dir <sd> --run-log <run log path>`.
  It create-only writes `<sd>/weekly-full-audit/<rid>/RESULTS.md` (run id, outcome, run log,
  published reports, ledgers, gate verdict verbatim; an existing file gets a `-N` name, never overwritten) and records delivery
  `delivered` with ref = that path. `RUN deliver --state delivered` is refused in file mode.
- `none`: `RUN deliver --state skipped`.
`gmail-draft` is refused (no Gmail); there is no branch/PR delivery — the anchor's earlier D2
branch/PR option is superseded. Sending is never a skill step.

**15. Remediation (separate stage, after publication).** Only with `--handoff`.
`RUN remediation start` — exit 1 means not started (e.g. `not-run (dirty tree)`) and is recorded as such.
- `--handoff`: run `audit-resolution` on the published report; outcome into the run log.
- `--autofix` is dropped (D1, 2026-10-30) and refused; the `fix` subcommands stay built but no
  step uses them.
`RUN remediation finish` → remediation `ok|failed|reverted` on the record and heartbeat. The
published report is never rewritten.

## Failure (any step)

`RUN finalize --reason "<step>: <error>" --run-id <rid> --state-dir <sd> [--watchdog-dir …]`.
Before publication: run `failed`, inspection `failed` if it was running, publication `not-published`,
heartbeat finished `failed`. After publication: the published run outcome stands and only the
remediation state becomes `failed`. Never leave a heartbeat unfinished.

## Dependencies (rule 9)

| Dependency | Owner | Discovery | Missing → |
|---|---|---|---|
| `scripts/audit_lib.py`, `audit_run.py`, `heartbeat.py`, `hyg8_drift.py`, `startup_context.py` | this skill | bundled | — |
| heartbeat schema | Workspace watchdog | `scripts/heartbeat.py` header | — |
| `ctxcheck/ctxcheck.py` | Claude-Tools | `--tools-root` / `CLAUDE_TOOLS_ROOT` | preflight fail |
| `local-audit/slop_prepass.py` | Claude-Tools | tools root | slop `NOT RUN` |
| `check-*.mjs` (3) + `node` | Workspace | `--checker-dir` | preflight fail |
| `grrp.py`, `/review-all`, `/ctxcheck`, `/slop-audit` | Claude-Profile skills | sibling skill dirs | preflight fail |
| persona agents | local user agents dir (untracked) | `start` JSON `personas` | `general-purpose` + `persona absent` |
| breach-chain, security-review-full, audit-resolution | code repo `.claude/` | repo root | `NOT RUN` (audit-resolution: `--handoff` refused) |
| `revit-functionality-audit` | Add-Ins `.claude/` | addins root | `NOT RUN` |
| HYG-8 live and baseline roots | host + Workspace | `--hyg8-live-root`, `--hyg8-baseline-root` | `NOT RUN` |

## Approval-gated (not host-gated)

Nothing here is disabled for being on White; the run is on White. Off unless the owner activates
or authorizes it: `--handoff` (remediation stage) · Add-Ins build checks (a build). Settled
2026-10-30: `--deliver file` is active (D2); `--deliver gmail-draft` and `--autofix` (D1) are refused. Each one not run is recorded with that reason.
