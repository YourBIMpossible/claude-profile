# claude-profile

Canonical source of truth for Claude Code configuration across machines.
GitHub is authoritative. ZIP and Google Drive are transfer-only fallbacks.
Full design and rationale: docs/superpowers/specs/2026-07-27-multi-machine-claude-profile-design.md

## Before you install (read this once)

**Prerequisites**: Claude Code installed and signed in; Git installed; GitHub
credentials configured on this machine (for the clone path below).

**Bootstrap replaces these live files.** It backs each up first, as
`<original-name>.pre-profile-backup-<timestamp>`, and never touches the backups
afterward - to undo, delete what bootstrap wrote and rename the backup back.

| Replaced | Becomes |
|---|---|
| `~/.claude/CLAUDE.md` | one-line pointer at this repo |
| `~/.claude/skills` | moved aside, replaced by a junction into this repo |
| `~/.claude/settings.json` | merged with `settings.template.json`: the template's hooks replace the ones it owns, `permissions.additionalDirectories` gains the detected project folders, missing plugin and marketplace entries are added, and every other existing key (permissions rules, `autoMode`, `env`, your own values) is kept. An empty or unparseable file is refused, never replaced; a changed file is backed up first |
| `~/.claude/hooks/*` | pinned copies of `hooks/*.py`, `*.ps1` and `closure_audit_repos.json`; a differing installed copy is backed up first |
| `%APPDATA%\Claude\claude_desktop_config.json` | generated from the template in `mcp/` |

Note that skills are *replaced, not merged*: any skill that exists only on this
machine ends up in the moved-aside backup folder, not in the new junction. If you
want to keep it, commit it to this repo first.

**Close Claude Code and Claude Desktop before running bootstrap** - a running
instance can rewrite `~/.claude/settings.json` after bootstrap generates it, or
hold a lock on `~/.claude/skills`. Re-running bootstrap is safe if that happens.

## Install on a new machine

### Normal path (GitHub reachable)

1. Clone this repo to a path with **no spaces in it** - `F:\Claude-Profile`,
   `C:\claude-profile`, or similar. `%USERPROFILE%\claude-profile` is fine too, but
   only if your Windows username has no space in it (`C:\Users\First Last\...`
   does not work). See "Why no spaces" below; bootstrap refuses rather than
   configure a machine that would silently misbehave.
   `git clone https://github.com/YourBIMpossible/claude-profile.git C:\claude-profile`

   (Private repo - the machine needs to be signed in to GitHub as an account
   with access. `gh auth login` is the easiest way.)
2. Run bootstrap from inside the clone:
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1`
3. Restart **Claude Code** (picks up CLAUDE.md, skills, settings) and **Claude
   Desktop** (picks up the MCP config - it is the only consumer of that file).
   Verify with `/context` (CLAUDE.md and skills should show as loaded) and
   `/plugin list`.

### ZIP fallback (GitHub not reachable right now)

1. Extract the latest ZIP snapshot (from Google Drive or however it was handed to
   you) to the same kind of location as above.
2. Run bootstrap exactly as in the normal path above - it behaves identically
   either way, there is no separate "zip mode".
3. As soon as this machine has network and git credentials, run `git pull` inside
   the extracted copy once to catch up with GitHub. The ZIP includes `.git/`, so
   the remote comes with it - provided the repo it was exported from had an
   `origin` (export-zip warns loudly if it did not, in which case the ZIP is a
   one-time file transfer and you should re-clone from GitHub when you can).

## Keeping a machine current (pulling)

A SessionStart hook attempts a silent `git pull --ff-only` in this repo when a
session starts, resumes, or is cleared. It never blocks session start and fails
quietly if offline - a best-effort refresh, not a guarantee of freshness. Check
`hooks/last-run.log` to see what it actually did, or run `git pull` here yourself
any time to force a refresh.

A SessionStart pull may update the profile after instructions have already been
captured. After policy updates, verify the policy version loaded by the session;
reading the updated file on disk alone does not prove it was loaded at startup.

## Changing your setup (pushing)

This repo is the source of truth, so changes belong here and get committed and
pushed - not made locally on one machine.

- **Skills**: `~/.claude/skills` is a junction into this repo, so editing a skill
  (including through Claude Code itself) edits the repo's working copy directly.
  Commit and push it.
- **Instructions**: edit this repo's `CLAUDE.md`. Do not edit
  `~/.claude/CLAUDE.md` - it is a generated one-line pointer and bootstrap
  rewrites it.
- **Settings**: edit `settings.template.json` here, then re-run `bootstrap.ps1`.
  The template's hooks are authoritative; its other values are defaults that only
  fill keys a machine does not have yet, so a change made in-app (e.g. via
  `/config`) is kept, and a changed template default does not override it.
  MCP servers come from `mcp/claude_desktop_config.template.json`, whose
  `mcpServers` block replaces the installed one.

Then on your other machines, the SessionStart hook (or a manual `git pull`)
picks it up.

## Publishing a new ZIP snapshot

Run `.\export-zip.ps1` from inside a normal clone - not a git worktree, where
`.git` is a file rather than a directory and the snapshot would not be a usable
clone (the script refuses, and says so). It writes a timestamped ZIP to your
Desktop by default; pass `-OutputDirectory` to change that.

**Commit and push first.** The snapshot is built from committed state: untracked
files are omitted entirely, and the script warns if the tree is dirty. It also
warns if the repo has no `origin`, because without one the extracted copy cannot
`git pull` to catch up - it is then only a file transfer.

Upload the ZIP to Google Drive if it needs to reach another machine. Never edit a
ZIP directly - regenerate it from the repo instead.

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
