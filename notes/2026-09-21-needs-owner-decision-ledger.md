# needs_owner_decision resolution ledger — 2026-09-21

Read-only revalidation of all 25 rows classified `needs_owner_decision` in
`~\.claude\memory-backups\20260921-phase2a\proposed-manifest.json` (17
Claude-Profile, 8 BIMpossible). No memory or policy file was edited to produce this ledger.
Each row was re-checked against current local evidence (live git log/branches/PRs/worktrees,
current file contents) — not just re-read from its old classification. {repos}\AI-Dev was not
inspected (out of scope per house rule); two rows note where that boundary limited resolution.

Taxonomy: **safely_actionable** (clear low-risk trim/archive, no ambiguity) · **retain_active**
(genuinely still live/unique, stays as-is) · **factual_correction_candidate** (content is now
factually stale — a mechanical fix, not a judgment call) · **requires_owner_decision** (genuine
open question or insufficient local evidence either way).

## Already resolved this session — not re-litigated

**`workspace-addins-branch-cleanup-pending.md`** (Claude-Profile) — closed by the Phase 2A-3 pass
earlier today. Index/body contradiction reconciled as "proven complete"; not re-opened here.

---

## Group A — CI-posture cluster: superseded by `ci-posture-lean-2026-09-11.md`?

Shared question across 5 Claude-Profile rows: does the 2026-09-11 CI-cost cutover (only
`secret-scan.yml` remains live on BIMpossible/AddIns/Workspace; `enforce_admins=false`; no
required status checks) make this file redundant?

| File | Verdict | Why |
|---|---|---|
| `cross-repo-ci-cost-audit-2026-08-19.md` | **safely_actionable** | 3 of its repo-rows (BIMpossible/AddIns/Workspace) fully match the canonical file; live-verified. Nugget to keep: the generic "check protection strict-flag + real PR-trigger counts before assuming a fix applies" methodology, not recorded elsewhere. |
| `gstack-activation-state.md` | **factual_correction_candidate** | Headline "all 4 scanners gating" is false today (only secret-scan remains); the file already carries an accurate inline 2026-09-11 correction, so only the stale headline/title needs fixing. Nugget: 4 durable CI lessons (semgrep test-dir skip, gitleaksignore quoting trap, pinned-tag-≠-upstream-exists, green-run-only-proves-triggered-workflows) aren't recorded in the canonical file. |
| `bimpossible-protected-main-workflow.md` | **retain_active** | PR→squash workflow, stacked-PR rebase gotcha, and reused-branch-after-squash trap are unique, not duplicated in the canonical file. |
| `breach-chain-skill-state.md` | **retain_active** | Only one paragraph is CI-related (already self-corrected); the rest (breach-chain methodology, RE-1/RE-2/ARCH-5, WSR9a) is unrelated unique content. **Contains an unresolved owner item:** CAND-4 — where the live weekly-audit scheduled-task definitions actually live is still unknown. |
| `draft-pr-ready-default.md` | **retain_active** | Verified current: `secret-scan.yml` triggers on `opened/reopened/ready_for_review` only, confirming draft PRs run zero CI. This is a standing approval-boundary rule (agent flips ready, never the owner), not a stale fact — correctly stays visible. |

## Group B — CLAUDE.md-duplicate cluster: trim to pointer?

Shared question across 2 Claude-Profile rows: is this now fully duplicated in
`{profile}\CLAUDE.md`?

| File | Verdict | Why |
|---|---|---|
| `feedback-act-then-report.md` | **safely_actionable** | Current CLAUDE.md lines 6–11 carry the authoritative rule verbatim. Nugget: the rejected 10-line-report-cap history ("produced telegraphic reports, reverted — do not reintroduce") isn't in CLAUDE.md. |
| `feedback-file-link-format.md` | **safely_actionable**, flag prominently | Duplicated in CLAUDE.md line 8 and `hooks/decay_reminder.py` (verbatim). Owner has flagged this recurrence ~10x — trim, but don't archive silently. Nugget: today's 2026-09-21 root-cause pair (scratch-cwd start, then absolute-path "fix" also failing). |

## Group C — Worktree-naming-scheme cluster: does the topology description still match reality?

Shared evidence across 2 BIMpossible rows: live `git worktree list` in {repos}\BIMpossible today
shows an entirely different naming scheme (hash-named `agent-a*`, `w4/*`, `w9/*`, etc.) than
either file describes.

| File | Verdict | Why |
|---|---|---|
| `ci-runtime-cleanup-state-2026-09-02.md` | **safely_actionable** | Every worktree it names is gone; its one durable fact (PR #546 aec-module-identity root cause) is already fully duplicated in `phase4-xdist-promotion-gate.md`. Nothing lost by archiving. |
| `state-tree-ownership-handoff-2026-09-02.md` | **factual_correction_candidate** | Queue watermark is 19+ days and 100+ PRs stale (`c0257d65`/#561 → live `f7bce943`/#678); the specific foreign-repointed worktree it flags no longer exists at all. |

## Group D — P7-SYNC-GOLIVE resolution: is the sync still "owner-deferred, don't nag"?

Shared underlying fact across 2 Claude-Profile rows: P7-SYNC-GOLIVE passed its supervised pilot
(commit `59df1324`, #442, 2026-08-21) and is confirmed PROMOTED landed→live 2026-08-30 per
`{tools}\state\queue.yaml`.

| File | Verdict | Why |
|---|---|---|
| `phase7-step2-shipped.md` | **factual_correction_candidate** | "Owner-deferred" framing is stale — it shipped and is live. |
| `session-2026-08-04-endstate.md` | **factual_correction_candidate** | Same P7-SYNC fact, plus its OD3/OD4 "waiting" items were **RULED BY OWNER 2026-08-18** per queue.yaml — only a mechanical strike-through remains, not an open decision. |

## Ungrouped — retain_active (genuinely live, no action)

| File | Why |
|---|---|
| `duplicate-collection-dependent-bug.md` | AddIns dev guide (committed 2026-08-23) still self-flags "pending live R2026 verification" — no later commit closes it. |
| `familyfixer-phase1-port-state.md` | PRs #23/#25 confirmed merged, but no evidence found of the owner re-test or naming-cleanup step completing. |
| `delivery-receipt-gate-live.md` (BIMpossible) | Live `.githooks/pre-push` and branch protection both confirm the warn-only gate + bypass-logging description exactly as written. |
| `slop-audit-remediation-2026-09-07-unstaged.md` (BIMpossible) | Every "don't touch" branch/PR it lists (Workspace #4/#17/#18/#27/#28/#111, 2 named branches) is still open/present, confirmed live. |
| `ai-dev-extraction-roots.md` (BIMpossible) | All 5 named live roots still exist; nothing contradicts the 2026-08-25 "{repos}\AI-Dev is dead" ruling. |
| `wfa-closure-wave-2026-09-12.md` (BIMpossible) | Closure-wave tips are a correctly-framed historical checkpoint. **Contains an unresolved owner item:** WFA rerun + C2 Key Plan smoke test are owner-only actions, not evidence-closeable. |

## Ungrouped — factual_correction_candidate

| File | What's wrong → current reality |
|---|---|
| `ai-dev-extraction-burnin-state.md` | Both burn-in windows closed 2026-09-05/06, now 15-16 days past. BIMpossible-Workspace commit `70b6115` (2026-09-12) supersedes this framing entirely: explicitly rules "NOT archive-ready," adds 3 new owner decisions (Revit-OCR corpus, Postgres dump, Finance-Dashboard backup) this file never mentions. |
| `aps-surface-isolation-followup.md` | Core deferral (relay routing) confirmed still current via `PARKED.md` (re-verified 2026-09-07, same posture). But: both secondary facts are stale — PR #285's "known rough edges" were fixed 2026-08-07; PR #19 (workspace repo) merged 2026-08-08. |
| `bimpossible-overnight-tool-revisions.md` | "Branch pushed but never merged" is now false — branch `overnight/tool-revisions-2026-06-16` was deleted, but its commits (`1bd1d88`, `f76aaa1`) ARE confirmed ancestors of `main`. |
| `resolution-tooling-session-2026-08-05.md` | At least 1 of its 7 flagged items (host-side backend-test `--noconftest` workaround) is concretely retired (commit `e0baf759`, 2026-09-16). Other 6 aren't tracked in current queue.yaml at all — drift, not individually confirmed closed. |
| `phase4-xdist-promotion-gate.md` (BIMpossible) | Self-flagged "unverified" survival is now resolved: confirmed dead. `ci.yml` deleted (`a3d32a92`), successor `clean-room-ci.yml` also deleted (`2fb01032`, #652), later commit (#669) retired lingering doc claims it still runs. Promotion counter (frozen 1/20) can never advance. Nugget: the aec-module-identity-drift root cause ("never `sys.modules.pop`") is durable, independent of the dead gate. |

## Ungrouped — requires_owner_decision

| File | Open question |
|---|---|
| `bimpossible-site-untracked-project.md` | Factual part is now settled (site confirmed moved out of {repos}\AI-Dev to `{repos}\BIMpossible-Site\site\`, live repo). But whether it's now documented in "CLAUDE.md's project table" can't be resolved locally — no project table exists in any accessible CLAUDE.md (BIMpossible, AddIns, Claude-Profile); if one exists it's likely in the out-of-scope {repos}\AI-Dev\CLAUDE.md. Owner call needed on where that table now lives, if anywhere. |

## Ungrouped — safely_actionable

| File | Why |
|---|---|
| `delivery-control-skills-state.md` (Claude-Profile) | Neither stale unpushed branch exists anymore; both repos' `delivery-control` skills are committed on `main`, clean, and actively maintained (BIMpossible touched via #646 2026-09-12, AddIns via #133 2026-09-11). Safe to mark resolved/archive. |

---

**Totals:** 25/25 rows revalidated. 8 safely_actionable · 8 retain_active (2 carrying an
embedded unresolved owner item) · 8 factual_correction_candidate · 1 requires_owner_decision
(the `bimpossible-site-untracked-project.md` documentation question). No memory or policy
content was modified to produce this ledger.
