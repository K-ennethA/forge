# composite_head2.py <view> : second pass for the HEAD only (back / side_left): take edited2_<view>.png only above HEAD_ROW
# inside the hull of what changed (+8 px), feathered; everything else = base2_<view>.png (= pass-1 composite).
# Then reports the final image's diff vs the v6 original outside the union of the pass-1 and pass-2 masks.
import json, os, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull

here = os.path.dirname(os.path.abspath(__file__))
v = sys.argv[1]; HEAD_ROW = int(sys.argv[2]) if len(sys.argv) > 2 else 205
outname = {"side_left": "aligned_body_side_left.png", "back": "aligned_body_back.png"}[v]
base = np.asarray(Image.open(os.path.join(here, "base2_%s.png" % v)).convert("RGB")).astype(np.float32)
b = np.asarray(Image.open(os.path.join(here, "edited2_%s.png" % v)).convert("RGB")).astype(np.float32)
orig = np.asarray(Image.open(os.path.join(here, "orig_%s.png" % v)).convert("RGB")).astype(np.float32)
w1 = np.asarray(Image.open(os.path.join(here, "try1", "hairmask_%s.png" % v))).astype(np.float32) / 255
d = np.abs(base - b).mean(2)
m = ndi.binary_opening(d > 25, structure=np.ones((3, 3))); m[HEAD_ROW:] = False
ys, xs = np.nonzero(m)
hull = ConvexHull(np.c_[xs, ys]); poly = [(float(xs[i]), float(ys[i])) for i in hull.vertices]
hm = Image.new("L", (b.shape[1], b.shape[0])); ImageDraw.Draw(hm).polygon(poly, fill=255)
keep = ndi.binary_dilation(np.asarray(hm) > 0, iterations=8); keep[HEAD_ROW + 6:] = False
w = ndi.gaussian_filter(keep.astype(np.float32), 3.0); w[ndi.binary_erosion(keep, iterations=4)] = 1.0; w[HEAD_ROW + 12:] = 0
# re-tone the edit's background to the base's local background
bg0 = np.median(base[:20, :20].reshape(-1, 3), 0); bg1 = np.median(b[:20, :20].reshape(-1, 3), 0)
obg = (np.abs(base - bg0).max(2) < 14).astype(np.float32)
num = np.stack([ndi.gaussian_filter(base[..., c] * obg, 30) for c in range(3)], -1); den = ndi.gaussian_filter(obg, 30)[..., None]
local_bg = num / np.maximum(den, 1e-3); ebg = np.abs(b - bg1).max(2) < 14
b = b.copy(); b[ebg] = b[ebg] - bg1 + local_bg[ebg]
out = b * w[..., None] + base * (1 - w[..., None])
Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)).save(os.path.join(here, outname))
wu = np.maximum(w1, w); Image.fromarray((wu * 255).astype(np.uint8)).save(os.path.join(here, "hairmask_%s.png" % v))
fin = np.asarray(Image.open(os.path.join(here, outname)).convert("RGB")).astype(np.float32)
outside = wu < 0.01; dc = np.abs(fin - orig).mean(2)
e2_out = np.abs(b - base).mean(2)[w < 0.01]
rep = json.load(open(os.path.join(here, "edit_diff.json")))
rep[v].update({"pass2_head_mask_pct": round(100 * float(keep.mean()), 2),
               "pass2_edit_MAD_outside_its_mask": round(float(e2_out.mean()), 3),
               "final_MAD_outside_mask": round(float(dc[outside].mean()), 4), "final_max_outside_mask": float(dc[outside].max()),
               "mask_pct_union": round(100 * float((wu > 0.01).mean()), 2)})
json.dump(rep, open(os.path.join(here, "edit_diff.json"), "w"), indent=1)
print(v, rep[v])
