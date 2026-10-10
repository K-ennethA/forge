# Size lineup, one command (hidden, headless). Every rigged/<unit>.glb (no __skin variants) in one row on one floor.
# Reads rigged/*.glb and the Conquest tree (READ-ONLY: <unit>_forge.glb.import root_scale + roster .tres model_scale).
# Writes renders/lineup/lineup_{ingame,ingame_roster,target,target_roster,ingame_tq}.png + lineup_table.json,
# merges design/size-table.json (the artist edits target_height_m there; edits are kept), logs improve/log_lineup_*.
#   -Conquest <path>  use this Conquest working tree instead of the auto-found one
#   -Only ingame,target,tq  render a subset
param([string]$Conquest = "", [string]$Only = "ingame,target,tq")
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"   # never assign lowercase $p: PowerShell names are case-insensitive
$I = "$P\improve"
New-Item -ItemType Directory -Force "$P\renders\lineup" | Out-Null
$T0 = Get-Date
$a = @("--background","--factory-startup","--python-exit-code","1","--python","`"$I\lineup_render.py`"","--","--only",$Only)
if ($Conquest -ne "") { $a += @("--conquest","`"$Conquest`"") }
$pr = Start-Process -FilePath $B -ArgumentList $a -WindowStyle Hidden -PassThru -RedirectStandardOutput "$I\log_lineup_render.txt" -RedirectStandardError "$I\log_lineup_render.err"
$null = $pr.Handle
$pr.WaitForExit()
"render exit=$($pr.ExitCode)"
(Get-Content "$I\log_lineup_render.txt" | Select-String "^\[lineup") | ForEach-Object { $_.Line }
"ALL DONE total_wall_s=$([math]::Round(((Get-Date)-$T0).TotalSeconds,1))"
