# 0003 — Disable Superpowers persistently; one stateless file-path rule; NorthStar nudge follows CLAUDE.md

- **Status:** Accepted
- **Date:** 2026-10-11
- **Supersedes:** — (no ADR; supersedes the machine-local Superpowers uninstall recorded in `notes/2026-08-06-harness-tuning.md` and the `context-slimdown-2026-08-06` memory)

## Context

The 2026-10-11 context-engineering audit
(`{reports}\2026-10-11-context-engineering-claude5-audit.md`, findings 1, 3, 4, 8)
found three sets of startup instructions contradicting the global `CLAUDE.md`:

- **Superpowers.** Its SessionStart hook injects an `<EXTREMELY_IMPORTANT>` mandate to invoke skills
  before any action, including clarifying questions. That conflicts with "act, then report" and the
  stop list. The 2026-08-06 fix uninstalled it from `~/.claude/settings.json` only. The template still
  had `true`, and `bootstrap.ps1` `Merge-ProfileSettings` adds missing `enabledPlugins` keys from the
  template, so a later bootstrap brought it back. Backups confirm this: the key is absent through
  `settings.json.pre-profile-backup-20260929-213342` and `true` from `pre-A-restore-20261001-081615` on.
- **File links.** `CLAUDE.md` and `hooks/decay_reminder.py` each carried their own wording of a link
  rule that depended on the current working directory. Its correctness turned on state that changes
  mid-session, and it required `SendUserFile` everywhere, though headless CLI sessions don't have that
  tool (verified 2026-10-11 by a `claude -p` probe).
- **NorthStar nudge.** With no active star, `hooks/northstar_inject.py` told the agent to ask the owner
  what to build and draft a mission. `CLAUDE.md` says to state the assumed milestone and proceed.

The audit doesn't show that Superpowers caused any particular pause. It shows a standing contradiction
loaded every session.

## Decision

1. Set `superpowers@superpowers-marketplace` to `false` in both `settings.template.json` and the live
   `~/.claude/settings.json`. An explicit `false` is an existing key, so bootstrap's merge keeps it.
   No replacement workflow plugin. Other plugins are unchanged.
2. `CLAUDE.md` owns the only file-reference rule: always the full absolute path as plain text, never a
   markdown file link. Use `SendUserFile` for deliverables only when the tool is available.
   `decay_reminder.py` drops its copy and keeps its two reporting reminders.
3. The no-star and inactive-star nudges in `northstar_inject.py` now say to state the assumed milestone
   and proceed within the request's scope. They add that an assumed milestone grants no authority
   beyond the request, and to escalate consequential ambiguity. Active-star injection keeps its rule
   against editing a north-star file, now explicit that it covers every tool (Bash included), since
   `protect_paths_gate.py` blocks only Write/Edit.

## Alternatives considered

- Uninstall Superpowers machine-locally again: rejected, since bootstrap re-adds a missing key (the 2026-08-06 failure).
- Remove the key from the template: rejected, since existing live `true` values win and the template wouldn't record the decision.
- Keep the cwd-conditional link rule and add a Stop-hook lint: rejected, since the task excluded new lints and the rule stays stateful.
- Delete the NorthStar no-star nudge entirely: rejected, since a short pointer to the assumed-milestone rule is still useful at SessionStart.

## Consequences

- Superpowers skills (brainstorming, systematic-debugging, etc.) no longer load. Re-enabling one is a new ADR.
- `pr-review-toolkit` and `plugin-dev`, also "uninstalled" in 2026-08-06 and re-added by bootstrap, stay enabled. Their status is an open owner call that this ADR doesn't decide.
- Rule text now lives in `CLAUDE.md` alone. Hooks may point to it but must not restate it (tests in `hooks/tests/test_decay_reminder.py` and `test_northstar_inject.py` pin this).
- Installed hook copies change only when bootstrap runs from a commit that contains these edits.
