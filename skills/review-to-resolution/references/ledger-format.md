# GRRP v1 ledger format

One Markdown file. Prose sections for humans; fenced `grrp-*` blocks of flat
`key: value` lines for the gate. Lists are comma-separated; `none` = empty.
`unknown` / `tbd` / `?` are rejected everywhere — write a value or `n/a: <reason>`.

```
# GRRP ledger — <slug>
grrp: v1
## Run brief            (6 bullets: Objective, Scope, Boundaries, Done means, Extra findings, Authority)
## Change log           (- YYYY-MM-DD: what changed and who decided; owner questions as "owner-asked:")
## Acceptance criteria  (grrp-criterion blocks)
## Findings             (grrp-finding blocks)
## Children             (grrp-child blocks)
## Transfers            (grrp-transfer blocks)
## Checkpoint           (next action, revisions, temp state, approvals, cleanup)
```

## grrp-criterion
| key | required | values |
|---|---|---|
| id, text | yes | `AC-1` … |
| status | yes | met · unmet · not_applicable · accepted_exception |
| evidence | when met | receipt, commit, test log, run |
| reason | when n/a or exception | exception = the owner decision that allows it (never "verified") |

## grrp-finding
Required: `id source classification assessment resolution execution_state owner
lane requirement authority blocks evidence`. Optional: `severity location
obligations scope_approval action_approval owner_action duplicate_of reason` (any other key is
kept and ignored).

| key | values / rule |
|---|---|
| source | original (supplied/charter) · discovered |
| classification | original · blocker · independent · enhancement · externally_owned |
| assessment | unassessed · confirmed_defect · intentional_behavior · disproved (last two need evidence) |
| resolution | open · resolved (needs evidence) · accepted_exception (needs reason) |
| execution_state | identified → implementing → implemented → reviewed → integrated → deployed → verified → cleanup_complete |
| owner | never empty: parent, child id, `queue:<ID>`, named session |
| lane | parent · external · a child id |
| requirement | a criterion id, or `n/a: <reason>` |
| authority | authorized · approval_required (needs `action_approval` before execution) · protected (no execution unless `owner_action:` cites the owner's dated act or ratification (must contain `owner` and a YYYY-MM-DD date) — the item stays protected and any agent action stays in `evidence`) |
| blocks | criterion ids this finding blocks while open, or none |
| obligations | `review=done:<evidence>; integration=n/a:<reason>; deployment=pending; …` |

Evidence-state rule: an `execution_state` at or past reviewed/integrated/deployed/
verified/cleanup_complete is invalid unless the matching obligation is `done` or
`n/a`. So "merged" is `integrated` with `deployment=pending`, never `verified`.

## grrp-child
Required: `id objective exclusions acceptance reserved return_evidence owner
location`. Optional: `depends may_delegate (default false) spawned receipt`.
Receipt = commit/PR, tests run, verification scope + result, cleanup, remaining
limitations, ledger revision it was based on — compact, not a transcript.

## grrp-transfer
Required: `id findings receiver next_action acceptance follow_up`. `ack` required
unless `receiver: queue:<ID>` (then the queue item itself is the acceptance; see
SKILL.md §4). `ack` is a parent attestation: the gate only checks it is non-blank, so it must
name the receiver and where the acceptance lives; write it only after that
acceptance exists.

## Example
```grrp-finding
id: F-003
source: original
severity: low
classification: original
assessment: confirmed_defect
resolution: resolved
execution_state: integrated
owner: parent
lane: parent
requirement: AC-1
authority: authorized
blocks: none
evidence: fix 4b2c1d; test_x red->green
obligations: review=done:review-all 2026-10-05; integration=done:merged 4b2c1d; deployment=n/a:library, not deployed; verification=n/a:no runtime surface; cleanup=n/a:nothing created
```

## Parser rules (v1)
- A block opens with exactly ```` ```grrp-<kind> ```` at column 0 and closes with a bare ```` ``` ````. Any other line containing ```` ```grrp ```` (typo, indent, unclosed) makes the ledger `invalid` — never silently dropped.
- Ordinary fences (```` ``` ````/`~~~`, any length) are skipped; an unclosed one, or any grrp opener inside one, is invalid — ledgers hold no example blocks.
- A key repeated inside one block is invalid.
- `obligations` separates entries with `;` only; evidence may contain commas.
- `grrp: v1` must be its own line; brief keys are read only under `## Run brief`.
- `classification: blocker` requires `blocks:`; at least one criterion must be `met`/`unmet`.
- `queue:<ID>` transfers read only the item-level `  status:` (exactly one) and need the ledger filename as a whole token.
