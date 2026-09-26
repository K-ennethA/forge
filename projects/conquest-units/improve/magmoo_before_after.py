"""Before/after sheet: the SOURCE blob alone (newunit-magmoo.blend's only mesh, game-framed, untextured) vs the
assembled 3-segment serpent (default lava palette) vs the obsidian skin. Pure image assembly, no scene.

    blender --background --factory-startup --python magmoo_before_after.py

Rows (top -> bottom): threequarter, side, front. Columns: source blob | serpent default | serpent obsidian.
Each tile is framed on its own model's projected bounds (the survey rule): this compares look and silhouette; the
source blob IS the serpent's main body (its middle segment).
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "magmoo"))
cols = [os.path.join(R, "source_blob_%s.png"), os.path.join(R, "magmoo_%s.png"), os.path.join(R, "magmoo_obsidian_%s.png")]
T = 512


def tile(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    iy = (np.arange(T) * h / T).astype(int); ix = (np.arange(T) * w / T).astype(int)
    return px[iy][:, ix]


rows = []
for view in ("front", "side", "threequarter"):      # pixel rows are bottom-up: last listed = top row
    row = [tile(c % view) for c in cols]
    sep = np.ones((T, 6, 4), np.float32)
    rows.append(np.concatenate([row[0], sep, row[1], sep, row[2]], axis=1))
hsep = np.ones((6, rows[0].shape[1], 4), np.float32)
sheet = np.concatenate([rows[0], hsep, rows[1], hsep, rows[2]], axis=0)
out = os.path.join(R, "magmoo_before_after.png")
im = bpy.data.images.new("ba", sheet.shape[1], sheet.shape[0], alpha=True)
im.pixels.foreach_set(sheet.ravel())
im.filepath_raw = out; im.file_format = "PNG"; im.save()
print("WROTE", out)
