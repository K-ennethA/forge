# composite_r2.py: base (r1 aligned) + Codex r2 edits only inside the edit regions (diff mask, dilated, feathered), so untouched regions stay r1-identical.
# back: whole edited image, except the loin-panel strip is kept only where the base shows background (the gap between the legs) -> no "tail" painted over the legs.
import numpy as np, json
from PIL import Image
from scipy import ndimage
def L(f): return np.asarray(Image.open(f).convert("RGB")).astype(float)
rep = {}
regions = {"front": [(420, 560, 610, 1310)], "side_left": [(360, 560, 470, 1310)]}
for v, boxes in regions.items():
    b, e = L("base_%s.png" % v), L("edited_%s.png" % v)
    d = np.abs(b - e).sum(2) > 45
    box = np.zeros(d.shape, bool)
    for x0, y0, x1, y1 in boxes: box[y0:y1, x0:x1] = True
    m = ndimage.binary_dilation(ndimage.binary_opening(d & box, iterations=1), iterations=6) & box
    m = ndimage.gaussian_filter(m.astype(float), 2.0)[..., None]
    out = b * (1 - m) + e * m
    Image.fromarray(out.round().clip(0, 255).astype(np.uint8)).save("body_%s.png" % v)
    rep[v] = dict(changed_px=int((m[..., 0] > 0.5).sum()))
b, e = L("base_back.png"), L("edited_back.png")
bgc = np.median(b[:10, :10].reshape(-1, 3), 0)
base_bg = np.abs(b - bgc).sum(2) < 25
burg = (e[..., 0] - e[..., 1] > 25) & (e[..., 0] > 60)
strip = np.zeros(burg.shape, bool); strip[640:1320, 420:610] = True
fix = ndimage.binary_dilation(burg & strip & ~base_bg, iterations=2) & strip & ~base_bg
out = e.copy(); out[fix] = b[fix]
Image.fromarray(out.round().clip(0, 255).astype(np.uint8)).save("body_back.png")
rep["back"] = dict(strip_px_reverted_to_base=int(fix.sum()), note="edited_back.png elsewhere (upper back / hips / knees simplified)")
json.dump(rep, open("composite_r2.json", "w"), indent=1); print(rep)
