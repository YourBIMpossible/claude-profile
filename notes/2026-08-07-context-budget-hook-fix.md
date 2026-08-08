# 2026-08-07 — context_budget.py payload-shape fix (closed)

Mandate: fix `hooks/context_budget.py` — it read `tool_output`/fell back to `tool_response`
and blindly `json.dumps()`'d the result whenever it wasn't already a string. That defeated
the hook's own purpose for large outputs: measured the wrong (JSON-blob) length for the
spill-size check, and for Bash/PowerShell truncated by the harness, returned metadata
instead of the real content pointed to by `persistedOutputPath`.

## Shipped (commit 0e119ec on `feat/graphify-query-rerank`, pushed)

- `_resolve_text()` dispatches structurally by which keys are present per tool shape
  (Bash/PowerShell, Read, Grep, Glob confirmed live) instead of one universal-shape
  assumption; falls back to the old `json.dumps()` behavior for unconfirmed shapes rather
  than guessing.
- `_read_if_safe()` / `_TOOL_RESULTS_ROOT` — path-containment check ported from
  `ctxdex_autoindex.py`'s equivalent fix, so a forged path in a tool's own output can't
  become an arbitrary local file read.
- `hooks/test_context_budget.py` — 50 tests: unit coverage of every confirmed shape +
  path-traversal rejection, plus subprocess integration tests against the hook's real
  stdin/stdout contract (including a real-subprocess test with faked `USERPROFILE`/`HOME`
  to exercise actual `persistedOutputPath` resolution). All passing.
- Live-verified end-to-end through the real harness: a large Bash output produced a real
  `persistedOutputPath`; the hook resolved it byte-for-byte correctly (confirmed via a
  standalone scratch script, not just repr() output — an earlier ad-hoc bash-quoted check
  gave a contradictory answer and was discarded rather than trusted).

This resolves the first queue line in `notes/2026-08-06-harness-tuning.md`
("`hooks/context_budget.py` ... uncommitted WIP in this shared tree").

## Queue — found, NOT handled (this session's, not this session's to fix)

- `_resolve_text()` still falls back to the generic `json.dumps()` path for ToolSearch,
  WebFetch, WebSearch, TaskOutput, and `mcp__*` tool shapes — unconfirmed against live
  payloads. Worth a future live-capture pass per shape, same method used for
  Bash/PowerShell/Read/Grep/Glob this session.
- `hooks/ctxdex_autoindex.py` (+ `ctxdex_autoindex.log`, `context_budget.error.log`,
  `skill-detection-hook.json`) are still untracked in this repo, same as flagged in the
  2026-08-06 note. Not this session's lane to land.
- PR for `feat/graphify-query-rerank` — pushed, no PR opened (explicit-permission action,
  owner's call).
