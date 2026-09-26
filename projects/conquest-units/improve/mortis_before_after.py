"""Before/after sheet: the shipped beige necromancer.glb (rendered at its TRUE front by mortis_render.py --glb
--yaw 180, same lights/camera rules) vs the reworked Mortis. Pure image assembly, no scene.

    blender --background --factory-startup --python mortis_before_after.py

Rows: front, threequarter, tactical (256 px tiles upscaled nearest). Columns: shipped glb | rework.
Each tile is framed on its own model's bounds (the survey rule): this compares look and silhouette.
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "mortis"))
cols = [os.path.join(R, "before_glb_%s.png"), os.path.join(R, "mortis_%s.png")]
T = 512


def tile(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    iy = (np.arange(T) * h / T).astype(int); ix = (np.arange(T) * w / T).astype(int)
    return px[iy][:, ix]


rows = []
for view in ("tactical", "threequarter", "front"):      # pixel rows are bottom-up: last listed = top row
    row = [tile(c % view) for c in cols]
    sep = np.ones((T, 6, 4), np.float32)
    rows.append(np.concatenate([row[0], sep, row[1]], axis=1))
hsep = np.ones((6, rows[0].shape[1], 4), np.float32)
sheet = np.concatenate([rows[0], hsep, rows[1], hsep, rows[2]], axis=0)
out = os.path.join(R, "mortis_before_after.png")
im = bpy.data.images.new("ba", sheet.shape[1], sheet.shape[0], alpha=True)
im.pixels.foreach_set(sheet.ravel())
im.filepath_raw = out; im.file_format = "PNG"; im.save()
print("WROTE", out)
