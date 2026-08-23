# BIMpossible Conflict Policy

Version: 1.0.0

## Purpose

Preserve useful disagreement between reviewers without allowing the orchestrator to manufacture consensus. The final decision must record the unresolved disagreement, named decision owner, and revisit trigger whenever material dissent remains.

## Authority rules

| Domain | Default authority | Override rule |
|---|---|---|
| Product scope, user value, and roadmap | Product owner | Strategic conflict escalates to CEO lens and product owner |
| Revit and BIM model semantics | BIM Manager | Only a named human risk owner may accept semantic risk |
| Architecture, reliability, and rollback feasibility | CTO | Technical owner may accept non-security implementation risk |
| Security, privacy, credentials, tenants, and permissions | CISO | Security owner or designated human approver must accept residual risk |
| AI grounding, model behavior, false positives, and evaluation thresholds | AI Evaluation | Product owner may proceed only with declared thresholds and risk acceptance |
| Commercial proof, ICP, and pilot claims | CRO | Unsupported claims cannot be promoted to marketing facts |
| Economics, pricing viability, and usage cost | CFO | Business owner accepts economic tradeoffs |

## Disagreement protocol

1. State the disagreement precisely. Name the reviewers, claim, decision impact, and evidence cited.
2. Identify divergent assumptions. Convert unsupported assumptions to hypotheses.
3. Resolve factual conflicts with evidence; involve Evidence Analyst if needed.
4. Narrow the proposal if disagreement is about scope, reversibility, or sequencing.
5. Create a reversible experiment if the disputed question is testable.
6. Escalate to the decision owner if the dispute remains material.
7. Record dissent rather than forcing a blended conclusion.

## Non-overridable stop conditions

- A model interpretation deemed semantically invalid by BIM Manager cannot become a product requirement without named human risk acceptance.
- A consequential write cannot proceed without validation, rollback/recovery, audit logging, and explicit human approval.
- A security rejection concerning tenant isolation, permissions, credentials, sensitive data access, or unsafe tool action cannot be bypassed without designated security approval.
- An AI workflow failing its required quality threshold cannot be represented as reliable or enabled for consequential autonomous actions.

## Dissent record

```yaml
dissent:
  reviewer: bim_manager
  position: reject
  disputed_claim: "The rule can safely infer room ownership from linked-model geometry."
  reason: "Linked models and phase conditions make ownership attribution semantically unreliable."
  evidence_refs: [E-014]
  decision_impact: "Blocks customer-visible finding until model-context strategy is defined."
  resolution: "Escalated to product owner; narrowed to host-document elements in v1."
```
