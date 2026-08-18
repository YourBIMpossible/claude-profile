# Item model, ranking, and rendering

Canonical store, two tiers (both in `F:\AI-Dev\.tools\state\`):

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
    level: verified             # claimed | verified | contradicted
    by: "PR#187 merged 2936c32f; BIMPOSSIBLE_REVIT_LINK_SYNC_ENABLED default OFF"
    at: 2026-07-26
  live_check:                   # commands that re-settle the deterministic half on read
    - "gh pr view 187 --repo YourBIMpossible/BIMpossible --json state,mergedAt"
  source: "BIMpossible_PHASE-STATUS.md row 7"
                                # where the judgment came from, so it can be re-read
  notes_url: null               # optional single link to the full doc. Never inline the doc.
```

Required: `id`, `title`, `projects`, `status`, `unblocks`, `verification`, `source`.
Everything else may be omitted; treat missing `effort` as `M`, missing `risk` as `0`,
missing `owner_gate` as `false`.

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

Compute for each item:

```
cone(item) = size of the transitive closure of `unblocks`, following item ids
```

Walk `unblocks` breadth-first. **Detect cycles**: if an id is revisited, stop that branch, count it
once, and report the cycle in the output as a data defect to fix — do not silently loop or drop it.
Unknown ids in `unblocks` count as 1 and are reported as dangling references.

Sort:

1. `cone` descending — **primary axis: critical path.**
2. `owner_gate == true AND effort == S` first — stranded finished value, near-zero cost to bank.
3. `effort` ascending (S, M, L).
4. `risk` descending.
5. `id` ascending — stable tiebreak, guarantees reproducible output.

Hard constraints:

- An item with `cone == 0` can **never** be ranked #1.
- The **#1 move** is the highest-ranked item whose status is `ready`, `blocked_owner`, or `landed`.
  Other statuses are not actionable and are reported but never recommended.
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
- Multi-project items show all keys joined by `+`. This is the cross-repo signal — never hide it.
- Never emit a `Prior:` chain, nested parenthetical history, or a paragraph in a cell. If an item
  needs more than one line, the extra content belongs at `notes_url`.

## Store hygiene and archiving

- **Archive eligibility (deterministic):** a record moves from `queue.yaml` to `queue-archive.yaml`
  when its status is `dropped`, or `live` with `verification.at` older than 30 days (outside the
  QUEUE.md live window), AND its id appears in no active item's `blocked_by`. `landed` is **never**
  eligible — it is actionable stranded value ("verify it"), the model's core concern.
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
