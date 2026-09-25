param([switch]$GlbOnly)
# Hero wave: survey.py (blend + tactical) over the hero source copies, glb mode over the shipped hero
# glbs (roster yaw 180, all four), and hero_facing.py over both. Hidden, headless, never saves.
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$G = "C:\Users\kenne\OneDrive\Desktop\git\Conquest\game\characters\models"
$S = "$P\survey\survey.py"
$F = "$P\survey\hero_facing.py"
$R = "$P\renders\survey"
$env:SURVEY_ORIG_DIR = "C:\Users\kenne\OneDrive\Documents"
$env:SURVEY_TACTICAL = "1"
$env:SURVEY_SIDE = "1"
function Run($argList, $tag) {
  $log = "$P\survey\log_$tag.txt"
  $proc = Start-Process -FilePath $B -ArgumentList $argList -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  "$tag exit=$($proc.ExitCode)"
}
if (-not $GlbOnly) { foreach ($n in "green_hero","gem_knight","necromancer","monster","monster2","monster_rigged") {
  $c = "`"$P\source-copies\hero-$n.blend`""
  Run @("--background",$c,"--factory-startup","--python","`"$S`"","--","blend","`"$R\hero-$n`"","`"$P\survey\hero-blend_$n.json`"") "hero-blend_$n"
  Run @("--background",$c,"--factory-startup","--python","`"$F`"","--","blend","`"$P\survey\hero-facing_$n.json`"") "hero-facing_$n"
} }
# (not $g: PowerShell names are case-insensitive and $G is the models root)
$glbs = @{ "vineweave"="forest"; "gem_knight"="earth"; "necromancer"="dark"; "monster"="dark" }
foreach ($k in $glbs.Keys) {
  $glbPath = "`"$G\$($glbs[$k])\$k.glb`""
  Run @("--background","--factory-startup","--python","`"$S`"","--","glb",$glbPath,"`"$R\hero-game_$k`"","`"$P\survey\hero-game_$k.json`"","180") "hero-game_$k"
  Run @("--background","--factory-startup","--python","`"$F`"","--","glb",$glbPath,"`"$P\survey\hero-facing_game_$k.json`"") "hero-facing_game_$k"
}
"ALL DONE"
