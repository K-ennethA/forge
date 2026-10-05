"""Shadow Assassin comparison strip + stills sheet (plain Python + PIL, no Blender; the forge service venv):

    python -P improve/shadow_assassin_compose.py

renders/shadow_assassin/ inputs: <ver>_ortho_{front,side,back}.png (transparent film) + the perspective stills / close-ups,
improved/shadow_assassin.json, rigged/shadow_assassin.json. Outputs:
  <ver>_sheet_compare.png  BUILD | SHEET for front / side / back -- each build ortho auto-cropped by alpha, laid on the sheet's
                           paper tone, scaled to the same figure height as the sheet's own view crop beside it (the sheet
                           figure crops below are the views of design/reference/shadow_assassin/shadow_assassin_sheet.webp)
  <ver>_sheet.png          the stills (4 full-body + close-ups) with the measured numbers
"""
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "shadow_assassin")
V = os.environ.get("SA_V", "shadow_assassin_v1")
SHEET = os.path.join(ROOT, "design", "reference", "shadow_assassin", "shadow_assassin_sheet.webp")
REF = {"front": (25, 100, 450, 915), "side": (455, 100, 735, 915), "back": (730, 100, 1030, 915)}   # sheet view crops (px)
PAPER = (212, 203, 176)
H = 900
PAD, LAB = 10, 28
try:
    FONT = ImageFont.truetype("arial.ttf", 20); FONT_S = ImageFont.truetype("arial.ttf", 15)
except Exception:
    FONT = FONT_S = ImageFont.load_default()


def fig_crop_sheet(box):
    im = Image.open(SHEET).convert("RGB").crop(box)
    a = np.asarray(im).astype(float)
    m = (np.abs(a - np.array(PAPER)).sum(2) > 60)      # figure pixels (not paper)
    ys, xs = np.nonzero(m)
    return im.crop((max(xs.min() - 6, 0), max(ys.min() - 6, 0), min(xs.max() + 7, im.size[0]), min(ys.max() + 7, im.size[1])))


def fig_crop_build(path):
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im)
    ys, xs = np.nonzero(a[:, :, 3] > 8)
    im = im.crop((xs.min() - 6, ys.min() - 6, xs.max() + 7, ys.max() + 7))
    bg = Image.new("RGBA", im.size, PAPER + (255,))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def fit_h(im, h):
    return im.resize((max(1, int(round(im.size[0] * h / im.size[1]))), h), Image.LANCZOS)


tiles = []
for view in ("front", "side", "back"):
    p = os.path.join(R, "%s_ortho_%s.png" % (V, view))
    b = fit_h(fig_crop_build(p), H) if os.path.exists(p) else Image.new("RGB", (300, H), (80, 30, 30))
    s = fit_h(fig_crop_sheet(REF[view]), H)
    tiles.append((view, b, s))
W = PAD + sum(b.size[0] + PAD + s.size[0] + 3 * PAD for _, b, s in tiles)
strip = Image.new("RGB", (W, PAD + LAB + H + PAD + 30), (28, 28, 30))
d = ImageDraw.Draw(strip)
x = PAD
for view, b, s in tiles:
    d.text((x + 4, PAD + 2), "%s  BUILD (%s)" % (view, V.split("_")[-1]), fill=(235, 235, 235), font=FONT)
    strip.paste(b, (x, PAD + LAB)); x += b.size[0] + PAD
    d.text((x + 4, PAD + 2), "%s  SHEET" % view, fill=(235, 235, 235), font=FONT)
    strip.paste(s, (x, PAD + LAB)); x += s.size[0] + 3 * PAD
d.text((PAD + 4, PAD + LAB + H + 6), "ortho build views auto-cropped + scaled to the sheet figure height; sheet side view = his left (faces image-left)",
       fill=(220, 220, 210), font=FONT_S)
out = os.path.join(R, V + "_sheet_compare.png")
strip.save(out)
print("STRIP", out, strip.size)
# ---- the stills sheet
IMP = json.load(open(os.path.join(ROOT, "improved", "shadow_assassin.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "shadow_assassin.json")))
ROW1 = [("front", "front"), ("threequarter", "3/4"), ("side", "side (his left)"), ("back", "back")]
ROW2 = [("head_front", "hood void (front)"), ("head_tq", "hood 3/4"), ("blade", "blade + fist"), ("hold_tq", "hold 3/4"),
        ("belt", "belt / pouches / sash"), ("chest", "scarf / brooch / pendant"), ("back_sigil", "back sigil"), ("boots", "boots + knee guards")]
W1 = 520; cols2 = 4
Wt = 4 * W1 + 5 * PAD
w2 = (Wt - (cols2 + 1) * PAD) // cols2
Hs = PAD + LAB + W1 + PAD + 2 * (LAB + w2 + PAD) + 140
sh = Image.new("RGB", (Wt, Hs), (28, 28, 30))
d = ImageDraw.Draw(sh)


def tile(name, w):
    p = os.path.join(R, "%s_%s.png" % (V, name))
    return Image.open(p).convert("RGB").resize((w, w), Image.LANCZOS) if os.path.exists(p) else Image.new("RGB", (w, w), (60, 30, 30))


for k, (v, lab) in enumerate(ROW1):
    xx = PAD + k * (W1 + PAD)
    d.text((xx + 4, PAD + 2), lab, fill=(235, 235, 235), font=FONT)
    sh.paste(tile(v, W1), (xx, PAD + LAB))
y = PAD + LAB + W1 + PAD
for k, (v, lab) in enumerate(ROW2):
    r_, c_ = divmod(k, cols2)
    xx = PAD + c_ * (w2 + PAD); yy = y + r_ * (LAB + w2 + PAD)
    d.text((xx + 4, yy + 2), lab, fill=(235, 235, 235), font=FONT)
    sh.paste(tile(v, w2), (xx, yy + LAB))
y += 2 * (LAB + w2 + PAD)
t = IMP["tris"]; hd = RIG["grip"]["blade"]["hold"]; gl = RIG.get("glb", {})
lines = ["SHADOW ASSASSIN %s draft (hero tier)   tris %d (main %d + blade %d; budget 30-50k)   height %.3f m (hood top), body %.3f m"
         % (V.split("_")[-1], t["total"], t["main"], t["blade"], IMP["measure"]["height"], IMP["landmarks"]["height_total"]),
         "blade hold (bind pose): wrist bend %.1f deg (hand roll %s, grip diag %s), elbow %.1f deg, finger pads %s mm, max finger penetration %s mm"
         % (hd["wrist_bend_deg"], hd["hand_roll_deg"], hd.get("grip_diag_deg"), hd["elbow_flex_deg"], hd["fingertip_pad_mesh_to_grip_surface_mm"],
            hd.get("finger_mesh_max_penetration_mm")),
         "glow: %s   glb %s B  %s   clips: none (S3)" % (json.dumps(gl.get("glow_audit", {})), gl.get("bytes"), gl.get("sha256_16"))]
for k, ln in enumerate(lines):
    d.text((PAD + 4, y + 6 + 24 * k), ln[:260], fill=(220, 220, 210), font=FONT_S)
out2 = os.path.join(R, V + "_sheet.png")
sh.save(out2)
print("SHEET", out2, sh.size)
