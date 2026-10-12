# North Star v3 — multi-mission design (2026-08-15)

Scope-governor v3. Replaces v2's single hard-coded `NORTHSTAR.md` with a set of
concurrent, individually lifecycled north stars. Implemented and installed the
same day; this doc records the diagnosis, the model, and the trade-offs.

## Diagnosis — why v2 bottlenecked on one file

v2 conflated four distinct things into one filename:

1. **Identity = the literal name `NORTHSTAR.md`.** One mission per directory,
   period. A second workflow had nowhere to put its mission except a different
   folder/worktree — the "new lane" workaround was baked into the inject text
   because the model had no other answer.
2. **Activation = existence.** The only way to deactivate a mission was to
   rename or delete the file — which the gate itself blocks Claude from doing,
   and which destroys the file-as-history.
3. **Selection = closest-wins walk-up.** Correct for lane-shadowing, but it
   guaranteed at most ONE mission could ever be in force, even when two
   initiatives legitimately run side by side in the same tree.
4. **Protection = exact basename match** (`name == "northstar.md"`). The gate
   couldn't protect a second mission file even if you created one.

Net effect: open/pivot/close/switch all required renames of a single contested
file, i.e. exactly the friction the requirement forbids.

## The v3 model

**A north star is any `NORTHSTAR.md` or `NORTHSTAR.<slug>.md` at a project
level.** One file per workflow/initiative. No new directory, no manifest, no
registry — the filename pattern IS the discovery mechanism, so legacy
`NORTHSTAR.md` files keep working untouched.

**Lifecycle lives inside the file** as one human-edited line near the top
(scanned in the first 20 lines, `---` frontmatter or bare):

```
status: active   (or no status line at all)  -> live, injected every turn
status: paused   (also: inactive)            -> kept, not injected
status: closed   (also: done, archived)      -> finished, kept as history
```

Open = draft saved as the real file; pause/close/switch = one word edited. Nothing moves,
nothing renames, nothing fights over a canonical path.

> **Superseded 2026-10-10 (approval model):** the human no longer does these edits by
> hand. Claude makes the edit (or saves the draft as the real file) and the owner
> approves the prompt `protect_paths_gate` raises; drafts stay free. Wherever this
> document says "the human renames/edits", read "the owner approves". The original text
> below is kept as history.

**Injection (northstar_inject.py, advisory):** walk up from cwd (unchanged, 6
levels); the **closest directory holding at least one ACTIVE star wins, and all
of its active stars inject together**, each under its own filename header,
plus the (unchanged) three-doors routing rule. Inactive stars in the winning
directory are named in one line as "not injected — do not work toward them."
Closest-wins is preserved, so a worktree lane's own star still shadows the
portfolio star.

- No active star anywhere + inactive ones exist → SessionStart-only note
  listing them, so resuming a paused workflow is one human edit away.
- No stars at all → the existing SessionStart draft nudge (now slug-aware).
- The v2 "STOP: a mission already exists" text is gone — a second mission is
  no longer a conflict. Claude drafts `NORTHSTAR.<slug>.draft.md`; the human
  renames to activate. Replacement works the same way.

**Protection (protect_paths_gate.py, fail-closed control):** basename matches
`northstar.md` or `northstar.*.md` → blocked, with two exemptions:

- `*.draft.md` — the one northstar-named thing Claude may write (the propose
  channel; the human's rename is the lock/activate act).
- `*.template.md` — documentation scaffolding, same reasoning as the existing
  `.env.template` exemption. (Found live: the first gate version blocked
  `templates/NORTHSTAR.template.md` during this very change.)

Everything else about the gate (fail-closed contract, env/key/credential
rules, Write|Edit-only scope) is untouched. settings.json needed no change —
same pinned paths, same events.

## What changed where

| File | Change |
|---|---|
| `hooks/northstar_inject.py` | v3 rewrite: multi-star discovery, status parsing, multi-block injection, all-inactive SessionStart note, slug-aware setup nudge |
| `hooks/protect_paths_gate.py` | basename rule `northstar.md` → `northstar(.*)?.md` minus `.draft.md`/`.template.md`; docstring |
| `hooks/README.md` | inventory + scope-honesty sections updated to v3 |
| `LONG-TASK-HARNESS.md` | autonomous-lane guardrail paragraph generalized to the mission set |
| `templates/NORTHSTAR.template.md` | multi-star naming + status-line lifecycle documented; explicit `status: active` line added |
| `templates/WORKLOG.template.md` | plain-box pointer un-hardcoded |
| `CLAUDE.md` (stop list) | "locked mission" gate generalized to `NORTHSTAR*.md` incl. status lines |
| `~/.claude/hooks/` | both hooks reinstalled per the C1 pinned-copy rule, byte-verified |

Verified by 25 fixture checks (scratchpad `test_northstar_v3.py`): gate
block/allow matrix incl. exemptions and fail-closed on garbage input; inject
multi-star, paused exclusion, draft/template exclusion, walk-up, lane
shadowing, all-inactive note, empty-project nudge, per-turn silence, UTF-8.

## Trade-offs called out

- **Status line is trusted content inside a locked file.** The gate blocks
  Write/Edit on the whole file, so Claude cannot flip `status:` through the
  guarded channel — but the Bash channel (`Set-Content`, redirection) remains
  uncovered, exactly as it always was for the mission text itself. No new
  exposure, but pausing a mission is now as sensitive as rewriting it.
- **Unparseable/missing status defaults to ACTIVE.** Over-injecting a mission
  is safer than silently dropping one; the cost is that a typo like
  `status: pasued` keeps a star live. The injected block names every active
  star, so the human sees it.
- **Draft exemption is a naming convention, not a semantic check.** Claude
  could write anything into a `.draft.md`; the human's rename is the review
  gate. Same trust model as v2, now stated explicitly.
- **`*.template.md` exemption** means a file literally named
  `NORTHSTAR.template.md` at a repo root is editable and never injected — if
  someone ever wants a mission called "template", pick another slug.
- **Closest-wins still means a lane cannot inherit the parent's stars while
  adding its own.** Chosen deliberately (shadowing is the point of lanes); if
  both are wanted, put both files at the same level.
- **Rejected: a `northstars/` directory or an ACTIVE manifest file.** Both add
  a second thing to keep consistent and a "magic location"; the
  pattern-plus-status model needs neither and was the smallest change that
  removed the limit.
