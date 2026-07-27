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

function Backup-IfRealDirectory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.LinkType -eq 'Junction') { return }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupPath = "$Path.pre-profile-backup-$stamp"
    Move-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "Backed up existing directory to $backupPath"
}

$repoRoot = $PSScriptRoot
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
Backup-IfRealDirectory -Path $skillsPath
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
$candidateRoots = @('F:\AI-Dev', (Join-Path $env:USERPROFILE 'AI-Dev'))
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
if (Test-Path -LiteralPath $settingsPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item -LiteralPath $settingsPath -Destination "$settingsPath.pre-profile-backup-$stamp" -Force
    Write-Host "Backed up existing settings.json"
}
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
if (Test-Path -LiteralPath $mcpConfigPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item -LiteralPath $mcpConfigPath -Destination "$mcpConfigPath.pre-profile-backup-$stamp" -Force
    Write-Host "Backed up existing claude_desktop_config.json"
}
[System.IO.File]::WriteAllText($mcpConfigPath, $mcpRaw, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote claude_desktop_config.json"

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Verify with: /context (check CLAUDE.md and skills load) and /plugins list"
