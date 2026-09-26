"""Before/after sheets (v5). Pure image assembly, no scene.

    blender --background --factory-startup --python magmoo_before_after.py

1. renders/magmoo/magmoo_v5_before_after.png -- left v4 (segmented rest on the floor, orange goo, empty sockets,
   parabolic leap; renders/magmoo/magmoo_v4_<view>.png, kept from the v4 lane), right v5 (magmoo_v5_<view>.png).
   Rows top -> bottom: threequarter (rest), side (rest), tactical, head closeup, walk mid-air side.
2. renders/magmoo/magmoo_v5_rest_vs_reference.png -- design/reference/magmoo-v5-rest-pose-reference.png beside the v5
   rest pose front / threequarter / side.
3. renders/magmoo/magmoo_v5_colour_compare.png -- rows [reference | v4 threequarter | v5 threequarter] and
   [v4 colour reference | v4 head | v5 head]; + magmoo_v5_colour.json: the hue of the saturated (unit) pixels of each
   image (HSV hue, degrees, 0 = red; median + quartiles + the red / orange / yellow band shares) -- the body hue shift.
4. renders/magmoo/magmoo_v5_eye_closeup.png -- [v4 head (empty socket) | v5 head | v5 eyeclose | v5 headside].
Each tile is framed on its own model's projected bounds (the survey rule): this compares look, not absolute size.
"""
import bpy, os, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "magmoo"))
DREF = os.path.normpath(os.path.join(HERE, "..", "design", "reference"))
REF5 = os.path.join(DREF, "magmoo-v5-rest-pose-reference.png")
REF4 = os.path.join(DREF, "magmoo-v4-color-reference.png")
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


def hue_stats(path):
    """HSV hue (deg, red = 0, wrapped to [-60, 300)) of the saturated, lit pixels (sat > 0.45, max > 60/255): the
    unit (the grey studio floor / background are unsaturated)."""
    px = load(path)[..., :3].reshape(-1, 3)
    mx = px.max(1); mn = px.min(1)
    m = ((mx - mn) / np.maximum(mx, 1e-6) > 0.45) & (mx > 60 / 255.0)
    p = px[m]; mx_, mn_ = mx[m], mn[m]; d = np.maximum(mx_ - mn_, 1e-6)
    r, g, b = p[:, 0], p[:, 1], p[:, 2]
    h = np.where(mx_ == r, ((g - b) / d) % 6.0, np.where(mx_ == g, (b - r) / d + 2.0, (r - g) / d + 4.0)) * 60.0
    h = np.where(h >= 300.0, h - 360.0, h)
    band = lambda a, b_: round(float(((h >= a) & (h < b_)).mean()), 4)
    return {"pixels": int(m.sum()), "hue_median_deg": round(float(np.median(h)), 2),
            "hue_p25_p75_deg": np.percentile(h, [25, 75]).round(2).tolist(),
            "share_red_lt12deg": band(-60, 12), "share_orange_12_30deg": band(12, 30), "share_yellow_ge30deg": band(30, 300),
            "median_rgb_red_band": (np.median(p[h < 12], 0) * 255).round().tolist() if (h < 12).any() else None}


v = lambda ver, view: os.path.join(R, "magmoo_%s_%s.png" % (ver, view))
pairs = ["threequarter", "side", "tactical", "head", "fly_side"]
save(grid([[tile(v("v4", a)), tile(v("v5", a))] for a in pairs]), "magmoo_v5_before_after.png")
save(grid([[tile(REF5), tile(v("v5", "front")), tile(v("v5", "threequarter")), tile(v("v5", "side"))]]),
     "magmoo_v5_rest_vs_reference.png")
save(grid([[tile(REF5), tile(v("v4", "threequarter")), tile(v("v5", "threequarter"))],
           [tile(REF4), tile(v("v4", "head")), tile(v("v5", "head"))]]), "magmoo_v5_colour_compare.png")
save(grid([[tile(v("v4", "head")), tile(v("v5", "head")), tile(v("v5", "eyeclose")), tile(v("v5", "headside"))]]),
     "magmoo_v5_eye_closeup.png")
col = {"rule": hue_stats.__doc__.strip(),
       "v5_reference": hue_stats(REF5), "v4_colour_reference": hue_stats(REF4),
       "v4_threequarter": hue_stats(v("v4", "threequarter")), "v5_threequarter": hue_stats(v("v5", "threequarter")),
       "v4_side": hue_stats(v("v4", "side")), "v5_side": hue_stats(v("v5", "side")),
       "v4_head": hue_stats(v("v4", "head")), "v5_head": hue_stats(v("v5", "head"))}
col["body_hue_shift_deg_threequarter"] = round(col["v5_threequarter"]["hue_median_deg"] - col["v4_threequarter"]["hue_median_deg"], 2)
col["v5_minus_reference_deg_threequarter"] = round(col["v5_threequarter"]["hue_median_deg"] - col["v5_reference"]["hue_median_deg"], 2)
json.dump(col, open(os.path.join(R, "magmoo_v5_colour.json"), "w"), indent=1)
print("COLOUR", json.dumps({k: (v_["hue_median_deg"] if isinstance(v_, dict) else v_) for k, v_ in col.items() if k != "rule"}))
