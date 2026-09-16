# Forge - the one command that runs every test the repo has.
#
#   .\run_tests.ps1            everything: 4 pytest suites + all headless Blender suites
#   .\run_tests.ps1 -Fast      pytest suites only (no Blender launches)
#
# Exit code 0 only when every suite passed. Each suite prints one summary line;
# failures re-print their tail at the end. Nothing here opens a window.
#
# Written per the 2026-09-15 architecture review: 21 headless suites documented
# one at a time is how a real regression hides in the suite nobody re-ran.

param([switch]$Fast)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$blender = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$results = @()
$failTails = @{}

function Run-Suite([string]$Name, [scriptblock]$Body) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $out = & $Body 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $sw.Stop()
    $line = ($out | Select-String -Pattern "passed|failed|checks" | Select-Object -Last 1)
    $summary = if ($line) { $line.Line.Trim() } else { "(no summary line)" }
    $status = if ($code -eq 0) { "PASS" } else { "FAIL" }
    $script:results += [pscustomobject]@{ Suite = $Name; Status = $status; Seconds = [int]$sw.Elapsed.TotalSeconds; Summary = $summary }
    if ($code -ne 0) { $script:failTails[$Name] = ($out | Select-Object -Last 15) -join "`n" }
    Write-Host ("  [{0}] {1}  ({2}s)  {3}" -f $status, $Name, [int]$sw.Elapsed.TotalSeconds, $summary)
}

Write-Host "Forge test run  $(Get-Date -Format s)  $(git -C $root rev-parse --short HEAD)"
Write-Host ""

# --- pytest suites -----------------------------------------------------------
Run-Suite "service"   { & "$root\service\.venv\Scripts\python.exe" -m pytest "$root\service\tests" -q --tb=line }
Run-Suite "assistant" { $env:FORGE_ASSISTANT_LIVE_CONTEXT = "0"; & "$root\service\.venv\Scripts\python.exe" -m pytest "$root\assistant\tests" -q --tb=line }
Run-Suite "meshgen"   { & "$root\service\.venv\Scripts\python.exe" -m pytest "$root\meshgen\tests" -q --tb=line }
Run-Suite "mcp"       { Push-Location "$root\mcp"; & ".venv\Scripts\python.exe" -m pytest tests -q --tb=line; Pop-Location }

# --- headless Blender suites -------------------------------------------------
if (-not $Fast) {
    if (-not (Test-Path $blender)) {
        Write-Host "  [FAIL] Blender not found at $blender"
        $results += [pscustomobject]@{ Suite = "blender"; Status = "FAIL"; Seconds = 0; Summary = "binary missing" }
    } else {
        Get-ChildItem "$root\addon\tests\headless_*.py" | Sort-Object Name | ForEach-Object {
            $suite = $_.BaseName
            Run-Suite $suite { & $blender --background --factory-startup --python $_.FullName }
        }
    }
}

# --- verdict -----------------------------------------------------------------
Write-Host ""
$failed = @($results | Where-Object { $_.Status -eq "FAIL" })
Write-Host ("{0} suites, {1} failed, {2} min total" -f $results.Count, $failed.Count,
    [int](($results | Measure-Object Seconds -Sum).Sum / 60))
foreach ($name in $failTails.Keys) {
    Write-Host ""
    Write-Host "---- $name (last 15 lines) ----"
    Write-Host $failTails[$name]
}
if ($failed.Count -gt 0) { exit 1 } else { exit 0 }
