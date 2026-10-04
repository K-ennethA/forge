# Professor Elias v1 DRAFT (two-speeds law: one clean headless build + probes-as-sanity + ONE comparison sheet; no gate
# wall, no clips). One command, hidden, headless:
#   1. the build (improve/elias_build.py -> improved/elias.{blend,json} + improved/textures/elias_* + rigged/elias.{blend,json,glb};
#      v4: the glb always, through the shared _GLOW path: export_glb.add_glow_attr + EXPORT_KW, audited in the build)
#      v5 -Twin: a second identical build into a scratch root (%TEMP%\elias_twin, never the project) in parallel; glb +
#      textures + the build's combined digest compared byte-for-byte (the v4 determinism precedent)
#   2. in parallel: the full-body stills, the face / props close-ups, the face probe (improve/wren_face_probe.py, read-only
#      reuse, --report improved/elias.json), the contract checker on the IMPROVED and (v4) the RIGGED file (the rigged run
#      needs the shared checker's no-clip guard; its clip checks fail by design: no clips this pass)
#   + v2: the hair diagnosis (improve/wren_hair_diag.py, read-only reuse) on the rigged file
#   + v4: the one-tone hair still (every hair region painted the one hair tone in memory: shading only)
#   + v5: the sheet's four head views (headc_front / headc_tq = the head panel's 3/4, his right / headc_side = his left /
#     headc_back; staff hidden) + the one-tone head 3/4
#   + v6: the head + beard views (hb_front / hb_tql = 3/4 his left / hb_side / hb_low = chin-up / hb_tq = the head panel)
#   3. the comparison sheet + the v5 | v6 | SHEET strip (improve/elias_compose.py -> renders/elias/<ver>_sheet.png,
#      _hair_compare.png; the strip = front, 3/4, side, back + the one-tone column)
#   v7 (staff hold baked into the bind pose + the belt / mantle / weight-source fix): render economy -- the full body, the
#   props / belt close-ups and the three HOLD views only (head + hair untouched: SCALPDIGEST / BEARDDIGEST prove it); no
#   face probe / hair diag / head views; the strip = v6 | v7 | SHEET for front (the artist's screenshot), 3/4, the hold
#   3/4 + side (the v6 hold_* stills were rendered from the v6 rig on its right-hand box before the rebuild)
#   v7.1 (HAND_DECIMATE): same jobs; the strip = v7 | v7.1 for the three hold views + the open left hand (book)
# Logs + probe outputs live in renders/elias/ (the lane allowlist). -SkipBuild re-runs 2 + 3 only.
param([switch]$SkipBuild, [switch]$Twin)
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
  if ($Twin) {
    $TW = "$env:TEMP\elias_twin"
    Remove-Item -Recurse -Force $TW -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $TW | Out-Null
    $targs = @("--background","--factory-startup","--python","`"$I\elias_build.py`"","--","--glb","--scratch","`"$TW`"")
    $tp = Start-Process -FilePath $B -ArgumentList $targs -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\twin.txt" -RedirectStandardError "$LOG\twin.err"
    $null = $tp.Handle
  }
  $bp.WaitForExit()
  "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  if (-not (Test-Path "$P\rigged\elias.blend")) { "BUILD FAILED -- see $LOG\build.txt / build.err"; Get-Content "$LOG\build.err" -Tail 15; exit 1 }
  if ($Twin) {
    $tp.WaitForExit()
    "twin exit=$($tp.ExitCode)"
    $same = $true
    foreach ($rel in @("rigged\elias.glb", "improved\textures\elias_normal.png", "improved\textures\elias_ao.png")) {
      $h1 = (Get-FileHash "$P\$rel" -Algorithm SHA256).Hash
      $h2 = if (Test-Path "$TW\$rel") { (Get-FileHash "$TW\$rel" -Algorithm SHA256).Hash } else { "missing" }
      "TWIN $rel main=$($h1.Substring(0,16)) twin=$($h2.Substring(0, [Math]::Min(16, $h2.Length))) equal=$($h1 -eq $h2)"
      $same = $same -and ($h1 -eq $h2)
    }
    $m1 = (Get-Content "$LOG\build.txt" | Select-String '"combined": "([0-9a-f]+)"' | Select-Object -Last 1)
    $m2 = (Get-Content "$LOG\twin.txt" | Select-String '"combined": "([0-9a-f]+)"' | Select-Object -Last 1)
    $d1 = if ($m1) { $m1.Matches[0].Groups[1].Value } else { "" }
    $d2 = if ($m2) { $m2.Matches[0].Groups[1].Value } else { "" }
    "TWIN digest main=$d1 twin=$d2 equal=$(($d1 -eq $d2) -and ($d1 -ne ''))"
    "TWIN_ALL_EQUAL=$($same -and ($d1 -eq $d2) -and ($d1 -ne ''))"
  }
}
$RB = "`"$P\rigged\elias.blend`""
$R = "`"$I\elias_render.py`""
$VER = "elias_v7.1"                   # (render / probe prefix; v7.1 = the hand-density pass; v7 stills = the baseline)
$W = "`"$OUT\$VER`""
$G1 = "154,131,113"                   # v5: the one-tone = the SAMPLED hair (palette "hair"; v4's used the v1 grey 186,186,182)
$ONE = (@("hair","hair_shade","hair_root","hair_ring","hair_tip","hair_inner","hair_crevice","hair_beard","hair_beard_shade",
          "hair_beard_root","hair_beard_tip","hair_beard_crevice") | ForEach-Object { "$_=$G1" }) -join ";"
$vJobs = @(
  @("render_full",  @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,side,back,threequarter","--res","1024")),
  @("render_close", @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"hold,hold_tq,hold_side,staff_head,book,satchel,belt,brooch","--res","800")),
  @("check_improved", @("--background","`"$P\improved\elias.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_improved.json`"")),
  @("check_rigged", @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$OUT\$($VER)_check_rigged.json`""))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LOG\$($j[0]).txt" -RedirectStandardError "$LOG\$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$env:ELIAS_V = $VER; $env:ELIAS_BASE = "elias_v7"; $env:ELIAS_STRIP_REF = "1"; $env:ELIAS_STRIP_OUT = "hand_compare"
$env:ELIAS_STRIP_VIEWS = "hold:gripping hand,hold_tq:hold 3/4 (his right),hold_side:hold (his right side),book:open left hand + tome"
& $VPY -P "$I\elias_compose.py" 2>&1 | Out-File -Encoding utf8 "$LOG\compose.txt"
"compose exit=$LASTEXITCODE"
(Get-Content "$LOG\compose.txt" | Select-String "^(SHEET|STRIP)").Line
(Get-Content "$LOG\build.txt" | Select-String "^(TRIS|GRIP|HOLD|HANDS|GLB|BEARD6|BEARDMASSES|BEARDLAYER|BEARDCOVER|HAIRCN|SCALPDIGEST|BEARDDIGEST|MASSES|HAIRCLEAR|CAPEXPOSE|HAIRPUSH)").Line | % { $_.Substring(0, [Math]::Min(400, $_.Length)) }
(Get-Content "$LOG\check_improved.txt" | Select-String "checks, ").Line
(Get-Content "$LOG\check_rigged.txt" | Select-String "checks, ").Line
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
