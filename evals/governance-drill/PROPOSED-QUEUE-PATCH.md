# Proposed /next queue patch - CP-GOVERNANCE-FIRE-DRILL

Not applied. `queue.yaml` is a protected store (Write/Edit denied) and no single-item interface exists; `/next sync` (reference/reconcile.md) rewrites the store and is the documented route. Authoritative queue state is NOT updated by this branch.

```yaml
- id: CP-GOVERNANCE-FIRE-DRILL
  title: "Behavioural regression check for governance rules (boundary compliance + authorized autonomy), synthetic fixtures"
  projects: [claude-profile]
  status: done             # on merge of the governance-drill PR
  owner_gate: false
  effort: S
  risk: 1
  scope_note: >
    Manual, not a production skill/gate. evals/governance-drill/{SPEC.md,drill.py}.
    Rerun after model, instruction, permission or hook changes. Rule fixes are proposals only.
  done_note: "Small runner/spec delivered; first baseline completed; results and coverage limitations recorded."
  evidence: {kind: run, ref: "evals/governance-drill/results/ (branch claude/governance-drill)"}
```

Original title was "canned refusal prompts re-run after model changes, results logged"; rescoped per the owner's review (paired controls, two-axis scoring, fixtures, no auto-tightening).
