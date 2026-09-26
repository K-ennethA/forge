# Vampito (mosquitopire sculpt, vampito name), one command (hidden, headless). Reads source-copies/newunit-mosquitopire.blend
# only. Writes improved/vampito.*, improved/textures/vampito_*, rigged/vampito.{blend,json,glb}, improved/check_vampito.json,
# rigged/check_vampito.json, renders/vampito/, improve/log_vampito_*.
#   -SkipBuild re-runs only the gates + renders.  The build runs TWICE in parallel: the real build and a --digest-only
#   twin (saves nothing but its digest); the two digests must match (byte-determinism of every consumed array).
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # never assign lowercase $p: PowerShell names are case-insensitive
$I = "$P\improve"
$OUT = "$P\renders\vampito"
$SRC = "$P\source-copies\newunit-mosquitopire.blend"
New-Item -ItemType Directory -Force $OUT | Out-Null
if (-not $SkipBuild) {
  $t0 = Get-Date
  $bp = Start-Process -FilePath $B -ArgumentList @("--background","`"$SRC`"","--factory-startup","--python","`"$I\vampito_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_vampito_build.txt" -RedirectStandardError "$I\log_vampito_build.err"
  $dp = Start-Process -FilePath $B -ArgumentList @("--background","`"$SRC`"","--factory-startup","--python","`"$I\vampito_build.py`"","--","--digest-only","`"$I\log_vampito_digest2.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_vampito_digest2.txt" -RedirectStandardError "$I\log_vampito_digest2.err"
  $null = $bp.Handle; $null = $dp.Handle
  $bp.WaitForExit(); "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$t0).TotalSeconds,1))"
  $dp.WaitForExit(); "digest twin exit=$($dp.ExitCode)"
  # Per-part digest gate. Every part must match EXCEPT bake_normal, which is not byte-deterministic on this
  # mesh (per-process Cycles tie-break on the thin membrane; see vampito_bake_diff.py for the measurement) and
  # is instead compared against pinned pixel tolerances.
  $j1 = (Get-Content "$P\rigged\vampito.json" -Raw | ConvertFrom-Json).digest
  $j2 = (Get-Content "$I\log_vampito_digest2.json" -Raw | ConvertFrom-Json).digest
  $mismatch = @()
  foreach ($k in $j1.parts.PSObject.Properties.Name) { if ($j1.parts.$k -ne $j2.parts.$k) { $mismatch += $k } }
  if ($mismatch.Count -eq 0) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=True"
  } elseif ($mismatch.Count -eq 1 -and $mismatch[0] -eq "bake_normal") {
    $VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
    $bd = & $VPY -P "$I\vampito_bake_diff.py" "$env:TEMP\vampito_normal_main.npy" "$env:TEMP\vampito_normal_twin.npy"
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=except-bake $bd gate=$(if ($LASTEXITCODE -eq 0) { 'PASS' } else { 'FAIL' })"
  } else {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=FALSE mismatched=$($mismatch -join ',') gate=FAIL"
  }
  $line; $line | Out-File -Encoding utf8 "$I\log_vampito_digest.txt"
}
$vJobs = @(
  @("check_improved", @("--background","`"$P\improved\vampito.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_vampito.json`"")),
  @("check_rigged",   @("--background","`"$P\rigged\vampito.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_vampito.json`"")),
  @("render_src0",    @("--background","`"$SRC`"","--factory-startup","--python","`"$I\vampito_render.py`"","--","`"$OUT\source_yaw0`"","front,threequarter,side,tactical","--yaw","0")),
  @("render_src180",  @("--background","`"$SRC`"","--factory-startup","--python","`"$I\vampito_render.py`"","--","`"$OUT\source_yaw180`"","front,side,threequarter","--yaw","180")),
  @("render_after",   @("--background","`"$P\rigged\vampito.blend`"","--factory-startup","--python","`"$I\vampito_render.py`"","--","`"$OUT\vampito`"","front,threequarter,tactical,side,back","--pose","idle:4")),   # idle frame 4 = mid-downstroke (wings at the stroke centre), hovering
  @("render_rest",    @("--background","`"$P\improved\vampito.blend`"","--factory-startup","--python","`"$I\vampito_render.py`"","--","`"$OUT\vampito_rest`"","front,threequarter")),
  @("clips_mp4",      @("--background","`"$P\rigged\vampito.blend`"","--factory-startup","--python","`"$I\vampito_clips.py`"","--","`"$OUT`"","768","sheet"))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_vampito_$($j[0]).txt" -RedirectStandardError "$I\log_vampito_$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$pb = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\vampito_before_after.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_vampito_ba.txt" -RedirectStandardError "$I\log_vampito_ba.err"
$null = $pb.Handle; $pb.WaitForExit(); "before_after exit=$($pb.ExitCode)"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_vampito_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
"ALL DONE"
