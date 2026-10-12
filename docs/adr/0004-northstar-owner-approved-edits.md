# 0004 — North-star files: owner-approved edits instead of human-only

- **Status:** Accepted
- **Date:** 2026-10-12
- **Supersedes:** the north-star edit rule in ADR 0003 (Decision 3: "never edit a north-star file yourself by any tool"). ADR 0003's assume-and-proceed nudges stand.

## Context

`protect_paths_gate.py` denied every Write/Edit to `NORTHSTAR*.md`, and `CLAUDE.md` made those files
human-only. Every mission change, including a one-word `status:` flip to pause, close or reopen,
meant the owner opening the file and typing it by hand. The owner asked for the change directly in
chat on 2026-10-12: "I'm very tired of being told I have to go type things in. This is me saying I
approve the North Star change."

## Decision

1. `protect_paths_gate.py` returns `permissionDecision: "ask"` (not deny) for live `NORTHSTAR*.md`
   edits in permission modes where a hook "ask" is known to prompt; it still denies where no prompt
   would reach the owner. `*.draft.md` and `*.template.md` stay free.
2. Claude may edit a north-star file, including its `status:` line, only through that prompt: state in
   one or two plain lines what changes and why, make the edit, the owner approves. Never via Bash,
   never silently, never also asking in chat.
3. `CLAUDE.md`, `LONG-TASK-HARNESS.md`, `northstar_inject.py`, the hooks README, the template and the
   multi-star design doc carry the same wording.

## Alternatives rejected

- Keep human-only (ADR 0003): rejected by the owner's explicit instruction.
- Let Claude edit freely with no prompt: rejected; the prompt is the lock that keeps the mission the
  owner's decision.

## Consequences

- Mission changes cost the owner one click, not an editor session.
- Bash writes to north-star files are still out of bounds; the gate covers Write/Edit only, so that
  part stays an instruction.
- The installed copies in `~/.claude/hooks/` change only when bootstrap runs from a commit that
  contains this change.
