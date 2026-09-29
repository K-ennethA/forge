# Wren (the Oakvale farm-boy HERO, from the artist's sheet design/reference/wren-character-sheet.webp; the vampwarrior
# lessons applied from the start): model + idle + walk + the winter skin, one command (hidden, headless).
# Writes improved/wren.{blend,json}, improved/textures/wren_*, rigged/wren.{blend,json,glb}, rigged/wren__winter.blend,
# improved/check_wren.json, rigged/check_wren.json, renders/wren/*, improve/log_wren_*.
#   -SkipBuild re-runs only the gates + renders. The build runs TWICE in parallel: the real build and a --digest-only twin
#   (saves nothing but its digest); every digest part must match, except the two bakes (+ ao_lifted = f(bake_ao)), which
#   are held to the pinned tolerance gate (wren_bake_diff.py) if they differ.
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
  $null = $bp.Handle; $null = $dp.Handle
  $bp.WaitForExit(); $bwall = [math]::Round(((Get-Date)-$T0).TotalSeconds,1); "build exit=$($bp.ExitCode) wall_s=$bwall"
  $dp.WaitForExit(); "digest twin exit=$($dp.ExitCode) wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
  $j1 = (Get-Content "$P\rigged\wren.json" -Raw | ConvertFrom-Json).digest
  $j2 = (Get-Content "$I\log_wren_digest2.json" -Raw | ConvertFrom-Json).digest
  $mismatch = @()
  foreach ($k in $j1.parts.PSObject.Properties.Name) { if ($j1.parts.$k -ne $j2.parts.$k) { $mismatch += $k } }
  $T = $env:TEMP
  $bd = & $VPY -P "$I\wren_bake_diff.py" "$T\wren_normal_main.npy" "$T\wren_normal_twin.npy" "$T\wren_ao_main.npy" "$T\wren_ao_twin.npy"
  $bdok = ($LASTEXITCODE -eq 0)
  $onlyBake = ($mismatch | Where-Object { $_ -notin @("bake_normal","bake_ao","ao_lifted") }).Count -eq 0
  if ($mismatch.Count -eq 0) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=True | $($bd -join ' | ')"
  } elseif ($onlyBake) {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=except-$($mismatch -join ',') | $($bd -join ' | ') gate=$(if ($bdok) { 'PASS' } else { 'FAIL' })"
  } else {
    $line = "DIGEST build=$($j1.combined) twin=$($j2.combined) identical=FALSE mismatched=$($mismatch -join ',') gate=FAIL"
  }
  $line; "$line | build_wall_s=$bwall" | Out-File -Encoding utf8 "$I\log_wren_digest.txt"
}
$RB = "`"$P\rigged\wren.blend`""
$RW = "`"$P\rigged\wren__winter.blend`""
$R = "`"$I\wren_render.py`""
$W = "`"$OUT\wren`""
$vJobs = @(
  @("check_improved", @("--background","`"$P\improved\wren.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_wren.json`"")),
  @("check_rigged",   @("--background",$RB,"--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_wren.json`"")),
  @("render_main",    @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"front,threequarter,side,back,back_threequarter,tactical,tactical_small","--pose","idle:1")),
  @("render_face",    @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"portrait,face,eyes,brows,head_front,head_side,head_tq_back,head_back","--pose","idle:1")),
  @("render_close",   @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"cloak,cloak_tq,patches,necklace,bracer,bracer_front,fork_full,boots,boots_front,torso","--pose","idle:1")),
  @("render_fork",    @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"fork_head")),   # rest pose: the fork upright, tines face-on
  @("render_ortho",   @("--background",$RB,"--factory-startup","--python",$R,"--",$W,"ortho_front,ortho_side,ortho_back","--pose","idle:1")),
  @("render_winter",  @("--background",$RW,"--factory-startup","--python",$R,"--","`"$OUT\wren_winter`"","front,threequarter,back,portrait","--pose","idle:1")),
  @("clips_mp4",      @("--background",$RB,"--factory-startup","--python","`"$I\wren_clips.py`"","--","`"$OUT`"","768","--prefix","wren"))
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
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
