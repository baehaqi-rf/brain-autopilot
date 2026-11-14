# ==============================================================================
# Brain Autopilot - Installer (Windows PowerShell 5.1+ / PowerShell 7+)
#
# 1. Links ~/.gemini/config/skills/brain-autopilot -> <clone>/skills/brain-autopilot
#    via a directory junction (no admin rights needed).
# 2. Installs ~/.gemini/config/agents/brain-autopilot.md (thin wrapper).
# 3. Installs the `brain-autopilot` Python package in editable mode so the CLI
#    and MCP server (`brain-autopilot mcp`) are available system-wide.
#
# NOTE: keep this file pure ASCII for Windows PowerShell 5.1 compatibility.
# ==============================================================================

param(
    [switch]$Local,
    [switch]$SkipPip
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/baehaqi-rf/brain-autopilot.git"

# --- Bootstrap mode: executed via Invoke-Expression, no script directory -------
if (-not $PSScriptRoot) {
    $cloneDir = $env:BRAIN_AUTOPILOT_HOME
    if (-not $cloneDir) { $cloneDir = Join-Path $HOME ".gemini\brain-autopilot" }
    if (Test-Path (Join-Path $cloneDir ".git")) {
        Write-Host "Updating existing clone: $cloneDir" -ForegroundColor Yellow
        git -C $cloneDir pull --ff-only
    } else {
        Write-Host "Cloning $RepoUrl -> $cloneDir" -ForegroundColor Yellow
        git clone $RepoUrl $cloneDir
    }
    if ($LASTEXITCODE -ne 0) { throw "git failed (exit $LASTEXITCODE)." }
    & (Join-Path $cloneDir "install.ps1") -Local:$Local -SkipPip:$SkipPip
    return
}

$repoDir   = $PSScriptRoot
$srcSkill  = Join-Path $repoDir "skills\brain-autopilot"
$srcAgent  = Join-Path $repoDir "agents\brain-autopilot.md"
$configDir = Join-Path $HOME ".gemini\config"
$tgtSkill  = Join-Path $configDir "skills\brain-autopilot"
$tgtAgents = Join-Path $configDir "agents"
$archive   = Join-Path $HOME ".gemini\archived_skills"
$stamp     = Get-Date -Format "yyyyMMdd-HHmmss"

if (-not (Test-Path (Join-Path $srcSkill "SKILL.md"))) { throw "SKILL.md not found under $srcSkill" }

function Move-ToArchive([string]$Path, [string]$Label) {
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $dest = Join-Path $archive "$Label-$stamp"
    Move-Item -Force $Path $dest
    Write-Host "  archived: $Path -> $dest" -ForegroundColor DarkGray
}

function Test-IsLink([string]$Path) {
    $item = Get-Item -Force $Path
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Get-LinkTarget([string]$Path) {
    $t = @((Get-Item -Force $Path).Target)[0]
    if ($t) { return $t.TrimEnd('\') } else { return "" }
}

Write-Host "Installing Brain Autopilot from $repoDir" -ForegroundColor Cyan

# --- 1. Skill: directory junction into the clone -------------------------------
New-Item -ItemType Directory -Force -Path (Split-Path $tgtSkill) | Out-Null
$needLink = $true
if (Test-Path $tgtSkill) {
    if (Test-IsLink $tgtSkill) {
        if ((Get-LinkTarget $tgtSkill) -ieq $srcSkill.TrimEnd('\')) {
            Write-Host "Skill junction already points to this clone." -ForegroundColor Green
            $needLink = $false
        } else {
            Write-Host "Re-pointing skill junction (was: $(Get-LinkTarget $tgtSkill))" -ForegroundColor Yellow
            cmd /c rmdir "$tgtSkill" | Out-Null
        }
    } else {
        Move-ToArchive $tgtSkill "brain-autopilot"
    }
}
if ($needLink) {
    New-Item -ItemType Junction -Path $tgtSkill -Target $srcSkill | Out-Null
    Write-Host "Linked skill: $tgtSkill -> $srcSkill" -ForegroundColor Green
}

# --- 2. Agents: thin wrappers --------------------------------------------------
New-Item -ItemType Directory -Force -Path $tgtAgents | Out-Null
Get-ChildItem -Path (Join-Path $repoDir "agents") -Filter "*.md" | ForEach-Object {
    Copy-Item -Force $_.FullName $tgtAgents
    Write-Host "Installed agent: $(Join-Path $tgtAgents $_.Name)" -ForegroundColor Green
}

# --- 3. Editable Python package install ----------------------------------------
if (-not $SkipPip) {
    Write-Host "Installing brain-autopilot Python package (editable)..." -ForegroundColor Yellow
    python -m pip install -e $repoDir --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install -e failed (exit $LASTEXITCODE)" }
    Write-Host "Installed Python package: brain-autopilot" -ForegroundColor Green
}

# --- 4. Optional per-workspace copy --------------------------------------------
if ($Local) {
    $localSkill = Join-Path (Get-Location) ".agent\skills\brain-autopilot"
    New-Item -ItemType Directory -Force -Path $localSkill | Out-Null
    Copy-Item -Recurse -Force (Join-Path $srcSkill "*") $localSkill
    Write-Host "Copied workspace skill: $localSkill" -ForegroundColor Green
}

# --- 5. Self-check -------------------------------------------------------------
if (-not (Test-Path (Join-Path $tgtSkill "SKILL.md"))) { throw "Self-check failed: SKILL.md not reachable through $tgtSkill" }
Write-Host ""
Write-Host "Done. Type /brain-autopilot in Google Antigravity or run 'python -m brain_autopilot --help'." -ForegroundColor Cyan
