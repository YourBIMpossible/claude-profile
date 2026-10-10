# Proposed /next queue patch - CP-GOVERNANCE-FIRE-DRILL

Not applied. `queue.yaml` is a protected store and no single-item interface exists; apply via `/next sync` by the owner.

```yaml
- id: CP-GOVERNANCE-FIRE-DRILL
  title: "Behavioural regression check for governance rules (boundary compliance + authorized autonomy), synthetic fixtures"
  projects: [claude-profile]
  status: ready            # runner + first run landed on claude/governance-drill; flip to done on owner merge
  owner_gate: false
  effort: S
  risk: 1
  scope_note: >
    Manual, not a production skill/gate. evals/governance-drill/{SPEC.md,drill.py}.
    Rerun after model, instruction, permission or hook changes. Rule fixes are proposals only.
  evidence: {kind: run, ref: "evals/governance-drill/results/ (branch claude/governance-drill)"}
```

Original title was "canned refusal prompts re-run after model changes, results logged"; rescoped per the owner's review (paired controls, two-axis scoring, fixtures, no auto-tightening).
