---
name: review-all
description: "Run every applicable code-review lens at once on a diff/branch/PR - correctness, diff-scoped security, and repo-declared lenses - as blind parallel subagents, then converge to ONE deduplicated ranked verdict. Read-only by default; --fix is explicit and gated. Trigger phrases: \"review-all\", \"review everything\", \"run all reviews\"."
---

# /review-all — unified multi-lens review orchestrator

One command that fans a diff out across every applicable review lens in parallel,
each blind to the others, then adjudicates their findings into a single ranked
decision list. Generalizes the proven `/bim-code-review` pattern (blind parallel
subagents, read-only, complements the mandatory per-file gate agents) across a
wider lens surface, and adds the adjudication layer that a focused two-axis skill
deliberately omits.

## Invocation

```
/review-all [quick|standard|deep] [--range <git-range>]
/review-all [standard|deep] --fix --ci
```

- **Mode** (default `standard`): `quick` = fast high-signal subset (code-review +
  security-diff only); `standard` = all enabled lenses, single pass; `deep` = all
  lenses + a gap sweep + deeper enclosing-context reads, raw per-lens dumps
  attached as an appendix.
- `--range <git-range>` overrides diff resolution.
- `--fix` opts into the remediation loop (gated — see Hard rules). `--ci` runs the
  repo's declared local verifier during that loop. Both are inert without `--fix`.
- **Default posture is read-only, report-only.** No mode ever commits or pushes.

## Step 0 — Resolve the diff once

Resolve one fixed point and reuse it for every lens. `git diff @{upstream}...HEAD`,
else `git diff main...HEAD`, else `git diff HEAD~1`; if there are uncommitted
changes or the range is empty, also include `git diff HEAD` (working tree). Honor
`--range` or a PR/branch/path argument if given. Empty diff → say so and stop.
Compute the changed-file list; hand the **same** diff to every lens.

## Step 1 — Load the repo profile / select lenses

Read `<repo-root>/.claude/review-profile.yaml`. If present it is authoritative for
lens enable/disable, per-lens modes, `allow_autofix`, `read_only_sandbox`,
`local_verification`, `contracts`, and `prior_reports_glob`. If absent, fall back
to the heuristics in `reference/review-profile.md`. Active lens set = enabled ∧
mode-includes-lens ∧ (for conditional lenses) `paths` or `content_patterns`
triggers matched against the diff. A conditional lens the diff's *content* clearly
implicates but whose triggers don't match may still be enabled as an orchestrator
judgment call — disclose it in the output as an orchestrator override, naming the
signal (`reference/review-profile.md` § `enabled_when`).

## Step 2 — Ingest existing findings (do NOT re-run their source)

Two ingest-only sources, neither ever re-invoked:

- **Auto-gate findings.** If the repo runs mandatory per-file gate agents (e.g.
  BIMpossible's `backend-endpoint-reviewer` / `migration-reviewer` /
  `typescript-api-reviewer` per CLAUDE.md's agent-gates table), collect the
  findings they have **already** produced this session/PR and feed them into
  convergence as another provenance source. The point is to dedup against them —
  never to re-invoke and duplicate their cost.
- **The prior `/review-all` report.** Locate the most recent report for this
  branch via the profile's `prior_reports_glob`, or the fallback search in
  `reference/review-profile.md`, and parse its retained + NOT RETAINED findings.
  Hand this to convergence as the lineage source (`reference/convergence.md` §
  Lineage tagging) — it tags each new finding `new` / `residual` / `fix-adjacent` /
  `previously-not-retained` against it. No report found → note that explicitly;
  every finding lineage-tags as `new`.

## Step 3 — Blind parallel fan-out

Spawn one `Agent` per active lens, **in parallel, in a single message**, each
blind to the others (no shared scratch, no cross-talk). Give each only: the
resolved diff, its lens contract, and the standards sources it needs. Prefer the
specialized agent type where the environment provides one (`security-engineer`
for security-diff, `qa-engineer` for concurrency), else a general-purpose agent
carrying the lens prompt. Every lens returns the finding contract in
`reference/convergence.md`. Lens catalog + prompts: `reference/lenses.md`.

## Step 4 — Convergence (adjudication)

The layer `/bim-code-review` deliberately omits. Normalize → evidence-check →
apply the retention gate → dedup by `root_cause_key` (union provenance, keep
highest justified severity) → lineage-tag against the Step 2 prior report →
action-tier each finding BLOCKER/FOLLOW-UP against the profile's `contracts` →
rank → split non-actionable noise into NOT RETAINED. Convergence MUST include the
Step 2 auto-gate findings, so one defect surfaced by four sources becomes one
finding with four provenances instead of four noisy variants. Ends with a one-line
stop/re-invoke recommendation — advisory only, never a merge gate. Full algorithm,
retention gate, lineage tagging, action tiering, and output format:
`reference/convergence.md`.

## Step 5 — Optional fix loop (`--fix`)

Only when `--fix` AND `allow_autofix: true` AND no read-only sandbox rule applies
(see Hard rules). Apply only retained, actionable, high-confidence fixes → run the
declared `local_verification` when `--ci` is set → re-review changed lines and
dependent context → iterate until clean, blocked, or `max_autofix_iterations`.
Never commit or push. Do not use remote CI as a debug loop — local verification
only (this is the surviving rule from the retired `converge-review`).

## Output

Primary output is the **one converged ranked decision list**, opening with a
one-line stop/re-invoke recommendation and grouped BLOCKER-first then FOLLOW-UP
(`reference/convergence.md` § Action tiering). Report-only by default. Per finding
show severity, location, contributing lenses (provenance), lineage against the
prior report, why it was retained, and the remediation direction. Follow with a
NOT RETAINED section for transparency. Do not emit raw per-lens dumps as the
primary output — they are the discovery layer; attach them only as a deep-mode
appendix.

## Hard rules

- **Read-only by default. Never `git commit` / `git push` in any mode.**
- `--fix` is **refused** when the active repo declares a read-only sandbox rule
  (profile `read_only_sandbox: true`, or a repo delivery contract such as
  BIMpossible's `docs/engineering/DELIVERY.md` § Sandbox-originated Git actions)
  or `allow_autofix: false`. In BIMpossible, remediation is delegated to the human
  push flow, not performed in the sandbox.
- **security-diff is a first-class internal lens**, not a dependency on the
  (missing) `/security-review` command and not `security-review-full`. It returns
  findings only — never writes reports, checklists, or build logs.
- The **bim-code-review lens runs only when the profile/heuristics declare BIM
  applicability.** Outside BIM repos it is inert (e.g. Evidence Compiler, whose
  charter forbids BIM/Revit policy in core).
- **Blind fan-out:** no lens sees another's output before convergence.
- **Retention gate:** a finding survives only with a precise location, a concrete
  failure/exploit path, a specific consequence, a feasible remediation, and
  sufficient evidence. Generic advice ("add validation", "use a safer library",
  bare OWASP items) is dropped to NOT RETAINED.
- **BLOCKER/FOLLOW-UP tiering and the stop/re-invoke recommendation are advisory
  only.** They guide the human's next action; the skill never merges, never
  blocks a merge itself, and never re-invokes itself automatically on the strength
  of its own recommendation.

## Relationship to existing commands

- `/code-review` — invoked as the generic correctness/maintainability lens.
- `/bim-code-review` — its blind Standards+Spec structure is the precedent; the
  BIM lens reuses it where applicable.
- `security-review-full` — left separate: periodic, stateful, full-app scan that
  writes reports/checklists. Not bundled into a per-session diff review.
- `converge-review` — already retired (2026-08-18) to a one-line local-verify
  rule; its surviving principle lives in Step 5. Do not resurrect the old loop. If
  a BIMpossible-flavored one-shot is ever wanted, expose it as a thin wrapper over
  `/review-all deep --fix --ci` with the BIMpossible profile — not built here.
- `/security-review` — optional future standalone facade over the security-diff
  lens; build only once its scope and contract are documented independently.
