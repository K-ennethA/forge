"""Supaoctto sheets, pure image assembly (no scene; vampito_before_after.py copied, not imported):

    blender --background --factory-startup --python supaoctto_before_after.py

renders/supaoctto/supaoctto_before_after.png   rows front / threequarter / side / tactical / back; columns: the raw sculpt
                                                AS SCULPTED (yaw 0, what the survey showed) | supaoctto (rigged, idle
                                                frame 1) | the deepsea skin (same pose: the palette-swap proof)
renders/supaoctto/facing_candidates.png        one row, the FRONT camera (on -Y) on the raw sculpt at yaw 0 / 90 / 180 / 270;
                                                180 is chosen: the goggles + mouth lead toward the -Y camera and the cape
                                                hangs behind
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


sheet([os.path.join(R, "source_yaw0_%s.png"), os.path.join(R, "supaoctto_%s.png"), os.path.join(R, "supaoctto_deepsea_%s.png")],
      ["front", "threequarter", "side", "tactical", "back"], os.path.join(R, "supaoctto_before_after.png"))
sheet([os.path.join(R, "source_yaw%d_%%s.png" % y) for y in (0, 90, 180, 270)], ["front_yaw"], os.path.join(R, "facing_candidates.png"))
