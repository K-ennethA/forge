"""Supaoctto sheets, pure image assembly (no scene; vampito_before_after.py copied, not imported):

    blender --background --factory-startup --python supaoctto_before_after.py

v4 sheets (renders/supaoctto/; the v3 sheets on disk stay as they were):
  v4_supaoctto_before_after.png        v3 octopus chest vs v4 starfish chest: rows front / threequarter / emblem close-up /
                                       tactical; columns v3 (the committed v3 renders) | v4 | the v4 deepsea skin (silver)
  v4_supaoctto_mask_threeup.png        rows front + threequarter (idle 1), face front, face 35 deg, face 68 deg; columns
                                       W-visor (committed default) | starfish glued (v3 variant) | starfish floating (v4)
  v4_supaoctto_emblem.png              emblem close-up gold | deepsea silver | tactical (256 px, nearest x2) gold | deepsea
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
grid([[P("v3_supaoctto_" + v), P("v4_supaoctto_" + v), P("v4_supaoctto_deepsea_" + v)]
      for v in ("front", "threequarter")] +
     [[P("v3_supaoctto_close_emblem"), P("v4_supaoctto_close_emblem"), P("v4_supaoctto_deepsea_emblem")],
      [P("v3_supaoctto_tactical"), P("v4_supaoctto_tactical"), P("v4_supaoctto_deepsea_tactical")]],
     P("v4_supaoctto_before_after"))
grid([[P("v4_supaoctto_" + v), P("v4_supaoctto_starfish_" + v), P("v4_supaoctto_starfish_floating_" + v)]
      for v in ("front", "threequarter")] +
     [[P("v4_supaoctto_close_" + v), P("v4_supaoctto_starfish_close_" + v), P("v4_supaoctto_starfish_floating_close_" + v)]
      for v in ("visorfront", "facetq", "faceside")], P("v4_supaoctto_mask_threeup"))
grid([[P("v4_supaoctto_close_emblem"), P("v4_supaoctto_deepsea_emblem"), P("v4_supaoctto_tactical"),
       P("v4_supaoctto_deepsea_tactical")]], P("v4_supaoctto_emblem"))
