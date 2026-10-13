---
name: next
description: "Use when the user asks where a project stands, what to work on next, what is blocking production, what is left to finish, or types \"/next\". Cross-repo portfolio board plus a leverage-ranked, roadmap-anchored queue. Modes: board, <target> drill, roadmap, sync, init, all deep. Read-only on repos - never fixes, commits, or pushes."
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
| Writes `queue.yaml` (its own store) | **Never writes `BIMpossible_PHASE-STATUS.md` or `BIMpossible_WAVE-STATUS.md` directly** — those are updated by each session's own close-out when it ships wave-worthy work (`F:\BIMpossible\CLAUDE.md` § Session close-out — wave ledger), not by `/next`'s sync. Report staleness so the next close-out (or a backfill pass) catches it up. |
| Verifies claims against machine evidence | Accepting a doc's word as verification |

If the user's actual question is "is this code correct" or "what's broken", say so and route to the
audit skills instead of answering it here.

## Data model

Canonical store is **structured, not prose**:

- `F:\Claude-Tools\state\projects.yaml` — the registry. One entry per project. Hand-edited.
- `F:\Claude-Tools\state\queue.yaml` — the item store. Canonical. Hand-editable and machine-written.
- `F:\Claude-Tools\state\QUEUE.md` — **generated view, do not hand-edit.** Regenerated from `queue.yaml`.

**Items are cross-repo work items, not repo-scoped rows.** An item declares `projects: [a, b]` when
it spans repos (BIMpossible ↔ Add-Ins lockstep work is the common case). Never mirror one item into
two per-project entries — mirrored items drift apart, which is the failure this design exists to prevent.

Display sections ("Blocked on you", "Next up", …) are **derived from `status` + rank at render time**.
They are never stored. Read `reference/item-model.md` for the schema, the closed status vocabulary,
and the ranking algorithm before writing to the store.

## Modes

`bare` · drill · `resume` · `waiting` · `roadmap` · `sync` · `init` · `all deep`. Only `sync`/`init`/`all deep` write.

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
   **Never run a `live_check` for an item scoped to a dormant project** (a `projects.yaml` entry
   with `dormant: true` — an item whose `projects` are all dormant). A dormant repo is
   reassessment-bound: its probes are suspended, non-executable metadata, so an in-scope
   dormant item carries an empty `live_check` and `queue_store.py` hard-fails if one is added.
   Treat any command text retained in such an item's evidence as historical provenance only —
   do not execute it, and render the item **SUSPENDED** (never verified) until dormancy lifts. A
   governance item scoped to a live project but referencing a dormant one via `affects_projects`
   is probed normally; the dormancy ban is on running commands *against* the dormant repo.
   **Mixed-scope items** (`projects` spanning a dormant *and* a live project) keep their live-leg
   probes but must not carry any `live_check` command that targets the dormant checkout (by path,
   directory name, or `owner/repo` slug — `queue_store.py` hard-fails on one, whatever the declared
   legs). Such an item stores `verification.level: partial`, never `verified`, and renders a
   **PARTIAL** badge — the live leg is real, the dormant leg stays unverified. Run only the
   live-leg commands; never the dormant one. See `reference/item-model.md` § Dormant projects.
4. Render the derived sections.
5. Name exactly one **#1 move**, stating the **anchor** (stated focus vs roadmap-default) and which
   **leverage** drives it (unblock vs finalization) as the reason — never a raw `unblocks` list, which
   is meaningless on the flat graph this store actually has.

If any `live_check` contradicts stored `verification`, see Anti-drift below — do not silently pick a side.

If the watermark is older than 7 days, print the staleness banner and **do not name a #1 move without
first offering `sync`**. A confident answer off a stale store is the exact failure mode this replaces.

### `<target> resume` — restart from records (Stage A, read-only)

`python F:\Claude-Tools\state\resume_queue.py --resume <key>`. Prints: watermark vs live HEAD and
the project-leg freshness; items `in_flight` with claim identity and `STALE CLAIM (>24h, not
auto-released)`; the anchor's last *Exact restart action* line **verbatim** with its git date/author
(anchor path = optional `anchor:` in `projects.yaml`); the ranked #1 actionable item with freshness;
the Waiting count. No anchor or no bookend → `no recorded next action (…)` and the #1 is labelled
`fallback from queue, not a recorded next action` — never a rewritten narrative (AC07). Never writes.

### `waiting` / `<target> waiting` — what only the owner can move (Stage A, read-only)

`python F:\Claude-Tools\state\resume_queue.py --waiting [--project <key>]`. Lists the Waiting set
(`reference/item-model.md` § Waiting set) ranked like the board, each with `ask:` (or
`UNSPECIFIED ⚠`), verification level/date, and per-leg freshness; done/status mismatches print
inline. Parked prints as a trailing count only. Missing asks are counted at the end — `sync`
supplies them, this mode never does.

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

## Single home — global skill, one location

`next` is a **global, cross-project** skill with exactly one home: `F:\Claude-Profile\skills\next`
(surfaced at the runtime path `~/.claude/skills/next` through a directory junction — same files, not
a copy). It renders a cross-project portfolio board, so it is **not** repo-specific.

**Never create a project-local mirror of it.** A second copy in any repo's `.claude/skills` is drift
waiting to happen — the former `BIMpossible-Workspace\.claude\skills\next` mirror silently diverged
and was deleted 2026-08-30 for exactly this reason. If a repo needs `/next`, it uses the global skill;
it does not carry its own copy. (Genuinely repo-specific skills — e.g. the workspace's `bim-*` skills
— correctly live only in their repo and are unaffected by this rule.)

**Durable boundary — one home for behavior, snapshots only in repos.** Global skill logic,
instructions, heuristics, references, and templates have exactly one canonical home: this global
profile. A repository may contain *generated queue snapshots* (`queue.yaml`/`QUEUE.md`) for review or
offline reading — but never an executable or authoritative mirror of the skill. The store *data*
mirror in `reconcile.md` step 6 is that permitted case: it copies data plus the deterministic renderer
for reading, not skill definition, and every published copy carries a provenance header marking it a
repo-local snapshot whose canonical producer is this skill (exact header text: `reference/reconcile.md`
step 6). A snapshot that could be read as configuration is drift; the header exists so it cannot.

## Reference

- `reference/item-model.md` — schema, status vocabulary, ranking algorithm, rendering rules
- `reference/reconcile.md` — the sync procedure, evidence rules, watermarks
- `reference/roadmap.md` — the whole-project live view: phase grouping, ledger cross-check, ordering
