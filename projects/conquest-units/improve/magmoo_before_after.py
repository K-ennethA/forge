"""Before/after sheets (v4). Pure image assembly, no scene.

    blender --background --factory-startup --python magmoo_before_after.py

1. renders/magmoo/magmoo_v4_before_after.png -- left v3 (the combined serpent, deep-red translucent goo, glowing
   eyes with pupils, horizontal-S flight; renders/magmoo/magmoo_v3_<view>.png, kept from the v3 lane), right v4
   (magmoo_v4_<view>.png). Rows top -> bottom: threequarter (v4 = the segmented rest), side, tactical, head, flight side.
2. renders/magmoo/magmoo_v4_colour_reference.png -- design/reference/magmoo-v4-color-reference.png beside the v4
   threequarter rest and the v4 head closeup (each tile fitted into a square, aspect kept, grey padding).
Each tile is framed on its own model's projected bounds (the survey rule): this compares look, not absolute size.
"""
import bpy, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "magmoo"))
REF = os.path.normpath(os.path.join(HERE, "..", "design", "reference", "magmoo-v4-color-reference.png"))
T = 512


def load(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, img.channels)
    bpy.data.images.remove(img)
    if px.shape[2] == 3:
        px = np.dstack([px, np.ones((h, w, 1), np.float32)])
    return px


def tile(path, T_=T):
    """fit into a T x T square, aspect kept (nearest sampling), grey padding."""
    px = load(path)
    h, w = px.shape[:2]
    s = T_ / max(w, h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    iy = (np.arange(nh) / s).astype(int).clip(0, h - 1); ix = (np.arange(nw) / s).astype(int).clip(0, w - 1)
    out = np.full((T_, T_, 4), 0.18, np.float32); out[..., 3] = 1.0
    y0, x0 = (T_ - nh) // 2, (T_ - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = px[iy][:, ix]
    return out


def save(sheet, name):
    im = bpy.data.images.new(name, sheet.shape[1], sheet.shape[0], alpha=True)
    im.pixels.foreach_set(sheet.ravel())
    out = os.path.join(R, name)
    im.filepath_raw = out; im.file_format = "PNG"; im.save()
    bpy.data.images.remove(im)
    print("WROTE", out)


def grid(rows):
    sep_v = np.ones((T, 6, 4), np.float32)
    rr = []
    for row in rows:
        parts = []
        for i, t_ in enumerate(row):
            parts.append(t_)
            if i < len(row) - 1:
                parts.append(sep_v)
        rr.append(np.concatenate(parts, axis=1))
    hsep = np.ones((6, rr[0].shape[1], 4), np.float32)
    out = []
    for i, r_ in enumerate(rr[::-1]):                        # pixel rows are bottom-up: the first row goes on top
        out.append(r_)
        if i < len(rr) - 1:
            out.append(hsep)
    return np.concatenate(out, axis=0)


pairs = [("threequarter", "threequarter"), ("side", "side"), ("tactical", "tactical"), ("head", "head"),
         ("fly_side", "fly_side")]
save(grid([[tile(os.path.join(R, "magmoo_v3_%s.png" % a)), tile(os.path.join(R, "magmoo_v4_%s.png" % b))]
           for a, b in pairs]), "magmoo_v4_before_after.png")
save(grid([[tile(REF), tile(os.path.join(R, "magmoo_v4_threequarter.png")), tile(os.path.join(R, "magmoo_v4_head.png"))]]),
     "magmoo_v4_colour_reference.png")
