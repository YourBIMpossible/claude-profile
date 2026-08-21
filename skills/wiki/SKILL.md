---
name: wiki
description: Maintain the BIMpossible design-knowledge wiki at BIMpossible_Workspace/wiki/ — a persistent, cross-referenced synthesis layer over decision records and design docs (pattern from Karpathy's LLM Wiki gist). Use "/wiki ingest [source]" to fold new/changed decision records into wiki pages, "/wiki query <question>" to answer a design-intent question with citations, "/wiki lint" to check for orphaned/stale/contradicting pages. Never runs automatically — always explicit invocation, from the user directly or via /close offering it.
---

# wiki — design-knowledge wiki maintenance

Schema and full conventions: `../../BIMpossible_Workspace/wiki/SCHEMA.md`. Read it before the
first ingest of a session — it defines page front matter, directory layout, and what counts as
a design-doc source. This skill file is the operational entry point; SCHEMA.md is the contract.

Raw sources the wiki synthesizes from (immutable — read only, never edit):
`BIMpossible/docs/decision-log/`, `BIMpossible_Workspace/00_Strategy/design-docs/`,
`BIMpossible_Workspace/02_Reference/`, and equivalents in Add-Ins.

## `/wiki ingest [source]`

No source given = ingest everything in `docs/decision-log/INDEX.md` and
`00_Strategy/design-docs/` not yet reflected in any wiki page's `sources:` front matter (check
`last_ingested` per page against the record's own date). A source given = ingest just that
file or that day's session output.

1. Read the source record(s) in full.
2. Find every wiki page whose `area` overlaps — a single decision can touch several pages.
   Update all of them, don't just drop a new page and move on.
3. Where no page exists yet for the area, create one per the SCHEMA.md front-matter format.
4. Update `wiki/index.md`.
5. Append one line to `wiki/log.md`.
6. Report to the user what changed — page(s) created/updated, one line each. This is a visible
   action, not a silent background write, even when the user asked `/close` to "offer to run
   it" and they accepted.

## `/wiki query <question>`

1. Check `wiki/index.md`, read matching page(s), follow cross-links if the answer spans pages.
2. Answer with citations to the underlying decision record(s) — the wiki page is a shortcut to
   the source, cite the source too, not just the page.
3. If answering required synthesis not yet captured anywhere, offer (don't auto-do) to file it
   back as a small ingest.

## `/wiki lint`

Scan `wiki/pages/*.md` for orphans (no incoming link, not in index.md), staleness
(`last_ingested` older than the newest record in that page's `sources:`), and pages that assert
something a newer decision record contradicts. Report only — fixing a flagged issue is a
follow-up ingest with the user in the loop, never an automatic rewrite.

## Hard rules

- Local-first, git-tracked markdown only. No database, no embeddings pipeline, no external
  service — if the wiki outgrows `index.md` + grep, reach for the already-installed `graphify`
  or `ctxdex` skills before adding new infrastructure.
- Every operation is read-from-sources, write-only-inside-`wiki/`. Never edit a decision record
  or design doc from this skill — that's out of scope, flag it to the user instead.
- Never invoked by a hook or automatically at session boundaries. `/close` may *offer* to run
  `/wiki ingest` when it finds unfoled decision records, but the user accepts explicitly each
  time — no standing auto-trigger.
