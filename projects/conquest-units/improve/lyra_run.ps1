# Lyra v1 DRAFT (two-speeds law: one clean headless build + probes-as-sanity + ONE comparison strip; no gate wall, no clips,
# no determinism twin). One command, hidden, headless:
#   0. the palette (improve/lyra_palette.py: every region PIXEL-SAMPLED from the saved sheet -> palettes/lyra/default.json)
#   1. the build (improve/lyra_build.py -> improved/lyra.{blend,json} + improved/textures/lyra_* + rigged/lyra.{blend,json,glb};
#      the glb through the shared _GLOW path, its _GLOW values audited ALL ZERO in the build: L3)
#   2. in parallel: the full-body stills, the head / ponytail views, the one-tone hair stills (every hair region = the sampled
#      hair tone in memory: shading only), the close-ups (book hold, belt, satchel, capelet emblem, chest, boots), the three
#      ortho views, the contract checker on the IMPROVED and the RIGGED file (rigged clip checks fail by design: no clips),
#      the face probe (improve/wren_face_probe.py, read-only reuse, --report improved/lyra.json) and the hair diagnosis
#      (improve/wren_hair_diag.py, read-only reuse) on the rigged file
#   3. the comparison strip <ver>_sheet_compare.png = BUILD | SHEET for front / side / back + the stills sheet
# Logs + probe / checker outputs live in renders/lyra/. -SkipBuild re-runs 2 + 3 only.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # (never a lowercase $p: PowerShell names are case-insensitive)
$I = "$P\improve"
$OUT = "$P\renders\lyra"
$LOG = "$OUT\logs"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
$VER = "lyra_v1"
New-Item -ItemType Directory -Force $OUT, $LOG | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  & $VPY -P "$I\lyra_palette.py" 2>&1 | Out-File -Encoding utf8 "$LOG\palette.txt"
  "palette exit=$LASTEXITCODE"
  if (Test-Path "$P\rigged\lyra.blend") { Remove-Item "$P\rigged\lyra.blend" }
  $args_ = @("--background","--factory-startup","--python","`"$I\lyra_build.py`"","--","--glb")
  $bp = Start-Process -FilePath $B -ArgumentList $args_ -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\build.txt" -RedirectStandardError "$LOG\build.err"
  $null = $bp.Handle
  $bp.WaitForExit()
  "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  if (-not (Test-Path "$P\rigged\lyra.blend")) { "BUILD FAILED -- see $LOG\build.txt / build.err"; Get-Content "$LOG\build.err" -Tail 15; exit 1 }
}
$RB = "`"$P\rigged\lyra.blend`""
$R = "`"$I\lyra_render.py`""
$W = "`"$OUT\$VER`""
$G1 = "64,47,41"                      # the one-tone = the SAMPLED hair (palette "hair")
$ONE = (@("hair","hair_shade","hair_root","hair_ring","hair_tip","hair_inner","hair_crevice") | ForEach-Object { "$_=$G1" }) -join ";"
$vJobs = @(
  @("render_full",  @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,threequarter,side,back","--res","1024")),
  @("render_head",  @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"head_front,head_tq,head_side,head_back,tail_side,tail_back","--res","800")),
  @("render_onetone", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"head_tq,head_side,head_back","--res","800","--palette-override","`"$ONE`"","--suffix","_onetone")),
  @("render_close", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"hold,hold_tq,hold_side,hold_top,belt,satchel,back_emblem,chest,boots","--res","800")),
  @("render_ortho", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"ortho_front,ortho_side,ortho_back","--res","1400")),
  @("check_improved", @("--background","`"$P\improved\lyra.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_improved.json`"")),
  @("check_rigged", @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_rigged.json`"")),
  @("face_probe", @("--background","`"$P\improved\lyra.blend`"","--factory-startup","--python","`"$I\wren_face_probe.py`"","--","`"$OUT\$($VER)_face_probe`"","--report","`"$P\improved\lyra.json`"")),
  @("hair_diag", @("--background",$RB,"--factory-startup","--python","`"$I\wren_hair_diag.py`"","--","`"$OUT\$($VER)_hair_diag.json`""))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\$($j[0]).txt" -RedirectStandardError "$LOG\$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$env:LYRA_V = $VER
& $VPY -P "$I\lyra_compose.py" 2>&1 | Out-File -Encoding utf8 "$LOG\compose.txt"
"compose exit=$LASTEXITCODE"
(Get-Content "$LOG\compose.txt" | Select-String "^(SHEET|STRIP)").Line
(Get-Content "$LOG\build.txt" | Select-String "^(TRIS|GLOWGATE|HOLD|GRIP|GLB|MASSES|HAIRCLEAR|CAPEXPOSE|CROWNSKIN|HAIRPUSH|TIE|EYEPROOF|PLACEMENT)").Line | % { $_.Substring(0, [Math]::Min(700, $_.Length)) }
(Get-Content "$LOG\face_probe.txt" | Select-String "PROBE|FACE").Line | % { $_.Substring(0, [Math]::Min(700, $_.Length)) }
(Get-Content "$LOG\hair_diag.txt" | Select-String "HAIRDIAG").Line | % { $_.Substring(0, [Math]::Min(900, $_.Length)) }
(Get-Content "$LOG\check_improved.txt" | Select-String "checks, ").Line
(Get-Content "$LOG\check_rigged.txt" | Select-String "checks, ").Line
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
