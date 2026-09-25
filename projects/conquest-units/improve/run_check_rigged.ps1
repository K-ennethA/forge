# Contract check on every RIGGED unit (parallel, hidden, headless, read-only): 7 base checks at rest + 4 rig checks.
param([string[]]$Units = @("barkling","petalfang","blightcap","mycothrall","eldroot"))
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$PRJ = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$C = "$PRJ\improve\conquest_contract_check.py"
$procs = @{}
foreach ($u in $Units) {
  $procs[$u] = Start-Process -FilePath $B -ArgumentList @("--background","`"$PRJ\rigged\$u.blend`"","--factory-startup","--python","`"$C`"","--","`"$PRJ\rigged\check_$u.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$PRJ\improve\log_check_rigged_$u.txt" -RedirectStandardError "$PRJ\improve\log_check_rigged_$u.err"
  $null = $procs[$u].Handle
}
foreach ($u in $Units) { $procs[$u].WaitForExit(); (Get-Content "$PRJ\improve\log_check_rigged_$u.txt" | Select-String "checks, ").Line | % { "$u : $_ (exit $($procs[$u].ExitCode))" } }
"ALL DONE"
