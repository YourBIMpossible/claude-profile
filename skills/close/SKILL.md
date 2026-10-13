---
name: close
description: "Session closeout audit. Use when the user says \"close\", \"complete\", \"status\", or asks whether the session holds anything not yet on disk. Bare /close = execute closeout; \"/close status\" = read-only report. Never fixes discovered items - out-of-scope findings become queue lines."
---

# close — session closeout

Goal: make this session safe to close. Closing means externalizing, never
finishing side quests.

## Both modes start the same

1. State the ORIGINAL MANDATE in one line — the first user ask, not what the
   session drifted into.
2. Inventory what this session produced or decided:
   - Files edited/created — committed? pushed?
   - Decisions, findings, gotchas that exist ONLY in this conversation.
   - Memory writes already made.
3. Classify each item: IN-SCOPE (belongs to the mandate) or WANDER (found along
   the way).

4. GRRP: if this session created or resumed a ledger (`F:\Claude-Tools\reports\grrp\*.ledger.md`
   or a repo anchor with `grrp: v1`), run Review to Resolution's gate:
   `python ~/.claude/skills/review-to-resolution/scripts/grrp.py gate --json <ledger>`.
   Read `verdict` (complete / incomplete / blocked / invalid), `transfers_ok`, and
   `closeable` from the JSON — never infer from the exit code alone (it carries the
   verdict only). Report `verdict` and transfers verbatim; never upgrade them in prose.
   `transfers_ok: false` = externalized work not yet accepted → NEEDS CHECKPOINT, not SAFE TO CLOSE.

The end-of-session bookend in `CLAUDE.md` (*Completed · Still open · Exact restart
action · New ideas captured*) is what `/close` writes into the anchor. When the
owner repeated an instruction this session, add one line: the missed obligation
and its cause; propose a durable fix only if the cause is not one-off.

Stage A conventions (BIMpossible Platform pilot: `bimpossible`, `addins`):
- *Completed* line per item = `queue_store.format_done(item)` output
  (`handoff:complete · acceptance:open · …`), written verbatim so `/next resume` can
  `parse_done` it back. Never upgrade a dimension to `complete` without evidence on
  disk; `unknown` is the honest default (no fabricated completion evidence).
- *Exact restart action* must be a single line starting `Exact restart action:` —
  `/next resume` reads the **last** such line verbatim. Absent → resume falls back to
  the ranked queue item and says so.
- Before writing any store item: `python F:\Claude-Tools\state\queue_store.py
  --claim-of <ID> --me <worktree toplevel> --me <branch>`; exit 3 = another session's claim → skip,
  report `OWNED BY`.
- Append one row to `F:\Claude-Tools\reports\stage-a-owner-turn-ledger.md` (manual;
  this session's id is the dedup key; turn refs; `uncertain: yes` when a count is a
  judgment). Never classify turns automatically.

## /close status (read-only)

Report ≤10 lines: mandate · in-scope remaining · not-on-disk list · wander list ·
verdict (SAFE TO CLOSE, or NEEDS CHECKPOINT: n items). Change nothing.

## /close (execute)

1. Externalize every not-on-disk item: commit + push in-scope code on the lane
   branch (verify locally first per the repo's delivery contract, e.g.
   `docs/engineering/DELIVERY.md` in BIMpossible; opening a PR or merging stays
   a separate, explicit ask). Write conversation-only decisions/gotchas to the
   anchor doc (or a dated notes file if the job has no anchor); durable
   lessons → one memory max.
   The anchor/notes write includes a **fenced-decisions table** — settled calls
   (merged, closed, deliberately gated, rejected alternatives) with a one-line
   rationale each, so a successor session neither re-litigates nor re-does them.
   Settled means fenced, not re-narrated.
   A design/architecture decision made this session — not just a task outcome —
   externalizes as a decision record against
   `BIMpossible_Workspace/02_Reference/DECISION-RECORD-TEMPLATE.md`, not just an
   anchor-doc line, so it survives with the front matter a future assistant
   needs (trigger, alternatives, consequences) rather than only a settled-table
   one-liner. Use the template's ADR-candidate test (hard to reverse, surprising
   without context, real trade-off) as the backstop check here — this is a
   retroactive catch-all for anything `bim-office-hours`/`bim-investigate` didn't
   already capture live, not a replacement for their live gate.
   For every local-only markdown file this touches or leaves behind, classify
   it explicitly: **safe intentional draft** (named `*.draft.md`/`*.wip.md`, or
   otherwise clearly marked WIP — report it, take no further action) vs.
   **at-risk untracked knowledge** (anything else durable sitting uncommitted —
   this must be externalized per the paragraph above, not left for the closure
   hook to merely flag). The automatic `session_closure_audit.py` hook detects
   and warns at session boundaries; this skill is where detection turns into
   an actual durable artifact.
   Write completed closeout records this session produced straight into the
   records path the current repo defines (BIMpossible: `docs/ops/_archive/<run>/`).
   No records path defined → leave files in place and flag it; never invent one.
   A session ending is not the run closing: move the run anchor there whole, with
   inbound references repaired in the same commit, only if this session closes the
   run; otherwise it stays live. A pending decision packet stays live until its
   decisions are executed or closed; an accepted decision record is never
   auto-archived. Transition only artifacts this run owns. A file a test or checker
   names stays at its required path — flag the conflict instead of moving it.
2. If any decision records were newly written or updated this session and
   `BIMpossible_Workspace/wiki/` has a page whose area overlaps, offer once:
   "N decision record(s) changed — run `/wiki ingest` now?" Accept or decline
   is the user's call each time; never run it unprompted, and never treat a
   prior session's acceptance as standing consent.
3. WANDER items: one line each into the /next item store or the anchor's queue.
   Do NOT fix, investigate, or expand them — that is the wander this skill
   exists to stop. Stage A pilot items get `done:` (six dimensions) and, when
   Waiting-eligible, `owner_ask:` at the same time.
4. `/next sync` if any tracked statuses moved.
5. Report ≤10 lines ending with the verdict: SAFE TO CLOSE, or exactly what
   blocks it. Only a user decision may block; work items get externalized or
   queued, never left dangling.

## Hard rules

- This skill never starts new work. A bug found during closeout = one queue
  line, not a fix.
- SAFE TO CLOSE requires: nothing of value exists only in this conversation.
- Shared dirty trees (house rule): `git diff --cached --stat` before any
  closeout commit — never sweep another session's hunks.
- Durable-lesson writes amend before they append: when a session proved an
  existing rule/skill/memory failed to bind, fix the line that failed rather
  than adding a new clause beside it. Rules-bloat is the failure mode.
