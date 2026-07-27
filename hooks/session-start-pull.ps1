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
    $rawOutput = git pull --ff-only 2>&1
    $exitCode = $LASTEXITCODE
    Pop-Location
    # Native stderr lines arrive as ErrorRecord objects even under Continue;
    # Out-String on those renders PowerShell's own file/line/caret diagnostics
    # (pointing at THIS script), not the git message. Extract .Exception.Message
    # instead so the log stays a clean, human-readable line.
    $outputText = (@($rawOutput) | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_.ToString() }
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
