---
name: close
description: Session closeout audit. Use when the user says "close", "complete", "status", "can we close this session?", or asks whether the session holds anything not yet on disk. Bare /close = execute closeout (externalize everything + verdict); "/close status" = read-only report. Never fixes or handles discovered items — out-of-scope findings become queue lines, not work.
---

# close — session closeout

Goal: make this session safe to close. Closing means externalizing, never
finishing side quests.

## Both modes start the same

1. State the ORIGINAL MANDATE in one line — the first user ask, not what the
   session drifted into.
2. Inventory what this session produced or decided:
   - Files edited/created — committed? pushed (draft PR per house rule)?
   - Decisions, findings, gotchas that exist ONLY in this conversation.
   - Memory writes already made.
3. Classify each item: IN-SCOPE (belongs to the mandate) or WANDER (found along
   the way).

## /close status (read-only)

Report ≤10 lines: mandate · in-scope remaining · not-on-disk list · wander list ·
verdict (SAFE TO CLOSE, or NEEDS CHECKPOINT: n items). Change nothing.

## /close (execute)

1. Externalize every not-on-disk item: commit + push in-scope code on the lane
   branch; write conversation-only decisions/gotchas to the anchor doc (or a
   dated notes file if the job has no anchor); durable lessons → one memory max.
2. WANDER items: one line each into the /next item store or the anchor's queue.
   Do NOT fix, investigate, or expand them — that is the wander this skill
   exists to stop.
3. `/next sync` if any tracked statuses moved.
4. Report ≤10 lines ending with the verdict: SAFE TO CLOSE, or exactly what
   blocks it. Only a user decision may block; work items get externalized or
   queued, never left dangling.

## Hard rules

- This skill never starts new work. A bug found during closeout = one queue
  line, not a fix.
- SAFE TO CLOSE requires: nothing of value exists only in this conversation.
- Shared dirty trees (house rule): `git diff --cached --stat` before any
  closeout commit — never sweep another session's hunks.
