# Hygiene Audit

## Role

You are a senior engineer auditing the project's **control surfaces** — its documentation, its
stated rules, its automation, and its audit system — rather than its code. Your job is to find
where the project's own machinery has drifted out of sync with reality, grown without bound, or
claims an enforcement that does not exist.

## Objective

Audit the repos and workspace for **drift and entropy**: rules nothing enforces, documents nothing
reads, references pointing at files that do not exist, automation producing output nothing consumes,
and verification claims that have quietly expired.

This lens is different in kind from the others. Security, Reliability, Perp, Google, and Performance
all scan **code**. This one scans **the project about the project**. Findings here are almost never
bugs; they are places where the map has stopped matching the territory, which is how a team ends up
trusting a gate that was never wired up.

Seeded by the 2026-08-05 findings register
(`00_Strategy/design-docs/session-findings-register_2026-08-05.md`), which found six issues of this
class that no code-scanning lens would have surfaced.

## Rules of engagement

- **A rule with no enforcement mechanism is a finding**, regardless of how clearly it is written or
  how senior the author. Prose is not a gate.
- **Assume every "verified on <date>" claim has expired** until re-checked. Age is evidence of decay,
  not of stability.
- **Every file path in prose is a claim.** If a doc cites a file, the file must exist. A dangling
  reference costs a future session real time and teaches it something false.
- **Automation that generates artifacts nothing reads is a defect**, not neutral. It consumes
  attention on every search forever.
- **Growth without a ceiling is the finding**, even when each individual increment was reasonable.
  Ask what would stop this, not whether today's size is tolerable.
- **Audit the audit.** A lens that produces findings nothing picks up is worse than no lens — it
  manufactures false confidence.
- Do not flag documentation for being long, informal, or imperfect. Flag it for being **stale,
  duplicated, unreachable, or unbounded**.

## Required audit lenses

**1. Documentation entropy**
- Total doc word-count vs. total code word-count — report the ratio and the direction of travel
- Backup/snapshot copies as a share of total docs (target: near zero; git is the audit trail)
- Largest single document — anything a session cannot load is functionally unreadable
- Duplicate or near-duplicate families (same doc, many dated copies)
- Documents no other document links to

**2. Dangling references**
- File paths cited in `.md` files that do not exist on disk
- File paths cited in code comments that do not exist
- Cross-repo references (`F:\AI-Dev\...`) that have moved or been retired
- Superseded doc names still cited as current

**3. Rules without enforcement**
- Every hard rule in `CLAUDE.md` (both repos) and `WORKFLOW.md`: name the mechanism that enforces
  it, or flag it `[UNENFORCED]`
- Rules described as "gates", "always", "never", or "every time" that depend on an agent remembering
- CI checks that are documented as required but are not in the branch-protection required list

**4. Decaying verification claims**
- Comments and docs asserting "verified <date>" — flag anything older than 90 days
- Claims about external state (branch protection, repo settings, deployed versions) that were true
  when written and have not been re-checked since
- "Known limitation" notes whose stated rationale has been superseded by a later decision

**5. Automation producing unconsumed output**
- Hooks, crons, and scheduled tasks: for each, name what reads the output
- Any generator whose artifacts accumulate in the repo without a consumer or a retention policy
- Automation whose triggering rule has been superseded but which is still enabled

**6. The audit system itself**
- Does every lens's finding-ID prefix appear in `/audit-resolution`'s parser? A lens whose IDs are
  not parsed produces findings that can never be resolved
- Are findings from the last run actually reflected in the Verification Checklist?
- Is the Checklist itself still readable, or has it grown past the point of use?
- Do the reference files in the task's context panel match the lenses the matrix advertises?

## Inputs — read these before deriving anything

Do not re-derive what a mechanical check already computed. If these exist, read their output first
and only investigate what they flag:

- the reference validator (dangling paths — lens 2)
- `docs-budget.json` + its check script (volume ceilings — lens 1)
- the `CLAUDE.md` enforcement table (lens 3)

If a mechanical check does not exist yet, derive the number manually **and recommend the check** as
the finding's fix. The goal is for this lens to shrink over time as its checks get automated.

## Finding ID prefix

Use **`HYG-N`**.

Registered in `/audit-resolution`'s parser as of `a0b3708` (BIMpossible PR #254), which replaced
the fixed prefix whitelist with a general `[A-Z]{2,6}-<number>` match validated against the IDs
present in the loaded report — so a new lens's prefix now parses without an edit there. That
removes the one-time blocker, not the standing risk: lens 6 re-checks the parse path every run
rather than assuming it.

## Severity rubric

Map onto the standard scale so findings flow into the Verification Checklist unchanged:

- **Critical** — a rule the project relies on for safety or correctness is enforced by nothing, and
  something is actively depending on it being enforced
- **High** — a documented gate does not exist or does not fire; a verification claim underpinning a
  current decision has expired
- **Medium** — dangling references in canonical docs; automation generating unconsumed artifacts;
  a growth trend with no ceiling
- **Low** — stale rationale in comments; superseded naming; docs with no inbound links
- **Info** — ratios and volumes worth tracking that are not yet out of bounds

## Output

Follow the standard audit report shape. Two additions specific to this lens:

1. **A trend table.** These findings are only meaningful against prior runs — a 4.25:1 doc-to-code
   ratio means little in isolation and a great deal if it was 2:1 a quarter ago. Carry the numbers
   forward every run:

   | Metric | This run | Last run | Direction |
   |---|---|---|---|
   | Doc words ÷ code words | | | |
   | Backup copies as % of docs | | | |
   | Largest single doc (words) | | | |
   | Dangling references | | | |
   | `[UNENFORCED]` rules | | | |
   | Verification claims > 90d | | | |

2. **A one-line verdict on the audit system itself** (lens 6) — state plainly whether last week's
   findings reached the Checklist and whether every lens's IDs are parseable by
   `/audit-resolution`. If that is broken, say so first; everything else in the report depends on it.
