# Multi-Machine Claude Profile — Design

## Problem

Work happens across a growing number of physically separate Windows machines — a laptop, a Plex PC, a PC at a location in Denver, a forthcoming AI-PC, and likely more later — sometimes signed into different Claude accounts. Claude Code's personal configuration (instructions, skills, settings) needs to be available and stay current on whichever machine is in front of the user, without manually re-copying files or maintaining a setup document that drifts the moment one machine changes and the others don't (as NEW-PC-SETUP.md already had: 14 skills documented vs. 19 actually present).

## Non-goals (explicitly out of scope for v1)

- **Cross-machine auto-memory continuity.** Auto memory (`~/.claude/projects/<project>/memory/`) is documented by Anthropic as machine-local and not synced across machines or cloud environments, even when redirected via `autoMemoryDirectory`. Not addressed here. Revisit only if the loss of continuity is actually felt as a problem in daily use.
- **Force-cloning every project repo onto every machine.** Project repos (BIMpossible_Workspace, Add-Ins, AI-Server, PC-Monitor, etc.) continue to be cloned on-demand, per machine, as needed — normal git flow, unchanged by this design.
- **Revit-bound work.** A machine without Revit installed cannot do live Revit-MCP work no matter what this repo does. That stays a Tailscale/remote-desktop concern, separate from this design.
- **Deep unification across different Claude account logins.** This repo governs machine-local configuration — files under the OS user profile. It solves *machine* portability, not *account* portability. Subscription tier, rate limits, model availability, and OAuth-authorized MCP connectors (Gmail, Calendar, Drive) are account-layer concerns, independent of this repo, and are not addressed by it. Those stay per-login, re-authorized as needed, wherever they're needed.

## Architecture

A single private git repo, `claude-profile`. Cloned once per machine — location derived per-machine (see Bootstrap), not forced to a specific drive letter. It is the durable source of truth for exactly three things: global instructions (`CLAUDE.md`), custom skills, and the templates used to generate machine-specific config. It is not a general sync layer for arbitrary state.

## Repo contents

| Path | Role |
|---|---|
| `CLAUDE.md` | The real, authored instruction content. Kept lean — a few hundred lines at most, a loader of behavior, not a dumping ground. |
| `skills/*` | Canonical source of every custom skill. |
| `settings.template.json` | Template for the user-level `settings.json` — only the parts identical across every machine (enabled plugins, marketplaces, theme, notification flags). |
| `mcp/claude_desktop_config.template.json` | Template for the Desktop app's MCP server config. |
| `bootstrap.ps1` | One-time per-machine initializer. |
| `hooks/session-start-pull.ps1` | Best-effort, non-blocking repo refresh, invoked via a SessionStart hook entry. |
| `docs/superpowers/specs/*.md` | This spec, and any future ones for this repo. |

## Machine-local wiring

- **`~/.claude/CLAUDE.md`** is a single static line: `@<absolute-path-to-clone>/CLAUDE.md`. Nothing else goes in this file — no conditional logic, no extra content, no cleverness. Its only job is to point at the repo copy. This relies on documented behavior: absolute-path `@`-imports in user-scope memory files load automatically (no trust-dialog prompt, unlike project-scope external imports) and need no admin rights or Developer Mode, unlike a true Windows symlink.
- **`~/.claude/skills`** is a directory junction (`mklink /J`, no admin required) to the repo's `skills/` folder, so editing a skill through Claude Code edits the repo's working copy directly — nothing to manually copy back later.
- **`~/.claude/settings.json`** and **`%AppData%\Roaming\Claude\claude_desktop_config.json`** are *generated*, never linked — they carry machine-specific values, so `bootstrap.ps1` reads the templates and writes the real files.

## Bootstrap flow — derive, don't interrogate

`bootstrap.ps1` determines machine specifics by inspecting the machine, prompting only for genuine exceptions it can't infer:

- **Clone location**: default to `<workspace-root>\claude-profile` if a workspace root (e.g. `F:\AI-Dev`) is found on this machine; otherwise fall back to `%USERPROFILE%\claude-profile`. Either way, no drive letter is assumed or required — the script records wherever it actually ends up and writes that real path into the CLAUDE.md stub and settings.
- **Revit presence**: checked by testing whether the known MCP-server install path exists (`C:\Program Files\Autodesk\Revit 2027 MCP Server Read-Tools Technical Preview\`). The Revit block is included in the generated `claude_desktop_config.json` only if that path is found — one clean derived conditional, not a pile of prompts or branches.
- **Project directories** referenced by `additionalDirectories`: detected by checking for known folder names under the resolved workspace root, not asked of the user.
- Anything that genuinely can't be inferred this way (e.g., no workspace root found anywhere obvious) is the only case that prompts.

## Staying current — SessionStart hook

A SessionStart hook entry (baked into the generated `settings.json`) runs `hooks/session-start-pull.ps1` at the start of every session. That script attempts a silent `git pull --ff-only` in the profile-repo clone and **exits cleanly no matter the outcome** — offline, conflicted, or any other failure just means the session proceeds on whatever is already on disk. This is a best-effort refresh, not a guarantee: it should never be described as "always current," only as "attempts a silent refresh, never blocks session start."

A manual `git pull` in the clone directory is always available to force a refresh mid-session.

## v1 scope — exactly this, nothing more

1. Create the `claude-profile` repo (this spec is its first commit).
2. `bootstrap.ps1`: clone + derive machine specifics + write `settings.json` / `claude_desktop_config.json` from templates.
3. Directory junction for `skills/`.
4. Static, one-line `CLAUDE.md` stub with absolute-path import.
5. SessionStart hook: silent pull, clean exit regardless of outcome.

Explicitly deferred past v1: auto-memory sync, `~/.claude/rules/` (CLAUDE.md alone covers current content — not needed yet), and any Revit conditional logic beyond the single derived presence check above.

## Excluded from the repo — never synced

`.credentials.json`, `.claude.json`, and anything under `cache/`, `debug/`, `telemetry/`, `sessions/`, `shell-snapshots/`, `session-env/`, `backups/`. All of this is either secrets or pure machine-runtime state — never portable config, never committed.

## Testing / verification

- Dry-run `bootstrap.ps1` against a throwaway clone path first; confirm the generated `settings.json` / `claude_desktop_config.json` look right before touching the real `~/.claude/`.
- Confirm the CLAUDE.md stub actually loads via `/context` → **Memory files**.
- Confirm the SessionStart hook fires (visible via a log line or `/context`) and confirm it does *not* block session start when the network is unreachable (simulate by temporarily disabling network or pointing at an unreachable remote).
- Run bootstrap for real on a second physical machine (the laptop, being the most readily available) as the actual cross-machine proof — everything before that point is only proven on Home Base.
