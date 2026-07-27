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

$resolvedRepo = (Resolve-Path -LiteralPath $repoRoot).ProviderPath

# Writing the archive into the tree being archived would race the enumeration
# (and bloat or corrupt the result), so refuse rather than produce a bad ZIP.
# Compare with a trailing separator on BOTH sides: a bare StartsWith would also
# reject a sibling like 'claude-profile-zips', which is perfectly valid. Checked
# before creating the directory so a rejected path leaves nothing behind.
$repoPrefix = $resolvedRepo.TrimEnd('\') + '\'
$outProbe = ([System.IO.Path]::GetFullPath($OutputDirectory)).TrimEnd('\') + '\'
if ($outProbe.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must be outside the repo ($resolvedRepo). Got: $outProbe"
}

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}
$resolvedOutput = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath

# The snapshot is built from committed state (git ls-files), so anything not
# committed is silently absent from it - including brand-new untracked files,
# which is exactly what editing skills through the junction produces. Say so
# rather than let a snapshot quietly ship without the work it appears to carry.
$dirty = @(& git -C $resolvedRepo status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "git status failed in $resolvedRepo - is this a git repository?" }
if ($dirty.Count -gt 0) {
    Write-Warning "Working tree is not clean. The snapshot is built from COMMITTED state:"
    Write-Warning "  - untracked files are omitted entirely"
    Write-Warning "  - uncommitted edits to tracked files travel as-is on disk"
    foreach ($line in $dirty) { Write-Warning "    $line" }
    Write-Warning "Commit first if this work belongs in the snapshot."
}

# The success message must not promise a remote that does not exist. Without an
# origin, the extracted copy cannot 'git pull' to catch up - which is the entire
# point of shipping .git/ - so establish the truth here and report it honestly.
# Listed first rather than calling 'remote get-url origin' and inspecting the
# failure: under $ErrorActionPreference='Stop', a native command's stderr is
# promoted to a terminating error, so probing for a missing origin would throw
# in exactly the no-origin case this is meant to detect. 'git remote' prints
# nothing to stderr and exits 0 when there are no remotes.
$originUrl = $null
if (@(& git -C $resolvedRepo remote) -contains 'origin') {
    $originUrl = (& git -C $resolvedRepo remote get-url origin).Trim()
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
if ($originUrl) {
    Write-Host "Verified: archive contains .git/ and origin is $originUrl"
    Write-Host "Extracting it produces a working clone that can 'git pull' to catch up."
} else {
    Write-Host "Verified: archive contains .git/"
    Write-Warning "This repo has NO 'origin' remote, so the extracted copy cannot 'git pull' to catch up."
    Write-Warning "It is a point-in-time file transfer only. Add a remote and push, then re-export."
}
