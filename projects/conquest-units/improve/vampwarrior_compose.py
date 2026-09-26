"""Vampire Warrior image assembly (plain Python + PIL, no Blender): run with the forge service venv.

    python -P vampwarrior_compose.py

renders/vampwarrior/vampwarrior_sheet_fidelity.png
    orthographic FRONT / SIDE of the build (idle frame 1, the sheet-style standing pose, sword point-down) beside the
    sheet-fidelity notes: what matches the transcription item by item, and every simplification / lane call. The sheet
    image itself is not on disk (design/review-log.md 2026-09-26 carries its transcription), so there is no overlay.
renders/vampwarrior/vampwarrior_contact.png
    every still (front / threequarter / side / back / tactical / face / face side / sword / torn hem / boots / grip hand /
    dawn skin) + the idle and walk 8-frame sheets, labelled.
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.normpath(os.path.join(HERE, "..", "renders", "vampwarrior"))
BG = (42, 42, 42, 255)
LAB = (235, 235, 235, 255)


def font(sz):
    for f in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def over_bg(path, bg=BG):
    im = Image.open(path).convert("RGBA")
    b = Image.new("RGBA", im.size, bg)
    b.alpha_composite(im)
    return b


def label(im, text, xy=(8, 6), sz=15):
    d = ImageDraw.Draw(im)
    f = font(sz)
    w = d.textlength(text, font=f)
    d.rectangle([xy[0] - 4, xy[1] - 3, xy[0] + w + 6, xy[1] + sz + 4], fill=(0, 0, 0, 170))
    d.text(xy, text, fill=LAB, font=f)
    return im


NOTES = [
    ("SHEET-FIDELITY NOTES", None),
    ("(sheet image not on disk: checked against the transcription, design/review-log.md 2026-09-26)", "dim"),
    ("MATCHES", "head"),
    ("Tall female, intimidating: 1.83 m standing (1.78 barefoot + heels), idealised long-legged MPFB2 body; chin-tucked glare in the idle.", None),
    ("Pale skin; RED eyes (glowing iris, dark pupil) with a dark maroon lid liner; FANGS under the upper lip; deep-maroon lips.", None),
    ("Very long bone-white hair: centre part; 3 locks a side drawn over the shoulders to the bust / waist in front; a 9-lock cascade to the waist behind. Sculpted lens-section masses, not cards.", None),
    ("Black high-collar armoured bodice: a standing collar (silver rim, open at the throat), silver seam trim (princess + back seams, underbust band, armholes); red throat gem in a silver setting.", None),
    ("Long TORN black cape, dark-red lining, ragged ankle hem (irregular rips + 3 slits), hanging folds.", None),
    ("Long black gloves to above the elbow (silver cuffs).", None),
    ("Black thigh-high heeled boots: stiletto heels, pointed toe caps, domed armoured knee cops; BARE upper thighs between boot top and fauld.", None),
    ("Skirt fauld: 8 hanging plates with silver hems (the front one pointed).", None),
    ("Sword nearly her height (1.53 m = 0.82 x): leaf / flame blade widest at 42 %, 15 serrations per edge pointing to the tip, dark centre vein, simple black grip, red tassel at the guard. Own object + bone.", None),
    ("Palette chips: bone-white, pale grey, near-black, dark red, deep maroon, black.", None),
    ("SIMPLIFICATIONS / LANE CALLS", "head"),
    ("Bodice, gloves and boot shafts are PAINTED on the skin (form-fitting garments, clean iso-cut edges); armour relief is geometry: collar, knee cops, fauld plates, cuffs, belt.", None),
    ("Shoulders + upper arms left bare between the armhole and the glove top (the transcription is silent on sleeves).", None),
    ("A belt + buckle added to hang the fauld; guard / pommel coloured deep maroon; the collar opens at the front for the gem.", None),
    ("Bind (rest) pose: the sword stands point-down beside the right hand; the clips hold it (planted in the idle, trailing in the walk).", None),
    ("No painted brows; ears left human (neither is in the transcription).", None),
]


def notes_panel(w, h):
    im = Image.new("RGBA", (w, h), (30, 30, 32, 255))
    d = ImageDraw.Draw(im)
    y = 16
    for text, kind in NOTES:
        sz = 22 if kind is None and text.startswith("SHEET") else (18 if kind == "head" else 15)
        col = (255, 120, 120, 255) if kind == "head" else ((160, 160, 160, 255) if kind == "dim" else LAB)
        if text.startswith("SHEET"):
            sz, col = 24, (255, 255, 255, 255)
        f = font(sz)
        words, line = text.split(), ""
        bullet = "" if kind in ("head", "dim") or text.startswith("SHEET") else "- "
        lines = []
        for wd in words:
            t = (line + " " + wd).strip()
            if d.textlength(bullet + t, font=f) > w - 40:
                lines.append(line); line = wd
            else:
                line = t
        lines.append(line)
        for i, ln in enumerate(lines):
            d.text((20 if i == 0 else 20 + d.textlength(bullet, font=f), y), (bullet if i == 0 else "") + ln, fill=col, font=f)
            y += sz + 5
        y += 8 if kind == "head" or text.startswith("SHEET") else 4
    return im


fr = over_bg(os.path.join(R, "vampwarrior_ortho_front.png"))
sd = over_bg(os.path.join(R, "vampwarrior_ortho_side.png"))
H = max(fr.height, sd.height)
panel = notes_panel(900, H)
out = Image.new("RGBA", (fr.width + sd.width + panel.width + 12, H), (255, 255, 255, 255))
out.paste(label(fr, "BUILD front (ortho, idle f1)"), (0, 0)); out.paste(label(sd, "BUILD side (ortho, idle f1)"), (fr.width + 6, 0))
out.paste(panel, (fr.width + sd.width + 12, 0))
out.convert("RGB").save(os.path.join(R, "vampwarrior_sheet_fidelity.png"))
print("WROTE", os.path.join(R, "vampwarrior_sheet_fidelity.png"))

T = 400
items = [("vampwarrior_front.png", "front (idle f1)"), ("vampwarrior_threequarter.png", "three-quarter"),
         ("vampwarrior_side.png", "side"), ("vampwarrior_back.png", "back"),
         ("vampwarrior_tactical.png", "tactical (256 px)"), ("vampwarrior_face.png", "face: red eyes / fangs"),
         ("vampwarrior_face_side.png", "face, side"), ("vampwarrior_sword.png", "sword: guard / tassel / vein"),
         ("vampwarrior_hem.png", "cape torn hem + lining"), ("vampwarrior_boots.png", "boots: heels / knee cops"),
         ("vampwarrior_hand.png", "grip hand"), ("vampwarrior_dawn_front.png", "dawn skin (swap proof)")]
tiles = []
for fn, lab in items:
    p = os.path.join(R, fn)
    im = Image.open(p).convert("RGBA").resize((T, T), Image.LANCZOS if not fn.endswith("tactical.png") else Image.NEAREST)
    tiles.append(label(im, lab))
W = 4 * T + 18
sheets = []
for clip, txt in (("idle", "idle: 8 frames over the 4 s loop (planted sword, breath, hair / cape drift)"),
                  ("walk", "walk: 8 frames over the 1.25 s stride (in place, sword trailing, cape trailing)")):
    s_ = Image.open(os.path.join(R, "vampwarrior_%s_sheet.png" % clip)).convert("RGBA")
    s_ = s_.resize((W, int(s_.height * W / s_.width)), Image.LANCZOS)
    sheets.append(label(s_, txt))
Hc = 3 * (T + 6) + sum(s.height + 6 for s in sheets)
cs = Image.new("RGBA", (W, Hc), (255, 255, 255, 255))
for i, t in enumerate(tiles):
    cs.paste(t, ((i % 4) * (T + 6), (i // 4) * (T + 6)))
y = 3 * (T + 6)
for s_ in sheets:
    cs.paste(s_, (0, y)); y += s_.height + 6
cs.convert("RGB").save(os.path.join(R, "vampwarrior_contact.png"))
print("WROTE", os.path.join(R, "vampwarrior_contact.png"))
