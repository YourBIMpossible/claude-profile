#Requires -Version 5.1

param(
    # Install hook sources even when they have uncommitted changes. Off by default:
    # the pinned copies under ~/.claude/hooks must be reviewed (committed) code (C1).
    [switch]$AllowUncommittedHooks
)

$ErrorActionPreference = 'Stop'

function ConvertTo-ForwardSlash {
    param([string]$Path)
    return $Path -replace '\\', '/'
}

function Backup-IfRealFile {
    param([string]$Path, [string]$ExpectedContent)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $existing = Read-Utf8Text $Path
    if ($existing -and ($existing.Trim() -ceq $ExpectedContent.Trim())) { return }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupPath = "$Path.pre-profile-backup-$stamp"
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "Backed up existing file to $backupPath"
}

function Read-Utf8Text {
    # Windows PowerShell 5.1's Get-Content -Raw decodes a BOM-less file with the ANSI
    # code page, which corrupts non-ASCII text that is written back. Read as UTF-8.
    param([string]$Path)
    return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}

function Test-JsonObject {
    param($Value)
    return $Value -is [System.Management.Automation.PSCustomObject]
}

function Merge-ProfileSettings {
    # The settings.json to write: the existing settings with only the Profile-owned
    # parts updated. hooks come from the generated settings (already merged with the
    # existing hooks the template does not own); permissions.additionalDirectories is
    # the union of existing and detected entries; enabledPlugins and
    # extraKnownMarketplaces gain the template's missing entries without overriding
    # existing ones; every other template key is a default, used only when the key is
    # absent. Keys the template does not know are kept as they are.
    param($Existing, $Generated)
    $out = [ordered]@{}
    if ($null -ne $Existing) { foreach ($p in $Existing.PSObject.Properties) { $out[$p.Name] = $p.Value } }
    foreach ($p in $Generated.PSObject.Properties) {
        $name = $p.Name
        $current = if ($out.Contains($name)) { $out[$name] } else { $null }
        if ($name -eq 'hooks') {
            $out[$name] = $p.Value
        } elseif ($name -eq 'permissions') {
            $perm = [ordered]@{}
            if ($null -ne $current) { foreach ($q in $current.PSObject.Properties) { $perm[$q.Name] = $q.Value } }
            foreach ($q in $p.Value.PSObject.Properties) {
                if ($q.Name -ne 'additionalDirectories' -and -not $perm.Contains($q.Name)) { $perm[$q.Name] = $q.Value }
            }
            # Collect with += rather than an if-expression: PowerShell unrolls a
            # one-element array returned from an expression into a bare string.
            $candidates = @()
            if ($perm.Contains('additionalDirectories')) { $candidates += @($perm['additionalDirectories']) }
            $candidates += @($p.Value.additionalDirectories)
            $dirs = New-Object System.Collections.ArrayList
            $seen = @{}
            foreach ($d in $candidates) {
                if ([string]::IsNullOrWhiteSpace([string]$d)) { continue }
                $key = (ConvertTo-ForwardSlash ([string]$d)).TrimEnd('/').ToLowerInvariant()
                if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; [void]$dirs.Add($d) }
            }
            $perm['additionalDirectories'] = [object[]]$dirs.ToArray()
            $out[$name] = [pscustomobject]$perm
        } elseif ($name -in @('enabledPlugins', 'extraKnownMarketplaces')) {
            $map = [ordered]@{}
            if ($null -ne $current) { foreach ($q in $current.PSObject.Properties) { $map[$q.Name] = $q.Value } }
            foreach ($q in $p.Value.PSObject.Properties) { if (-not $map.Contains($q.Name)) { $map[$q.Name] = $q.Value } }
            $out[$name] = [pscustomobject]$map
        } elseif (-not $out.Contains($name)) {
            $out[$name] = $p.Value
        }
    }
    return [pscustomobject]$out
}

function Get-JsonLeafPaths {
    # Flattens parsed JSON into path -> compact JSON text; an array is one leaf, so a
    # changed hook event reports its event path, never a command or its arguments.
    param($Node, [string]$Path, [System.Collections.IDictionary]$Into)
    if (Test-JsonObject $Node) {
        $props = @($Node.PSObject.Properties)
        if ($props.Count -eq 0) { $Into[$Path] = '{}' }
        foreach ($p in $props) { Get-JsonLeafPaths -Node $p.Value -Path "$Path/$($p.Name)" -Into $Into }
    } else {
        $Into[$Path] = ConvertTo-Json -InputObject $Node -Depth 32 -Compress
    }
}

function Get-SettingsChanges {
    # One Kind/Path record per JSON leaf path whose parsed value differs between Before
    # and After. Values and paths compare case-sensitively; formatting and key order
    # do not count.
    param($Before, $After)
    $b = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)
    $a = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)
    if ($null -ne $Before) { Get-JsonLeafPaths -Node $Before -Path '' -Into $b }
    if ($null -ne $After) { Get-JsonLeafPaths -Node $After -Path '' -Into $a }
    foreach ($k in $b.Keys) {
        if (-not $a.ContainsKey($k)) { [pscustomobject]@{ Kind = 'removed'; Path = $k } }
        elseif (-not [string]::Equals($a[$k], $b[$k], [System.StringComparison]::Ordinal)) { [pscustomobject]@{ Kind = 'changed'; Path = $k } }
    }
    foreach ($k in $a.Keys) {
        if (-not $b.ContainsKey($k)) { [pscustomobject]@{ Kind = 'added'; Path = $k } }
    }
}

function Write-SettingsPlan {
    # Prints the change counts and each changed path with its change type - never
    # values. Names under /env are masked because a variable's name can be sensitive.
    param([object[]]$Changes)
    $counts = @{ added = 0; removed = 0; changed = 0 }
    $lines = New-Object 'System.Collections.Generic.SortedSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($c in @($Changes)) {
        if ($null -eq $c) { continue }
        $counts[$c.Kind]++
        [void]$lines.Add("  $($c.Kind) $($c.Path -creplace '^/env/.*', '/env/*')")
    }
    Write-Host "settings.json plan: $($counts.added) added, $($counts.removed) removed, $($counts.changed) changed"
    foreach ($line in $lines) { Write-Host $line }
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

function Get-UnmanagedHookGroups {
    # Returns @{ Event; Group } for each hook group in the existing settings that
    # still holds handlers the template does not own. A command handler is owned
    # when its command or args point under one of ManagedRoots, or at one of
    # ManagedFileNames in any .claude/hooks directory (a settings.json carried over
    # from another profile path); a prompt handler is owned when the template
    # carries the identical prompt.
    param($Existing, $Generated, [string[]]$ManagedRoots, [string[]]$ManagedFileNames)
    $templatePrompts = @()
    foreach ($prop in $Generated.PSObject.Properties) {
        foreach ($group in @($prop.Value)) {
            foreach ($handler in @($group.hooks)) {
                if ($handler.type -eq 'prompt') { $templatePrompts += $handler.prompt }
            }
        }
    }
    $roots = @($ManagedRoots | ForEach-Object { (ConvertTo-ForwardSlash $_).TrimEnd('/').ToLowerInvariant() + '/' })
    $pinnedNames = @($ManagedFileNames | ForEach-Object { '/.claude/hooks/' + $_.ToLowerInvariant() })
    $result = @()
    foreach ($prop in $Existing.PSObject.Properties) {
        foreach ($group in @($prop.Value)) {
            $kept = @()
            foreach ($handler in @($group.hooks)) {
                if ($handler.type -eq 'prompt') {
                    if ($templatePrompts -notcontains $handler.prompt) { $kept += $handler }
                    continue
                }
                $text = (ConvertTo-ForwardSlash ((@($handler.command) + @($handler.args)) -join ' ')).ToLowerInvariant()
                $owned = $false
                foreach ($root in $roots) { if ($text.Contains($root)) { $owned = $true } }
                foreach ($name in $pinnedNames) { if ($text.Contains($name)) { $owned = $true } }
                if (-not $owned) { $kept += $handler }
            }
            if ($kept.Count -gt 0) {
                $copy = [ordered]@{}
                if ($group.PSObject.Properties.Name -contains 'matcher') { $copy.matcher = $group.matcher }
                $copy.hooks = $kept
                $result += @{ Event = $prop.Name; Group = [pscustomobject]$copy }
            }
        }
    }
    return $result
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

# Hook preflight, before anything is written. Bootstrap installs these files as
# pinned copies that run on every prompt or tool call (C1 in hooks/README.md), so
# every hook the template registers must exist here, and the code being pinned must
# be committed - a pinned copy of an uncommitted edit is exactly the
# unreviewed-code hazard C1 exists to prevent.
$hookDataFiles = @('closure_audit_repos.json')
$hookSources = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'hooks') -File |
    Where-Object { $_.Extension -in @('.py', '.ps1') -or $hookDataFiles -contains $_.Name })
$settingsTemplatePath = Join-Path $repoRoot 'settings.template.json'
$templateHookFiles = @([regex]::Matches((Get-Content -Raw -LiteralPath $settingsTemplatePath), '__CLAUDE_HOOKS_DIR__/([^"/]+)') |
    ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
$missingHookSources = @($templateHookFiles | Where-Object { @($hookSources.Name) -notcontains $_ })
if ($missingHookSources.Count -gt 0) {
    throw "settings.template.json registers hooks missing from hooks/: $($missingHookSources -join ', ')"
}
$hooksRevision = 'unknown (no git)'
if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath (Join-Path $repoRoot '.git'))) {
    $hookPaths = @($hookSources | ForEach-Object { 'hooks/' + $_.Name })
    $hooksDirty = @(& git -C $repoRoot status --porcelain -- $hookPaths)
    if ($LASTEXITCODE -ne 0) { throw "git status failed in $repoRoot (exit $LASTEXITCODE)" }
    if ($hooksDirty.Count -gt 0 -and -not $AllowUncommittedHooks) {
        throw @"
Hook sources have uncommitted changes:
  $($hooksDirty -join "`n  ")

Bootstrap installs them as pinned copies under ~/.claude/hooks (C1), which must be
reviewed code. Commit (or discard) these changes and run bootstrap again, or pass
-AllowUncommittedHooks to install them anyway.
"@
    }
    $sha = (& git -C $repoRoot rev-parse --short HEAD).Trim()
    $hooksRevision = $sha + $(if ($hooksDirty.Count -gt 0) { ' (with uncommitted hook changes)' } else { '' })
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

# 4. Detect known project directories (siblings on the drive root since the 2026-08-23
# AI-Dev extraction - BIMpossible/BIMpossible-Workspace are no longer nested under AI-Dev)
$candidateDriveRoots = @('F:\', 'C:\', $env:USERPROFILE)
$additionalDirs = @()
foreach ($driveRoot in $candidateDriveRoots) {
    foreach ($name in @('BIMpossible', 'BIMpossible-Workspace')) {
        $candidate = Join-Path $driveRoot $name
        if ((Test-Path -LiteralPath $candidate) -and ($additionalDirs -notcontains (ConvertTo-ForwardSlash $candidate))) {
            $additionalDirs += (ConvertTo-ForwardSlash $candidate)
            Write-Host "Project directory detected: $candidate"
        }
    }
}
if (-not $additionalDirs) {
    Write-Host "No known project directories found - additionalDirectories will be empty"
}

# 5a. Install pinned hook copies (C1 in hooks/README.md). settings.json only ever
# references these copies, so a branch switch or pull in this repo never changes
# code that runs on every prompt or tool call; re-running bootstrap is the
# deliberate update step.
$hooksDir = Join-Path $claudeDir 'hooks'
if (-not (Test-Path -LiteralPath $hooksDir)) {
    New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
}
Write-Host "Installing pinned hooks from revision $hooksRevision -> $hooksDir"
foreach ($source in $hookSources) {
    $bytes = [System.IO.File]::ReadAllBytes($source.FullName)
    if ($source.Name -eq 'session-start-pull.ps1') {
        # An installed copy must not derive the repo root from its own location
        # (C1 rule 4) - it runs from ~/.claude/hooks, outside the repo.
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        $derived = '$repoRoot = Split-Path -Parent $PSScriptRoot'
        if (-not $text.Contains($derived)) { throw "session-start-pull.ps1 no longer derives `$repoRoot as bootstrap expects; update the pinning step." }
        $pinned = "`$repoRoot = '$($repoRoot -replace "'", "''")'  # pinned by bootstrap (C1): this copy runs outside the repo"
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($text.Replace($derived, $pinned))
    }
    $target = Join-Path $hooksDir $source.Name
    if (Test-Path -LiteralPath $target) {
        $current = [System.IO.File]::ReadAllBytes($target)
        if ([Convert]::ToBase64String($current) -eq [Convert]::ToBase64String($bytes)) { continue }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        Copy-Item -LiteralPath $target -Destination "$target.pre-profile-backup-$stamp" -Force
        Write-Host "Backed up installed hook to $target.pre-profile-backup-$stamp"
    }
    [System.IO.File]::WriteAllBytes($target, $bytes)
    Write-Host "Installed hook: $target"
}

# 5. Generate settings.json from template
$settingsRaw = Get-Content -Raw -LiteralPath $settingsTemplatePath

$dirsJson = ($additionalDirs | ForEach-Object { $_ | ConvertTo-Json }) -join ",`n      "
$settingsRaw = $settingsRaw.Replace('"__ADDITIONAL_DIRECTORIES__"', $dirsJson)

# Hooks register against the pinned copies installed in step 5a, never this
# working tree (C1 in hooks/README.md).
$hooksDirForward = ConvertTo-ForwardSlash $hooksDir
if ($hooksDirForward -match '"') { throw "Hooks directory path contains a double quote: $hooksDirForward" }
$settingsRaw = $settingsRaw.Replace('__CLAUDE_HOOKS_DIR__', $hooksDirForward)

$leftoverTokens = @([regex]::Matches($settingsRaw, '__[A-Z_]+__') | ForEach-Object { $_.Value } | Sort-Object -Unique)
if ($leftoverTokens.Count -gt 0) {
    throw "Generated settings.json still contains unresolved tokens: $($leftoverTokens -join ', ')"
}

try {
    $generated = $settingsRaw | ConvertFrom-Json
} catch {
    throw "Generated settings.json is not valid JSON: $($_.Exception.Message)"
}

# Every hook file the template registers must now exist as an installed copy -
# a registration pointing at a missing file is a silently dead hook.
foreach ($match in [regex]::Matches($settingsRaw, [regex]::Escape($hooksDirForward) + '/([^"/]+)')) {
    $registered = Join-Path $hooksDir $match.Groups[1].Value
    if (-not (Test-Path -LiteralPath $registered)) { throw "Template registers a hook that was not installed: $registered" }
}

$settingsPath = Join-Path $claudeDir 'settings.json'

# The template owns every hook it registers. Hook entries in the existing
# settings.json that the template does not own (machine-specific hooks from
# other repos) are carried over rather than silently dropped; entries that point
# into the installed hooks directory or this repo's hooks/ are the template's,
# so the template's version replaces them. Anything else under the repo (a skill
# script, say) is machine-specific and is kept.
# settings.json is shared with the user: Claude Code, /config, /permissions and
# hand edits all write it. Only the Profile-owned parts are updated
# (Merge-ProfileSettings); every other key is kept, and a file that cannot be read
# as a settings object is refused rather than replaced.
$preserved = @()
$existing = $null
if (Test-Path -LiteralPath $settingsPath) {
    # An empty file is not a fresh install - something truncated it - and ConvertFrom-Json
    # would read it as "no settings", so the template would silently replace it.
    $existingText = Read-Utf8Text $settingsPath
    if ([string]::IsNullOrWhiteSpace($existingText)) {
        throw "Existing settings.json is empty; refusing to replace it. Restore it from a backup or delete it, then run bootstrap again."
    }
    try {
        $existing = $existingText | ConvertFrom-Json
    } catch {
        throw "Existing settings.json is not valid JSON; refusing to replace it. Fix or move it, then run bootstrap again: $($_.Exception.Message)"
    }
    if ($null -eq $existing) {
        throw "Existing settings.json is JSON null, not a settings object; refusing to replace it."
    }
    if ($null -ne $existing -and -not (Test-JsonObject $existing)) {
        throw "Existing settings.json is not a JSON object; refusing to replace it."
    }
    foreach ($key in @('hooks', 'permissions', 'enabledPlugins', 'extraKnownMarketplaces')) {
        if ($null -ne $existing -and $null -ne $existing.$key -and -not (Test-JsonObject $existing.$key)) {
            throw "Existing settings.json has a '$key' value that is not a JSON object; refusing to replace it."
        }
    }
    if ($existing -and $existing.hooks) {
        $preserved = @(Get-UnmanagedHookGroups -Existing $existing.hooks -Generated $generated.hooks -ManagedRoots @($hooksDirForward, "$repoRootForward/hooks") -ManagedFileNames @($hookSources | ForEach-Object { $_.Name }))
    }
}
if ($preserved.Count -gt 0) {
    foreach ($item in $preserved) {
        $hookEvent = $item.Event
        if ($generated.hooks.PSObject.Properties.Name -contains $hookEvent) {
            $generated.hooks.$hookEvent = @($generated.hooks.$hookEvent) + $item.Group
        } else {
            $generated.hooks | Add-Member -NotePropertyName $hookEvent -NotePropertyValue @($item.Group)
        }
        Write-Host "Kept $(@($item.Group.hooks).Count) machine-specific $hookEvent hook(s) not owned by the template"
    }
}
$merged = Merge-ProfileSettings -Existing $existing -Generated $generated
if ($null -ne $existing) {
    $mergedKeys = @($merged.PSObject.Properties.Name)
    $lost = @($existing.PSObject.Properties.Name | Where-Object { $mergedKeys -notcontains $_ })
    if ($lost.Count -gt 0) { throw "Settings merge would drop top-level keys: $($lost -join ', ') - refusing to write." }
}
$settingsRaw = ConvertTo-Json -InputObject $merged -Depth 32
# Decide on parsed values, not text: a file Claude Code re-saved in its own formatting
# is still correct, and rewriting it would only churn backups.
$changes = @(Get-SettingsChanges -Before $existing -After $merged)
if ($null -ne $existing -and $changes.Count -eq 0) {
    Write-Host "settings.json already correct - left untouched"
} else {
    Write-SettingsPlan -Changes $changes
    Backup-IfRealFile -Path $settingsPath -ExpectedContent $settingsRaw
    [System.IO.File]::WriteAllText($settingsPath, $settingsRaw, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Wrote settings.json"
}

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
    $existingRaw = Read-Utf8Text $mcpConfigPath
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
