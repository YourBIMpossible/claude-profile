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

# Stage exactly what belongs in a snapshot - git-tracked files plus .git/ -
# rather than archiving the working directory as-is. Archiving in place would
# sweep in untracked scratch files, gitignored artifacts, and (when run from a
# checkout that has worktrees under .claude/) an entire duplicate copy of the
# repo. Staging keeps the snapshot equal to "the repo", not "this disk".
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("claude-profile-stage-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging -Force | Out-Null
try {
    $tracked = & git -C $resolvedRepo ls-files
    if ($LASTEXITCODE -ne 0) { throw "git ls-files failed in $resolvedRepo - is this a git repository?" }
    foreach ($rel in $tracked) {
        if (-not $rel) { continue }
        $src = Join-Path $resolvedRepo $rel
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $dest = Join-Path $staging $rel
        $destDir = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item -LiteralPath $src -Destination $dest -Force
    }

    # .git must travel for the extracted copy to be a working clone with origin.
    # In a worktree .git is a FILE pointing into the parent repo's admin dir, so
    # copying it would produce a broken clone - refuse rather than ship one.
    $gitPath = Join-Path $resolvedRepo '.git'
    if (-not (Test-Path -LiteralPath $gitPath)) { throw "No .git found in $resolvedRepo" }
    if (-not (Get-Item -LiteralPath $gitPath -Force).PSIsContainer) {
        throw "Run this from a normal clone, not a git worktree: .git here is a file pointing into another repository, so a snapshot of it would not be a usable clone."
    }
    Copy-Item -LiteralPath $gitPath -Destination (Join-Path $staging '.git') -Recurse -Force

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $staging,
        $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )
} finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

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
