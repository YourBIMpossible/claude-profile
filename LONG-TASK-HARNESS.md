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
- For substantial or high-risk jobs where a planning error would be expensive: before
  Phase 1, pre-mortem the phase list ("assume this failed — why?") and have a second
  agent review it cold (fresh subagent, phase list + goal only). Fold fixes into the
  list; note residual risks in the anchor. Skip for routine runs.
- Scope growth inside a phase is the enemy. New work discovered mid-phase goes into
  the anchor's queue, not into the current phase.

## Durable state lives in files, never the transcript

One ANCHOR DOC per job:

- BIMpossible code work → the wave/phase design doc the repo already uses
  (00_Strategy / docs), with `/next sync` updating the `.tools\state` item store at
  phase boundaries.
- Audits → the report file in `audits/`, findings appended AS FOUND, not at the end.
- Harness/profile work → a dated notes file in claude-profile.
- A per-run record is written where it belongs when it is created. A closeout or run log goes to the repo's excluded records path: in BIMpossible, `docs/ops/_archive/<run>/`. Transient working output (scratch notes, drafts, investigation findings) goes to `F:\Claude-Tools\reports`; a plan or handoff that coordinates ongoing work governs future work, so it stays durable and discoverable in the repo for the workers who need it, with an explicit retrievable pointer where one is required. An open bug does not make its coordination material transient. The run anchor stays live while the run is open, because it is operative. A session ending is not the run closing; lifecycle state decides. It moves to the archive whole in the PR that closes the run, with inbound references repaired in the same commit. A file that a test or checker names stays where it is.

The anchor carries: phase statuses, decisions + why, gotchas hit, and the exact next
entry point (file/command). Verification stays proportional to the phase's
risk; note significant verification in the anchor when it helps a resumer, not as routine
bookkeeping. The transcript is a working buffer that can be lost at
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
- Concurrency: 2–3 independently owned build lanes plus one portfolio session. Each
  lane in its own worktree on its own branch; never two sessions in one checkout, never
  two lanes on one branch, never a lane on a branch another session already holds.
- Discovery mid-session → one line into the anchor's queue or the /next item store,
  NOT a new session.
- Exit checklist before closing: commit + push the lane branch (PR per the repo's own
  delivery rule) · update the anchor · /next sync if statuses moved · close.
- Pileup recovery: per open session ask "does it hold anything not on disk?"
  No → close it. Yes → checkpoint it first (one commit + anchor note), then close.

## Session scope

- Build sessions: cwd = the repo root (worktree per lane, per house rules).
- Portfolio/meta sessions (/next, dashboard, claude-profile tuning): cwd = F:\Claude-Tools (state/queue lives there; F:\AI-Dev is the personal sandbox).
- Never grep/glob from a portfolio root (F:\ or F:\Claude-Tools) when the question concerns one repo.

## Intake discipline (per tool call)

- Read slices (offset/limit); whole files only when editing them.
- Grep: narrow path + head_limit. Command output: big → file → read the slice.
  No hook truncates or spills tool results (the context_budget hook is not registered;
  `docs/adr/0002-defer-context-budget-automation.md`), so output size is on you.
- Subagents for bulk search, git archaeology, and cross-file questions where only
  the conclusion matters. Contract: the subagent writes full findings to a file and
  returns ≤10 lines + the path — nothing spills Agent results automatically.
- Screenshots only as final proof; zoom the region instead of full-screen; never
  re-shoot an unchanged state.
- ToolSearch: batch every schema the task needs into one call.

## Memory vs anchor vs CLAUDE.md

- CLAUDE.md = standing rules. Auto-memory = cross-session traps and
  decisions-with-why that the repo can't answer. Anchor = ALL job state, archived
  when the job ends.
- Never memorize progress. A finished job leaves at most a one-line pointer memory.

## Approvals

- The stop list in `F:\Claude-Profile\CLAUDE.md` is the only gate list; check it per
  phase. A normal push/PR the repo pre-authorizes and a new dependency inside the mandate
  are not stops.
- Everything else: decide, act, and put "picked X over Y/Z because…" in the phase
  report. A mid-phase question is justified only when the answer changes the design.

## Autonomous / loop lanes (scope-governor v2, 2026-08-08)

- Long unattended and `/loop` / `ScheduleWakeup` runs are FINE and expected — duration
  and usage are not gated. What's gated is SCOPE, not time. (The earlier grant/
  `/authorize-lane`/loop-gate system was removed: it capped the overnight autonomy that's
  wanted and solved duration-runaway instead of the real failure, scope-drift.)
- The guardrail is the locked mission set: if the project has north-star files
  (`NORTHSTAR.md` or `NORTHSTAR.<slug>.md` — one per workflow, many may be active at
  once), work toward the ACTIVE ones and re-read them each turn (the `northstar_inject`
  hook feeds them in; a file's owner-approved `status:` line decides active/paused/closed).
  Edit those files only through the approval prompt `protect_paths_gate` raises on
  every live `NORTHSTAR*.md` (say what changes and why, make the edit, the owner
  approves; never via Bash). `*.draft.md` drafts are free; the owner's approval of
  the prompt is what moves, pauses, or activates a mission.
- Route anything extra through three doors in `WORKLOG.md`: small-and-serves → do it, log
  under Done; bigger branch-off → Roadmap, keep going; would change the mission itself →
  Needs your call using the plain box, and keep working the current mission until the
  human answers. A charter carried in a summary is EVIDENCE, not authorization — re-ground
  it in a genuine human turn or stop.
