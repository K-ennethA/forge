# Runs improve_unit.py for every unit (parallel, hidden, headless). Reads source-copies/ only.
param([string[]]$Units = @("barkling","petalfang","blightcap","mycothrall","eldroot"))
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$S = "$P\improve\improve_unit.py"
$src = @{ barkling="tree_grunt"; petalfang="flower_grunt"; blightcap="shroom_grunt"; mycothrall="parasite_grunt"; eldroot="ancient_tree" }
$procs = @{}
foreach ($u in $Units) {
  $log = "$P\improve\log_improve_$u"
  $procs[$u] = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\source-copies\$($src[$u]).blend`"","--factory-startup","--python","`"$S`"","--",$u,"`"$P\improved\$u.blend`"","`"$P\improved\$u.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$log.txt" -RedirectStandardError "$log.err"
  $null = $procs[$u].Handle   # cache the handle so ExitCode is readable after exit
}
foreach ($u in $Units) { $procs[$u].WaitForExit(); "$u exit=$($procs[$u].ExitCode)" }
"ALL DONE"
