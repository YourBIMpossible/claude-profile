# Convergence (adjudication layer)

`/bim-code-review` aggregates its blind subagents **unmerged** — correct for a
focused two-axis skill. `/review-all` combines a wider surface, so it adds an
adjudication layer on top. **Do NOT concatenate or average lens outputs** — that
reproduces the four-noisy-variants-of-one-defect problem the scan warned about.

## Finding contract (every lens emits this)

```yaml
finding:
  lens: <lens key>            # security-diff, code-review, bim-code-review:spec, revit-lifecycle, ...
  severity: critical|high|medium|low|info
  confidence: high|medium|low
  location: <path:line or a narrow, identifiable region>
  root_cause_key: <stable slug naming the underlying defect, not its symptom>
  evidence: <what in the code shows it>
  failure_path: <input / state / interleaving that triggers it>   # exploit_path for security
  consequence: <concrete impact>
  recommended_fix: <direction, not a patch>
  provenance: [<lens keys that raised it>]
```

Convergence adds two more fields to each **retained** finding — never emitted by a
lens, computed only here: `lineage` (cross-pass provenance) and `action_tier`
(`BLOCKER` | `FOLLOW-UP`). Both are described below.

## Lineage tagging (cross-pass)

Step 0 always diffs the full branch (a narrower range misses defects that pre-date
the branch but are newly reached by it). Lineage is what distinguishes "first
review of this surface" from "what's left after N passes."

Step 2 ingests the most recent prior `/review-all` report reachable on this branch
(never re-running it — same ingest-only discipline as auto-gate findings). Tag
every retained finding against it:

- **`new`** — no matching `root_cause_key` and no overlapping location anywhere in
  the prior report, retained or NOT RETAINED.
- **`residual`** — same `root_cause_key` was retained in the prior report and the
  defect is still present; its remediation direction was not (fully) applied.
- **`fix-adjacent`** — a different `root_cause_key`, but the location falls inside
  code that was touched specifically to remediate a prior report's retained
  finding. Correlate by: (a) the file/function appears in commits added since the
  prior report's recorded range, and (b) the prior report names that same
  file/function in a retained finding's location or remediation direction, or a
  commit message / code comment cites the prior report directly (e.g. `# review-all
  pass-8 #2`, as seen in practice). Best-effort — state the correlation basis in
  the output rather than just asserting the tag.
- **`previously-not-retained`** — same `root_cause_key` as an entry in the prior
  report's NOT RETAINED list. Re-adjudicate fresh — context may have changed since
  — never auto-drop on the strength of the earlier call, but flag that it was
  already considered once, and why.

No prior report found (first pass on this branch, or none exists in the repo's
report location) → every retained finding is `new`. Say so explicitly in the
output rather than silently omitting the field.

A finding `fix-adjacent` to a prior **BLOCKER** is itself a BLOCKER (below) — a
fix generating the next pass's defect is the runaway-loop signal.

## Action tiering — BLOCKER vs FOLLOW-UP

Severity (`critical`..`info`) is a per-lens judgment and is noisy across lenses —
a stale comment can get called `HIGH` by one lens and a real cross-tenant byte
leak `MEDIUM` by another. Severity is not what should decide a human's next
action. `action_tier` is convergence's own call, checked against the repo's
declared contract (`review-profile.yaml`'s `contracts` key — see
`review-profile.md`), never copied from whichever severity a lens attached.

A finding is **BLOCKER** only if at least one holds:

- **Contract break** — violates an invariant the profile's `contracts` list names
  (e.g. "no cross-tenant read/write of another owner's data without
  authorization"). No `contracts` list declared → fall back to the security-diff
  lens's own AuthN/AuthZ, secrets, and injection checklist as the contract.
- **Non-owner read or write** — data belonging to one principal/tenant/firm is
  read or written by another without an authorization check on that access.
- **Unpinned contract rail** — a test, gate, or pin that exists specifically to
  enforce a declared invariant can be bypassed or evaded — the invariant has no
  working enforcement, not just a stylistic gap in one.
- **Fix-adjacent regression of a prior BLOCKER** — `lineage: fix-adjacent` where
  the prior finding it regresses was itself a BLOCKER.

Everything else is **FOLLOW-UP** — including real defects: stale docs, weak
assertions, count mismatches, non-contract lifecycle/concurrency bugs, missing
tests. FOLLOW-UP is not "ignore" — it is "does not justify another full pass
before merge." Route it to PR comments or a tracked ticket.

## Pipeline

1. **Normalize** every lens's output into the contract. Ingest the Step-2
   auto-gate findings and the Step-2 prior review-all report as additional
   sources — never re-run either.
2. **Evidence-check.** Confirm each finding names a real location and a reachable
   path. Where two lenses disagree about the same location, examine before
   keeping.
3. **Retention gate.** Keep a finding ONLY if it has: a precise location; a
   concrete failure/exploit path; a specific consequence; a feasible remediation;
   and sufficient evidence. Everything else goes to NOT RETAINED with a reason —
   dropped, not deleted, so it can be contested.
4. **Dedup by `root_cause_key`.** Collapse findings that share a root cause into
   one; keep the highest justified severity; union their `provenance`.
5. **Lineage-tag** each retained finding against the prior report (above).
6. **Action-tier** each retained finding BLOCKER/FOLLOW-UP against the profile's
   contract (above).
7. **Rank**: BLOCKER before FOLLOW-UP, then severity desc, then confidence, then
   blast radius.
8. **Split noise** into a NOT RETAINED appendix.

## root_cause_key guidance

Name the **defect**, not the symptom or the file: `project-authorization-missing-membership-check`,
not `line-118-bug`. Two lenses describing the same underlying flaw must land on the
same key — that is what makes cross-lens dedup work, and it is also what makes
lineage matching work across passes. When unsure, prefer the security/behavioral
root over the stylistic one.

## Stop / re-invoke recommendation

Advisory only — `/review-all` is report-only in every mode and never gates a merge
itself; this is guidance for the human (or CI) reading the report, not
enforcement.

- **Zero BLOCKERS retained, and no retained FOLLOW-UP identifies an unmet
  acceptance criterion or a violated safety/authority boundary** → recommend
  stopping the review loop on this branch. Remaining FOLLOW-UP items go to PR
  comments or a tracked ticket, not another pass. A FOLLOW-UP that does map to an
  unmet criterion or boundary is a BLOCKER regardless of its severity label.
- **One or more BLOCKERS** → recommend a re-invoke after remediation, scoped to
  `quick` mode by default, targeting the blocker locations and their lineage.
  Escalate to `deep` only when the user asks for it — never self-escalate.
- **Retained `new` findings keep tracing to the same specification or design
  ambiguity** (several lineage keys across passes point at one unclear requirement,
  not at independent code defects) → recommend resolving that ambiguity before
  another review; the problem is upstream of the code. The trigger is the evidence,
  not the number of passes. Advisory only.

State the recommendation as one line at the top of the output, e.g.
`Recommendation: stop — 0 BLOCKERS, 6 FOLLOW-UP.`, `Recommendation: re-invoke
(quick) after fixing 2 BLOCKERS.`, or `Recommendation: resolve the spec
ambiguity first — 3 new findings trace to the unspecified retry contract.`

## Output format

Primary output = one ranked decision list, grouped by `action_tier`. Per retained
finding:

```
[BLOCKER] <SEVERITY> — <one-line claim>
Location: <path:line>
Raised by: <provenance lenses>
Lineage: <new|residual|fix-adjacent|previously-not-retained> <correlation basis if not `new`>
Why retained: <the reachable path / cross-lens agreement that justifies it>
Action: <remediation direction>
```

BLOCKERS first, then FOLLOW-UP, then a NOT RETAINED section (one line + reason
each). In `deep` mode, optionally attach raw per-lens dumps as an appendix — never
as the primary output.

### Example

```
Recommendation: re-invoke (quick) after fixing 2 BLOCKERS.

## BLOCKERS

[BLOCKER] HIGH — Cross-tenant project read is not authorized
Location: src/api/ProjectsController.cs:118-146
Raised by: security-diff, backend-endpoint-reviewer
Lineage: fix-adjacent — this file was touched to close pass-N's F3 (missing tenant
check on the sibling route); the new route repeats the same gap.
Why retained: Both reviews identify the same missing membership check; the
authorization path confirms it is reachable.
Action: Enforce tenant/project membership before data lookup.

## FOLLOW-UP

[FOLLOW-UP] MEDIUM — Revit transaction may remain open on failure
Location: src/Revit/ImportCommand.cs:72-121
Raised by: bim-code-review:standards, revit-lifecycle
Lineage: new
Why retained: Exception handling exits before rollback/disposal.
Action: Use the project-standard transaction wrapper or guarantee rollback.

## NOT RETAINED

NOT RETAINED — "Add more input validation"
Raised by: code-review
Reason: No affected input path, failure mode, or specific missing validation
identified.
```

The blind-lens advantage is preserved during discovery; adjudication — including
lineage and action-tiering — happens only here, after every lens has reported.
