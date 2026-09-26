# Magmoo build, one command (hidden, headless). Reads source-copies/newunit-magmoo.blend + palettes/magmoo/ only.
# Writes improved/magmoo.{blend,json} + improved/magmoo_source_blob.blend, rigged/magmoo.{blend,json,glb},
# rigged/magmoo__obsidian.blend, improved/check_magmoo.json, rigged/check_magmoo.json, renders/magmoo/.
# Determinism: a SECOND full build runs in parallel into a temp outroot; its digests must equal run 1's (quoted at the end).
# -SkipBuild re-runs only the gates + renders.
param([switch]$SkipBuild)
$T0 = Get-Date
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$P\improve"
$OUT = "$P\renders\magmoo"
$DET = Join-Path ([System.IO.Path]::GetTempPath()) "magmoo_det"
New-Item -ItemType Directory -Force $OUT | Out-Null
if (-not $SkipBuild) {
  if (Test-Path $DET) { Remove-Item $DET -Recurse -Force }
  New-Item -ItemType Directory -Force $DET | Out-Null
  $bArgs = @("--background","`"$P\source-copies\newunit-magmoo.blend`"","--factory-startup","--python","`"$I\magmoo_build.py`"","--","--skins","default,obsidian")
  $b1 = Start-Process -FilePath $B -ArgumentList $bArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_magmoo_build.txt" -RedirectStandardError "$I\log_magmoo_build.err"
  $b2 = Start-Process -FilePath $B -ArgumentList ($bArgs + @("--outroot","`"$DET`"")) -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_magmoo_build_det.txt" -RedirectStandardError "$I\log_magmoo_build_det.err"
  $null = $b1.Handle; $null = $b2.Handle
  $b1.WaitForExit(); $b2.WaitForExit()
  "build exit=$($b1.ExitCode) determinism-rerun exit=$($b2.ExitCode) build_wall_s=$([math]::Round(((Get-Date) - $T0).TotalSeconds, 1))"
  if ($b1.ExitCode -ne 0) { "BUILD FAILED - see log_magmoo_build.*"; exit 1 }
}
$T1 = Get-Date
$jobs = @(
  @("check_improved",  @("--background","`"$P\improved\magmoo.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\improved\check_magmoo.json`"")),
  @("check_rigged",    @("--background","`"$P\rigged\magmoo.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$P\rigged\check_magmoo.json`"")),
  @("render_default",  @("--background","`"$P\rigged\magmoo.blend`"","--factory-startup","--python","`"$I\magmoo_render.py`"","--","`"$OUT\magmoo`"")),
  @("render_obsidian", @("--background","`"$P\rigged\magmoo__obsidian.blend`"","--factory-startup","--python","`"$I\magmoo_render.py`"","--","`"$OUT\magmoo_obsidian`"","front,threequarter,side,head,gap_tail")),
  @("render_source",   @("--background","`"$P\improved\magmoo_source_blob.blend`"","--factory-startup","--python","`"$I\magmoo_render.py`"","--","`"$OUT\source_blob`"","front,threequarter,side")),
  @("clips_mp4",       @("--background","`"$P\rigged\magmoo.blend`"","--factory-startup","--python","`"$I\magmoo_clips.py`"","--","`"$OUT`"","768","sheet"))
)
$procs = @()
foreach ($j in $jobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_magmoo_$($j[0]).txt" -RedirectStandardError "$I\log_magmoo_$($j[0]).err"
  $null = $pr.Handle; $procs += ,@($j[0], $pr)
}
foreach ($x in $procs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
$ba = Start-Process -FilePath $B -ArgumentList @("--background","--factory-startup","--python","`"$I\magmoo_before_after.py`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_magmoo_ba.txt" -RedirectStandardError "$I\log_magmoo_ba.err"
$null = $ba.Handle; $ba.WaitForExit(); "before_after exit=$($ba.ExitCode)"
foreach ($c in @("check_improved","check_rigged")) { (Get-Content "$I\log_magmoo_$c.txt" | Select-String "checks, ").Line | % { "$c : $_" } }
if (-not $SkipBuild) {
  $r1 = Get-Content "$P\rigged\magmoo.json" -Raw | ConvertFrom-Json
  $r2 = Get-Content "$DET\rigged\magmoo.json" -Raw | ConvertFrom-Json
  "DIGEST run1 full=$($r1.digest_full) geo=$($r1.digest_geometry_colour_uv) glb=$($r1.glb.sha256_16)"
  "DIGEST run2 full=$($r2.digest_full) geo=$($r2.digest_geometry_colour_uv) glb=$($r2.glb.sha256_16)"
  $same = ($r1.digest_full -eq $r2.digest_full) -and ($r1.digest_geometry_colour_uv -eq $r2.digest_geometry_colour_uv)
  "DETERMINISM identical=$same glb_identical=$($r1.glb.sha256_16 -eq $r2.glb.sha256_16)"
  Remove-Item $DET -Recurse -Force
}
"gates_renders_wall_s=$([math]::Round(((Get-Date) - $T1).TotalSeconds, 1)) total_wall_s=$([math]::Round(((Get-Date) - $T0).TotalSeconds, 1))"
"ALL DONE"
