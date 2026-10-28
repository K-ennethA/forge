# register_px.py <body_dir> <cape_dir> <worn_dir> <out.json>
# pixel-space registration: silhouette bboxes of the aligned body / cape views + the cape's placement in the BODY frame read from the worn views
# (worn_X = Codex edit of the body view with the cape added: cape-visible px = differs from the body view and is not background).
# front: cape-alone width + hem row -> scale k and offsets; side: the back-most cape px at the hem gives the side offset.
import sys, json, numpy as np
from PIL import Image
from scipy import ndimage
bd, cd, wd, out = sys.argv[1:5]
def A(f): return np.asarray(Image.open(f).convert("RGB")).astype(int)
def fg(a):
    bg = np.median(np.concatenate([a[:8].reshape(-1, 3), a[:, :8].reshape(-1, 3), a[:, -8:].reshape(-1, 3)]), 0)
    m = ndimage.binary_opening(np.abs(a - bg).sum(2) > 28, iterations=1)
    lab, n = ndimage.label(m); s = ndimage.sum(m, lab, range(1, n + 1)); return np.isin(lab, np.nonzero(s > 0.002 * m.size)[0] + 1)
def bbox(m):
    ys, xs = np.nonzero(m); return dict(x0=int(xs.min()), x1=int(xs.max()), top=int(ys.min()), bot=int(ys.max()))
R = {}
for part, d in (("body", bd), ("cape", cd)):
    R[part] = {v: bbox(fg(A("%s/aligned_%s_%s.png" % (d, part, v)))) for v in ("front", "side_left", "back")}
# cape visible in worn views (worn vs the body view it was edited from = body_r1 aligned, copied as body_<v>.png in the cape_r1 dir)
W = {}
for v in ("front", "side_left", "back"):
    w = A("%s/worn_%s.png" % (wd, v)); b = A("%s/body_%s.png" % (wd, v))
    if w.shape != b.shape: w = np.asarray(Image.fromarray(w.astype(np.uint8)).resize(b.shape[1::-1])).astype(int)
    wf, bf = fg(w), fg(b)
    cape_vis = wf & ~ndimage.binary_dilation(bf, iterations=4)
    cape_vis = ndimage.binary_opening(cape_vis, iterations=2)
    W[v] = dict(cape_visible=bbox(cape_vis), worn_all=bbox(wf), body=bbox(bf))
R["worn"] = W
cf, wv = R["cape"]["front"], W["front"]["cape_visible"]
k = (wv["x1"] - wv["x0"]) / float(cf["x1"] - cf["x0"])
tx = (wv["x0"] + wv["x1"]) / 2 - k * (cf["x0"] + cf["x1"]) / 2
ty = wv["bot"] - k * cf["bot"]
cs, ws = R["cape"]["side_left"], W["side_left"]["cape_visible"]
tsx = ws["x1"] - k * cs["x1"]          # back-most cape px (image right = the character's back in the left profile)
R["cape_to_body_px"] = dict(k=k, tx=tx, ty=ty, tsx=tsx, predicted_cape_top_row=k * cf["top"] + ty,
                            side_hem_row_check=dict(worn=ws["bot"], mapped=k * cs["bot"] + ty))
json.dump(R, open(out, "w"), indent=1); print(json.dumps(R, indent=1))
