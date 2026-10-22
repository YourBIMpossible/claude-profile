#Requires -Version 5.1
<#
.SYNOPSIS
Windows entry point for installing this profile: runs bootstrap.py, then installs REA.

.DESCRIPTION
All install logic lives in bootstrap.py (one implementation for Windows, Linux and
macOS): the machine config (~/.claude/profile-machine.json), the CLAUDE.md stub, the
skills junction, the settings.json merge and the Claude Desktop config. This wrapper
finds Python, forwards the options, and then runs tools\Install-Rea.ps1 (REA is an npm
package registered with the Claude CLI; -SkipRea skips it, -DryRun never runs it).

Run it from the clone, wherever the clone lives:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 [-DryRun]
      [-ReposRoot <dir>] [-ToolsRoot <dir>] [-CollabMailbox <dir> | -NoCollabMailbox]
      [-Yes] [-SkipRea]
#>
param(
    [switch]$DryRun,
    [string]$ReposRoot,
    [string]$ToolsRoot,
    [string]$CollabMailbox,
    [switch]$NoCollabMailbox,
    [switch]$Yes,
    [switch]$SkipRea
)

$ErrorActionPreference = 'Stop'

function Find-Python {
    # Python 3.9+ on PATH: 'python', the 'py' launcher, or 'python3'. The Microsoft
    # Store alias stub (WindowsApps\python.exe) prints an install hint and exits
    # non-zero, so every candidate is probed by actually running it.
    foreach ($candidate in @(@('python'), @('py', '-3'), @('python3'))) {
        $cmd = Get-Command $candidate[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $prefix = @($candidate | Select-Object -Skip 1)
        $ErrorActionPreference = 'Continue'
        $ok = & $cmd.Source @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($code -eq 0) { return , (@($cmd.Source) + $prefix) }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    throw "Python 3.9+ was not found on PATH (tried python, py -3, python3). Install Python, then run bootstrap again."
}

$forward = @()
if ($DryRun) { $forward += '--dry-run' }
if ($ReposRoot) { $forward += @('--repos-root', $ReposRoot) }
if ($ToolsRoot) { $forward += @('--tools-root', $ToolsRoot) }
if ($NoCollabMailbox) { $forward += '--no-collab-mailbox' }
elseif ($PSBoundParameters.ContainsKey('CollabMailbox')) { $forward += @('--collab-mailbox', $CollabMailbox) }
if ($Yes) { $forward += '--yes' }

$exe = $python[0]
$prefixArgs = @($python | Select-Object -Skip 1)
& $exe @prefixArgs -I (Join-Path $PSScriptRoot 'bootstrap.py') @forward
if ($LASTEXITCODE -ne 0) { throw "bootstrap.py failed (exit $LASTEXITCODE); nothing after it was run." }

# REA (Reverse Engineer Anything): npm package + user-scope Claude Code MCP server.
# Network-touching and optional: a missing Node.js warns and skips.
if ($DryRun) {
    Write-Host "Dry run: REA install not run (tools\Install-Rea.ps1 -DryRun shows its plan)."
} elseif ($SkipRea) {
    Write-Host "Skipped REA install (-SkipRea)"
} else {
    & (Join-Path $PSScriptRoot 'tools\Install-Rea.ps1')
}
