# Multi-Machine Claude Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `claude-profile` repo (CLAUDE.md, skills/, config templates, bootstrap, SessionStart hook, ZIP export) so any Windows machine can be brought to a working, current Claude Code configuration from either a GitHub clone or a ZIP snapshot.

**Architecture:** A single git repo is the source of truth for CLAUDE.md content, skills, and config templates. A `bootstrap.ps1` script generates machine-specific files from those templates by deriving facts about the machine (Revit installed? workspace root present? which project dirs exist?) rather than prompting. A SessionStart hook does a best-effort, non-blocking `git pull` at the start of every session. A ZIP export script produces a full-fidelity (including `.git/`) snapshot for offline/transfer use, with GitHub remaining the only authoritative source.

**Tech Stack:** Windows PowerShell 5.1 (`powershell.exe`, not `pwsh` — this machine does not have PowerShell 7 installed), git, Claude Code settings.json / hooks, JSON config templates.

## Global Constraints

- GitHub is the canonical source of truth; ZIP and Google Drive are transfer mechanisms only and are never authoritative.
- Must support a new-PC install flow (documented in README.md).
- Windows-first for v1 — no cross-platform script logic.
- Do not solve cross-machine auto-memory continuity in v1.
- Do not force-clone every project repo to every machine.
- Do not unify account login state across different Claude logins — this repo is machine-local configuration only.
- `~/.claude/CLAUDE.md` is a single static line importing the repo copy by absolute path — no other content, no conditional logic in the stub.
- The SessionStart hook must never block session start, must exit cleanly regardless of outcome, and must be described (in code comments and docs) as a best-effort refresh — never as "always current."
- `settings.json` and `claude_desktop_config.json` are generated from templates, never symlinked.
- All `.ps1` script source must be pure ASCII (PowerShell 5.1 misparses non-ASCII without a BOM — a bare em-dash can silently terminate a string literal). This does not apply to CLAUDE.md or JSON content, which are data, not executed script.
- Every `ConvertTo-Json` call on a nested object must pass an explicit `-Depth` (5.1's default of 2 silently truncates nested structure); prefer plain string/token templating over JSON object round-tripping wherever the shape is already known, to sidestep this and the related 5.1 array-reserialization quirk entirely.
- All generated file writes use `[System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))` — `Set-Content -Encoding UTF8` writes a BOM that can break downstream JSON/text consumers.
- Never overwrite a pre-existing real `~/.claude/CLAUDE.md` or `~/.claude/skills` — back it up (rename/copy aside) first. This machine (Home Base) has real content in both right now.

---

## File Structure

| Path | Responsibility |
|---|---|
| `CLAUDE.md` | Real, authored instruction content (migrated from the current live global CLAUDE.md). |
| `skills/*` | Copies of the 19 current custom skill folders. |
| `settings.template.json` | Token-templated `settings.json`, including the SessionStart hook block. |
| `mcp/claude_desktop_config.template.json` | Token-templated Desktop MCP config, Revit entry conditional. |
| `bootstrap.ps1` | Derives machine facts, generates real files, wires CLAUDE.md stub + skills junction. Idempotent, backs up before overwriting. |
| `hooks/session-start-pull.ps1` | Best-effort `git pull --ff-only`, logs to `hooks/last-run.log`, always exits 0. |
| `export-zip.ps1` | Zips the whole repo (incl. `.git/`) to a timestamped file. |
| `.gitignore` | Excludes `last-run.log`, `*.zip`, backup files. |
| `README.md` | Human-facing install/transfer instructions. |
| `docs/superpowers/specs/*.md` | Design spec (already committed). |

---

### Task 1: Populate CLAUDE.md and skills/

**Files:**
- Create: `F:\AI-Dev\claude-profile\CLAUDE.md`
- Create: `F:\AI-Dev\claude-profile\skills\*` (copies of all folders under `C:\Users\Zeria\.claude\skills`)

**Interfaces:**
- Produces: the canonical `CLAUDE.md` content and skill set that `bootstrap.ps1` (Task 5) references via `$repoRoot\CLAUDE.md` and `$repoRoot\skills`.

- [ ] **Step 1: Write CLAUDE.md with the current live content verbatim**

```markdown
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
its own worktree INSIDE the repo: `git worktree add .claude\worktrees\<lane> -b <branch> origin/main`
(run from the repo root). Sibling folders (`../<repo>-<lane>`) are RETIRED 2026-07-24 — they
sprawled into 9+ mystery folders under F:\AI-Dev; canonical rules incl. "every pushed branch
gets a PR (draft ok)" live in F:\AI-Dev\BIMpossible_Workspace\WORKFLOW.md. A shared working tree
makes sessions' uncommitted edits intermingle in the same files (proven 2026-07-02 on
BIMpossible — required `git add -p` hunk surgery to unmix, plus care to not sweep another
session's WIP into your commit). Rules: never `git checkout`/switch branches in a tree another
session is editing; never keep working on a branch that has already merged; before committing
in a possibly-shared tree, `git diff --cached --stat` and confirm no foreign files/hunks.
```

- [ ] **Step 2: Copy the current skills folders into the repo**

```powershell
$src = "C:\Users\Zeria\.claude\skills"
$dst = "F:\AI-Dev\claude-profile\skills"
New-Item -ItemType Directory -Path $dst -Force | Out-Null
Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
```

- [ ] **Step 3: Verify — folder count and content spot-check**

Run:
```powershell
(Get-ChildItem "F:\AI-Dev\claude-profile\skills" -Directory).Count
Test-Path "F:\AI-Dev\claude-profile\skills\graphify\SKILL.md"
Select-String -Path "F:\AI-Dev\claude-profile\CLAUDE.md" -Pattern "^# next$" -Quiet
```
Expected: `19`, then `True`, then `True`.

- [ ] **Step 4: Commit**

```powershell
Set-Location "F:\AI-Dev\claude-profile"
git add CLAUDE.md skills
git -c user.name="Zeriah" -c user.email="zeriah.t@gmail.com" commit -m "Populate CLAUDE.md and skills from current live config"
```

---

### Task 2: settings.template.json

**Files:**
- Create: `F:\AI-Dev\claude-profile\settings.template.json`

**Interfaces:**
- Produces: two literal tokens that Task 5 (`bootstrap.ps1`) replaces by exact substring match: `"__ADDITIONAL_DIRECTORIES__"` (quoted, sits inside the `additionalDirectories` array) and `"__HOOK_SCRIPT_PATH__"` (quoted, sits inside the hook `args` array). Both tokens must appear exactly once, with their surrounding quotes, for the substring replacement in Task 5 to work.

- [ ] **Step 1: Write the template**

```json
{
  "permissions": {
    "additionalDirectories": ["__ADDITIONAL_DIRECTORIES__"]
  },
  "model": "claude-sonnet-4-6",
  "enabledPlugins": {
    "superpowers@superpowers-marketplace": true,
    "typescript-lsp@claude-plugins-official": true,
    "pyright-lsp@claude-plugins-official": true,
    "csharp-lsp@claude-plugins-official": true,
    "context7@claude-plugins-official": true,
    "claude-md-management@claude-plugins-official": true,
    "hookify@claude-plugins-official": true,
    "security-guidance@claude-code-plugins": true,
    "plugin-dev@claude-code-plugins": true,
    "pr-review-toolkit@claude-code-plugins": true,
    "semgrep@claude-plugins-official": false
  },
  "extraKnownMarketplaces": {
    "superpowers-marketplace": {
      "source": {
        "source": "github",
        "repo": "obra/superpowers-marketplace"
      }
    },
    "claude-plugins-official": {
      "source": {
        "source": "github",
        "repo": "anthropics/claude-plugins-official"
      }
    },
    "claude-community": {
      "source": {
        "source": "github",
        "repo": "anthropics/claude-plugins-community"
      }
    },
    "claude-code-plugins": {
      "source": {
        "source": "github",
        "repo": "anthropics/claude-code"
      }
    }
  },
  "skipWorkflowUsageWarning": true,
  "theme": "dark",
  "inputNeededNotifEnabled": true,
  "agentPushNotifEnabled": true,
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "powershell",
            "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "__HOOK_SCRIPT_PATH__"],
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

Note: `model` carries over the current live value (`claude-sonnet-4-6`) unchanged — updating it is a separate, deliberate decision outside this plan's scope, not a silent fix bundled in here.

- [ ] **Step 2: Verify — template is valid JSON and contains both tokens exactly once**

```powershell
$raw = Get-Content -Raw "F:\AI-Dev\claude-profile\settings.template.json"
$null = $raw | ConvertFrom-Json
([regex]::Matches($raw, '"__ADDITIONAL_DIRECTORIES__"')).Count
([regex]::Matches($raw, '"__HOOK_SCRIPT_PATH__"')).Count
```
Expected: no error from `ConvertFrom-Json`, then `1`, then `1`.

- [ ] **Step 3: Commit**

```powershell
git add settings.template.json
git -c user.name="Zeriah" -c user.email="zeriah.t@gmail.com" commit -m "Add settings.json template with SessionStart hook wiring"
```

---

### Task 3: mcp/claude_desktop_config.template.json

**Files:**
- Create: `F:\AI-Dev\claude-profile\mcp\claude_desktop_config.template.json`

**Interfaces:**
- Produces: one literal, unquoted token `__REVIT_MCP_ENTRY__` placed directly after the closing `}` of the `mcp-server-for-revit` entry (no comma in the template — the replacement supplies its own leading comma when present, or an empty string when absent, so the result is valid JSON either way).

- [ ] **Step 1: Write the template**

```json
{
  "mcpServers": {
    "mcp-server-for-revit": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "mcp-server-for-revit"]
    }__REVIT_MCP_ENTRY__
  }
}
```

- [ ] **Step 2: Verify — template is valid JSON (empty token) and stays valid with a sample substitution**

```powershell
$raw = Get-Content -Raw "F:\AI-Dev\claude-profile\mcp\claude_desktop_config.template.json"
$null = ($raw.Replace('__REVIT_MCP_ENTRY__', '')) | ConvertFrom-Json
$sample = ',
    "autodesk-revit-2027": {
      "command": "C:/Program Files/Autodesk/Revit 2027 MCP Server Read-Tools Technical Preview/Autodesk.RevitMcpServer.Stdio.exe",
      "args": []
    }'
$null = ($raw.Replace('__REVIT_MCP_ENTRY__', $sample)) | ConvertFrom-Json
```
Expected: no errors from either `ConvertFrom-Json` call.

- [ ] **Step 3: Commit**

```powershell
git add mcp/claude_desktop_config.template.json
git -c user.name="Zeriah" -c user.email="zeriah.t@gmail.com" commit -m "Add Claude Desktop MCP config template with conditional Revit entry"
```

---

### Task 4: .gitignore and README.md

**Files:**
- Create: `F:\AI-Dev\claude-profile\.gitignore`
- Create: `F:\AI-Dev\claude-profile\README.md`

**Interfaces:**
- None — these are standalone, referenced only by humans and git itself.

- [ ] **Step 1: Write .gitignore**

```
*.zip
hooks/last-run.log
*.pre-profile-backup-*
```

- [ ] **Step 2: Write README.md**

```markdown
# claude-profile

Canonical source of truth for Claude Code configuration across machines.
GitHub is authoritative. ZIP and Google Drive are transfer-only fallbacks.
Full design and rationale: docs/superpowers/specs/2026-07-27-multi-machine-claude-profile-design.md

## Install on a new machine

### Normal path (GitHub reachable)

1. Clone this repo anywhere. A workspace root like `F:\AI-Dev\claude-profile` if you
   have one, otherwise `%USERPROFILE%\claude-profile` works just as well.
2. Run bootstrap from inside the clone:
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1`
3. Restart Claude Code. Verify with `/context` (CLAUDE.md and skills should show as
   loaded) and `/plugins list`.

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

## What this repo does not do

See the design spec's Non-goals section. Short version: no cross-machine memory
sync, no forced project-repo cloning, no Claude-account unification. The Revit
MCP entry in the generated Desktop config is included only if this machine has
Revit's MCP server installed.
```

- [ ] **Step 3: Verify**

```powershell
Test-Path "F:\AI-Dev\claude-profile\.gitignore"
Test-Path "F:\AI-Dev\claude-profile\README.md"
```
Expected: `True`, `True`.

- [ ] **Step 4: Commit**

```powershell
git add .gitignore README.md
git -c user.name="Zeriah" -c user.email="zeriah.t@gmail.com" commit -m "Add .gitignore and README install/transfer instructions"
```

---

### Task 5: bootstrap.ps1

**Files:**
- Create: `F:\AI-Dev\claude-profile\bootstrap.ps1`

**Interfaces:**
- Consumes: `settings.template.json` and `mcp/claude_desktop_config.template.json` (Tasks 2-3), their exact token names.
- Produces: `~/.claude/CLAUDE.md` (stub), `~/.claude/skills` (junction), `~/.claude/settings.json`, `%AppData%\Roaming\Claude\claude_desktop_config.json`. Never called by other scripts in this repo; it is the human-invoked entry point.

- [ ] **Step 1: Write bootstrap.ps1**

```powershell
#Requires -Version 5.1

$ErrorActionPreference = 'Stop'

function ConvertTo-ForwardSlash {
    param([string]$Path)
    return $Path -replace '\\', '/'
}

function Backup-IfRealFile {
    param([string]$Path, [string]$ExpectedContent)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $existing = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Trim() -eq $ExpectedContent.Trim())) { return }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupPath = "$Path.pre-profile-backup-$stamp"
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "Backed up existing file to $backupPath"
}

function Backup-IfRealDirectory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.LinkType -eq 'Junction') { return }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupPath = "$Path.pre-profile-backup-$stamp"
    Move-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "Backed up existing directory to $backupPath"
}

$repoRoot = $PSScriptRoot
$claudeDir = Join-Path $env:USERPROFILE '.claude'
if (-not (Test-Path -LiteralPath $claudeDir)) {
    New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null
}

Write-Host "Repo root: $repoRoot"
Write-Host "Claude dir: $claudeDir"

# 1. CLAUDE.md stub
$claudeMdPath = Join-Path $claudeDir 'CLAUDE.md'
$repoRootForward = ConvertTo-ForwardSlash $repoRoot
$stubContent = "@$repoRootForward/CLAUDE.md"
Backup-IfRealFile -Path $claudeMdPath -ExpectedContent $stubContent
[System.IO.File]::WriteAllText($claudeMdPath, $stubContent, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote CLAUDE.md stub: $stubContent"

# 2. skills junction
$skillsPath = Join-Path $claudeDir 'skills'
$skillsTarget = Join-Path $repoRoot 'skills'
Backup-IfRealDirectory -Path $skillsPath
if (-not (Test-Path -LiteralPath $skillsPath)) {
    New-Item -ItemType Junction -Path $skillsPath -Target $skillsTarget | Out-Null
    Write-Host "Created skills junction -> $skillsTarget"
} else {
    Write-Host "skills junction already present"
}

# 3. Detect Revit
$revitExePath = 'C:\Program Files\Autodesk\Revit 2027 MCP Server Read-Tools Technical Preview\Autodesk.RevitMcpServer.Stdio.exe'
$hasRevit = Test-Path -LiteralPath $revitExePath
Write-Host "Revit MCP server detected: $hasRevit"

# 4. Detect workspace root and known project directories
$candidateRoots = @('F:\AI-Dev', (Join-Path $env:USERPROFILE 'AI-Dev'))
$workspaceRoot = $candidateRoots | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$additionalDirs = @()
if ($workspaceRoot) {
    Write-Host "Workspace root detected: $workspaceRoot"
    foreach ($name in @('BIMpossible', 'BIMpossible_Workspace')) {
        $candidate = Join-Path $workspaceRoot $name
        if (Test-Path -LiteralPath $candidate) {
            $additionalDirs += (ConvertTo-ForwardSlash $candidate)
        }
    }
} else {
    Write-Host "No workspace root found - additionalDirectories will be empty"
}

# 5. Generate settings.json from template
$settingsTemplatePath = Join-Path $repoRoot 'settings.template.json'
$settingsRaw = Get-Content -Raw -LiteralPath $settingsTemplatePath

$dirsJson = ($additionalDirs | ForEach-Object { $_ | ConvertTo-Json }) -join ",`n      "
$settingsRaw = $settingsRaw.Replace('"__ADDITIONAL_DIRECTORIES__"', $dirsJson)

$hookScriptPath = ConvertTo-ForwardSlash (Join-Path $repoRoot 'hooks\session-start-pull.ps1')
$hookScriptJson = $hookScriptPath | ConvertTo-Json
$settingsRaw = $settingsRaw.Replace('"__HOOK_SCRIPT_PATH__"', $hookScriptJson)

try {
    $null = $settingsRaw | ConvertFrom-Json
} catch {
    throw "Generated settings.json is not valid JSON: $($_.Exception.Message)"
}

$settingsPath = Join-Path $claudeDir 'settings.json'
if (Test-Path -LiteralPath $settingsPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item -LiteralPath $settingsPath -Destination "$settingsPath.pre-profile-backup-$stamp" -Force
    Write-Host "Backed up existing settings.json"
}
[System.IO.File]::WriteAllText($settingsPath, $settingsRaw, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote settings.json"

# 6. Generate claude_desktop_config.json from template
$mcpTemplatePath = Join-Path $repoRoot 'mcp\claude_desktop_config.template.json'
$mcpRaw = Get-Content -Raw -LiteralPath $mcpTemplatePath

$revitEntry = ''
if ($hasRevit) {
    $revitExeJson = $revitExePath | ConvertTo-Json
    $revitEntry = (',' + "`n" + '    "autodesk-revit-2027": {' + "`n" + '      "command": ' + $revitExeJson + ',' + "`n" + '      "args": []' + "`n" + '    }')
}
$mcpRaw = $mcpRaw.Replace('__REVIT_MCP_ENTRY__', $revitEntry)

try {
    $null = $mcpRaw | ConvertFrom-Json
} catch {
    throw "Generated claude_desktop_config.json is not valid JSON: $($_.Exception.Message)"
}

$mcpConfigDir = Join-Path $env:APPDATA 'Claude'
if (-not (Test-Path -LiteralPath $mcpConfigDir)) {
    New-Item -ItemType Directory -Path $mcpConfigDir -Force | Out-Null
}
$mcpConfigPath = Join-Path $mcpConfigDir 'claude_desktop_config.json'
if (Test-Path -LiteralPath $mcpConfigPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item -LiteralPath $mcpConfigPath -Destination "$mcpConfigPath.pre-profile-backup-$stamp" -Force
    Write-Host "Backed up existing claude_desktop_config.json"
}
[System.IO.File]::WriteAllText($mcpConfigPath, $mcpRaw, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote claude_desktop_config.json"

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Verify with: /context (check CLAUDE.md and skills load) and /plugins list"
```

- [ ] **Step 2: Verify — dry run against a throwaway HOME, not the real one**

This is the safe way to test bootstrap logic without touching the real `~/.claude/` on this machine yet:

```powershell
$testHome = "F:\AI-Dev\claude-profile\.dry-run-test"
Remove-Item -Recurse -Force $testHome -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $testHome | Out-Null

$oldUserProfile = $env:USERPROFILE
$oldAppData = $env:APPDATA
$env:USERPROFILE = $testHome
$env:APPDATA = (Join-Path $testHome 'AppData\Roaming')
try {
    powershell -NoProfile -ExecutionPolicy Bypass -File "F:\AI-Dev\claude-profile\bootstrap.ps1"
} finally {
    $env:USERPROFILE = $oldUserProfile
    $env:APPDATA = $oldAppData
}

Test-Path "$testHome\.claude\CLAUDE.md"
Get-Content "$testHome\.claude\CLAUDE.md" -Raw
(Get-Item "$testHome\.claude\skills").LinkType
$null = (Get-Content -Raw "$testHome\.claude\settings.json") | ConvertFrom-Json
$null = (Get-Content -Raw "$testHome\AppData\Roaming\Claude\claude_desktop_config.json") | ConvertFrom-Json
```
Expected: `True`; the stub line starting with `@F:/AI-Dev/claude-profile/CLAUDE.md`; `Junction`; no error from either `ConvertFrom-Json` call. Overriding `$env:APPDATA` alongside `$env:USERPROFILE` matters — `claude_desktop_config.json` is written under `$env:APPDATA`, which does not follow `$env:USERPROFILE` automatically. Without this, "dry run" would silently write to the real Desktop app config on this machine.

- [ ] **Step 3: Clean up the dry-run directory**

```powershell
Remove-Item -Recurse -Force "F:\AI-Dev\claude-profile\.dry-run-test"
```

- [ ] **Step 4: Commit**

```powershell
git add bootstrap.ps1
git -c user.name="Zeriah" -c user.email="zeriah.t@gmail.com" commit -m "Add bootstrap.ps1: derive machine facts, generate config, wire CLAUDE.md/skills"
```

---

### Task 6: hooks/session-start-pull.ps1

**Files:**
- Create: `F:\AI-Dev\claude-profile\hooks\session-start-pull.ps1`

**Interfaces:**
- Consumes: nothing from other scripts — it locates its own repo root via `Split-Path -Parent $PSScriptRoot`.
- Produces: `hooks/last-run.log` (gitignored), used as empirical proof the hook actually fires.

- [ ] **Step 1: Write session-start-pull.ps1**

```powershell
#Requires -Version 5.1

$ErrorActionPreference = 'Continue'

$repoRoot = Split-Path -Parent $PSScriptRoot
$logPath = Join-Path $PSScriptRoot 'last-run.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Write-Log {
    param([string]$Message)
    try {
        Add-Content -LiteralPath $logPath -Value "$stamp - $Message" -Encoding UTF8
    } catch {
        # logging must never be the thing that fails this script
    }
}

try {
    Push-Location -LiteralPath $repoRoot
    $output = git pull --ff-only 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    Pop-Location
    if ($exitCode -eq 0) {
        Write-Log "pull ok: $($output.Trim())"
    } else {
        Write-Log "pull failed (exit $exitCode): $($output.Trim())"
    }
} catch {
    Write-Log "pull threw: $($_.Exception.Message)"
}

exit 0
```

- [ ] **Step 2: Verify — run it directly and confirm it always exits 0**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "F:\AI-Dev\claude-profile\hooks\session-start-pull.ps1"
$LASTEXITCODE
Get-Content "F:\AI-Dev\claude-profile\hooks\last-run.log" -Tail 1
```
Expected: `0`, then a log line ending in `pull ok: ...` (since GitHub isn't configured as a remote yet at this point in the plan, this may instead read `pull failed (exit 1): ...` or `pull threw: ...` mentioning no upstream configured — either way, the key check is the exit code is `0` and a log line was written).

- [ ] **Step 3: Verify — simulate an unreachable remote and confirm it still exits 0**

```powershell
Set-Location "F:\AI-Dev\claude-profile"
git remote add test-offline-check https://192.0.2.1/nonexistent.git
git -c remote.pushDefault=test-offline-check fetch test-offline-check 2>&1 | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File "F:\AI-Dev\claude-profile\hooks\session-start-pull.ps1"
$LASTEXITCODE
git remote remove test-offline-check
```
Expected: `0` (the script must exit 0 regardless of the actual `git pull` outcome, since it only ever pulls from `origin`, not the test remote — this step mainly confirms adding/removing an unrelated unreachable remote doesn't destabilize the script; the real offline proof is Step 2's behavior once `origin` exists after Task 8's clone test).

- [ ] **Step 4: Commit**

```powershell
git add hooks/session-start-pull.ps1
git -c user.name="Zeriah" -c user.email="zeriah.t@gmail.com" commit -m "Add SessionStart hook script: best-effort pull, always exits 0"
```

---

### Task 7: export-zip.ps1

**Files:**
- Create: `F:\AI-Dev\claude-profile\export-zip.ps1`

**Interfaces:**
- Produces: a `claude-profile-<timestamp>.zip` file, default location `%USERPROFILE%\Desktop`, containing the full repo working tree including `.git/`.

- [ ] **Step 1: Write export-zip.ps1**

```powershell
#Requires -Version 5.1

param(
    [string]$OutputDirectory = (Join-Path $env:USERPROFILE 'Desktop')
)

$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$zipName = "claude-profile-$stamp.zip"

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}
$zipPath = Join-Path $OutputDirectory $zipName

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Compress-Archive -Path (Join-Path $repoRoot '*') -DestinationPath $zipPath -Force

Write-Host "Wrote $zipPath"
Write-Host "This ZIP includes .git/ - extracting it produces a working clone with 'origin' already configured."
```

- [ ] **Step 2: Verify — produce a zip to a throwaway location and confirm .git/ is inside**

```powershell
$testOut = "F:\AI-Dev\claude-profile\.dry-run-zip-test"
Remove-Item -Recurse -Force $testOut -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $testOut | Out-Null

powershell -NoProfile -ExecutionPolicy Bypass -File "F:\AI-Dev\claude-profile\export-zip.ps1" -OutputDirectory $testOut

$zip = Get-ChildItem $testOut -Filter "claude-profile-*.zip" | Select-Object -First 1
$extractPath = Join-Path $testOut 'extracted'
Expand-Archive -Path $zip.FullName -DestinationPath $extractPath
Test-Path (Join-Path $extractPath '.git')
Test-Path (Join-Path $extractPath 'CLAUDE.md')
Test-Path (Join-Path $extractPath 'bootstrap.ps1')
```
Expected: `True`, `True`, `True`.

- [ ] **Step 3: Clean up**

```powershell
Remove-Item -Recurse -Force "F:\AI-Dev\claude-profile\.dry-run-zip-test"
```

- [ ] **Step 4: Commit**

```powershell
git add export-zip.ps1
git -c user.name="Zeriah" -c user.email="zeriah.t@gmail.com" commit -m "Add export-zip.ps1 for the ZIP/Drive transfer fallback"
```

---

### Task 8: End-to-end verification (still against throwaway state, not live ~/.claude/)

**Files:** none created — this task only runs verification across everything built in Tasks 1-7.

**Interfaces:** none — this is the integration check the spec's "Testing / verification" section calls for.

- [ ] **Step 1: Confirm the repo is self-consistent — every template token has a corresponding replacement in bootstrap.ps1**

```powershell
$bootstrap = Get-Content -Raw "F:\AI-Dev\claude-profile\bootstrap.ps1"
$bootstrap.Contains('__ADDITIONAL_DIRECTORIES__')
$bootstrap.Contains('__HOOK_SCRIPT_PATH__')
$bootstrap.Contains('__REVIT_MCP_ENTRY__')
```
Expected: `True`, `True`, `True`.

- [ ] **Step 2: Confirm nothing on the excluded list is tracked in the repo**

```powershell
Set-Location "F:\AI-Dev\claude-profile"
git ls-files | Select-String -Pattern '\.credentials\.json$|^\.claude\.json$|(^|/)cache/|(^|/)debug/|(^|/)telemetry/|(^|/)sessions/|(^|/)shell-snapshots/|(^|/)session-env/|(^|/)backups/'
```
Expected: no output (no matches) — confirms none of the Global Constraints' excluded paths were ever committed. This is a cheap defense-in-depth check on top of the fact that every task above used targeted `git add <specific files>`, never a broad `git add -A` or `git add .`.

- [ ] **Step 3: Full dry run — clone-equivalent to a throwaway folder, bootstrap it, verify generated files, without touching real `~/.claude/`**

```powershell
$testHome = "F:\AI-Dev\claude-profile\.e2e-test-home"
$testRepo = "F:\AI-Dev\claude-profile\.e2e-test-repo"
Remove-Item -Recurse -Force $testHome, $testRepo -ErrorAction SilentlyContinue

git clone "F:\AI-Dev\claude-profile" $testRepo

New-Item -ItemType Directory -Path $testHome | Out-Null
$oldUserProfile = $env:USERPROFILE
$oldAppData = $env:APPDATA
$env:USERPROFILE = $testHome
$env:APPDATA = (Join-Path $testHome 'AppData\Roaming')
try {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $testRepo 'bootstrap.ps1')
} finally {
    $env:USERPROFILE = $oldUserProfile
    $env:APPDATA = $oldAppData
}

Get-Content (Join-Path $testHome '.claude\CLAUDE.md') -Raw
(Get-Item (Join-Path $testHome '.claude\skills')).LinkType
$null = (Get-Content -Raw (Join-Path $testHome '.claude\settings.json')) | ConvertFrom-Json
$null = (Get-Content -Raw (Join-Path $testHome 'AppData\Roaming\Claude\claude_desktop_config.json')) | ConvertFrom-Json
```
Expected: the stub line points at `$testRepo` (proving bootstrap correctly derives its own location via `$PSScriptRoot` rather than assuming a fixed path); `Junction`; both `ConvertFrom-Json` calls succeed without error. As in Task 5 Step 2, overriding `$env:APPDATA` alongside `$env:USERPROFILE` is required — otherwise this "safe" end-to-end test would silently write to the real Desktop app config on this machine, exactly the kind of live-state side effect this task exists to avoid.

- [ ] **Step 4: Confirm the SessionStart hook fires from a real session against the test repo**

```powershell
Set-Location $testRepo
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $testRepo 'hooks\session-start-pull.ps1')
Get-Content (Join-Path $testRepo 'hooks\last-run.log')
```
Expected: at least one log line, script exits without throwing.

- [ ] **Step 5: Clean up all throwaway state**

```powershell
Remove-Item -Recurse -Force "F:\AI-Dev\claude-profile\.e2e-test-home"
Remove-Item -Recurse -Force "F:\AI-Dev\claude-profile\.e2e-test-repo"
```

- [ ] **Step 6: Report status — explicitly stop here**

Do not run `bootstrap.ps1` against the real `C:\Users\Zeria\.claude\` on this machine as part of this plan. That is a deliberate, separate, later action the user takes once ready — per the spec's testing philosophy ("dry-run first... before touching the real `~/.claude/`") and because this session is itself running under the current live config. Summarize what was built and verified, and that flipping Home Base to it live, pushing the repo to GitHub, and testing on a second physical machine remain explicit next steps outside this plan.

