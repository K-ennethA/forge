"""Wren image assembly (plain Python + PIL, no Blender): run with the forge service venv.

    python -P wren_compose.py

renders/wren/ (inputs: wren_<view>.png stills, wren_winter_<view>.png, wren_idle_sheet.png, wren_walk_sheet.png):
  wren_sheet_vs_model.png   the sheet's front / side / back figures | the model's orthographic front / side / back (idle f1)
  wren_face_vs_sheet.png    the sheet's head panel | portrait | three-quarter face | eyes close-up, + the skin numbers
  wren_details_vs_sheet.png the sheet's necklace / bracer / cloak + patch / staff panels | the matching close-ups
  wren_hair.png             hair from the front, side, three-quarter back, back
  wren_contact.png          the main stills, close-ups, the winter skin + the idle / walk 8-frame sheets
  wren_skin_tone.json       sampled lit skin: the sheet vs the render
"""
import colorsys
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "wren")
SHEET = os.path.join(ROOT, "design", "reference", "wren-character-sheet.webp")
LAB = (235, 235, 235, 255)
IMP = json.load(open(os.path.join(ROOT, "improved", "wren.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "wren.json")))
PAL = json.load(open(os.path.join(ROOT, "palettes", "wren", "default.json")))
SH = Image.open(SHEET).convert("RGB")
CROPS = {"front": (20, 90, 470, 970), "side": (470, 90, 710, 970), "back": (730, 90, 1030, 970),
         "head": (1116, 90, 1318, 322), "necklace": (1344, 98, 1497, 282), "bracer": (1118, 357, 1287, 560),
         "cloak": (1318, 335, 1500, 528), "patch": (1320, 568, 1500, 744), "staff": (1120, 598, 1290, 840),
         "chips": (1300, 790, 1480, 975)}


def font(sz):
    for f in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def label(im, text, xy=(8, 6), sz=16):
    d = ImageDraw.Draw(im)
    f = font(sz)
    y = xy[1]
    for line in text.split("\n"):
        w = d.textlength(line, font=f)
        d.rectangle([xy[0] - 4, y - 3, xy[0] + w + 6, y + sz + 4], fill=(0, 0, 0, 185))
        d.text((xy[0], y), line, fill=LAB, font=f)
        y += sz + 8
    return im


def load(fn):
    if isinstance(fn, Image.Image):
        return fn.convert("RGBA")
    return Image.open(fn if os.path.isabs(fn) else os.path.join(R, fn)).convert("RGBA")


def fit(im, W, H):
    k = min(W / im.width, H / im.height)
    im2 = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    t = Image.new("RGBA", (W, H), (28, 28, 30, 255))
    t.paste(im2, ((W - im2.width) // 2, (H - im2.height) // 2), im2)
    return t


def grid(rows, W, H, title, out, head_h=46):
    nc = max(len(r) for r in rows)
    cs = Image.new("RGBA", (nc * (W + 6), len(rows) * (H + 6) + head_h), (28, 28, 30, 255))
    d = ImageDraw.Draw(cs)
    d.text((10, 10), title, fill=(255, 255, 255, 255), font=font(22))
    for i, r in enumerate(rows):
        for j, (src, lab) in enumerate(r):
            if src is None:
                continue
            t = fit(load(src), W, H)
            if lab:
                label(t, lab)
            cs.paste(t, (j * (W + 6), head_h + i * (H + 6)))
    cs.convert("RGB").save(os.path.join(R, out))
    print("WROTE", out)


def crop(k):
    return SH.crop(CROPS[k])


def lit_skin(img, box):
    """median of the lit warm-skin pixels in box (hue 12-40 deg, saturation 0.25-0.65, value > 0.45)."""
    a = np.array(img.convert("RGB")).astype(float)[box[1]:box[3], box[0]:box[2]].reshape(-1, 3)
    hsv = np.array([colorsys.rgb_to_hsv(*(p / 255.0)) for p in a])
    m = (hsv[:, 0] * 360 > 12) & (hsv[:, 0] * 360 < 40) & (hsv[:, 1] > 0.25) & (hsv[:, 1] < 0.65) & (hsv[:, 2] > 0.45)
    med = np.median(a[m], 0) if m.any() else np.zeros(3)
    h, s, v = colorsys.rgb_to_hsv(*(med / 255.0))
    return {"rgb": med.astype(int).tolist(), "hue_deg": round(h * 360, 1), "sat": round(s, 3), "val": round(v, 3), "pixels": int(m.sum())}


# ---- sheet vs model
grid([[(crop("front"), "SHEET front"), ("wren_ortho_front.png", "model, orthographic front (idle f1)"),
       (crop("side"), "SHEET side"), ("wren_ortho_side.png", "model, orthographic side"),
       (crop("back"), "SHEET back"), ("wren_ortho_back.png", "model, orthographic back")]],
     300, 580, "Wren - the sheet vs the model (front / side / back)", "wren_sheet_vs_model.png")

# ---- face + skin numbers
sk_sheet_front = lit_skin(SH, (228, 190, 262, 232))
sk_sheet_head = lit_skin(SH, (1190, 200, 1300, 300))
por = load("wren_portrait.png")
W_, H_ = por.size
sk_render = lit_skin(por, (int(W_ * 0.28), int(H_ * 0.45), int(W_ * 0.72), int(H_ * 0.75)))
SKIN = {"palette_skin_rgb": PAL["regions"]["skin"]["rgb"], "sheet_front_face_lit": sk_sheet_front,
        "sheet_head_panel_lit": sk_sheet_head, "render_portrait_lit": sk_render,
        "rule": "median of lit warm-skin pixels (hue 12-40 deg, sat 0.25-0.65, value > 0.45) in a face box"}
json.dump(SKIN, open(os.path.join(R, "wren_skin_tone.json"), "w"), indent=1)
grid([[(crop("head"), "SHEET head panel\nlit skin %s (hue %.0f, sat %.2f)" % (sk_sheet_head["rgb"], sk_sheet_head["hue_deg"], sk_sheet_head["sat"])),
       ("wren_portrait.png", "model portrait\nlit skin %s (hue %.0f, sat %.2f)" % (sk_render["rgb"], sk_render["hue_deg"], sk_render["sat"])),
       ("wren_face.png", "three-quarter"), ("wren_eyes.png", "eyes: bold brows, lid liner, brown iris")]],
     420, 460, "Wren - face vs the sheet (palette skin %s, sampled off the sheet's lit face)" % (PAL["regions"]["skin"]["rgb"],),
     "wren_face_vs_sheet.png")

# ---- details
grid([[(crop("necklace"), "SHEET necklace"), ("wren_necklace.png", "pendant + cord + clasp"),
       (crop("bracer"), "SHEET bracer"), ("wren_bracer.png", "bracer (left forearm), teal diamond")],
      [(crop("cloak"), "SHEET cloak + brass boss"), ("wren_cloak.png", "cloak from behind: hood, patches, ragged hem"),
       (crop("patch"), "SHEET cloth patch"), ("wren_patches.png", "patches + cross stitches")],
      [(crop("staff"), "SHEET staff"), ("wren_fork_head.png", "pitchfork head (rest pose): tines, brass collar, wrap"),
       (crop("chips"), "SHEET palette chips"), ("wren_fork_full.png", "pitchfork, full (idle f1, planted)")]],
     360, 360, "Wren - detail panels vs the model", "wren_details_vs_sheet.png")

# ---- hair
grid([[("wren_head_front.png", "front"), ("wren_head_side.png", "side"), ("wren_head_tq_back.png", "three-quarter back"),
       ("wren_head_back.png", "back: nape tail")]],
     420, 420, "Wren - hair (crown %.1f mm above the scalp; cap feathered to %.1f mm at the hairline; own UV strip)"
     % (IMP["hair"]["crown"]["crown_above_scalp_mm"], 1000 * (IMP["hair"]["cap_feather"]["rim_thickness_m_p50"] or 0)),
     "wren_hair.png")

# ---- contact
T = 400
items = [("wren_front.png", "front (idle f1)"), ("wren_threequarter.png", "three-quarter"), ("wren_side.png", "side"),
         ("wren_back.png", "back"), ("wren_tactical.png", "tactical (256 px)"), ("wren_portrait.png", "face"),
         ("wren_head_tq_back.png", "hair"), ("wren_patches.png", "cloak patches"), ("wren_bracer.png", "bracer"),
         ("wren_fork_head.png", "pitchfork"), ("wren_winter_front.png", "winter skin (swap proof)"),
         ("wren_winter_threequarter.png", "winter, three-quarter")]
tiles = [label(fit(load(fn), T, T), lab) for fn, lab in items]
Wc = 4 * T + 18
sheets = []
for clip, txt in (("idle", "idle: 8 frames / 4 s (leaning on the planted fork, weight shift, cloak / fringe follow-through)"),
                  ("walk", "walk: 8 frames / %.2f s, %.0f steps/min (bounce, the fork carried, cloak / tail / ties lag)"
                   % (RIG["clips"]["walk"]["seconds"], RIG["clips"]["walk"]["cadence_steps_per_min"]))):
    s_ = load("wren_%s_sheet.png" % clip)
    s_ = s_.resize((Wc, int(s_.height * Wc / s_.width)), Image.LANCZOS)
    sheets.append(label(s_, txt))
Hc = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (Wc, Hc), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 4) * (T + 6), (i // 4) * (T + 6)))
y = 3 * (T + 6)
for s_ in sheets:
    cs.paste(s_, (0, y)); y += s_.height + 6
cs.convert("RGB").save(os.path.join(R, "wren_contact.png"))
print("WROTE wren_contact.png", json.dumps(SKIN))
