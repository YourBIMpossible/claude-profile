---
name: local-audit
description: "Run the local Ollama second-opinion audit over a repo in one step - deterministic pre-pass, local Qwen classifies, one report. Findings are the LOCAL model's, always HYPOTHESIS. On-demand only. Read-only on the target repo; all traffic stays localhost."
---

# Local audit — whole-repo second opinion, one command

Wraps the manual local-audit steps (run the pre-pass → open Continue → attach a file
→ paste rows → `/audit`, per file) into a single invocation. **You** (Claude) drive
the orchestration; the **local Qwen model** does the actual code review, so the
findings stay decorrelated from Claude's own blind spots. The user just reads the
report.

This is the batch / whole-repo path. For "check this one file while I'm coding," the
Continue panel with `/audit` on an `@file` is still faster — point the user there
instead of invoking this.

## What this is (and is not)

- **Is:** the decorrelated (non-Claude) second opinion, run on demand. A backup to the
  primary Claude-side audits (`/audit`, `/slop-audit`, `/revit-functionality-audit`,
  the Monday sweep), never their replacement.
- **Is not:** authoritative. Every finding is a **HYPOTHESIS** — the local model is
  weaker than Claude and over/under-calls. Present findings as leads to verify, not
  verdicts. Do not silently upgrade them.
- The known-good example: a validation run flagged a CRITICAL on documented-deliberate
  `edit_log` behavior (a false positive). That profile is expected — say so.

## Standing constraints (owner, 2026-08-08 — do not relax)

- **On-demand only. NEVER schedule this** or wire it into a hook/cron. A weak model's
  misses go unnoticed when unattended; the human must read every run.
- **Read-only on the target repo.** The report is written under the local-audit
  `out/` folder, never inside the audited repo.
- **All traffic localhost** (`http://localhost:11434`). No cloud, no paid tools, no
  CI, no PR automation.
- The findings are the local model's. You may format, dedupe, rank, and flag
  malformed output — do **not** substitute your own code judgment for the model's, or
  the decorrelation is gone.

## Run it

1. **Resolve args.** Repo path (default `F:\BIMpossible`; never guess a
   different one — ask). Scope: default **incremental** (`--since` the last local
   report for that repo under `out/`, else the newest `main`/`master` merge base);
   `full` on request. Optional `--max-files N` (default 25).
2. **Confirm Ollama is up:** the runner checks `/api/tags` and exits with a clear
   message if not — if it reports Ollama down, tell the user to start Ollama and stop.
3. **Launch the runner** (it does pre-pass → per-file Qwen calls → report assembly):

   ```bash
   python F:\Claude-Tools\local-audit\local_audit.py "<repo>" [--full] [--since <ref>] [--max-files N]
   ```

   Each flagged file is ~1 min on the local GPU. A full BIMpossible sweep (25 files)
   is ~20 min — **run it in the background** and let the user know it's working; don't
   block. Incremental runs are usually a handful of files.
4. **Present the result** from the report the runner writes
   (`out/<repo>-local-audit-<datetime>.md`): the path, files audited / flagged / skipped
   (surface skipped-for-cap files explicitly — no silent truncation), and the top
   HYPOTHESIS findings in one tight list. Reiterate: second opinion, verify before
   acting.

## Scope discipline

- Covers the **slop class** the pre-pass can discover deterministically: silent
  failures + misleading success counters, over the files it flags. The focused
  security / contract lenses are interactive-only (`/audit-security`,
  `/audit-contract` in Continue on a specific file) — a whole-repo grep can't select
  files for "contract mismatch." If the user wants those, route them to the Continue
  panel, don't force them through this runner.
- Test files are excluded from the model pass (the pre-pass flags them; the runner
  skips them as non-shipped).
- BIMpossible / Add-Ins already have stronger primary lanes. Use this to get the
  *different* set of eyes, not to re-cover what the Monday sweep and `/audit` do.
