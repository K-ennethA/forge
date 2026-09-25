# Contract check + preview renders for every improved unit (parallel, hidden, headless, read-only on the blends).
param([string[]]$Units = @("barkling","petalfang","blightcap","mycothrall","eldroot"), [switch]$NoRender)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$C = "$P\improve\conquest_contract_check.py"
$R = "$P\improve\render_improved.py"
$procs = @()
foreach ($u in $Units) {
  $pc = Start-Process -FilePath $B -ArgumentList @("--background","`"$P\improved\$u.blend`"","--factory-startup","--python","`"$C`"","--","`"$P\improved\check_$u.json`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$P\improve\log_check_$u.txt" -RedirectStandardError "$P\improve\log_check_$u.err"
  $null = $pc.Handle; $procs += ,@("check $u", $pc)
  if (-not $NoRender) {
    $extra = @(); if ($u -eq "eldroot") { $extra = @("closeup") }
    $pr = Start-Process -FilePath $B -ArgumentList (@("--background","`"$P\improved\$u.blend`"","--factory-startup","--python","`"$R`"","--","`"$P\renders\improved\$u`"") + $extra) -WindowStyle Hidden -PassThru -RedirectStandardOutput "$P\improve\log_render_$u.txt" -RedirectStandardError "$P\improve\log_render_$u.err"
    $null = $pr.Handle; $procs += ,@("render $u", $pr)
  }
}
foreach ($x in $procs) { $x[1].WaitForExit(); "$($x[0]) exit=$($x[1].ExitCode)" }
foreach ($u in $Units) { (Get-Content "$P\improve\log_check_$u.txt" | Select-String "checks, ").Line | % { "$u : $_" } }
"ALL DONE"
