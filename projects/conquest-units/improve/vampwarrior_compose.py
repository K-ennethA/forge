"""Vampire Warrior v3 image assembly (plain Python + PIL, no Blender): run with the forge service venv.

    python -P vampwarrior_compose.py

renders/vampwarrior/ (v1 vampwarrior_* and v2 vampwarrior_v2* stills stay on disk as the 'before'):
  vampwarrior_v3_face_sheet.png      liner contour + brows + fangs + chin: portrait / three-quarter / low / side / eyes
                                     (+ the eyes with the authored regions tinted), with the measured numbers
  vampwarrior_v3_hair.png            the long front strands: front / three-quarter / head front / head three-quarter / back
  vampwarrior_v3_shadow_regions.png  the authored shadow shapes tinted (magenta skin_shadow, cyan bodice_shadow, green brows,
                                     orange liner, blue mouth line) over the same views in the real palette
  vampwarrior_v3_armor.png           breastplate + pauldrons: torso front / torso three-quarter / three-quarter / back
  vampwarrior_v3_red.png             'fainter red': v2 lips | v3 lips (build) | v3 + the fainter-EYE alternative (artist picks)
  vampwarrior_v2_vs_v3.png           v2 (as shipped, toon preview) vs v3: front / three-quarter / face
  vampwarrior_v3_contact.png         every v3 still + dawn + the idle / walk 8-frame sheets
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


lp = IMP["liner_proof"]; br = IMP["brow"]; fg = IMP["fangs"]; ch = IMP["chin"]
dials = IMP["mpfb"]["face_dials_v2_to_v3"]
grid([[("vampwarrior_v3_portrait.png", "v3 face: liner contour, brows,\nfangs from under the lip, sharper chin"),
       ("vampwarrior_v3_portrait_tq.png", "three-quarter"),
       ("vampwarrior_v3_portrait_low.png", "low angle (chin, neck shadow)")],
      [("vampwarrior_v3_eyes.png", "liner: upper %.2f / lower %.2f mm (median)\ncontour closed %.0f%% of angles, max %.2f mm"
        % (lp["upper_lid_width_mm"]["median"], lp["lower_lid_width_mm"]["median"], lp["contour_closed_pct"],
           lp["max_visible_width_any_angle_mm"])),
       ("vampwarrior_v3shadow_eyes.png", "tinted: liner orange, brows green\nbrow slant %.0f deg, %.1f mm above the lid"
        % (br["slant_deg_inner_to_peak"], br["stroke_centre_above_upper_lid_mm_at_eye_centre"])),
       ("vampwarrior_v3_face_side.png", "side: fang %.1f mm below the slit\nchin %.1f mm lower than v2 dials"
        % (fg["L"]["visible_len_mm"], 1000 * (ch["slit_to_chin_m"] - 0.0403)))]],
     520, "Vampwarrior v3 - face: eyeliner traces the lid edge, painted angled brows, fangs from under the upper lip, sharper chin",
     "vampwarrior_v3_face_sheet.png")

grid([[("vampwarrior_v3_front.png", "v3 front: long front strands over the\nshoulders down the chest (solid, split at the ends)"),
       ("vampwarrior_v3_threequarter.png", "three-quarter"), ("vampwarrior_v3_head_front.png", "head, front"),
       ("vampwarrior_v3_head.png", "head, three-quarter"), ("vampwarrior_v3_back.png", "back cascade (v2's, kept)")]],
     420, "Vampwarrior v3 - hair front reworked: long solid strands in front (3 per side), back cascade unchanged",
     "vampwarrior_v3_hair.png")

sh = IMP["shadow_shapes"]["shapes"]
grid([[("vampwarrior_v3shadow_portrait.png", "tinted: fringe %.0f cm2, jaw/neck %.0f cm2" % (sh["fringe"]["area_cm2"], sh["jaw_neck"]["area_cm2"])),
       ("vampwarrior_v3shadow_portrait_tq.png", "three-quarter"), ("vampwarrior_v3shadow_portrait_low.png", "low"),
       ("vampwarrior_v3shadow_torso_front.png", "chest (cyan) %.0f cm2" % sh["chest"]["area_cm2"])],
      [("vampwarrior_v3_portrait.png", "real palette"), ("vampwarrior_v3_portrait_tq.png", "real palette"),
       ("vampwarrior_v3_portrait_low.png", "real palette"), ("vampwarrior_v3_torso_front.png", "real palette")]],
     440, "Vampwarrior v3 - authored shadow shapes (drawn palette regions, crisp edges; top row tinted for reading)",
     "vampwarrior_v3_shadow_regions.png")

grid([[("vampwarrior_v3_torso_front.png", "breastplate (silver edge + keel)\n+ pauldrons (cap + lower lame)"),
       ("vampwarrior_v3_torso.png", "torso three-quarter"), ("vampwarrior_v3_threequarter.png", "three-quarter"),
       ("vampwarrior_v3_back_threequarter.png", "back three-quarter")]],
     460, "Vampwarrior v3 - armour toward the sheet: conforming breastplate + pauldrons, silver-trimmed (artist: accept / adjust?)",
     "vampwarrior_v3_armor.png")

grid([[("vampwarrior_v2_face_front.png", "v2 lips: heavy deep maroon"),
       ("vampwarrior_v3_portrait.png", "v3 BUILD: faint rose lip paint,\nthinner band, dark mouth line"),
       ("vampwarrior_v3alt_fainteyes_portrait.png", "ALTERNATIVE: v3 + fainter red EYES\n(if 'the red' meant the eyes)")],
      [("vampwarrior_v2_face.png", "v2"), ("vampwarrior_v3_eyes.png", "v3 eyes (build)"),
       ("vampwarrior_v3alt_fainteyes_eyes.png", "fainter-eye alternative")]],
     500, "Vampwarrior v3 - 'a fainter red': built as the lip paint; the fainter-eye reading rendered as an alternative",
     "vampwarrior_v3_red.png")

grid([[("vampwarrior_v2_front.png", "v2 front (cel + outline)"), ("vampwarrior_v2_threequarter.png", "v2 three-quarter"),
       ("vampwarrior_v2_face_front.png", "v2 face")],
      [("vampwarrior_v3_front.png", "v3 front (shaded, no outline)"), ("vampwarrior_v3_threequarter.png", "v3 three-quarter"),
       ("vampwarrior_v3_portrait.png", "v3 face")]],
     480, "Vampwarrior v2 vs v3", "vampwarrior_v2_vs_v3.png")

T = 400
items = [("vampwarrior_v3_front.png", "front (idle f1)"), ("vampwarrior_v3_threequarter.png", "three-quarter"),
         ("vampwarrior_v3_side.png", "side"), ("vampwarrior_v3_back.png", "back"),
         ("vampwarrior_v3_tactical.png", "tactical (256 px)"), ("vampwarrior_v3_portrait.png", "face"),
         ("vampwarrior_v3_face_side.png", "face, side"), ("vampwarrior_v3_torso.png", "armour"),
         ("vampwarrior_v3_sword.png", "sword"), ("vampwarrior_v3_hem.png", "cape hem + lining"),
         ("vampwarrior_v3_boots.png", "boots"), ("vampwarrior_v3_dawn_front.png", "dawn skin (swap proof)")]
tiles = [tile(fn, T, lab) for fn, lab in items]
W = 4 * T + 18
sheets = []
for clip, txt in (("idle", "idle: 8 frames / 4 s (eased weight shift, hair + cape follow-through)"),
                  ("walk", "walk: 8 frames / 1.25 s (chest / head overlap, hair + cape lag and settle)")):
    s_ = Image.open(os.path.join(R, "vampwarrior_v3_%s_sheet.png" % clip)).convert("RGBA")
    s_ = s_.resize((W, int(s_.height * W / s_.width)), Image.LANCZOS)
    sheets.append(label(s_, txt))
Hc = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (W, Hc), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 4) * (T + 6), (i // 4) * (T + 6)))
y = 3 * (T + 6)
for s_ in sheets:
    cs.paste(s_, (0, y)); y += s_.height + 6
cs.convert("RGB").save(os.path.join(R, "vampwarrior_v3_contact.png"))
print("WROTE vampwarrior_v3_contact.png")
print("DIALS", json.dumps(dials))
