"""ember_stump image assembly (scaffolded from firefly_compose.py; plain Python + PIL, no Blender): run with the forge service venv.

    python -P ember_stump_compose.py

renders/ember_stump/ember_stump_sheet_vs_model.png
    one row per TRUE-ortho sheet view (intake.json ortho_frame.views); columns: the artist's sheet view (cropped by design/reference/ember_stump/intake.json 'ortho_frame', at
    the sheet's own scale) | the build, orthographic at the SAME scale and floor line | overlay: the sheet with the build's
    silhouette outline (cyan) | one column per extra skin at the same framing. Proportions compare 1:1.
renders/ember_stump/ember_stump_contact.png
    every still that exists (views, close-ups, skin fronts) + every clip contact sheet, labelled.
"""
import glob, json, os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

UNIT = "ember_stump"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", UNIT)
INTAKE = json.load(open(os.path.join(ROOT, "design", "reference", UNIT, "intake.json")))
SHEET = os.path.join(ROOT, INTAKE["sheet"])
OF = INTAKE["ortho_frame"]
BG = (42, 42, 42, 255)
LAB = (235, 235, 235, 255)
SKINS = sorted({os.path.basename(p)[len(UNIT) + 1:-len("_ortho_front.png")]
                for p in glob.glob(os.path.join(R, UNIT + "_*_ortho_front.png"))})


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
W, H = OF["width_px"], OF["height_px"]
rows = []
for view in OF.get("views", ["front", "side"]):          # only views the sheet draws as TRUE orthos
    p = os.path.join(R, "%s_ortho_%s.png" % (UNIT, view))
    if view not in OF["crops"] or not os.path.exists(p):
        continue
    crop = OF["crops"][view]
    ref = sh.crop(tuple(crop))
    # blank whatever of the neighbouring views / detail panels the shared-size box picks up (only this view's own
    # bbox + a small margin stays): a crop centred on an off-centre axis otherwise shows slices of the next view
    vb = INTAKE["views"][view]["bbox_px"]
    keep = Image.new("L", ref.size, 0)
    ImageDraw.Draw(keep).rectangle([vb[0] - crop[0] - 12, vb[1] - crop[1] - 12, vb[2] - crop[0] + 12, vb[3] - crop[1] + 12], fill=255)
    ref = Image.composite(ref, Image.new("RGBA", ref.size, BG), keep)
    mod, a = over_bg(p)
    ov = ref.copy()
    px = np.array(ov)
    px[outline(a)] = (0, 230, 255, 255)
    ov = Image.fromarray(px)
    tiles = [label(ref, "SHEET %s" % view.upper()), label(mod, "BUILD %s" % view), label(ov, "SHEET + build outline")]
    for s in SKINS:
        q = os.path.join(R, "%s_%s_ortho_%s.png" % (UNIT, s, view))
        if os.path.exists(q):
            tiles.append(label(over_bg(q)[0], "BUILD %s (%s skin)" % (view, s)))
    row = Image.new("RGBA", (len(tiles) * (W + 6) - 6, H), (255, 255, 255, 255))
    for i, t in enumerate(tiles):
        row.paste(t, (i * (W + 6), 0))
    rows.append(row)
if rows:
    out = Image.new("RGBA", (max(r.width for r in rows), len(rows) * (H + 6) - 6), (255, 255, 255, 255))
    for i, r in enumerate(rows):
        out.paste(r, (0, i * (H + 6)))
    out.convert("RGB").save(os.path.join(R, UNIT + "_sheet_vs_model.png"))
    print("WROTE", os.path.join(R, UNIT + "_sheet_vs_model.png"))

T = 400
fixed = ["front", "threequarter", "side", "back", "tactical"]
stills = [(UNIT + "_%s.png" % v, v) for v in fixed]
known = {fn for fn, _ in stills}
for p in sorted(glob.glob(os.path.join(R, UNIT + "_*.png"))):
    fn = os.path.basename(p)
    tag = fn[len(UNIT) + 1:-4]
    if fn in known or "ortho" in tag or "sheet" in tag or tag == "contact" or tag.startswith("_"):
        continue
    stills.append((fn, tag))
tiles = []
for fn, lab in stills:
    p = os.path.join(R, fn)
    if os.path.exists(p):
        im = Image.open(p).convert("RGBA").resize((T, T), Image.NEAREST if lab == "tactical" else Image.LANCZOS)
        tiles.append(label(im, lab))
cols = 3
Wc = cols * T + (cols - 1) * 6
strips = []
for p in sorted(glob.glob(os.path.join(R, UNIT + "_*_sheet.png"))):
    if p.endswith("_vs_model.png"):
        continue
    im = Image.open(p).convert("RGBA")
    im = im.resize((Wc, int(im.height * Wc / im.width)), Image.LANCZOS)
    strips.append(label(im, "CLIP " + os.path.basename(p)[len(UNIT) + 1:-4]))
nrow = (len(tiles) + cols - 1) // cols
Hc = nrow * (T + 6) + sum(s.height + 6 for s in strips)
cs = Image.new("RGBA", (Wc, max(Hc, 1)), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % cols) * (T + 6), (i // cols) * (T + 6)))
y = nrow * (T + 6)
for s in strips:
    cs.paste(s, (0, y)); y += s.height + 6
cs.convert("RGB").save(os.path.join(R, UNIT + "_contact.png"))
print("WROTE", os.path.join(R, UNIT + "_contact.png"))
