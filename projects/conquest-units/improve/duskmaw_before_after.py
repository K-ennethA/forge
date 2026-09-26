"""Duskmaw v1 vs v2 sheets (pure image assembly, no scene).

    blender --background --factory-startup --python duskmaw_before_after.py

v1 tiles: renders/duskmaw/v1/duskmaw_<view>.png (the v1 build's renders, kept; the extra v1 views were rendered from a byte
copy of the v1 improved blend with duskmaw_render.py). v2 tiles: renders/duskmaw/duskmaw_<view>.png. Same lights / camera
rules; each tile framed on its own model's bounds.
Writes:
  duskmaw_v2_before_after.png    rows front, maw, lowfront, back, threequarter  | columns v1 | v2
  duskmaw_head_before_after.png  rows head, headthreequarter                    | columns v1 | v2
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "duskmaw"))
cols = [os.path.join(R, "v1", "duskmaw_%s.png"), os.path.join(R, "duskmaw_%s.png")]
T = 512


def tile(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    iy = (np.arange(T) * h / T).astype(int); ix = (np.arange(T) * w / T).astype(int)
    return px[iy][:, ix]


def sheet(views, out):
    rows = []
    for view in reversed(views):                     # pixel rows are bottom-up: last listed = bottom row
        row = [tile(c % view) for c in cols]
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


sheet(["front", "maw", "lowfront", "back", "threequarter"], "duskmaw_v2_before_after.png")
sheet(["head", "headthreequarter"], "duskmaw_head_before_after.png")
