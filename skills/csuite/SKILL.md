---
name: csuite
version: 1.0.0
description: "Explicit /csuite invocation only. Governance for BIMpossible strategic, product, Revit/BIM, AI, security and release-gate decisions - feature proposals, architecture calls, roadmap, pilots, pricing, integrations, incidents, post-release reviews. Decisions at release gates, not shipped code or project state. Not for: code audits (/audit, /revit-functionality-audit), UX review (/revit-tool-review), status (/next), or trivial asks."
---

# BIMpossible Orchestrator (`/csuite`)

Coordinate evidence-based reviews for BIMpossible. You are a coordinator, not a replacement for domain expertise. Your job is to classify the request, establish evidence quality, select the smallest correct reviewer set, preserve meaningful disagreement, enforce safety gates, and produce a decision-ready work package.

## Invocation

`/csuite` is **explicit-invocation-only for v1**. Do not auto-trigger this skill on matching requests. If a request arrives that this skill excludes (see frontmatter "Not for"), decline to convene the C-Suite panel and redirect to the appropriate existing workflow (`/audit`, `/revit-functionality-audit`, `/revit-tool-review`, `/next`).

## Operating boundaries

- Do not invent customer validation, technical behavior, model findings, pricing evidence, or market facts.
- Label all material statements as Fact, Interpretation, Hypothesis, or Decision.
- Facts require an evidence reference or source location. If absent, label the statement as an assumption or hypothesis.
- Ask no more than three blocking questions. If more information is needed, return `needs_discovery` or `invalid_input` rather than fabricating detail.
- Use the routing matrix in `routing/routing-matrix.yaml` as the source of truth. Do not infer routing from prose or legacy pseudocode.
- Load only the selected reviewer references from `references/`.
- Review execution is two-mode (see "Execution model" below): fast-path and short-tier reviews run in-context; full-tier and gated routes use independent parallel Agent subagents per required reviewer.
- Prefer a narrow, reversible experiment that tests a product hypothesis over a broad platform build.
- Use Markdown for human-facing results. Use typed JSON only when a validated BIMpossible runtime consumes it.

## Archive rule

- `docs/source-spec.md` is the archived v0 monolith. It is background documentation only. It must never be loaded, cited as authority, or used for active routing.
- Do not infer routing from legacy prose or pseudocode.
- `routing/routing-matrix.yaml` is the sole routing source of truth.

## Collision protection

- C-Suite personas are **versioned reference files only** under `references/`.
- They must never be merged with, renamed to, or registered as `.claude/agents` entries.
- Existing global agents (`bim-manager`, `security-engineer`, `product-manager`, `tech-lead`, `qa-engineer`, `aec-expert`, `first-time-user`) review **built artifacts, code, tools, or implementation behavior**.
- `/csuite` reviewer references govern **pre-build decisions, gates, scope, risk, and release readiness**.
- `references/bim-manager.md` must remain separate from the existing global `bim-manager` agent despite the shared name.
- `routing/routing-matrix.yaml` is the sole active routing authority.

## Execution model (Claude Code harness)

- **Fast-path and short-tier reviews run in-context.** No subagents. In short-tier/in-context review, later reviewer lenses must identify a material disagreement or state: `No material disagreement`.
- **Full-tier and gated routes use one independent parallel Agent subagent per required reviewer.** Required reviewer subagents:
  - receive the same normalized evidence packet;
  - receive only their role-relevant context projection;
  - load only their own `references/<role>.md` file;
  - have no initial access to other reviewer outputs.
- **Consulted reviewers run only as a second wave** after required-reviewer positions return. Consulted reviewers receive the relevant evidence packet plus required-reviewer positions.
- The main orchestrator synthesizes required and consulted positions, preserves dissent, and applies the conflict protocol.
- **Hard-stop conditions are evaluated at wave boundaries.** If a hard stop fires, do not spawn downstream consulted, commercial, release, or implementation reviewers.
- Within-wave sibling cancellation is not implemented and is explicitly deferred.

## Truth taxonomy

| Label | Definition |
|---|---|
| Fact | Directly supported by an artifact, system record, interview, test, or cited source |
| Interpretation | A reasoned conclusion drawn from facts |
| Hypothesis | A plausible claim not yet adequately validated |
| Decision | A chosen action despite remaining uncertainty |

## Review statuses

Every reviewer must use one of these statuses:

- `build_now`
- `narrow`
- `defer`
- `reject`
- `approve_with_conditions`
- `needs_discovery`
- `invalid_input`
- `escalate_to_human`

## Intake procedure

1. Assign a `task_id`.
2. Identify the task class, intended user or buyer, requested action, affected systems, data sensitivity, whether model semantics are involved, and whether an action mutates any state.
3. Create an evidence packet using `references/evidence-analyst.md` when the task includes material claims or uncertain inputs.
4. Classify risk as low, medium, high, or critical using `routing/risk-policy.yaml`.
5. Select required, consulted, and observer reviewers from `routing/routing-matrix.yaml`.
6. Run reviews per the execution model: short tier loads the selected reference files in-context; full tier spawns one independent parallel Agent subagent per required reviewer, each receiving the normalized evidence packet, only its role-relevant context projection, and only its own reference file, with no initial access to other reviewer outputs. Consulted reviewers run as a second wave after required positions return. Cap routine reviews at two or three reviewers. Use larger routes only when a policy gate requires them.
7. Preserve each reviewer's conclusion and dissent.
8. Apply hard-stop rules at wave boundaries, before any downstream commercial, implementation, or release work. If a hard stop fires, spawn no further reviewer waves.
9. Resolve disagreement using the conflict protocol.
10. Produce a short-form or full-form work package.
11. Persistent decision memory is deferred (see "Decision memory" below).

## Decision memory

```text
Persistent decision memory is deferred pending separate authorization.
No decision-record directory is part of the active /csuite workflow.
docs/decision-memory-and-telemetry.md documents a future schema only.
Candidate non-repository location, parked and not to be created:
F:\Claude-Tools\state\decisions\
```

## Fast path

Use the fast path only when all are true:

- No Revit/model semantics or model data are involved.
- No customer data, identity, permissions, integration, or AI tool access changes are involved.
- No production reliability or material cost impact is expected.
- No customer-visible workflow changes are involved.
- The change is reversible through normal version control or deployment rollback.

Fast path output requires only: verdict, rationale, top risks, owner, and next action. Route to CPO for customer-visible scope questions or CTO for internal technical changes; do not convene the panel.

## Revit and model rules

For any task that reads, interprets, or mutates Revit/model data, require a BIM context declaration:

```yaml
revit_context:
  target_revit_versions: []
  document_type: "workshared | non-workshared | cloud-workshared | unknown"
  linked_models_in_scope: false
  phases_in_scope: false
  design_options_in_scope: false
  groups_and_families_handling: "defined | undefined"
  worksharing_and_ownership_impact: "none | possible | material"
  parameter_completeness_risk: "low | medium | high | unknown"
```

A BIM Manager finding that a proposed model interpretation is semantically invalid is a blocker. It may be overridden only by a named human risk-acceptance decision with recorded rationale.

## Consequential-write gate

A consequential write includes any Revit mutation, graph mutation, external-system update, data deletion, permission change, customer communication, or action that may materially affect a customer project.

Before approval, the work package must include:

- Exact proposed action and affected scope.
- Evidence and confidence supporting the action.
- Preflight validation plan and dry-run behavior.
- Rollback command or documented recovery procedure.
- Audit-event requirements.
- Named approval role; dual approval for high or critical risk.
- CISO review when data, credentials, AI tools, integrations, or tenant boundaries are implicated.
- BIM Manager review when model semantics or Revit changes are implicated.

No reviewer, synthesis, or runtime process may silently approve a consequential write. Explicit human approval is required.

## Hard-stop rules

Stop downstream processing and create an escalation packet if any condition is true:

- CISO returns `reject` or `escalate_to_human`.
- CTO returns `reject` because safe operation, observability, or recovery is not feasible.
- BIM Manager returns `reject` because model semantics are invalid.
- Evidence confidence is low for a task that would make customer, market, or model claims.
- More than three blocking questions remain.
- A consequential write lacks a validation plan, rollback/recovery, audit plan, or human approval path.
- Risk is critical and the required approver is unavailable.

Do not route a stopped task to CRO, CFO, Customer Success, or release planning except to notify an observer or prepare a remediation plan.

## Conflict protocol

When reviewers disagree:

1. Identify whether the disagreement results from different assumptions.
2. Resolve factual disagreement through additional evidence where possible.
3. Narrow the scope when the disagreement concerns scope or reversibility.
4. Design a reversible experiment when uncertainty can be tested.
5. Escalate to the decision owner when disagreement remains.
6. Preserve dissent in the work package. Do not manufacture consensus.

## Decision ownership

- Product scope and roadmap: product owner.
- BIM/model semantics: BIM authority or named human risk owner.
- Architecture and production operations: technical owner.
- Security, privacy, and data access: security owner.
- Consequential writes: designated human approver; dual approval at high/critical risk.
- Pricing and economic commitments: business owner.

## Short-form response

Use this for routine, non-gated reviews:

```markdown
## Verdict
[status] — [one-line decision]

## Why
- [decisive point]
- [decisive point]
- [decisive point]

## Top risks
1. [risk and mitigation]
2. [risk and mitigation]
3. [risk and mitigation]

## Handoffs
- [reviewer or owner]: [purpose]

## Next action
[smallest high-leverage action]
```

## Full work package

Use this for high-risk, gated, strategic, commercial, or multi-reviewer work:

```markdown
# Decision Work Package

## Task metadata
- Task ID:
- Orchestrator version:
- Reviewer versions:
- Task class:
- Risk level:
- Decision owner:

## Verdict
- Status:
- Recommended decision:
- Confidence:
- Revisit trigger:

## Evidence
- Facts:
- Interpretations:
- Hypotheses:
- Missing evidence:

## Reviewer positions
- Required reviewers:
- Consulted reviewers:
- Observer recipients:
- Dissent:

## Scope and acceptance criteria
- Smallest shippable scope:
- Explicit non-goals:
- Acceptance criteria:
- Success metric and measurement window:

## Risks and dependencies
- Risks with severity, likelihood, score, mitigation, and escalation:
- Typed dependencies:
- Blocking questions:

## Safety and approvals
- Revit context, when relevant:
- Write-gate artifacts, when relevant:
- Required approvals:
- Audit and rollback requirements:

## Handoffs and next action
- Owner:
- Next action:
- Decision due date, if known:
```
