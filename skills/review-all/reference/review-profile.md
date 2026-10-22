# Repo profile: `.claude/review-profile.yaml`

Lens selection and local verification live in a versioned **per-repo** file, not
hardcoded in the global skill. The orchestrator reads
`<repo-root>/.claude/review-profile.yaml`. If present it is authoritative; if
absent, fall back to the heuristics below. Declaring intent explicitly — especially
disabling BIM lenses in non-BIM repos — is strongly preferred over heuristics.

## Schema (version 1)

```yaml
version: 1
review:
  default_mode: standard          # quick | standard | deep
  read_only_default: true
  allow_autofix: false            # --fix is refused unless true
  read_only_sandbox: false        # true => --fix refused regardless of allow_autofix
  local_verification:             # run by --ci during the fix loop; never remote CI as a debug loop
    - <command>
  prior_reports_glob: <glob>      # optional: where Step 2 finds the last /review-all report for
                                   # lineage tagging (convergence.md § Lineage tagging). Absent →
                                   # fallback search below.
  contracts:                      # optional: invariants that define a BLOCKER (convergence.md
                                   # § Action tiering). Plain language, not lens severities.
    - <invariant>
  lenses:
    code-review:      { enabled: true }
    bim-code-review:  { enabled: true, modes: [standard, deep] }
    security-diff:    { enabled: true, modes: [standard, deep] }
    revit-lifecycle:
      enabled_when:
        paths: ["**/*.cs", "**/*Revit*", "**/*ExternalEvent*"]
    concurrency:
      enabled_when:
        paths: ["**/*async*", "**/*queue*", "**/*worker*", "**/*signalr*"]
        content_patterns: ["lock", "savepoint", "FOR UPDATE", "ContextVar", "request_scope"]
auto_gates:
  # Include findings already emitted by repository-mandated per-file gate agents.
  ingest_if_available: true
  # Never invoke gate agents again merely because /review-all is running.
  # Their lifecycle remains controlled by the repository's normal agent-gate rules.
  reinvoke: false
  agents: []                      # optional: names, for provenance/dedup only
convergence:
  include_agent_gates: true       # ingest per-file gate agent findings into dedup
  require_location: true
  require_failure_path: true
  min_confidence: medium
  max_autofix_iterations: 3
```

### `auto_gates` behavior

- `ingest_if_available: true` permits convergence to consume gate findings that
  the repository's per-file agents have **already** emitted this session/PR.
- `reinvoke: false` prevents duplicated reviews, token waste, and repeated
  reporting — `/review-all` never launches those agents just because it is
  running; their lifecycle stays owned by the repo's normal agent-gate rules.
- If no machine-readable prior findings exist, `/review-all` records that gate
  evidence was **unavailable**; it does not pretend those agents were run.
- `agents` is an optional list of gate-agent names, used only for provenance
  labelling and cross-lens dedup. Repos with no mandated gate agents leave it
  empty (`agents: []`).

### `prior_reports_glob` — lineage source

Points Step 2 at the repo's own `/review-all` report history so convergence can
lineage-tag findings (`new` / `residual` / `fix-adjacent` / `previously-not-retained`
— convergence.md). Absent → search common locations (`docs/audits/`,
`docs/reviews/`, `reviews/`) for the most recently dated file whose name or
frontmatter identifies it as a `/review-all` report. Finding none is a valid state,
not an error — proceed and say so explicitly; every finding lineage-tags as `new`.

### `contracts` — what counts as a BLOCKER

The list convergence checks a finding against before calling it BLOCKER (see
convergence.md § Action tiering). Write invariants, not vague hardening goals: "no
cross-tenant read/write of another owner's data without authorization," not "take
security seriously." Absent → convergence falls back to the security-diff lens's
own AuthN/AuthZ/secrets/injection checklist as the contract, which is narrower than
a repo-authored list can be (it won't know, for instance, that a repo treats a
specific cache-key shape as a tenancy boundary).

### `enabled_when` — path vs content signals

`paths` alone is a weak proxy for "this diff's substance concerns the lens" — a
lock-ordering rewrite that never touches a file named `*async*` or `*queue*` won't
trigger `concurrency` on filename globs alone. `content_patterns` matches
substrings/regexes over the diff body itself (case-insensitive), independent of
file naming. Keep patterns narrow enough to avoid matching unrelated comments or
strings; widen deliberately rather than defaulting to something broad like `.*`.

Either signal (`paths` OR `content_patterns`) is enough to activate a conditional
lens. When neither matches but the diff content clearly implicates a lens anyway,
the orchestrator may enable it as a judgment call — but must disclose this in the
output as an **orchestrator override**, naming the signal that triggered it (e.g.
"concurrency enabled: profile globs didn't match, but the diff is substantively
lock-ordering changes").

## Fallback heuristics (no profile present)

- **Always on:** `code-review`, `security-diff`.
- **BIM lenses** (`bim-code-review`, `revit-lifecycle`) only if the repo signals
  BIM: a `revit-relay/` dir, `*.rvt` / `*.rfa` files, `Autodesk` / `RevitAPI`
  references, or a BIMpossible remote. Never enable by path heuristic in a repo
  whose profile disables them.
- **concurrency:** enable when changed paths match async/queue/worker/signalr/thread
  patterns, or the diff body matches lock/transaction-isolation content patterns.
- `contracts` / `prior_reports_glob` absent → fallbacks in their sections above.
- `read_only_default: true`, `allow_autofix: false`.

## Template — BIMpossible (`{repos}\BIMpossible`)

```yaml
version: 1
review:
  default_mode: standard
  read_only_default: true
  allow_autofix: false
  read_only_sandbox: true         # DELIVERY.md § Sandbox-originated Git actions: no sandbox commits/pushes
  local_verification:
    - powershell -ExecutionPolicy Bypass -File .\Verify-Local-CI.ps1 -BaseRef origin/main
  prior_reports_glob: "docs/audits/*review-all*.md"
  contracts:
    - "no cross-tenant/cross-firm read or write of another owner's project or model data without an authorization check on that access"
    - "no mutation of a protected file/path (.env, DELIVERY.md-listed paths) without the declared approval step"
    - "a durable write to project↔item↔model state is verified through the pairing oracle for the REQUESTING principal, not inherited from a differently-scoped cached verdict"
  lenses:
    code-review:      { enabled: true }
    bim-code-review:  { enabled: true, modes: [standard, deep] }
    security-diff:    { enabled: true, modes: [standard, deep] }
    revit-lifecycle:
      enabled_when: { paths: ["**/*.cs", "**/*Revit*", "**/*ExternalEvent*"] }
    concurrency:
      enabled_when:
        paths: ["**/*async*", "**/*queue*", "**/*worker*", "**/*signalr*"]
        content_patterns: ["lock", "savepoint", "FOR UPDATE", "ContextVar", "request_scope"]
convergence:
  include_agent_gates: true       # dedup against backend-endpoint-reviewer / migration-reviewer / typescript-api-reviewer
  require_location: true
  require_failure_path: true
  min_confidence: medium
  max_autofix_iterations: 3
```

`read_only_sandbox: true` makes `--fix` refuse in the sandbox — remediation is
delegated to the human push flow. `--ci` runs the canonical local verifier (the
surviving rule from the retired `converge-review`: fix locally, rerun the verifier,
never debug through remote CI).

## Template — Evidence Compiler

```yaml
version: 1
review:
  default_mode: standard
  read_only_default: true
  allow_autofix: false
  lenses:
    code-review:     { enabled: true }
    bim-code-review: { enabled: false }   # EC charter forbids BIM/Revit policy in core — explicit, not heuristic
    revit-lifecycle: { enabled: false }
    security-diff:   { enabled: true, modes: [deep] }
convergence:
  include_agent_gates: false
  require_location: true
  require_failure_path: true
  min_confidence: medium
```

Disabling the BIM/Revit lenses here is deliberate: it matches the repository
boundary from the scan — BIMpossible gets BIM-oriented review; Evidence Compiler
never accrues irrelevant Revit/BIM policy checks.

## Placement

These are **templates**. Writing a profile into a repo changes THAT repo's tree, so
it is done only on request. Copy the matching template to
`<repo>/.claude/review-profile.yaml` and commit it in that repo's own workflow.
