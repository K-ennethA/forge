"""Before/after sheet (v3): v2 (opaque goo, eyeball spheres; renders/magmoo/magmoo_v2_<view>.png, kept from the v2
lane) vs v3 (translucent goo, eye sockets; magmoo_v3_<view>.png). Pure image assembly, no scene.

    blender --background --factory-startup --python magmoo_before_after.py

Rows (top -> bottom): threequarter, front, tactical, head, eyeprofile. Left column v2, right column v3. Each tile is
framed on its own model's projected bounds (the survey rule): this compares look and silhouette, not absolute size.
magmoo_v2_eyeprofile.png was rendered once from the v2 rigged blend with the v3 magmoo_render.py (same view rule)
before the v3 build replaced it.
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "magmoo"))
cols = [os.path.join(R, "magmoo_v2_%s.png"), os.path.join(R, "magmoo_v3_%s.png")]
T = 512


def tile(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    iy = (np.arange(T) * h / T).astype(int); ix = (np.arange(T) * w / T).astype(int)
    return px[iy][:, ix]


rows = []
for view in ("eyeprofile", "head", "tactical", "front", "threequarter"):   # pixel rows are bottom-up: last = top row
    row = [tile(c % view) for c in cols]
    sep = np.ones((T, 6, 4), np.float32)
    rows.append(np.concatenate([row[0], sep, row[1]], axis=1))
hsep = np.ones((6, rows[0].shape[1], 4), np.float32)
parts = []
for i, r in enumerate(rows):
    parts.append(r)
    if i < len(rows) - 1:
        parts.append(hsep)
sheet = np.concatenate(parts, axis=0)
out = os.path.join(R, "magmoo_v3_before_after.png")
im = bpy.data.images.new("ba", sheet.shape[1], sheet.shape[0], alpha=True)
im.pixels.foreach_set(sheet.ravel())
im.filepath_raw = out; im.file_format = "PNG"; im.save()
print("WROTE", out)
