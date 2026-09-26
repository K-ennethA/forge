"""Duskmaw v3 vs v4 sheets + the artist's v4 references beside v4 (pure image assembly, no scene).

    blender --background --factory-startup --python duskmaw_before_after.py

v3 tiles: renders/duskmaw/v3/duskmaw_<view>.png (v3's renders, archived; v3's silhouettes rendered from
improved/duskmaw_v3.blend with duskmaw_render.py into the same folder). v4 tiles: renders/duskmaw/duskmaw_v4_<view>.png.
Same lights / camera rules; each tile framed on its own model's bounds. References: design/reference/duskmaw-v4-maw-curve-
annotation.png (the blue curves + zigzag on v3's maw) and duskmaw-v4-shadowlord-reference.webp, letterboxed.
Writes:
  duskmaw_v4_before_after.png        rows front, maw, hem, threequarter, side, back   | columns v3 | v4
  duskmaw_v4_vs_annotation.png       rows maw, front                                  | columns artist's reference | v4
  duskmaw_v4_silhouette_compare.png  one row: v3 silhouette | v4 silhouette | the shadow-lord reference
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "duskmaw"))
REF = os.path.normpath(os.path.join(HERE, "..", "design", "reference"))
cols = [os.path.join(R, "v3", "duskmaw_%s.png"), os.path.join(R, "duskmaw_v4_%s.png")]
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
        parts = []
        for i, p in enumerate(paths):
            if i:
                parts.append(np.ones((T, 6, 4), np.float32))
            parts.append(tile(p))
        rows.append(np.concatenate(parts, axis=1))
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


sheet(views(["front", "maw", "hem", "threequarter", "side", "back"]), "duskmaw_v4_before_after.png")
sheet([[os.path.join(REF, "duskmaw-v4-maw-curve-annotation.png"), os.path.join(R, "duskmaw_v4_maw.png")],
       [os.path.join(REF, "duskmaw-v4-shadowlord-reference.webp"), os.path.join(R, "duskmaw_v4_front.png")]], "duskmaw_v4_vs_annotation.png")
sheet([[os.path.join(R, "v3", "duskmaw_silhouette.png"), os.path.join(R, "duskmaw_v4_silhouette.png"),
        os.path.join(REF, "duskmaw-v4-shadowlord-reference.webp")]], "duskmaw_v4_silhouette_compare.png")
