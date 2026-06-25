# SPDX-License-Identifier: LGPL-2.1-or-later
# Deploys the ClaudeChat addon into the FreeCAD user Mod directory by copying.
# Run from anywhere:  pwsh -File deploy.ps1   (or right-click > Run with PowerShell)

$ErrorActionPreference = 'Stop'

# Source = the folder this script lives in.
$Source = $PSScriptRoot

# Find FreeCAD's console binary so we can ask it for the *real* user data dir.
# FreeCAD 1.x uses a version-namespaced path like %APPDATA%\FreeCAD\v1-1\Mod,
# so we must not hard-code %APPDATA%\FreeCAD\Mod.
$cmdExe = Get-ChildItem -ErrorAction SilentlyContinue @(
    'C:\Program Files\FreeCAD*\bin\freecadcmd.exe',
    "$env:LOCALAPPDATA\Programs\FreeCAD*\bin\freecadcmd.exe"
) | Select-Object -First 1

$ModDir = $null
if ($cmdExe) {
    # getUserAppDataDir() returns the versioned dir, e.g. ...\FreeCAD\v1-1\
    $userDir = (& $cmdExe.FullName -c "import FreeCAD; print(FreeCAD.getUserAppDataDir())" 2>$null |
                Select-String -Pattern '.+FreeCAD.+' | Select-Object -Last 1).ToString().Trim()
    if ($userDir -and (Test-Path $userDir)) {
        $ModDir = Join-Path $userDir 'Mod'
    }
}
if (-not $ModDir) {
    # Fallback: newest versioned dir under %APPDATA%\FreeCAD, else unversioned.
    $base = Join-Path $env:APPDATA 'FreeCAD'
    $verDir = Get-ChildItem -Path $base -Directory -Filter 'v*' -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending | Select-Object -First 1
    $ModDir = if ($verDir) { Join-Path $verDir.FullName 'Mod' } else { Join-Path $base 'Mod' }
    Write-Host "  (could not query FreeCAD; falling back to $ModDir)" -ForegroundColor Yellow
}

$Dest = Join-Path $ModDir 'ClaudeChat'

# Remove any stale copy left in the unversioned location by earlier deploys.
$staleDest = Join-Path $env:APPDATA 'FreeCAD\Mod\ClaudeChat'
if (($staleDest -ne $Dest) -and (Test-Path $staleDest)) {
    Write-Host "  removing stale copy: $staleDest" -ForegroundColor Yellow
    Remove-Item -Recurse -Force $staleDest
}

Write-Host "Deploying ClaudeChat" -ForegroundColor Cyan
Write-Host "  from: $Source"
Write-Host "  to:   $Dest"

# Start clean so removed files don't linger in the deployed copy.
# Clean the destination but PRESERVE the vendored dependencies (vendor/), so a
# code-only redeploy doesn't force a re-install of claude-agent-sdk.
if (Test-Path $Dest) {
    Get-ChildItem -Path $Dest -Force | Where-Object { $_.Name -ne 'vendor' } |
        Remove-Item -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# Copy everything except build/VCS cruft, the deploy/install scripts, and the
# source-side vendor dir (deps live in the deployed copy, installed in place).
$exclude = @('__pycache__', '.git', '.gitignore', '.gitattributes', 'deploy.ps1', 'install_deps.ps1', 'vendor', 'eval')
Get-ChildItem -Path $Source -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $Dest -Recurse -Force
}

# Drop any __pycache__ dirs that came along inside subfolders.
Get-ChildItem -Path $Dest -Recurse -Directory -Filter '__pycache__' -Force |
    Remove-Item -Recurse -Force

Write-Host "Done. Restart FreeCAD, then pick the 'Claude Chat' workbench." -ForegroundColor Green
