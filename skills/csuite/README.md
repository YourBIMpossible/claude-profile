# BIMpossible Orchestrator v1 Document Pack

This pack is a production-oriented starting point for a modular executive-review and governance system for BIMpossible.

## Contents

- `SKILL.md` — Claude Skill core: operating boundaries, response conventions, routing, gates, and review procedure.
- `references/` — On-demand reviewer instructions for product, engineering, BIM domain, security, evidence, AI evaluation, and commercial functions.
- `routing/` — Source-of-truth routing matrix and policy configuration.
- `examples/` — Worked IDS dashboard review example.
- `docs/` — Runtime design, telemetry, decision memory, and evaluation-suite guidance.

## Use modes

Claude Code harness mode: explicit `/csuite` invocation with in-context short-tier review and independent parallel subagents for full-tier or gated routes; product runtime mode remains parked.

### Claude Skill mode

Use `SKILL.md` as the entry point. Load only the reference files selected by `routing/routing-matrix.yaml`. Use Markdown short-form responses by default. Do not claim independent parallel review inside a single chat context.

### BIMpossible runtime mode

Use the same policies as runtime configuration, but execute required reviewers as independent API calls where independence matters. Validate structured responses, enforce write gates, persist audit records, and collect outcome telemetry.

## Recommended initial reviewers

Always available:

- CPO
- CTO
- BIM Manager
- CISO
- Evidence Analyst
- AI Evaluation

On demand:

- CEO
- CRO
- Customer Success
- CFO

## Non-negotiables

- Never represent a hypothesis as customer, market, model, or product fact.
- Never perform a consequential write without explicit human approval, validation, rollback/recovery, and audit logging.
- Treat invalid BIM/Revit semantics as a blocker unless a named human accepts the risk.
- Stop downstream work when a hard-stop condition is met.
- Store decisions with evidence, dissent, alternatives, approver, expected outcome, and revisit trigger.

## Versioning

Version each reviewer reference separately (for example, `cto@1.0.0`). Include `task_id`, `orchestrator_version`, and reviewer versions in every stored runtime record.
