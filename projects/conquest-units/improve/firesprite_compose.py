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
    every still: front / threequarter / side / back / tactical / face / crown / wand / soulfire front + the placeholder idle
    8-frame sheet, labelled.
"""
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
idle = Image.open(os.path.join(R, "firesprite_idle_sheet.png")).convert("RGBA")
idle = idle.resize((3 * T + 12, int(idle.height * (3 * T + 12) / idle.width)), Image.LANCZOS)
label(idle, "PLACEHOLDER idle: 8 frames over the 2 s loop (flame waver + crown / wand flame flicker)")
W = 3 * T + 12
H = 3 * (T + 6) + idle.height
cs = Image.new("RGBA", (W, H), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 3) * (T + 6), (i // 3) * (T + 6)))
cs.paste(idle, (0, 3 * (T + 6)))
cs.convert("RGB").save(os.path.join(R, "firesprite_contact.png"))
print("WROTE", os.path.join(R, "firesprite_contact.png"))
