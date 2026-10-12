# winged_cat (from-scratch unit, scaffolded by new_unit_scaffold.py from the firefly pattern), one command (hidden, headless).
# Reads no source blend (the sheet only). Writes improved/winged_cat.*, improved/textures/winged_cat_*, rigged/winged_cat.{blend,json,glb},
# rigged/winged_cat__<skin>.blend per extra palette, improved/check_winged_cat.json, rigged/check_winged_cat.json, renders/winged_cat/,
# improve/log_winged_cat_*.
#   -SkipBuild re-runs only the gates + renders. The build runs TWICE in parallel: the real build and a --digest-only twin
#   (saves nothing but its digest); every digest part must match, except the two bakes, which are held to the pinned
#   tolerance gate (winged_cat_bake_diff.py) if they differ.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = Split-Path -Parent $PSScriptRoot   # this runner's own project folder (worktree-safe); never assign lowercase $p: PowerShell names are case-insensitive
$I = "$P\improve"
$U = "winged_cat"
$OUT = "$P\renders\$U"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  $bp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python-exit-code","1","--python","`"$I\$($U)_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_$($U)_build.txt" -RedirectStandardError "$I\log_$($U)_build.err"
  $dp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python-exit-code","1","--python","`"$I\$($U)_build.py`"","--","--digest-only","`"$I\log_$($U)_digest2.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_$($U)_digest2.txt" -RedirectStandardError "$I\log_$($U)_digest2.err"
  $null = $bp.Handle; $null = $dp.Handle
  $bp.WaitForExit(); "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  $dp.WaitForExit(); "digest twin exit=$($dp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  if ($bp.ExitCode -ne 0) { "BUILD FAILED - see log_$($U)_build.err"; exit 1 }
  $j1 = (Get-Content "$P\rigged\$U.json" -Raw | ConvertFrom-Json).digest
  $j2 = (Get-Content "$I\log_$($U)_digest2.json" -Raw | ConvertFrom-Json).digest
  $mismatch = @()
  foreach ($k in $j1.parts.PSObject.Properties.Name) { if ($j1.parts.$k -ne $j2.parts.$k) { $mismatch += $k } }
  $T = $env:TEMP
  $bd = & $VPY -P "$I\$($U)_bake_diff.py" "$T\$($U)_normal_main.npy" "$T\$($U)_normal_twin.npy" "$T\$($U)_ao_main.npy" "$T\$($U)_ao_twin.npy"
  $bdok = ($LASTEXITCODE -eq 0)
  $onlyBake = ($mismatch | Where-Object { $_ -notin @("bake_normal","bake_ao") }).Count -eq 0
  if ($mismatch.Count -eq 0) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=True | $($bd -join ' | ')"
  } elseif ($onlyBake) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=except-$($mismatch -join ',') | $($bd -join ' | ') gate=$(if ($bdok) { 'PASS' } else { 'FAIL' })"
  } else {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=FALSE mismatched=$($mismatch -join ',') gate=FAIL"
  }
  $line; $line | Out-File -Encoding utf8 "$I\log_$($U)_digest.txt"
}
$RB = "`"$P\rigged\$U.blend`""
$R = "`"$I\$($U)_render.py`""
$vJobs = @(
  @("check_improved", @("--background","`"$P\improved\$U.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_$U.json`"")),
  @("check_rigged",   @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_$U.json`"")),
  @("render_views",   @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\$U`"","front,threequarter,side,back,tactical","--pose","idle:1")),
  @("render_close",   @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\$U`"","focus")),
  @("render_ortho",   @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\$U`"","ortho_front,ortho_back,ortho_side")),
  @("clips_mp4",      @("--background",$RB,"--factory-startup","--python","`"$I\$($U)_clips.py`"","--","`"$OUT`"","768"))
)
foreach ($sk in (Get-ChildItem "$P\rigged\$($U)__*.blend" -ErrorAction SilentlyContinue)) {   # one front + ortho pair per extra skin
  $s = $sk.BaseName.Substring($U.Length + 2)
  $vJobs += ,@("render_skin_$s", @("--background","`"$($sk.FullName)`"","--factory-startup","--python",$R,"--","`"$OUT\$($U)_$s`"","front,ortho_front,ortho_back,ortho_side","--pose","idle:1"))
}
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_$($U)_$($j[0]).txt" -RedirectStandardError "$I\log_$($U)_$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
& $VPY -P "$I\$($U)_compose.py" 2>&1 | Out-File -Encoding utf8 "$I\log_$($U)_compose.txt"
"compose exit=$LASTEXITCODE"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_$($U)_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
