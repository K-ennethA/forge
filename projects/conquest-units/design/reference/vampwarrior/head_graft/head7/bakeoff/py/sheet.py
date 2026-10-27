import sys
from PIL import Image, ImageDraw, ImageFont
out = sys.argv[1]
cols = ["f0", "f35", "f90", "s0", "s35", "s90", "t0", "t35", "t90", "cs", "ct", "jaw", "blink"]
heads = ["flat 0", "flat 35", "flat 90", "smooth 0", "smooth 35", "smooth 90", "tex 0", "tex 35", "tex 90", "face clay", "face tex", "jaw open 14deg", "blink"]
R = "s2/ra/"; RR = "s2/rr/"
rows = [
 ("SOURCE head7_s56_edit (199.8k tris)", "src", None, "sheet face panel"),
 ("G4 = current in-body finish (4.1k head tris, kit bake, cage 0.20 u)", "G4", None, None),
 ("(a) current graft path at 11k (my smooth-ref bake, cage 0.03)", "a", "a", None),
 ("(b) quadric decimation of the edit at 11k, own UVs, normal+albedo bake, cage 0.004", "b2", "b2", None),
 ("(e) MPFB hm08 template wrapped (9.8k incl. 2 eyeballs), bake cage 0.008", "e", "e", None),
]
cs = 200; lab = 34
try:
    f = ImageFont.truetype("arial.ttf", 13); fb = ImageFont.truetype("arialbd.ttf", 15)
except Exception:
    f = fb = ImageFont.load_default()
W = cs * len(cols); H = 24 + len(rows) * (cs + lab)
G = Image.new("RGB", (W, H), (18, 18, 20)); d = ImageDraw.Draw(G)
for j, h in enumerate(heads):
    d.text((j * cs + 4, 5), h, fill=(200, 200, 200), font=f)
for i, (title, key, rig, extra) in enumerate(rows):
    y = 24 + i * (cs + lab)
    d.text((4, y + 8), title, fill=(255, 230, 150), font=fb)
    for j, c in enumerate(cols):
        p = None
        if c in ("jaw", "blink"):
            if rig:
                p = RR + "%s_%s_ct.png" % (rig, c)
            elif extra and c == "jaw":
                p = "s2/head_panel.png"
        else:
            p = R + "%s_%s.png" % (key, c)
        if p:
            try:
                im = Image.open(p).convert("RGB"); im.thumbnail((cs, cs)); G.paste(im, (j * cs + (cs - im.width) // 2, y + lab))
            except FileNotFoundError:
                pass
    if extra:
        d.text((11 * cs + 4, y + lab + 2), extra, fill=(255, 255, 255), font=f)
G.save(out); print(out, G.size)
