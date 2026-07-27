#Requires -Version 5.1

# Best-effort refresh of the claude-profile repo at Claude Code session start.
# This is NOT a guarantee of freshness: if the machine is offline, the pull is
# not a fast-forward, or git is missing entirely, the session simply proceeds
# on whatever is already on disk. Every path through this script exits 0 - it
# must never block session start. Its record of what happened is last-run.log.

$ErrorActionPreference = 'Continue'

$logPath = $null

function Write-Log {
    param([string]$Message)
    if (-not $logPath) { return }
    try {
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -LiteralPath $logPath -Value "$stamp - $Message" -Encoding UTF8
    } catch {
        # logging must never be the thing that fails this script
    }
}

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $logPath = Join-Path $PSScriptRoot 'last-run.log'

    # -ErrorAction Stop so a bad repoRoot surfaces in the catch below rather
    # than silently running git pull in whatever directory the hook launched in.
    Push-Location -LiteralPath $repoRoot -ErrorAction Stop
    try {
        $rawOutput = git pull --ff-only 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    # Native stderr lines arrive as ErrorRecord objects even under Continue;
    # Out-String on those renders PowerShell's own file/line/caret diagnostics
    # (pointing at THIS script), not the git message. Extract .Exception.Message
    # instead so the log stays a clean, human-readable line. The null branch
    # matters because a command producing no output at all yields $null, not an
    # empty array, and $null.ToString() would throw.
    $outputText = (@($rawOutput) | ForEach-Object {
        if ($null -eq $_) { '' }
        elseif ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message }
        else { $_.ToString() }
    }) -join ' | '

    if ($exitCode -eq 0) {
        Write-Log "pull ok: $outputText"
    } else {
        Write-Log "pull failed (exit $exitCode): $outputText"
    }
} catch {
    Write-Log "pull threw: $($_.Exception.Message)"
}

exit 0
