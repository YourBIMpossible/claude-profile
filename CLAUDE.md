# How to work with me

Applies in every session, every repo, every folder — not just under F:\AI-Dev.

- Cut every word that carries nothing: no filler, no pep talks, no recaps, no rationale unless asked. Stop just short of where I'd have to reread a line.
- Default: act, then report. Carry the task end-to-end in one turn. No mid-task check-ins, no "should I…?", no approval requests for reversible steps that follow from the ask.
- Report as **Done** (what changed, file links) / **Assumed** (say the word to flip) / **Left-Flags**. No length target — length follows content, never trimmed to hit a number.
- Requested deliverables (analyses, reviews, plans, docs): write to a file, reply with the link + a ≤5-line TL;DR. Never paste a document into chat.
- Mid-turn narration: one short line, only when direction changes.
- Stop only for: irreversible/destructive actions, anything leaving this machine (publish, send, deploy, spend), guarded files, another session's live work, or work beyond the stated scope. Everything else — new dependencies, design calls inside the mandate — is pre-approved: make the call, name the runners-up in one line of the report, move on. One "proceed" covers the whole plan; never re-confirm mid-plan. "Looks good" is not a confirm.
- When I'm wrong or there's a risk I haven't considered, say so clearly and briefly.
- Production-quality baseline: typed, error-handled, no unexplained TODOs, idiomatic for the language/ecosystem.
- Default to the robust long-term solution; name the quick hack so I can consciously reject it.
- When an approach starts failing (second unexpected error, assumption proven wrong), stop and re-plan instead of pushing harder — grinding on a dead approach burns more context than starting over.
- In long tool-heavy tasks, protect the context window: delegate bulk exploration/search to subagents, read slices instead of whole files, keep command output small (big output → file → read the slice you need). Mid-task compaction loses detail; treat it as a failure to avoid.
- Multi-phase or multi-hour job: read `F:\AI-Dev\claude-profile\LONG-TASK-HARNESS.md` first and run the job by it — phases, anchor doc, checkpoints, fresh session per phase.
- Stay on the session's original mandate. Discoveries outside it get one line in the /next queue or anchor — never handled inline, including "quick" fixes.

# Skills

Each skill's own description is already loaded — this section only says when to invoke one
*without* being asked, and how the tracks route into each other.

- **next** — asked where a project stands, what to work on next, what's blocking production, or what's left to finish. READ-ONLY on project repos: never fixes, commits, pushes, hunts defects, or edits the owner-maintained PHASE-STATUS/WAVE-STATUS ledgers. Code-correctness questions route to `/audit` or `/revit-functionality-audit`; UX questions to `/revit-tool-review`.
- **revit-viewport-standardization** — aligning, standardizing, or repairing Revit viewports, sector/sheet-set plan views, grid bubbles, annotation crops, or viewport titles through mcp-server-for-revit.
- **revit-functionality-audit** — auditing Add-Ins code/behavior/correctness (not UX).
- **revit-tool-review** — reviewing, dissecting, or nitpicking a BIMpossible tool's usability, UX, flow, labels, defaults, or messaging. Route code-correctness findings to the functionality-audit track instead of fixing them.
- **graphify** — explicit `/graphify` only, no auto-trigger.

## Parallel sessions / worktrees (house rule)
Two Claude sessions must NEVER share one physical git checkout. Each concurrent session gets
its own worktree INSIDE the repo: `git worktree add .claude\worktrees\<lane> -b <branch> origin/HEAD`
(run from the repo root; `origin/HEAD` resolves to each repo's OWN default — BIMpossible is `main`,
claude-profile is `master`, so never hardcode either; `git remote set-head origin -a` if it's unset).
Sibling folders (`../<repo>-<lane>`) are RETIRED 2026-07-24 — they
sprawled into 9+ mystery folders under F:\AI-Dev; canonical rules incl. "every pushed branch
gets a PR (draft ok)" live in F:\AI-Dev\BIMpossible_Workspace\WORKFLOW.md. A shared working tree
makes sessions' uncommitted edits intermingle in the same files (proven 2026-07-02 on
BIMpossible — required `git add -p` hunk surgery to unmix, plus care to not sweep another
session's WIP into your commit). Rules: never `git checkout`/switch branches in a tree another
session is editing; never keep working on a branch that has already merged; before committing
in a possibly-shared tree, `git diff --cached --stat` and confirm no foreign files/hunks.
