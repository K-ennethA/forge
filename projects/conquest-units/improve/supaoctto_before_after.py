"""Supaoctto sheets, pure image assembly (no scene; vampito_before_after.py copied, not imported):

    blender --background --factory-startup --python supaoctto_before_after.py

v3 sheets (renders/supaoctto/):
  v3_supaoctto_before_after.png        rows front / threequarter / side / tactical / back; columns: v2 (the committed v2
                                       renders, idle frame 1) | v3 (rigged, idle frame 1) | the v3 deepsea skin (same pose)
  v3_supaoctto_mouth.png               the smirk mouth, rest pose: front close-up SHUT | OPEN (morph 1.0), then the same
                                       from 30 deg, then face SHUT | OPEN
  v3_supaoctto_belt_gold_vs_orange.png rows front (idle 1) / belt close-up / belt from the side; columns gold (the
                                       committed default) | orange (palettes/supaoctto/belt_orange.json)
  v3_supaoctto_mask_w_vs_starfish.png  rows front (idle 1) / threequarter (idle 1) / face front / face 35 deg; columns
                                       W-visor (the committed default) | starfish (MASK_STYLE = "starfish")
  v3_supaoctto_closeups.png            one row: visor | mouth shut | mouth open | sigil | belt | cape + webs
Each tile is framed on its own model's bounds (the survey rule). 512 px tiles.
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "supaoctto"))
T = 512


def tile(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    iy = (np.arange(T) * h / T).astype(int); ix = (np.arange(T) * w / T).astype(int)
    return px[iy][:, ix]


def grid(rows_paths, out):
    rows = []
    for paths in reversed(rows_paths):              # pixel rows are bottom-up: the first listed row ends up on top
        parts = []
        for k, p in enumerate(paths):
            if k:
                parts.append(np.ones((T, 6, 4), np.float32))
            parts.append(tile(p))
        rows.append(np.concatenate(parts, axis=1))
    stack = []
    for k, r_ in enumerate(rows):
        if k:
            stack.append(np.ones((6, rows[0].shape[1], 4), np.float32))
        stack.append(r_)
    s = np.concatenate(stack, axis=0)
    im = bpy.data.images.new("sheet", s.shape[1], s.shape[0], alpha=True)
    im.pixels.foreach_set(s.ravel())
    im.filepath_raw = out; im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    print("WROTE", out)


P = lambda n: os.path.join(R, n + ".png")      # noqa: E731
grid([[P("v2_supaoctto_" + v), P("v3_supaoctto_" + v), P("v3_supaoctto_deepsea_" + v)]
      for v in ("front", "threequarter", "side", "tactical", "back")], P("v3_supaoctto_before_after"))
grid([[P("v3_supaoctto_close_mouth"), P("v3_supaoctto_open_mouth")],
      [P("v3_supaoctto_close_mouthside"), P("v3_supaoctto_open_mouthside")],
      [P("v3_supaoctto_close_face"), P("v3_supaoctto_open_face")]], P("v3_supaoctto_mouth"))
grid([[P("v3_supaoctto_front"), P("v3_supaoctto_beltorange_front")],
      [P("v3_supaoctto_close_belt"), P("v3_supaoctto_beltorange_close_belt")],
      [P("v3_supaoctto_close_beltside"), P("v3_supaoctto_beltorange_close_beltside")]], P("v3_supaoctto_belt_gold_vs_orange"))
grid([[P("v3_supaoctto_front"), P("v3_supaoctto_starfish_front")],
      [P("v3_supaoctto_threequarter"), P("v3_supaoctto_starfish_threequarter")],
      [P("v3_supaoctto_close_visorfront"), P("v3_supaoctto_starfish_close_visorfront")],
      [P("v3_supaoctto_close_facetq"), P("v3_supaoctto_starfish_close_facetq")]], P("v3_supaoctto_mask_w_vs_starfish"))
grid([[P("v3_supaoctto_close_visor"), P("v3_supaoctto_close_mouth"), P("v3_supaoctto_open_mouth"),
       P("v3_supaoctto_close_emblem"), P("v3_supaoctto_close_belt"), P("v3_supaoctto_close_capefull")]],
     P("v3_supaoctto_closeups"))
