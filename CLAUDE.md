# next
- **next** (`~/.claude/skills/next/SKILL.md`) - "where is this at / what do I do now". Cross-repo portfolio board + ranked, evidence-verified production queue from the durable item store at `F:\AI-Dev\.tools\state`. Ranks by critical path (transitive `unblocks` cone). Modes: bare = board · `<target>` = drill · `<target> sync` = reconcile · `<target> init` = first derivation · `all deep` = re-derive from scratch. Trigger: `/next [target] [sync|init|deep]`
When the user types `/next`, invoke the Skill tool with `skill: "next"` before doing anything else.
Auto-trigger: also invoke it unprompted when the user asks where a project stands, what to work on next, what's blocking production, or what's left to finish — even if the skill isn't named. It is READ-ONLY on project repos: it never fixes, commits, pushes, hunts defects, or edits the owner-maintained PHASE-STATUS/WAVE-STATUS ledgers. Code-correctness questions route to `/audit` or `/revit-functionality-audit`; UX questions to `/revit-tool-review`.

# graphify
- **graphify** (`~/.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, invoke the Skill tool with `skill: "graphify"` before doing anything else.

# revit-viewport-standardization
- **revit-viewport-standardization** (`~/.claude/skills/revit-viewport-standardization/SKILL.md`) - standardize Revit plan viewports to per-sector template views via the Revit MCP: box alignment, grid bubbles, annotation crop, titles (in that order). Trigger: `/revit-viewport-standardization`
When the user types `/revit-viewport-standardization`, invoke the Skill tool with `skill: "revit-viewport-standardization"` before doing anything else.
Auto-trigger: also invoke it unprompted whenever a task involves aligning, standardizing, or repairing Revit viewports, sector/sheet-set plan views, grid bubbles, annotation crops, or viewport titles through mcp-server-for-revit — even if the skill isn't named.

# revit-functionality-audit
- **revit-functionality-audit** (`~/.claude/skills/revit-functionality-audit/SKILL.md`) - code-correctness audit for the Revit ADD-INS repo (F:\AI-Dev\Add-Ins) — the Add-Ins counterpart of /audit (which covers only the BIMpossible web repo). Incremental since the last report in audits/ by default; "complete" = full. Build both TFMs first; findings are hypotheses until verified; hunts "reports success, quietly did nothing". Trigger: `/revit-functionality-audit [complete]`
When the user types `/revit-functionality-audit`, invoke the Skill tool with `skill: "revit-functionality-audit"` before doing anything else.
Auto-trigger: also invoke it unprompted when the user asks to audit the Add-Ins code/behavior/correctness (not UX). UX/usability requests go to revit-tool-review instead; that skill routes its CODE findings here.

# revit-tool-review
- **revit-tool-review** (`~/.claude/skills/revit-tool-review/SKILL.md`) - ruthless usability review of ONE BIMpossible Revit tool at a time (the second audit track — user experience, not code correctness): 10-section checklist, live-walkthrough stall log, findings as Critical/Medium/Polish with behavior-level improvements only. Trigger: `/revit-tool-review <tool name>`
When the user types `/revit-tool-review`, invoke the Skill tool with `skill: "revit-tool-review"` before doing anything else.
Auto-trigger: also invoke it unprompted whenever the user asks to review, audit, dissect, or nitpick a BIMpossible tool's usability, UX, flow, labels, defaults, or messaging — even if the skill isn't named. Route code-correctness findings to the functionality-audit track instead of fixing them.

## Superpowers gating
Only invoke brainstorming/writing-plans skills for new features, new tools, or tasks
with genuinely ambiguous scope. For bug fixes, tweaks, refactors of known code, and
tasks under ~30 minutes: skip the planning chain and execute directly.

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
