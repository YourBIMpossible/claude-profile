#Requires -Version 5.1

# Regenerates a ZIP snapshot of this repo for the offline/transfer path.
# GitHub remains the source of truth - this ZIP is a transport artifact only,
# never edited by hand and never authoritative. It deliberately includes .git/
# so that extracting it yields a real working clone with 'origin' already
# configured, which is what makes reconnecting to GitHub later a plain git pull.

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
$resolvedOutput = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath
$resolvedRepo = (Resolve-Path -LiteralPath $repoRoot).ProviderPath

# Writing the archive into the tree being archived would race the enumeration
# (and bloat or corrupt the result), so refuse rather than produce a bad ZIP.
if ($resolvedOutput.TrimEnd('\').StartsWith($resolvedRepo.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must be outside the repo ($resolvedRepo). Got: $resolvedOutput"
}

$zipPath = Join-Path $resolvedOutput $zipName
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

# NOT Compress-Archive: its -Path wildcard resolves via Get-ChildItem WITHOUT
# -Force, so it silently omits hidden entries - and .git is marked Hidden on
# Windows. That produced a ZIP with no .git at all while still reporting
# success, defeating the entire point of this script. CreateFromDirectory walks
# the directory itself and includes hidden entries.
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $resolvedRepo,
    $zipPath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false
)

# Verify the promise this script makes, rather than asserting it blindly.
# Entry names are matched against BOTH separators on purpose: .NET Framework 4.x
# (what Windows PowerShell 5.1 runs on) writes zip entries with backslashes,
# while .NET Core and the zip spec itself use forward slashes. Matching only one
# silently reports "no .git" on a perfectly good archive.
$archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    $hasGit = @($archive.Entries | Where-Object {
        $_.FullName.Replace('\', '/').StartsWith('.git/')
    }).Count -gt 0
} finally {
    $archive.Dispose()
}
if (-not $hasGit) {
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    throw "Archive did not contain .git/ - refusing to publish a snapshot that cannot become a working clone."
}

Write-Host "Wrote $zipPath"
Write-Host "Verified: archive contains .git/ - extracting it produces a working clone with 'origin' already configured."
