# F:\AI-Dev — Default Working Style (Canonical)

> **Precedence superseded by `F:\Claude-Profile\CLAUDE.md`** (Operating agreement + stop list). Where this file conflicts with it, CLAUDE.md wins.

> **Relocated 2026-08-22** — moved from `F:\AI-Dev\system\` to `F:\Claude-Profile\docs\system\` (AI-Dev extraction). `F:\AI-Dev` is now the owner's personal sandbox; project roots are `F:\BIMpossible*`, `F:\Claude-Profile`, `F:\Claude-Tools`, `F:\AI-Dashboard`, `F:\AI-Brain-Data`. Historical `F:\AI-Dev\...` paths below translate via `RENAMES.md`.

## Status
Canonical working-style for all projects in `F:\AI-Dev`. Subfolder rules cross-reference this file. Last updated 2026-05-30.

## Default: do what's asked, at the scope asked
- Build exactly what I requested. Don't expand scope, add features, or "round up" to a fuller version unless I ask.
- If a bigger or more robust version is clearly worth it, say so in one line and let me decide — don't build it preemptively.
- Don't write plans, design docs, or audit reports unless I ask. A short inline note of what you're about to do is fine; a separate document is not.
- Handle obvious edge cases inline. Flag larger gaps in one line rather than building for them unprompted.

## Optimization priority (when I ask you to improve something)
Rank by: 1) correctness/data integrity, 2) security/authorization, 3) performance, 4) simpler architecture.
Default to the minimal correct change. Go further only when I ask, or when one of the above is genuinely at risk — and name the risk in one line first.

## Surfacing trade-offs (keep it short)
See something worth fixing that's out of scope? Mention it in one or two sentences: what it is, and the risk of leaving it. No ranked findings, severity tables, or multi-section write-ups unless I ask for an audit.

## Audits & prior-art research — opt-in, not default
Real work, but only when I explicitly request it. Don't treat subsystems as default candidates for prior-art comparison. When I do ask, structured output (ranked issues, evidence, effort) is welcome.

## Per-project overrides
Subfolder rules may add to this file. The more specific instruction (repo CLAUDE.md, then the current task) wins; name the conflict in one line of the report and keep going.
