"""post_tex.py <out_dir> : fill bake misses (black texels) inside the head's skin UV islands with the nearest baked texel"""
import sys, os, numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
O = sys.argv[1]
Z = np.load(os.path.join(O, "hyb_uv.npz")); uv, lt, mi = Z["uv"], Z["lt"], Z["mi"]
A = np.asarray(Image.open(os.path.join(O, "hybrid_albedo.png")).convert("RGB")).copy()
R = A.shape[0]
mask = Image.new("L", (R, R), 0); d = ImageDraw.Draw(mask)
o = 0
for n, m in zip(lt, mi):
    P = uv[o:o + n]; o += n
    if m != 0:
        continue
    d.polygon([(float(x * R), float((1 - y) * R)) for x, y in P], fill=255)
isl = np.asarray(mask) > 0
miss = isl & (A.sum(-1) <= 6)
good = A.sum(-1) > 6
_, (iy, ix) = ndimage.distance_transform_edt(~good, return_indices=True)
A2 = A.copy(); A2[miss] = A[iy[miss], ix[miss]]
Image.fromarray(A2).save(os.path.join(O, "hybrid_albedo.png"))
print("POSTTEX island px", int(isl.sum()), "misses filled", int(miss.sum()), "frac", round(float(miss.sum() / max(isl.sum(), 1)), 4))
