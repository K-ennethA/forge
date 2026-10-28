#!/usr/bin/env bash
# vampwarrior_rawhead_run.sh <build_dir_name> [master_tris] [conquest_tris] : the look-only raw head, end to end.
# Inputs (rawhead_v1/work2): raw.npz (= head7_s56_edit.glb dumped to the f-frame; hybrid/work_v2/s2/edit_orig.npz),
# vis.npz (vampwarrior_rawhead_vis.py, z_cut -0.39), raw_islands.npy (vampwarrior_rawhead_islands.py),
# tex/${ALB:-albedo_fix_pad.png} (vampwarrior_rawhead_texfix.py + vampwarrior_rawhead_texpad.py).
set -e
# env ALB = albedo png name in rawhead_v1/tex (default albedo_fix_pad.png)
U=/c/Users/kenne/OneDrive/Desktop/git/forge/projects/conquest-units
I=$U/improve; RH=$U/design/reference/vampwarrior/rawhead_v1; W=$RH/work2
B="/c/Program Files/Blender Foundation/Blender 5.0/blender.exe"; PY=/c/Users/kenne/OneDrive/Desktop/git/forge/service/.venv/Scripts/python.exe
N=${1:-build1}; MT=${2:-50000}; CT=${3:-12000}; SZI=${4:-0}
# 4th arg: iterations of the eye-smear-zone Laplacian smoothing (weights work2/raw_smoothw.npy, vampwarrior_rawhead_zone.py); 0 = off
SZJ=""; [ "$SZI" != "0" ] && SZJ=", \"smooth_zone\": {\"w\": \"$(cd $U/design/reference/vampwarrior/rawhead_v1 && pwd -W)/work2/raw_smoothw.npy\", \"iters\": $SZI, \"mode\": \"laplace\"}"
O=$RH/$N; mkdir -p $O/r
Rw=$(cd $RH && pwd -W); Ow=$Rw/$N; Ww=$Rw/work2
cat > $O/build.json <<EOF
{"raw": "$Ww/raw.npz", "vis": "$Ww/vis.npz", "out": "$Ow/geo", "margin": 3, "export_glb": false,
 "lods": [{"name": "master", "tris": $MT}, {"name": "conquest", "tris": $CT}], "albedo": "$Rw/tex/${ALB:-albedo_fix_pad.png}"$SZJ}
EOF
"$B" -b --factory-startup --python $I/vampwarrior_rawhead_build.py -- $O/build.json > $O/build.log 2>&1
grep -E "^LOD|^NECK|^BUILD" $O/build.log | cut -c1-200
for L in master conquest; do
  "$B" -b --factory-startup --python $I/vampwarrior_rawhead_uvfix.py -- $W/raw.npz $W/raw_islands.npy $O/geo/$L.npz $O/$L.npz 2>&1 | grep UVFIX
done
cat > $O/export.json <<EOF
{"items": [{"name": "vampwarrior_head_master", "npz": "$Ow/master.npz", "glb": "$Ow/vampwarrior_head_master.glb"},
           {"name": "vampwarrior_head_conquest", "npz": "$Ow/conquest.npz", "glb": "$Ow/vampwarrior_head_conquest.glb"}],
 "albedo": "$Rw/tex/${ALB:-albedo_fix_pad.png}", "roughness": 0.6, "report": "$Ow/export_report.json", "zc": -0.39}
EOF
"$B" -b --factory-startup --python $I/vampwarrior_rawhead_export.py -- $O/export.json 2>&1 | grep -E "EXPORT|Error|Traceback"
"$B" -b --factory-startup --python $I/vampwarrior_rawhead_measure.py -- $W/raw.npz $W/vis.npz $O/metrics.json $O/master.npz $O/conquest.npz > $O/measure.log 2>&1
"$B" -b --factory-startup --python $I/vampwarrior_rawhead_texcheck.py -- $W/raw_padtex.npz $O/texcheck.json $O/master.npz $O/conquest.npz 2>&1 | grep TEX
RT="$Rw/../head_graft/head7/hybrid/work_v2/s2/edit_tex_orig.png"
cat > $O/render.json <<EOF
{"out": "$Ow/r", "res": 768, "items": [
 {"name": "raw", "npz": "$Ww/raw.npz", "tex": "$RT"},
 {"name": "new", "npz": "$Ow/master.npz", "tex": "$Rw/tex/${ALB:-albedo_fix_pad.png}"},
 {"name": "lod", "npz": "$Ow/conquest.npz", "tex": "$Rw/tex/${ALB:-albedo_fix_pad.png}"}],
 "shots": [{"tag": "h0", "angle": 0, "mode": "tex", "frame": "head"}, {"tag": "h35", "angle": 35, "mode": "tex", "frame": "head"},
  {"tag": "h90", "angle": 90, "mode": "tex", "frame": "head"}, {"tag": "hm35", "angle": -35, "mode": "tex", "frame": "head"},
  {"tag": "h180", "angle": 180, "mode": "tex", "frame": "head"},
  {"tag": "c0", "angle": 0, "mode": "clay", "frame": "head"}, {"tag": "c35", "angle": 35, "mode": "clay", "frame": "head"},
  {"tag": "c90", "angle": 90, "mode": "clay", "frame": "head"}, {"tag": "c180", "angle": 180, "mode": "clay", "frame": "head"},
  {"tag": "face", "angle": 0, "mode": "tex", "frame": "face"}, {"tag": "face35", "angle": 35, "mode": "tex", "frame": "face"},
  {"tag": "faceclay", "angle": 0, "mode": "clay", "frame": "face"}, {"tag": "eye", "angle": 0, "mode": "tex", "frame": "eye"},
  {"tag": "eyeR", "angle": 0, "mode": "tex", "frame": "eyeR"}, {"tag": "mouth", "angle": 0, "mode": "tex", "frame": "mouth"},
  {"tag": "eyeclay", "angle": 0, "mode": "clay", "frame": "eye"}, {"tag": "neck90", "angle": 90, "mode": "tex", "frame": "neck"}]}
EOF
"$B" -b --factory-startup --python $I/vampwarrior_rawhead_render.py -- $O/render.json 2>&1 | grep -c RENDERED
$PY $I/vampwarrior_rawhead_sheet.py $O/r $O/sheet_head.png raw new h0,h35,h90,c0,c35,c90 3 512
$PY $I/vampwarrior_rawhead_sheet.py $O/r $O/sheet_close.png raw new face,face35,faceclay,eye,eyeR,mouth 3 512
$PY $I/vampwarrior_rawhead_sheet.py $O/r $O/sheet_lod.png raw lod h0,h35,face,eye 2 512
"$B" -b --factory-startup --python $I/vampwarrior_rawhead_holes.py -- $W/raw.npz $W/vis.npz $O/holes.json $O/master.npz $O/conquest.npz 2>&1 | grep HOLES | cut -c1-300
echo RUN_DONE $N
