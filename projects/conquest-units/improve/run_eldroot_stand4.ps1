# Eldroot standing v4 (tasset plates + proportion rebalance on v3) build, then (parallel, hidden, headless,
# read-only on the output): contract check, v3-vs-v4 sheet + closeups + annotation overlay, stand_up / sit_down / walk previews.
# Reads rigged/eldroot.blend (seated, untouched) and leaves v1/v2/v3 outputs untouched. Writes
# rigged/eldroot_standing4.{blend,json}, rigged/check_eldroot_standing4.json, renders/eldroot-standing4/,
# renders/animated/eldroot4_*.
param([switch]$RenderOnly, [switch]$BuildOnly, [switch]$NoClips)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$PRJ = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$PRJ\improve"
$OUTB = "$PRJ\rigged\eldroot_standing4.blend"
if (-not $RenderOnly) {
  $t = Get-Date
  $p = Start-Process -FilePath $B -ArgumentList @("--background","`"$PRJ\rigged\eldroot.blend`"","--factory-startup","--python","`"$I\eldroot_stand4.py`"","--","`"$OUTB`"","`"$PRJ\rigged\eldroot_standing4.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_stand4_build.txt" -RedirectStandardError "$I\log_stand4_build.err"
  $null = $p.Handle; $p.WaitForExit(); "build exit=$($p.ExitCode) sec=$([int]((Get-Date)-$t).TotalSeconds)"
  $done = Get-Content "$I\log_stand4_build.txt" | Select-String "STAND4_DONE"
  $done | % { "  " + $_.Line }
  if ($p.ExitCode -ne 0 -or -not $done) { "BUILD FAILED"; Get-Content "$I\log_stand4_build.err" -Tail 20; exit 1 }
  if ($BuildOnly) { exit 0 }
}
$t = Get-Date
$jobs = @(
  @("check",  @("--background","`"$OUTB`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$PRJ\rigged\check_eldroot_standing4.json`"")),
  @("sheet",  @("--background","`"$OUTB`"","--factory-startup","--python","`"$I\eldroot4_render_sheet.py`"","--","`"$PRJ\renders\eldroot-standing4`"","`"$PRJ\rigged\eldroot_standing3.blend`"","`"$PRJ\rigged\eldroot_standing4.json`""))
)
if (-not $NoClips) {
  $jobs += ,@("clips",  @("--background","`"$OUTB`"","--factory-startup","--python","`"$I\eldroot4_render_clips.py`"","--","`"$PRJ\renders\animated`"","768"))
}
$procs = @()
foreach ($j in $jobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_stand4_$($j[0]).txt" -RedirectStandardError "$I\log_stand4_$($j[0]).err"
  $null = $pr.Handle; $procs += ,@($j[0], $pr)
}
foreach ($x in $procs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
"renders sec=$([int]((Get-Date)-$t).TotalSeconds)"
(Get-Content "$I\log_stand4_check.txt" | Select-String "checks, ").Line
Get-Content "$I\log_stand4_sheet.txt" | Select-String "WROTE|HEIGHTS|MIDFOLD" | % { "  " + $_.Line }
if (-not $NoClips) { Get-Content "$I\log_stand4_clips.txt" | Select-String "WROTE" | % { "  " + $_.Line } }
"ALL DONE"
