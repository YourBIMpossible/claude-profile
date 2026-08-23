# Orchestrator Runtime Architecture

Version: 1.0.0

## Scope

This document describes the application/runtime implementation, not the Claude Skill implementation. A Skill can guide a single contextual review; it cannot provide independent agent calls, schema enforcement, durable storage, approval controls, or reliable telemetry by itself.

## Processing pipeline

```text
Intake
  -> Evidence normalization
  -> Task classification and risk scoring
  -> Routing selection
  -> Independent reviewer calls where needed
  -> Schema validation and repair/retry
  -> Hard-stop evaluation
  -> Conflict protocol
  -> Decision work package
  -> Human approval gate, when required
  -> Execution or release
  -> Outcome telemetry and retrospective
```

## Components

| Component | Responsibility |
|---|---|
| Intake service | Normalizes task metadata, user intent, affected systems, and requested action |
| Evidence service | Creates traceable evidence packets and freshness/quality scores |
| Policy engine | Applies routing matrix, risk policy, gates, and stop rules |
| Reviewer executor | Calls role-specific prompts/tools with least-privilege context |
| Schema validator | Rejects or repairs malformed reviewer output before routing continues |
| Conflict resolver | Tracks assumptions, evidence conflicts, dissent, and escalation |
| Approval service | Collects named human approvals and enforces dual approval when required |
| Action executor | Runs approved, scoped actions with preflight, idempotency, and rollback hooks |
| Audit store | Stores immutable decision, provenance, approval, and action records |
| Outcome service | Captures expected versus actual metrics and routes learning feedback |

## Independent review

For high-risk or controversial tasks, call required reviewers independently with:

- The same normalized evidence packet.
- A role-specific minimal context projection.
- No access to other reviewer outputs until initial positions are complete.
- A per-reviewer token and time budget.
- A reviewer-specific schema contract.

After initial positions, optionally run a bounded rebuttal phase that exposes only the disputed claim, relevant evidence, and opponent’s stated reasoning. Do not allow an unbounded debate loop.

## Context-pruning policy

The common evidence packet must be normalized, but each reviewer receives only data relevant to its function.

| Reviewer | Include | Exclude unless required |
|---|---|---|
| CPO | User workflow, evidence quality, adoption data, constraints | Low-level logs, secrets, raw infrastructure traces |
| CTO | Architecture, interfaces, failure data, constraints, costs | Unnecessary sales narrative or unrelated customer anecdotes |
| BIM Manager | Model/Revit context, source traceability, rules, project conditions | Credentials, unrelated commercial assumptions |
| CISO | Data flows, auth, tool permissions, retention, threat model | Raw model content unless needed to assess exposure |
| CFO | Usage forecasts, unit costs, pricing assumptions, implementation effort | Detailed source code or model geometry |
| CRO/CS | ICP, buyer/user map, proof, adoption, onboarding | Sensitive technical traces and unnecessary model details |

## Output schema principles

- Use typed enums for verdict, confidence, truth class, risk severity, risk likelihood, dependency type, and reviewer type.
- Store structured data in the runtime; render a readable Markdown work package for humans.
- Require `task_id`, `orchestrator_version`, reviewer version, evidence references, decision owner, and revisit trigger on every non-fast-path run.
- Support `invalid_input` and `escalate_to_human` as first-class outcomes.

## Write execution policy

Before executing any consequential write:

1. Validate request scope and invoking user authorization.
2. Create a dry-run/preflight result.
3. Verify data state and concurrency/worksharing assumptions.
4. Confirm exact action, affected objects, and rollback/recovery procedure.
5. Obtain recorded human approval; obtain dual approval for critical risk.
6. Execute idempotently where feasible.
7. Record pre-state, post-state, actor, tools, policy versions, approval, and result.
8. Run post-action verification and surface recovery action if verification fails.

## Failure behavior

- If a required reviewer output is malformed, retry once with a structured repair instruction; if still invalid, return `escalate_to_human`.
- If a hard-stop condition is met, terminate subsequent reviewer calls except necessary incident/remediation calls.
- If approval is denied, do not mutate state; record the denial and route to the task owner.
- If execution partially succeeds, stop retries, preserve evidence, surface recovery, and create an incident or remediation task.
