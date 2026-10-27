"""eyecover.py <src.npz> <eyemaps.npz> <out_faces.npy> <out_png>: the eye aperture on the source = the region inside the dark
liner ring (flood fill from the iris, bounded by liner pixels) -> the source faces hit there"""
import sys, numpy as np
from scipy import ndimage
from PIL import Image
sys.path.insert(0, r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units\improve")
import proxy_finish as PF
Z = np.load(sys.argv[1]); M = np.load(sys.argv[2])
FUV, tex = Z["FUV"], Z["tex"]; th, tw = tex.shape[:2]
sel = np.zeros(len(Z["F"]), bool); ims = []
for i in (0, 1):
    FI = M["FI%d" % i]; ok = FI >= 0
    uv = FUV[np.maximum(FI, 0)].mean(-2)
    col = tex[np.clip((uv[..., 1] * th).astype(int), 0, th - 1), np.clip((uv[..., 0] * tw).astype(int), 0, tw - 1)].astype(float)
    lab = PF.srgb_to_lab(col)
    dark = (lab[..., 0] < 48) & (np.hypot(lab[..., 1], lab[..., 2]) < 12) & ok
    lbl, n = ndimage.label(dark)
    sizes = ndimage.sum(dark, lbl, range(1, n + 1))
    top = set(np.unique(lbl[0]).tolist())
    cand = [k + 1 for k in np.argsort(-sizes) if (k + 1) not in top]
    ring = lbl == cand[0]
    ring = ndimage.binary_closing(ring, iterations=2)
    m = ndimage.binary_fill_holes(ring) & ~ring
    sel[np.unique(FI[m & ok])] = True
    vis = col.copy(); vis[m] = vis[m] * 0.5 + np.array([0, 255, 0]) * 0.5
    ims.append(vis.astype(np.uint8))
    print("eye", i, "aperture px", int(m.sum()), "faces", int(len(np.unique(FI[m & ok]))))
np.save(sys.argv[3], sel)
Image.fromarray(np.concatenate([x[:200] for x in ims], 1)).save(sys.argv[4])
# contours of the aperture (3D points, ordered by angle about the aperture centroid in the front view)
out = {}
for i in (0, 1):
    FI = M["FI%d" % i]; H = M["H%d" % i]
    ok = FI >= 0
    uv = FUV[np.maximum(FI, 0)].mean(-2)
    col = tex[np.clip((uv[..., 1] * th).astype(int), 0, th - 1), np.clip((uv[..., 0] * tw).astype(int), 0, tw - 1)].astype(float)
    lab = PF.srgb_to_lab(col)
    dark = (lab[..., 0] < 48) & (np.hypot(lab[..., 1], lab[..., 2]) < 12) & ok
    lbl, n = ndimage.label(dark); sizes = ndimage.sum(dark, lbl, range(1, n + 1)); top = set(np.unique(lbl[0]).tolist())
    cand = [k + 1 for k in np.argsort(-sizes) if (k + 1) not in top]
    ring = ndimage.binary_closing(lbl == cand[0], iterations=2)
    m = ndimage.binary_fill_holes(ring) & ~ring
    bd = m & ~ndimage.binary_erosion(m)
    P = H[bd & ok]; c = H[m & ok].mean(0)
    ang = np.arctan2(P[:, 2] - c[2], P[:, 0] - c[0])
    P = P[np.argsort(ang)]
    out["eye%d" % i] = P; out["c%d" % i] = c
    print("contour", i, len(P), "centre", c.round(4), "x", P[:, 0].min().round(4), P[:, 0].max().round(4), "z", P[:, 2].min().round(4), P[:, 2].max().round(4))
np.savez(sys.argv[3].replace("_eyecover.npy", "_eyecontour.npz"), **out)
