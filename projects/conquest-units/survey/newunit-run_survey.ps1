param([switch]$ChecksOnly, [switch]$PartsOnly)
# New-unit wave (2026-09-25): survey.py (blend + tactical + side), hero_facing.py and the contract
# checker over the newunit-* source copies. Hidden, headless, never saves.
# (PowerShell names are case-insensitive: the checker path is $K, never $C, which the loop's $c clobbers.)
$B = "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
$P = "C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"
$S = "$P\survey\survey.py"
$F = "$P\survey\hero_facing.py"
# hero_facing skips 'Icosphere*' meshes (glb bone shapes); magmoo/vampito/Fidough keep real parts under
# that name, so blend mode always goes through the renaming wrapper.
$W = "$P\survey\newunit-facing_wrap.py"
# the checker raises on an armature with no animation_data (supaoctto_rig); the wrapper adds an empty one
$K = "$P\survey\newunit-check_wrap.py"
$R = "$P\renders\survey"
$env:SURVEY_ORIG_DIR = "C:\Users\kenne\OneDrive\Documents"
$env:SURVEY_TACTICAL = "1"
$env:SURVEY_SIDE = "1"
function Run($argList, $tag) {
  $log = "$P\survey\log_$tag.txt"
  $proc = Start-Process -FilePath $B -ArgumentList $argList -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  "$tag exit=$($proc.ExitCode)"
}
foreach ($n in "mosquitopire","Fidough","magmoo","vampito","huntress","supaoctto","supaoctto_rig") {
  $c = "`"$P\source-copies\newunit-$n.blend`""
  Run @("--background",$c,"--factory-startup","--python","`"$P\survey\newunit-parts.py`"","--","`"$P\survey\newunit-parts_$n.json`"") "newunit-parts_$n"
  Run @("--background",$c,"--factory-startup","--python","`"$P\survey\newunit-landmarks.py`"","--","`"$P\survey\newunit-landmarks_$n.json`"") "newunit-landmarks_$n"
  if ($PartsOnly) { continue }
  if (-not $ChecksOnly) {
    Run @("--background",$c,"--factory-startup","--python","`"$S`"","--","blend","`"$R\newunit-$n`"","`"$P\survey\newunit-blend_$n.json`"") "newunit-blend_$n"
  }
  Run @("--background",$c,"--factory-startup","--python","`"$W`"","--","blend","`"$P\survey\newunit-facing_$n.json`"") "newunit-facing_$n"
  Run @("--background",$c,"--factory-startup","--python","`"$K`"","--","`"$P\survey\newunit-check_$n.json`"") "newunit-check_$n"
}
"ALL DONE"
