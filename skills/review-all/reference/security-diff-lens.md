# Lens: security-diff

A first-class **internal** security lens. It is NOT the (missing) `/security-review`
command and NOT `security-review-full`. It writes nothing — no reports, no
checklists, no build logs — it returns findings only. Prefer the
`security-engineer` agent type; fall back to a general-purpose agent given this
file.

## Mandate

Review ONLY the selected diff plus the directly necessary surrounding code. Report
only concrete, **reachable** risk that names a plausible code path and a specific
impact. No generic hardening advice.

## Checklist — general

- **AuthN/AuthZ:** missing or incorrect guards; broken tenant isolation; IDOR (an
  ID accepted without scoping to the authenticated principal).
- **Secrets:** keys / passwords / connection strings / tokens in source, config,
  CI, generated files, diagnostics, or client bundles; secrets logged or returned
  to clients.
- **Injection:** SQL, shell/PowerShell, template, path traversal, deserialization,
  unsafe file handling — anywhere untrusted input reaches an interpreter/sink.
- **SSRF** and outbound URL-fetch controls (user-controlled URLs, cloud-metadata
  reachability).
- **Web:** XSS (incl. `dangerouslySetInnerHTML`), CSRF on state-changing requests,
  browser token exposure (`localStorage` vs HttpOnly), CORS posture, unsafe HTML
  rendering, open redirect.
- **Webhooks/callbacks:** signature verification, replay protection, trust-boundary
  validation.
- **LLM:** prompt injection, retrieval/boundary leakage, tool authorization,
  sensitive-output handling, control-flow decisions that trust model output;
  token-cost runaway (rate / size caps).
- **Supply chain:** dependency or build-pipeline changes that widen the attack
  surface.

## Checklist — BIMpossible-specific (only when the repo is BIMpossible)

- APS token issuance/validation: audience/issuer, scope minimization, storage, and
  logging.
- Firm↔hub tenancy bypass (hub-grant / project-enrollment gate, `aec/hub_tenancy.py`).
- Three-layer auth on Revit Link routes (`_auth_check` / `RELAY_SECRET` /
  `X-Relay-Token`) integrity.
- Protected `.env` touches; APS write-back approval rule.

Coverage mirrors `security-review-full`'s threat model **without** importing its
stateful writes or its full-tree cost — this lens is diff-scoped.

## Retention discipline

Emit a finding only when you can name: a precise location, a reachable path, the
impact, and a feasible fix. Drop "use a safer library", "consider adding
validation", and bare OWASP-category references that don't identify a real path in
this diff.

## Output contract (per finding)

```yaml
finding:
  lens: security-diff
  severity: high
  confidence: high
  location: src/api/ProjectsController.cs:118-146
  root_cause_key: project-authorization-missing-membership-check
  evidence: >
    The handler loads a project by caller-supplied ID and returns its data
    after authentication, but does not verify organization membership.
  exploit_path: >
    Any authenticated tenant user can substitute another project identifier.
  consequence: >
    Cross-tenant read of another firm's project data.
  recommended_fix: >
    Authorize project access against the authenticated tenant before loading
    or returning project-scoped data.
  provenance:
    - security-diff
```

`root_cause_key` names the defect, not its location — so the same flaw found by an
auto-gate agent (e.g. `backend-endpoint-reviewer`) collapses into one converged
finding with both lenses in `provenance`.
