# Graphify lane closeout — 2026-09-24/25

Mandate: move graphify refresh targets into a gitignored local config, fail loudly, keep private paths off the public dashboard (Claude-Tools PR #9), then finish the post-merge rollout.

## Fenced decisions (settled — do not re-open)

| Decision | Rationale |
|---|---|
| Claude-Tools PR #9 merged (merge commit `4f92b5b`) | F1–F6 review findings fixed; CI green on ubuntu/pwsh and windows/PS 5.1. First scheduled publish 2026-09-25 06:00 verified clean (allowlisted schema, no paths/SHAs). |
| BIMpossible PRs #737 (`5bd02599`) and #756 (`2699eec9`) merged | Daily graph snapshot rows for 9/24 and 9/25 in `backend/graphify-out/metrics-history.jsonl`. |
| ai-dev-dashboard public history NOT rewritten | Leaked data is only local scan paths plus commit SHAs; a force-push breaks the publisher checkout and Pages deploys, and GitHub keeps old SHAs reachable anyway. Reopen only if a leaked path proves genuinely private. |
| graphifyy 0.9.58 → 0.9.67, plus the `[sql]` extra (tree-sitter-sql 0.3.11) | Owner-approved; a manual refresh passed on 0.9.67. |
| GraphRAG "6 patterns" article: nothing adopted | Graphify already works graph-first with the agent choosing tools. The only candidate, text-to-query over the Revit model, stays unqueued at the owner's call. |

## Gotchas

- **Only one script writes the metrics ledger.** `metrics-history.jsonl` is written only by BIMpossible's manual `Update-Graph.ps1`. The scheduled `Refresh-Graphs.ps1` never touches it, so it cannot dirty the backend tree. If a row shows up, commit it through a snapshot PR built on `origin/main`: the primary checkout may lag and be missing earlier rows.
- **Skill upgrade: never run `graphify install`.** `~/.claude/skills/graphify` is this repo's `skills/graphify`. `graphify install` overwrites the local customizations, and `claude install` also edits CLAUDE.md. Instead:
  1. `pip download graphifyy==<old> --no-deps`.
  2. For `SKILL.md` and each `references/*.md`, run `git merge-file <installed> <old-pristine> <new-pristine>`.
  3. Bump `.graphify_version`.

  The customizations live in the `SKILL.md` description, the reranker paragraph in `SKILL.md`, and the reranked-BFS / timing section in `references/query.md`. The 0.9.67 bundle left all three unchanged, so this upgrade changed only `.graphify_version`.
- **Checking a skill upgrade appends to the query log.** Running `graphify query` for the check writes a real query-log record: one was written on 2026-09-25. Verify with a non-query command instead.
