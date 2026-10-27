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
Output: exactly one status line on the success stream, and nothing else there; bootstrap.ps1
prints it as its last line ("REA: <status>"). A skip still exits 0, because REA is optional:
  skipped: <reason> | not registered: <reason> | dry run: no changes made |
  already registered | registered and connected | registered, not connected
Under -DryRun every status starts with "dry run: ".
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

function Format-Status {
    # A dry run changes nothing, so its status must never read as a real outcome.
    param([string]$Status)
    if ($DryRun) { return "dry run: $Status" }
    return $Status
}

function Invoke-NativeLines {
    # Run a native command and keep its stdout lines, stderr text and exit code apart. Under
    # Windows PowerShell 5.1 with ErrorActionPreference=Stop, a redirected native stderr line
    # becomes a terminating NativeCommandError (npm prints warnings there), so relax it for
    # this call only.
    param([string]$FilePath, [string[]]$ArgumentList)
    $ErrorActionPreference = 'Continue'
    $stdout = [System.Collections.Generic.List[string]]::new()
    $stderr = [System.Collections.Generic.List[string]]::new()
    & $FilePath @ArgumentList 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $stderr.Add("$_") } else { $stdout.Add("$_") }
    }
    return [pscustomobject]@{ Out = $stdout.ToArray(); Err = ($stderr -join "`n"); ExitCode = $LASTEXITCODE }
}

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
    return (Format-Status 'skipped: Node.js/npm not on PATH')
}
$nodeRun = Invoke-NativeLines $node.Source @('--version')
$nodeVersion = @($nodeRun.Out | ForEach-Object { "$_".Trim() } | Where-Object { $_ }) | Select-Object -First 1
if ($nodeRun.ExitCode -ne 0 -or -not $nodeVersion) {
    $reason = if ($nodeRun.ExitCode -ne 0) { "'node --version' failed (exit $($nodeRun.ExitCode))" } else { "'node --version' printed no version" }
    Write-Warning "[rea] $reason - skipping. Repair Node.js and re-run tools\Install-Rea.ps1. $($nodeRun.Err)"
    return (Format-Status "skipped: $reason")
}
if (-not (Test-NodeVersion $nodeVersion)) {
    Write-Warning "[rea] Node $nodeVersion is below REA's floor (22.19+/24.11+/26+) - skipping. Upgrade Node and re-run tools\Install-Rea.ps1."
    return (Format-Status "skipped: Node $nodeVersion is below REA's floor (22.19+/24.11+/26+)")
}
if (-not $claude) {
    Write-Warning "[rea] 'claude' CLI not on PATH - will install the package but cannot register the MCP server."
}

# npm can print warnings before the path; keep the stdout line that is an absolute path.
$prefixRun = Invoke-NativeLines $npm.Source @('prefix', '-g')
if ($prefixRun.ExitCode -ne 0) { throw "npm prefix -g failed (exit $($prefixRun.ExitCode)): $($prefixRun.Err)" }
$npmPrefix = @($prefixRun.Out | ForEach-Object { "$_".Trim() } |
    Where-Object { $_ -match '^[A-Za-z]:[\\/]' -or $_.StartsWith('/') }) | Select-Object -Last 1
if (-not $npmPrefix) { throw "npm prefix -g returned no path (exit $($prefixRun.ExitCode)): $($prefixRun.Err)" }
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
        & $npm.Source install --global $spec | Out-Host
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
if (-not $claude) { return (Format-Status "not registered: 'claude' CLI not on PATH") }
$entryForward = $reaEntry -replace '\\', '/'
$expectedArgs = "$entryForward --mcp"
$info = Get-McpServerInfo $serverName
$current = $info.Text
$registered = $info.ExitCode -eq 0 -and $current -match 'Scope:\s*User'
$matchesExpected = $registered -and ($current -match [regex]::Escape($entryForward)) -and ($current -match 'Command:\s*node')

if ($matchesExpected) {
    Write-Step "MCP server '$serverName' already registered at user scope - left untouched"
    return (Format-Status 'already registered')
}
if ($registered) {
    Write-Step "MCP server '$serverName' registered with a different command - re-registering"
    if (-not $DryRun) {
        $removal = Invoke-NativeLines $claude.Source @('mcp', 'remove', '--scope', 'user', $serverName)
        if ($removal.ExitCode -ne 0) {
            throw "claude mcp remove --scope user $serverName failed (exit $($removal.ExitCode)); not re-adding it. $($removal.Err)"
        }
    }
}
Write-Step "claude mcp add --scope user $serverName -- node $expectedArgs"
if ($DryRun) { return 'dry run: no changes made' }
& claude mcp add --scope user $serverName -- node $entryForward --mcp | Out-Host
if ($LASTEXITCODE -ne 0) { throw "claude mcp add failed (exit $LASTEXITCODE)" }
$verify = (Get-McpServerInfo $serverName).Text
# Only the exact status token counts: "Status: <check mark> Connected", never "Disconnected" or
# "Not Connected". The mark is skipped as any run of symbols or non-ASCII characters, because
# Windows PowerShell 5.1 may decode it from the console code page as several letters.
if ($verify -cnotmatch '(?m)^\s*Status:\s*(?:[^\w\s]|[^\x00-\x7F])*\s*Connected\s*$') {
    Write-Warning "[rea] Registered, but 'claude mcp get $serverName' does not report Connected:`n$verify"
    return 'registered, not connected'
}
Write-Step "MCP server '$serverName' registered and connected. Restart Claude Code to load its tools."
return 'registered and connected'
