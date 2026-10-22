# Audit — Add-Ins\CLAUDE.md

Date: 2026-08-07 · Auditor: Claude (harness-tuning session) · Target: `{repos}\AI-Dev\Add-Ins\CLAUDE.md`
Size: 179 lines / 14.6 KB · Read-only audit — **no edits made to the Add-Ins repo.**
Method matches the BIMpossible audit (2026-08-06): every section tested against *would a session that
violates this line produce broken or unsafe work?*

## Verdict — this is a different problem from BIMpossible

BIMpossible's CLAUDE.md was accurate but badly composed: half of it was completed-status
reporting that belonged in linked docs. Add-Ins is the reverse. It is **well composed and
partly wrong.** Very little here is filler — the Definition-of-Done section alone is worth
more than most of what I cut from BIMpossible. The defects are factual: **one broken link,
three internal contradictions, one unverifiable number, and one dead section.**

So the headline recommendation is not "cut it." It is **fix five things, then trim ~40 lines**
(179 → ~135). If only one thing gets done, do the broken link — a section's authoritative
reference points at a file that was never committed.

Ranked by cost-if-ignored, not by line count:

| # | Defect | Why it costs something |
|---|---|---|
| 1 | `docs/ui-workflow-stack-handoff.md` **is not reachable from `main`** | It is the "full stack + scope boundaries" reference for the 5-step UI workflow. **Corrected 2026-08-07 during execution:** my first pass searched the filesystem only and concluded "never committed." It *was* committed — `3bb29cf`, on the branch `wip/main-preexisting-2026-07-26`, which is pushed to origin but never merged. So the fix is a branch-qualified pointer (`git show wip/main-preexisting-2026-07-26:docs/ui-workflow-stack-handoff.md`), not a dead-link removal, and nothing needs promoting out of `_scratch`. |
| 2 | Project-structure tree (142–153) contradicts the ship-set section (14–44) | Line 149 says `Deploy-Local.ps1 — build + xcopy deploy for BIMpossible.RevitLink`. Lines 19–21 say the default builds and deploys all seven and "there is no RevitLink-only default any more." I read the script: `$SHIP_SET` at line 32 lists every discipline and `-RevitLinkOnly` (line 39) is an opt-in switch. **The ship-set section is right; the tree is stale.** A session reading top-to-bottom hits the correct version first and the wrong one last. |
| 3 | Same tree says `BIMpossible.RevitLink/ (git init pending)`; line 50 says "git per-project" | Neither is true. `{repos}\AI-Dev\Add-Ins` is one git repo on `main` with four live worktrees under `.claude\worktrees\`. Line 84 of this same file already states the correct rule ("One canonical home"). |
| 4 | "Current state: 45 occurrences across 21 files" (77) is not reproducible | See the count section below. |
| 5 | Superpowers gating (107–110) is dead | Verbatim the section I deleted from BIMpossible in PR #295. `superpowers`, `pr-review-toolkit`, and `plugin-dev` are absent from `~/.claude/plugins/installed_plugins.json`. |

## The firm-literal count — why I am flagging it rather than correcting it

Line 77 claims **45 occurrences across 21 files**. I could not reproduce that under any obvious
counting rule. Numbers below are from `git grep` (tracked files only, so `bin/`, `obj/`, and the
worktrees are excluded by construction) — this supersedes the filesystem grep I ran first, which
was off by one file and one occurrence on the bottom row:

| Slice | Occurrences | Files |
|---|---|---|
| Everything | 264 | 42 |
| Minus `*Tests/` | 59 | — |
| Minus tests **and** the four named exceptions | **41** | **13** |
| *File's claim* | *45* | *21* |

The bottom row is close to the claim on occurrences (41 vs 45) and about half on files (13 vs 21),
so the number was probably measured roughly this way and has drifted. I am **not** asserting the
rule is being violated 6× over — that would be reading the top row as if it were the claim. The
test suites alone hold 205 of the 264, and `FamilyFixer` is a legitimate exception.

The real finding is that **nobody can tell which row is the true one**, because Add-Ins has no
mechanical gate. BIMpossible solved this exact problem with `.semgrep/bim-firm-literal.yml` +
`firm-literal-baseline.txt` + `check-firm-literal-baseline.mjs`, and its CLAUDE.md now points at
the baseline file instead of carrying a prose count. Add-Ins has the same rule, the same four
exception carve-outs, and no ratchet.

Two options, in preference order:
1. **Port the ratchet** (a real change, not a doc edit — separate job, C#/PowerShell rather than
   semgrep-on-Python). Then delete the prose number entirely.
2. **Doc-only interim:** replace the count with the counting method and the current
   non-exception/non-test figure, dated. Honest, but it drifts again.

Concentration is useful either way — of the 41, **21 are in `Shared/Sync/SyncConfig.cs`** and the
remaining 20 are spread one-to-four apiece across 12 files.

**Edge case worth a policy decision (not fixed):** `Commands/FamilyFixerWindow.cs` is FamilyFixer
code, but exception #1 is written as paths (`FamilyFixer/*`, `Shared/FamilyFixer/*`) and this file
is in `Commands/`. It therefore counts as coupling under the list as written. Either widen the
exception or move the file; I did neither, since that is a policy change.

## Section-by-section

| Lines | Section | Verdict | Note |
|---|---|---|---|
| 1–2 | Title + Inherits | **KEEP** | Both `SYSTEM-RULES.md` and `WORKING-STYLE.md` verified present. Same caveat as BIMpossible: WORKING-STYLE carries the optimization priority the global rules do not. |
| 4–5 | Overview | **KEEP** | 2 lines. |
| 7–12 | Active projects | **KEEP, tighten** | Genuine orientation, and the Core co-load deploy caveat in line 11 is load-bearing. Line 8's RevitAddin_Z ban: the folder is gone from the working tree and has no git history here, so it bans something that cannot be found. Shrink to a footnote or drop. |
| 14–44 | Ship set + `Deploy-Local.ps1` | **KEEP rules, DROP status** | 19–33 (the switches and the net8-only skip) and 35–39 (the `bin\x64` path trap and the `$year` case-collision trap) are exactly the kind of expensive knowledge that belongs here — I verified both against the script. Lines 41–44 "Live status 2026-07-26" are status. |
| 46–50 | Shared build rules | **KEEP 4, FIX 1** | "Close Revit before building" earns its place. Line 50's "git per-project" is wrong — see defect #3. |
| 52–80 | Firm-parameter neutrality | **KEEP rule + exceptions, FIX the number** | The exhaustive four-item exception list is the most valuable part and is genuinely Add-Ins-specific — every path in it resolves. Lines 77–80 are the unverifiable status. |
| 82–105 | Repo structure, worktrees & PRs | **TRIM 24→~10** | Triple-duplicated: global `claude-profile\CLAUDE.md`, `WORKFLOW.md` (named here as canonical), and this file. **Do not blanket-cut** — lines 103–105 (Deploy-Local writes to a *shared* `%APPDATA%` Revit Addins folder, so parallel sessions clobber each other) are unique to this repo and appear nowhere else. Keep those; let the generic worktree/PR rules compress to a pointer. |
| 107–110 | Superpowers gating | **DELETE — dead** | Defect #5. |
| 112–120 | Definition of Done | **KEEP, near-verbatim** | Best section in either repo's CLAUDE.md. The 355px `ToolTipImage` limit cost 16 tools' worth of unreadable tooltips before it was written down; the "missing image is fine, oversized is strictly worse" framing is what makes it stick. Tighten prose at most. |
| 122–140 | Post-audit UI workflow | **KEEP order, FIX link** | The five-stage ordering and the "do not let Taste invent workflow logic" boundary are real. `design-taste-frontend` / `taste-skill` is **correct as written** — `taste-skill/SKILL.md` self-declares `name: design-taste-frontend`, so naming both is right, not a typo. `revit-tool-review`, `ui-ux-pro-max`, and `revit-functionality-audit` all resolve. Only the handoff-doc link is broken. |
| 142–153 | Project structure tree | **DELETE or REBUILD** | Defects #2 and #3 both live here. It shows one of eight-plus projects and omits ModelQA.Core, the six disciplines, the test suites, `How to Guides\`, and `Shared\`. A tree that is wrong is worse than no tree; the Active-projects section above already covers this correctly. |
| 155–171 | RevitLink structure | **TRIM** | Still broadly accurate — `PipeServer.cs` and `Ribbon/TradesRibbon.cs` both verified present. Useful orientation for the biggest project; tighten rather than cut. |
| 173–174 | Namespace conventions | **FOLD or DELETE** | A heading plus one line covering one project out of eight. Either complete it or move the line into Active projects. |
| 176–178 | Decision log | **KEEP** | 3 lines, two pointers. |

## Path ambiguity (minor, worth a pass)

The firm-neutrality section writes bare paths — `Shared/FirmParams.cs`, `RoomData/SpfReader.cs`,
`Conformance/DeliverableParameterInstaller.cs`, `Shared/Visibility/FamilyDisciplineClassifier.cs`.
All four resolve, but all four are under `BIMpossible.RevitLink/`, and the repo now holds eight-plus
projects — several with their own `Shared/`. Prefixing them costs one word each and removes a real
"which project?" ambiguity.

## Cross-file duplication

- **Worktree/PR rules appear three times** — here (82–105), the global `claude-profile\CLAUDE.md`,
  and `BIMpossible_Workspace\WORKFLOW.md` (which this section itself names as canonical). Same
  pattern found in BIMpossible. The Add-Ins-only `%APPDATA%` clobber rule is the part worth keeping.
- **Superpowers gating is byte-identical** to the section deleted from BIMpossible in PR #295.
- **Firm-parameter neutrality** is stated in both repos' CLAUDE.md. That duplication is correct —
  the two implementations differ (`param_aliases.py` / `clientRules.ts` vs `Shared/FirmParams.cs`)
  and each file states its own. Add-Ins already links to BIMpossible's as the companion rule.

## What was not changed

Nothing. This is analysis only; the Add-Ins repo was not modified, no branch and no commit.
Executing it is a separate job from an Add-Ins worktree so the edit lands on a branch with a PR.

Two items inside it are **not** doc edits and should not ride along in that PR: committing
`ui-workflow-stack-handoff.md` from the scratch stash (needs a read of the file first — it has
never been reviewed in-repo), and porting the firm-literal ratchet.
