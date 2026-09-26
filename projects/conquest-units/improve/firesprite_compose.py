"""Fire Sprite image assembly (plain Python + PIL, no Blender): run with the forge service venv.

    python -P firesprite_compose.py

renders/firesprite/firesprite_sheet_vs_model.png
    rows FRONT / SIDE; columns: the artist's sheet view (cropped at its own scale: 100 px per unit, the foot line y 605)
    | the build, orthographic at the SAME scale | overlay: the sheet with the build's silhouette outline (cyan) | the
    'soulfire' skin at the same framing. Proportions compare 1:1 (the body is squarer by the artist's deviation).
renders/firesprite/firesprite_sketch_vs_model.png
    the artist's sketch (the square one-piece block, jagged hole eyes, zigzag mouth) | the build front | the build face
    close-up. The sketch is a phone photo of paper (perspective), so this is side-by-side, not an overlay.
renders/firesprite/firesprite_contact.png
    every still: front / threequarter / side / back / tactical / face / crown / wand / soulfire front + the idle 8-frame
    sheet + the walk side-view sheet, labelled.
renders/firesprite/firesprite_walk_side_sheet.png
    labelled in place: every tile's frame + its MEASURED contact state (rigged/firesprite.json walk contact_frames).
renders/firesprite/firesprite_v11_vs_v12.png
    v1.1 (kept copies firesprite_{front,side}_v1_1.png) vs v1.2 front + side: the torso-leg blend and the arm outline.
"""
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "firesprite"))
REF = os.path.normpath(os.path.join(HERE, "..", "design", "reference"))
SHEET = os.path.join(REF, "firesprite-character-sheet.webp")
SKETCH = os.path.join(REF, "firesprite-sketch.webp")
BG = (34, 34, 34, 255)
LAB = (235, 235, 235, 255)
W_, H_ = 400, 485


def over_bg(path, bg=BG):
    im = Image.open(path).convert("RGBA")
    b = Image.new("RGBA", im.size, bg)
    b.alpha_composite(im)
    return b, np.array(im)[:, :, 3]


def outline(alpha):
    m = Image.fromarray(((alpha > 40) * 255).astype(np.uint8))
    e = np.array(m.filter(ImageFilter.MaxFilter(3))).astype(int) - np.array(m.filter(ImageFilter.MinFilter(3))).astype(int)
    return e > 0


def label(im, text, xy=(8, 6)):
    d = ImageDraw.Draw(im)
    d.rectangle([xy[0] - 4, xy[1] - 3, xy[0] + 7 * len(text) + 6, xy[1] + 13], fill=(0, 0, 0, 170))
    d.text(xy, text, fill=LAB)
    return im


sh = Image.open(SHEET).convert("RGBA")
crops = {"front": (25, 140, 425, 625), "side": (715, 140, 1115, 625)}   # centred on the character axis (x 225 / 915 px)
rows = []
for view in ("front", "side"):
    ref = sh.crop(crops[view])
    mod, a = over_bg(os.path.join(R, "firesprite_ortho_%s.png" % view))
    var, _ = over_bg(os.path.join(R, "firesprite_soulfire_ortho_%s.png" % view))
    ov = ref.copy()
    px = np.array(ov)
    px[outline(a)] = (0, 230, 255, 255)
    ov = Image.fromarray(px)
    tiles = [label(ref, "SHEET %s" % view.upper()), label(mod, "BUILD %s (default)" % view),
             label(ov, "SHEET + build outline"), label(var, "BUILD %s (soulfire)" % view)]
    row = Image.new("RGBA", (4 * W_ + 3 * 6, H_), (255, 255, 255, 255))
    for i, t in enumerate(tiles):
        row.paste(t, (i * (W_ + 6), 0))
    rows.append(row)
out = Image.new("RGBA", (rows[0].width, 2 * H_ + 6), (255, 255, 255, 255))
out.paste(rows[0], (0, 0)); out.paste(rows[1], (0, H_ + 6))
out.convert("RGB").save(os.path.join(R, "firesprite_sheet_vs_model.png"))
print("WROTE", os.path.join(R, "firesprite_sheet_vs_model.png"))

# sketch vs model
HS = 820
sk = Image.open(SKETCH).convert("RGBA").crop((360, 270, 1040, 1460))
sk = sk.resize((int(sk.width * HS / sk.height), HS), Image.LANCZOS)
fr = Image.open(os.path.join(R, "firesprite_front.png")).convert("RGBA").resize((HS, HS), Image.LANCZOS)
fc = Image.open(os.path.join(R, "firesprite_face.png")).convert("RGBA").resize((HS, HS), Image.LANCZOS)
tiles = [label(sk, "ARTIST SKETCH (photo)"), label(fr, "BUILD front"), label(fc, "BUILD face: eye + mouth holes")]
sv = Image.new("RGBA", (sum(t.width for t in tiles) + 12, HS), (255, 255, 255, 255))
x = 0
for t in tiles:
    sv.paste(t, (x, 0)); x += t.width + 6
sv.convert("RGB").save(os.path.join(R, "firesprite_sketch_vs_model.png"))
print("WROTE", os.path.join(R, "firesprite_sketch_vs_model.png"))

T = 400
items = [("firesprite_front.png", "front"), ("firesprite_threequarter.png", "three-quarter (wand side)"),
         ("firesprite_side.png", "side"), ("firesprite_back.png", "back"), ("firesprite_tactical.png", "tactical (256 px)"),
         ("firesprite_face.png", "face: eye + mouth holes"), ("firesprite_crown.png", "crown (open top, flame)"),
         ("firesprite_wand.png", "wand + wand flame"), ("firesprite_soulfire_front.png", "soulfire skin (variant)")]
tiles = []
for fn, lab in items:
    p = os.path.join(R, fn)
    im = Image.open(p).convert("RGBA").resize((T, T), Image.LANCZOS if not fn.endswith("tactical.png") else Image.NEAREST)
    tiles.append(label(im, lab))
W = 3 * T + 12
# walk side sheet: label every tile with its frame + the MEASURED contact state (rigged/firesprite.json)
rep = json.load(open(os.path.join(HERE, "..", "rigged", "firesprite.json")))
walk = rep["clips"]["walk"]
ws_path = os.path.join(R, "firesprite_walk_side_sheet.png")
ws_meta = json.load(open(ws_path[:-4] + ".json"))
ws = Image.open(ws_path).convert("RGBA")
tp, cols = ws_meta["tile_px"], ws_meta["cols"]
for k, f in enumerate(ws_meta["frames"]):
    fl = (f - 1) % ws_meta["loop_frames"] + 1
    st = [s + " down" for s in ("L", "R") if fl in walk["contact_frames"][s]] or ["AIR (drift)"]
    label(ws, "f%d  %s" % (f, " + ".join(st)), ((k % cols) * tp + 8, (k // cols) * tp + 6))
ws.convert("RGB").save(ws_path)
print("WROTE", ws_path)
sheets = []
for fn, lab in (("firesprite_idle_sheet.png", "IDLE: 8 frames over the 4 s loop (grounded, living fire)"),
                ("firesprite_walk_side_sheet.png", "WALK side view: floaty steps, 12 frames over the 2 s / 2-step loop "
                 "(%.0f steps/min, hang %.2f s/step)" % (walk["cadence_steps_per_min"], walk["hang"]["hang_time_per_step_s"]))):
    im = Image.open(os.path.join(R, fn)).convert("RGBA")
    im = im.resize((W, int(im.height * W / im.width)), Image.LANCZOS)
    sheets.append(label(im, lab, (8, im.height - 20)))
H = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (W, H), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 3) * (T + 6), (i // 3) * (T + 6)))
y = 3 * (T + 6)
for s in sheets:
    cs.paste(s, (0, y)); y += s.height + 6
cs.convert("RGB").save(os.path.join(R, "firesprite_contact.png"))
print("WROTE", os.path.join(R, "firesprite_contact.png"))

# v1.1 -> v1.2 body (artist 2026-09-26: torso-leg blend, arms wider / longer to the drawn outline)
ba = []
for fn, lab in (("firesprite_front_v1_1.png", "v1.1 front"), ("firesprite_front.png", "v1.2 front"),
                ("firesprite_side_v1_1.png", "v1.1 side"), ("firesprite_side.png", "v1.2 side")):
    p = os.path.join(R, fn)
    if os.path.exists(p):
        ba.append(label(Image.open(p).convert("RGBA").resize((512, 512), Image.LANCZOS), lab))
if ba:
    out = Image.new("RGBA", (len(ba) * 518 - 6, 512), (255, 255, 255, 255))
    for i, t in enumerate(ba):
        out.paste(t, (i * 518, 0))
    out.convert("RGB").save(os.path.join(R, "firesprite_v11_vs_v12.png"))
    print("WROTE", os.path.join(R, "firesprite_v11_vs_v12.png"))
