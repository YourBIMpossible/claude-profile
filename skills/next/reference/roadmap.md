# Roadmap procedure (`<target> roadmap`)

Answers a broader question than drill. Drill names the single next move; `roadmap` shows *everything*,
grouped by where it sits in the product, so you can pick across the whole arc rather than accept one
ranked answer. (Historically drill also buried leaf product items under cone-rank — a deliverable that
unblocks nothing had `cone == 0` and could never rank #1. The item-model.md ranking rewrite of
2026-08-30 fixed that at the source: ranking is anchor-first and, on a flat graph, leverages
finalization over cone. Roadmap's whole-arc view remains the right tool when you want breadth, not a
single pick.)

Never cache this output to a file. A file is a snapshot; the whole point of this mode is that there
is no snapshot to go stale — it re-derives from the store and the live ledger on every call.

## 1. Load

All active items where `projects` contains the target (or every active item, for `all`).

## 2. Group by phase

Parse a phase token from `title` first, then `source` if title has none:

- `Phase\s*(\d+(\.\d+)?[a-z]?)` — e.g. "Phase 7", "Phase 3.10b", "Phase 9 reopened scope"
- id prefix fallback: `P(\d+)(-|$)` on the item `id` (e.g. `P7-SYNC-GOLIVE`, `P3-8-SLICE23`)

No match → bucket `Cross-cutting / ops` (most `OPS-*`, `SEC-*`, `DEP-*`, `ARCH-*` items land here
legitimately — not every item is phase-scoped, and forcing one would be a fabricated fact).

## 3. Read the ledger live

Read the project's `ledgers:` entries from `projects.yaml` in full — do not rely on what earlier
items already cited in `source`, that is exactly the stale-cache failure this mode exists to avoid.
For each phase row in the ledger, check whether any grouped item's phase token matches.

- Matched → the item(s) render under that phase, as normal.
- No match, and the ledger row is not phrased as fully done → render one `UNTRACKED` line carrying
  the ledger's own wording verbatim (never invent an id, status, or evidence for it). This is a sync
  candidate, not a ranked item — it has no place in the cone graph until someone gives it one.

Do not edit the ledger. Do not create a queue item for it here — that is `sync`'s job, and doing it
inline would mean writing unverified items outside the reconcile procedure's evidence discipline.

## 4. Order

Within each phase, order by status tier, not cone (cone is meaningless for a whole-project view):

1. `blocked_owner` with `effort: S` — stranded, near-zero cost to close
2. `blocked_owner` (other effort)
3. `ready`
4. `landed` — flag "verify it"
5. `in_flight`
6. `blocked_dep`, `blocked_external`
7. `UNTRACKED` ledger rows
8. `parked`
9. `dropped` — omit unless the user asked for history

Phases themselves render in ascending numeric order; `Cross-cutting / ops` last.

## 5. Render

1. Lead with the #1 move from the ranking algorithm (item-model.md Step 0–2). Under roadmap-default
   that is the earliest-incomplete-phase move, not a cone-rank sink — roadmap adds breadth around it,
   it does not resurrect the old cone-first answer.
2. Then every phase, in order, one line per item (same line format as drill — see item-model.md
   "Rendering"). Phase heading carries a one-line ledger status/percentage if the ledger states one.
3. `UNTRACKED` lines render as `[UNTRACKED] <ledger wording>` with no id, no rank.
4. Staleness banner still applies at >7 days on the watermark, but unlike drill it does not block the
   render — seeing the whole project slightly stale is still useful; only the single #1-move claim
   needs the fresh gate (drill already enforces that).
5. Close by naming exactly what would turn each `UNTRACKED` line into a tracked item: run
   `<target> sync`.

## Cost

More than `bare`/`drill` (reads the full ledger, not just the store + cheap probes). Far less than
`sync` (no `git log`, no `gh pr list`, no store write, no watermark advance — purely a read + render).
