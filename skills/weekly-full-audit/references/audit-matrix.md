# Audit routing matrix & process

Reference for the `bimpossible-audit` skill. Source: the original Audit Process Pack.

## Lens summary

| Audit | Best used when | Typical input |
|------|-----------------|---------------|
| Security | Tokens, auth, secrets, access control, data-at-rest changes | PR, code path, service, config |
| Reliability | Queues, jobs, workers, retries, stale reclaim, long-running operations | PR, workflow, incident area |
| Perp | Code quality, maintainability, edge cases | file, diff, PR |
| Google | System shape, architecture drift, scalability, standards | design doc, PR, subsystem |
| Performance | Slow endpoint or UI action with a reproducible proving case | live environment + proving case |
| Hygiene | Control surfaces rather than code: docs, stated rules, automation, and the audit system itself | both repos + workspace |
| Slop | The "reports success, quietly did nothing" class: silent catches, counters that exclude failures, tests over dead code | PR, diff, subsystem |

## Recommended process

1. Run Perp on code-heavy changes.
2. Run Google on larger architecture or cross-cutting work.
3. Run Security whenever tokens, secrets, sessions, permissions, trust boundaries, browser exposure, or persistence changes.
4. Run Reliability whenever jobs, queues, retries, stale reclaim, background workers, or long-running tasks change.
5. Run Performance only when a proving case and live environment exist.
6. Run Hygiene on the **weekly full audit only** — never per-PR. It measures drift and entropy
   (rules nothing enforces, docs nothing reads, references pointing at files that do not exist,
   automation whose output nothing consumes), which are trends, not defects in a diff. Its findings
   use the `HYG-N` prefix.
7. Run Slop (`references/slop-audit.md`) on the **weekly full audit** and on any AI-authored
   diff that was merged after a skim rather than a line-by-line read. It hunts exactly three
   behaviors — silent-catch census, counter-integrity, tested-but-dead — because gstack's
   scanners gate build/security regressions but cannot prove behavioral honesty. Findings use
   the `SLOP-N` prefix (added 2026-08-08; parses without registry edits — resolution matches
   prefixes by pattern).

## Unattended-run integrity (weekly full audit)

The weekly run has no reader while it executes, so degradation must be loud — this is the
transport-evidence rule applied to the audit itself: a lens that did not run produced **no
evidence**, and no evidence is INCONCLUSIVE, never a pass. (Adopted 2026-08-08 from an
external audit-skill review.)

1. **RUN DEGRADED banner.** If any lens errors out, is skipped, or its scope silently
   shrinks (repo unreachable, tool missing, timeout), the report title line is prefixed
   `RUN DEGRADED — ` and the first section names what was lost. A thinner report must never
   read as a cleaner codebase.
2. **Coverage-gap section (every run).** List what was NOT covered and why — even when the
   answer is "nothing". A lens absent from this list is claimed as fully run.
3. **Pre-declared expected failures.** Checks known to fail for documented reasons are listed
   here so they are neither re-reported as fresh findings nor counted as passes. Currently:
   none. Add entries with a date and the doc that explains them; remove when fixed.
4. **Docs-hygiene gate line in the report header (HYG-13).** The report header must carry a
   one-line status for the mechanical docs-hygiene checks — G3 (`check-doc-references.mjs`,
   dangling refs) and G4 (`check-docs-budget.mjs`, word/file budget) — for BOTH repos
   (`Docs-hygiene: BIMpossible G3 ✓ / G4 ✓ · Workspace G3 ✓ / G4 ✗`). The CI job is weekly-cron,
   `continue-on-error`, and not in any `needs:`, so its only reader is this report; surfacing the
   pass/fail here is the consumption path. A red G3/G4 is reported, not silently dropped.
   Mechanically enforced since 2026-08-31 (audit finding HYG-2) by
   `scripts/check-audit-report-header.mjs` in the workspace docs-hygiene workflow: every
   report dated 2026-08-31+ must carry the line in its first 15 lines with all four
   results, or the workflow fails.

## Minimal audit matrix

| Change type | Perp | Google | Security | Reliability | Performance | Hygiene | Slop |
|------------|------|--------|----------|-------------|-------------|---------|------|
| Small bug fix | Yes | Optional | If auth/data affected | If background/stateful | No | No | Optional |
| Token/session/auth change | Yes | Yes | Yes | Yes | Optional | No | Optional |
| Queue/worker/job change | Yes | Optional | Yes | Yes | Optional | No | Yes |
| Large refactor | Yes | Yes | Optional | Yes | Optional | No | Yes |
| Slow endpoint/UI | Optional | Optional | Optional | Optional | Yes | No | No |
| Weekly full audit | Yes | Yes | Yes | Yes | Optional | **Yes** | **Yes** |

## Notes

- Keep each audit report separate and dated.
- For performance runs, maintain a markdown run index.
- For security and reliability findings, require proving tests or live smokes before closure.
- Do not collapse partially mitigated issues into "resolved" without explicit proof.
- **Every lens must register its finding-ID prefix in `.claude/commands/audit-resolution.md`
  (Step 2, item 1) or its findings can never be resolved.** `OPS-N` was missing there for months
  despite 164 OPS findings existing — fixed 2026-08-06 alongside adding `HYG-N`. Check this when
  adding a lens; it is the failure mode the Hygiene lens itself exists to catch.
