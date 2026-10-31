# Context audit — Phase 2B closeout (2026-09-21)

Applies the `safely_actionable` + `factual_correction_candidate` rows from
[2026-09-21-needs-owner-decision-ledger.md](2026-09-21-needs-owner-decision-ledger.md), with
the same non-versioned-memory protections used in Phase 2A-3: before/after full snapshots, a
pre-edit apply manifest, archive-not-delete, nugget-merge into verified canonical targets,
MEMORY.md index updates, post-edit hash verification, link/orphan validation, and
context-weight before/after.

## Count discrepancy — disclosed

The user's Task 2 instruction referenced "16 records" / "eight" + "eight" safely-actionable and
factual-correction rows. The ledger's own closing Totals line also miscounted, as 8/8/8/1. Both
are wrong. The ledger's real per-row tables sum to **5 safely_actionable + 9
factual_correction_candidate = 14 rows** (+9 retain_active +1 requires_owner_decision +1
already-resolved-prior-session = 25 total). Every file the user explicitly named in Task 2
matches those 14 exactly — one user bullet bundled two files under one line item, which is what
produced the "eight" framing. This pass applied the real 14-row set; no row was skipped or
invented to reconcile the count.

## What was applied

**Archived (5, archive-not-delete, post-move hash-verified byte-identical to pre-move):**
- `cross-repo-ci-cost-audit-2026-08-19.md` (Claude-Profile) → `archive/2026-09-21-context-phase2b/`.
  Generic verification method (protection strict-flag + real PR-trigger run-history count) merged
  forward into [[ci-posture-lean-2026-09-11]] (BIMpossible store) as a dated "carried forward"
  paragraph; its Supersedes line now lists this file.
- `feedback-act-then-report.md` (Claude-Profile) → archived. Rejected-report-cap historical
  lesson kept inline in the MEMORY.md pointer line; the duplicated standing rule is dropped in
  favor of the canonical copy in `CLAUDE.md`.
- `feedback-file-link-format.md` (Claude-Profile) → archived. **Flagged explicitly, not a
  routine trim:** this is a recurrence the owner has flagged roughly ten times. The 2026-09-21
  root-cause pair (scratch-cwd session start; an absolute-path "fix" that also failed) is kept
  inline in the MEMORY.md pointer, and the enforcement note records it now lives in three places
  (the decay_reminder hook text, the CLAUDE.md deliverables rule, and this pointer).
- `delivery-control-skills-state.md` (Claude-Profile) → archived, marked resolved (branches gone,
  both skills live/maintained).
- `ci-runtime-cleanup-state-2026-09-02.md` (BIMpossible) → archived. Its one durable fact was
  already fully present in [[phase4-xdist-promotion-gate]], so no additional merge-write was
  needed.

**Factual corrections (9, in place, surrounding content preserved):**
- `gstack-activation-state.md` — frontmatter description only: removed the stale "all 4 scanners
  gating" headline, replaced with the current secret-scan-only status. Body's existing
  2026-09-11 correction paragraph and all 4 durable lessons untouched. **Manifest correction:**
  the pre-edit apply-manifest listed this file's store as `bimpossible`; it actually lives in the
  Claude-Profile store. The edit itself was applied at the correct path — only that bookkeeping
  field was wrong, now corrected in `after-manifest.json`.
- `phase7-step2-shipped.md` — added a dated correction: P7-SYNC-GOLIVE is shipped/live (pilot
  PASS commit `59df1324`/#442, 2026-08-21; PROMOTED landed→live 2026-08-30), replacing the stale
  "owner-deferred" framing. Original text preserved below as retained history.
- `session-2026-08-04-endstate.md` — struck the "waiting on P7-SYNC + OD3/OD4" line; P7-SYNC-GOLIVE
  is live, OD3/OD4 were RULED BY OWNER 2026-08-18 (`queue.yaml` id `OD-DECISIONS`). The still-open
  "doors build ready on owner go" item is untouched.
- `ai-dev-extraction-burnin-state.md` — appended a correction marking the burn-in framing expired,
  pointing to BIMpossible-Workspace commit `70b6115` (2026-09-12). Explicitly does **not** resolve
  the 3 newly surfaced owner decisions that commit raised (Revit-OCR corpus, Postgres dump,
  Finance-Dashboard backup) — those remain open, unaddressed by this pass.
- `aps-surface-isolation-followup.md` — struck the two stale secondary facts (#271 rough edges;
  paper-trail PR #19 unmerged) with inline corrections (fixed via #285 2026-08-07; merged
  2026-08-08). The parked relay-routing deferral itself is confirmed unchanged and re-verified
  current as of 2026-09-07.
- `bimpossible-overnight-tool-revisions.md` — corrected "never merged to main": commits `1bd1d88`
  and `f76aaa1` are confirmed ancestors of `main` via `git merge-base --is-ancestor`; the branch
  was deleted, the commits weren't lost. All "durable traps" content preserved.
- `resolution-tooling-session-2026-08-05.md` — retired item 7 (host-side `--noconftest`
  workaround) per commit `e0baf759` (2026-09-16); items 1–6 explicitly retained as still-unresolved,
  not individually re-verified this pass.
- `state-tree-ownership-handoff-2026-09-02.md` — appended a correction marking the watermark and
  worktree topology stale (19+ days, 100+ PRs behind). Does **not** assert a new current
  watermark or ownership value — points to live `{tools}\state\queue.yaml` and a fresh
  `git worktree list` instead.
- `phase4-xdist-promotion-gate.md` — replaced the 2026-09-11 "needs owner check" flag with a
  confirmed-dead finding: `ci.yml` deleted (`a3d32a92`), successor `clean-room-ci.yml` deleted
  (`2fb01032`/#652), lingering doc claims retired (#669). The aec-module-identity root-cause
  lesson is explicitly unaffected and preserved.

**MEMORY.md index updates:** both stores' indexes rewritten to point at the above (archive
pointers for archived files, corrected one-line summaries for in-place corrections). Line-by-line
diffs are in `apply-manifest.json` / `after-manifest.json`.

## What stayed untouched

- **The one true owner decision** — `bimpossible-site-untracked-project.md` — was not read or
  edited this pass. It remains the single preserved, unresolved line item.
- **All 9 `retain_active` rows** — not read or edited this pass (listed in
  `apply-manifest.json`'s `excluded_untouched` block).
- The already-resolved-prior-session row (`workspace-addins-branch-cleanup-pending.md`) was left
  as-is; it needed no action.

## Closeout condition — verified against the user's checklist

- **All memory links resolve:** yes. Both stores: 0 orphaned live files (every live `.md` has a
  MEMORY.md mention), 0 broken MEMORY.md links (every linked file exists), verified by direct
  scan against the live directory listing.
- **No live non-archive orphan introduced:** yes, same scan as above.
- **All superseded sources archived, not deleted:** yes — 5/5 archived files hash-verified
  byte-identical pre-move vs post-move (`sha256sum`).
- **Every retained lesson is in a verified canonical/current record:** yes — the one cross-store
  nugget (`cross-repo-ci-cost-audit-2026-08-19` → `ci-posture-lean-2026-09-11`) is merged and
  hash-changed accordingly; the one same-store no-write case
  (`ci-runtime-cleanup-state-2026-09-02` → `phase4-xdist-promotion-gate`) was confirmed already
  present before archiving, not re-merged.
- **Before/after manifests and full snapshots exist:** yes —
  `~\.claude\memory-backups\20260921-phase2b\` holds `apply-manifest.json` (pre-edit,
  built before any edit), `after-manifest.json` (post-edit hashes + corrections), `before\` (135
  files) and `after\` (135 files, same count — 5 files moved into a new `archive/` subdir inside
  their existing store, 9 edited in place, 0 added/deleted at top level).
- **Context scanner does not increase unexpectedly:** partially — see below, disclosed rather
  than glossed over.
- **Both active indexes no longer promote stale/duplicated policy/status records:** yes, per the
  updates above.
- **The one project-table ownership decision is explicitly preserved as open:** yes —
  `bimpossible-site-untracked-project.md`, untouched.

### Context-weight before/after (disclosed, not just asserted)

Last recorded run before this pass's edits (`2026-09-22T03:57:47Z`, pre-existing status
already `warn` — driven by an unrelated per-source graphify-description length issue, not this
pass):

| scenario | memory-index bytes (before) | memory-index bytes (after) | delta |
|---|---|---|---|
| global_session (Claude-Profile MEMORY.md) | 15272 B | 16043 B | **+771 B** |
| bimpossible_session (BIMpossible MEMORY.md) | 14839 B | 14774 B | −65 B |

The BIMpossible index shrank as expected from archive-pointer replacement. The Claude-Profile
index grew by 771 B — the direct, expected cost of two explicit Task 2 instructions: spelling the
`feedback-act-then-report` rejected-report-cap nugget and the `feedback-file-link-format`
recurrence-pattern flag out inline in the MEMORY.md pointer lines themselves (rather than leaving
bare one-line pointers), per "merge any unique durable nugget into the verified current target"
and the explicit "flag this in closeout as a recurrence pattern" instruction. This is a disclosed
tradeoff, not a regression: overall scenario status (`clean` per-source for both indexes) was
unaffected, and the pre-existing overall `warn` verdict (graphify skill-description length) is
unrelated to this pass and unchanged by it. `python tools/tests/test_context_weight.py` — 28/28
cases pass, both before and after.

## Artifacts

- `~\.claude\memory-backups\20260921-phase2b\apply-manifest.json` — pre-edit manifest.
- `~\.claude\memory-backups\20260921-phase2b\after-manifest.json` — post-edit hashes,
  manifest corrections, snapshot/validation summary.
- `~\.claude\memory-backups\20260921-phase2b\before\`,
  `~\.claude\memory-backups\20260921-phase2b\after\` — full before/after snapshots
  of both memory stores (135 files each).

Note: the memory stores themselves
(`~\.claude\projects\*\memory\`) are not git-backed and are not part of this commit
— only this closeout note is.
