---
name: next-workspace-sync-weekly
description: Weekly /next workspace sync (portable). Reconciles queue.yaml against reality through reconcile.md step 5, validates + regenerates QUEUE.md, flags WAVE-STATUS/PHASE-STATUS staleness. Never pushes, commits, or opens a PR.
---

# next-workspace-sync-weekly (portable)

Replacement for the scheduled routine of the same name. Same meaning; every machine path is a parameter.

## Parameters (resolve all before starting; no defaults are machine paths)

| Name | Meaning |
|---|---|
| `STATE_DIR` | /next live store holding `queue.yaml`, `queue-archive.yaml`, `QUEUE.md`, `queue_store.py`, `render_queue.py`, `projects.yaml` |
| `WORKSPACE_ROOT` | BIMpossible-Workspace checkout (for the WAVE-STATUS ledger: `<WORKSPACE_ROOT>/00_Strategy/BIMpossible_WAVE-STATUS.md`) |
| `MIRROR_DIR` | workspace mirror path (normally `<WORKSPACE_ROOT>/.tools/state`); only ever *compared*, see Step 6 |
| `GH_REPOS` | repos to check for merged PRs (default set: `YourBIMpossible/BIMpossible`, `YourBIMpossible/BIMpossible-AddIns`) |
| `TODAY` | run date, `YYYY-MM-DD` |
| `LIVE` | `true` only when `STATE_DIR` is the canonical store and the operator intends a real sync |

Wrapper: `scripts/sync_finish.py` (this skill's folder). It refuses the canonical store unless `--live`.
Canonical store location comes from `--canonical-state-dir` or env `NEXT_CANONICAL_STATE_DIR`.

## Procedure

Invoke the `next` skill with args `workspace sync`, following its `reference/reconcile.md`
**through step 5 only**, substituting `STATE_DIR` wherever reconcile.md names the live store path:

1. Read machine facts (git/gh) and owner ledgers since the last watermark (`reconciled:` in `<STATE_DIR>/queue.yaml`).
2. Confirm / promote / demote / add / drop items.
3. Recompute ranks; run the claim check (skip items owned by another session, report `OWNED BY <owner>`).
4. Write `<STATE_DIR>/queue.yaml`.
5. Finish deterministically with the wrapper (validate both tiers, regenerate QUEUE.md, byte-check, watermark delta):

   ```
   python <skill>/scripts/sync_finish.py --state-dir <STATE_DIR> --generated-on <TODAY> --baseline <pre-sync copy of queue.yaml> [--live]
   ```
   Exit 0 required. Non-zero = stop, report, do not publish anything.
6. Advance watermarks only for projects whose sources read cleanly. A source that failed (gh unauthenticated, network down) = say so plainly and leave that project's watermark unchanged. The wrapper's watermark delta must show only the projects that read cleanly.

### Ledger staleness (same pass; never edit these ledgers)

- `<WORKSPACE_ROOT>/00_Strategy/BIMpossible_WAVE-STATUS.md`: read its `**Updated:**` date, then for each repo in `GH_REPOS`:
  `gh pr list --repo <repo> --state merged --search "merged:>=<that date>"`.
  Any merged PR that looks wave-worthy (real feature/fix, not pure docs/chore/ops) → staleness flag naming PR numbers + merge dates. The ledger is updated by each shipping session's own close-out, not by this sync.
- Any other ledger `<STATE_DIR>/projects.yaml` names for bimpossible (e.g. PHASE-STATUS.md): same check.

### Step 6 — SKIPPED (mirror publish)

Do NOT run reconcile.md step 6 (Push-And-Verify.ps1, git commit, git push, PR). Unattended run, no live user turn to approve anything leaving the machine. Instead compare `<STATE_DIR>/queue.yaml` + `QUEUE.md` with `<MIRROR_DIR>` and report what *would* be published (files that differ, one-line diff summary) for the owner to publish next session. The original routine allowed leaving the regenerated files as uncommitted copies in `MIRROR_DIR`; that copy is optional and only when the operator passed `MIRROR_DIR` for writing — default is compare-only.

## Report (terse, /next output style)

- Lead with whether the #1 move changed.
- Promoted / demoted / added / dropped items.
- WAVE-STATUS / PHASE-STATUS staleness flags with named PRs.
- Wrapper result line (validate / render / check exit codes, watermark delta).
- Pending-publish diff waiting for manual push.
- Any source that failed to read, and which watermark was held back.
