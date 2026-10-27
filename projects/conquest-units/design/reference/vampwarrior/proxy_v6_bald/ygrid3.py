import sys, json, math
from PIL import Image, ImageDraw
pref, out = sys.argv[1], sys.argv[2]; r0, r1 = int(sys.argv[3]), int(sys.argv[4])
m = json.load(open(pref + "_ortho_map.json")); zt, pp = m["z_top"], m["per_px"]
ims = [Image.open(pref + "_ortho_%s.png" % v).convert("RGB").crop((250, r0, 750, r1)) for v in ("front", "side", "back")]
c = Image.new("RGB", (60 + 500 * 3, r1 - r0)); d = ImageDraw.Draw(c)
for k, i in enumerate(ims): c.paste(i, (60 + 500 * k, 0))
yv = math.floor((zt - r0 * pp) / 0.005) * 0.005
while True:
    r = (zt - yv) / pp
    if r > r1: break
    if r >= r0:
        d.line([(55, r - r0), (c.width, r - r0)], fill=(0, 255, 255) if abs(yv / 0.025 - round(yv / 0.025)) < 1e-6 else (255, 255, 0), width=1)
        d.text((0, r - r0 - 6), "%.3f" % yv, fill=(255, 255, 255))
    yv -= 0.005
c.save(out)
