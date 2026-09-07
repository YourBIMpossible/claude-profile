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

## Pipeline

1. **Normalize** every lens's output into the contract. Ingest the Step-2
   auto-gate agent findings as just another set, each with its own provenance —
   never re-run them.
2. **Evidence-check.** Confirm each finding names a real location and a reachable
   path. Where two lenses disagree about the same location, examine before keeping.
3. **Retention gate.** Keep a finding ONLY if it has: a precise location; a
   concrete failure/exploit path; a specific consequence; a feasible remediation;
   and sufficient evidence. Everything else goes to NOT RETAINED with a reason —
   dropped, not deleted, so it can be contested.
4. **Dedup by `root_cause_key`.** Collapse findings that share a root cause into
   one; keep the highest justified severity; union their `provenance`.
5. **Rank** retained findings: severity desc, then confidence, then blast radius.
6. **Split noise** into a NOT RETAINED appendix.

## root_cause_key guidance

Name the **defect**, not the symptom or the file: `project-authorization-missing-membership-check`,
not `line-118-bug`. Two lenses describing the same underlying flaw must land on the
same key — that is what makes cross-lens dedup work. When unsure, prefer the
security/behavioral root over the stylistic one.

## Output format

Primary output = one ranked decision list. Per retained finding:

```
<SEVERITY> — <one-line claim>
Location: <path:line>
Raised by: <provenance lenses>
Why retained: <the reachable path / cross-lens agreement that justifies it>
Action: <remediation direction>
```

Then a NOT RETAINED section (one line + reason each). In `deep` mode, optionally
attach raw per-lens dumps as an appendix — never as the primary output.

### Example

```
HIGH — Cross-tenant project read is not authorized
Location: src/api/ProjectsController.cs:118-146
Raised by: security-diff, backend-endpoint-reviewer
Why retained: Both reviews identify the same missing membership check; the
authorization path confirms it is reachable.
Action: Enforce tenant/project membership before data lookup.

MEDIUM — Revit transaction may remain open on failure
Location: src/Revit/ImportCommand.cs:72-121
Raised by: bim-code-review:standards, revit-lifecycle
Why retained: Exception handling exits before rollback/disposal.
Action: Use the project-standard transaction wrapper or guarantee rollback.

NOT RETAINED — "Add more input validation"
Raised by: code-review
Reason: No affected input path, failure mode, or specific missing validation
identified.
```

The blind-lens advantage is preserved during discovery; adjudication happens only
here, after every lens has reported.
