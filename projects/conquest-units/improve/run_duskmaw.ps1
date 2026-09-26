# Duskmaw re-run, one command (hidden, headless). Reads source-copies/hero-monster.blend + hero-monster_rigged.blend and the
# shipped monster.glb only. Writes improved/duskmaw.*, rigged/duskmaw.{blend,json,glb}, improved/check_duskmaw.json,
# rigged/check_duskmaw.json, rigged/duskmaw_aabb.json, renders/duskmaw/.  -SkipBuild re-runs only the gates + renders.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$P\improve"
$OUT = "$P\renders\duskmaw"
$GLB = "C:\Users\kenne\OneDrive\Desktop\git\Conquest\game\characters\models\dark\monster.glb"
New-Item -ItemType Directory -Force $OUT | Out-Null
if (-not $SkipBuild) {
  $p = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\source-copies\hero-monster.blend`"","--factory-startup","--python","`"$I\duskmaw_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_duskmaw_build.txt" -RedirectStandardError "$I\log_duskmaw_build.err"
  $null = $p.Handle; $p.WaitForExit(); "build exit=$($p.ExitCode)"
}
# natural-scale glb: the roster would carry model_scale = the report-only cell fit (improved/duskmaw.json natural.export_cell_fit_report_only.scale)
# (the aabb check reads the fit itself: @fit)
$dmJobs = @(
  @("check_improved", @("--background","`"$P\improved\duskmaw.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_duskmaw.json`"")),
  @("check_rigged",   @("--background","`"$P\rigged\duskmaw.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_duskmaw.json`"")),
  @("render_after",   @("--background","`"$P\improved\duskmaw.blend`"","--factory-startup","--python","`"$I\duskmaw_render.py`"","--","`"$OUT\duskmaw`"")),
  @("render_before",  @("--background","--factory-startup","--python","`"$I\duskmaw_render.py`"","--","`"$OUT\before_glb`"","front,threequarter,tactical,maw","--glb","`"$GLB`"","--yaw","0")),
  @("aabb",           @("--background","--factory-startup","--python","`"$I\duskmaw_aabb_check.py`"","--","`"$P\rigged\duskmaw_aabb.json`"","shipped=$GLB#180@1.0","rerun_natural=$P\rigged\duskmaw.glb#180@1.0","rerun_roster_fit=$P\rigged\duskmaw.glb#180@fit")),
  @("clips_mp4",      @("--background","`"$P\rigged\duskmaw.blend`"","--factory-startup","--python","`"$I\duskmaw_clips.py`"","--","`"$OUT`"","768","sheet"))
)
$dmProcs = @()
foreach ($j in $dmJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_duskmaw_$($j[0]).txt" -RedirectStandardError "$I\log_duskmaw_$($j[0]).err"
  $null = $pr.Handle; $dmProcs += ,@($j[0], $pr)
}
foreach ($x in $dmProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$pb = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\duskmaw_before_after.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_duskmaw_ba.txt" -RedirectStandardError "$I\log_duskmaw_ba.err"
$null = $pb.Handle; $pb.WaitForExit(); "before_after exit=$($pb.ExitCode)"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_duskmaw_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
(Get-Content "$I\log_duskmaw_aabb.txt" | Select-String "^AABB").Line
"ALL DONE"
