---
name: next
description: Use when the user asks where a project stands, what to work on next, what is blocking production, what is left to finish, wants to see the whole project/roadmap so they can work through it, or types "/next". Renders a cross-repo portfolio board and a ranked, evidence-verified production queue from a durable structured item store at F:\AI-Dev\.tools\state. Ranks by critical path (how much each item unblocks). Modes — /next = board; /next <target> = ranked drill (single #1 move); /next <target> roadmap = live whole-project view, phase by phase, cross-checked against the owner ledger every run (never a static snapshot); /next <target> sync = reconcile the store (run when stale or after a merge wave); /next <target> init = bootstrap a new project; /next all deep = full re-derive. Read-only against project repos — it never fixes, commits, pushes, or hunts defects.
---

# /next — production status and next-move

Answers two questions without being argued with:

1. **Where is this at?** — a portfolio board across registered projects.
2. **What should I do now?** — one ranked, evidence-backed next move.

The expensive part of "where are we" is not gathering facts, it is **judging and ranking** them.
This skill persists the judgment in a structured store and re-verifies the deterministic facts
live on every read. That is the whole design.

## Boundaries — read before anything else

| This skill | Not this skill |
|---|---|
| Reports status, ranks work, names the next move | Fixing, committing, pushing, tagging |
| Reads repos, ledgers, PRs, flags | Hunting defects → route to `/audit`, `/revit-functionality-audit` |
| Writes `queue.yaml` (its own store) | **Never edits `BIMpossible_PHASE-STATUS.md` or `BIMpossible_WAVE-STATUS.md`** — owner-maintained. Report staleness and draft the row for the owner to paste. |
| Verifies claims against machine evidence | Accepting a doc's word as verification |

If the user's actual question is "is this code correct" or "what's broken", say so and route to the
audit skills instead of answering it here.

## Data model

Canonical store is **structured, not prose**:

- `F:\AI-Dev\.tools\state\projects.yaml` — the registry. One entry per project. Hand-edited.
- `F:\AI-Dev\.tools\state\queue.yaml` — the item store. Canonical. Hand-editable and machine-written.
- `F:\AI-Dev\.tools\state\QUEUE.md` — **generated view, do not hand-edit.** Regenerated from `queue.yaml`.

**Items are cross-repo work items, not repo-scoped rows.** An item declares `projects: [a, b]` when
it spans repos (BIMpossible ↔ Add-Ins lockstep work is the common case). Never mirror one item into
two per-project entries — mirrored items drift apart, which is the failure this design exists to prevent.

Display sections ("Blocked on you", "Next up", …) are **derived from `status` + rank at render time**.
They are never stored. Read `reference/item-model.md` for the schema, the closed status vocabulary,
and the ranking algorithm before writing to the store.

## Modes

Dispatch on the argument. Unrecognized argument → try to resolve it as a project name or alias from
the registry; if it resolves to nothing, show the board and say the name was not recognized.

### bare — `/next`

Portfolio board. One line per registered project. Cheap: deterministic probes plus the item store, no re-derivation.

For each `active: true` project in the registry:
- Live probe: current branch, ahead/behind origin, working tree clean/dirty, open PR count.
- From the store: count of items by status, the single highest-ranked actionable item, watermark age.
- Staleness: `reconciled.<project>.at` older than 7 days → mark the line `STALE`.

Then print, below the board, the **top 3 actionable items workspace-wide** — because the ranking unit
is the cross-repo work item, this list is not per-project and may surface an item that spans two repos.

End by naming what to type to drill in. Never guess a target on a bare run.

### `<target>` — drill

Full ranked queue for one project (or `all`).

1. Load items where `projects` contains the target.
2. Rank them (see `reference/item-model.md`).
3. **Run the `live_check` of the top 5 ranked items and any item with `status: landed`.** Landed items
   are where finished value strands, so they always get re-checked.
4. Render the derived sections.
5. Name exactly one **#1 move** with its `unblocks` list as the stated reason.

If any `live_check` contradicts stored `verification`, see Anti-drift below — do not silently pick a side.

If the watermark is older than 7 days, print the staleness banner and **do not name a #1 move without
first offering `sync`**. A confident answer off a stale store is the exact failure mode this replaces.

### `<target> roadmap` — whole project, live

The answer to "show me everything, phase by phase, so I can pick." Never write this to a file —
a saved snapshot is exactly the staleness this replaces. Render it fresh, in chat, every time.

Follow `reference/roadmap.md`. Summary: load every active item in scope, group by the phase token
parsed from its `title`/`source`, read the project's phase ledger **live** (not from the item store)
and cross-check each phase row against the grouped items, then render every phase in order with the
existing #1 critical-path move still leading the output. Ledger rows with no matching item print as
`UNTRACKED` — named, not silently dropped — because that is the gap `sync` should close next, and
surfacing it live is what keeps this mode from ever needing a companion document.

Read-only: no store writes, no watermark advance. Costs more than `bare`/drill (it reads the ledger
in full) but far less than `sync` (no git/gh probing, no re-ranking write-back).

### `<target> sync` — reconcile

The expensive path. Run when the store is stale, after a merge wave, or when drill reports a
contradiction. Cadence (2026-08-18 delivery rebuild): sync — like dashboard refreshes, anchor
updates, queue-mirror publishes, and workspace backups — is a **weekly or on-demand** action,
never a per-merge or session-exit obligation.

Follow `reference/reconcile.md`. Summary: read everything that changed since the project's watermark
(commits, PRs, ledger rows, audit reports, build logs), then confirm / demote / add / re-rank items,
advance the watermark, rewrite `queue.yaml`, regenerate `QUEUE.md`, and report the diff as a summary
of what changed and why.

v1 writes the store directly. Every change is diffable and revertible through version history.
The interactive propose→confirm gate is deliberately deferred to v2.

### `<target> init` — first derivation

Bootstrap a project that has no items yet. Same as `sync` but with no watermark — derive the full
picture from scratch and write the initial item set. Say plainly in the output that this is a first
derivation and every item starts at `verification.level: claimed` unless machine evidence was found.

### `all deep` — re-derive from scratch

Ignores `queue.yaml` entirely. Dispatch one agent per active project, re-derive independently, then
reconcile the results against the existing store and report every divergence. Expensive and opt-in —
never run automatically, never as a fallback when a cheaper mode is inconvenient.

## Anti-drift contract

A short durable store is only safe if it cannot quietly become a second stale ledger. Three rules,
none of them optional:

1. **Never report from the store alone.** The deterministic half — branch, merge state, PR state,
   feature-flag value, file existence — is re-checked live on every read that touches it. The store
   holds judgment; the repo holds facts.

2. **Every item is `claimed`, `verified`, or `contradicted`.** `verified` requires named machine
   evidence: a commit SHA, a merged PR, a live-smoke log path. "The ledger says so" is `claimed`, not
   `verified`. Render the level next to every item — never present a claim as a verified fact.

3. **Contradictions are surfaced, never resolved.** When `live_check` disagrees with stored
   `verification`, set `verification.level: contradicted`, print **both readings side by side**, and
   stop. Do not prefer the store, do not prefer the probe, do not quietly rewrite. Ask.

Corollary: if a probe cannot run — no network, `gh` unauthenticated, repo locked — report the item as
**UNVERIFIED (probe failed: reason)**. Absence of evidence is never evidence.

## Output style

Terse. The source artifacts this replaces failed by being exhaustive, so length is a defect here.

- One line per item in rendered views. Detail lives behind a link.
- Always show `verification.level` and item age.
- Lead with the answer — the #1 move — not with the methodology.
- Never emit a `Prior:` chain, a nested history clause, or a paragraph inside a table cell.
- When something is unknown, say "unknown" and name the probe that would settle it.

## Canonical copy and project mirrors

This copy (`F:\Claude-Profile\skills\next`) is **canonical** for shared `next` mechanics — item
model, ranking, reconcile procedure, roadmap mode. `BIMpossible_Workspace\.claude\skills\next` is
a **project-local mirror**: same behavior, scoped description, no `all deep` (single-project
workspace has no cross-project board to re-derive). Distinct from the data mirror in
`reconcile.md` step 6 (`queue.yaml`/`QUEUE.md` copied into the workspace repo) — that publishes
store *data*; this section is about keeping the *skill files* in sync.

Rule: when shared `next` behavior changes (item schema, ranking, reconcile steps, claiming), edit
this canonical copy and mirror the same change into the workspace copy **in the same work item** —
never as a separate follow-up pass. Project-specific wording (scoped description, dropped modes)
stays local to the mirror.

To check for drift, diff the shared sections directly — no tooling beyond what already exists:

```bash
diff "F:/Claude-Profile/skills/next/reference/item-model.md" \
     "F:/AI-Dev/BIMpossible_Workspace/.claude/skills/next/reference/item-model.md"
diff "F:/Claude-Profile/skills/next/reference/reconcile.md" \
     "F:/AI-Dev/BIMpossible_Workspace/.claude/skills/next/reference/reconcile.md"
```

Non-empty output beyond the known project-local deltas (scope line in `SKILL.md`, absent
`all deep`) means the mirror has drifted — reconcile before trusting either copy.

## Reference

- `reference/item-model.md` — schema, status vocabulary, ranking algorithm, rendering rules
- `reference/reconcile.md` — the sync procedure, evidence rules, watermarks
- `reference/roadmap.md` — the whole-project live view: phase grouping, ledger cross-check, ordering
