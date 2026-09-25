# Eldroot standing v2 (full leg extension) build, then (parallel, hidden, headless, read-only on the output):
# contract check, seated-vs-standing sheet, clip previews. Reads rigged/eldroot.blend (seated, untouched) and
# leaves rigged/eldroot_standing.blend (v1, rejected) untouched. Writes rigged/eldroot_standing2.{blend,json},
# rigged/check_eldroot_standing2.json, renders/eldroot-standing2/, renders/animated/eldroot2_*.
param([switch]$RenderOnly)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$PRJ = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$PRJ\improve"
$OUTB = "$PRJ\rigged\eldroot_standing2.blend"
if (-not $RenderOnly) {
  $t = Get-Date
  $p = Start-Process -FilePath $B -ArgumentList @("--background","`"$PRJ\rigged\eldroot.blend`"","--factory-startup","--python","`"$I\eldroot_stand2.py`"","--","`"$OUTB`"","`"$PRJ\rigged\eldroot_standing2.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_stand2_build.txt" -RedirectStandardError "$I\log_stand2_build.err"
  $null = $p.Handle; $p.WaitForExit(); "build exit=$($p.ExitCode) sec=$([int]((Get-Date)-$t).TotalSeconds)"
  Get-Content "$I\log_stand2_build.txt" | Select-String "STAND2_DONE" | % { "  " + $_.Line }
  if ($p.ExitCode -ne 0) { Get-Content "$I\log_stand2_build.err" -Tail 20; exit 1 }
}
$t = Get-Date
$jobs = @(
  @("check",  @("--background","`"$OUTB`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$PRJ\rigged\check_eldroot_standing2.json`"")),
  @("sheet",  @("--background","`"$OUTB`"","--factory-startup","--python","`"$I\eldroot2_render_sheet.py`"","--","`"$PRJ\renders\eldroot-standing2`"","`"$PRJ\rigged\eldroot.blend`"")),
  @("clips",  @("--background","`"$OUTB`"","--factory-startup","--python","`"$I\eldroot2_render_clips.py`"","--","`"$PRJ\renders\animated`"","768"))
)
$procs = @()
foreach ($j in $jobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_stand2_$($j[0]).txt" -RedirectStandardError "$I\log_stand2_$($j[0]).err"
  $null = $pr.Handle; $procs += ,@($j[0], $pr)
}
foreach ($x in $procs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
"renders sec=$([int]((Get-Date)-$t).TotalSeconds)"
(Get-Content "$I\log_stand2_check.txt" | Select-String "checks, ").Line
Get-Content "$I\log_stand2_sheet.txt", "$I\log_stand2_clips.txt" | Select-String "WROTE" | % { "  " + $_.Line }
"ALL DONE"
