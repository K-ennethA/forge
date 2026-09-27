# Vampire Warrior v4.1 (from-scratch HERO on an MPFB2 base: model + idle + walk; v3 = the v2 cel treatment undone (no outline
# shells, no tone bands, the v1 material back), liner contour, fangs from under the lip, the drawn stylised pass (shadow
# shapes, face dials, brows, faint lips), armour overlays, long front hair strands; v4 = thicker brows, the face-framing
# curtain strands from the centre part, warm skin, fang roots proven hidden; v4.1 = ONE broad front piece per side, the hair hugging the scalp, the darker eye red), one command (hidden, headless).
# Writes improved/vampwarrior.*, improved/textures/vampwarrior_*, rigged/vampwarrior.{blend,json,glb},
# rigged/vampwarrior__dawn.blend, improved/check_vampwarrior.json, rigged/check_vampwarrior.json, renders/vampwarrior/
# (v4.1 files are vampwarrior_v41*; v1 / v2 / v3 / v4 stills stay as the 'before'), improve/log_vampwarrior_*.
#   -SkipBuild re-runs only the gates + renders. The build runs TWICE in parallel: the real build and a --digest-only twin
#   (saves nothing but its digest); every digest part must match, except the two bakes, which are held to the pinned
#   tolerance gate (vampwarrior_bake_diff.py) if they differ.
param([switch]$SkipBuild)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # never assign lowercase $p: PowerShell names are case-insensitive
$I = "$P\improve"
$OUT = "$P\renders\vampwarrior"
$VPY = "C:\Users\kenne\OneDrive\Desktop\git\forge\service\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $OUT | Out-Null
$T0 = Get-Date
if (-not $SkipBuild) {
  $bp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\vampwarrior_build.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_vampwarrior_build.txt" -RedirectStandardError "$I\log_vampwarrior_build.err"
  $dp = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\vampwarrior_build.py`"","--","--digest-only","`"$I\log_vampwarrior_digest2.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_vampwarrior_digest2.txt" -RedirectStandardError "$I\log_vampwarrior_digest2.err"
  $null = $bp.Handle; $null = $dp.Handle
  $bp.WaitForExit(); "build exit=$($bp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  $dp.WaitForExit(); "digest twin exit=$($dp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  $j1 = (Get-Content "$P\rigged\vampwarrior.json" -Raw | ConvertFrom-Json).digest
  $j2 = (Get-Content "$I\log_vampwarrior_digest2.json" -Raw | ConvertFrom-Json).digest
  $mismatch = @()
  foreach ($k in $j1.parts.PSObject.Properties.Name) { if ($j1.parts.$k -ne $j2.parts.$k) { $mismatch += $k } }
  $T = $env:TEMP
  $bd = & $VPY -P "$I\vampwarrior_bake_diff.py" "$T\vampwarrior_normal_main.npy" "$T\vampwarrior_normal_twin.npy" "$T\vampwarrior_ao_main.npy" "$T\vampwarrior_ao_twin.npy"
  $bdok = ($LASTEXITCODE -eq 0)
  $onlyBake = ($mismatch | Where-Object { $_ -notin @("bake_normal","bake_ao","ao_lifted") }).Count -eq 0   # ao_lifted = f(bake_ao)
  if ($mismatch.Count -eq 0) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=True | $($bd -join ' | ')"
  } elseif ($onlyBake) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=except-$($mismatch -join ',') | $($bd -join ' | ') gate=$(if ($bdok) { 'PASS' } else { 'FAIL' })"
  } else {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=FALSE mismatched=$($mismatch -join ',') gate=FAIL"
  }
  $line; $line | Out-File -Encoding utf8 "$I\log_vampwarrior_digest.txt"
}
$RB = "`"$P\rigged\vampwarrior.blend`""
$RD = "`"$P\rigged\vampwarrior__dawn.blend`""
$R = "`"$I\vampwarrior_render.py`""
$V4 = "`"$OUT\vampwarrior_v41`""
# debug tint for the authored regions (render-only, in memory): shadow shapes magenta / cyan, brows green, liner orange,
# mouth line blue
$DBG = "skin_shadow=255,40,220;bodice_shadow=40,220,255;brow=40,200,60;liner=255,160,0;mouth=0,90,255"
# the v4 mouth / brow close-ups use the SAME fixed focus boxes as the v3 'before' close-ups (vampwarrior_v4cmp_v3_*,
# rendered off the v3 rig before this build), so the pairs compare 1:1 (the body / head geometry is unchanged v3 -> v4)
$MOUTHF = "-0.024,-0.15,1.608,0.030,-0.14,1.648"
$BROWF = "-0.044,-0.12,1.676,0.050,-0.10,1.712"
$vJobs = @(
  @("check_improved", @("--background","`"$P\improved\vampwarrior.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_vampwarrior.json`"")),
  @("check_rigged",   @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_vampwarrior.json`"")),
  @("render_main",    @("--background",$RB,"--factory-startup","--python",$R,"--",$V4,"front,threequarter,side,back,back_threequarter,tactical,tactical_small","--pose","idle:1")),
  @("render_face",    @("--background",$RB,"--factory-startup","--python",$R,"--",$V4,"portrait,portrait_tq,portrait_low,face,face_side,eyes,head,head_front,head_back","--pose","idle:1")),
  @("render_close",   @("--background",$RB,"--factory-startup","--python",$R,"--",$V4,"torso,torso_front,sword,hem,boots,hand","--pose","idle:1")),
  @("render_mouth",   @("--background",$RB,"--factory-startup","--python",$R,"--",$V4,"mouth,mouth_low,mouth_tq_low,mouth_high","--pose","idle:1","--focus",$MOUTHF)),
  @("render_brows",   @("--background",$RB,"--factory-startup","--python",$R,"--",$V4,"brows","--pose","idle:1","--focus",$BROWF)),
  @("render_ortho",   @("--background",$RB,"--factory-startup","--python",$R,"--",$V4,"ortho_front,ortho_side","--pose","idle:1")),
  @("render_dawn",    @("--background",$RD,"--factory-startup","--python",$R,"--","`"$OUT\vampwarrior_v41_dawn`"","front,threequarter,portrait","--pose","idle:1")),
  @("render_shadow",  @("--background",$RB,"--factory-startup","--python",$R,"--","`"$OUT\vampwarrior_v41shadow`"","portrait,portrait_tq,portrait_low,eyes,torso_front","--pose","idle:1","--palette-override",$DBG)),
  @("clips_mp4",      @("--background",$RB,"--factory-startup","--python","`"$I\vampwarrior_clips.py`"","--","`"$OUT`"","768","--prefix","vampwarrior_v41"))
)
$vProcs = @()
foreach ($j in $vJobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_vampwarrior_$($j[0]).txt" -RedirectStandardError "$I\log_vampwarrior_$($j[0]).err"
  $null = $pr.Handle; $vProcs += ,@($j[0], $pr)
}
foreach ($x in $vProcs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
& $VPY -P "$I\vampwarrior_compose.py" 2>&1 | Out-File -Encoding utf8 "$I\log_vampwarrior_compose.txt"
"compose exit=$LASTEXITCODE"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_vampwarrior_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
