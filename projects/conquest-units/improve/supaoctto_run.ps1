# Supaoctto (the octopus superhero), one command (hidden, headless). Reads source-copies/newunit-supaoctto.blend only.
# Writes improved/supaoctto.*, improved/textures/supaoctto_*, rigged/supaoctto.{blend,json,glb}, rigged/supaoctto__deepsea.blend,
# improved/check_supaoctto.json, rigged/check_supaoctto.json, renders/supaoctto/, improve/log_supaoctto_*.
#   -SkipBuild re-runs only the gates + renders.  The build runs TWICE in parallel: the real build and a --digest-only
#   twin (saves nothing but its digest); the two digests must match (byte-determinism of every consumed array), with the
#   bake buffers compared against pinned tolerances if (and only if) the bakes are the only difference.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # never assign lowercase $p: PowerShell names are case-insensitive
$I = "$P\improve"
$OUT = "$P\renders\supaoctto"
$SRC = "$P\source-copies\newunit-supaoctto.blend"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT | Out-Null
$tAll = Get-Date
if (-not $SkipBuild) {
  $t0 = Get-Date
  $bp = Start-Process -FilePath $B -ArgumentList @("--background","`"$SRC`"","--factory-startup","--python","`"$I\supaoctto_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_supaoctto_build.txt" -RedirectStandardError "$I\log_supaoctto_build.err"
  $dp = Start-Process -FilePath $B -ArgumentList @("--background","`"$SRC`"","--factory-startup","--python","`"$I\supaoctto_build.py`"","--","--digest-only","`"$I\log_supaoctto_digest2.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_supaoctto_digest2.txt" -RedirectStandardError "$I\log_supaoctto_digest2.err"
  $null = $bp.Handle; $null = $dp.Handle
  $bp.WaitForExit(); "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$t0).TotalSeconds,1))"
  $dp.WaitForExit(); "digest twin exit=$($dp.ExitCode)"
  $j1 = (Get-Content "$P\rigged\supaoctto.json" -Raw | ConvertFrom-Json).digest
  $j2 = (Get-Content "$I\log_supaoctto_digest2.json" -Raw | ConvertFrom-Json).digest
  $mismatch = @()
  foreach ($k in $j1.parts.PSObject.Properties.Name) { if ($j1.parts.$k -ne $j2.parts.$k) { $mismatch += $k } }
  $bakeOnly = ($mismatch.Count -gt 0) -and (@($mismatch | Where-Object { $_ -notin @("bake_normal","bake_ao") }).Count -eq 0)
  if ($mismatch.Count -eq 0) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=True"
  } elseif ($bakeOnly) {
    $bd = & $VPY -P "$I\supaoctto_bake_diff.py" "$env:TEMP\supaoctto_normal_main.npy" "$env:TEMP\supaoctto_normal_twin.npy" "$env:TEMP\supaoctto_ao_main.npy" "$env:TEMP\supaoctto_ao_twin.npy"
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=except-$($mismatch -join '+') $($bd -join ' | ') gate=$(if ($LASTEXITCODE -eq 0) { 'PASS' } else { 'FAIL' })"
  } else {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=FALSE mismatched=$($mismatch -join ',') gate=FAIL"
  }
  $line; $line | Out-File -Encoding utf8 "$I\log_supaoctto_digest.txt"
}
# the variant skin blend (pure palette swap of the rigged blend's stored regions; palettes.py read-only use)
$sp = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\rigged\supaoctto.blend`"","--factory-startup","--python","`"$I\palettes.py`"","--","--unit","supaoctto","--skin","deepsea","--out","`"$P\rigged\supaoctto__deepsea.blend`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_supaoctto_skin.txt" -RedirectStandardError "$I\log_supaoctto_skin.err"
$null = $sp.Handle; $sp.WaitForExit(); "skin exit=$($sp.ExitCode)"
$vJobs = @(
  @("check_improved", @("--background","`"$P\improved\supaoctto.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_supaoctto.json`"")),
  @("check_rigged",   @("--background","`"$P\rigged\supaoctto.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_supaoctto.json`"")),
  @("render_src0",    @("--background","`"$SRC`"","--factory-startup","--python","`"$I\supaoctto_render.py`"","--","`"$OUT\source_yaw0`"","front,threequarter,side,tactical,back,front_yaw","--yaw","0")),
  @("render_src90",   @("--background","`"$SRC`"","--factory-startup","--python","`"$I\supaoctto_render.py`"","--","`"$OUT\source_yaw90`"","front_yaw","--yaw","90")),
  @("render_src180",  @("--background","`"$SRC`"","--factory-startup","--python","`"$I\supaoctto_render.py`"","--","`"$OUT\source_yaw180`"","front_yaw","--yaw","180")),
  @("render_src270",  @("--background","`"$SRC`"","--factory-startup","--python","`"$I\supaoctto_render.py`"","--","`"$OUT\source_yaw270`"","front_yaw","--yaw","270")),
  @("render_after",   @("--background","`"$P\rigged\supaoctto.blend`"","--factory-startup","--python","`"$I\supaoctto_render.py`"","--","`"$OUT\supaoctto`"","front,threequarter,tactical,side,back,cape","--pose","idle:1")),
  @("render_variant", @("--background","`"$P\rigged\supaoctto__deepsea.blend`"","--factory-startup","--python","`"$I\supaoctto_render.py`"","--","`"$OUT\supaoctto_deepsea`"","front,threequarter,tactical,side,back,cape","--pose","idle:1")),
  @("render_rest",    @("--background","`"$P\improved\supaoctto.blend`"","--factory-startup","--python","`"$I\supaoctto_render.py`"","--","`"$OUT\supaoctto_rest`"","front,threequarter")),
  @("clips_mp4",      @("--background","`"$P\rigged\supaoctto.blend`"","--factory-startup","--python","`"$I\supaoctto_clips.py`"","--","`"$OUT`"","768"))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_supaoctto_$($j[0]).txt" -RedirectStandardError "$I\log_supaoctto_$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$pb = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\supaoctto_before_after.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_supaoctto_ba.txt" -RedirectStandardError "$I\log_supaoctto_ba.err"
$null = $pb.Handle; $pb.WaitForExit(); "before_after exit=$($pb.ExitCode)"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_supaoctto_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
"total wall_s=$([math]::Round(((Get-Date)-$tAll).TotalSeconds,1))"
"ALL DONE"
