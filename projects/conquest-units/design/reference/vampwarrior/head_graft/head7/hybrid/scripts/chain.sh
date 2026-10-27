#!/bin/bash
# chain.sh <work_dir> [eye_expand] : the whole hybrid head build, reproducible from the frozen inputs in <work_dir>.
#   prep (once): s2/edit.npz + s2/edit_tex.png from head7_s56_edit.glb (sha d2886e4a6aa0)
#   fit (wrap2.py) -> parts + bake (hyb_build.py) -> albedo miss fill (post_tex.py) -> rig + keys + tests + glb (hyb_rig.py)
#   -> assembled rest mesh (hyb_export_npz.py) -> eye-opening metrics (eyepx.py + eyecomp.py)
# Heavy-lock note: each step is a single-head Blender job (< 2 min, < 3 GB); run through scratchpad/heavy_lock.py if a full
# unit build runs in parallel.
set -e
W=$(cd "$1" && pwd); S=$(cd "$(dirname "$0")" && pwd); M=$(cygpath -m "$W")
export HYB_EYE_EXPAND=${2:-0.0065}
BL="/c/Program Files/Blender Foundation/Blender 5.0/blender.exe"; PY=/c/Users/kenne/OneDrive/Desktop/git/forge/service/.venv/Scripts/python.exe
REF=/c/Users/kenne/OneDrive/Desktop/git/forge/projects/conquest-units/design/reference/vampwarrior/head_graft/head7
mkdir -p "$W/s2" "$W/s3" "$W/out"
if [ ! -f "$W/s2/edit.npz" ]; then
  $PY -P "$S/dump_glb.py" "$REF/head7_s56_edit.glb" "$W/s2/edit.npz"
  $PY -P -c "import numpy as np,sys; from PIL import Image; Image.fromarray(np.load(sys.argv[1])['tex']).save(sys.argv[2])" "$W/s2/edit.npz" "$W/s2/edit_tex.png"
fi
"$BL" -b --factory-startup --python "$S/wrap2.py" -- "$M" > "$W/out/fit.log" 2>&1
"$BL" -b --factory-startup --python "$S/hyb_build.py" -- "$M" "$M/s3/hyb_fit.npz" "$M/out" 2048 0.010 > "$W/out/build.log" 2>&1
$PY -P "$S/post_tex.py" "$W/out"
"$BL" -b --factory-startup --python "$S/hyb_rig.py" -- "$M/s3/hyb_fit.npz" "$M/out" > "$W/out/rig.log" 2>&1
"$BL" -b --factory-startup --python "$S/hyb_export_npz.py" -- "$M/out/hyb_rigged.blend" "$M/out/hyb_rest.npz"
"$BL" -b --factory-startup --python "$S/eyepx.py" -- "$M/out/hyb_rest.npz" "$M/s2/edit_eyemaps.npz" | grep EYEPX
(cd "$W" && $PY -P "$S/eyecomp.py" s2/edit.npz s2/edit_eyemaps.npz out/hyb_rest)
