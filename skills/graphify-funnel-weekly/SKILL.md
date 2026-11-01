---
name: graphify-funnel-weekly
description: Weekly graphify adoption funnel (availability -> routing -> execution -> measurement) with per-stage verdicts, plus reranker latency/quality once real queries arrive. Read-only observation; portable replacement for the graphify-rerank-usage-check routine.
---

# Graphify adoption funnel (weekly)

Read-only observation. Do not re-run benchmarks, change flags or env vars, edit hooks/skills/config,
or touch lexical handling. A recall re-measurement is a separate, explicitly scoped follow-up, never
started here.

Background: the query log alone cannot tell "no demand" from "graph not found", "skill/subagent
bypassed" or "wrapper/logging broken". A SessionStart/SubagentStart hook injects a `<graphify-graph>`
hint in graph-backed sessions (go-live date = config `funnel.hint_live_since`), and queries go through
the graphify skill's `query_reranked.py` wrapper (logs `rerank=true|false`, `via=wrapper`). A record
with no `rerank` field is a bare `graphify query` CLI call: report it as "unlabelled bare CLI",
outside both measurement arms; it is itself a routing deviation. Records before 2026-09-06 are
pre-cutover and not evidence.

## Parameters

| Parameter | Meaning | Default |
|---|---|---|
| `SCRIPT` | `graphify_funnel.py` (run from a dedicated, detached runner worktree; never edit/update it here) | `$GRAPHIFY_FUNNEL_SCRIPT` (required if unset: pass `--script`) |
| `CONFIG` | funnel config: resolver folder (`skill_scripts`), `query_log`, `targets`, `funnel.hint_live_since`, smoke/test exclusions | script's default: `graphify.local.json` beside the script; normally the main-checkout config |
| `PROJECTS` | Claude transcript root | `~/.claude/projects` |
| `LOG` | graphify query log | config `query_log`, else `$GRAPHIFY_QUERY_LOG` |
| `DAYS` | window length | `7` |
| `UNTIL` | window end, ISO timestamp | now |
| `REPORTS` | output folder; outside any git tree, or git-ignored inside one | `~/.claude-reports/graphify` (any non-repo folder) |

Output names: `graphify-funnel-<YYYY-MM-DD>-N.md` / `.json`, `N` = next unused integer for that date.

## Steps

1. Run (date = today, local; paths are the parameters above):

       python <skill-dir>/scripts/run_funnel.py --script <SCRIPT> --config <CONFIG> \
         [--projects <PROJECTS>] [--log <LOG>] --days 7 [--until <UNTIL>] \
         --out <REPORTS>/graphify-funnel-<YYYY-MM-DD>-N.md --json <REPORTS>/graphify-funnel-<YYYY-MM-DD>-N.json

   The wrapper refuses an `--out`/`--json` inside a git tree unless git-ignored there (exit 3) and a missing script (exit 4);
   otherwise it passes the funnel's exit code through: `0` ok, `2` bad config or bad `--until`.
   The funnel writes only `--out` and `--json`. If it fails, report stderr verbatim as
   "audit tooling FAIL" - do not substitute a log-only count.

2. Read the report's verdict table and post one line per row, exactly:
   row - PASS / FAIL / INSUFFICIENT EVIDENCE - its counts - and for FAIL the specific failed boundary:
   - **1a Graph resolution**: organic sessions that resolved a graph; FAIL = graph-expected repo with
     no graph (graph missing / resolver failing).
   - **1b Hint delivery**: post-go-live graph-backed sessions that saw the hint, with the pre-hint
     count stated as no evidence. FAIL = hook failure. Zero post-go-live graph-backed sessions =
     INSUFFICIENT EVIDENCE, never PASS.
   - **2 Routing**: lead with the decisive line - post-go-live graph-backed exploration units ->
     graphify queries (X/Y). Eligibility is a heuristic; say so. Any bypass is a routing FAIL to
     investigate THIS week: name the post-go-live bypassed units with their transcript paths (from
     the report), else the top whole-window bypasses. It is never a reason to wait for 10-15
     reranked records. State the "eligibility unknown" counts (shell-only units, sessions with no
     exploration unit) - never turn zero logged queries into "no demand".
   - **3 Execution**: calls vs completed vs log records; FAIL names wrapper errors, completed calls
     with no record, or unrecovered shell errors. Mention recovered shell errors (kept as evidence)
     and the bare-CLI call count.
   - **4 Measurement** (organic matched records only): reranked vs stock (both wrapper wall time) and
     unlabelled bare CLI separately; zero-node counts; unattributed records. Thresholds apply ONLY
     here: <10 reranked organic records = INSUFFICIENT EVIDENCE for quality/latency; reranked median
     >2000 ms or p90 >3000 ms = FAIL. Never compare smoke/test latencies to these ceilings, and never
     compare bare-CLI (traversal-only) timing with wrapper timing.

3. Tests/smoke sessions (config exclusions, temp-dir runs, redirected `GRAPHIFY_QUERY_LOG`) are
   already excluded; state the excluded count and never fold them into organic numbers. If a new
   smoke/test session pollutes the week, list its id as a suggested exclusion - do not edit the config.

4. End with the report's absolute path (plain text) and a <=3-line bottom line: the first row that
   FAILed (or is INSUFFICIENT) and what to investigate.

## Tests

    GRAPHIFY_FUNNEL_SCRIPT=<SCRIPT> python -m pytest -q <skill-dir>/tests

Fixture-only (synthetic projects/log/config/resolver under pytest `tmp_path`); skipped when
`GRAPHIFY_FUNNEL_SCRIPT` is unset.
