# Shadow Assassin v1 DRAFT (two-speeds law: one clean headless build + probes-as-sanity + ONE comparison strip; no gate wall,
# no clips, no benchmark). One command, hidden, headless:
#   0. the palette (improve/shadow_assassin_palette.py: every region PIXEL-SAMPLED from the saved sheet ->
#      palettes/shadow_assassin/default.json; deterministic)
#   1. the build (improve/shadow_assassin_build.py -> improved/shadow_assassin.{blend,json} + improved/textures/shadow_assassin_* +
#      rigged/shadow_assassin.{blend,json,glb}; the glb through the shared _GLOW path, audited in the build)
#   2. in parallel: the full-body stills, the close-ups (hood void, blade + hold, belt, chest, back sigil, boots), the three
#      ortho views (the comparison), the contract checker on the IMPROVED and the RIGGED file (rigged clip checks fail by
#      design: no clips)
#   3. the comparison strip <ver>_sheet_compare.png = BUILD | SHEET for front / side / back + the stills sheet
# Logs + checker outputs live in renders/shadow_assassin/. -SkipBuild re-runs 2 + 3 only.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # (never a lowercase $p: PowerShell names are case-insensitive)
$I = "$P\improve"
$OUT = "$P\renders\shadow_assassin"
$LOG = "$OUT\logs"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
$VER = "shadow_assassin_v1"
New-Item -ItemType Directory -Force $OUT, $LOG | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  & $VPY -P "$I\shadow_assassin_palette.py" 2>&1 | Out-File -Encoding utf8 "$LOG\palette.txt"
  "palette exit=$LASTEXITCODE"
  if (Test-Path "$P\rigged\shadow_assassin.blend") { Remove-Item "$P\rigged\shadow_assassin.blend" }
  $args_ = @("--background","--factory-startup","--python","`"$I\shadow_assassin_build.py`"","--","--glb")
  $bp = Start-Process -FilePath $B -ArgumentList $args_ -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\build.txt" -RedirectStandardError "$LOG\build.err"
  $null = $bp.Handle
  $bp.WaitForExit()
  "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  if (-not (Test-Path "$P\rigged\shadow_assassin.blend")) { "BUILD FAILED -- see $LOG\build.txt / build.err"; Get-Content "$LOG\build.err" -Tail 15; exit 1 }
}
$RB = "`"$P\rigged\shadow_assassin.blend`""
$R = "`"$I\shadow_assassin_render.py`""
$W = "`"$OUT\$VER`""
$vJobs = @(
  @("render_full",  @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,threequarter,side,back","--res","1024")),
  @("render_close", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"head_front,head_tq,head_side,blade,blade_tq,hold,hold_tq,hold_side,belt,chest,back_sigil,boots","--res","800")),
  @("render_ortho", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"ortho_front,ortho_side,ortho_back","--res","1400")),
  @("check_improved", @("--background","`"$P\improved\shadow_assassin.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_improved.json`"")),
  @("check_rigged", @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_rigged.json`""))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\$($j[0]).txt" -RedirectStandardError "$LOG\$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$env:SA_V = $VER
& $VPY -P "$I\shadow_assassin_compose.py" 2>&1 | Out-File -Encoding utf8 "$LOG\compose.txt"
"compose exit=$LASTEXITCODE"
(Get-Content "$LOG\compose.txt" | Select-String "^(SHEET|STRIP)").Line
(Get-Content "$LOG\build.txt" | Select-String "^(TRIS|GLOWGATE|HOLD|GRIP|GLB|HIDDEN)").Line | % { $_.Substring(0, [Math]::Min(600, $_.Length)) }
(Get-Content "$LOG\check_improved.txt" | Select-String "checks, ").Line
(Get-Content "$LOG\check_rigged.txt" | Select-String "checks, ").Line
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
