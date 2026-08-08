# 0001 — SQLite FTS5, local-first retrieval before embeddings or vector infrastructure

- **Status:** Accepted
- **Date:** 2026-08-07
- **Supersedes:** —

## Context

Content paid for once (fetched docs, large logs, exports) was being truncated
away or re-read whole. Evaluating context-mode (github.com/mksglu/context-mode)
found real exposure — telemetry activation, settings rewriting, env passthrough —
and a benchmark measuring against a strawman. Exactly one of its mechanisms was
additive: a local lexical index over previously-seen content. The broader market
push is toward embeddings, vector DBs, and hosted memory services.

## Decision

Retrieval is a single-file, stdlib-only SQLite FTS5 CLI (`ctxdex`, its own local
git repo at `F:\AI-Dev\.tools\ctxdex`): porter + trigram indexes merged by RRF,
all-terms boost, per-project DBs. Fully local — nothing leaves the machine.
Embeddings, vector stores, graph memory, and hosted services are deferred until
FTS retrieval demonstrates a specific, recorded failure (missed-query log first;
an eval set is built from real misses, never from imagined queries).

## Alternatives considered

- context-mode MCP — rejected: audited exposure, strawman benchmark, framework owns the store.
- Embeddings/vector DB now — rejected: operational + privacy cost with no demonstrated lexical failure.
- Context7-style hosted docs — rejected for this purpose: remote, queries leave the machine; kept for published-library docs only.

## Consequences

- Lexical only: synonyms/paraphrases miss; users must query in the content's vocabulary.
- One portable DB file per project; no service dependency, no network surface.
- A future move to hybrid retrieval requires a superseding ADR backed by the missed-query evidence.
