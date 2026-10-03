# Professor Elias v1 DRAFT (two-speeds law: one clean headless build + probes-as-sanity + ONE comparison sheet; no gate
# wall, no determinism twin, no clips). One command, hidden, headless:
#   1. the build (improve/elias_build.py -> improved/elias.{blend,json} + improved/textures/elias_* + rigged/elias.{blend,json,glb};
#      v4: the glb always, through the shared _GLOW path: export_glb.add_glow_attr + EXPORT_KW, audited in the build)
#   2. in parallel: the full-body stills, the face / props close-ups, the face probe (improve/wren_face_probe.py, read-only
#      reuse, --report improved/elias.json), the contract checker on the IMPROVED and (v4) the RIGGED file (the rigged run
#      needs the shared checker's no-clip guard; its clip checks fail by design: no clips this pass)
#   + v2: the hair diagnosis (improve/wren_hair_diag.py, read-only reuse) on the rigged file
#   + v4: the one-tone hair still (face 3/4, every hair region painted the one hair grey in memory: shading only)
#   3. the comparison sheet + the v3 | v4 strip (improve/elias_compose.py -> renders/elias/<ver>_sheet.png, _hair_compare.png;
#      the strip = portrait, face 3/4, chin-up, front + the one-tone column)
# Logs + probe outputs live in renders/elias/ (the lane allowlist). -SkipBuild re-runs 2 + 3 only.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # (never a lowercase $p: PowerShell names are case-insensitive)
$I = "$P\improve"
$OUT = "$P\renders\elias"
$LOG = "$OUT\logs"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT, $LOG | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  if (Test-Path "$P\rigged\elias.blend") { Remove-Item "$P\rigged\elias.blend" }
  $args_ = @("--background","--factory-startup","--python","`"$I\elias_build.py`"","--","--glb")
  $bp = Start-Process -FilePath $B -ArgumentList $args_ -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\build.txt" -RedirectStandardError "$LOG\build.err"
  $null = $bp.Handle
  $bp.WaitForExit()
  "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  if (-not (Test-Path "$P\rigged\elias.blend")) { "BUILD FAILED -- see $LOG\build.txt / build.err"; Get-Content "$LOG\build.err" -Tail 15; exit 1 }
}
$RB = "`"$P\rigged\elias.blend`""
$R = "`"$I\elias_render.py`""
$VER = "elias_v4"                     # (render / probe prefix; v3 stills stay as the comparison baseline)
$W = "`"$OUT\$VER`""
$ONE = "hair_shade=186,186,182;hair_root=186,186,182;hair_ring=186,186,182;hair_tip=186,186,182;hair_inner=186,186,182;hair_crevice=186,186,182;hair_beard=186,186,182;hair_beard_shade=186,186,182;hair_beard_root=186,186,182;hair_beard_tip=186,186,182;hair_beard_crevice=186,186,182"
$vJobs = @(
  @("render_full",  @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,side,back,threequarter","--res","1024")),
  @("render_close", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"portrait,face_tq,portrait_low,glasses,staff_head,book,satchel,belt,brooch","--res","800")),
  @("render_onetone", @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\$($VER)_onetone`"","face_tq","--res","800","--palette-override","`"$ONE`"")),
  @("face_probe",   @("--background","`"$P\improved\elias.blend`"","--factory-startup","--python","`"$I\wren_face_probe.py`"","--","`"$OUT\$($VER)_face_probe`"","--report","`"$P\improved\elias.json`"")),
  @("hair_diag",    @("--background",$RB,"--factory-startup","--python","`"$I\wren_hair_diag.py`"","--","`"$OUT\$($VER)_hairdiag.json`"")),
  @("check_improved", @("--background","`"$P\improved\elias.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_improved.json`"")),
  @("check_rigged", @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_rigged.json`""))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\$($j[0]).txt" -RedirectStandardError "$LOG\$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$env:ELIAS_V = $VER; $env:ELIAS_BASE = "elias_v3"; $env:ELIAS_ONETONE = "1"
& $VPY -P "$I\elias_compose.py" 2>&1 | Out-File -Encoding utf8 "$LOG\compose.txt"
"compose exit=$LASTEXITCODE"
(Get-Content "$LOG\compose.txt" | Select-String "^(SHEET|STRIP)").Line
(Get-Content "$LOG\hair_diag.txt" | Select-String "^HAIRDIAG").Line | % { $_.Substring(0, [Math]::Min(600, $_.Length)) }
(Get-Content "$LOG\build.txt" | Select-String "^(TRIS|GRIP|GLB|RIG_DONE|BEARDSHELL|BEARDCOVER|HAIRCN|SCALPDIGEST)").Line | % { $_.Substring(0, [Math]::Min(400, $_.Length)) }
(Get-Content "$LOG\check_improved.txt" | Select-String "checks, ").Line
(Get-Content "$LOG\check_rigged.txt" | Select-String "checks, ").Line
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
