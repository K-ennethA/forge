# Eldroot standing-rest rebuild + state clips, then (parallel, hidden, headless, read-only on the output):
# contract check, silhouette sheet, clip previews. Reads rigged/eldroot.blend (seated, untouched), writes
# rigged/eldroot_standing.{blend,json}, rigged/check_eldroot_standing.json, renders/eldroot-standing/, renders/animated/.
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$PRJ = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$I = "$PRJ\improve"
$p = Start-Process -FilePath $B -ArgumentList @("--background","`"$PRJ\rigged\eldroot.blend`"","--factory-startup","--python","`"$I\eldroot_stand.py`"","--","`"$PRJ\rigged\eldroot_standing.blend`"","`"$PRJ\rigged\eldroot_standing.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_stand_eldroot.txt" -RedirectStandardError "$I\log_stand_eldroot.err"
$null = $p.Handle; $p.WaitForExit(); "build exit=$($p.ExitCode)"
Get-Content "$I\log_stand_eldroot.txt" | Select-String "STAND_DONE" | % { "  " + $_.Line }
$jobs = @(
  @("check",  @("--background","`"$PRJ\rigged\eldroot_standing.blend`"","--factory-startup","--python","`"$I\conquest_contract_check.py`"","--","`"$PRJ\rigged\check_eldroot_standing.json`"")),
  @("sheet",  @("--background","`"$PRJ\rigged\eldroot_standing.blend`"","--factory-startup","--python","`"$I\render_standing_sheet.py`"","--","`"$PRJ\renders\eldroot-standing`"","`"$PRJ\rigged\eldroot.blend`"")),
  @("clips",  @("--background","`"$PRJ\rigged\eldroot_standing.blend`"","--factory-startup","--python","`"$I\render_state_clips.py`"","--","`"$PRJ\renders\animated`"","768"))
)
$procs = @()
foreach ($j in $jobs) {
  $pr = Start-Process -FilePath $B -ArgumentList $j[1] -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_stand_$($j[0]).txt" -RedirectStandardError "$I\log_stand_$($j[0]).err"
  $null = $pr.Handle; $procs += ,@($j[0], $pr)
}
foreach ($x in $procs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
(Get-Content "$I\log_stand_check.txt" | Select-String "checks, ").Line
Get-Content "$I\log_stand_sheet.txt", "$I\log_stand_clips.txt" | Select-String "WROTE" | % { "  " + $_.Line }
"ALL DONE"
