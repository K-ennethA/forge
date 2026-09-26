# Fire Sprite (from-scratch unit; v1.2 = the movement wave: real idle + floaty-step walk), one command (hidden, headless). Reads no source blend (sheet + sketch).
# Writes improved/firesprite.*, improved/textures/firesprite_*, rigged/firesprite.{blend,json,glb},
# rigged/firesprite__soulfire.blend, improved/check_firesprite.json, rigged/check_firesprite.json, renders/firesprite/,
# improve/log_firesprite_*.
#   -SkipBuild re-runs only the gates + renders. The build runs TWICE in parallel: the real build and a --digest-only twin
#   (saves nothing but its digest); every digest part must match, except the two bakes, which are held to the pinned
#   tolerance gate (firesprite_bake_diff.py) if they differ.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # never assign lowercase $p: PowerShell names are case-insensitive
$I = "$P\improve"
$OUT = "$P\renders\firesprite"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  $bp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\firesprite_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_firesprite_build.txt" -RedirectStandardError "$I\log_firesprite_build.err"
  $dp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\firesprite_build.py`"","--","--digest-only","`"$I\log_firesprite_digest2.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_firesprite_digest2.txt" -RedirectStandardError "$I\log_firesprite_digest2.err"
  $null = $bp.Handle; $null = $dp.Handle
  $bp.WaitForExit(); "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  $dp.WaitForExit(); "digest twin exit=$($dp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  $j1 = (Get-Content "$P\rigged\firesprite.json" -Raw | ConvertFrom-Json).digest
  $j2 = (Get-Content "$I\log_firesprite_digest2.json" -Raw | ConvertFrom-Json).digest
  $mismatch = @()
  foreach ($k in $j1.parts.PSObject.Properties.Name) { if ($j1.parts.$k -ne $j2.parts.$k) { $mismatch += $k } }
  $T = $env:TEMP
  $bd = & $VPY -P "$I\firesprite_bake_diff.py" "$T\firesprite_normal_main.npy" "$T\firesprite_normal_twin.npy" "$T\firesprite_ao_main.npy" "$T\firesprite_ao_twin.npy"
  $bdok = ($LASTEXITCODE -eq 0)
  $onlyBake = ($mismatch | Where-Object { $_ -notin @("bake_normal","bake_ao") }).Count -eq 0
  if ($mismatch.Count -eq 0) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=True | $($bd -join ' | ')"
  } elseif ($onlyBake) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=except-$($mismatch -join ',') | $($bd -join ' | ') gate=$(if ($bdok) { 'PASS' } else { 'FAIL' })"
  } else {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=FALSE mismatched=$($mismatch -join ',') gate=FAIL"
  }
  $line; $line | Out-File -Encoding utf8 "$I\log_firesprite_digest.txt"
}
$RB = "`"$P\rigged\firesprite.blend`""
$RV = "`"$P\rigged\firesprite__soulfire.blend`""
$R = "`"$I\firesprite_render.py`""
$vJobs = @(
  @("check_improved", @("--background","`"$P\improved\firesprite.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_firesprite.json`"")),
  @("check_rigged",   @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_firesprite.json`"")),
  @("render_views",   @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\firesprite`"","front,threequarter,side,back,tactical")),
  @("render_close",   @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\firesprite`"","face,crown,wand")),
  @("render_ortho",   @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\firesprite`"","ortho_front,ortho_side")),
  @("render_soulfire",@("--background",$RV,"--factory-startup","--python",$R,"--","`"$OUT\firesprite_soulfire`"","front,ortho_front,ortho_side")),
  @("clips_idle",     @("--background",$RB,"--factory-startup","--python","`"$I\firesprite_clips.py`"","--","`"$OUT`"","768","idle")),
  @("clips_walk",     @("--background",$RB,"--factory-startup","--python","`"$I\firesprite_clips.py`"","--","`"$OUT`"","768","walk"))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_firesprite_$($j[0]).txt" -RedirectStandardError "$I\log_firesprite_$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
& $VPY -P "$I\firesprite_compose.py" 2>&1 | Out-File -Encoding utf8 "$I\log_firesprite_compose.txt"
"compose exit=$LASTEXITCODE"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_firesprite_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
