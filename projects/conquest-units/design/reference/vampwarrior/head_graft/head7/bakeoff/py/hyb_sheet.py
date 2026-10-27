"""hyb_sheet.py <out.png> : source | (b) | hybrid comparison + the hybrid's poses, from existing renders (no Blender)"""
import sys
from PIL import Image, ImageDraw, ImageFont
out = sys.argv[1]
cs = 230; lab = 30
try:
    f = ImageFont.truetype("arial.ttf", 13); fb = ImageFont.truetype("arialbd.ttf", 15)
except Exception:
    f = fb = ImageFont.load_default()
rows = [
 ("SOURCE head7_s56_edit (199.8k tris)", [("tex 0", "s2/ra/src_t0.png"), ("tex 35", "s2/ra/src_t35.png"), ("tex 90", "s2/ra/src_t90.png"),
                                          ("face tex", "s2/ra/src_ct.png"), ("face clay", "s2/ra/src_cs.png"), ("sheet panel", "s2/head_panel.png")]),
 ("(b) decimated edit, 11k tris (look reference)", [("tex 0", "s2/ra/b2_t0.png"), ("tex 35", "s2/ra/b2_t35.png"), ("tex 90", "s2/ra/b2_t90.png"),
                                                     ("face tex", "s2/ra/b2_ct.png"), ("face clay", "s2/ra/b2_cs.png")]),
 ("HYBRID (MPFB topology + source look), 14.3k tris all parts", [("face tex (rest)", "s3/rp/p_rest_ct.png"), ("face clay (rest)", "s3/rp/p_rest_cs.png"),
                                          ("eye L zoom", "s3/r/h1_ez_t.png"), ("eye R zoom", "s3/r/h1_ezr_t.png"), ("mouth zoom", "s3/r/h1_mz_t.png"),
                                          ("eye clay", "s3/r/h1_ez_s.png")]),
 ("HYBRID poses (shape keys)", [("blink_L", "s3/rp/p_blink_L_ct.png"), ("blink_R", "s3/rp/p_blink_R_ct.png"), ("jaw_open", "s3/rp/p_jaw_open_ct.png"),
                                ("viseme_A", "s3/rp/p_viseme_A_ct.png"), ("viseme_O", "s3/rp/p_viseme_O_ct.png"), ("viseme_M", "s3/rp/p_viseme_M_ct.png")]),
 ("HYBRID poses (clay)", [("brow_raise", "s3/rp/p_brow_raise_ct.png"), ("blink_L clay", "s3/rp/p_blink_L_cs.png"), ("jaw_open clay", "s3/rp/p_jaw_open_cs.png"),
                          ("viseme_O clay", "s3/rp/p_viseme_O_cs.png"), ("viseme_M clay", "s3/rp/p_viseme_M_cs.png")]),
]
W = cs * 6; H = len(rows) * (cs + 2 * lab)
G = Image.new("RGB", (W, H), (18, 18, 20)); d = ImageDraw.Draw(G)
for i, (title, cells) in enumerate(rows):
    y = i * (cs + 2 * lab)
    d.text((4, y + 6), title, fill=(255, 230, 150), font=fb)
    for j, (cap, p) in enumerate(cells):
        x = j * cs
        d.text((x + 4, y + lab + 2), cap, fill=(220, 220, 220), font=f)
        try:
            im = Image.open(p).convert("RGB"); im.thumbnail((cs, cs - 4)); G.paste(im, (x + (cs - im.width) // 2, y + 2 * lab))
        except FileNotFoundError:
            d.text((x + 10, y + 2 * lab + 20), "missing", fill=(255, 80, 80), font=f)
G.save(out); print(out, G.size)
