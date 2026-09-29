"""Wren image assembly (plain Python + PIL, no Blender): run with the forge service venv.
v4 (review-log 2026-09-29 "Wren v4 feedback"): every model input / output is v4-prefixed (V = "wren_v4"); the v3 stills
(wren_v3_*.png) are the comparison baseline; + the v4 composites wren_v4_eyes_compare (v3 | v4 iris fit), _face_threeup
(v3 | v4 | sheet), _nose_side_compare (the orbit-blend cleanup), _mouth_compare (the 2D line), _hair (four sides + top),
_hair_layers (the layer shadows, v3 | v4), _winter, _body_untouched, _contact.
v3 (the Fire Emblem round): every model input / output is v3-prefixed (V = "wren_v3"); the v2 stills (wren_v2_*.png) are
the comparison baseline; + the FE-round composites wren_v3_face_threeup (v2 | v3 | sheet), _eyes_compare, _hair (front /
side / back / three-quarter back + the close-up), _hair_shading_ab (the hair strip flat = v2 shading | the smooth-proxy
bake), _winter (the hair tiers repainted through the winter skin), _body_untouched.

    python -P wren_compose.py

renders/wren/ (inputs: wren_<view>.png stills, wren_winter_<view>.png, wren_idle_sheet.png, wren_walk_sheet.png):
  wren_sheet_vs_model.png   the sheet's front / side / back figures | the model's orthographic front / side / back (idle f1)
  wren_face_vs_sheet.png    the sheet's head panel | portrait | three-quarter face | eyes close-up, + the skin numbers
  wren_details_vs_sheet.png the sheet's necklace / bracer / cloak + patch / staff panels | the matching close-ups
  wren_hair.png             hair from the front, side, three-quarter back, back
  wren_contact.png          the main stills, close-ups, the winter skin + the idle / walk 8-frame sheets
  wren_skin_tone.json       sampled lit skin: the sheet vs the render
(v2: all of the above as wren_v2_*)
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
V = "wren_v4"
V3 = "wren_v3"
V2 = "wren_v2"
CROPS = {"eyes": (1178, 152, 1292, 200), "mouth": (1200, 208, 1262, 244), "brows": (1172, 140, 1298, 196),
         "front": (20, 90, 470, 970), "side": (470, 90, 710, 970), "back": (730, 90, 1030, 970),
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
grid([[(crop("front"), "SHEET front"), (V + "_ortho_front.png", "model, orthographic front (idle f1)"),
       (crop("side"), "SHEET side"), (V + "_ortho_side.png", "model, orthographic side"),
       (crop("back"), "SHEET back"), (V + "_ortho_back.png", "model, orthographic back")]],
     300, 580, "Wren - the sheet vs the model (front / side / back)", V + "_sheet_vs_model.png")

# ---- face + skin numbers
sk_sheet_front = lit_skin(SH, (228, 190, 262, 232))
sk_sheet_head = lit_skin(SH, (1190, 200, 1300, 300))
por = load(V + "_portrait.png")
W_, H_ = por.size
sk_render = lit_skin(por, (int(W_ * 0.28), int(H_ * 0.45), int(W_ * 0.72), int(H_ * 0.75)))
SKIN = {"palette_skin_rgb": PAL["regions"]["skin"]["rgb"], "sheet_front_face_lit": sk_sheet_front,
        "sheet_head_panel_lit": sk_sheet_head, "render_portrait_lit": sk_render,
        "rule": "median of lit warm-skin pixels (hue 12-40 deg, sat 0.25-0.65, value > 0.45) in a face box"}
json.dump(SKIN, open(os.path.join(R, V + "_skin_tone.json"), "w"), indent=1)
grid([[(crop("head"), "SHEET head panel\nlit skin %s (hue %.0f, sat %.2f)" % (sk_sheet_head["rgb"], sk_sheet_head["hue_deg"], sk_sheet_head["sat"])),
       (V + "_portrait.png", "model portrait\nlit skin %s (hue %.0f, sat %.2f)" % (sk_render["rgb"], sk_render["hue_deg"], sk_render["sat"])),
       (V + "_face.png", "three-quarter"), (V + "_eyes.png", "eyes: lash band, iris shade, highlight")]],
     420, 460, "Wren - face vs the sheet (palette skin %s, sampled off the sheet's lit face)" % (PAL["regions"]["skin"]["rgb"],),
     V + "_face_vs_sheet.png")

# ---- details
grid([[(crop("necklace"), "SHEET necklace"), (V + "_necklace.png", "pendant + cord + clasp"),
       (crop("bracer"), "SHEET bracer"), (V + "_bracer.png", "bracer (left forearm), teal diamond")],
      [(crop("cloak"), "SHEET cloak + brass boss"), (V + "_cloak.png", "cloak from behind: hood, patches, ragged hem"),
       (crop("patch"), "SHEET cloth patch"), (V + "_patches.png", "patches + cross stitches")],
      [(crop("staff"), "SHEET staff"), (V + "_fork_head.png", "pitchfork head (rest pose): tines, brass collar, wrap"),
       (crop("chips"), "SHEET palette chips"), (V + "_fork_full.png", "pitchfork, full (idle f1, planted)")]],
     360, 360, "Wren - detail panels vs the model", V + "_details_vs_sheet.png")

# ---- hair (v4: the side part + layer shadows)
HI = IMP["hair"]
LS = HI.get("layer_shadows", {})
grid([[(V + "_head_front.png", "front"), (V + "_head_side.png", "side (his left: the part side)"),
       (V + "_head_tq_back.png", "three-quarter back"), (V + "_head_back.png", "back: nape tail + zigzag points")],
      [(V + "_hair_close.png", "close-up: layer shadows + broken angel ring"), (V + "_head_top.png", "from above: the side part"),
       (V + "_hair_part.png", "over the part (his left, above)"), (V + "_hair_sweep.png", "the sweep side (his right)")]],
     420, 420, "Wren v4 - hair: side part on his left, %d clumps; layer shadows %s faces / %.1f cm2 (%.0f%% of the clump tops), "
     "%d caster->receiver pairs; crown %.1f mm above the scalp" % (HI["clumps"], LS.get("faces"), LS.get("area_cm2", 0.0),
                                                                  LS.get("share_of_clump_top_area_pct", 0.0), LS.get("pairs", 0),
                                                                  HI["crown"]["crown_above_scalp_mm"]), V + "_hair.png")
grid([[(V3 + "_hair_close.png", "v3 close-up (dome with lines)"), (V + "_hair_close.png", "v4 close-up (layer shadows)")],
      [(V3 + "_hair_sweep.png", "v3 from his right"), (V + "_hair_sweep.png", "v4 from his right (the sweep)")],
      [(V3 + "_hair_part.png", "v3 from above-left (centre part)"), (V + "_hair_part.png", "v4 from above-left (the side part)")],
      [(V3 + "_hair_back_close.png", "v3 back"), (V + "_hair_back_close.png", "v4 back")]],
     560, 460, "Wren v4 - hair layers: v3 | v4 (same views, same light, pitchfork hidden): each clump outline traced as a crevice band onto the "
     "hair beneath it", V + "_hair_layers.png")
grid([[(V + "_winter_front.png", "winter front"), (V + "_winter_threequarter.png", "winter three-quarter"),
       (V + "_winter_back.png", "winter back")],
      [(V + "_winter_portrait.png", "winter portrait"), (V + "_winter_hair_close.png", "winter: hair tiers + layer shadows"),
       (V + "_winter_head_tq_back.png", "winter: hair three-quarter back")]],
     420, 460, "Wren v4 - the winter skin (a pure palette swap: the hair tiers, layer shadows + eye regions repaint through it)",
     V + "_winter.png")

# ---- contact
T = 400
items = [(V + "_front.png", "front (idle f1)"), (V + "_threequarter.png", "three-quarter"), (V + "_side.png", "side"),
         (V + "_back.png", "back"), (V + "_tactical.png", "tactical (256 px)"), (V + "_portrait.png", "face"),
         (V + "_eye_close.png", "eye: smaller iris in the approved socket"), (V + "_hair_close.png", "hair: layer shadows"),
         (V + "_mouth.png", "mouth: the 2D smirk line"), (V + "_head_top.png", "hair from above: side part"),
         (V + "_winter_front.png", "winter skin (swap proof)"), (V + "_winter_hair_close.png", "winter: hair tiers")]
tiles = [label(fit(load(fn), T, T), lab) for fn, lab in items]
Wc = 4 * T + 18
sheets = []
for clip, txt in (("idle", "idle: 8 frames / 4 s (leaning on the planted fork, weight shift, cloak / fringe follow-through)"),
                  ("walk", "walk: 8 frames / %.2f s, %.0f steps/min (bounce, the fork carried, cloak / tail / ties lag)"
                   % (RIG["clips"]["walk"]["seconds"], RIG["clips"]["walk"]["cadence_steps_per_min"]))):
    s_ = load(V + "_%s_sheet.png" % clip)
    s_ = s_.resize((Wc, int(s_.height * Wc / s_.width)), Image.LANCZOS)
    sheets.append(label(s_, txt))
Hc = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (Wc, Hc), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 4) * (T + 6), (i // 4) * (T + 6)))
y = 3 * (T + 6)
for s_ in sheets:
    cs.paste(s_, (0, y)); y += s_.height + 6
cs.convert("RGB").save(os.path.join(R, V + "_contact.png"))
print("WROTE", V + "_contact.png", json.dumps(SKIN))

# ---- v4 round: v3 | v4 (| the sheet)
V4R = IMP.get("v4_round", {})
eyes = V4R.get("eyes", {})
grid([[(V3 + "_portrait.png", "v3 portrait"), (V + "_portrait.png", "v4 portrait"), (crop("head"), "SHEET head panel")]],
     460, 500, "Wren v4 - v3 | v4 | the sheet (iris to the socket, no eye bags, orbit cleanup, 2D mouth, side-part hair)",
     V + "_face_threeup.png")
cov3, cov4 = eyes.get("iris_coverage_pct_v3", {}), eyes.get("iris_coverage_pct_v4", {})
grid([[(V3 + "_eyes_v2frame.png", "v3: iris %s%% of the opening" % cov3.get("L")),
       (V + "_eyes_v2frame.png", "v4: iris %s%% of the opening (socket unchanged)" % cov4.get("L")), (crop("eyes"), "SHEET eyes")],
      [(V3 + "_eye_close.png", "v3 his left eye (iris %s deg)" % eyes.get("iris_deg_v3")),
       (V + "_eye_close.png", "v4 his left eye (iris %s deg, pupil %s deg)" % (eyes.get("iris_deg_v4"), eyes.get("pupil_deg_v4"))),
       (None, None)]],
     520, 380, "Wren v4 - eyes: the x1.30 socket kept, the iris shrunk to fit it (same framing v3 | v4)", V + "_eyes_compare.png")
SD = V4R.get("orbit", {}).get("shape_dev_mm_vs_v2_surface", {})
SD3 = V4R.get("orbit", {}).get("v3_shape_dev_mm_vs_v2_surface", {})
lab = lambda k: "%s max %s mm (v3 %s)" % (k, SD.get(k, {}).get("max"), SD3.get(k, {}).get("max"))
grid([[(V3 + "_nose.png", "v3 nose"), (V + "_nose.png", "v4 nose: " + lab("nose"))],
      [(V3 + "_nose_tq.png", "v3 nose three-quarter"), (V + "_nose_tq.png", "v4 nose three-quarter: " + lab("nose_bridge"))],
      [(V3 + "_face_side.png", "v3 face 70 deg"), (V + "_face_side.png", "v4 face 70 deg: " + lab("side"))],
      [(V3 + "_face_side90.png", "v3 profile"), (V + "_face_side90.png", "v4 profile: " + lab("cheek"))]],
     560, 460, "Wren v4 - orbit-blend cleanup: shape deviation from the v2 surface outside the socket core (v4 | v3)",
     V + "_nose_side_compare.png")
LF = V4R.get("mouth", {})
grid([[(V3 + "_mouth.png", "v3 mouth (paired lip volumes + tint)"), (V + "_mouth.png", "v4 mouth: the drawn smirk line"),
       (crop("mouth"), "SHEET mouth")],
      [(V3 + "_mouth_tq.png", "v3 three-quarter"), (V + "_mouth_tq.png", "v4 three-quarter"), (None, None)],
      [(V3 + "_mouth_side.png", "v3 profile"), (V + "_mouth_side.png", "v4 profile: lip relief %s -> %s mm" % (
          (LF.get("relief_vs_fit_mm") or {}).get("before_max"), (LF.get("relief_vs_fit_mm") or {}).get("after_max"))), (None, None)]],
     460, 380, "Wren v4 - mouth: 2D anime (thin barely-there lips, the drawn line, no tint)", V + "_mouth_compare.png")
grid([[(V3 + "_front.png", "v3 front (idle f1)"), (V + "_front.png", "v4 front (idle f1)"),
       (V3 + "_threequarter.png", "v3 three-quarter"), (V + "_threequarter.png", "v4 three-quarter")]],
     360, 520, "Wren v4 - below the neck untouched", V + "_body_untouched.png")
