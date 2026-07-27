---
name: converge-review
description: Use before pushing a finished unit of work (a fix, tool, feature, or wave) in the BIMpossible code repo when you want it auto-converged to clean-and-green first. Runs a LOCAL loop of `/code-review high --fix` + `Verify-Local-CI.ps1` until the diff is bug-clean and local CI is green, then hands the push to the human via `Push-And-Verify.ps1` / `Complete-Wave.ps1`. Never commits, never pushes, never calls an external service. Trigger phrases: "converge", "converge-review", "review and fix until green", "get this push-ready".
---

# Converge-Review — local review → fix → verify loop, single human push

This adapts greptile's **greploop** converge pattern to BIMpossible's hard rules.
greploop pushes to the remote on **every** iteration and runs a hosted SaaS
reviewer (Greptile). **This skill does neither.** The whole loop is local; the
only push is one Windows-PowerShell command the human runs at the very end.

The point of the inversion: get the diff fully reviewed, fixed, and green
*locally* — then the human does a single deliberate push, so paid CI runs once
and nothing leaves the machine.

## When to use
- A coherent unit of work (fix / tool / feature / wave) is **finished** and you
  are about to push it from `F:\AI-Dev\BIMpossible`.
- You want the review→fix→verify cycle automated to convergence instead of done
  by hand.

Do **not** use it mid-build, for exploration, or on the workspace/docs repo.

## Preconditions — STOP if any fail
1. You are in the **code** repo `F:\AI-Dev\BIMpossible` (not
   `BIMpossible_Workspace`).
2. `git -C F:\AI-Dev\BIMpossible status --porcelain` shows **only this unit's
   changes** — no unrelated loose work. If the tree mixes in other in-flight
   edits, STOP: converging would conflate them, and the eventual
   `Push-And-Verify` would bundle unrelated changes into one commit. Park the
   unrelated work (branch/stash) or narrow scope first.
3. Branch is `main` (or a deliberate feature branch you intend to PR).
4. Docker stack is up if the backend was touched (the local-CI backend lane
   needs the `docker-backend` image). If Docker is down, scope to
   `-SkipBackend` and lean on GitHub CI for the backend lane.

## The loop (max 5 iterations)
1. Snapshot the baseline: `git -C F:\AI-Dev\BIMpossible diff --stat` — this is
   the surface under review.
2. **Review + fix:** run the `/code-review high --fix` skill on the current diff
   (correctness bugs **and** reuse / simplification / efficiency cleanups,
   applied to the working tree). Use `medium` for a small/low-risk diff, `high`
   for anything load-bearing.
3. **Green-gate:** run `.\Verify-Local-CI.ps1` from the repo root. Scope it to
   the surface you touched: `-SkipFrontend` (backend only) or `-SkipBackend`
   (frontend only). It must end with `LOCAL CI GREEN`.
4. **Adversarial re-review:** review **only** the lines step 2 just changed —
   fixes can introduce new issues. Findings here feed the next iteration.
5. **Converged** when step 2 produces no actionable findings AND step 3 is
   green. Otherwise iterate from step 2.
6. If you reach 5 iterations without converging: STOP. List the residual
   findings honestly — do **not** hand off a non-converged tree as "ready."

## Terminal handoff — human-gated, never automated
Print the summary table, then STOP and give the human the exact push command.
**You do not run it** — `git push` / `commit` / `tag` are Windows-PowerShell,
human-only per standing orders (the SMB mount corrupts `.git/` on sandbox
writes).

- standalone fix / feature / tool → `cd F:\AI-Dev\BIMpossible; .\Push-And-Verify.ps1`
- wave completion → `cd F:\AI-Dev\BIMpossible; .\Complete-Wave.ps1 <N>`

Never suggest `Push-And-Verify.ps1 -AutoCommit` to "converge" a dirty tree: it
runs `git add -A` (sweeps every loose file, including unrelated ones) and lands
a CI-skipped `WIP:` commit. The whole point of preconditions #2 is that the tree
already holds only this unit, so the human's plain push is clean.

```
| field             | value                                  |
|-------------------|----------------------------------------|
| target            | BIMpossible code repo (branch: main)   |
| iterations        | N / 5                                  |
| findings resolved | X                                      |
| residual findings | none | <list>                          |
| local CI          | GREEN | FAILED (<lane>)                 |
| push command      | .\Push-And-Verify.ps1 (human runs)     |
```

## Why this shape, not greploop as-shipped
- **No remote push in the loop.** greploop pushes/shelves every iteration; here
  the loop is local and the single push is human-run — respects the
  PowerShell-only-push rule and "only push when the unit is done."
- **No external SaaS.** Uses your own `/code-review` + the local ephemeral-DB
  `Verify-Local-CI.ps1`. Nothing leaves the machine, unlike greploop streaming
  the repo to Greptile's cloud (a data-governance decision not yet made).
- **CI spend stays 1×.** Convergence is measured against local CI; paid GitHub
  Actions (~$0.006/min × ~150 min) runs once on the final human push, not once
  per iteration.
- **Cap + honesty.** Max 5 iterations; if capped, surface the residual findings
  rather than implying "all clean."
