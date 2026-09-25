# Walk + idle preview mp4s (+ contact sheets) for every rigged unit (parallel, hidden, headless, read-only on the blends).
param([string[]]$Units = @("barkling","petalfang","blightcap","mycothrall","eldroot"))
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$PRJ = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$R = "$PRJ\improve\render_animated.py"
$procs = @{}
foreach ($u in $Units) {
  $procs[$u] = Start-Process -FilePath $B -ArgumentList @("--background","`"$PRJ\rigged\$u.blend`"","--factory-startup","--python","`"$R`"","--","`"$PRJ\renders\animated`"","768","sheet") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$PRJ\improve\log_anim_$u.txt" -RedirectStandardError "$PRJ\improve\log_anim_$u.err"
  $null = $procs[$u].Handle
}
foreach ($u in $Units) { $procs[$u].WaitForExit(); "$u exit=$($procs[$u].ExitCode)"; Get-Content "$PRJ\improve\log_anim_$u.txt" | Select-String "WROTE" | % { "  " + $_.Line } }
"ALL DONE"
