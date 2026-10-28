# align_views.py <dir> <prefix> [ref_top ref_bot]: <prefix>_{front,side_left,back}.png -> aligned_<prefix>_{front,side_left,side_right,back}.png
# every view scaled + shifted so its subject's top/bottom rows match the front's (or ref_top/ref_bot), bbox-centred at x=512; side_right = side_left mirrored.
import sys, os, json
import numpy as np
from PIL import Image
from scipy import ndimage
d, pre = sys.argv[1], sys.argv[2]
ref = [int(v) for v in sys.argv[3:5]] if len(sys.argv) > 4 else None
W, H, BG = 1024, 1536, (109, 109, 109)

def subject(a):
    bg = np.median(np.concatenate([a[:8].reshape(-1, 3), a[-8:].reshape(-1, 3), a[:, :8].reshape(-1, 3), a[:, -8:].reshape(-1, 3)]), 0)
    m = np.abs(a.astype(int) - bg).sum(2) > 28
    m = ndimage.binary_opening(m, iterations=1)
    lab, n = ndimage.label(m)
    if n > 1:
        sizes = ndimage.sum(m, lab, range(1, n + 1)); keep = np.nonzero(sizes > 0.002 * m.size)[0] + 1
        m = np.isin(lab, keep)
    ys, xs = np.nonzero(m)
    return m, ys.min(), ys.max(), xs.min(), xs.max(), bg

imgs = {v: np.asarray(Image.open(os.path.join(d, "%s_%s.png" % (pre, v))).convert("RGB")) for v in ("front", "side_left", "back")}
info = {}
_, t0, b0, _, _, _ = subject(imgs["front"])
if ref: t0, b0 = ref
for v, a in imgs.items():
    m, t, b, x0, x1, bg = subject(a)
    s = (b0 - t0) / float(b - t)
    im = Image.fromarray(a).resize((round(a.shape[1] * s), round(a.shape[0] * s)), Image.LANCZOS)
    cx = (x0 + x1) / 2 * s
    out = Image.new("RGB", (W, H), tuple(int(c) for c in bg))
    out.paste(im, (round(W / 2 - cx), round(t0 - t * s)))
    out.save(os.path.join(d, "aligned_%s_%s.png" % (pre, v)))
    info[v] = dict(src_top=int(t), src_bot=int(b), src_x0=int(x0), src_x1=int(x1), scale=s, size=list(a.shape[:2]))
    if v == "side_left":
        out.transpose(Image.FLIP_LEFT_RIGHT).save(os.path.join(d, "aligned_%s_side_right.png" % pre))
info["target_rows"] = [int(t0), int(b0)]
json.dump(info, open(os.path.join(d, "align_%s.json" % pre), "w"), indent=1)
print(json.dumps(info))
