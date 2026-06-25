# SPDX-License-Identifier: LGPL-2.1-or-later
# ClaudeChat has NO Python dependencies — it drives the `claude` CLI directly,
# using your own Claude account. This script just verifies the CLI is present
# and logged in, and removes the now-unused vendor/ dir from older versions.

$ErrorActionPreference = 'Stop'

# Remove the stale vendored Python packages (from the API-SDK approach).
$cmdExe = Get-ChildItem -ErrorAction SilentlyContinue @(
    'C:\Program Files\FreeCAD*\bin\freecadcmd.exe',
    "$env:LOCALAPPDATA\Programs\FreeCAD*\bin\freecadcmd.exe"
) | Select-Object -First 1
if ($cmdExe) {
    $userDir = (& $cmdExe.FullName -c "import FreeCAD; print(FreeCAD.getUserAppDataDir())" 2>$null |
                Select-String -Pattern '.+FreeCAD.+' | Select-Object -Last 1).ToString().Trim()
    $vendor = Join-Path (Join-Path $userDir 'Mod\ClaudeChat') 'vendor'
    if ($userDir -and (Test-Path $vendor)) {
        Write-Host "Removing unused vendor dir: $vendor" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $vendor
    }
}

# Verify the claude CLI.
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude) {
    Write-Host "claude CLI NOT found on PATH." -ForegroundColor Red
    Write-Host "Install it:  npm install -g @anthropic-ai/claude-code" -ForegroundColor Cyan
    Write-Host "Then run 'claude' once to log in with your Claude account." -ForegroundColor Cyan
    exit 1
}
Write-Host "Found claude CLI: $claude" -ForegroundColor Green
& $claude --version
Write-Host "Make sure you've run 'claude' once to log in, then restart FreeCAD." -ForegroundColor Green
