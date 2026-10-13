# Item model, ranking, and rendering

Canonical store, two tiers (both in `F:\Claude-Tools\state\`):

- `queue.yaml` — ACTIVE operational queue + reconcile watermarks. Authoritative for open work.
- `queue-archive.yaml` — closed-item provenance (`live`/`dropped` records moved out of the active
  queue). Authoritative for historical PR/commit identity; **reconciliation reads it for dedup but
  never rewrites it.** Loader/validator: `queue_store.py` (same dir) — run it after any hand edit.

Design rule that governs everything below: **identity and data are separate from display.** An item
never stores which section it appears under. Sections are computed from `status` and rank at render
time. This keeps `sync` deterministic and stops the store from decaying into prose that has to be
re-parsed.

## Item schema

```yaml
- id: P7-1                      # stable, unique, never reused. Prefix by area (P7-, P13-, OPS-, DOC-).
  title: Supervised first Revit-Link sync — flag ON, test model, log §11
                                # <= 15 words, imperative, no history, no nested clauses
  projects: [bimpossible, addins]
                                # 1..n registry keys. Cross-repo items live here ONCE.
  status: blocked_owner         # closed vocabulary, see below
  owner_gate: true              # true = only the human can advance it
  owner: null                   # claiming session's identifier, or null if unclaimed — see Claiming below
  owner_ask: "approve #819"     # Stage A: the one thing the owner must do. Required on every
                                # Waiting-eligible item; missing -> soft defect + `ask: UNSPECIFIED ⚠`.
                                # Precedence: owner_ask, else owner_steps[0], else unspecified.
  done:                         # Stage A: six-dimension completion map. Values: complete|open|unknown|n/a.
    handoff: complete           # Missing field or dimension reads as `unknown`, never as `complete`.
    acceptance: open
    integration: complete
    validation: open
    deployment: n/a
    cleanup: unknown
  done_note: "validation waits on Revit end-user run"   # optional free text, string only
  unblocks: [P9-1, P10-1, P13-promote, P15d]
                                # direct downstream item ids. Drives the primary ranking axis.
  blocked_by: []                # upstream item ids that must land first
  effort: S                     # S | M | L — remaining work, not work already done
  risk: 2                       # 0-5, decay/fragility. Higher = rots or misleads faster.
  evidence:
    - kind: pr                  # pr | commit | log | doc | flag | db | run
      ref: BIMpossible#187
      url: https://github.com/YourBIMpossible/BIMpossible/pull/187
  verification:
    level: verified             # claimed | verified | partial | contradicted
                                # partial: mixed dormant+live scope — live leg holds, dormant
                                # leg unprobeable (see Dormant projects). suspended is a
                                # render-only badge, never a stored level.
    by: "PR#187 merged 2936c32f; BIMPOSSIBLE_REVIT_LINK_SYNC_ENABLED default OFF"
    at: 2026-07-26
  live_check:                   # commands that re-settle the deterministic half on read
    - "gh pr view 187 --repo YourBIMpossible/BIMpossible --json state,mergedAt"
  source: "BIMpossible_PHASE-STATUS.md row 7"
                                # where the judgment came from, so it can be re-read
                                # phase-scoped items: name the phase in title or source
                                # ("Phase 7", "Phase 3.10b") — `<target> roadmap` groups on
                                # this token, no separate field to keep in sync
  notes_url: null               # optional single link to the full doc. Never inline the doc.
```

Required: `id`, `title`, `projects`, `status`, `unblocks`, `verification`, `source`.
Everything else may be omitted; treat missing `effort` as `M`, missing `risk` as `0`,
missing `owner_gate` as `false`, missing `owner` as `null`.

Optional cross-project field:

- `affects_projects: [<key>, ...]` — projects an item *governs or touches* without being scoped
  to them for ranking. Use it for a workspace-owned governance item that must stay visible while
  pointing at another project (the dormancy gate is the canonical case). Unlike `projects`, it
  never puts the item on that project's board and never triggers a probe against it.

## Dormant projects (reassessment-bound repos)

A registry entry in `projects.yaml` may carry `dormant: true`. A dormant repo is under top-down
reassessment: `/next` must never run, emit, or propose a command against it. The flag is machine-read,
and the invariants are enforced deterministically, not by convention:

- **No runnable probes.** An active item scoped *entirely* to dormant projects must carry an empty
  `live_check`. `queue_store.py`'s `dormancy_defects` makes a non-empty one a hard load failure — the
  exact condition that would let a drill run a command against the dormant repo. Retain any historical
  command text only as non-executable `kind: log` evidence, labelled SUSPENDED.
- **Not actionable, not #1.** Park such an item (`status: parked`); parked is outside
  `ACTIONABLE_STATUSES`, so it can never rank as the #1 move.
- **Never verified from stale evidence.** The renderer forces the badge to **SUSPENDED** for a
  dormant-scoped item regardless of its stored `verification.level`, because an unprobed item cannot
  be currently verified. Set the stored level to `claimed` to match.
- **Gate visibility without reactivation.** Express the governing gate as a workspace-owned item with
  `projects: [workspace]` and `affects_projects: [<dormant key>]` — never by flipping the dormant
  project back to `active`. The board discloses every referenced dormant project explicitly.

### Mixed-scope items (one item, dormant + live legs)

An item whose `projects` include *both* a dormant and a live project (e.g.
`projects: [workspace, families]`) is not all-dormant, so it keeps its live-leg probes — but two
extra invariants apply, both machine-enforced by `dormancy_defects`:

- **No command may target the dormant leg.** Every individual `live_check` command is scanned;
  one that references a dormant checkout — by path (`F:/BIMpossible-Families`, either separator,
  subpaths), by the checkout's directory name as a standalone segment (a relative path, an MSYS
  `/f/...` path), or by a GitHub `owner/repo` slug reusing that name — is a hard load failure,
  whatever the item's declared legs (mis-scoping an item to hide a dormant leg is not a bypass).
  The live-leg commands stay runnable; only the dormant-targeting one must move to non-executable
  evidence.
- **`partial`, never `verified`.** The dormant leg is unprobeable, so a whole-item `verified`
  over-claims. Store `verification.level: partial`; `dormancy_defects` rejects a mixed item stored
  `verified`, and rejects `partial` on any item that is *not* mixed-dormant (the level is reserved
  for exactly this shape). The renderer prints a **PARTIAL** badge and a `⏸ dormant leg … suspended`
  marker so the active leg's evidence never hides the unverified one. (`partial` is a real stored
  level; `SUSPENDED` remains render-only, for the all-dormant case above.)

## Completion semantics (Stage A, shared with `/close`)

`done` is the only completion vocabulary; status is not completion. Consistency rules (soft defects,
reported by `queue_store.py`, driven to zero by `sync`, never auto-corrected):

- `live` → `integration`, `deployment`, `validation` each ∈ {complete, n/a}
- `landed` → `integration: complete`, `validation` ∈ {open, unknown}
- `blocked_owner` → at least one dimension `open`

`/close` writes its *Completed* bookend line from `queue_store.format_done(item)`
(`handoff:complete · acceptance:open · …`); `parse_done` inverts it exactly, so the anchor prose and
the store cannot drift. AC04 (local vs integration/publication vs verification distinguishable) is
read off this map, not inferred from status.

## Freshness (Stage A, read-only, `resume_queue.py`)

Evaluated per project leg; a multi-leg item prints worst-of **and** every leg. First match wins:

| State | Meaning |
|---|---|
| `UNREADABLE` | repo path missing or git failed — stored observation printed unchanged |
| `UNRECONCILED` | no `reconciled.<key>` watermark |
| `STALE` | watermark older than `STALE_DAYS = 7` — offer `sync` before naming a move |
| `MOVED` | a `kind: commit` evidence sha is not in HEAD's lineage (rewritten / other branch) |
| `TOUCHED` | a doc/log/flag evidence path changed since `verification.at` (same-day counts) |
| `BEHIND` | watermark not in HEAD lineage, or HEAD ≠ watermark and `verification.at` < sweep date |
| `FRESH` | none of the above |
| `SUSPENDED` | dormant leg — printed, excluded from worst-of |

A new commit alone never invalidates an item (no status change, no level downgrade); it can only
lower confidence to `BEHIND`. PR refs never drive `MOVED`. Unresolvable shas are ignored, not errors.

## Waiting set (Stage A)

W = `blocked_owner` ∪ (`owner_gate: true` ∧ status ∈ {`ready`, `landed`}). `parked` is an owner
choice, reported only as a trailing count. Empty `blocked_by`/`unblocks` is **not** readiness.

## Claiming (concurrent-session safety)

**Claim vs role label.** `owner` is overloaded in the live store (`owner`, `agent`, `Zeriah`,
`zeria`, null, plus real identifiers). Only a value shaped like a path, a worktree, or a
`branch/with-slash` is a *claim* (`queue_store.claim_kind` → `claim`); bare words are *role labels*,
inert for write protection and reported `(D3: left as-is)` — no normalization sweep. A non-null
`owner` therefore does **not** mean the item is in flight; only `status: in_flight` does.
Probe before writing: `python queue_store.py --claim-of <ID> --me <ident>` → exit 3 = foreign claim.

`owner` stops two concurrent sessions from writing conflicting updates to the same item during
`sync` — this workspace already forbids two sessions sharing one checkout (CLAUDE.md), and `sync`
is the one mode that writes the store, so the same discipline extends here.

- `owner: null` — unclaimed; any session may claim it.
- `owner: <identifier>` — set to the claiming session's branch or worktree path
  (`git rev-parse --show-toplevel` + current branch) the moment a session marks an item
  `in_flight`, or is about to write that item during `sync`.
- **Before writing an item during `sync`,** check `owner`. If it is set to a *different* session's
  identifier, do not write that item — report it as `OWNED BY <owner>` and skip it. Never silently
  overwrite another session's in-progress claim. Applies to both tiers: an item mid-archive under
  another session's claim is skipped the same way.
- Clear `owner` back to `null` when the item leaves `in_flight` (lands, blocks, or parks).
- A stale claim — `owner` set but the item hasn't moved in over 24h — is reported, not
  auto-released. Releasing on a timer risks two sessions racing right after expiry; a human clears
  a stale claim explicitly.

## Status vocabulary (closed — do not invent values)

| Status | Meaning | Actionable by owner? |
|---|---|---|
| `ready` | Could be started now, nothing upstream missing | yes |
| `blocked_owner` | Built and merged; awaiting a human decision, flag flip, or supervised run | **yes — this is where finished value strands** |
| `blocked_dep` | Waiting on another item in `blocked_by` | no |
| `blocked_external` | Waiting on a third party (vendor grant, API access, approval) | no |
| `in_flight` | Actively being worked right now | no |
| `landed` | Merged/shipped but **not yet verified live** | yes — verify it |
| `live` | Verified running in production with named evidence | no |
| `parked` | Deliberately deferred by an explicit decision | no |
| `dropped` | Killed. Retained for history so it is not rediscovered. | no |

`landed` vs `live` is the most important distinction in the model. Most stranded value in this
workspace sits in `landed`, and every source ledger blurs the two.

## Ranking algorithm

Deterministic. Same store plus same probes must produce the same order every run — unstable ordering
between sessions is the drift the user is trying to escape.

### Step 0 — Establish the anchor BEFORE sorting (this is the fix for goal-blind ranking)

Ranking has no meaning without a goal. Pick the anchor first, every run:

- **Stated focus** — if the project declares a current focus (a north-star file, a `focus:` key in the
  registry, or the user has named one *this session*), rank **toward it**: items on the path to that
  focus lead.
- **Roadmap-default (the common case)** — if no focus is stated, **default to roadmap phase order**.
  The #1 move comes from the **earliest incomplete phase** (per the live ledger read in `roadmap.md`
  step 3), not from the deepest dependency cone. Close phases in sequence.

**Never surface a launch / activation / ops-terminal item (e.g. `OPS-LAUNCH`) as the #1 move unless the
user has set production as the stated focus.** Such items are *sinks* — many things point at them, so
cone-rank mechanically drags the answer toward them — but reaching a sink is a goal the user chooses,
never a default the graph picks. This is the exact failure that made prior runs keep pointing at
"launch" while the real product work sat ignored.

### Step 1 — Measure the graph before trusting cone

```
cone(item) = size of the transitive closure of `unblocks`, following item ids
```

Walk `unblocks` breadth-first. **Detect cycles**: if an id is revisited, stop that branch, count it
once, and report the cycle as a data defect — do not silently loop or drop it. Unknown ids count as 1
and are reported as dangling references.

Then check the graph's *shape*, because cone only carries signal when the graph has depth:

- **Flat graph** — most actionable items have `cone == 0` and the top actionable cone is ≤ 1. Cone is
  meaningless here (it degenerates to "point at the lone sink"). Do **not** rank by it → Step 2b.
  *(This store is flat today: ~105 of 123 items unblock nothing; measure, don't assume.)*
- **Deep graph** — several items with `cone ≥ 2` feed distinct downstream work. Only here does
  cone-descending carry real unblock-leverage → Step 2a.

Report which regime you detected — it explains the ranking to the user.

### Step 2 — Sort

**2a. Deep graph (real dependency depth) with a stated production focus:** unblock-leverage is genuine.
1. `cone` descending — critical path. 2. `owner_gate == true AND effort == S` — stranded finished
value, near-zero cost to bank. 3. `effort` ascending. 4. `risk` descending. 5. `id` ascending.

**2b. Flat graph OR roadmap-default (the common case here):** rank by **payoff / finalization
leverage**, not cone.
1. **Roadmap position** — items in the earliest incomplete phase first (roadmap-default anchor only).
2. **Activates a dark feature** — a `landed`/`blocked_owner` item that flips a merged-but-off
   capability live. A small step that lights up real product value: the biggest win per unit effort.
3. `owner_gate == true AND effort == S` — other stranded finished value.
4. `effort` ascending. 5. `risk` descending. 6. `id` ascending (stable tiebreak).

### Two kinds of leverage — always name which one is driving the pick

- **Unblock-leverage** — finishing X frees blocked code (cone). Real *only* in a deep graph.
- **Visibility / finalization-leverage** — finishing X (verify a `landed` item live, or activate a
  dark feature) frees no code; it clears the fog so the board becomes honest and the next big chunk
  becomes pickable. When most value sits in `landed`, this is where the leverage is — surface it,
  never bury it. A one-step switch that unlocks a large *accountable* workload can outrank a large
  coding effort that unlocks nothing.

### Hard constraints

- The **#1 move** is the highest-ranked item whose status is `ready`, `blocked_owner`, or `landed`.
  Other statuses are reported but never recommended.
- **There is no "cone == 0 can never be #1" rule.** *(Removed 2026-08-30 — it was the exact mechanism
  that buried the flat field of real product work and forced every answer toward the cone sink.)* An
  item that unblocks nothing is a valid #1 when it activates a dark capability or sits in the earliest
  incomplete phase.
- If the top actionable item is `blocked_owner`, say explicitly that it needs the owner, not a build
  session, and state precisely what action closes it.

## Rendering (derived views only)

Sections, in this order. Omit any section with no items.

| Section | Statuses |
|---|---|
| Blocked on you | `blocked_owner` |
| Landed — not verified live | `landed` |
| Next up | `ready` |
| In flight | `in_flight` |
| Blocked elsewhere | `blocked_dep`, `blocked_external` |
| Parked | `parked` |
| Live (last 30 days) | `live`, pruned by `verification.at` |

Line format — one line, no wrapping into prose:

```
[P7-1] Supervised first Revit-Link sync — flag ON, test model, log §11
       unblocks 4 · S · bimpossible+addins · VERIFIED 2026-07-26 · PR#187
```

Rules:
- `unblocks N` is the cone size, not the raw list length.
- Always print `verification.level` in caps. `CONTRADICTED` renders with both readings beneath it.
  A dormant-scoped item renders `SUSPENDED` and a mixed dormant+live item renders `PARTIAL`
  (both with a `⏸` leg marker) regardless of the stored level — see Dormant projects.
- Multi-project items show all keys joined by `+`. This is the cross-repo signal — never hide it.
- Never emit a `Prior:` chain, nested parenthetical history, or a paragraph in a cell. If an item
  needs more than one line, the extra content belongs at `notes_url`.

## Store hygiene and archiving

- **Archive eligibility (deterministic):** a record moves from `queue.yaml` to `queue-archive.yaml`
  when its status is `dropped` or `live`, AND its id appears in no active item's `blocked_by`.
  `landed` is **never** eligible — it is actionable stranded value ("verify it"), the model's core
  concern. Recently verified `live` records stay visible: the QUEUE.md "Live (last 30 days)"
  section renders from both tiers.
- Archiving preserves the record verbatim plus `archived_at`, `archived_reason`,
  `source_queue_version`. Never summarize away evidence or verification prose.
- Archived records are immutable under ordinary reconciliation. **Reopening** archived work means
  creating a NEW active item with a new id carrying `follow_up_of: <archived-id>` — never mutating
  or un-archiving the record.
- **Never delete archive records** — they are the dedup index that stops merged PRs from being
  rediscovered as new work.
- Never reuse an `id`, even after `dropped`/archive; `queue_store.py` fails on duplicates across
  both tiers.
- Cone math runs over the UNION of both tiers, so archiving never changes active ranking;
  active `unblocks` refs into the archive are resolved, not dangling.
- `QUEUE.md` is regenerated from `queue.yaml` on every write, and carries a do-not-hand-edit banner.
- If either store file fails to parse or validate, **stop and report it**. Do not rebuild from
  scratch to recover — that silently destroys accumulated judgment.
