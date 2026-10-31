# claude-profile

Canonical source of truth for Claude Code configuration across machines.
GitHub is authoritative. ZIP and Google Drive are transfer-only fallbacks.
Full design and rationale: docs/superpowers/specs/2026-07-27-multi-machine-claude-profile-design.md
(machine-agnostic layout: docs/adr/0005-hooks-run-from-the-clone.md).

**Machine-agnostic.** Clone it into any folder on any drive, on Windows, Linux or macOS. No
committed file names a machine path; CI (`.github/workflows/machine-paths.yml`) fails a PR
that adds one. Paths come from three places only:

| Source | Gives | Used for |
|---|---|---|
| the clone's own location | `{profile}` | hooks, skills, the CLAUDE.md stub |
| the home folder | `~`, `~/.claude` | settings, logs, the machine config |
| `~/.claude/profile-machine.json` (local, never committed) | `{repos}`, `{tools}`, `{reports}`, `{mailbox}`, the Python name, extra session folders | anything that cannot be derived |

Bootstrap writes the machine config with auto-detected defaults: `{repos}` is the folder
that holds a claude-tools clone (beside this clone, else `~/src`, `~/workspace`, `~/repos`,
`~`), `{tools}` that clone, `{reports}` its `reports/`. `{mailbox}` (the Claude Collab mailbox)
is user-defined and may stay empty; the CLAUDE.md routing rule for it applies only when set.
Edit the file, or re-run bootstrap with flags, to change a value. `python tools/machine_config.py`
prints the resolved roots; every session also gets them from the session-start hook.

## Before you install (read this once)

**Prerequisites**: Claude Code installed and signed in; Git; Python 3.9+ (`python` on
Windows, `python3` elsewhere); GitHub credentials for the clone (`gh auth login`).

**Bootstrap replaces these live files.** It backs each up first, as
`<original-name>.pre-profile-backup-<timestamp>`, and never deletes anything - to undo,
delete what bootstrap wrote and rename the backup back.

| Replaced | Becomes |
|---|---|
| `~/.claude/profile-machine.json` | the machine config above (stored values are kept on re-runs; flags override) |
| `~/.claude/CLAUDE.md` | one-line pointer at this clone's CLAUDE.md |
| `~/.claude/skills` | moved aside, replaced by a junction (Windows) / symlink into this clone |
| `~/.claude/settings.json` | merged with `settings.template.json`: hooks are registered to run from this clone (the template's hooks replace the ones it owns, including copies earlier bootstraps pinned under `~/.claude/hooks`), `permissions.additionalDirectories` gains the configured folders, missing plugin, marketplace and `env` entries are added, and every other existing key is kept. An empty or unparseable file is refused, never replaced |
| Claude Desktop config (Windows: `%APPDATA%\Claude\claude_desktop_config.json`) | only its `mcpServers` value is replaced from `mcp/`; every other byte is kept |
| REA (`rea-agents` npm package, Windows via `bootstrap.ps1`) | installed globally and registered as the user-scope MCP server `rea` by `tools/Install-Rea.ps1` (`-SkipRea` to skip). Needs Node 22.19+/24.11+/26+ |

Skills are *replaced, not merged*: a skill that exists only on this machine ends up in the
moved-aside backup folder. Commit it to this repo first if you want to keep it.

Close Claude Code and Claude Desktop before running bootstrap; re-running it is safe.

## Install on a new machine

1. Clone to any folder **without spaces in its path** (see "Why no spaces" below):
   `git clone https://github.com/YourBIMpossible/claude-profile.git <folder>/claude-profile`
   Putting it beside your other clones (and claude-tools) lets bootstrap detect `{repos}`.
2. Check the plan, then install - from inside the clone:
   - Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -DryRun`, then without `-DryRun`
   - Linux / macOS: `python3 bootstrap.py --dry-run`, then without `--dry-run`

   Options (both entry points): `--repos-root` / `-ReposRoot`, `--tools-root` / `-ToolsRoot`,
   `--collab-mailbox` / `-CollabMailbox` (empty = not used; asked interactively when unset),
   `--no-collab-mailbox`, `--yes` (never prompt).
3. Restart Claude Code (and Claude Desktop on Windows). Verify with `/context` (CLAUDE.md
   and skills load) and `/plugin list`; the first session shows "Machine roots for this profile".

The installer is needed only for: a new machine, a moved clone, a changed root, or a
repaired skills link / CLAUDE.md stub. A machine bootstrapped before the machine-agnostic
layout runs it **once** to move its hooks from `~/.claude/hooks` copies to the clone.

### ZIP fallback (GitHub not reachable right now)

Extract the latest ZIP snapshot to a folder without spaces and run bootstrap exactly as
above. As soon as the machine has network and git credentials, `git pull` once inside the
extracted copy (the ZIP includes `.git/`; export-zip warns when it had no `origin`).

## Keeping a machine current (pulling)

Merging to the default branch is the rollout. At every session start
`hooks/session_start.py` (registered first on SessionStart, any clone location):
1. runs `git pull --ff-only` in this clone - the first of several concurrent sessions pulls,
   the rest log `pull skipped (in flight)`; offline or non-fast-forward just logs and goes on;
2. re-applies the settings merge, so a changed `settings.template.json` reaches
   `~/.claude/settings.json` without bootstrap (the previous file is kept as
   `settings.json.pre-profile-sync`). Changed hook code runs at once; a changed registration
   applies from the next session, because Claude Code reads registrations at session start;
3. prints the machine roots, plus a warning when this clone is not on its default branch or
   has uncommitted hook/tool changes (hooks run from it - do feature work in a worktree).

Record: `~/.claude/hooks/last-run.log`. Opt-outs: `CLAUDE_PROFILE_PULL=0`, `CLAUDE_PROFILE_SYNC=0`.

## Changing your setup (pushing)

This repo is the source of truth: change it here, commit, push, merge.

- **Skills**: `~/.claude/skills` links into this clone, so a skill edit is a working-tree
  edit here. Commit and push it.
- **Instructions**: edit this repo's `CLAUDE.md` (never `~/.claude/CLAUDE.md`, a generated
  pointer). Name paths by root (`{reports}`, `{repos}\X`), never by drive.
- **Hooks and settings**: edit `hooks/` or `settings.template.json`. The template's hooks are
  authoritative; its other values are defaults that only fill keys a machine does not have,
  so an in-app change (e.g. `/config`) is kept. MCP servers come from
  `mcp/claude_desktop_config.template.json`.
- **Machine-specific values** belong in `~/.claude/profile-machine.json`, never in the repo.
  Run `python .github/scripts/check_machine_paths.py` before pushing (CI runs it too).
- **Before every push**: run `python tools/gate.py` from the repo (or its worktree) and push only on `GATE PASS`. It runs pytest over
  `hooks/tests` and `tools/tests` (CI runs only part of `hooks/tests`), then the machine-paths guard (self-test and scan), runs every
  step even when one fails, and ends with one verdict line; the exit status is 0 only for a pass. Needs `pytest` (`python -m pip install pytest==8.3.3`).

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
instructions apply. `bootstrap.py` checks for this and refuses up front rather
than leave you with a silently broken setup.

Only the CLAUDE.md import is affected - the skills junction and the generated
JSON configs handle spaces without trouble.

## What this repo does not do

See the design spec's Non-goals section. Short version: no cross-machine memory
sync, no forced project-repo cloning, no Claude-account unification. The Revit
MCP entry in the generated Desktop config is included only if this machine has
Revit's MCP server installed.
