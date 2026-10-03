# General Varden v1 DRAFT (two-speeds law: one clean headless build + probes-as-sanity + ONE comparison sheet; no gate
# wall, no determinism twin, no clips). One command, hidden, headless:
#   1. the build (improve/varden_build.py -> improved/varden.{blend,json} + improved/textures/varden_* + rigged/varden.{blend,json,glb};
#      the glb goes through the shared _GLOW path: export_glb.add_glow_attr + EXPORT_KW, audited in the build)
#   2. in parallel: the full-body stills, the close-ups (face / fur mantle / sword / emblem / brooch), the face probe
#      (improve/wren_face_probe.py, read-only reuse, --report improved/varden.json), the hair diagnosis
#      (improve/wren_hair_diag.py, read-only reuse) on the rigged file, the contract checker on the IMPROVED file (report only;
#      the read-only checker crashes on a rig with no animation data -- no clips this pass)
#   3. the comparison sheet (improve/varden_compose.py -> renders/varden/<ver>_sheet.png)
# v2 (frame): + the ONE v1 | v2 compare strip (front + three-quarter full body).
# Logs + probe outputs live in renders/varden/ (the lane allowlist). -SkipBuild re-runs 2 + 3 only.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # (never a lowercase $p: PowerShell names are case-insensitive)
$I = "$P\improve"
$OUT = "$P\renders\varden"
$LOG = "$OUT\logs"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT, $LOG | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  if (Test-Path "$P\rigged\varden.blend") { Remove-Item "$P\rigged\varden.blend" }
  $args_ = @("--background","--factory-startup","--python","`"$I\varden_build.py`"")
  $bp = Start-Process -FilePath $B -ArgumentList $args_ -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\build.txt" -RedirectStandardError "$LOG\build.err"
  $null = $bp.Handle
  $bp.WaitForExit()
  "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  if (-not (Test-Path "$P\rigged\varden.blend")) { "BUILD FAILED -- see $LOG\build.txt / build.err"; Get-Content "$LOG\build.err" -Tail 15; exit 1 }
}
$RB = "`"$P\rigged\varden.blend`""
$R = "`"$I\varden_render.py`""
$VER = "varden_v3"                    # (render / probe prefix; v2 stills stay as the compare baseline)
$W = "`"$OUT\$VER`""
$vJobs = @(
  @("render_full",  @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,side,back,threequarter","--res","1024")),
  @("render_close", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"portrait,face_tq,fur,fur_back,sword,sword_full,emblem,brooch","--res","800")),
  @("render_fixed", @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\$($VER)fix`"","front,threequarter","--res","1024","--fixed","1.0,1.25")),
  @("face_probe",   @("--background","`"$P\improved\varden.blend`"","--factory-startup","--python","`"$I\wren_face_probe.py`"","--","`"$OUT\$($VER)_face_probe`"","--report","`"$P\improved\varden.json`"")),
  @("hair_diag",    @("--background",$RB,"--factory-startup","--python","`"$I\wren_hair_diag.py`"","--","`"$OUT\$($VER)_hairdiag.json`"")),
  @("check_improved", @("--background","`"$P\improved\varden.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_improved.json`""))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\$($j[0]).txt" -RedirectStandardError "$LOG\$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$env:VARDEN_V = $VER; $env:VARDEN_BASE = "varden_v2"; & $VPY -P "$I\varden_compose.py" 2>&1 | Out-File -Encoding utf8 "$LOG\compose.txt"
"compose exit=$LASTEXITCODE"
(Get-Content "$LOG\compose.txt" | Select-String "^(SHEET|STRIP)").Line
(Get-Content "$LOG\hair_diag.txt" | Select-String "^HAIRDIAG").Line | % { $_.Substring(0, [Math]::Min(600, $_.Length)) }
(Get-Content "$LOG\build.txt" | Select-String "^(TRIS|GRIP|GLB|RIG_DONE)").Line | % { $_.Substring(0, [Math]::Min(300, $_.Length)) }
(Get-Content "$LOG\check_improved.txt" | Select-String "checks, ").Line
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
