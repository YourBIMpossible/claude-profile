---
name: dashboard-update
description: Use when updating, refreshing, rescanning, or fixing the AI-Dev project dashboard (F:\AI-Dev\Dashboard, data.js, ai-dev-dashboard, ai-dev-dashboard.pages.dev) — after an audit run, after work lands across repos, when a project card or the Toolkit looks stale, or when asked to "update the dashboard".
---

# Dashboard Update

Renders the AI-Dev dashboard from other systems' outputs. **This skill is a renderer, never an auditor.** It never runs, judges, or resolves an audit — `/audit`, `/revit-functionality-audit`, `/project:audit-resolution`, and `/project:breach-chains` own that. This skill reads what they already produced and puts it on the board.

## Modes

| Mode | Use when | Scope |
|---|---|---|
| `refresh` (default) | Routine "update the dashboard" | Run the scheduled refresh; nothing hand-written. Correct even if today's 06:00 run already fired — it's idempotent and reports "already current" |
| `ingest` | An audit/report just finished | `refresh` + rewrite one card's `audit` block |
| `full` | "Rescan", "be thorough", days of cross-repo work | `refresh` + per-repo research + hand-patch narrative fields |

## Always, every mode

1. **Sync before touching anything.** `git -C F:\AI-Dev\Dashboard fetch origin main` then `git merge --ff-only origin/main`. The auto-clone pushes ~daily; a patch built on a stale base silently reverts live content. Re-check for drift again right before you commit.
2. **Run the refresh:** `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\AI-Dev\Dashboard-auto\Refresh-Dashboard.ps1"`, then read `F:\AI-Dev\Dashboard-auto\_backups\refresh-log.txt`. This renders phases/waves from the ledgers, dates/activity from git, DAG, graph-metrics. Never hand-edit what it owns (`progress` for `bimpossible`, `activity`, `lastActivity`, `generated`). Then ff-sync the editing clone to what it pushed.
3. **Never freehand-edit `data.js`.** Patch through the repo's own machinery — a throwaway Python script importing from the repo:
   ```python
   sys.path.insert(0, r"F:\AI-Dev\Dashboard")
   from sync_dashboard import extract_block, apply_patch, node_check
   from sync_ledgers import compact_to_js
   i, j, block = extract_block(data_js, "addins")
   new_block = apply_patch(block, {"focus": "..."}, serialize=compact_to_js)
   data_js = data_js[:i] + "\n    " + new_block + ",\n    " + data_js[j:]
   ```
   Untouched fields stay byte-identical; a bad patch can't ship. Targeted `Edit` calls on a 130KB generated file are how you corrupt it.
4. **Gate before commit:** `node --check data.js` **and** `python validate_dashboard.py` (0 warnings). Then read the actual diff.
5. **Commit `data.js` alone** unless you deliberately changed `index.html` too. Push; both deploys fire (Cloudflare Pages via `deploy.yml`, plus an incidental GitHub Pages mirror).
6. **Verify the live bytes, not the commit.** Fetch cache-busted and assert on the *specific* card — several cards have a `lastRun`, so a bare `grep lastRun` proves nothing:
   ```bash
   curl -s "https://ai-dev-dashboard.pages.dev/data.js?_=$(date +%s)" > /tmp/live.js
   node -e "global.window={};require('/tmp/live.js');const p=window.DASHBOARD_DATA.projects.find(x=>x.id==='bimpossible');console.log(p.audit.lastRun,JSON.stringify(p.audit.counts),p.audit.open.length)"
   ```
   If you check in a browser instead, hard-reload first — a cached page shows old content and you'll report success wrongly.

## Mode: ingest

The report is a **point-in-time snapshot**. Before writing counts:

- **Look for work that post-dates it — `git log` is the check, not just a doc search.** Run `git log --oneline --since="<report date>"` in *every* repo the report covers. A resolution may exist as `audit-resolution_<date>.md`, or as nothing but a commit (real case: a report at 09:29 was fully superseded by a PR at 11:22 the same morning, with no doc). Transcribing the report's summary line without this check publishes numbers that were already wrong.
- Findings marked RESOLVED go in the `history[]` summary, **not** `open[]`.
- `counts` must equal the actual `open[]` composition. Sum them and assert. Reports sometimes dual-tag ("Low/Med"); pick one, make the totals reconcile, and say so if you had to force it.
- **`closedLastRun` counts closures *new to this cycle* only.** Reports routinely re-verify the prior cycle's closures; those were already credited to the earlier `history[]` entry. Counting them again silently inflates the number.
- `history[]`: **prepend**, never clobber. Re-ingesting the *same* date (a resolution superseding that morning's findings) replaces that one entry instead.
- Point `reportPath`/`reportFile` at the newest authoritative doc — the resolution doc if one supersedes the findings doc. `ledgerPath` stays pointed at the run index (`_audit-runs.md`), which is owner-maintained — don't write to it.
- **One report can cover several repos; that doesn't mean several cards.** Findings are tracked under the card whose `audit.open[]` ID-space they already live in. Check where an ID appears today before moving it.
- Refresh `focus`/`nextActions` in the same pass. Leaving them citing closed items contradicts the audit block right beside them.
- `lastRun` feeds `check_audit_freshness.py` → the staleness badge; moving it is what clears the flag.

**Public-disclosure scoping.** The dashboard is public. For a finding that is still unfixed *and* genuinely exploitable, keep it visible and correctly counted, but scope the public `title` to impact class — omit filenames, hostnames, env-var names, counts, and artifact paths. Exploit detail belongs in the gitignored `local/`. Reliability bugs need no scoping.

## Mode: full

Fan out one read-only agent per repo with real activity (see superpowers:dispatching-parallel-agents), then hand-patch. Two failure modes that have actually bitten:

- **Check local-only work.** `git log --branches --not --remotes` and `git status`. Unpushed commits and uncommitted docs are invisible to every automated sync — a whole roadmap sat local-only and got missed.
- **Read the canonical doc, not the summary.** A README's "Status" section may be a stale three-line gloss of a real `ROADMAP.md`. Find the doc that calls itself the source of truth. Don't stop at the first thing you find.

Cards other than `bimpossible` have **no automated writer** for `progress`/`phase`/`focus`/`nextActions` — they only drift. Verify against the repo, never assume.

## The Toolkit tab

`toolkitView()` in `index.html` (~line 2835) is a hand-curated `SECT` catalog — commands, skills, agents, cowork, connectors, scheduled tasks. Nothing syncs it. When tooling is added or renamed, update it here. Sources of truth: `~/.claude/skills/*/SKILL.md`, each repo's `.claude/{skills,commands,agents}`, `schtasks /query`, and `.github/workflows/`. Verify a scheduled entry's real name and trigger before trusting the existing text.

## Red flags

- About to run `Edit` on `data.js` → use the splice machinery.
- Writing counts straight off a report's summary line → check for post-report fixes first.
- "Deploy succeeded" as proof it's live → fetch the bytes.
- Patch built before syncing → rebase your work onto origin and re-apply.
