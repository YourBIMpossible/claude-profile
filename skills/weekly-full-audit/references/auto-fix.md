# BIMpossible Audit — Auto-Fix Pass

Runs after the weekly report is written (Step 4 of the audit skill). Applies safe,
surgical fixes to findings from the current run, verifies CI, and appends a fix
summary to the report. This pass is write-enabled — the "read-only on code" rule
from the audit steps does NOT apply here.

---

## Hard Boundaries — never cross these regardless of finding severity

These are enforced BEFORE any fix attempt. If a finding touches these, move it to
the Human-Review queue and do not touch the file.

| Always gate to human | Reason |
|---|---|
| `.env` / `backend/guard.py` | Human-only by workspace standing order |
| `database/alembic/versions/**` | Schema migrations require human review |
| Any Autodesk APS write endpoint (POST/PUT/PATCH/DELETE to APS APIs) | Requires explicit human approval |
| Auth flow redesign or OAuth token handling | Security-critical, too high risk |
| Files with >100 lines of change needed | Too large for unattended fix |

---

## Step AF-0 — Open a commit-capable scratch worktree (HYG-42)

The verification lane needs a *lane*: `Verify-Local-CI.ps1 -BaseRef origin/main` compares
HEAD against `origin/main` and refuses a zero-lane run on a clean `main` checkout, so an
auto-fix pass that edits the main checkout and never commits can never verify (audit
2026-10-05, HYG-42; owner decision: scratch-worktree commits, not an uncommitted-diff mode).

From `F:\BIMpossible`:

```powershell
git worktree add .claude\worktrees\auto-fix -b claude/auto-fix-$(Get-Date -Format yyyy-MM-dd) origin/main
```

Every fix below is applied and **committed** in that worktree (one commit per finding,
`fix(<ID>): …`). Record the baseline SHA (`git rev-parse HEAD` before the first fix) in the
report. Git is the recovery mechanism; this pass writes **no** `_backups\` copies (that
mechanism is retired — see the audit-resolution skill, Step 0). A finished pass leaves the
worktree with its lane branch pushed and a PR opened; the owner merges or discards it.

---

## Step AF-1 — Triage findings from the current report

Read the report written in Step 4. For every finding:

1. Check the hard boundaries table above. If it matches any row → **Human-Review queue**.
2. If `auto_fixable` is not stated, apply this rule: a finding is auto-fixable if the
   fix is a targeted change under ~25 lines that does not redesign a flow or change a
   public API signature.
3. Severity gate: **Critical and High findings always go to Human-Review** unless the fix
   is a one-liner (e.g. adding a missing null check, adding `rel="noopener noreferrer"`).
   Do not auto-fix architectural issues, auth flows, or data-layer changes at High severity.

Output of this step: two lists — **Auto-Fix queue** and **Human-Review queue**.

---

## Step AF-2 — Apply fixes (sequential, one file at a time)

Process the Auto-Fix queue **sequentially** — never two agents editing the same
file concurrently.

For each finding:

1. Read the target file. Confirm the issue still exists at the stated location.
2. Apply only the change described. Do not refactor surrounding code, rename
   variables, or clean up unrelated issues in the same file.
3. After applying, re-read the changed block to confirm it looks correct.
4. Commit the fix in the AF-0 worktree: `git commit -am "fix(<ID>): <summary>"`. Prior
   versions are `git log -p --follow -- <path>`; no backup copies are written.
5. Record: finding ID, file changed, lines changed, commit SHA, summary of change.

If the fix would require more than 25 lines of change → move to Human-Review,
do not attempt it.

---

## Step AF-3 — Verify CI (backend lane)

Run the backend CI lane **from the AF-0 worktree** (its committed lane branch is what the
verifier diffs against `origin/main`) to confirm nothing was broken:

```powershell
powershell.exe -NonInteractive -ExecutionPolicy Bypass `
  -File "F:\BIMpossible\.claude\worktrees\auto-fix\Verify-Local-CI.ps1" -BaseRef origin/main -SkipFrontend
```

Wait for completion (typically 3–6 minutes). Capture exit code and any failures.

- **Exit 0** → CI green. Proceed.
- **Non-zero exit** → CI failed. Do NOT proceed with further fixes. Revert every fix
  commit of this run in the worktree (`git revert --no-edit <baseline>..HEAD`, or
  `git checkout <baseline> -- <path>` per file and commit). Record which specific test
  failed. Move all fixes from this run to the Human-Review queue with note
  "reverted — CI failure after fix".

---

## Step AF-4 — Append fix summary to the report

Append this section to the bottom of the weekly report file written in Step 4:

```markdown
---

## Auto-Fix Pass — Summary

**Run date/time:** <ISO timestamp>
**CI result:** GREEN / FAILED (exit <code>)

### Applied fixes (<N> total)

| Finding ID | File | Summary |
|---|---|---|
| ... | ... | ... |

### Human-review queue (<N> items)

| Finding ID | Severity | File | Reason not auto-fixed |
|---|---|---|---|
| ... | ... | ... | ... |

### CI failures (if any)

<list test failures here, or "none">
```

---

## Step AF-5 — Update the Gmail draft

If the Gmail draft from Step 4 (Email) is still in Drafts (not sent), update its
body to include the Auto-Fix Pass summary table. If the draft was already sent or cannot
be found, create a new draft:

- to: `z@yourbimpossible.com`
- subject: `BIMpossible Audit + Auto-Fix — <YYYY-MM-DD>`
- body: the executive summary from the report + the Auto-Fix Pass summary table
- Note in the body: "N fixes applied, CI <GREEN/FAILED>, N items need human review"

---

## What this pass does NOT do

- Does **not** run `git commit`, `git push`, or any git write operation.
  Committing and pushing is always human-triggered via `Push-And-Verify.ps1`.
- Does **not** run the frontend CI lane (too slow for unattended; run manually if needed).
- Does **not** fix the same finding twice — if a finding is already resolved when
  the file is read, record it as "already resolved" and move on.
- Does **not** touch anything in `99_Archive\`.
