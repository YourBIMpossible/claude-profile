# claude-profile

Canonical source of truth for Claude Code configuration across machines.
GitHub is authoritative. ZIP and Google Drive are transfer-only fallbacks.
Full design and rationale: docs/superpowers/specs/2026-07-27-multi-machine-claude-profile-design.md

## Install on a new machine

### Normal path (GitHub reachable)

1. Clone this repo to a path with **no spaces in it** - `F:\AI-Dev\claude-profile`,
   `C:\claude-profile`, or similar. `%USERPROFILE%\claude-profile` is fine too, but
   only if your Windows username has no space in it (`C:\Users\First Last\...`
   does not work). See "Why no spaces" below; bootstrap refuses rather than
   configure a machine that would silently misbehave.
2. Run bootstrap from inside the clone:
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1`
3. Restart Claude Code. Verify with `/context` (CLAUDE.md and skills should show as
   loaded) and `/plugin list`.

### ZIP fallback (GitHub not reachable right now)

1. Extract the latest ZIP snapshot (from Google Drive or however it was handed to
   you) to the same kind of location as above.
2. Run bootstrap exactly as in the normal path above - it behaves identically
   either way, there is no separate "zip mode".
3. As soon as this machine has network and git credentials, run `git pull` inside
   the clone once to catch up with GitHub. The remote is already configured,
   because the ZIP includes `.git/`.

## Keeping a machine current

Every Claude Code session attempts a silent `git pull --ff-only` in this repo via a
SessionStart hook. It never blocks session start and fails silently if offline -
it is a best-effort refresh, not a guarantee of freshness. Check
`hooks/last-run.log` for its recent activity, or run `git pull` here yourself at
any time to force a refresh.

## Publishing a new ZIP snapshot

Run `.\export-zip.ps1` from inside the repo. It writes a timestamped ZIP to your
Desktop by default (pass `-OutputDirectory` to change that). Upload that file to
Google Drive if it needs to reach another machine. Never edit a ZIP directly -
regenerate it from the repo instead.

## Why no spaces in the clone path

`~/.claude/CLAUDE.md` is a one-line `@` import pointing at this repo's CLAUDE.md.
Claude Code's `@` import fails *silently* when the path contains a space - the
file is simply never loaded, with no error or warning, and there is no quoting
or escaping syntax that works (anthropics/claude-code#56927, closed as not
planned). A machine in that state looks fully configured while none of your
instructions apply. `bootstrap.ps1` checks for this and refuses up front rather
than leave you with a silently broken setup.

Only the CLAUDE.md import is affected - the skills junction and the generated
JSON configs handle spaces without trouble.

## What this repo does not do

See the design spec's Non-goals section. Short version: no cross-machine memory
sync, no forced project-repo cloning, no Claude-account unification. The Revit
MCP entry in the generated Desktop config is included only if this machine has
Revit's MCP server installed.
