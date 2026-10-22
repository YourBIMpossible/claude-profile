# LANE GRANT — one bounded lane, from grant to delivery endpoint

<!--
  Copy into the chat message that starts the lane. Fill every field; delete nothing.
  Task blank, "TBD", or both options filled = the lane asks the owner once and edits nothing
  until answered.
  No field is inferred from memory, a summary, or a prior grant.
-->

## Task
<!-- Exactly one of the two. Example: CP-SLOP1012-LOW-BATCH ({tools}\state\queue.yaml) -->
- Queue ID / task:
- OR selection authority (granted / not granted): ___ — criteria: ___

## Placement
- Repo:
- Remote base ref: `origin/HEAD` = `origin/<default>` (verify: `git ls-remote --symref origin HEAD`)
- Branch:
- Worktree path: `<repo>\.claude\worktrees\<lane>`

## Delivery endpoint
<!-- Exactly one. -->
- [ ] report
- [ ] ready PR
- [ ] merge
- [ ] deploy

## Authority
| Action | Granted / Withheld |
|---|---|
| Push | |
| Merge | |
| Deploy | |
| Live config | |
| Worktree cleanup | |
| Audit status change | |

Blank = Withheld. This grant overrides any repo pre-authorization for this lane.

## Exclusions
-

## Acceptance
| Criterion (observable) | Evidence type (output, SHA, test run, URL) |
|---|---|
| | |

## Pre-publication preflight
Run before the first push or PR create/update, and again whenever the remote base or the lane's commits change.
```
git fetch origin && git merge-base --is-ancestor origin/HEAD HEAD && echo base-ok
git log --oneline origin/HEAD..HEAD
python tools/pre_publish_check.py
```
- These make no source-file edits and publish nothing remotely. They are not all read-only:
  `fetch` updates local git metadata and remote-tracking refs.
- Resolve `origin/HEAD` and verify it matches the actual remote default before relying on it.
- `base-ok` proves only that the remote default is an ancestor of HEAD. It does not prove the
  unpublished commits belong to this lane. A branch cut from local main can still print it.
- Read the complete `origin/HEAD..HEAD` listing. Every commit must be attributable to this lane;
  any foreign or unexplained commit means no publication.
- Wrong scope: re-cut only this lane from the verified remote default and carry only its
  authorized changes. Do not reconcile unrelated history.
- `pre_publish_check.py` applies only where the repo has it; elsewhere record "n/a".
- These are procedural detection checks, not hook enforcement.

## Closeout states
<!-- GRRP meanings. Each line stays blank until its evidence is pasted. Never upgrade a state without evidence. -->
- Run finished: <!-- the lane's work ran to its end; evidence: closeout path + final HEAD -->
- Repair resolved: <!-- only when `grrp.py gate --json` (pasted verbatim) shows verdict complete AND transfers_ok true (closeable: true); otherwise record verdict and transfers_ok as-is, unresolved -->
- Lane closed: <!-- delivery endpoint reached + acceptance verified; evidence matching the endpoint: report path / PR URL + ready (non-draft) state / merge SHA on the remote default / deploy verification -->
- Audit closed: <!-- the audit's own status, set by its owner; `closeable: true` alone is not closure; a lane does not set it unless Audit status change is Granted -->

## Return only if
<!-- Conditions that end the lane early with a closeout naming the smallest owner action. -->
-
