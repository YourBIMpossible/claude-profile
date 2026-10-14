# Enrolling an audit workflow (GRRP v1)

The contract every enrolled workflow follows. The workflow's own text carries two
marked blocks — `grrp-enrollment` (entry, before any discovery) and `grrp-exit`
(after its report) — and points here. Enrollment is additive: it changes none of the
workflow's checks, lenses, severities, report paths, or approval gates.

## Entry
1. **Nested run → no ledger of its own.** When the workflow runs as a lens or
   sub-step of another enrolled run (e.g. `/revit-functionality-audit` inside the
   weekly BIMpossible audit), it returns its findings to the parent; the parent is the
   single writer (SKILL.md §3). Skip the rest of Entry and Exit.
2. `grrp.py init --slug <slug> [--dir <dir>]` with the slug the workflow names. A
   recurring/scheduled run puts its date in the slug, so a new run never resumes last
   week's ledger; a same-slug ledger means an interrupted run: resume its finding IDs,
   do not rediscover.
3. Run brief from the invocation: Objective = run this workflow's checks over its
   scope; Scope = the window/paths it resolves; Boundaries = the workflow's own
   read-only/protected rules plus the active north star; Done means = AC-1 and AC-2
   below; Extra findings = classify; Authority = the workflow's own.
4. Criteria: `AC-1` every check/lens the workflow defines ran over its scope (a lens
   NOT RUN or degraded → `unmet`, evidence = the report's coverage note); `AC-2` every
   finding is in the report and the ledger.

5. **Batch.** A scheduled run belongs to an audit batch (its runs plus all follow-through).
   `grrp.py batch-init --slug <batch-slug>` returns the existing manifest or creates one; list
   this run's ledger as a `grrp-ledger` and, before any execution, state the endpoint: runs and
   follow-through included, authority available for repair/publish/merge/activation, authority
   gaps (never infer permission for a protected operation), and the archive evidence required.
   Then run `grrp.py batch --record <manifest>` once, even while items are still missing (it
   records whenever the manifest parses): it pins the endpoint, the inventory and every open
   ledger obligation so a restarted session cannot quietly change the contract or drop one.
   Changing the endpoint later needs `- Amended: <owner, date, why> (from EP-<latest pin>)`.

## Findings (one `grrp-finding` per report finding, kept or dismissed)
| workflow result | assessment | classification · resolution |
|---|---|---|
| verified / confirmed / re-demonstrated | confirmed_defect | fixed in this run → `original`, `resolved` + evidence; otherwise `independent`, `open` + a transfer |
| refuted / dismissed / NOT RETAINED | disproved (evidence = why) | `original`, `resolved` |
| HYPOTHESIS / unverified | unassessed | `independent`, `open` + a transfer |
| intended behaviour | intentional_behavior (evidence) | `original`, `resolved` |

`unassessed` keeps the verdict `incomplete`: an unverified finding is not done, and
the workflow says so instead of upgrading it. Report-only runs mark implementation
obligations `n/a:review-only`. A Critical/High that threatens an included criterion
carries `blocks:`.

## Transfers
Open findings go in one `grrp-transfer` to the place the workflow already hands work
to: its resolution artifact (e.g. `/audit-resolution`), or `queue:<ID>` in the `/next`
store. `ack:` only after that receiver's acceptance exists, naming it and its path.
No acceptance yet → leave `ack` out: `transfers_ok: false` is the true result.

## Exit
1. `grrp.py apply <ledger>` — ingest any worker receipts waiting in `<ledger-stem>.receipts/`.
2. `grrp.py gate --json <ledger>` → print `verdict` and `transfers_ok` verbatim,
   `closeable`, and the ledger path. Never soften the verdict.
3. `grrp.py handoff --record <ledger>` → the one owner handoff: decisions required now,
   blockers, conflicts, deferred items (owner + prerequisite), work continuing under existing
   authority, any other open work the gate counts, completed/superseded. `--record` logs each ask once; an unchanged ask is
   listed under "Awaiting owner", not raised again. Give the owner this, not the lens reports.

4. `grrp.py batch --record <manifest>` → the batch claim. Every criterion, finding
   disposition, delivery, left-flag and artifact disposition is a `grrp-item` carried to its latest
   evidence; owner actions are consolidated there (one per action, raised once, kept visible).
   Report its first line verbatim, and cite the latest `grrp-record` in the closeout. Only `100% COMPLETE — READY TO ARCHIVE` lets anyone call the
   batch complete; the ledger verdict above is one input to it. Keep working independent items
   while one waits on the owner; routine merges, bookkeeping and coordination are the agent's.

Results that arrive after exit (deploy, verification, a fixture becoming available) go in as
receipts and the next `apply` → `gate` → `handoff` picks them up; an older report never
reopens a newer `done` (references/ledger-format.md, `grrp-event`). A check that needs an
absent fixture or sign-in is `deferred` with `owner` + `prerequisite` — it is retained, and
it does not hold an otherwise complete audit open. A `denied` operation is never retried
through another route; it surfaces once as an owner decision.

**Approval fields are attestations:** `ack`, `owner_action`, `scope_approval` and
`action_approval` are free text the parent writes; the gate checks shape, never that
the act happened. They are attestations, not verified authorization, and never
substitute for the workflow's own approval gates (owner sign-off, preflight scripts,
dirty-tree guards, Checklist rules), which stay in force unchanged.
