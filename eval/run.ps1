# SPDX-License-Identifier: LGPL-2.1-or-later
# End-to-end eval: launch FreeCAD, run a prompt through the ClaudeChat agent,
# and print the resulting document snapshot.
#
#   pwsh -File eval/run.ps1                       # default prompt
#   pwsh -File eval/run.ps1 -Prompt "..."         # custom prompt
#   pwsh -File eval/run.ps1 -Prompt "..." -TimeoutSec 300

param(
    [string]$Prompt = "Create a box exactly 20 x 20 x 20 mm. Do not ask questions.",
    [int]$TimeoutSec = 240,
    # Optional pass/fail check: a regex matched against the result JSON.
    [string]$Expect = ""
)

$ErrorActionPreference = "Stop"

$fc = Get-ChildItem -ErrorAction SilentlyContinue @(
    "C:\Program Files\FreeCAD*\bin\freecad.exe",
    "$env:LOCALAPPDATA\Programs\FreeCAD*\bin\freecad.exe"
) | Select-Object -First 1
if (-not $fc) { throw "Could not find freecad.exe." }

$result = Join-Path $env:TEMP "claudechat_eval_result.json"
Remove-Item $result -ErrorAction SilentlyContinue

$env:CLAUDECHAT_EVAL = "1"
$env:CLAUDECHAT_EVAL_PROMPT = $Prompt
$env:CLAUDECHAT_EVAL_RESULT = $result
$env:CLAUDECHAT_EVAL_TIMEOUT = "$TimeoutSec"

Write-Host "Launching FreeCAD eval..." -ForegroundColor Cyan
Write-Host "  prompt: $Prompt"

# freecad.exe is a launcher that returns immediately while the GUI runs in the
# background, so we poll for the result file the eval writes, then quit.
Start-Process -FilePath $fc.FullName
$grace = $TimeoutSec + 120
$deadline = (Get-Date).AddSeconds($grace)
while (-not (Test-Path $result) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
}
if (Test-Path $result) {
    Write-Host "Eval finished." -ForegroundColor Green
} else {
    Write-Host "Eval overran ${grace}s - killing FreeCAD." -ForegroundColor Yellow
    Get-Process freecad -ErrorAction SilentlyContinue | Stop-Process -Force
}

if (-not (Test-Path $result)) {
    Write-Host "No result file produced (eval did not complete)." -ForegroundColor Red
    exit 2
}

$raw = Get-Content $result -Raw
Write-Host "=== EVAL RESULT ===" -ForegroundColor Green
$raw

if ($Expect) {
    if ($raw -match $Expect) {
        Write-Host "`nPASS - matched /$Expect/" -ForegroundColor Green
        exit 0
    } else {
        Write-Host "`nFAIL - did not match /$Expect/" -ForegroundColor Red
        exit 1
    }
}
