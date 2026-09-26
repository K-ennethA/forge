"""Vampire Warrior v2 image assembly (plain Python + PIL, no Blender): run with the forge service venv.

    python -P vampwarrior_compose.py

renders/vampwarrior/ (the v1 stills vampwarrior_front / _face / _tactical stay on disk as the 'before'):
  vampwarrior_v2_face_fix.png         lips + liner: v1 face vs v2 face (same camera) + v2 straight-on, with the numbers
  vampwarrior_v2_hair_options.png     hair A / B / C (rows) x head / front / back three-quarter / side; A = build default
  vampwarrior_v2_cel_before_after.png v1 | v2 asset in a stock PBR viewer | v2 + toon preview (= the proposed game shader)
                                      x front / face / tactical
  vampwarrior_v2_outline_near_far.png outline shell off | on x near (three-quarter, face) / far (tactical 256 + 128 px)
  vampwarrior_v2_contact.png          every v2 still + dawn + the idle / walk 8-frame sheets
"""
import json
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "vampwarrior")
LAB = (235, 235, 235, 255)
IMP = json.load(open(os.path.join(ROOT, "improved", "vampwarrior.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "vampwarrior.json")))


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


def tile(fn, T, text=None):
    im = Image.open(os.path.join(R, fn)).convert("RGBA")
    small = im.width <= 256
    im = im.resize((T, T), Image.NEAREST if small else Image.LANCZOS)
    return label(im, text) if text else im


def grid(rows, T, title, out, head_h=46):
    """rows: [[(file, label), ...], ...]"""
    nc = max(len(r) for r in rows)
    W, H = nc * (T + 6), len(rows) * (T + 6) + head_h
    cs = Image.new("RGBA", (W, H), (28, 28, 30, 255))
    d = ImageDraw.Draw(cs)
    d.text((10, 10), title, fill=(255, 255, 255, 255), font=font(22))
    for i, r in enumerate(rows):
        for j, (fn, lab) in enumerate(r):
            if fn is None:
                continue
            cs.paste(tile(fn, T, lab), (j * (T + 6), head_h + i * (T + 6)))
    cs.convert("RGB").save(os.path.join(R, out))
    print("WROTE", out)


lf = IMP["lip_fix"]; lw = IMP["liner_width_mm"]
grid([[("vampwarrior_face.png", "v1: lipstick %.1f mm low\n(on the lower lip + chin)" % (1000 * lf["shift_up_m"])),
       ("vampwarrior_v2_face.png", "v2: lips on the mouth\n(slit z %.4f, moved up %.1f mm)" % (lf["slit_z"], 1000 * lf["shift_up_m"])),
       ("vampwarrior_v2_face_front.png", "v2 straight on: liner %.1f mm\n(v1 %.1f mm median visible width)" % (lw["v2"]["median"], lw["v1"]["median"]))]],
     560, "Vampwarrior v2 - face fixes: lipstick moved up onto the mouth, eye liner thinned to a fine line", "vampwarrior_v2_face_fix.png")

HL = {"A": "A  broad smooth masses (BUILD DEFAULT)", "B": "B  chunkier ribbon locks", "C": "C  v1 locks, wider + thicker (merged)"}
grid([[("vampwarrior_v2_hair%s_%s.png" % (o, v), (HL[o] if k == 0 else v)) for k, v in enumerate(("head", "front", "back_threequarter", "side"))]
      for o in "ABC"], 420, "Vampwarrior v2 - hair options (centre part, over the shoulders in front, waist cascade behind): artist picks A / B / C",
     "vampwarrior_v2_hair_options.png")

grid([[("vampwarrior_front.png", "v1 (realistic bake)"), ("vampwarrior_v2pbr_front.png", "v2 asset, stock PBR viewer"),
       ("vampwarrior_v2_front.png", "v2 + toon preview\n(proposed game shader)")],
      [("vampwarrior_face.png", "v1"), ("vampwarrior_v2pbr_face.png", "v2 asset, stock PBR"), ("vampwarrior_v2_face.png", "v2 + toon preview")],
      [("vampwarrior_tactical.png", "v1 tactical 256 px"), ("vampwarrior_v2pbr_tactical.png", "v2 stock PBR"),
       ("vampwarrior_v2_tactical.png", "v2 + toon preview")]],
     460, "Vampwarrior - cel-shade before / after: flat tone bands in the vertex colours, no gloss, inverted-hull comic line",
     "vampwarrior_v2_cel_before_after.png")

ol = RIG["outline"]
grid([[("vampwarrior_v2noline_threequarter.png", "outline OFF"), ("vampwarrior_v2_threequarter.png", "outline ON: %.1f mm shell" % (1000 * ol["thickness_m"]))],
      [("vampwarrior_v2noline_face.png", "OFF"), ("vampwarrior_v2_face.png", "ON (face x %.1f = %.1f mm)" % (ol["face_k"], 1000 * ol["thickness_m"] * ol["face_k"]))],
      [("vampwarrior_v2noline_tactical.png", "OFF - tactical 256 px"), ("vampwarrior_v2_tactical.png", "ON - tactical 256 px")],
      [("vampwarrior_v2noline_tactical_small.png", "OFF - 128 px"), ("vampwarrior_v2_tactical_small.png", "ON - 128 px")]],
     440, "Vampwarrior v2 - outline near / far (shell %d + %d tris)" % (ol["tris_main_shell"], ol["tris_sword_shell"]),
     "vampwarrior_v2_outline_near_far.png")

T = 400
items = [("vampwarrior_v2_front.png", "front (idle f1, toon preview)"), ("vampwarrior_v2_threequarter.png", "three-quarter"),
         ("vampwarrior_v2_side.png", "side"), ("vampwarrior_v2_back.png", "back"),
         ("vampwarrior_v2_tactical.png", "tactical (256 px)"), ("vampwarrior_v2_face.png", "face"),
         ("vampwarrior_v2_face_side.png", "face, side"), ("vampwarrior_v2_sword.png", "sword"),
         ("vampwarrior_v2_hem.png", "cape hem + lining"), ("vampwarrior_v2_boots.png", "boots"),
         ("vampwarrior_v2_hand.png", "grip hand"), ("vampwarrior_v2_dawn_front.png", "dawn skin (swap proof)")]
tiles = [tile(fn, T, lab) for fn, lab in items]
W = 4 * T + 18
sheets = []
for clip, txt in (("idle", "idle: 8 frames / 4 s (eased weight shift, hair + cape follow-through)"),
                  ("walk", "walk: 8 frames / 1.25 s (chest / head overlap, hair + cape lag and settle)")):
    s_ = Image.open(os.path.join(R, "vampwarrior_v2_%s_sheet.png" % clip)).convert("RGBA")
    s_ = s_.resize((W, int(s_.height * W / s_.width)), Image.LANCZOS)
    sheets.append(label(s_, txt))
Hc = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (W, Hc), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 4) * (T + 6), (i // 4) * (T + 6)))
y = 3 * (T + 6)
for s_ in sheets:
    cs.paste(s_, (0, y)); y += s_.height + 6
cs.convert("RGB").save(os.path.join(R, "vampwarrior_v2_contact.png"))
print("WROTE vampwarrior_v2_contact.png")
