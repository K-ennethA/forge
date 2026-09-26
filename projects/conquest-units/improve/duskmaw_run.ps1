# Duskmaw v3 (shadow figure: maw / tendrils / arms / proportions), one command (hidden, headless). Reads
# source-copies/hero-monster.blend (and v2's kept outputs improved/duskmaw_v2.blend for the before column). Writes
# improved/duskmaw.*, rigged/duskmaw.{blend,json,glb}, improved/check_duskmaw.json, rigged/check_duskmaw.json,
# rigged/duskmaw_aabb.json, rigged/duskmaw_seethrough.json, rigged/duskmaw_base.json, renders/duskmaw/duskmaw_v3_*
# (+ the extra v2 views into renders/duskmaw/v2/). -SkipBuild re-runs only the gates + renders.
param([switch]$SkipBuild, [switch]$Determinism)   # -Determinism: 3 twin builds at the end, digests compared
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$P\improve"
$OUT = "$P\renders\duskmaw"
$GLB = "C:\Users\kenne\OneDrive\Desktop\git\Conquest\game\characters\models\dark\monster.glb"
New-Item -ItemType Directory -Force $OUT | Out-Null
New-Item -ItemType Directory -Force "$OUT\v2" | Out-Null
$t0 = Get-Date
if (-not $SkipBuild) {
  $bp = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\source-copies\hero-monster.blend`"","--factory-startup","--python","`"$I\duskmaw_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_duskmaw_build.txt" -RedirectStandardError "$I\log_duskmaw_build.err"
  $null = $bp.Handle; $bp.WaitForExit(); "build exit=$($bp.ExitCode) wall=$([math]::Round(((Get-Date)-$t0).TotalSeconds,1))s"   # not $p: PowerShell names are case-insensitive ($P is the project path)
  if ($bp.ExitCode -ne 0 -or -not (Select-String -Path "$I\log_duskmaw_build.txt" -Pattern "^RIG_DONE" -Quiet)) { "BUILD FAILED - stopping"; exit 1 }
}
$VIEWS = "front,threequarter,tactical,back,side,backthreequarter,maw,mawback,mawthreequarter,lowfront,head,headthreequarter,hem,hemthreequarter"
$dmJobs = @(
  @("check_improved", @("--background","`"$P\improved\duskmaw.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_duskmaw.json`"")),
  @("check_rigged",   @("--background","`"$P\rigged\duskmaw.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_duskmaw.json`"")),
  @("render_after",   @("--background","`"$P\improved\duskmaw.blend`"","--factory-startup","--python","`"$I\duskmaw_render.py`"","--","`"$OUT\duskmaw_v3`"",$VIEWS)),
  @("render_v2extra", @("--background","`"$P\improved\duskmaw_v2.blend`"","--factory-startup","--python","`"$I\duskmaw_render.py`"","--","`"$OUT\v2\duskmaw`"","hem,hemthreequarter,mawthreequarter")),
  @("aabb",           @("--background","--factory-startup","--python","`"$I\duskmaw_aabb_check.py`"","--","`"$P\rigged\duskmaw_aabb.json`"","shipped=$GLB#180@1.0","rerun_natural=$P\rigged\duskmaw.glb#180@1.0","rerun_roster_fit=$P\rigged\duskmaw.glb#180@fit")),
  @("seethrough",     @("--background","--factory-startup","--python","`"$I\duskmaw_seethrough.py`"","--","`"$P\rigged\duskmaw_seethrough.json`"","v2_improved=`"$P\improved\duskmaw_v2.blend`"","v3_improved=`"$P\improved\duskmaw.blend`"","v3_rigged=`"$P\rigged\duskmaw.blend`"")),
  @("clips_mp4",      @("--background","`"$P\rigged\duskmaw.blend`"","--factory-startup","--python","`"$I\duskmaw_clips.py`"","--","`"$OUT`"","768","sheet","--tag","v3")),
  @("base",           @("--background","--factory-startup","--python","`"$I\duskmaw_base_measure.py`"","--","`"$P\rigged\duskmaw_base.json`"","v2_improved=`"$P\improved\duskmaw_v2.blend`"","v3_improved=`"$P\improved\duskmaw.blend`""))
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
"gates+renders wall=$([math]::Round(((Get-Date)-$t0).TotalSeconds,1))s"
if ($Determinism) {
  # two-run digest: TWIN builds (--out-root under %TEMP%, never the project) compared with the main build: every build
  # digest must be identical except the normal bake, which duskmaw_bake_diff.py gates (Cycles tie-break, see there)
  function Digests($R) { $a = Get-Content "$R\improved\duskmaw.json" -Raw | ConvertFrom-Json; $r = Get-Content "$R\rigged\duskmaw.json" -Raw | ConvertFrom-Json
    "geom_colour=$($a.digest_geometry_colour) uv=$($a.uv.uv_sha) bake_ao=$($a.bake.pixel_sha.ao) rig=$($r.digest_rig)" }
  $d1 = Digests $P
  $t1 = Get-Date
  $tw = @()
  foreach ($k in 1..3) {
    $R = "$env:TEMP\duskmaw_twin$k"; Remove-Item -Recurse -Force $R -ErrorAction SilentlyContinue
    $pr = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\source-copies\hero-monster.blend`"","--factory-startup","--python","`"$I\duskmaw_build.py`"","--","--out-root","`"$R`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_duskmaw_twin$k.txt" -RedirectStandardError "$I\log_duskmaw_twin$k.err"
    $null = $pr.Handle; $tw += ,@($k, $R, $pr)
  }
  foreach ($x in $tw) { $x[2].WaitForExit(); "twin$($x[0]) exit=$($x[2].ExitCode)" }
  "twins wall=$([math]::Round(((Get-Date)-$t1).TotalSeconds,1))s"
  "MAIN  $d1"
  $same = $true
  foreach ($x in $tw) { $d = Digests $x[1]; "TWIN$($x[0]) $d"; $same = $same -and ($d -eq $d1) }
  "DIGESTS identical (all but the normal bake)=$same"
  $npys = @("$env:TEMP\duskmaw_normal_main.npy") + ($tw | % { "$env:TEMP\duskmaw_normal_duskmaw_twin$($_[0]).npy" })
  $pd = Start-Process -FilePath $B -ArgumentList (@("--background","--factory-startup","--python","`"$I\duskmaw_bake_diff.py`"","--") + $npys) -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_duskmaw_bakediff.txt" -RedirectStandardError "$I\log_duskmaw_bakediff.err"
  $null = $pd.Handle; $pd.WaitForExit(); (Get-Content "$I\log_duskmaw_bakediff.txt" | Select-String "^BAKE_DIFF").Line; "bake_diff exit=$($pd.ExitCode)"
}
"ALL DONE wall=$([math]::Round(((Get-Date)-$t0).TotalSeconds,1))s"
