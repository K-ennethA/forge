"""Firefly image assembly (plain Python + PIL, no Blender): run with the forge service venv.

    python -P firefly_compose.py

renders/firefly/firefly_sheet_vs_model.png
    rows FRONT / SIDE; columns: the artist's sheet view (cropped at its own scale) | the build, orthographic at the SAME
    scale (100 px per unit, floor = the sheet's flame-tip line) | overlay: the sheet with the build's silhouette outline
    (cyan) | the 'ember' skin (the sheet's own amber) at the same framing. Proportions compare 1:1.
renders/firefly/firefly_contact.png
    every still: front / threequarter / side / back / tactical / head / port / flame / ember front + the idle 8-frame sheet
    + the walk (hover-drift) side-view 12-frame sheet, labelled.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "firefly"))
SHEET = os.path.normpath(os.path.join(HERE, "..", "design", "reference", "firefly-character-sheet.webp"))
BG = (42, 42, 42, 255)
LAB = (235, 235, 235, 255)


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
crops = {"front": (20, 45, 420, 865), "side": (700, 45, 1100, 865)}   # both centred on the torso axis (x 220 / 900 px)
rows = []
for view in ("front", "side"):
    ref = sh.crop(crops[view])
    mod, a = over_bg(os.path.join(R, "firefly_ortho_%s.png" % view))
    emb, _ = over_bg(os.path.join(R, "firefly_ember_ortho_%s.png" % view))
    ov = ref.copy()
    px = np.array(ov)
    px[outline(a)] = (0, 230, 255, 255)
    ov = Image.fromarray(px)
    tiles = [label(ref, "SHEET %s" % view.upper()), label(mod, "BUILD %s (firefly glow)" % view),
             label(ov, "SHEET + build outline"), label(emb, "BUILD %s (ember = sheet amber)" % view)]
    row = Image.new("RGBA", (4 * 400 + 3 * 6, 820), (255, 255, 255, 255))
    for i, t in enumerate(tiles):
        row.paste(t, (i * 406, 0))
    rows.append(row)
out = Image.new("RGBA", (rows[0].width, 2 * 820 + 6), (255, 255, 255, 255))
out.paste(rows[0], (0, 0)); out.paste(rows[1], (0, 826))
out.convert("RGB").save(os.path.join(R, "firefly_sheet_vs_model.png"))
print("WROTE", os.path.join(R, "firefly_sheet_vs_model.png"))

T = 400
items = [("firefly_front.png", "front (idle hover, frame 1)"), ("firefly_threequarter.png", "three-quarter"),
         ("firefly_side.png", "side"), ("firefly_back.png", "back"), ("firefly_tactical.png", "tactical (256 px)"),
         ("firefly_head.png", "head / mask"), ("firefly_port.png", "side ports + smoke roots"),
         ("firefly_flame.png", "flame"), ("firefly_ember_front.png", "ember skin (sheet amber)")]
tiles = []
for fn, lab in items:
    p = os.path.join(R, fn)
    im = Image.open(p).convert("RGBA").resize((T, T), Image.LANCZOS if not fn.endswith("tactical.png") else Image.NEAREST)
    tiles.append(label(im, lab))
W = 3 * T + 12
strips = []
for fn, lab in (("firefly_idle_sheet.png", "IDLE hover: 8 frames over the 4 s loop (bob + pitch/roll wander, thrust-pulsed "
                                           "flame, billowing smoke)"),
                ("firefly_walk_side_sheet.png", "WALK hover-drift, SIDE view: 12 frames over the 2 s loop (12 deg lean, "
                                                "flame + smoke streaming back; front = image left)")):
    im = Image.open(os.path.join(R, fn)).convert("RGBA")
    im = im.resize((W, int(im.height * W / im.width)), Image.LANCZOS)
    strips.append(label(im, lab))
H = 3 * (T + 6) + sum(s.height + 6 for s in strips)
cs = Image.new("RGBA", (W, H), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 3) * (T + 6), (i // 3) * (T + 6)))
y = 3 * (T + 6)
for s in strips:
    cs.paste(s, (0, y)); y += s.height + 6
cs.convert("RGB").save(os.path.join(R, "firefly_contact.png"))
print("WROTE", os.path.join(R, "firefly_contact.png"))
