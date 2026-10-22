# 0005 — Machine-agnostic profile: named roots, one machine config, hooks run from the clone

- **Status:** Proposed — accepted when the owner merges the PR that adds it
- **Date:** 2026-10-22
- **Supersedes:** the installation rule C1 in `hooks/README.md` (2026-08-08: "executable hooks are
  installed only as pinned, copied files under `~/.claude/hooks`").

## Context

Committed files named machine paths (`F:\Claude-Tools`, `C:\src\...`, a user profile folder): about
90 files across CLAUDE.md, skills, hooks, tests and notes. Each machine with a different layout
carried local "repath" commits that conflicted on every pull, bootstrap crashed on a machine
without an F: drive (`Join-Path` on a missing drive), 47 tests failed on such a machine, and a Linux
port had to fork per-platform defaults. The owner asked on 2026-10-22 for the repos to be
machine-agnostic so that drive layout is never a blocker again, for any number of users, and for
hook and settings changes to reach every machine at its next session start without an installer.

## Decision

1. **No machine path in any committed file.** Paths derive from the clone's own location
   (`{profile}`), the home folder (`~`), or one local machine config,
   `~/.claude/profile-machine.json` (`tools/machine_config.py`), which bootstrap writes with
   auto-detected defaults: `{repos}`, `{tools}`, `{reports}`, `{mailbox}` (user-defined,
   optional), the Python name, extra session folders. CLAUDE.md and skills name these roots;
   the session-start hook prints their values into every session.
2. **Guard.** `.github/scripts/check_machine_paths.py` + `.github/workflows/machine-paths.yml`
   fail a PR that adds a drive-letter, Git-Bash drive, user-home or UNC path; exemptions live in
   `.machine-paths-allow.json` with a reason each, and a stale exemption fails too. The same two
   files guard claude-tools and can be copied into any repo.
3. **Hooks run from the clone** (`__PROFILE_ROOT__/hooks`, `__PROFILE_ROOT__/vendor`), not from
   copies under `~/.claude/hooks`, which now holds only runtime state.
4. **One installer, all platforms.** `bootstrap.py` (Windows, Linux, macOS; `bootstrap.ps1` is a
   wrapper that adds REA). Its settings merge is `tools/profile_setup.py`, which the
   session-start hook (`hooks/session_start.py`) re-applies after `git pull --ff-only`, so a merge
   is the rollout.

## Consequences

- C1's protection (a branch switch or pull must not silently change code that runs on every
  prompt) is now: the clone follows only the merged default branch via `--ff-only`; feature work
  happens in worktrees (house rule); the session-start hook warns in-session when the clone is on
  another branch or has uncommitted hook/tool changes. A machine owner who checks out a branch in
  the clone itself runs that branch's hooks — that is the accepted trade for no installer runs.
- Installer still needed: a new machine, a moved clone, a changed root, a broken skills link or
  CLAUDE.md stub, REA, and **once** on each machine bootstrapped before this change (its
  settings still point at the old pinned copies, which cannot update themselves).
- Byte-identical upstream pins (`vendor/**`, `hooks/Show-AuditAlerts.ps1`) keep their upstream
  bytes, machine-path text included; they are exempted in the guard and fixed upstream, then
  re-pinned.
- Runners-up: keep pinned copies and re-copy them at session start (keeps C1 fully, but two
  copies of every hook and still an install step per change); `@import` a generated
  `machine.md` for the roots (silently does nothing when missing); environment variables for the
  roots (invisible to the model unless printed, and empty variables expand silently in shells).
