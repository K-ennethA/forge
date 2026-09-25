# Runs survey.py over every source copy and every shipped Conquest glb. Hidden, headless.
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$G = "C:\Users\kenne\OneDrive\Desktop\git\Conquest\game\characters\models\forest"
$S = "$P\survey\survey.py"
$R = "$P\renders\survey"
function Run($argList, $tag) {
  $log = "$P\survey\log_$tag.txt"
  $proc = Start-Process -FilePath $B -ArgumentList $argList -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  "$tag exit=$($proc.ExitCode)"   # not $p: PowerShell names are case-insensitive and $P is the project root
}
foreach ($n in "ancient_tree","flower_grunt","shroom_grunt","tree_grunt") {
  Run @("--background","`"$P\source-copies\$n.blend`"","--factory-startup","--python","`"$S`"","--","blend","`"$R\blend_$n`"","`"$P\survey\blend_$n.json`"") "blend_$n"
}
$glbs = @{ "tree_grunt"=0; "petalfang"=180; "blightcap"=0; "eldroot"=0; "mycothrall"=0 }
foreach ($k in $glbs.Keys) {
  Run @("--background","--factory-startup","--python","`"$S`"","--","glb","`"$G\$k.glb`"","`"$R\game_$k`"","`"$P\survey\game_$k.json`"",$glbs[$k]) "game_$k"
}
"ALL DONE"
