---
name: context-weight-weekly
description: Weekly standing-context budget check for the Claude Code harness (context_weight.py), run through a history-isolated portable wrapper. Read-only observation; never applies fixes.
---

# context-weight-weekly

Deterministic standing-context budget check. Replaces the scheduled routine of the same name; same measurement, verdict and report — only the invocation is parameterized.

## Parameters (all locations are parameters)

| Parameter | Default | Meaning |
|---|---|---|
| `TOOLS_DIR` (`--tools-dir`) | `$CONTEXT_WEIGHT_TOOLS_DIR`, else `<repo>/tools` (this skill sits at `<repo>/skills/context-weight-weekly/`) | dir holding `context_weight.py` |
| `HISTORY_PATH` (`--history-path`) | none — must be explicit | trend JSONL read and appended on complete runs |
| `--no-history-write` | off | do not append history |
| `PYTHON` | `python` on PATH | interpreter |

History rule: with no `--history-path`, `--no-history-write` is required and the canonical `<TOOLS_DIR>/context_weight.history.jsonl` is neither read nor written. The scheduled weekly run that should feed the canonical trend passes `--history-path <TOOLS_DIR>/context_weight.history.jsonl` explicitly.

## Run

    <PYTHON> scripts/run_context_weight.py --tools-dir <TOOLS_DIR> --history-path <HISTORY_PATH>

Exit codes: `0` = measurement complete, no hard failure (VERDICT CLEAN or ADVISORY); `2` = hard failure or a required source unmeasured (never read as clean); `64` = wrapper usage error (bad args, tools dir missing, implicit history write refused). Exit 1 is not emitted.

## What it measures

Schema v2: two independent scenario totals — `global_session` (loads in every session) and `bimpossible_session` (global_session plus what additionally loads inside the BIMpossible repo) — as telemetry. The 32KB design target and 45KB/56KB reference lines are informational only and never gate (status=INFO past one). Real limits fail per source, above all Claude Code's MEMORY.md startup truncation (25KB / 200 lines). It prints a measured/unmeasured/not-applicable coverage inventory and, from the second run on, trend lines versus the last comparable history entry.

## Report to the user, briefly

- Exit 0 and `VERDICT: CLEAN`: one line — "context-weight: CLEAN — global_session <total> (~<tokens>), bimpossible_session <total> (~<tokens>)". Nothing else.
- `VERDICT: ADVISORY` (exit 0) or exit 2: each scenario's status line verbatim, then each FAIL/WARN line (scenario, per-source ceiling, per-skill, or trend) verbatim, then per line the likely fix in a few words (trim the named skill's description to trigger+routing; run a consolidate-memory pass; trim the named CLAUDE.md; disable or allowlist the named plugin). Do NOT apply any fix.
- Either way, if "Unmeasured/discovered sources" lists a *required* entry (not the always-present optional/hosted ones), call it out: the verdict is incomplete, not clean.
- Exit 64 or a script error (missing python, moved file): report that as the finding instead of guessing.

## Tests

    <PYTHON> -m pytest -q tests        # CONTEXT_WEIGHT_TOOLS_DIR selects the tools dir
