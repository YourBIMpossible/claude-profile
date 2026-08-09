---
name: context-budget
description: Use when a session is burning tokens on large context, when tool output is about to flood the window (bulk greps, full-file reads, verbose builds, log dumps, MCP schema loads), or when the user asks to reduce token usage / stop wasting context. Enforces "big outputs go to files; small relevant slices stay in context."
---

# Context Intake Budget

**Principle: big outputs go to files; small, relevant slices stay in context.**

The expensive mistake is not a long conversation. It is admitting one enormous
tool result, which is then re-billed on every subsequent turn of the session.

## Why this exists (measured, not assumed)

From the owner's 25 largest sessions — 51,569 messages, 9,327 tool results:

| Measurement | Value |
|---|---|
| Context growth caused by the **top 1%** of tool results (93 calls) | **64.6%** |
| Top 5% of results | 80.7% |
| Cumulative `cache_read` | **7.46B tokens** |
| Compactions across all 25 sessions | **8** |
| Images / screenshots | 1.78M tok (~2.6%) — *not* the problem |

Two conclusions follow, and they overturn the usual advice:

1. **Intake is the bottleneck, not summarization.** Compaction almost never
   fired. Tuning summary quality would have addressed a problem that was not
   occurring. Do not reach for `/compact` as the primary lever.
2. **A few calls cause nearly all the damage.** Ninety-three calls out of 9,327
   drove two-thirds of all context growth. The fix is narrow: stop those.

Worst offenders by measured context growth: `Bash` 24.3M, `ToolSearch` 7.6M
(mean ~61k *per call*), `Read` 6.8M, `PowerShell` 6.5M.

*Method caveat:* attribution uses `cache_creation` deltas, which bundle
co-located new content, so per-tool means for cheap tools are inflated. The
right-tail concentration and the identity of the offenders are solid.

## What Claude Code already does — and the gap it leaves

Claude Code persists *very* large results by itself, to
`<project>/tool-results/<id>.txt`, showing a `<persisted-output>` wrapper with a
2KB preview. Measured on 2.1.178:

| Bash output | Native behaviour |
|---|---|
| 293 KB | persisted |
| 40 KB | persisted |
| **11 KB** | **entered context in full** |

The native floor sits between 11 KB and 40 KB. **That band is the gap.** An 11 KB
result is ~2,700 tokens the harness waves through, which `cache_read` then
re-bills on every later turn of the session.

The two compose safely rather than collide: once the harness has swapped a huge
result for its 2KB preview, that preview is below this hook's threshold and
passes straight through. No double spill.

## What is automatic

`hooks/context_budget.py` runs on `PostToolUse`. Any allowlisted tool result
over its threshold is written to `.claude/scratch/tool_<ts>_<tool>.log` and
replaced in context with a bounded record: size, origin, head and tail
excerpts, and the file path. Every record is capped at ~2.4k chars (~600
tokens) regardless of input size — 11 KB compresses ~4x, 280 KB ~113x.

It runs at **PostToolUse**, not PreToolUse, deliberately: after the tool runs,
the size is a *fact*. Beforehand it is a guess, and guessing truncates results
that mattered while missing the blobs it failed to predict.

It fails **open** — on any error the output passes through unchanged and the
failure is logged to `hooks/context_budget.error.log`. It will never break a
session, and it will never fail silently.

Defaults: 8,000 chars generally; `Read` 12,000; `Grep` 10,000; `ToolSearch`
6,000. Override with `CLAUDE_CONTEXT_BUDGET_CHARS`. Spill files self-ignore via
`.claude/scratch/.gitignore` and are pruned after 7 days.

**Activation:** hooks are read from `settings.json` at session start. Editing the
hook config does not affect the running session — a session started before the
edit is unprotected. Verify it is live by checking that `.claude/scratch/`
appears after a large tool result, not by assuming.

## What is on you

The hook is a backstop for output you did not anticipate. It is not permission
to be careless — a spilled result still cost the tokens to produce, and the
round trip to retrieve it costs more than never fetching it.

**Bound output at the source.**

- `git log --oneline -20`, not `git log -p`
- `grep -c` or `rg --files-with-matches` first; fetch content only where it hit
- `Read` with `offset`/`limit` once you know the region; never whole-file "to
  get oriented"
- Pipe verbose builds to a file yourself and grep for the failure
- Prefer `read_page` (accessibility tree, text) over `screenshot` when
  verifying text or structure

**Never re-admit what was just excluded.** When you see a `[context-budget]`
record, do not `cat`/`Read` the whole spill file. Grep it:

```
Grep(pattern="FAILED|Error", path="<FULL OUTPUT path>", output_mode="content", -C=3)
```

**Delegate genuinely large reading.** A subagent explores in its own window and
returns a conclusion. Ten thousand tokens of searching become a few hundred of
answer, and the searching never enters this session.

## MCP and ToolSearch discipline

`ToolSearch` averaged ~61k tokens per call — the highest per-call cost measured,
because it loads full MCP tool schemas permanently into context.

**The cost is the call, not the connection.** With tool-search deferral active
(the norm here), a connected server contributes only its tool *names* to the
session preamble — roughly 2–3k tokens for ~170 tools, paid once. The schemas
stay out until a `ToolSearch` pulls them in, and then they never leave.
Disconnecting servers is therefore close to worthless as a token measure;
disciplined `ToolSearch` use is where the 61k/call actually lives.

- Load tools **once**, in **one batched call**: `select:A,B,C`. Every extra
  `ToolSearch` is another schema dump that never leaves.
- Load only what the task needs. Do not speculatively load a server's toolkit.
- After compaction, do **not** reload entire MCP surfaces. Re-load the specific
  tools still in use.

## Native knobs — verified in the binary, with defaults

| Env var | Default | Unit | On exceed |
|---|---|---|---|
| `BASH_MAX_OUTPUT_LENGTH` | 30,000 | chars | **lossless** — full output saved to file |
| `MAX_MCP_OUTPUT_TOKENS` | 25,000 | tokens | **LOSSY** — content discarded |
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | 25,000 | tokens | — |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | — | tokens | set to 150,000 here |

`BASH_MAX_OUTPUT_LENGTH` is the 30,000 that appears in the Bash/PowerShell tool
description — it is interpolated live, so the doc always states the effective
value. The resolver applies no minimum, and caps at 150,000.

Two traps:

1. **Do not lower `MAX_MCP_OUTPUT_TOKENS`.** Exceeding it emits
   `[OUTPUT TRUNCATED - exceeded N token limit]` and the content is *gone*.
   Native truncation runs before this hook, so a lower cap destroys exactly what
   the hook would have preserved. The hook's 8,000-char MCP threshold fires far
   below the native 25,000-token one, so they do not collide.
2. **Do not lower `BASH_MAX_OUTPUT_LENGTH` below the hook's threshold.** Native
   spill is lossless but its preview is *head-only*. This hook keeps head **and
   tail** plus the originating command — and for build and test output the
   failure is at the end. Undercutting the hook trades the tail away.

`CLAUDE_CODE_MAX_OUTPUT_TOKENS` is **not** an intake control despite the name —
the binary's own error text ties it to *Claude's response* length. Wrong lever.

## Escape hatch

When a full result genuinely must stay inline (a file being edited verbatim, a
structured payload that breaks if clipped), raise the threshold for that call:

```bash
CLAUDE_CONTEXT_BUDGET_CHARS=200000 <command>
```

Prefer narrowing the request over raising the ceiling.

## Related

Earlier compaction is a complement, not a substitute:
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=120000` (verified real in 2.1.178).

Note: `compactPrompt` and `compactInstructions` **do not exist** — zero
occurrences in the binary, despite widespread blog advice. Setting them does
nothing. `/compact <instructions>` as an inline argument *is* supported, but
auto-compaction cannot be steered.
