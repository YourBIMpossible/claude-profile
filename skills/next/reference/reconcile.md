# Reconcile procedure (`sync` and `init`)

The expensive path. Everything else in this skill is designed so this runs rarely.

Goal: bring `queue.yaml` back into agreement with reality, and advance the watermark so the next
reconcile only has to read the delta.

## Watermarks

Per project, in `queue.yaml`:

```yaml
reconciled:
  bimpossible:
    at: 2026-07-26
    commit: 08116c5        # main tip that was read
    pr: 229                # highest PR number seen
```

`sync` reads only what changed since the watermark. `init` has no watermark and reads everything.
Never advance a watermark for a project whose sources failed to read — a false watermark permanently
hides the window that was skipped.

## Sources, in priority order

Read cheapest and most trustworthy first. Stop early if the delta is empty.

1. **Machine facts** (always, cannot lie): `git log <watermark>..HEAD`, branch/ahead/behind,
   working-tree state, tags, `gh pr list --state all --search "updated:>=<date>"`, CI status.
2. **Deploy and runtime evidence**: feature-flag values, migration head, live-smoke logs, deploy
   records. This is what separates `landed` from `live`.
3. **Owner ledgers** — `BIMpossible_PHASE-STATUS.md`, `BIMpossible_WAVE-STATUS.md`, and whatever
   `ledgers:` the registry names. **Judgment source, never verification source.** A ledger claim
   becomes `verification.level: claimed` until machine evidence confirms it.
4. **Audit reports, build logs, decision logs** — for items and gates the ledgers missed.

When a source in 3 or 4 contradicts a source in 1 or 2, the machine fact wins for the *fact*, and the
disagreement is reported as a doc/reality mismatch. Do not edit the owner ledger to fix it — draft
the corrected row and hand it to the owner.

## Per-item decisions

For every existing item in scope, decide exactly one:

| Decision | When | Action |
|---|---|---|
| **Confirm** | Evidence still holds | Refresh `verification.at`, no other change |
| **Promote** | Status moved forward (`ready`→`in_flight`→`landed`→`live`) | Update `status`, add the new evidence, set `verification` from machine evidence |
| **Demote** | Claimed progress is not real | Move status back, set `verification.level: contradicted`, keep both readings in `by` |
| **Close** | Done and verified live | `status: live` with named evidence |
| **Drop** | Superseded or killed by decision | `status: dropped`, record the deciding doc in `source` |
| **Split** | One item turned out to be several | Drop the original, create new ids, wire `unblocks`/`blocked_by` |

Then scan for **new** items in the delta: merged PRs with no matching item, new flags that default
off, new gates in ledger rows, audit findings that block production, un-run verification steps.

**"No matching item" means no match in EITHER tier.** Before creating an item for a discovered PR
or commit, probe the combined index:

```bash
python F:/Claude-Tools/state/queue_store.py --known-pr <repo>#<N>     # exit 0 = known, 2 = unknown
python F:/Claude-Tools/state/queue_store.py --known-commit <sha>
```

A PR represented only in `queue-archive.yaml` is **known** — never re-create it as a new active
item. If new evidence genuinely reopens archived work, create a NEW active item with a new id and
`follow_up_of: <archived-id>`; ordinary reconciliation never edits, promotes, or deletes archive
records. Confirm/Promote/Demote/Drop decisions apply to ACTIVE items only.

Every new item needs `unblocks` populated. An item with no `unblocks` can never rank #1, so leaving
it empty silently buries the work — if nothing downstream depends on it, say so explicitly by
setting `unblocks: []` and `risk` appropriately, rather than leaving it unconsidered.

## Evidence rules

- `verification.level: verified` requires naming the artifact: commit SHA, merged PR with state,
  log file path, DB row, or observed flag value. Prose is not evidence.
- **No verdict without a named source.** If a probe cannot run, record `claimed` and note the failed
  probe in `by`. Never downgrade to a guess and never treat a failed probe as a negative result.
- A live-smoke log that exists is not the same as one that passed — read it.
- `landed` → `live` requires runtime evidence, not merge evidence. This transition is where the
  workspace's history shows the most over-claiming; be strict about it.

## Cross-repo items

Lockstep work (a backend PR plus its add-in PR) is **one item** with `projects: [a, b]` and both PRs
in `evidence`. It is not `live` until both halves are verified. If one half merged and the other did
not, the item stays `landed` with the gap named in `verification.by` — that asymmetry is exactly the
condition the per-project ledgers lose.

## Finishing

1. Recompute ranks (`reference/item-model.md`). Report any cycles or dangling `unblocks` ids.
2. **Claim check** — for every item about to be written, if `owner` is set to a different
   session's identifier, skip writing that item and report it `OWNED BY <owner>` instead
   (`reference/item-model.md` § Claiming). Write everything else normally.
3. Write `queue.yaml` in the live store (`F:\Claude-Tools\state`), then validate both tiers:
   `python F:/Claude-Tools/state/queue_store.py` (must print OK, exit 0). Archive-eligible
   records (item-model.md "Store hygiene and archiving") may be moved to `queue-archive.yaml`
   in the same pass; keep the watermark block structured values only — sync narrative goes in
   the commit message, not the file.
4. Regenerate `QUEUE.md` deterministically — never hand-render it:

   ```bash
   python F:/Claude-Tools/state/render_queue.py --generated-on <today> --check   # expect CHECK OK after step-4 write
   ```

   `render_queue.py` (no flags) writes `QUEUE.md`; `--check` re-derives and byte-compares without
   writing. If `--check` fails after you regenerate, the store and the view disagree — fix it, do not
   ship the mismatch.
5. Advance watermarks for successfully-read projects only.
6. **Publish the mirror (Option B, 2026-08-08).** The workspace repo carries a committed mirror at
   `F:\BIMpossible-Workspace\.tools\state`. Copy the regenerated `queue.yaml` + `QUEUE.md`
   (and `render_queue.py`/`projects.yaml` if they changed) into the mirror and commit **both together**
   through the normal workspace path — `Push-And-Verify.ps1` (PowerShell), never Bash git, never a
   hand-edit of the Markdown. The mirror's `.gitattributes` pins the store to LF so `--check` holds on
   any checkout. Skip only if the sync changed no queue data. This is the one data-publish path — the
   workspace `next` skill copy does not duplicate it.
7. Report the diff: promoted, demoted, added, dropped, contradictions found, and whether the #1 move
   changed. If the #1 move changed, lead with that. Name the mirror commit/PR if one was published.

Do not also print the full queue after a sync — run the drill view if that is wanted.
