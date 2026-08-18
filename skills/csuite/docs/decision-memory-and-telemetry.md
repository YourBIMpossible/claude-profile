# Decision Memory and Telemetry Design

Version: 1.0.0

## Purpose

Decision memory stores why BIMpossible chose an action, what evidence and dissent informed it, what was expected, who approved it, and what happened after release. It prevents repeated rediscovery and turns the orchestrator into a learning system rather than a prompt collection.

## Minimum decision record

```yaml
task_id: TASK-2026-001
created_at: 2026-08-14T20:00:00-07:00
orchestrator_version: orchestrator@1.0.0
reviewer_versions:
  cpo: cpo@1.0.0
  bim_manager: bim-manager@1.0.0
  cto: cto@1.0.0

task_class: read_only_model_qa_or_model_interpretation
risk_level: high
decision_owner: product_owner

verdict: build_now
recommended_decision: "Ship read-only IDS findings with Revit source traceability; defer auto-fix."
confidence: medium

evidence_used:
  - E-001
  - E-006
missing_evidence:
  - "Pilot false-positive rate across representative models"
assumptions:
  - "Target customers use supported Revit versions"
dissent: []

alternatives_rejected:
  - alternative: "Enable automatic parameter writes in v1"
    reason: "Write-gate, rollback, and quality evidence are incomplete"

expected_outcome:
  metric: validated_finding_rate
  target: ">= 85%"
  measurement_window: 30_days
  owner: product_owner

revisit_trigger: "Validated finding rate below 85%, or any severe false-positive incident."
required_approvals: []
actual_outcome: null
decision_quality: pending
```

## AI provenance record

Record this for every AI-assisted consequential decision or action:

```yaml
ai_provenance:
  event_id: AIEVT-001
  user_id: user-123
  tenant_id: tenant-456
  project_id: project-789
  model_id: model-123
  source_data_refs: [E-001, E-002]
  retrieval_context_refs: [CTX-009]
  model_provider_and_version: "provider/model-version"
  prompt_or_skill_versions: [orchestrator@1.0.0, ai-evaluation@1.0.0]
  tools_invoked: []
  proposed_action: ""
  approval:
    required: false
    approver: null
    timestamp: null
  actual_action: ""
  result: ""
  audit_hash_or_reference: ""
```

## Post-release outcome record

```yaml
outcome_record:
  task_id: TASK-2026-001
  review_date: 2026-09-14
  expected_metric: validated_finding_rate
  target: ">= 85%"
  actual: ""
  adoption_signal: ""
  support_signal: ""
  security_or_quality_incidents: []
  was_recommendation_followed: true
  decision_quality: correct | partially_correct | incorrect | inconclusive
  what_was_missing: []
  corrective_action: ""
  feedback_route: product | engineering | BIM | security | sales | customer_success | finance
```

## Feedback taxonomy

Use one primary category per finding:

- Product UX or workflow
- Revit/BIM semantics
- Technical reliability or performance
- Data quality or customer standards
- Permissions, security, or privacy
- Training or documentation
- Implementation dependency
- Value communication or commercial expectation
- Economics or usage cost
- AI quality or grounding

## Retention and access

- Scope records to the tenant and project where applicable.
- Do not persist secrets, raw credentials, or unnecessary sensitive model content.
- Apply role-based access to evidence and audit records.
- Retain enough provenance to reconstruct a consequential action and associated approval.
