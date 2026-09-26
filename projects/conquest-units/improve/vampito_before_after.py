"""Vampito sheets, pure image assembly (no scene):

    blender --background --factory-startup --python vampito_before_after.py

renders/vampito/vampito_before_after.png   rows front / threequarter / side / tactical;
                                            columns: the raw mosquitopire sculpt AS SCULPTED (yaw 0, what the survey
                                            showed) | vampito (rigged, hovering: idle frame 4, mid-downstroke)
renders/vampito/facing_candidates.png      rows front / side; columns: sculpt yaw 0 | sculpt yaw 180 (the chosen facing:
                                            the proboscis prongs and the eyes lead toward the -Y front camera)
Each tile is framed on its own model's bounds (the survey rule): the sheets compare look and silhouette. 512 px tiles.
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "vampito"))
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


sheet([os.path.join(R, "source_yaw0_%s.png"), os.path.join(R, "vampito_%s.png")],
      ["front", "threequarter", "side", "tactical"], os.path.join(R, "vampito_before_after.png"))
sheet([os.path.join(R, "source_yaw0_%s.png"), os.path.join(R, "source_yaw180_%s.png")],
      ["front", "side"], os.path.join(R, "facing_candidates.png"))
