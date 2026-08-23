#Requires -Version 5.1

$ErrorActionPreference = 'Stop'

function ConvertTo-ForwardSlash {
    param([string]$Path)
    return $Path -replace '\\', '/'
}

function Backup-IfRealFile {
    param([string]$Path, [string]$ExpectedContent)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $existing = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Trim() -eq $ExpectedContent.Trim())) { return }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupPath = "$Path.pre-profile-backup-$stamp"
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "Backed up existing file to $backupPath"
}

function Find-JsonObjectSpan {
    # Returns the index of the '{' opening the given top-level key's object value
    # and the index of its matching '}', tracking string literals and escapes so
    # braces inside strings do not throw the count off. Null if the key is absent.
    param([string]$Text, [string]$Key)
    $m = [regex]::Match($Text, '"' + [regex]::Escape($Key) + '"\s*:\s*\{')
    if (-not $m.Success) { return $null }
    $open = $Text.IndexOf('{', $m.Index + $m.Length - 1)
    $depth = 0; $inString = $false; $escaped = $false
    for ($i = $open; $i -lt $Text.Length; $i++) {
        $c = $Text[$i]
        if ($inString) {
            if ($escaped) { $escaped = $false }
            elseif ($c -eq '\') { $escaped = $true }
            elseif ($c -eq '"') { $inString = $false }
            continue
        }
        if ($c -eq '"') { $inString = $true }
        elseif ($c -eq '{') { $depth++ }
        elseif ($c -eq '}') {
            $depth--
            if ($depth -eq 0) { return @{ Start = $open; End = $i } }
        }
    }
    return $null
}

function Backup-IfRealDirectory {
    param([string]$Path, [string]$ExpectedTarget)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.LinkType -eq 'Junction') {
        $currentTarget = @($item.Target)[0]
        if ($currentTarget -and ($currentTarget.TrimEnd('\') -eq $ExpectedTarget.TrimEnd('\'))) { return }
        # Junction exists but points at a different (stale or dangling) target - e.g. the
        # repo was relocated since the last bootstrap. Remove just the junction/reparse
        # point itself, never the target's contents: [IO.Directory]::Delete($path, $false)
        # deletes a reparse point without following it, unlike Remove-Item -Recurse, which
        # has historically followed symlinks/junctions into their target on some versions.
        [System.IO.Directory]::Delete($Path, $false)
        Write-Host "Removed stale skills junction (was pointing at $currentTarget) - its old target and contents are untouched"
        return
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupPath = "$Path.pre-profile-backup-$stamp"
    Move-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "Backed up existing directory to $backupPath"
}

$repoRoot = $PSScriptRoot

# The CLAUDE.md stub relies on Claude Code's @import, which fails SILENTLY when
# the path contains a space - the file is simply never loaded, with no error or
# warning (anthropics/claude-code#56927, closed as not planned; no quoting or
# escaping syntax exists). Bootstrap would otherwise report success on a machine
# whose instructions never actually load, which is the exact failure this repo
# exists to prevent. Refuse up front, before writing anything, so the machine is
# left untouched rather than half-configured.
# Note this is reachable via the documented fallback location: %USERPROFILE% is
# "C:\Users\First Last" on any account whose Windows username contains a space.
if ($repoRoot -match ' ') {
    throw @"
Repo path contains a space:
  $repoRoot

Claude Code's @import silently fails on paths containing spaces, so
~/.claude/CLAUDE.md would be written and appear fine while never actually
loading - no error, no warning, just missing instructions.

Move or re-clone this repo somewhere without spaces, then run bootstrap again:
  C:\claude-profile
  F:\Claude-Profile

(The skills junction and generated JSON configs handle spaces fine - it is
only the CLAUDE.md @import that does not.)
"@
}

$claudeDir = Join-Path $env:USERPROFILE '.claude'
if (-not (Test-Path -LiteralPath $claudeDir)) {
    New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null
}

Write-Host "Repo root: $repoRoot"
Write-Host "Claude dir: $claudeDir"

# 1. CLAUDE.md stub
$claudeMdPath = Join-Path $claudeDir 'CLAUDE.md'
$repoRootForward = ConvertTo-ForwardSlash $repoRoot
$stubContent = "@$repoRootForward/CLAUDE.md"
Backup-IfRealFile -Path $claudeMdPath -ExpectedContent $stubContent
[System.IO.File]::WriteAllText($claudeMdPath, $stubContent, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote CLAUDE.md stub: $stubContent"

# 2. skills junction
$skillsPath = Join-Path $claudeDir 'skills'
$skillsTarget = Join-Path $repoRoot 'skills'
Backup-IfRealDirectory -Path $skillsPath -ExpectedTarget $skillsTarget
if (-not (Test-Path -LiteralPath $skillsPath)) {
    New-Item -ItemType Junction -Path $skillsPath -Target $skillsTarget | Out-Null
    Write-Host "Created skills junction -> $skillsTarget"
} else {
    Write-Host "skills junction already present"
}

# 3. Detect Revit
$revitExePath = 'C:\Program Files\Autodesk\Revit 2027 MCP Server Read-Tools Technical Preview\Autodesk.RevitMcpServer.Stdio.exe'
$hasRevit = Test-Path -LiteralPath $revitExePath
Write-Host "Revit MCP server detected: $hasRevit"

# 4. Detect workspace root and known project directories
$candidateRoots = @('F:\AI-Dev', 'C:\AI-Dev', (Join-Path $env:USERPROFILE 'AI-Dev'))
$workspaceRoot = $candidateRoots | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$additionalDirs = @()
if ($workspaceRoot) {
    Write-Host "Workspace root detected: $workspaceRoot"
    foreach ($name in @('BIMpossible', 'BIMpossible_Workspace')) {
        $candidate = Join-Path $workspaceRoot $name
        if (Test-Path -LiteralPath $candidate) {
            $additionalDirs += (ConvertTo-ForwardSlash $candidate)
        }
    }
} else {
    Write-Host "No workspace root found - additionalDirectories will be empty"
}

# 5. Generate settings.json from template
$settingsTemplatePath = Join-Path $repoRoot 'settings.template.json'
$settingsRaw = Get-Content -Raw -LiteralPath $settingsTemplatePath

$dirsJson = ($additionalDirs | ForEach-Object { $_ | ConvertTo-Json }) -join ",`n      "
$settingsRaw = $settingsRaw.Replace('"__ADDITIONAL_DIRECTORIES__"', $dirsJson)

$hookScriptPath = ConvertTo-ForwardSlash (Join-Path $repoRoot 'hooks\session-start-pull.ps1')
$hookScriptJson = $hookScriptPath | ConvertTo-Json
$settingsRaw = $settingsRaw.Replace('"__HOOK_SCRIPT_PATH__"', $hookScriptJson)

try {
    $null = $settingsRaw | ConvertFrom-Json
} catch {
    throw "Generated settings.json is not valid JSON: $($_.Exception.Message)"
}

$settingsPath = Join-Path $claudeDir 'settings.json'
Backup-IfRealFile -Path $settingsPath -ExpectedContent $settingsRaw
[System.IO.File]::WriteAllText($settingsPath, $settingsRaw, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote settings.json"

# 6. Generate claude_desktop_config.json from template
$mcpTemplatePath = Join-Path $repoRoot 'mcp\claude_desktop_config.template.json'
$mcpRaw = Get-Content -Raw -LiteralPath $mcpTemplatePath

$revitEntry = ''
if ($hasRevit) {
    $revitExeJson = $revitExePath | ConvertTo-Json
    $revitEntry = (',' + "`n" + '    "autodesk-revit-2027": {' + "`n" + '      "command": ' + $revitExeJson + ',' + "`n" + '      "args": []' + "`n" + '    }')
}
$mcpRaw = $mcpRaw.Replace('__REVIT_MCP_ENTRY__', $revitEntry)

try {
    $null = $mcpRaw | ConvertFrom-Json
} catch {
    throw "Generated claude_desktop_config.json is not valid JSON: $($_.Exception.Message)"
}

$mcpConfigDir = Join-Path $env:APPDATA 'Claude'
if (-not (Test-Path -LiteralPath $mcpConfigDir)) {
    New-Item -ItemType Directory -Path $mcpConfigDir -Force | Out-Null
}
$mcpConfigPath = Join-Path $mcpConfigDir 'claude_desktop_config.json'

# This file is NOT ours to own. On a machine that has run Claude Desktop it also
# holds coworkUserFilesPath and a large 'preferences' object - Chrome-extension
# pairing, trusted folders, folder-consent grants, feature flags. On this
# machine mcpServers was 263 bytes of a 33KB file. Overwriting it with the
# rendered template would silently drop all of that (backed up, but the running
# app would lose pairing and grants), so replace ONLY the mcpServers value and
# leave every other byte untouched.
if (-not (Test-Path -LiteralPath $mcpConfigPath)) {
    # Fresh machine: no existing state to preserve, write the template as-is.
    [System.IO.File]::WriteAllText($mcpConfigPath, $mcpRaw, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Wrote claude_desktop_config.json (new file)"
} else {
    $existingRaw = Get-Content -Raw -LiteralPath $mcpConfigPath
    try {
        $existingObj = $existingRaw | ConvertFrom-Json
    } catch {
        throw "Existing claude_desktop_config.json is not valid JSON; refusing to edit it: $($_.Exception.Message)"
    }
    $existingKeys = @($existingObj.PSObject.Properties.Name)

    # The replacement text is the template's own mcpServers object, lifted verbatim.
    $srcSpan = Find-JsonObjectSpan -Text $mcpRaw -Key 'mcpServers'
    if (-not $srcSpan) { throw "Template is missing an mcpServers object" }
    $newBlock = $mcpRaw.Substring($srcSpan.Start, $srcSpan.End - $srcSpan.Start + 1)

    $dstSpan = Find-JsonObjectSpan -Text $existingRaw -Key 'mcpServers'
    if ($dstSpan) {
        $merged = $existingRaw.Substring(0, $dstSpan.Start) + $newBlock + $existingRaw.Substring($dstSpan.End + 1)
    } else {
        # Key absent entirely: insert it right after the opening brace.
        $firstBrace = $existingRaw.IndexOf('{')
        if ($firstBrace -lt 0) { throw "Existing claude_desktop_config.json has no opening brace" }
        $merged = $existingRaw.Substring(0, $firstBrace + 1) + "`n  `"mcpServers`": " + $newBlock + "," + $existingRaw.Substring($firstBrace + 1)
    }

    # Prove the edit preserved everything else before writing it.
    try {
        $mergedObj = $merged | ConvertFrom-Json
    } catch {
        throw "Merged claude_desktop_config.json is not valid JSON - refusing to write: $($_.Exception.Message)"
    }
    $mergedKeys = @($mergedObj.PSObject.Properties.Name)
    $lostKeys = @($existingKeys | Where-Object { $mergedKeys -notcontains $_ })
    if ($lostKeys.Count -gt 0) {
        throw "Merge would drop top-level keys: $($lostKeys -join ', ') - refusing to write."
    }
    if ($existingObj.preferences) {
        $prefBefore = @($existingObj.preferences.PSObject.Properties.Name).Count
        $prefAfter = @($mergedObj.preferences.PSObject.Properties.Name).Count
        if ($prefAfter -ne $prefBefore) {
            throw "Merge would change preferences sub-key count ($prefBefore -> $prefAfter) - refusing to write."
        }
    }

    if ($merged -ne $existingRaw) {
        Backup-IfRealFile -Path $mcpConfigPath -ExpectedContent $merged
        [System.IO.File]::WriteAllText($mcpConfigPath, $merged, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "Updated mcpServers in claude_desktop_config.json (preserved $($existingKeys.Count) top-level keys)"
    } else {
        Write-Host "claude_desktop_config.json already correct - left untouched"
    }
}

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Verify with: /context (check CLAUDE.md and skills load) and /plugin list"
