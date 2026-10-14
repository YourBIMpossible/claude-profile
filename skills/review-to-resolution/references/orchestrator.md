# GRRP Orchestrator role

Turns classified work into bounded lanes and accountable transfers. Never
redefines the parent's scope, criteria, or completion.

## Branch only when it pays
Open a child lane when coherent work has its own acceptance criteria, different
dependencies/approvals, another repo/system, an ownership conflict, real volume,
or needs worktree isolation. Otherwise do it sequentially in the parent. One
child per coherent unit, not per edit.

## Before a child
1. Search `queue.yaml`, open ledgers, and `git worktree list` for an equivalent
   owner/lane → link instead of duplicating.
2. Confirm which criteria the child blocks.
3. Write the `grrp-child` contract (objective, exclusions, acceptance, reserved
   files/state, depends, return_evidence, owner, location; `may_delegate: false`
   unless the parent explicitly allows).
4. Parallelize only across disjoint reserved files/state. Worktree per lane
   (house rule); never clean up or overwrite another active lane.

## Child prompt carries
The contract, the ledger path (read-only for the child), and: "Return a receipt;
do not edit the ledger. Also write your per-finding results as `grrp-event` blocks
(references/ledger-format.md) to a new file in `<ledger-stem>.receipts/`." Retries fix execution failures, never renegotiate
acceptance. Exhausted retry/concurrency budget → checkpoint + escalate.

## On return
Validate the receipt against the contract; prose-only "done" without the
required proof is rejected (receipt stays `none`, parent stays blocked).
Changed scope or consequences → stop that action, get approval. Ingest results
with `grrp.py apply <ledger>` (single writer; reads the `.receipts/` inbox plus any
receipt paths given; `--dry-run` first when unsure). It dedupes repeated receipts,
keeps older results as history, refuses retries of a denied operation without a dated
owner approval, and leaves contradictory evidence as an explicit `conflict` — never
pick a side by hand-editing the finding. Lanes that never had a contract (a later
deploy or verification lane) report the same way: a receipt in the inbox, no owner relay. Child closes on its own criteria; parent closure is the
gate's, and batch closure is `grrp.py batch`'s: a lane finishing (even the coordinator's own
repair) clears only its items. Consume each receipt into the batch manifest too (item status +
latest evidence); contact other sessions directly, never through the owner.

## Transfers
Independent work → `/next` item (status `ready`, `source`/evidence referencing the
ledger filename, acceptance + trigger in the note) and `receiver: queue:<ID>`, or
a named executor with `ack:`. An unacknowledged entry is a failed transfer, shown
as OUTSTANDING by the gate.
