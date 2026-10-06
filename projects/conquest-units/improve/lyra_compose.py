"""Lyra comparison strip + stills sheet (plain Python + PIL, no Blender; the forge service venv):

    python -P improve/lyra_compose.py

renders/lyra/ inputs: <ver>_ortho_{front,side,back}.png (transparent film) + the perspective stills / close-ups,
improved/lyra.json, rigged/lyra.json. Outputs:
  <ver>_sheet_compare.png  BUILD | SHEET for front / side / back -- each build ortho auto-cropped by alpha, laid on the sheet's
                           paper tone, scaled to the same figure height as the sheet's own view crop beside it (the sheet
                           figure crops = the views of design/reference/lyra/lyra_sheet.webp)
  <ver>_sheet.png          the stills (4 full-body + close-ups) with the measured numbers
"""
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "lyra")
V = os.environ.get("LYRA_V", "lyra_v1")
SHEET = os.path.join(ROOT, "design", "reference", "lyra", "lyra_sheet.webp")
REF = {"front": (20, 95, 372, 935), "side": (385, 80, 662, 935), "back": (665, 60, 975, 935)}   # sheet view crops (px)
PAPER = (226, 219, 196)
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
    m[-12:, :] = False                                 # (the view label row under the figure)
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


PREV = os.environ.get("LYRA_FIX_PREV")
if PREV:
    # v1.1 FIX STRIP (render economy: only the views the fix touches): PREV | V | SHEET per row, rows = the full front, the
    # head 3/4, the head side; the sheet crop per row = its front figure / HEAD DETAIL panel / side figure's head
    FIX_ROWS = [("front", "front", (20, 95, 372, 935)), ("head_tq", "head 3/4", (1024, 20, 1266, 236)),
                ("head_side", "head side", (418, 70, 618, 270))]
    HT = 560
    rows = []
    for v_, lab_, box_ in FIX_ROWS:
        a_ = [Image.open(os.path.join(R, "%s_%s.png" % (k_, v_))).convert("RGB") if os.path.exists(os.path.join(R, "%s_%s.png" % (k_, v_)))
              else Image.new("RGB", (HT, HT), (80, 30, 30)) for k_ in (PREV, V)]
        s_ = Image.open(SHEET).convert("RGB").crop(box_)
        rows.append((lab_, [fit_h(x_, HT) for x_ in a_] + [fit_h(s_, HT)]))
    Wf = PAD + max(sum(t_.size[0] + PAD for t_ in ts_) for _, ts_ in rows)
    fx = Image.new("RGB", (Wf, PAD + len(rows) * (LAB + HT + PAD) + 30), (28, 28, 30))
    d = ImageDraw.Draw(fx)
    y = PAD
    for lab_, ts_ in rows:
        x = PAD
        for t_, nm_ in zip(ts_, (PREV.split("_")[-1], V.split("_")[-1], "SHEET")):
            d.text((x + 4, y + 2), "%s  %s" % (lab_, nm_), fill=(235, 235, 235), font=FONT)
            fx.paste(t_, (x, y + LAB)); x += t_.size[0] + PAD
        y += LAB + HT + PAD
    d.text((PAD + 4, y + 4), "fix strip: %s | %s | SHEET (the views the fix touches; same cameras both builds)" % (PREV, V),
           fill=(220, 220, 210), font=FONT_S)
    out = os.path.join(R, V + "_fix_compare.png")
    fx.save(out)
    print("STRIP", out, fx.size)
    raise SystemExit(0)
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
d.text((PAD + 4, PAD + LAB + H + 6), "ortho build views auto-cropped + scaled to the sheet figure height; build side = her left (faces image-left, as the sheet's side view)",
       fill=(220, 220, 210), font=FONT_S)
out = os.path.join(R, V + "_sheet_compare.png")
strip.save(out)
print("STRIP", out, strip.size)
# ---- the stills sheet
IMP = json.load(open(os.path.join(ROOT, "improved", "lyra.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "lyra.json")))
ROW1 = [("front", "front"), ("threequarter", "3/4 (her right)"), ("side", "side (her left)"), ("back", "back")]
ROW2 = [("head_front", "head front"), ("head_tq", "head 3/4"), ("head_side", "head side"), ("head_back", "head back"),
        ("head_tq_onetone", "one-tone hair 3/4"), ("tail_side", "ponytail side"), ("hold", "book hold"), ("hold_tq", "hold 3/4"),
        ("belt", "corset / belts / vials"), ("satchel", "satchel + scroll case"), ("back_emblem", "capelet emblem"), ("chest", "collar / tie / brooch")]
W1 = 520; cols2 = 6
Wt = 4 * W1 + 5 * PAD
w2 = (Wt - (cols2 + 1) * PAD) // cols2
Hs = PAD + LAB + W1 + PAD + 2 * (LAB + w2 + PAD) + 120
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
    d.text((xx + 4, yy + 2), lab, fill=(235, 235, 235), font=FONT_S)
    sh.paste(tile(v, w2), (xx, yy + LAB))
y += 2 * (LAB + w2 + PAD)
t = IMP["tris"]; hd = RIG["grip"]["books"]["hold"]; gl = RIG.get("glb", {})
lines = ["LYRA %s draft (hero tier)   tris %d (main %d + books %d; budget 30-50k)   height %.3f m (with the ponytail tie), body %.3f m"
         % (V.split("_")[-1], t["total"], t["main"], t["books"], IMP["measure"]["height"], IMP["landmarks"]["height_total"]),
         "book hold (bind pose, right arm): wrist bend %s deg, elbow %s deg, finger pads %s mm, forearm-to-books %s mm, chest/outfit-to-books %s mm"
         % (hd.get("wrist_bend_deg"), hd.get("elbow_flex_deg"), hd.get("fingertip_pad_to_books_mm"), hd.get("forearm_mesh_min_to_books_mm"),
            hd.get("chest_body_outfit_min_to_books_mm")),
         "glow: %s   glb %s B  %s   clips: none (L5)" % (json.dumps(gl.get("glow_audit", {})), gl.get("bytes"), gl.get("sha256_16"))]
for k, ln in enumerate(lines):
    d.text((PAD + 4, y + 6 + 24 * k), ln[:300], fill=(220, 220, 210), font=FONT_S)
out2 = os.path.join(R, V + "_sheet.png")
sh.save(out2)
print("SHEET", out2, sh.size)
