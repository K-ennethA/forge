"""grid.py <out.png> <ncols> <cell_px> label=path ... (label '-' = blank)"""
import sys
from PIL import Image, ImageDraw, ImageFont
out, nc, cs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
items = [a.split("=", 1) for a in sys.argv[4:]]
nr = (len(items) + nc - 1) // nc
G = Image.new("RGB", (nc * cs, nr * (cs + 22)), (20, 20, 22))
d = ImageDraw.Draw(G)
try:
    f = ImageFont.truetype("arial.ttf", 15)
except Exception:
    f = ImageFont.load_default()
for i, (lab, p) in enumerate(items):
    x, y = (i % nc) * cs, (i // nc) * (cs + 22)
    if p != "-":
        im = Image.open(p).convert("RGB"); im.thumbnail((cs, cs)); G.paste(im, (x + (cs - im.width) // 2, y + 22))
    d.text((x + 4, y + 3), lab, fill=(235, 235, 235), font=f)
G.save(out); print(out, G.size)
