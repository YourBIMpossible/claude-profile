<#
.SYNOPSIS
  Claude Code SessionStart hook: shows open audit-watchdog alerts at session start.

.DESCRIPTION
  Reads the watchdog's alerts.json and status.json and emits SessionStart hook JSON:
  `systemMessage` (shown to the user in the session) and `additionalContext` (given to Claude
  so it relays the alerts). Prints nothing when there is nothing to report.

  Rules:
  - Every OPEN alert is shown at every session start until it is acknowledged
    (Ack-AuditAlert.ps1) or resolved by the watchdog. Showing it is NOT acknowledgment.
  - At most 8 alerts are listed (malfunctions first, newest first); the rest are counted.
  - Watchdog problems are shown, never swallowed: unreadable state, a watchdog error, a stale
    evaluation (older than the longest scheduled gap + grace, 102 h; i.e. the Windows task is not running), or not activated.
  - Always exits 0: a monitoring fault must never block Claude startup.
  Each display is appended to deliveries.log (time + alert ids) as a delivery record.

  Compatible with Windows PowerShell 5.1 (hook runs `powershell`).
#>
[CmdletBinding()]
param(
    [string]$StatusRoot = (Join-Path $env:USERPROFILE '.claude\scheduled-tasks\_watchdog'),
    [datetime]$Now = (Get-Date),
    # Longest gap between scheduled checks is Thu 11:00 -> Mon 11:00 = 96 h (Install-AuditWatchdog.ps1
    # $CheckDays); 6 h grace. Test-AuditWatchdog.ps1 derives the gap from $CheckDays and fails on drift.
    [int]$StaleHours = 102,
    [int]$MaxShown = 8
)
$ErrorActionPreference = 'Stop'
$ack = 'F:\BIMpossible-Workspace\system\scheduled-tasks\audit-watchdog\Ack-AuditAlert.ps1'

function Out-Hook([string[]]$lines) {
    if (-not $lines -or $lines.Count -eq 0) { return }
    $text = ($lines -join "`n")
    $obj = [ordered]@{
        systemMessage      = $text
        hookSpecificOutput = [ordered]@{ hookEventName = 'SessionStart'
            additionalContext = "AUDIT WATCHDOG ALERTS (from the session-start hook; relay them to the user near the start of your first reply, briefly, and do not acknowledge them yourself unless the user asks):`n$text" }
    }
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    Write-Output ($obj | ConvertTo-Json -Depth 4 -Compress)
}

$lines = New-Object System.Collections.Generic.List[string]
try {
    $statusPath = Join-Path $StatusRoot 'status.json'
    $storePath = Join-Path $StatusRoot 'alerts.json'
    if (-not (Test-Path $statusPath)) {
        $lines.Add("Audit watchdog: no status at $statusPath. The watchdog has never run: check Windows Task Scheduler task 'BIMpossible Audit Watchdog'.")
        Out-Hook $lines; exit 0
    }
    $status = Get-Content -Raw $statusPath | ConvertFrom-Json
    if ($status.PSObject.Properties['error'] -and $status.error) {
        $lines.Add("Audit watchdog ERROR at $(([datetime]$status.evaluated_at).ToLocalTime().ToString('yyyy-MM-dd HH:mm')): $($status.error). Fix it, then run the Windows task 'BIMpossible Audit Watchdog'.")
    }
    $age = $Now - ([datetime]$status.evaluated_at).ToLocalTime()
    if ($age.TotalHours -gt $StaleHours) {
        $lines.Add("Audit watchdog STALE: last evaluation $([int]$age.TotalHours) h ago ($(([datetime]$status.evaluated_at).ToLocalTime().ToString('yyyy-MM-dd HH:mm'))). The Windows task 'BIMpossible Audit Watchdog' is not running; missed audits would go unnoticed.")
    }
    if ($status.PSObject.Properties['audits'] -and ($status.audits | Where-Object state -eq 'not-activated')) {
        $lines.Add("Audit watchdog NOT ACTIVATED: activation.json is missing in $StatusRoot, so no audit is being evaluated.")
    }

    $open = @()
    if (Test-Path $storePath) {
        $store = Get-Content -Raw $storePath | ConvertFrom-Json
        $open = @($store.PSObject.Properties | ForEach-Object { $_.Value } | Where-Object status -eq 'open' |
            Sort-Object @{ e = { if ($_.class -eq 'MALFUNCTION') { 0 } else { 1 } } }, @{ e = 'first_seen'; Descending = $true })
    }
    if ($open.Count) {
        $m = @($open | Where-Object class -eq 'MALFUNCTION').Count
        $lines.Add("Audit watchdog: $($open.Count) open alert(s) ($m malfunction, $($open.Count - $m) finding). Checks run on the schedule in Install-AuditWatchdog.ps1 and at logon.")
        foreach ($a in ($open | Select-Object -First $MaxShown)) {
            $lines.Add("- [$($a.class)] $($a.task) $($a.kind) (scheduled run $($a.run), detected $($a.first_seen.ToString().Replace('T', ' ')))")
            $lines.Add("    reason: $($a.message)")
            $lines.Add("    next: $($a.next_action)")
            $lines.Add("    ack id: $($a.id)")
        }
        if ($open.Count -gt $MaxShown) { $lines.Add("- ... $($open.Count - $MaxShown) more in $storePath") }
        $lines.Add("Acknowledge: pwsh -File `"$ack`" -Id `"<ack id>`" -Note `"<what you did>`"  (or -All). Acknowledging records that you saw it; it is not verified authorization.")
        try { Add-Content -Encoding utf8 (Join-Path $StatusRoot 'deliveries.log') "$($Now.ToString('s'))`tsession-start`t$(($open | ForEach-Object id) -join ',')" } catch { }
    }
}
catch {
    $lines.Add("Audit watchdog: could not read state in ${StatusRoot}: $($_.Exception.Message). Alerts may be hidden; inspect alerts.json / status.json.")
}
try { Out-Hook $lines } catch { }
exit 0
