# Worked Example: Read-Only IDS Dashboard Review

This is an illustrative completed review for a proposed BIMpossible feature: a web dashboard that displays IDS-based findings from a connected Revit model and lets a BIM manager inspect evidence before acting.

## Intake

- **Task ID:** TASK-IDS-001
- **Task class:** `read_only_model_qa_or_model_interpretation`
- **Requested outcome:** Let a BIM manager run an IDS ruleset against a connected Revit model, see findings in the web dashboard, and navigate each finding to clear source evidence.
- **Proposed v1 constraint:** Read-only findings only. No Revit parameter changes, automated fixes, or external writes.
- **Risk level:** High, because incorrect model findings can cause harmful project decisions even without a write.

## Evidence packet

### Facts

- **E-001:** BIMpossible has an IDS-based model QA capability available behind a feature flag. Source: internal product artifact.
- **E-002:** The requested workflow uses connected Revit model data and presents findings to a BIM manager. Source: feature brief.

### Interpretations

- **I-001:** A read-only flow has lower operational risk than an auto-fix flow because it does not mutate the model.

### Hypotheses

- **H-001:** BIM managers will consider the dashboard useful if each finding can be traced to the relevant model source and rule.
- **H-002:** The workflow can reduce manual QA triage time.

### Missing evidence

- Representative project models spanning linked-model, workshared, phase, option, group, and incomplete-parameter conditions.
- Measured validated-finding rate and time-to-triage baseline.

## Route

| Reviewer type | Reviewers | Reason |
|---|---|---|
| Required | BIM Manager, CTO, AI Evaluation | Model semantics, technical data path, and generated/explained finding quality |
| Consulted | CPO | Defines user workflow, V1 scope, activation, and measurement |
| Observer | CISO | Receives outcome because connected model data is involved; becomes required if access or retention changes |
| Decision owner | Product owner | Decides whether to ship the bounded read-only experiment |

## Reviewer positions

### BIM Manager — approve with conditions

- **Semantic condition:** Each finding must expose the IDS rule, source document, element identifier, element category, parameter/property evaluated, and model context needed to inspect it.
- **Revit scope condition:** Declare target Revit versions and explicitly define behavior for linked models, phases, design options, groups, families, and missing parameters.
- **UAT requirement:** Test at least three representative conditions: a workshared host model with linked models; a phased model with design options; a model with groups/families and incomplete parameters.
- **Dissent:** No material disagreement.

### CTO — approve with conditions

- **Technical scope:** Build a read-only findings pipeline with stable rule/run identifiers, source references, pagination, authorization checks, and clear stale-data state.
- **Failure behavior:** A failed rule run must be distinguishable from a clean run with zero findings. Avoid presenting incomplete results as complete.
- **Observability:** Log run ID, ruleset version, model version/context, counts, failures, and user-visible data freshness.
- **Mutation:** None. No rollback command required for v1.
- **Dissent:** No material disagreement.

### AI Evaluation — narrow

- **Quality condition:** Do not let an LLM independently create compliance findings in v1. It may summarize or explain deterministic IDS findings only when it cites the rule and source evidence.
- **Evaluation:** Measure evidence-link availability, explanation faithfulness, user correction rate, and false-positive/false-negative rate for any AI-added classification.
- **Dissent:** The feature should be deterministic-first, not agentic-first.

### CPO — build now, narrow

- **Primary user:** BIM Manager.
- **Job:** Verify model compliance against a defined standard and triage actionable exceptions without manually hunting through the model.
- **V1 workflow:** Select model/ruleset → run or view most recent QA → filter findings → inspect rule and source evidence → export/share a read-only result.
- **Non-goals:** Automatic correction, generalized “talk to the model,” cross-project benchmarking, and speculative compliance claims.
- **Activation event:** A BIM manager completes a run and validates at least one finding in Revit.
- **Success metric:** At least 85% of sampled findings are confirmed as valid by the BIM manager during pilot UAT.

## Synthesis

## Verdict
Build now — ship a bounded, read-only IDS findings dashboard with source traceability; defer any write-back or autonomous remediation.

## Why
- It creates a concrete BIM-manager workflow from existing QA capability.
- Read-only scope provides a lower-risk path to validate trust and usefulness.
- Source traceability, version boundaries, and representative UAT protect against misleading model findings.

## Top risks
1. Linked-model or phase context can make a finding appear to apply to the wrong object; mitigate with explicit document/context evidence and conditional support boundaries.
2. Incomplete or failed rulesets may look like clean results; mitigate with run status, completeness indicators, and failure logging.
3. AI explanations could overstate deterministic rule output; mitigate with evidence-cited explanations and deterministic-first v1 policy.

## Acceptance criteria

- Every displayed finding links to rule ID/version, source document, element identifier, evaluated property, and run status.
- Supported Revit versions and model-condition boundaries are documented.
- A failed or partial run is visibly distinct from zero findings.
- UAT covers the three specified representative project conditions.
- Pilot sampling shows at least 85% validated-finding rate, or the feature returns to review.
- No feature path modifies Revit, the graph, or an external system in v1.

## Handoffs

- **CTO:** Define findings/run schema, authorization, data freshness, and observability.
- **BIM Manager:** Produce test models and UAT expected results.
- **AI Evaluation:** Define explanation-grounding test cases and pilot quality measurement.
- **CPO:** Define activation instrumentation and pilot interview protocol.

## Next action

Write a one-page implementation brief for the read-only flow and assemble three representative UAT models before adding dashboard polish or AI conversational features.

## Outcome record template

```yaml
task_id: TASK-IDS-001
expected_metric: validated_finding_rate
target: ">= 85%"
measurement_window: 30_days
revisit_trigger: "Validated-finding rate below 85%, severe false positive, or unresolved source-traceability defect."
```
