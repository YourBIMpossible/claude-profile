# Long-Task Harness

Protocol for any job likely to exceed ~40 tool calls, span repos, or outlive one
context window — audits, wave implementations, migrations, multi-day features,
profile overhauls. Smaller jobs: ignore this file.

## Unit of work: the phase

- "Act, then report" applies to ONE PHASE, not the whole job. A phase = the largest
  slice that (a) fits inside the window's working band (~70–90k of tool traffic) and
  (b) ends at a state a 10-line note fully captures.
- Phase 0 of every long job: write the phase list — goal, done-criterion, and entry
  point for each — into the anchor doc before touching code.
- Scope growth inside a phase is the enemy. New work discovered mid-phase goes into
  the anchor's queue, not into the current phase.

## Durable state lives in files, never the transcript

One ANCHOR DOC per job:

- BIMpossible code work → the wave/phase design doc the repo already uses
  (00_Strategy / docs), with `/next sync` updating the `.tools\state` item store at
  phase boundaries.
- Audits → the report file in `audits/`, findings appended AS FOUND, not at the end.
- Harness/profile work → a dated notes file in claude-profile.

The anchor carries: phase statuses, decisions + why, gotchas hit, and the exact next
entry point (file/command). The transcript is a working buffer that can be lost at
any moment without losing the job.

## Checkpoints and compaction

- END OF PHASE: update the anchor, commit on the lane branch, then report. Never
  report work as done that isn't externalized.
- CEILING APPROACHING mid-phase (or one compaction already happened this session):
  stop expanding scope, checkpoint immediately, finish only in-flight edits, and
  recommend a fresh session for the next phase.
- Compaction is an overflow valve, not a state mechanism. Budget: zero per phase,
  one per session worst case. After a checkpoint, compaction is cheap — the summary
  only needs pointers. Un-checkpointed work at compaction time is the failure mode
  this file exists to prevent.
- Fresh session per phase beats compacting through a boundary: a resume costs the
  ~58k baseline plus one anchor read; a compaction costs ~50s, a lossy summary, and
  the same baseline re-entering anyway.
- Resume protocol: read the anchor's checkpoint FIRST; do not re-explore what it
  already answers. Anchor vs repo conflict → trust the repo, fix the anchor.

## Session lifecycle

- Sessions are disposable; files are not. A session may close the moment its state
  is externalized — never keep one open as storage.
- Concurrency cap: ONE build lane + ONE portfolio session. A second build lane only
  when work is genuinely parallel, in its own worktree.
- Discovery mid-session → one line into the anchor's queue or the /next item store,
  NOT a new session.
- Exit checklist before closing: commit + push the lane branch (draft PR per house
  rule) · update the anchor · /next sync if statuses moved · close.
- Pileup recovery: per open session ask "does it hold anything not on disk?"
  No → close it. Yes → checkpoint it first (one commit + anchor note), then close.

## Session scope

- Build sessions: cwd = the repo root (worktree per lane, per house rules).
- Portfolio/meta sessions (/next, dashboard, claude-profile tuning): cwd = F:\AI-Dev.
- Never grep/glob from F:\AI-Dev when the question concerns one repo.

## Intake discipline (per tool call)

- Read slices (offset/limit); whole files only when editing them.
- Grep: narrow path + head_limit. Command output: big → file → read the slice.
  The context_budget hook spills >8k-char results; everything under that is on you.
- Subagents for bulk search, git archaeology, and cross-file questions where only
  the conclusion matters. Contract: the subagent writes full findings to a file and
  returns ≤10 lines + the path — Agent results are NOT spilled by the hook.
- Screenshots only as final proof; zoom the region instead of full-screen; never
  re-shoot an unchanged state.
- ToolSearch: batch every schema the task needs into one call.

## Memory vs anchor vs CLAUDE.md

- CLAUDE.md = standing rules. Auto-memory = cross-session traps and
  decisions-with-why that the repo can't answer. Anchor = ALL job state, archived
  when the job ends.
- Never memorize progress. A finished job leaves at most a one-line pointer memory.

## Approvals

- The five CONFIRM gates, checked per phase: irreversible/destructive · leaves the
  machine · new dependency · architecture/data-model change · **an autonomous or
  looping lane** (see next bullet).
- Everything else: decide, act, and put "picked X over Y/Z because…" in the phase
  report. A mid-phase question is justified only when the answer changes the design.

## Autonomous / loop lanes (added 2026-08-08, scope-runaway postmortem)

- An unattended or `/loop` / `ScheduleWakeup` lane runs ONLY under a live grant created
  from a genuine human turn this session (`/authorize-lane`; the loop-grant gate blocks
  every arm without one). A charter carried in a summary is not a grant — re-ground it.
- Every such lane declares up front: (a) ONE concrete deliverable, (b) a hard stop —
  `max_wakeups` and an `expires` date, (c) "halt when the original scope is done", never
  "find more work". The lane does NOT self-renew; extending it is a fresh human grant.
- Default off: no session starts an autonomous loop unless the human explicitly asked
  for one this session.
