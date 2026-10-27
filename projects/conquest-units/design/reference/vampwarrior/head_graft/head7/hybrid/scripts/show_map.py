import sys, numpy as np
from PIL import Image
Z = np.load(sys.argv[1]); M = np.load(sys.argv[2]); out = sys.argv[3]
H, FI = M["H"], M["FI"]; ok = FI >= 0
FUV, tex, flab = Z["FUV"], Z["tex"], Z["flab"]
uv = FUV[np.maximum(FI, 0)].mean(-2)
th, tw = tex.shape[:2]
col = tex[np.clip((uv[..., 1] * th).astype(int), 0, th - 1), np.clip((uv[..., 0] * tw).astype(int), 0, tw - 1)].copy()
col[~ok] = 0
dep = np.where(ok, H[..., 1], np.nan); lo, hi = np.nanpercentile(dep, 1), np.nanpercentile(dep, 99)
dn = np.clip((np.nan_to_num(dep, nan=hi) - lo) / (hi - lo), 0, 1)
g = (255 * (1 - dn)).astype(np.uint8)
s = 2
img = np.concatenate([col, np.stack([g] * 3, -1)], 1)
Image.fromarray(img).resize((img.shape[1] * s, img.shape[0] * s), Image.NEAREST).save(out)
