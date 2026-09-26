# Geode build, one command (hidden, headless). Reads source-copies/hero-gem_knight.blend + palettes/geode/ only.
# Writes improved/geode.*, rigged/geode.*, rigged/geode__amethyst.blend, improved/check_geode.json,
# rigged/check_geode.json, rigged/geode_gltf_probe.json, renders/geode/.  -SkipBuild re-runs only the gates + renders.
# Motion v2 (2026-09-25): stills geode_v2_* (default) / geode_v2_amethyst_*, clips geode_walk.mp4 + geode_idle_v2.mp4
# with contact sheets (geode_anim_render.py binds the arc-flicker shape-key slot). v1 renders are left untouched.
param([switch]$SkipBuild)
$T0 = Get-Date
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$P\improve"
$OUT = "$P\renders\geode"
$TMPGLB = Join-Path ([System.IO.Path]::GetTempPath()) "geode_gltf_probe.glb"
New-Item -ItemType Directory -Force $OUT | Out-Null
if (-not $SkipBuild) {
  $pbuild = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\source-copies\hero-gem_knight.blend`"","--factory-startup","--python","`"$I\geode_build.py`"","--","--skins","default,amethyst") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_geode_build.txt" -RedirectStandardError "$I\log_geode_build.err"
  $null = $pbuild.Handle; $pbuild.WaitForExit(); "build exit=$($pbuild.ExitCode)"
  if ($pbuild.ExitCode -ne 0) { "BUILD FAILED - see log_geode_build.*"; exit 1 }
}
$jobs = @(
  @("check_improved",  @("--background","`"$P\improved\geode.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_geode.json`"")),
  @("check_rigged",    @("--background","`"$P\rigged\geode.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_geode.json`"")),
  @("render_default",  @("--background","`"$P\rigged\geode.blend`"","--factory-startup","--python","`"$I\geode_render.py`"","--","`"$OUT\geode_v2`"")),
  @("render_amethyst", @("--background","`"$P\rigged\geode__amethyst.blend`"","--factory-startup","--python","`"$I\geode_render.py`"","--","`"$OUT\geode_v2_amethyst`"","front,threequarter,tactical,arcs_closeup")),
  @("gltf_probe",      @("--background","`"$P\rigged\geode.blend`"","--factory-startup","--python","`"$I\geode_gltf_probe.py`"","--","`"$TMPGLB`"","`"$P\rigged\geode_gltf_probe.json`"")),
  @("anim_mp4",        @("--background","`"$P\rigged\geode.blend`"","--factory-startup","--python","`"$I\geode_anim_render.py`"","--","`"$OUT`"","768"))
)
$procs = @()
foreach ($j in $jobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_geode_$($j[0]).txt" -RedirectStandardError "$I\log_geode_$($j[0]).err"
  $null = $pr.Handle; $procs += ,@($j[0], $pr)
}
foreach ($x in $procs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$pb = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\geode_before_after.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_geode_ba.txt" -RedirectStandardError "$I\log_geode_ba.err"
$null = $pb.Handle; $pb.WaitForExit(); "before_after exit=$($pb.ExitCode)"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_geode_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
(Get-Content "$I\log_geode_gltf_probe.txt" | Select-String "GLTF_PROBE").Line
if (Test-Path $TMPGLB) { Remove-Item $TMPGLB -Force }
"wall_s=$([math]::Round(((Get-Date) - $T0).TotalSeconds, 1))"
"ALL DONE"
