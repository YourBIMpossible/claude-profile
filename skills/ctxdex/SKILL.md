---
name: ctxdex
description: Local FTS5 index for durable recall over large content already seen once — fetched docs, big logs, exports, reference dirs. Use when a large artifact will be needed again later in a session or across sessions, when re-reading a big file just to find one section, or when the user says "index this", "search the index", or asks to recall something previously read. Not for live codebase search (use Grep) or code structure (use graphify).
---

# ctxdex — local knowledge index

A ~350-line SQLite FTS5 CLI at `F:\AI-Dev\.tools\ctxdex\ctxdex.py`. Fully local — no network except URLs you explicitly index.

**Dependency pin (for the auto-capture hook):** ctxdex is its own git repo at `F:\AI-Dev\.tools\ctxdex` (local-only, no remote). The hook was built and verified against commit `402c0e6` ("Initial commit", 2026-08-07); current pin is `dd0173b`, which adds the manual-ingestion secret gate (hook suite re-verified against it). Required support features: `index --path` override (synthetic `bash://` URIs) and `purge --source`/`--older-than` compose (scoped retention). Check with `git -C F:\AI-Dev\.tools\ctxdex log --oneline -1`. If ctxdex is absent or lacks `cmd_index`, the hook fails open silently in-session and writes a clear `preflight:` diagnostic to `ctxdex_autoindex.log`.

Solves one problem: content you paid tokens to see **once** (a fetched doc page, a 300KB log, a CSV export, a reference directory) is otherwise truncated away or must be re-read whole. Index it, then pull back ranked 1500-char snippets on demand.

## When to use

| Situation | Use ctxdex? |
|---|---|
| Large artifact needed again later (log, export, fetched doc, spec) | **Yes** — index it once, search after |
| Re-reading a big file to find one section | **Yes** — index, then search |
| Reference material spanning many files (docs dir, vendored SDK) | **Yes** — index the dir with a `--source` label |
| Finding a symbol/string in the live codebase | No — use Grep (live, exact, no staleness) |
| "What calls X" / impact analysis | No — use graphify |
| Published library API docs | No — use Context7 (curated, remote, always current) |

Rule of thumb: ctxdex is for **your** content that would otherwise be re-read or lost. It is a cache, not a source of truth — never cite it for current file state without verifying live.

## Commands

Run from anywhere; `--project` defaults to the current folder name, giving each repo its own DB.

```bash
python F:/AI-Dev/.tools/ctxdex/ctxdex.py index <path-or-url> --source LABEL
```
Indexes a file, a directory (recursive, text extensions only, skips .git/node_modules/venv/bin/obj/dist/build), or a URL (HTML stripped to text). Chunks on markdown headings, 4KB max per chunk.

```bash
python F:/AI-Dev/.tools/ctxdex/ctxdex.py search "<query>" [--source LABEL] [--limit N]
```
Returns `--limit` results (default **2**) as 1500-char snippets. Ranking: porter-stemmed BM25 + trigram index merged by Reciprocal Rank Fusion, then chunks matching **all** query terms are boosted above partial matches.

```bash
python F:/AI-Dev/.tools/ctxdex/ctxdex.py stats     # chunk counts, DB size, per-source breakdown
python F:/AI-Dev/.tools/ctxdex/ctxdex.py sources   # list source labels
python F:/AI-Dev/.tools/ctxdex/ctxdex.py purge --source LABEL   # or --older-than DAYS (default 14) / --all
```

## Auto-capture (Bash output)

A PostToolUse hook (`F:\AI-Dev\claude-profile\hooks\ctxdex_autoindex.py`), registered in `~/.claude/settings.json` scoped to `Bash` only, auto-indexes large Bash output as a side effect — no `index` call needed.

- **Threshold:** output ≥100 KiB → indexed under `source=bash-output`, `path` set to a synthetic `bash://<timestamp>` URI.
- **Opt-out:** set `CTXDEX_AUTO_INDEX=0` to disable.
- **Secret safety (conservative — skips rather than risks):** never captures when the command looks like credential/env inspection (`env`, `printenv`, bare `set`, `Get-ChildItem Env:`, `.env`, `credential`/`secret`/`token`/`password`/`api-key` mentions), or when the output itself matches a private-key header, AWS/GitHub token shape, Bearer token, or connection-string pattern. Skips are silent to the session — check `ctxdex_autoindex.log` (rotating, next to the hook) for why something didn't get captured.
- **Self-index guard:** a command that actually *invokes* ctxdex — `ctxdex.py` at any path/case, or a bare `ctxdex` (optionally after `python`/`py`) in command position — is never captured, so index/search runs can't recursively index themselves. Tightened 2026-08-07: merely mentioning the word no longer trips it, so reading this hook's own source, grepping for "ctxdex", or a commit message about it all capture normally.
- **Native-persistence follow:** Claude Code's own harness hard-truncates Bash `stdout` to 30,000 characters before any hook sees it, and for genuinely large output carries the real content via a separate structured field, `tool_response.persistedOutputPath` (confirmed live 2026-08-07 — it is *not* rendered "Full output saved to: `<path>`" text inside the payload; that text is a transcript-display artifact only). The hook reads that field directly — only when it resolves under this machine's own `~/.claude/projects/**/tool-results/` tree — so the size gate sees the true output, not the truncated preview.
- **Retention:** `bash-output` rows expire after 14 days by default, scoped separately from manual sources — `ctxdex purge --source bash-output --older-than 14` clears only auto-captured rows and leaves manually-indexed docs/dirs/URLs untouched. Bare `ctxdex purge` (no flags) still age-purges everything at the default TTL, unchanged.
- **Finding it:** `ctxdex search "<query>" --source bash-output`, or `ctxdex sources` to see the `bash-output` count alongside manual sources.
- **Failure policy:** fails open, always — a hook error never blocks, alters, or adds noise to the Bash result; stdout is always empty, exit is always 0.

## Working rules

- **Always pass `--source`** with a meaningful label on index. It's the only scoping mechanism at search time; unlabeled content becomes unfindable noise.
- **Keep `--limit` low.** Default 2 exists because the point is saving context. Raise to 3-4 only when the first pass genuinely missed.
- **Scope searches** with `--source` whenever you know which body of content holds the answer — it prevents cross-source dilution.
- **Purge stale sources** rather than letting DBs grow. Anything indexed from a moving target (a log, a working file) is stale the moment it changes; re-index rather than trusting an old chunk.
- Never index secrets, `.env` files, or credential stores. Content is stored in plaintext SQLite. Since `dd0173b` this is enforced, not just policy: manual `index` refuses known secret filenames and secret-shaped content (same pattern list as the hook — the two copies must stay aligned, see the ALIGNMENT NOTE in `ctxdex.py`), prints the reason, and exits 2 without writing. Directory runs still index their clean files. There is deliberately no override flag.

## Known limits

- Lexical only — no embeddings. Synonyms and paraphrases won't match; use the vocabulary the content uses.
- TOC-heavy HTML pages can rank their table of contents first, since it legitimately contains every query term.
- Binary and non-text extensions are skipped silently on directory indexing.
- Auto-capture's "native-persistence follow" depends on Claude Code's tool-results files staying under `~/.claude/projects/**/tool-results/`; if the harness ever moves that path, the safety check just stops following (fails closed to the small preview, not open to an untrusted path) and large output silently stops getting captured until this hook is updated.
- `stdout` alone is hard-capped at 30,000 characters by the harness regardless of true output size — this is why the persisted-output follow exists at all; there is no way to recover full content beyond that cap without it.
