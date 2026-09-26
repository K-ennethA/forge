"""Duskmaw v2 vs v3 sheets + the artist's annotations beside v3 (pure image assembly, no scene).

    blender --background --factory-startup --python duskmaw_before_after.py

v2 tiles: renders/duskmaw/v2/duskmaw_<view>.png (v2's renders, kept; the extra v2 views -- hem, hemthreequarter,
mawthreequarter -- rendered from improved/duskmaw_v2.blend with duskmaw_render.py). v3 tiles:
renders/duskmaw/duskmaw_v3_<view>.png. Same lights / camera rules; each tile framed on its own model's bounds.
Annotations: design/reference/duskmaw-v3-{maw,hem}-annotation.png (the artist's red marks on v2), letterboxed.
Writes:
  duskmaw_v3_before_after.png       rows front, maw, hem, threequarter, side, back | columns v2 | v3
  duskmaw_v3_head_before_after.png  rows head, headthreequarter                    | columns v2 | v3
  duskmaw_v3_vs_annotation.png      rows maw, hem                                  | columns artist's mark (on v2) | v3
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "duskmaw"))
REF = os.path.normpath(os.path.join(HERE, "..", "design", "reference"))
cols = [os.path.join(R, "v2", "duskmaw_%s.png"), os.path.join(R, "duskmaw_v3_%s.png")]
T = 512


def tile(path):
    """letterboxed into T x T (aspect kept), grey padding"""
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    k = T / max(w, h)
    nw, nh = max(1, int(round(w * k))), max(1, int(round(h * k)))
    iy = (np.arange(nh) * h / nh).astype(int); ix = (np.arange(nw) * w / nw).astype(int)
    out = np.full((T, T, 4), 0.35, np.float32); out[..., 3] = 1.0
    y0, x0 = (T - nh) // 2, (T - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = px[iy][:, ix]
    return out


def sheet(rows_paths, out):
    rows = []
    for paths in reversed(rows_paths):              # pixel rows are bottom-up: last listed = bottom row
        row = [tile(p) for p in paths]
        rows.append(np.concatenate([row[0], np.ones((T, 6, 4), np.float32), row[1]], axis=1))
    parts = []
    for i, r_ in enumerate(rows):
        if i:
            parts.append(np.ones((6, rows[0].shape[1], 4), np.float32))
        parts.append(r_)
    s = np.concatenate(parts, axis=0)
    im = bpy.data.images.new("ba", s.shape[1], s.shape[0], alpha=True)
    im.pixels.foreach_set(s.ravel())
    im.filepath_raw = os.path.join(R, out); im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    print("WROTE", out)


def views(vs):
    return [[c % v for c in cols] for v in vs]


sheet(views(["front", "maw", "hem", "threequarter", "side", "back"]), "duskmaw_v3_before_after.png")
sheet(views(["head", "headthreequarter"]), "duskmaw_v3_head_before_after.png")
sheet([[os.path.join(REF, "duskmaw-v3-maw-annotation.png"), os.path.join(R, "duskmaw_v3_maw.png")],
       [os.path.join(REF, "duskmaw-v3-hem-annotation.png"), os.path.join(R, "duskmaw_v3_front.png")]], "duskmaw_v3_vs_annotation.png")
