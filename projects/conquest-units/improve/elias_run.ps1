# Professor Elias v1 DRAFT (two-speeds law: one clean headless build + probes-as-sanity + ONE comparison sheet; no gate
# wall, no determinism twin, no clips). One command, hidden, headless:
#   1. the build (improve/elias_build.py -> improved/elias.{blend,json} + improved/textures/elias_* + rigged/elias.{blend,json})
#   2. in parallel: the full-body stills, the face / props close-ups, the face probe (improve/wren_face_probe.py, read-only
#      reuse, --report improved/elias.json), the contract checker on the IMPROVED file (report only; on the rigged file
#      the read-only checker crashes on a rig with no animation data -- no clips this pass)
#   + v2: the hair diagnosis (improve/wren_hair_diag.py, read-only reuse) on the rigged file
#   3. the comparison sheet + the v1 | v2 hair strip (improve/elias_compose.py -> renders/elias/<ver>_sheet.png, _hair_compare.png)
# Logs + probe outputs live in renders/elias/ (the lane allowlist). -SkipBuild re-runs 2 + 3 only. -Glb also exports
# rigged/elias.glb.
param([switch]$SkipBuild, [switch]$Glb)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # (never a lowercase $p: PowerShell names are case-insensitive)
$I = "$P\improve"
$OUT = "$P\renders\elias"
$LOG = "$OUT\logs"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT, $LOG | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  $args_ = @("--background","--factory-startup","--python","`"$I\elias_build.py`"")
  if ($Glb) { $args_ += @("--","--glb") }
  $bp = Start-Process -FilePath $B -ArgumentList $args_ -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\build.txt" -RedirectStandardError "$LOG\build.err"
  $null = $bp.Handle
  $bp.WaitForExit()
  "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  if (-not (Test-Path "$P\rigged\elias.blend")) { "BUILD FAILED -- see $LOG\build.txt / build.err"; Get-Content "$LOG\build.err" -Tail 15; exit 1 }
}
$RB = "`"$P\rigged\elias.blend`""
$R = "`"$I\elias_render.py`""
$VER = "elias_v3"                     # (render / probe prefix; v1 stills stay as the comparison baseline)
$W = "`"$OUT\$VER`""
$vJobs = @(
  @("render_full",  @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,side,back,threequarter","--res","1024")),
  @("render_close", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"portrait,face_tq,portrait_low,glasses,staff_head,book,satchel,belt,brooch","--res","800")),
  @("face_probe",   @("--background","`"$P\improved\elias.blend`"","--factory-startup","--python","`"$I\wren_face_probe.py`"","--","`"$OUT\$($VER)_face_probe`"","--report","`"$P\improved\elias.json`"")),
  @("hair_diag",    @("--background",$RB,"--factory-startup","--python","`"$I\wren_hair_diag.py`"","--","`"$OUT\$($VER)_hairdiag.json`"")),
  @("check_improved", @("--background","`"$P\improved\elias.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_improved.json`""))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\$($j[0]).txt" -RedirectStandardError "$LOG\$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$env:ELIAS_V = $VER; $env:ELIAS_BASE = "elias_v2"; & $VPY -P "$I\elias_compose.py" 2>&1 | Out-File -Encoding utf8 "$LOG\compose.txt"
"compose exit=$LASTEXITCODE"
(Get-Content "$LOG\compose.txt" | Select-String "^(SHEET|STRIP)").Line
(Get-Content "$LOG\hair_diag.txt" | Select-String "^HAIRDIAG").Line | % { $_.Substring(0, [Math]::Min(600, $_.Length)) }
(Get-Content "$LOG\build.txt" | Select-String "^(TRIS|GRIP|RIG_DONE)").Line | % { $_.Substring(0, [Math]::Min(300, $_.Length)) }
(Get-Content "$LOG\check_improved.txt" | Select-String "checks, ").Line
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
