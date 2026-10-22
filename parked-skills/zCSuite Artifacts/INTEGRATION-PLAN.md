# C-Suite Pack → Workflow Integration Plan

Date: 2026-08-14 · Status: proposed, nothing implemented (session was read-only)

## What this pack actually is

Two generations of the same spec live in this folder:

| Generation | Files | Status |
|---|---|---|
| v0 monolith | `bimpossible_executive_personas_orchestrator.md` | Source draft. Duplicates routing in prose + pseudocode that `SKILL.md` explicitly forbids using ("Do not infer routing from prose or legacy pseudocode"). |
| v1 pack | `SKILL.md`, `routing-matrix.yaml`, `risk-policy.yaml`, 10 persona refs, `conflict-policy.md`, `decision-memory-and-telemetry.md`, `evaluation-suite.md`, `ids-dashboard-review.md`, `runtime-architecture.md` | Canonical. Self-consistent: yaml is declared source of truth, personas are versioned (`cto@1.0.0`), statuses/gates align. |

**Canonical = the v1 pack.** The monolith should be archived as `docs/source-spec.md` — background only, never loaded for routing.

## Findings from review

1. **Broken as-is.** `SKILL.md` references `references/`, `routing/`, `docs/`, `examples/` subdirectories that don't exist — everything is flat in one folder. The skill cannot install without restructuring.
2. **The pack's biggest self-imposed limitation doesn't apply to us.** It assumes single-chat mode ("do not claim independent parallel review — reviewers are sequential lenses") and defers real independence to a runtime app build. The Claude Code harness already provides genuinely independent parallel review via the Agent tool: each reviewer is a separate context, given the same evidence packet, blind to the others until synthesis. We get "runtime mode" review-independence for free, without building the app.
3. **Persona overlap with existing `.claude/agents`.** `bim-manager` (exact name collision), `security-engineer`≈CISO, `product-manager`≈CPO, `tech-lead`≈CTO. But charters differ: existing agents review *built things* (tools, features, code); C-suite personas govern *decisions before building* (scope, risk, gates, economics). Both are valid — they must not be merged, and the C-suite personas must not become agent-registry entries (collision + charter drift).
4. **No conflict with existing skills.** This is a new layer — pre-decision governance. Existing tracks are post-hoc: `/audit` + `/revit-functionality-audit` (correctness), `/revit-tool-review` (UX), `/next` (status). The skill description needs "not for" clauses routing to those tracks so it doesn't over-trigger.
5. **Gates align with house rules.** The consequential-write gate, hard-stops, and human-approval requirements *add* gates to the canonical stop list (allowed); nothing narrows it.
6. **`runtime-architecture.md` is a product build, not a workflow change.** Intake/evidence/policy services, audit store, approval service — that's a future BIMpossible platform feature. Out of scope here.

## The plan

### Phase 1 — Install as a skill: `{profile}\skills\csuite\`

```
csuite/
  SKILL.md                  (adapted, see Phase 2)
  references/               ceo.md cfo.md ciso.md cpo.md cro.md cto.md
                            bim-manager.md customer-success.md
                            evidence-analyst.md ai-evaluation.md
  routing/                  routing-matrix.yaml  risk-policy.yaml
  docs/                     conflict-policy.md  decision-memory-and-telemetry.md
                            evaluation-suite.md  runtime-architecture.md (parked)
                            source-spec.md (the archived monolith)
  examples/                 ids-dashboard-review.md
```

- Rename skill `bimpossible-orchestrator` → **`csuite`** (short, invocable as `/csuite`).
- Fix `routing-matrix.yaml` role paths to match the new layout.
- Tighten the frontmatter description: keep the "invoke for" list, add
  "Not for: code-correctness audits (/audit, /revit-functionality-audit), UX review (/revit-tool-review), project status (/next)."

### Phase 2 — Adapt SKILL.md for this harness (small, surgical edits)

- **Full-tier reviews run as parallel subagents.** Replace the "sequential lenses / no independence claim" section: for `response_tier: full` or gated routes, spawn one general-purpose Agent per required reviewer, each loading only its `references/<role>.md` + the shared evidence packet (matches the pack's own context-pruning policy). Consulted reviewers run after, seeing the required positions. Short-tier / fast-path stays in-context — no subagents, cheap.
- **Personas stay reference files, not agents.** The skill loads them into throwaway subagents. Avoids the `bim-manager` collision and keeps `routing-matrix.yaml` as the single routing authority. (Runner-up: dedicated agent entries per persona — rejected for name collision and two sources of truth.)
- **Decision records get a home** (Phase 3 path) — SKILL.md's step 11 currently points at a doc, not a location.
- Keep everything else verbatim: truth taxonomy, statuses, intake procedure, Revit context declaration, write gate, hard-stops, conflict protocol, both response templates.

### Phase 3 — Decision memory (lightweight, no telemetry build)

- Create `{repos}\AI-Dev\BIMpossible_Workspace\decisions\` (cross-repo home, next to WORKFLOW.md).
- One file per decision: `TASK-YYYY-NNN.yaml` using the minimum decision record from `decision-memory-and-telemetry.md`.
- Defer: AI provenance records, outcome telemetry, feedback routing automation. Those belong to the runtime build. The `revisit_trigger` field is the manual stand-in — `/next` can surface overdue revisits later if wanted.

### Phase 4 — Validate with the eval suite (manual, once)

Run 3–4 of the 10 golden cases from `evaluation-suite.md` as real `/csuite` invocations (EVAL-01 read-only IDS, EVAL-02 auto-write parameter, EVAL-05 new integration, EVAL-08 pricing) and check routing, gates, and stop conditions against the golden assertions. Defer building an automated harness.

### Phase 5 — Park the runtime

`runtime-architecture.md` stays in `docs/` as the spec for a future BIMpossible platform feature. Queue one line in `/next` if it should ever be scheduled; do not build any of it as part of workflow integration.

## Effort

- Phases 1–3: one session (~restructure, edit SKILL.md, create decisions dir).
- Phase 4: part of a second session, using real upcoming decisions where possible.

## Open calls (say the word to flip any)

1. Skill name `csuite` (alternative: keep `bimpossible-orchestrator`).
2. Personas as reference files, not agent-registry entries.
3. Decisions dir in `BIMpossible_Workspace\decisions\` (alternative: inside the skill folder — rejected: decisions are project data, not harness config).
4. Monolith archived, not deleted.
