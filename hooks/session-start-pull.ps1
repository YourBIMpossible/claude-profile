#Requires -Version 5.1

# Best-effort refresh of the claude-profile repo at Claude Code session start.
# This is NOT a guarantee of freshness: if the machine is offline, the pull is
# not a fast-forward, another session is already pulling, or git is missing
# entirely, the session simply proceeds on whatever is already on disk. Every
# path through this script exits 0 - it must never block session start. Its
# record of what happened is last-run.log.
#
# Concurrency: Claude starts many processes at once (13 observed on 2026-07-27,
# 11 of them inside a 6-second window). All of them run this hook against the
# SAME physical checkout, and concurrent `git pull` racing on .git/FETCH_HEAD
# produced "fatal: Cannot fast-forward to multiple branches." That failure was
# benign - every path already exited 0 - but it was logged identically to a real
# breakage, and a loser of the race can silently proceed on a stale profile when
# there ARE incoming changes. A named mutex serializes it: first session in
# pulls, the rest log a distinct "skipped (in flight)" and move on.
#
# TWO mutexes, deliberately. The log needs its own because sessions that SKIP
# the pull never hold the pull mutex, yet still have to write a line.
#   Global\ClaudeProfileGitPull  - serializes the git pull
#   Global\ClaudeProfileHookLog  - serializes the log append
#
# Log vocabulary is deliberately distinct so contention never reads as breakage:
#   pull ok: <git output>
#   pull skipped (in flight) [repo]
#   pull failed (exit N): <git output> [repo]
#   git missing: <detail> [repo]
#   pull threw: <detail> [repo]
#   lock abandoned by prior session (recovered) [repo]

$ErrorActionPreference = 'Continue'

$logPath = $null

function Write-Log {
    param([string]$Message)
    if (-not $logPath) { return }

    # Add-Content is deliberately NOT used. Measured on 2026-07-27: 12 concurrent
    # processes each reported a SUCCESSFUL append and only 2 lines survived - no
    # exception thrown, no garbled text, just same-offset clobbering that silently
    # ate 10 entries. That is precisely the "claims success, quietly lost truth"
    # failure this log exists to expose, so the logger must not commit it. A
    # .NET append under a dedicated mutex gives one writer at a time and a real
    # exception if anything genuinely fails.
    $logMutex = $null
    $held = $false
    try {
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        # AppendAllText does not add a terminator - the line carries its own.
        # UTF8Encoding($false) = no BOM, so appends never inject one mid-file.
        $line = "$stamp - $Message`r`n"
        $enc = New-Object System.Text.UTF8Encoding($false)

        $logMutex = New-Object System.Threading.Mutex($false, 'Global\ClaudeProfileHookLog')
        try {
            # Generous relative to the work: each holder writes one line in well
            # under a millisecond, so even a 12-way herd drains in ~ms. A holder
            # that dies signals abandonment immediately rather than stalling us
            # for the full timeout.
            $held = $logMutex.WaitOne(2000)
        } catch [System.Threading.AbandonedMutexException] {
            $held = $true
        }

        if ($held) {
            # $logPath is absolute (built from $PSScriptRoot); .NET resolves
            # relative paths against the process working directory, not
            # PowerShell's location, so absolute matters here.
            [System.IO.File]::AppendAllText($logPath, $line, $enc)
        }
    } catch {
        # logging must never be the thing that fails this script
    } finally {
        if ($logMutex) {
            if ($held) { try { $logMutex.ReleaseMutex() } catch { } }
            try { $logMutex.Dispose() } catch { }
        }
    }
}

$mutex = $null
$acquired = $false
$repoRoot = '<unresolved>'

try {
    # $logPath first so a failure resolving $repoRoot is still recorded.
    $logPath = Join-Path $PSScriptRoot 'last-run.log'
    $repoRoot = Split-Path -Parent $PSScriptRoot

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        # Distinct from a pull failure: nothing is wrong with the repo, the tool
        # just is not on PATH. Previously this fell into the generic catch below
        # and logged as "pull threw", which is indistinguishable from breakage.
        Write-Log "git missing: git not found on PATH [$repoRoot]"
    }
    else {
        # Global\ so the lock also covers invocations from another logon session
        # (scheduled task, remote shell), not just this interactive desktop.
        # Verified creatable unelevated as a standard user on this machine.
        $mutex = New-Object System.Threading.Mutex($false, 'Global\ClaudeProfileGitPull')

        try {
            $acquired = $mutex.WaitOne(800)
        } catch [System.Threading.AbandonedMutexException] {
            # A previous holder died without releasing. .NET throws, but the wait
            # still succeeded and ownership HAS transferred to this thread - so
            # this is recoverable, and we must still release it below. git's own
            # index.lock remains the real guard against a genuinely torn pull.
            $acquired = $true
            Write-Log "lock abandoned by prior session (recovered) [$repoRoot]"
        }

        if (-not $acquired) {
            # Benign: another session is pulling this very moment. Whatever it
            # fetches lands on disk for us too.
            Write-Log "pull skipped (in flight) [$repoRoot]"
        }
        else {
            # -ErrorAction Stop so a bad repoRoot surfaces in the catch below
            # rather than silently running git pull in whatever directory the
            # hook launched in.
            Push-Location -LiteralPath $repoRoot -ErrorAction Stop
            try {
                # No branch named on purpose: --ff-only resolves through the
                # current branch's configured upstream (branch.<name>.merge), so
                # this stays correct on master, main, or a feature branch.
                $rawOutput = git pull --ff-only 2>&1
                $exitCode = $LASTEXITCODE
            } finally {
                Pop-Location
            }

            # Native stderr lines arrive as ErrorRecord objects even under
            # Continue; Out-String on those renders PowerShell's own file/line/
            # caret diagnostics (pointing at THIS script), not the git message.
            # Extract .Exception.Message instead so the log stays a clean,
            # human-readable line. The null branch matters because a command
            # producing no output at all yields $null, not an empty array, and
            # $null.ToString() would throw.
            $outputText = (@($rawOutput) | ForEach-Object {
                if ($null -eq $_) { '' }
                elseif ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message }
                else { $_.ToString() }
            }) -join ' | '

            if ($exitCode -eq 0) {
                Write-Log "pull ok: $outputText"
            } else {
                Write-Log "pull failed (exit $exitCode): $outputText [$repoRoot]"
            }
        }
    }
} catch {
    Write-Log "pull threw: $($_.Exception.Message) [$repoRoot]"
} finally {
    # Release before Dispose, and only if we actually own it - ReleaseMutex on a
    # non-owned mutex throws ApplicationException. Neither may escape: this
    # script's contract is exit 0 on every path.
    if ($mutex) {
        if ($acquired) {
            try { $mutex.ReleaseMutex() } catch { }
        }
        try { $mutex.Dispose() } catch { }
    }
}

exit 0
