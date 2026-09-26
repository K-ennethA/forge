"""Supaoctto sheets, pure image assembly (no scene; vampito_before_after.py copied, not imported):

    blender --background --factory-startup --python supaoctto_before_after.py

renders/supaoctto/v2_supaoctto_before_after.png  rows front / threequarter / side / tactical / back; columns: v1 (the
                                                  committed v1 renders, supaoctto_<view>.png, idle frame 1) | v2 (rigged,
                                                  idle frame 1) | the v2 deepsea skin (same pose: the palette-swap proof)
renders/supaoctto/v2_supaoctto_closeups.png       one row: visor | siphon | neck + head | chest sigil | cape + webs
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


def sheet(cols, views, out):
    rows = []
    for view in reversed(views):                  # pixel rows are bottom-up: the first listed view ends up on top
        row = [tile(c % view) for c in cols]
        parts = []
        for k, t_ in enumerate(row):
            if k:
                parts.append(np.ones((T, 6, 4), np.float32))
            parts.append(t_)
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


sheet([os.path.join(R, "supaoctto_%s.png"), os.path.join(R, "v2_supaoctto_%s.png"), os.path.join(R, "v2_supaoctto_deepsea_%s.png")],
      ["front", "threequarter", "side", "tactical", "back"], os.path.join(R, "v2_supaoctto_before_after.png"))
sheet([os.path.join(R, "v2_supaoctto_close_" + v + "%s.png") for v in ("visor", "siphon", "neck", "emblem", "capefull")],
      [""], os.path.join(R, "v2_supaoctto_closeups.png"))
