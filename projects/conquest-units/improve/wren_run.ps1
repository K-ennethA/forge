# Wren (the Oakvale farm-boy HERO, from the artist's sheet design/reference/wren-character-sheet.webp; the vampwarrior
# lessons applied from the start): model + idle + walk + the winter skin, one command (hidden, headless).
# v3 (the Fire Emblem round): renders are written v3-prefixed (renders/wren/wren_v3_*) so the v2 stills stay as the
# comparison baseline (v2: wren_v2_*; v1: wren_* + wren_v1ref_*). + wren_v3_hairflat_* = the same views with the hair
# strip's normals set flat in memory (the v2 hair shading on the v3 geometry: the one-volume A/B proof).
# v4 (review-log 2026-09-29 "Wren v4 feedback"): renders are written v4-prefixed (wren_v4_*); the v3 stills are the
# comparison baseline (the v4-only views nose / nose_tq / face_side(90) / mouth_tq / mouth_side / hair_part / hair_sweep /
# hair_back_close were rendered once off the committed v3 rig as wren_v3_* before the v4 build replaced it).
# v5 (review-log 2026-09-29 "Wren v5 feedback + FE reference set"): renders are written v5-prefixed (wren_v5_*); the v4
# stills are the comparison baseline (the v5-only views undereye / undereye_tq were rendered once off the committed v4 rig
# as wren_v4_*, and the face probe was run once on the committed v4 mesh -> wren_v4_face_probe.{json,npz}, before the v5
# build replaced them). + the face probe on the delivered v5 mesh (improve/wren_face_probe.py -> wren_v5_face_probe.*).
# Writes improved/wren.{blend,json}, improved/textures/wren_*, rigged/wren.{blend,json,glb}, rigged/wren__winter.blend,
# improved/check_wren.json, rigged/check_wren.json, renders/wren/*, improve/log_wren_*.
#   -SkipBuild re-runs only the gates + renders. The build runs TWICE in parallel: the real build and a --digest-only twin
#   (saves nothing but its digest); every digest part must match, except the two bakes (+ ao_lifted = f(bake_ao)), which
#   are held to the pinned tolerance gate (wren_bake_diff.py) if they differ. v3: + the hair proxy normal bake
#   (bake_hair_normal), same gate.
#   v5.1 (the hair-face decoupling): + a third process, the hair-stability probe (wren_build.py --hair-digest-only under a
#   face edit); HAIRSTABLE = the build's, the twin's and the face-edited exact hair digests, all three must match.
# v6 (review-log 2026-09-29 "Wren v6 mouth feedback" + "addendum"): renders are written v6-prefixed (wren_v6_*); the v5
# stills are the comparison baseline (the v5.1 mouth probe wren_v5_mprobe.{json,png} was run once off the committed v5.1 rig
# before the v6 build replaced it). + the MOUTH PROBE on the delivered v6 rig (improve/wren_mouth_probe.py ->
# wren_v6_mprobe.*: orthographic mouth renders with the line repainted skin -- the second-feature proof -- and the
# placement ratios vs the FE portrait).
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # never assign lowercase $p: PowerShell names are case-insensitive
$I = "$P\improve"
$OUT = "$P\renders\wren"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  $bp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\wren_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_wren_build.txt" -RedirectStandardError "$I\log_wren_build.err"
  $dp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\wren_build.py`"","--","--digest-only","`"$I\log_wren_digest2.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_wren_digest2.txt" -RedirectStandardError "$I\log_wren_digest2.err"
  # v5.1 HAIR-STABILITY PROBE (the hair-face decoupling): sections 1-5 again under a deliberate FACE edit (the v4 mouth
  # profile = a geometry edit + the v1 brows = a paint-cut edit next to the hairline); its exact hair digest must equal the
  # build's (face edits never move approved hair).
  $hp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\wren_build.py`"","--","--hair-digest-only","`"$I\log_wren_hairprobe.json`"","--set","`"LIP_PROFILE='line'`"","--set","`"BROW_W=(0.0064,0.0028)`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_wren_hairprobe.txt" -RedirectStandardError "$I\log_wren_hairprobe.err"
  $null = $bp.Handle; $null = $dp.Handle; $null = $hp.Handle
  $bp.WaitForExit(); $bwall = [math]::Round(((Get-Date)-$T0).TotalSeconds,1); "build exit=$($bp.ExitCode) wall_s=$bwall"
  $dp.WaitForExit(); "digest twin exit=$($dp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  $hp.WaitForExit(); "hair probe exit=$($hp.ExitCode)"
  $j1 = (Get-Content "$P\rigged\wren.json" -Raw | ConvertFrom-Json).digest
  $j2 = (Get-Content "$I\log_wren_digest2.json" -Raw | ConvertFrom-Json).digest
  $mismatch = @()
  foreach ($k in $j1.parts.PSObject.Properties.Name) { if ($j1.parts.$k -ne $j2.parts.$k) { $mismatch += $k } }
  $T = $env:TEMP
  $bd = & $VPY -P "$I\wren_bake_diff.py" "$T\wren_normal_main.npy" "$T\wren_normal_twin.npy" "$T\wren_ao_main.npy" "$T\wren_ao_twin.npy" "$T\wren_hairnormal_main.npy" "$T\wren_hairnormal_twin.npy"
  $bdok = ($LASTEXITCODE -eq 0)
  $onlyBake = ($mismatch | Where-Object { $_ -notin @("bake_normal","bake_ao","ao_lifted","bake_hair_normal") }).Count -eq 0
  if ($mismatch.Count -eq 0) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=True | $($bd -join ' | ')"
  } elseif ($onlyBake) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=except-$($mismatch -join ',') | $($bd -join ' | ') gate=$(if ($bdok) { 'PASS' } else { 'FAIL' })"
  } else {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=FALSE mismatched=$($mismatch -join ',') gate=FAIL"
  }
  $h3 = (Get-Content "$I\log_wren_hairprobe.json" -Raw | ConvertFrom-Json).hair_geometry
  $hline = "HAIRSTABLE build=$($j1.parts.hair_geometry) twin=$($j2.parts.hair_geometry) face_edited=$h3 identical=$(($j1.parts.hair_geometry -eq $j2.parts.hair_geometry) -and ($j1.parts.hair_geometry -eq $h3))"
  $line; $hline; "$line | build_wall_s=$bwall`r`n$hline" | Out-File -Encoding utf8 "$I\log_wren_digest.txt"
}
$RB = "`"$P\rigged\wren.blend`""
$RW = "`"$P\rigged\wren__winter.blend`""
$R = "`"$I\wren_render.py`""
$W = "`"$OUT\wren_v6`""
$vJobs = @(
  @("check_improved", @("--background","`"$P\improved\wren.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_wren.json`"")),
  @("check_rigged",   @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_wren.json`"")),
  @("render_main",    @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,threequarter,side,back,back_threequarter,tactical,tactical_small","--pose","idle:1")),
  @("render_face",    @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"portrait,face,eyes,eyes_v2frame,eye_close,brows,mouth,mouth_tq,mouth_side,nose,nose_tq,face_side,face_side90,undereye,undereye_tq","--pose","idle:1")),
  @("render_hair",    @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"head_front,head_side,head_tq_back,head_back,hair_close,head_top","--pose","idle:1")),
  @("render_hair2",   @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"hair_part,hair_sweep,hair_back_close","--pose","idle:1","--hide-fork")),
  @("render_close",   @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"cloak,cloak_tq,patches,necklace,bracer,bracer_front,fork_full,boots,boots_front,torso","--pose","idle:1")),
  @("render_fork",    @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"fork_head")),   # rest pose: the fork upright, tines face-on
  @("render_ortho",   @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"ortho_front,ortho_side,ortho_back","--pose","idle:1")),
  @("render_winter",  @("--background",$RW,"--factory-startup","--python",$R,"--","`"$OUT\wren_v6_winter`"","front,threequarter,back,portrait,hair_close,head_tq_back","--pose","idle:1")),
  @("face_probe",     @("--background","`"$P\improved\wren.blend`"","--factory-startup","--python","`"$I\wren_face_probe.py`"","--","`"$OUT\wren_v6_face_probe`"")),
  @("mouth_probe",    @("--background",$RB,"--factory-startup","--python","`"$I\wren_mouth_probe.py`"","--","`"$OUT\wren_v6_mprobe`"")),
  @("clips_mp4",      @("--background",$RB,"--factory-startup","--python","`"$I\wren_clips.py`"","--","`"$OUT`"","768","--prefix","wren_v6"))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_wren_$($j[0]).txt" -RedirectStandardError "$I\log_wren_$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
& $VPY -P "$I\wren_compose.py" 2>&1 | Out-File -Encoding utf8 "$I\log_wren_compose.txt"
"compose exit=$LASTEXITCODE"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_wren_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
(Get-Content "$I\log_wren_face_probe.txt" | Select-String "^PROBE").Line
(Get-Content "$I\log_wren_mouth_probe.txt" | Select-String "^MPROBE ").Line
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
