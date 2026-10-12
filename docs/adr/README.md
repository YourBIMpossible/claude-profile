# Architecture Decision Records

Durable, human-reviewed decisions for the AI-Dev harness and its local knowledge
substrate. One decision per file, numbered, short.

## Rules

- **Immutable after acceptance.** A wrong or outdated ADR is superseded by a new
  one (`Supersedes:` / `Superseded-by:` headers), never edited in place. Typos
  and broken links may be fixed; meaning may not.
- **Short.** Context, decision, alternatives considered, consequences. If it
  needs more than a page, the decision isn't crisp yet.
- **ADRs record intent; code records truth.** When they disagree, the code is
  what runs — write the superseding ADR, don't quietly bend the old one.
- Filename: `NNNN-short-slug.md`, numbered in acceptance order.

## Index

- [0001](0001-fts5-local-first-retrieval.md) — SQLite FTS5, local-first retrieval before embeddings/vector infrastructure
- [0002](0002-defer-context-budget-automation.md) — Context-budget automation deferred; ctxdex is artifact retrieval, not context management
- [0003](0003-context-repair-superpowers-file-links-northstar.md) — Superpowers disabled persistently; CLAUDE.md owns one stateless file-path rule; NorthStar nudge follows assume-and-proceed
- [0004](0004-northstar-owner-approved-edits.md) — North-star files become owner-approved edits via the gate's approval prompt (supersedes 0003's human-only edit rule)

## Template

See [template.md](template.md).
