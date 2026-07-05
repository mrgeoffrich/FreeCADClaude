# SPDX-License-Identifier: LGPL-2.1-or-later
# End-to-end eval: launch FreeCAD, run a prompt through the FreeCADClaude agent,
# and print the resulting document snapshot.
#
#   pwsh -File eval/run.ps1                       # default prompt
#   pwsh -File eval/run.ps1 -Prompt "..."         # custom prompt
#   pwsh -File eval/run.ps1 -Prompt "..." -TimeoutSec 300
#   pwsh -File eval/run.ps1 -Case multifeature    # a named in-tree case
#   pwsh -File eval/run.ps1 -ListCases            # list named cases

param(
    [string]$Prompt = "Create a box exactly 20 x 20 x 20 mm. Do not ask questions.",
    [int]$TimeoutSec = 240,
    # Optional pass/fail check: a regex matched against the result JSON.
    [string]$Expect = "",
    # A named, in-tree case (see $cases below) so a complex multi-feature prompt
    # is repeatable rather than re-typed. Mirrors run.sh's -c/-l.
    [string]$Case = "",
    [switch]$ListCases
)

$ErrorActionPreference = "Stop"

# Named eval cases. A case sets Prompt (and may bump TimeoutSec); an explicit
# -Prompt / -TimeoutSec still overrides. Most set no Expect: for a creative
# multi-feature build the real signal is the session trace, not the snapshot.
$cases = [ordered]@{
    box          = @{ Prompt = "Create a box exactly 20 x 20 x 20 mm. Do not ask questions." }
    multifeature = @{
        Prompt = "Create a 20 x 20 x 20 mm cube, then add exactly one feature per face: " +
                 "on the BOTTOM face, cut a 5 mm radius hemisphere into the cube; " +
                 "on the LEFT face, add a raised 8 x 10 mm rectangular pad standing 4 mm off the face; " +
                 "on the RIGHT face, add a complex revolved shape standing off the face; " +
                 "on the FRONT face, cut 4 small squares into it; " +
                 "on the BACK face, add a small cylinder standing off the face. " +
                 "Work through the faces one at a time and do not ask questions."
        TimeoutSec = 600
    }
}

if ($ListCases) { "cases: $($cases.Keys -join ', ')"; exit 0 }
if ($Case) {
    if (-not $cases.ContainsKey($Case)) {
        throw "unknown case: $Case (try: $($cases.Keys -join ', '))"
    }
    $c = $cases[$Case]
    if (-not $PSBoundParameters.ContainsKey('Prompt'))     { $Prompt = $c.Prompt }
    if ($c.ContainsKey('TimeoutSec') -and
        -not $PSBoundParameters.ContainsKey('TimeoutSec')) { $TimeoutSec = $c.TimeoutSec }
}

$fc = Get-ChildItem -ErrorAction SilentlyContinue @(
    "C:\Program Files\FreeCAD*\bin\freecad.exe",
    "$env:LOCALAPPDATA\Programs\FreeCAD*\bin\freecad.exe"
) | Select-Object -First 1
if (-not $fc) { throw "Could not find freecad.exe." }

$result = Join-Path $env:TEMP "freecadclaude_eval_result.json"
Remove-Item $result -ErrorAction SilentlyContinue

$env:FREECADCLAUDE_EVAL = "1"
$env:FREECADCLAUDE_EVAL_PROMPT = $Prompt
$env:FREECADCLAUDE_EVAL_RESULT = $result
$env:FREECADCLAUDE_EVAL_TIMEOUT = "$TimeoutSec"

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
