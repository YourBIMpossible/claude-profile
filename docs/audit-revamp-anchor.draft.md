# Audit revamp — anchor (DRAFT, Phase 1)

Status: draft, revision 2.3 (2026-10-30; D1–D4 closed, see decision log at end; M2 isolated validation met 2026-10-26; cutover not authorized) · job type: LONG-TASK-HARNESS, Phase 0 split + 4 phases
Owner instruction (2026-10-17, chat): "Revamp first: start the Phase 1 anchor session." Drafting milestone accepted 2026-10-17 (not approval of D1–D4, implementation, schedules, or migration).
Source assessment: `{tools}\.claude\worktrees\evidence-review-remediation-8707ae\reports\2026-10-17__audits-to-mybuddy-and-revamp-assessment.md` §2–5.
Live prompts under rewrite (NOT edited by this job until Phase 3 cutover): `~\.claude\scheduled-tasks\bimpossible-weekly-full-audit\SKILL.md`, `...\slop-audit-weekly\SKILL.md`.

## Decision needed (owner — all four closed as of 2026-10-30: D1 B, D2 B file-only, D3 none, D4 six routines; see end)

Each is designed to go either way; the skills carry a switch, not an assumption. None of the contract rules below depend on these answers.

| # | Decision | If A | If B | Skill switch |
|---|---|---|---|---|
| D1 | Keep the Auto-Fix Pass? (the only remediation-permission stage; the reason the audit needs White's live tree) | A = keep: White-only remediation stage after the published inspection run | B = drop: inspection-only everywhere; fix items become transfers to `audit-resolution` / `/next` | `--autofix`, default OFF |
| D2 | Report delivery: Gmail draft or results file / PR notice? | A = Gmail: White-only step creating a **draft** (sending stays an owner action) | B = file: `RESULTS.md` block + branch/PR; a White SessionStart hook surfaces it | `--deliver {gmail-draft\|file\|none}`, required, no default |
| D3 | MyBuddy account: hosting deploy account or separate audit account? | A = deploy account: shared clones; scope rule: never touch `/srv/bimpossible` or its ports | B = separate: own `$HOME`, clones, `~/.claude` | Phase 2 only; Phase 1 skills take explicit roots and never assume a `$HOME` layout |
| D4 | Scope of "all": the two live audits only, or also the four related weeklies (context-weight, nl-filter drift, next-sync, graphify funnel)? | A = two: this anchor closes at Phase 4 | B = six: Phase 1b appended, 4 more skills, same pattern | Phase row `1b (if D4=B)`; nothing in 1a depends on it |

What each decision blocks (nothing else waits on it):
- D1 blocks only the **activation** of step 13 remediation (`--autofix` on) in 1a.iv and any scheduled run; building the step and its rollback (1a.ii) proceeds either way, flag off.
- D2 blocks only the `--deliver gmail-draft` and `--deliver file`/PR-notice **implementations** in 1a.ii and the White results-reader hook in Phase 3; 1a.iv runs with `--deliver none` regardless.
- D3 blocks Phase 2 entry only (MyBuddy account, clones, cron); no Phase 1a step depends on it.
- D4 blocks Phase 1b entry only; no Phase 1a step depends on it.
1a.i, 1a.iii and every AC except the flag-on halves of AC-6 are decision-independent. M1 implementation is not authorized by M0 acceptance.

## Milestones and acceptance (reported separately)

**M0 — drafting milestone (this session): accepted 2026-10-17.** Deliverables: this anchor, two skeleton skills, White-only list, hand-over note. Revision 2 corrects contract defects raised at acceptance (permission tiers, preflight, revision baselines, identities, publication order, archive/rollback, dependency ownership, persona investigation, engine ownership, run vs remediation outcome). M0 does not claim any AC below.

**M1 — Phase 1a runnable milestone: in progress, not complete. 8/9 passed (AC-3..AC-9 on isolated fixtures; AC-1 with `references/` exempt by owner 2026-10-17); AC-2 pending the 1a.iv run, authorized by owner chat 2026-10-17 ("one manual run of each skill on White, --deliver none, --autofix off"). Implementation authorized by owner chat 2026-10-17 ("Proceed with M1 / Phase 1a").** Two weekly audits run from path-free skills (`/weekly-full-audit`, `/slop-audit-sweep`) that produce the same artifacts the live prompts produce today, newer tooling at the core, on White, unscheduled, side by side with the live tasks.

Done means (acceptance, N = 9):
- AC-1 Both skills exist under `{profile}\skills\<name>\`; every input is an argument or env var. Check: no drive-letter path, no home-folder path, no `py `, `pwsh` token in `SKILL.md`, `references/`, `scripts/`. **Conflict (2026-10-17):** AC-4 requires verbatim copies, and three copies carry machine paths (`auto-fix.md:32,:89`, `hygiene-audit.md:54`, `performance-audit.md:88,:104`). SKILL.md and scripts of both skills are clean. **Resolved 2026-10-17, owner chat: `references/` exempt from AC-1** (provenance copies, never executed); the check covers `SKILL.md` and `scripts/` only.
- AC-2 One manual run of each skill on White reaches the GRRP gate with `verdict` printed verbatim and writes the same report filename pattern + header contract the live task writes (`weekly-full-audit_<date>[-N].md` with `**ctxcheck:**` and `**Docs-hygiene (HYG-13):**` in the first 15 lines; `<repo>/audits/<date>__slop-audit[-N].md`), each carrying `run_id` and revision front matter.
- AC-3 `node <workspace-root>/scripts/check-audit-report-header.mjs` accepts the skill-written weekly report. If the checker rejects the revision front matter or `-N` suffix, the finding goes to the Workspace repo (script owner), not into the skill as a workaround.
- AC-4 The nine lens reference files (corrected from eight 2026-10-17: the source folder holds nine) are **copied** (never moved) into `skills/weekly-full-audit/references/` with `SOURCE.md` (source path + commit SHA); originals untouched.
- AC-5 Heartbeat is `scripts/heartbeat.py` in the skill, writing the JSON schema the Workspace watchdog defines (schema owner: `{repos}\BIMpossible-Workspace\system\scheduled-tasks\audit-watchdog\`); verified by the watchdog display after one run.
- AC-6 Every White-only item sits behind a flag defaulting to off and, when off, appears in the coverage-gap section as `NOT RUN (White-only, flag off)`.
- AC-7 Live scheduled-task folders and `04_PromptsThatWorked` are byte-identical to 2026-10-17.
- AC-8 Slop-result reuse (rule 4) is demonstrated to refuse a sweep report whose `head_rev` matches but whose scope differs (different root set or path_arg) or whose run record is not `run: ok` with that root's coverage complete; the refused case runs `/slop-audit` nested.
- AC-9 Autofix rollback (rule 8) is demonstrated to refuse restoring a file whose current blob differs from the post-fix blob it recorded (concurrent change); the file is left as found, the fix is logged `rollback-refused (concurrent change)`, remediation outcome `failed`.

Not in M1: scheduling, MyBuddy clones, cron, disabling live tasks, tombstone retirement, moving any local configuration.

## Contract rules (decision-independent; both skills)

1. **Three permission tiers, never conflated.** *Inspection*: read-only on every target root; no file under a target root is created or modified. *Artifact writes*: only to declared output locations — report, GRRP ledger, run record, run log, Checklist rows, heartbeat JSON, `RESULTS.md`; each location is an explicit argument or derived from one and listed in the skill's §Outputs. *Remediation*: `--autofix` / `--handoff` only, White-only, default off, runs **after** the inspection run is complete and published, with its own outcome. No skill text may claim "read-only" while the run writes artifacts; the claim is "inspection is read-only on targets".
2. **Preflight.** Required roots (`--repo-root`, `--workspace-root`, `CLAUDE_TOOLS_ROOT`) missing or not a git checkout → run outcome `failed`, heartbeat `failed` with reason, no report, exit. Optional roots/tools (`--addins-root`, breach-chain skill, security-review-full command, skillspector, scheduled-task folder) missing → that lens `NOT RUN (optional input absent: <name>)` in coverage gaps; run may still be `ok`.
3. **Revision-based baselines.** Every report carries front matter `task_id`, `run_id`, `roots: {<name>: {path_arg, head_rev}}`, `baseline_rev` per root. The incremental window is `baseline_rev..head_rev`, where `baseline_rev` is read from the newest prior report's front matter (newest = the one whose `head_rev` is an ancestor-descendant maximum; ties → most recent `run_id`). No parseable baseline → `--baseline <rev>` is required or the lens runs full-scope with coverage note `no baseline`. Dates, file mtimes and "≤7 days" are never used to decide scope or reuse.
4. **Slop-report reuse** (weekly folding a sweep report): reuse only when, for that root, the sweep report's `head_rev` equals the current `head_rev` **and** its scope matches (same root identity and `path_arg`, no narrower window) **and** its run record shows `run: ok` with that root's coverage complete (no lost check, no `no baseline` note unless the weekly is also full-scope); otherwise run `/slop-audit` nested. Age is irrelevant.
5. **Identities and names.** `task_id` is stable: `weekly-full-audit`, `slop-audit-sweep`. `run_id` = `<task_id>-<UTC yyyymmddThhmmssZ>-<6 hex random>`. Report filename keeps the live pattern; a same-name collision gets `-N` chosen by exclusive-create (`O_EXCL`), never by listing then writing. GRRP ledger slug = `<task_id>-<date>-<run_id short>` (dated slugs per enrollment rules, collision-safe). Run record = `<state-dir>/<task_id>/<run_id>.json`.
6. **Publication order.** Inspection → report written → GRRP `apply` → `gate` → **then** publication (`--deliver`) of the final inspection outcome. If anything is published earlier (e.g. a progress heartbeat), it is labelled `PRELIMINARY — not gated`. Gmail path creates a draft only; sending is an owner action and never a skill step.
7. **Run outcome vs remediation outcome.** Two fields, both in the run record and heartbeat: `run: ok|degraded|failed` (inspection + gate) and `remediation: not-run|ok|failed|reverted`. Run completion is declared before remediation starts; remediation never changes the published inspection report — it appends a remediation record.
8. **No archive, defined rollback.** Skills never move, rename, archive or delete prior reports or ledgers (`99_Archive/` is protected and is not an output). Rollback exists only inside `--autofix`: each fix records the pre-fix blob SHAs of the files it touches; each fix also records the post-fix blob SHAs; on verify failure a file is restored to its pre-fix blob only if its current blob still equals the recorded post-fix blob — otherwise the restore is refused, the file left as found, the fix logged `rollback-refused (concurrent change)` and remediation outcome set `failed`; a restored fix is logged `reverted`; nothing else in the tree is touched; a dirty tree at remediation start → remediation `not-run (dirty tree)`.
9. **Dependency discovery and ownership.** Every external script/tool is listed in the skill's §Dependencies with owner repo, discovery (argument/env/derived path), required/optional, and missing behaviour. The skills own only their own `SKILL.md`, `references/` copies and `scripts/` (amended 2026-10-17: `audit_lib.py`, `audit_run.py`, `heartbeat.py`, `hyg8_drift.py`, `startup_context.py`, all under `weekly-full-audit/scripts/`; `slop-audit-sweep` uses them from the sibling skill dir and owns no scripts); checker scripts stay owned by Workspace, ctxcheck/slop_prepass/skillspector by Claude-Tools, the heartbeat JSON schema by the Workspace watchdog.
10. **Engine ownership.** `/review-all` owns lens dispatch (blind parallel) and convergence to one deduped finding set. The skill owns scope resolution, ctxcheck, the post-convergence refute + proving-tests stage (direct `Agent` calls to persona agents, one refuter per Critical/High), hygiene sections, report assembly, GRRP, publication. The Workflow tool is **not** used in 1a; it is a 1a.iv option only if direct Agent fan-out proves insufficient, recorded here if adopted.

## Phases

| Phase | Goal | Done-criterion | Entry point | Status |
|---|---|---|---|---|
| 0 | Owner split decisions D1–D4 | four rows filled | this file | **closed 2026-10-30** (D1 drop, D2 file-only, D3 none, D4 six) |
| 1a | Skeletons → working skills on White | AC-1..AC-9 | `skills/weekly-full-audit/SKILL.md`, `skills/slop-audit-sweep/SKILL.md` | 1a.i–1a.iii implemented + fixture-tested 2026-10-17 (46 tests OK), uncommitted; 1a.iv awaits authorization |
| 1b | (D4=B only) four related weeklies as skills | same pattern ×4 | — | gated on D4 |
| 2 | MyBuddy headless worker | clones; `claude -p` under cron/systemd timer; outputs pushed to `audit/<date>` branch + `RESULTS.md`; two clean runs | assessment §3 Phase 2 | gated on D3, persona-agent investigation (Queue), owner `enable-linger`, GitHub sign-in. Entry constraint: a remote worker sees only pushed state — no uncommitted work, untracked local state, Windows-only checks, Gmail or Auto-Fix. **Owner 2026-10-17: audits stay on White; no MyBuddy switch until the owner says so.** |
| 3 | White side: results reader hook, planner opens PR, disable the two live tasks after 2 clean MyBuddy runs | live tasks disabled; watchdog reads new source | assessment §3 Phase 3 | — |
| 4 | Retire tombstones (`-sunday`, `-full`, `-incremental`), HYG-8 baseline re-take, watchdog README, `schedule-run` skill update | hygiene register green | assessment §3 Phase 4 | — |

Phase 1a sub-steps — one fresh session each, per harness:
1. **1a.i references + heartbeat + run record** — copy 8 lens files + `SOURCE.md`; `scripts/heartbeat.py` against the watchdog schema; run-record writer + baseline reader (rule 3/5). Done: AC-4, AC-5 (fixture). **Implemented 2026-10-17.**
2. **1a.ii weekly-full-audit body** — fill the skeleton per its §Steps. Done: AC-1 for this skill. **Implemented 2026-10-17** (SKILL.md clean; references conflict above).
3. **1a.iii slop-audit-sweep body** — fill the skeleton per its §Steps. Done: AC-1 for this skill. **Implemented 2026-10-17** (0 tokens). Ledger default moved from `<repo-root>/audits/grrp` to GRRP's own ledger dir, outside every target (inspection tier).
4. **1a.iv side-by-side run** — both skills manually on White (`--autofix` off, `--deliver none`); diff artifacts against the 2026-10-12 live outputs; header-checker findings routed to Workspace; reuse-refusal and rollback-refusal demonstrated. Done: AC-2, AC-3, AC-6, AC-7, AC-8, AC-9.
5. **1a.v checkpoint** — anchor status updated, lane branch pushed, PR only if Claude-Profile's CLAUDE.md pre-authorizes it, owner review.

## White-only (explicit list)

Behind a flag, default off, coverage note when off:
1. **Auto-Fix Pass** (`--autofix`, remediation tier): `autofix-preflight.sh`, dirty-tree guard, `Verify-Local-CI.ps1 -SkipFrontend`, edits in live checkouts. Fate = D1.
2. **Gmail draft** (`--deliver gmail-draft`): creates a draft only. Fate = D2.
3. **Watchdog display**: Windows Task Scheduler job + SessionStart hook reading `_watchdog\status.json`. Heartbeat *writing* portable; *display* White until Phase 3.
4. **Revit/MSBuild build checks** in the Add-Ins lens — coverage note off-White.
5. **local-audit (Ollama)** — interactive-only by decision 2026-08-08; not part of either skill.
6. **`audit-resolution` handoff** into the live Workspace tree (`--handoff`, remediation tier); headless variant pushes a branch — Phase 2 call tied to D2.
7. **Startup-context telemetry** — ported to Python (`scripts/startup_context.py`) 2026-10-17; portable, report-only.

## Inputs (arguments / env, never machine paths)

`--repo-root` (required; weekly: one; sweep: repeatable) · `--workspace-root` (weekly required) · `--addins-root` (optional) · `CLAUDE_TOOLS_ROOT` (required env) · `--checker-dir` (default `<workspace-root>/scripts`) · `--ledger-dir` · `--state-dir` (run records; default `<ledger-dir>/../audit-runs`) · `--watchdog-dir` (default `~/.claude/scheduled-tasks/_watchdog`) · `--baseline <rev>` (per root, overrides front-matter baseline) · `--deliver` · `--autofix` · `--handoff` · `--out`. Per-skill tables in each SKILL.md.

## Checkpoints / resume

**M1 checkpoint 2026-10-17 (lane `claude/compassionate-bhaskara-3692a3`, uncommitted by owner instruction).** Completed: 1a.i–1a.iii, 46 fixture tests OK, AC-7 re-checked (live audit prompts dated 2026-10-06, `04_PromptsThatWorked` clean). Still open: 1a.iv production side-by-side run (AC-2, real-report AC-3/AC-5), AC-1 references call, D1–D4. Restart: owner reviews the diff, then authorizes 1a.iv.


- After each 1a sub-step: update the Status column, commit on the lane branch, close the session.
- Resume: read this file first, then the skeleton being filled; the live-prompt contract is captured in each skeleton's §Contract carried.
- Transcript disposable; this file + the two skeletons are the state.

## Queue (not in scope; one line each)

- **Persona-agent portability — dependency investigation, no action.** Agents live only in `~\.claude\agents` (plain folder, untracked, not junctioned; `{profile}\agents` absent). Investigation item for Phase 2: how MyBuddy obtains `_HOUSE-RULES.md` + 7 agent files (track in Claude-Profile and junction, or clone-time copy). Active local configuration is not moved by this job.
- HYG-8 is now the Python port `scripts/hyg8_drift.py` (`--live-root`, `--baseline-root`); semantics change when prompts become skills; baseline re-take is Phase 4.
- Docs-hygiene 8-day resolution clock assumes same-tree write-back; Phase 2 push-from-branch may trip it.
- `evidence-*` suite fit for sealing audit findings is unproven; spike with stop condition (1 session; no sealed-finding writer → keep GRRP `evidence:` fields).
- No SSH target for MyBuddy saved in the desktop app on White.
- `check-audit-report-header.mjs` accepts the front matter and `-N` suffix (fixture test against the live checker, 2026-10-17); a real report still checked in 1a.iv.
- Dependency findings 2026-10-17: `CLAUDE_TOOLS_ROOT` unset on White (pass `--tools-root`); GRRP ledger and queue defaults are F:-bound (set `GRRP_LEDGER_DIR`, `GRRP_QUEUE` off-White); `/review-all` opens its own ledger (nested lens rule); full scope uses the empty-tree range; the watchdog monitors only the two live task names (`--watchdog-task` alias until Phase 3); persona agents local-only (above).
- **Captured (owner idea 2026-10-17): spend expiring cloud credits on audits.** Same limits as MyBuddy (pushed state only, no F:, local agents, Gmail, Windows checks, Auto-Fix); moving runs there is migration, not authorized. Fits without migration: read-only reviews of pushed branches, the fixture test suite.

## Gotchas carried from the live prompts

Never write under `_backups\`; never touch `.env`, `backend/guard.py`, `99_Archive\`, alembic versions; `-N` suffix, never overwrite (HYG-16); a lens absent from the coverage-gap section is claimed as run; never soften the GRRP verdict; Checklist rows added open (⚠️), resolution never inside the audit; G4 and Startup-context are report-only; never reference ClickUp; a nested enrolled skill opens no ledger of its own.

**1a.iv attempt 2026-10-17 (owner-authorized one manual run each, --deliver none, --autofix off): both runs FAILED, contract-correct.** Isolated outputs under `{reports}\2026-10-17__audit-revamp-1a4` (state, watchdog, ledgers, reports; nothing written into target repos or the live watchdog). Sweep `slop-audit-sweep-20261018T013944Z-9c2170`: 3 changed roots audited (Workspace 0C/0H/0M/2L, Site 0/0/0/0, Claude-Profile 0/0/2M/3L, all HYPOTHESIS), Claude-Tools unchanged; reports written; gate exit 3 on all ledgers → finalized failed. Weekly `weekly-full-audit-20261018T014216Z-c36eec`: preflight, baselines, ctxcheck, HYG-8 (0 drift), doc checkers, startup context ran; finalized failed before lenses. Defects to fix before a re-run (AC-2 still open):
1. Neither SKILL.md fills the GRRP run brief (Scope/Boundaries/Done means/Authority) or adds `grrp-criterion` blocks or findings → `GRRP gate` verdict `invalid`, exit 3. Blocking.
2. Legacy live reports carry no `head_rev` front matter → every baseline falls to `full`. Run used `--override` from each prior report's stated window head (Site: parent of first commit after its 2026-09-21 report, date-derived — flagged). Needs a documented first-run override rule.
3. `RUN slug` rejects `--state-dir`, contradicting "every RUN call after start carries --state-dir".
4. Ledger and batch file dates differ (10-17 local vs 10-18 UTC).
Restart: fix 1–4 in the lane, re-test on fixtures, owner re-authorizes 1a.iv.

**1a.iv re-run 2026-10-17 (after repairs a–d, 54 tests OK): both runs finished `degraded`, gates `incomplete`, M1 NOT complete.** Outputs `{reports}\2026-10-17__audit-revamp-1a4-2`; closeout `{reports}\2026-10-17__audit-revamp-1a4-2-closeout.md`. AC-1/2/3/6/7 met on rerun; AC-4/8/9 met earlier; AC-5 partial (live watchdog display not exercised). Open: (1) ENROLL batch step — `GRRP batch --record` always `invalid`; (2) default checklist path nonexistent; (3) 11 weekly coverage gaps → owner calls; (4) lens depth partial on every lens. Restart: fix (1)+(2), then one more isolated weekly run.

## Revision 3 — six-routine scope (owner chat 2026-10-17, pasted prompt "Continue the scheduled-routine revamp on White — six routines")

**D4 resolved: six recurring routines** (supersedes the two- and seven-routine statements). Excluded and left untouched: `bimp-clickup-daily-populate`, `pr877-f6-f9-monday-scheduled-acceptance`. D1/D2 still unanswered; D3 deferred with migration. M0 accepted/closed; M1 AC-1..AC-9 and their evidence stand unchanged above. New milestone **M2 = six installation-ready replacements, isolated-validated on White; installation and cutover NOT authorized.**

Phase list (extension; one checkpoint in this file per phase):

| Phase | Goal | Done-criterion | Status |
|---|---|---|---|
| R0 | Resolve IDs, read live prompts, write matrix | matrix below filled from live SKILL.md text | done 2026-10-17 |
| R1 | Audit defects (child JSON, artifact contracts, ctxcheck count, per-run evidence dir, GRRP lifecycle) | focused regression tests pass; full suite green | done 2026-10-17: 76 passed / 56 subtests (was 72) |
| R2 | Remaining audit coverage incl. tested-but-dead | every required check `complete` or a named genuine blocker | done 2026-10-26 (1a4-4): all lenses complete per contract; perf-live/addins-build not-run-approval; run `degraded`, gate `incomplete` |
| R3 | Four routines as portable skills | fixture tests per routine; isolated real run where boundaries allow | done 2026-10-17; tests re-run 2026-10-26: 8/7/19/10 passed (two stale assertions fixed) |
| R4 | Side-by-side + acceptance reconcile | matrix status column evidenced | done 2026-10-26: {reports}\2026-10-17__audit-revamp-1a4-4-closeout.md |
| R5 | Install + rollback plan, handover | handover in {reports} | done 2026-10-17: six-routine-revamp-handover.md §9; install NOT authorized |

### Six-routine acceptance matrix (R0; status column updated per phase)

Dependency survey: {reports}\2026-10-17__six-routine-dependency-map.md. All Mondays, White.

| | weekly-full-audit | slop-audit-weekly | graphify-rerank-usage-check (Adoption Funnel) | next-workspace-sync-weekly | nl-filter-drift-monitor-weekly | context-weight-weekly |
|---|---|---|---|---|---|---|
| Schedule | 01:06 | 05:07 (after next-sync) | 04:08 | 04:13 | 04:34 | 09:11 |
| Purpose / coverage | Lens audit of BIMpossible + Workspace (+AddIns) over the change window; GRRP-enrolled | Slop sweep of 4 roots; reused by weekly when window covers | 4-row graphify funnel (resolution, hints, routing, execution, measurement) over 7 days | Reconcile /next queue: ranks, claims, watermarks, ledger staleness flags | Live NL-filter eval drift vs pinned baseline | Context-load weight of rules/skills/memory vs budget |
| Inputs (local-only state) | target repos, prior reports, checklist, ~/.claude scheduled-tasks | 4 roots, prior sweep reports | ~/.claude/projects transcripts, query log, graphify.local.json | {tools}\state queue.yaml (holds watermarks), projects.yaml, gh, WAVE/PHASE-STATUS | docker-backend-1 container, OpenAI-backed live eval, committed baseline | ~/.claude CLAUDE.md, skills, MEMORY.md, history.jsonl |
| Deps / ordering | none upstream | after next-sync | none | before slop | docker healthy; origin/HEAD | none |
| Outputs / permitted writes | report (-N), ledger, batch, state record, heartbeat | reports, ledgers, state, heartbeat | report .md + .json in reports | queue.yaml, QUEUE.md (uncommitted); no publish | worktree branch: ledger row, evidence JSON, alert file; commit + PR | history.jsonl append; report |
| Completion | run ok/degraded with gate recorded | same | report with every row verdict + bottom line | queue_store OK, render --check OK, watermarks only for clean reads | exit 0/1 with ledger row; PR opened+reviewed | exit 0 CLEAN/ADVISORY |
| Failure | finalize failed; partial never complete | same | script fail → "audit tooling FAIL"; INSUFFICIENT EVIDENCE is not pass | any read failure named; that watermark not advanced | exit 2 = red not-run; publish fail = STOP | exit 2 hard fail / unmeasured required source |
| ACs | M1 AC-1..9 (above) | M1 AC-1..9 | GF-1 contract rows/thresholds preserved; GF-2 writes only --out/--json; GF-3 tooling-fail path; GF-4 portable paths | NS-1 reconcile steps 1-5 only, no publish; NS-2 state-dir parameterized; NS-3 watermark only on clean read; NS-4 staleness flags read-only | NL-1 health precheck, never starts docker; NL-2 all writes follow -LedgerDir; NL-3 red re-run once, alert never silenced; NL-4 no --write-baseline; NL-5 publish only via Push-And-Verify, stop on fail | CW-1 verdict mapping from exit; CW-2 history redirectable; CW-3 unmeasured required source called out; CW-4 never applies fixes |
| Implementation status | lane; 1a4-4 degraded; 83 tests + 71 subtests | lane; sweep db2755 complete, 4 roots | lane; 8 passed | lane; 7 passed | lane; 19 passed | lane; 10 passed |
| Approval-dependent ops | install prompt; D1 autofix; D2 delivery | install prompt | install prompt | install prompt; step-6 publish stays manual | install prompt; live paid eval; push/PR | install prompt; live history append |

**Checkpoint 2026-10-26 (1a4-4 closeout): M2 isolated validation met; cutover not authorized.** Weekly run d54e42 `degraded`; gate-2 `incomplete` (AC-1 unmet, 592 unassessed hypotheses); batch "NOT COMPLETE / verdict: incomplete · items 1/3 cleared · ledgers W incomplete". Verified Highs: CQ-R7-4, SLOP-P1-1. Run defects disclosed: 107 undeclared-location files (rule not followed), false ws attestation, skipped brief step, mojibake, G4 measured at current HEADs. Report: {reports}\2026-10-17__audit-revamp-1a4-4-closeout.md. Restart: owner decides D1/D2 and approves install per routine (handover §9); approval-dependent checks in closeout §7. Watchdog FINDINGs of 2026-10-26 reported, not acked.


**Decision 2026-10-29 (owner, relayed by session local_33546a97 "Audit D1-4 checks"): D3 closed: no MyBuddy audit account. Phase 2 (MyBuddy headless worker) dropped.** Owner: "We're not putting any audits on my buddy because it's Linux, every other machine is on Windows, so it makes no sense." Supersedes the D3 row and Phase 2 row above, which are left as written. D1 and D2 stay open.

**Decision 2026-10-30 (owner chat, relayed by session local_33546a97; record `{reports}/2026-10-29__audits-mybuddy-and-revamp-closeout.md`): D1 closed = B, drop the Auto-Fix Pass.** Owner: "I am fine to drop D1 auto fix pass". `--autofix` stays off and is not activated.

**Decision 2026-10-30 (same source): D2 closed = file delivery only, no PR** (owner: extra work and cost). `--deliver file`; `gmail-draft` unused. Records trail = RESULTS.md path via `RUN deliver --state delivered --ref`, plus run log and GRRP ledger. Supersedes the D2 row's "branch/PR" wording, which is left as written.

**Checkpoint 2026-10-30 (1a.iv repairs, commit 63d5020, local only):** 1a4-4 tooling defects fixed with tests: `--deliver file` built and activated (D2), `RUN brief-done` required before dispatch, `method-line` load proof checked on return, `RUN outputs-audit` flags undeclared writes, strict UTF-8 line storage, G4 measured at the inspected revision, G4 seven-part text in SKILL.md. Tests 2026-10-30: weekly 90 passed / 3 skipped / 81 subtests; context-weight 10; nl-filter 19; graphify 8 and next-sync 7 skipped (env vars unset, suites unchanged). Open conflict: live prompt G4 part (2) says the checker measures the working tree at HEAD; the skill now measures the inspected revision (owner to reconcile at cutover). Restart: 1a4-5 side-by-side rerun (`--autofix` off, `--deliver file`) in a fresh session, against the live 2026-10-26 outputs.

**Checkpoint 2026-10-30 (1a4-5 isolated rerun, White): repairs verified; cutover not authorized.** Weekly run 7c2747 `degraded`; gate `incomplete` (AC-1 unmet); batch "NOT COMPLETE / verdict: incomplete · items 1/3 cleared · ledgers W incomplete"; RESULTS.md delivered (D2). Findings C0 H5 M35 L65 I30 (135); ctxcheck 73/4/0; HYG-13 all ✓; G4 TIGHT at inspected revisions. Sweep 1c1220 `degraded`, outputs-audit clean. 1a4-4 defects: brief, attestation, early hand-back, mojibake and G4 drift did not recur; undeclared-outputs flag fired (1,730) but every hit traces to concurrent sessions' worktrees or hook/cache dirs, none to this run. Open: G4 live-prompt wording conflict; outputs-audit scope; sweep run log. Report: {reports}\2026-10-30__audit-revamp-1a4-5-closeout.md. Restart: owner decides the §5 calls in that report, then approves install per routine.

**Checkpoint 2026-10-30 (readiness closeout): PENDING owner — cutover not authorized.** Commit e1e9a5a fixes the open 1a4-5 items:
- G4 is reported at the inspected SHA.
- The Checklist is checked for readability.
- `RUN isolate`/`release-src` give a run-owned detached checkout.
- The sweep writes a run log via `RUN write-run-log`.
- outputs-audit is run-attributed: changes in live roots are informational, and undeclared output in run-owned dirs is a defect.
- Archive exclusion uses the `:(exclude,glob)` pathspec.
- Child-rule checks were added.

Tests 2026-10-30 on e1e9a5a: weekly 113 passed / 3 skipped / 107 subtests; context-weight 10; nl-filter 19. Delivery: PR https://github.com/YourBIMpossible/claude-profile/pull/76 (open). Status: A done · B done (pending merge) · C not started · D not started · AC-1 unmet (perf, Add-Ins build and breach-chain have no home). Report: {reports}\2026-10-30__audit-revamp-phase1a-closeout.md. Restart: owner merges PR 76 and fills {reports}\2026-10-30__audit-revamp-away-authorization.md, then pastes it as the prompt.
