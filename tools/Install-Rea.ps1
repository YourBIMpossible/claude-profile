#Requires -Version 5.1
<#
.SYNOPSIS
Install REA (Reverse Engineer Anything, npm package rea-agents) and register it as a
user-scope MCP server for Claude Code. Idempotent; safe to re-run.

.DESCRIPTION
Bootstrap calls this as its last step. It can also be run on its own:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Install-Rea.ps1 [-Update] [-DryRun]

What it does
  1. Checks Node.js (22.19+, 24.11+ or 26+) and npm on PATH. Missing -> warn and skip; never throws.
  2. `npm install --global rea-agents` when it is not installed, or always with -Update.
  3. `claude mcp add --scope user rea -- node <global rea.mjs> --mcp` when no `rea` entry exists or
     its command differs. The registration points at the global install path, so `npm install -g`
     updates are picked up without re-registering.
The skill itself (skills/reverse-engineer-anything) ships in this repo and reaches
~/.claude/skills through the junction bootstrap creates, so no copy step is needed here.

Why not `rea setup`: it returns `needs_human` with no actions when run without a TTY, and
`rea mcp add` fails with `spawn npx ENOENT` under Git Bash. Direct registration is equivalent.
#>
param(
    [switch]$Update,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$package = 'rea-agents'
$serverName = 'rea'

function Write-Step { param([string]$Text) Write-Host "[rea] $Text" }

function Test-NodeVersion {
    param([string]$VersionText)
    if ($VersionText -notmatch '^v?(\d+)\.(\d+)\.(\d+)') { return $false }
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -ge 26) { return $true }
    if ($major -eq 24) { return $minor -ge 11 }
    if ($major -eq 22) { return $minor -ge 19 }
    return $false
}

# 1. Prerequisites
$node = Get-Command node -ErrorAction SilentlyContinue
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) {
    Write-Warning "[rea] Node.js/npm not on PATH - skipping REA install. Install Node 24 LTS and re-run tools\Install-Rea.ps1."
    return
}
$nodeVersion = (& node --version 2>$null | Select-Object -First 1)
if (-not (Test-NodeVersion $nodeVersion)) {
    Write-Warning "[rea] Node $nodeVersion is below REA's floor (22.19+/24.11+/26+) - skipping. Upgrade Node and re-run tools\Install-Rea.ps1."
    return
}
if (-not $claude) {
    Write-Warning "[rea] 'claude' CLI not on PATH - will install the package but cannot register the MCP server."
}

# npm can print warnings before the path; keep the line that is an absolute path.
$npmPrefix = @(& $npm.Source prefix -g 2>$null | ForEach-Object { "$_".Trim() } |
    Where-Object { $_ -match '^[A-Za-z]:[\\/]' -or $_.StartsWith('/') }) | Select-Object -Last 1
if (-not $npmPrefix) { throw "npm prefix -g returned no path" }
$reaEntry = Join-Path $npmPrefix "node_modules\$package\scripts\rea.mjs"
$installed = Test-Path -LiteralPath $reaEntry

# 2. Package
if ($installed -and -not $Update) {
    $pkgJson = Join-Path $npmPrefix "node_modules\$package\package.json"
    $ver = (Get-Content -LiteralPath $pkgJson -Raw | ConvertFrom-Json).version
    Write-Step "$package $ver already installed at $npmPrefix"
} else {
    $spec = if ($Update) { "$package@latest" } else { $package }
    Write-Step "npm install --global $spec"
    if (-not $DryRun) {
        & $npm.Source install --global $spec
        if ($LASTEXITCODE -ne 0) { throw "npm install --global $spec failed (exit $LASTEXITCODE)" }
        if (-not (Test-Path -LiteralPath $reaEntry)) { throw "Install finished but $reaEntry is missing" }
    }
}

function Get-McpServerInfo {
    # 'claude mcp get' writes to stderr when the server is absent. Under Windows PowerShell 5.1
    # with ErrorActionPreference=Stop, redirected native stderr becomes a terminating
    # NativeCommandError, so relax it for this call only and report the exit code explicitly.
    param([string]$Name)
    $ErrorActionPreference = 'Continue'
    $text = & claude mcp get $Name 2>&1 | ForEach-Object { "$_" } | Out-String
    return [pscustomobject]@{ Text = $text; ExitCode = $LASTEXITCODE }
}

# 3. Claude Code user-scope MCP registration
if (-not $claude) { return }
$entryForward = $reaEntry -replace '\\', '/'
$expectedArgs = "$entryForward --mcp"
$info = Get-McpServerInfo $serverName
$current = $info.Text
$registered = $info.ExitCode -eq 0 -and $current -match 'Scope:\s*User'
$matchesExpected = $registered -and ($current -match [regex]::Escape($entryForward)) -and ($current -match 'Command:\s*node')

if ($matchesExpected) {
    Write-Step "MCP server '$serverName' already registered at user scope - left untouched"
    return
}
if ($registered) {
    Write-Step "MCP server '$serverName' registered with a different command - re-registering"
    if (-not $DryRun) { & claude mcp remove --scope user $serverName | Out-Null }
}
Write-Step "claude mcp add --scope user $serverName -- node $expectedArgs"
if ($DryRun) { return }
& claude mcp add --scope user $serverName -- node $entryForward --mcp
if ($LASTEXITCODE -ne 0) { throw "claude mcp add failed (exit $LASTEXITCODE)" }
$verify = (Get-McpServerInfo $serverName).Text
if ($verify -notmatch 'Connected') {
    Write-Warning "[rea] Registered, but 'claude mcp get $serverName' does not report Connected:`n$verify"
} else {
    Write-Step "MCP server '$serverName' registered and connected. Restart Claude Code to load its tools."
}
