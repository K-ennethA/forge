# Rig + idle/walk clips for every improved unit (parallel, hidden, headless). Reads improved/, writes rigged/.
param([string[]]$Units = @("barkling","petalfang","blightcap","mycothrall","eldroot"))
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$PRJ = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$S = "$PRJ\improve\rig_unit.py"
New-Item -ItemType Directory -Force "$PRJ\rigged" | Out-Null
$procs = @{}
foreach ($u in $Units) {
  $log = "$PRJ\improve\log_rig_$u"
  $procs[$u] = Start-Process -FilePath $B -ArgumentList @("--background","`"$PRJ\improved\$u.blend`"","--factory-startup","--python","`"$S`"","--",$u,"`"$PRJ\rigged\$u.blend`"","`"$PRJ\rigged\$u.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$log.txt" -RedirectStandardError "$log.err"
  $null = $procs[$u].Handle
}
foreach ($u in $Units) { $procs[$u].WaitForExit(); "$u exit=$($procs[$u].ExitCode)" }
"ALL DONE"
