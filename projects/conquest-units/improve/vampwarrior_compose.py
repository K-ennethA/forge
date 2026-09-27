"""Vampire Warrior v4.2 image assembly (v4.2 stills are vampwarrior_v42_*; v4.1 vampwarrior_v41_* / v4 vampwarrior_v4_* stay as the before; (plain Python + PIL, no Blender): run with the forge service venv.

    python -P vampwarrior_compose.py

renders/vampwarrior/ (v1 vampwarrior_*, v2 vampwarrior_v2*, v3 vampwarrior_v3* stills stay on disk as the 'before'; the v3
mouth / brow close-ups for the 1:1 pairs are vampwarrior_v4cmp_v3_*):
  vampwarrior_v42_hairline_vs_annotation.png  v4.2: the artist's hairline annotation | v4.1 | v4.2 at the same scale and
                                        crop | v4.2 with the traced lines + the measured deviations; portrait / three-quarter /
                                        side / back of the head
  vampwarrior_v42_face_vs_reference.png  face reference | v4.1 portrait | v4.2 portrait | v4.2 three-quarter
  vampwarrior_v42_brows.png              reference brows | v3 | v4 (measured widths)
  vampwarrior_v42_fangs.png              the fang annotation | v3 vs v4 at the portrait angle, from below, three-quarter
                                        below and above (+ the multi-view proof numbers)
  vampwarrior_v42_skin_tone.png          v3 | v4 | reference, with the sampled / rendered skin numbers
  vampwarrior_v42_shadow_regions.png     authored shadow shapes tinted (the curtain-edge fringe band) over the real palette
  vampwarrior_v42_contact.png            every v4 still + dawn + the idle / walk 8-frame sheets
"""
import colorsys
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "renders", "vampwarrior")
REF = os.path.join(ROOT, "design", "reference")
LAB = (235, 235, 235, 255)
IMP = json.load(open(os.path.join(ROOT, "improved", "vampwarrior.json")))
RIG = json.load(open(os.path.join(ROOT, "rigged", "vampwarrior.json")))
PAL = json.load(open(os.path.join(ROOT, "palettes", "vampwarrior", "default.json")))


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
    return Image.open(fn if os.path.isabs(fn) else os.path.join(R, fn)).convert("RGBA")


def fit(im, T):
    """letterbox into a T x T tile (keeps the aspect: the references are not square)."""
    k = min(T / im.width, T / im.height)
    im2 = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                    Image.NEAREST if im.width <= 256 else Image.LANCZOS)
    t = Image.new("RGBA", (T, T), (28, 28, 30, 255))
    t.paste(im2, ((T - im2.width) // 2, (T - im2.height) // 2))
    return t


def tile(fn, T, text=None):
    im = fit(load(fn), T)
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


def hsv_patch(fn, box):
    a = np.array(load(fn).convert("RGB")).astype(float)
    m = np.median(a[box[1]:box[3], box[0]:box[2]].reshape(-1, 3), 0)
    h, s, v = colorsys.rgb_to_hsv(*(m / 255.0))
    return m.astype(int).tolist(), round(h * 360.0, 1), round(s, 3), round(v, 3)


FACE_REF = os.path.join(REF, "vampwarrior-v4-face-reference.png")
HAIR_ANN = os.path.join(REF, "vampwarrior-v4-hair-annotation.png")
FANG_ANN = os.path.join(REF, "vampwarrior-v4-fang-annotation.png")
fg = IMP["fangs"]; br = IMP["brow"]; hv = IMP["hair_v4"]


# ---- v4.2 hairline: the annotation | v4.1 | v4.2 at the annotation's scale / crop | v4.2 + traced lines, then 4 head views
HL_ANN = os.path.join(REF, "vampwarrior-v42-hairline-annotation.png")
TRC = json.load(open(os.path.join(R, "vampwarrior_v42_hairline_trace.json")))
HLC = json.load(open(os.path.join(HERE, "log_vampwarrior_hairline.json")))
_rg = TRC["registration"]


def ann_crop(fn):
    """a head_front still at the annotation's scale and crop (the inverse of the registration)."""
    im = load(fn).convert("RGB")
    s_ = _rg["scale"]
    im = im.resize((round(1024 * s_), round(1024 * s_)), Image.LANCZOS)
    x0, y0 = round(_rg["offset_x"]), round(_rg["offset_y"])
    return im.crop((x0, y0, x0 + 388, y0 + 321))


ann_crop("vampwarrior_v41_head_front.png").save(os.path.join(R, "vampwarrior_v42cmp_v41_annframe.png"))
ann_crop("vampwarrior_v42_head_front.png").save(os.path.join(R, "vampwarrior_v42cmp_v42_annframe.png"))
_ov = load("vampwarrior_v42_head_front.png").convert("RGB")
_d = ImageDraw.Draw(_ov)
for nm, col in (("green", (0, 200, 60)), ("orange", (255, 120, 0))):
    _d.line([tuple(p) for p in TRC[nm]], fill=col, width=2)
_ov.crop((340, 160, 700, 460)).save(os.path.join(R, "vampwarrior_v42cmp_traced_lines.png"))
_os, _gs = HLC["orange_summary"], HLC["green_summary_upper_arc"]
grid([[(HL_ANN, "ARTIST: strand to the orange line,\nscalp hair to the green"),
       ("vampwarrior_v42cmp_v41_annframe.png", "v4.1 (same frame)"), ("vampwarrior_v42cmp_v42_annframe.png", "v4.2 (same frame)"),
       ("vampwarrior_v42cmp_traced_lines.png", "v4.2 + the traced lines: strand edge %.1f mm\nmean off orange, outline %.1f mm off green"
        % (_os["mean_abs_mm"], _gs["mean_abs_mm"]))],
      [("vampwarrior_v42_portrait.png", "v4.2 face"), ("vampwarrior_v42_head.png", "v4.2 head three-quarter"),
       ("vampwarrior_v42_face_side.png", "v4.2 side: band over the strand,\nfalls from under it"),
       ("vampwarrior_v42_head_back.png", "v4.2 back (cascade as v4.1)")]],
     440, "Vampwarrior v4.2 - hairline: strand top edge on the orange line, scalp band to the green line",
     "vampwarrior_v42_hairline_vs_annotation.png")

# ---- face vs reference
grid([[(FACE_REF, "REFERENCE (artist)"), ("vampwarrior_v41_portrait.png", "v4.1 (strand from the part)"),
       ("vampwarrior_v42_portrait.png", "v4.2: strand from the orange line,\nscalp band to the green line"),
       ("vampwarrior_v42_portrait_tq.png", "v4.2 three-quarter")]],
     480, "Vampwarrior v4.2 - face vs the reference", "vampwarrior_v42_face_vs_reference.png")
cr = hv["crown"]
# (v4.2: the v4 red-annotation sheet and the eye-red sheet are not re-made -- the hairline sheet supersedes the first,
#  the eyes are unchanged since v4.1; their v41 files stay on disk)

# ---- brows
fr = load(FACE_REF)
fr.crop((95, 45, 215, 105)).save(os.path.join(R, "vampwarrior_v4cmp_ref_brows.png"))
bw = br["width_mm_measured"]
grid([[("vampwarrior_v4cmp_ref_brows.png", "REFERENCE brows: ~0.28 x eye width\nat the inner end, ~0.08 at the tail"),
       ("vampwarrior_v4cmp_v3_brows.png", "v3: %.1f -> %.1f mm" % tuple(br["BROW_W_mm_v3"])),
       ("vampwarrior_v42_brows.png", "v4: %.1f -> %.1f mm (taper ^%.1f)\nmeasured %s" % (br["BROW_W_mm"][0], br["BROW_W_mm"][1],
                                                                                   br["BROW_TAPER"], " / ".join("%.1f" % v for v in bw.values())))]],
     520, "Vampwarrior v4.2 - brows (v4, unchanged) (front-view widths at t = %s)" % ", ".join(k[1:] for k in bw),
     "vampwarrior_v42_brows.png")

# ---- fangs
fa = load(FANG_ANN)
fa.save(os.path.join(R, "vampwarrior_v4cmp_fang_annotation.png"))
L_ = fg["L"]; R_ = fg["R"]
grid([[("vampwarrior_v4cmp_fang_annotation.png", "ARTIST: the circled root\nmust not show"),
       ("vampwarrior_v4cmp_v3_mouth.png", "v3 portrait angle\n(proof: %d / %d violations L / R)" % (L_["v3_placement_violations"], R_["v3_placement_violations"])),
       ("vampwarrior_v4cmp_v3_mouth_low.png", "v3 from below (-30)"), ("vampwarrior_v4cmp_v3_mouth_tq_low.png", "v3 three-quarter below"),
       ("vampwarrior_v4cmp_v3_mouth_high.png", "v3 from above (+25)")],
      [(None, None),
       ("vampwarrior_v42_mouth.png", "v4: root %.1f mm behind the lip front,\n%.1f mm up inside the lip; %d / %d violations"
        % (L_["tuck_behind_lip_front_mm"], L_["root_above_lip_edge_mm"], L_["violations"], R_["violations"])),
       ("vampwarrior_v42_mouth_low.png", "v4 from below (-30)"), ("vampwarrior_v42_mouth_tq_low.png", "v4 three-quarter below"),
       ("vampwarrior_v42_mouth_high.png", "v4 from above (+25)")]],
     400, "Vampwarrior v4.2 - fang roots hidden: proof over %d views (yaw %s x elevation %s), %.1f mm visible below the lip"
     % (L_["proof_views"], "/".join("%d" % v for v in fg["views"]["yaw_deg"]), "/".join("%d" % v for v in fg["views"]["elevation_deg"]),
        L_["visible_len_below_lip_edge_mm"]), "vampwarrior_v42_fangs.png")

# ---- skin tone: the rendered cheek / forehead (same framing v3 / v4) and the reference's lit skin
CHEEK, FORE = (330, 560, 420, 640), (420, 300, 600, 380)
s3c, s4c = hsv_patch("vampwarrior_v3_portrait.png", CHEEK), hsv_patch("vampwarrior_v42_portrait.png", CHEEK)
s3f, s4f = hsv_patch("vampwarrior_v3_portrait.png", FORE), hsv_patch("vampwarrior_v42_portrait.png", FORE)
rr = np.array(Image.open(FACE_REF).convert("RGB")).astype(float)[60:215, 95:210].reshape(-1, 3)
hsv_ = np.array([colorsys.rgb_to_hsv(*(p / 255.0)) for p in rr])
lit = (hsv_[:, 2] > 0.45) & (hsv_[:, 1] < 0.25)
refm = np.median(rr[lit], 0)
rh, rs, rv = colorsys.rgb_to_hsv(*(refm / 255.0))
SKIN_NUM = {"palette_v3": [226, 220, 218], "palette_v4": PAL["regions"]["skin"]["rgb"],
            "rendered_cheek_v3": s3c, "rendered_cheek_v4": s4c, "rendered_forehead_v3": s3f, "rendered_forehead_v4": s4f,
            "reference_lit_median": [refm.astype(int).tolist(), round(rh * 360, 1), round(rs, 3), round(rv, 3), int(lit.sum())]}
json.dump(SKIN_NUM, open(os.path.join(R, "vampwarrior_v42_skin_tone.json"), "w"), indent=1)
T = 480
cs = Image.new("RGBA", (3 * (T + 6), T + 46 + 150), (28, 28, 30, 255))
d = ImageDraw.Draw(cs)
d.text((10, 10), "Vampwarrior v4.2 - skin: v3 (Blender-default grey read) | v4 (pale warm grey-mauve) | reference",
       fill=(255, 255, 255, 255), font=font(22))
for j, (fn, lab, sw, num) in enumerate([
        ("vampwarrior_v3_portrait.png", "v3 palette 226,220,218", (226, 220, 218), s3c),
        ("vampwarrior_v42_portrait.png", "v4 palette %d,%d,%d" % tuple(SKIN_NUM["palette_v4"]), tuple(SKIN_NUM["palette_v4"]), s4c),
        (FACE_REF, "reference (sampled lit skin)", tuple(refm.astype(int)), SKIN_NUM["reference_lit_median"])]):
    cs.paste(tile(fn, T, lab), (j * (T + 6), 46))
    x0 = j * (T + 6)
    d.rectangle([x0 + 10, T + 56, x0 + 110, T + 156], fill=tuple(int(c) for c in sw) + (255,))
    d.text((x0 + 124, T + 60), "rendered cheek rgb %s" % (num[0],), fill=LAB, font=font(15))
    d.text((x0 + 124, T + 84), "hue %.0f deg  sat %.3f  val %.3f" % (num[1], num[2], num[3]), fill=LAB, font=font(15))
    d.text((x0 + 124, T + 108), "(swatch = palette / sample)", fill=LAB, font=font(15))
cs.convert("RGB").save(os.path.join(R, "vampwarrior_v42_skin_tone.png"))
print("WROTE vampwarrior_v42_skin_tone.png", json.dumps(SKIN_NUM))

# ---- shadow regions (tinted)
sh = IMP["shadow_shapes"]["shapes"]
grid([[("vampwarrior_v42shadow_portrait.png", "tinted: curtain-edge fringe %.0f cm2,\njaw/neck %.0f cm2, brows green"
        % (sh["fringe"]["area_cm2"], sh["jaw_neck"]["area_cm2"])),
       ("vampwarrior_v42shadow_portrait_tq.png", "three-quarter"), ("vampwarrior_v42shadow_portrait_low.png", "low"),
       ("vampwarrior_v42shadow_eyes.png", "eyes: liner orange, brows green")],
      [("vampwarrior_v42_portrait.png", "real palette"), ("vampwarrior_v42_portrait_tq.png", "real palette"),
       ("vampwarrior_v42_portrait_low.png", "real palette"), ("vampwarrior_v42_eyes.png", "real palette")]],
     440, "Vampwarrior v4.2 - authored shadow shapes (the fringe band now follows the curtain edge; top row tinted)",
     "vampwarrior_v42_shadow_regions.png")

# ---- contact
T = 400
items = [("vampwarrior_v42_front.png", "front (idle f1)"), ("vampwarrior_v42_threequarter.png", "three-quarter"),
         ("vampwarrior_v42_side.png", "side"), ("vampwarrior_v42_back.png", "back"),
         ("vampwarrior_v42_tactical.png", "tactical (256 px)"), ("vampwarrior_v42_portrait.png", "face"),
         ("vampwarrior_v42_portrait_tq.png", "face, three-quarter"), ("vampwarrior_v42_mouth.png", "fangs"),
         ("vampwarrior_v42_torso.png", "armour"), ("vampwarrior_v42_sword.png", "sword"),
         ("vampwarrior_v42_dawn_front.png", "dawn skin (swap proof)"), ("vampwarrior_v42_dawn_portrait.png", "dawn face")]
tiles = [tile(fn, T, lab) for fn, lab in items]
W = 4 * T + 18
sheets = []
for clip, txt in (("idle", "idle: 8 frames / 4 s (eased weight shift, hair + cape follow-through)"),
                  ("walk", "walk: 8 frames / 1.25 s (chest / head overlap, hair + cape lag and settle)")):
    s_ = load("vampwarrior_v42_%s_sheet.png" % clip)
    s_ = s_.resize((W, int(s_.height * W / s_.width)), Image.LANCZOS)
    sheets.append(label(s_, txt))
Hc = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (W, Hc), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 4) * (T + 6), (i // 4) * (T + 6)))
y = 3 * (T + 6)
for s_ in sheets:
    cs.paste(s_, (0, y)); y += s_.height + 6
cs.convert("RGB").save(os.path.join(R, "vampwarrior_v42_contact.png"))
print("WROTE vampwarrior_v42_contact.png")
