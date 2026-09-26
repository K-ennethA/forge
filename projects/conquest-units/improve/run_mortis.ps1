# Mortis rework, one command (hidden, headless). Reads source-copies/hero-necromancer.blend + the shipped glb only.
# Writes improved/mortis.*, rigged/mortis.*, improved/check_mortis.json, rigged/check_mortis.json,
# improved/mortis_glb_share.json, renders/mortis/.  -SkipBuild re-runs only the gates + renders.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$P\improve"
$OUT = "$P\renders\mortis"
$GLB = "C:\Users\kenne\OneDrive\Desktop\git\Conquest\game\characters\models\dark\necromancer.glb"
New-Item -ItemType Directory -Force $OUT | Out-Null
if (-not $SkipBuild) {
  $p = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\source-copies\hero-necromancer.blend`"","--factory-startup","--python","`"$I\mortis_rework.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_mortis_build.txt" -RedirectStandardError "$I\log_mortis_build.err"
  $null = $p.Handle; $p.WaitForExit(); "build exit=$($p.ExitCode)"
}
$jobs = @(
  @("check_improved", @("--background","`"$P\improved\mortis.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_mortis.json`"")),
  @("check_rigged",   @("--background","`"$P\rigged\mortis.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_mortis.json`"")),
  @("render_after",   @("--background","`"$P\improved\mortis.blend`"","--factory-startup","--python","`"$I\mortis_render.py`"","--","`"$OUT\mortis`"")),
  @("render_before",  @("--background","--factory-startup","--python","`"$I\mortis_render.py`"","--","`"$OUT\before_glb`"","front,threequarter,tactical","--glb","`"$GLB`"","--yaw","0")),
  @("glb_audit",      @("--background","`"$P\source-copies\hero-necromancer.blend`"","--factory-startup","--python","`"$I\mortis_glb_audit.py`"","--","`"$GLB`"","`"$P\improved\mortis_glb_share.json`"")),
  @("idle_mp4",       @("--background","`"$P\rigged\mortis.blend`"","--factory-startup","--python","`"$I\render_animated.py`"","--","`"$OUT`"","768","sheet"))
)
$procs = @()
foreach ($j in $jobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_mortis_$($j[0]).txt" -RedirectStandardError "$I\log_mortis_$($j[0]).err"
  $null = $pr.Handle; $procs += ,@($j[0], $pr)
}
foreach ($x in $procs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$pb = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\mortis_before_after.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_mortis_ba.txt" -RedirectStandardError "$I\log_mortis_ba.err"
$null = $pb.Handle; $pb.WaitForExit(); "before_after exit=$($pb.ExitCode)"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_mortis_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
"ALL DONE"
