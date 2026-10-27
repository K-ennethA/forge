"""eyecomp.py <src.npz> <src eyemaps> <assembled prefix> : what fills the source eye aperture (inside the liner) in the
hybrid's front view; prints per eye the share of eyeball / skin / pocket px and the visible-eyeball % of the aperture"""
import sys, numpy as np
from scipy import ndimage
sys.path.insert(0, r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units\improve")
import proxy_finish as PF
Z = np.load(sys.argv[1]); M = np.load(sys.argv[2]); pre = sys.argv[3]
FUV, tex = Z["FUV"], Z["tex"]; th, tw = tex.shape[:2]
res = []
for i in (0, 1):
    FI = M["FI%d" % i]; ok = FI >= 0
    uv = FUV[np.maximum(FI, 0)].mean(-2)
    col = tex[np.clip((uv[..., 1] * th).astype(int), 0, th - 1), np.clip((uv[..., 0] * tw).astype(int), 0, tw - 1)].astype(float)
    lab = PF.srgb_to_lab(col)
    dark = (lab[..., 0] < 48) & (np.hypot(lab[..., 1], lab[..., 2]) < 12) & ok
    lbl, n = ndimage.label(dark); sz = ndimage.sum(dark, lbl, range(1, n + 1)); top = set(np.unique(lbl[0]).tolist())
    cand = [k + 1 for k in np.argsort(-sz) if (k + 1) not in top]
    ring = ndimage.binary_closing(lbl == cand[0], iterations=2)
    m = ndimage.binary_fill_holes(ring) & ~ring
    P = np.load(pre + "_eyepart%d.npy" % i)
    h, w = min(P.shape[0], m.shape[0]), min(P.shape[1], m.shape[1])
    m = m[:h, :w]; P = P[:h, :w]
    tot = int(m.sum())
    comp = {k: int(((P == v) & m).sum()) for k, v in (("eyeball", 1), ("skin", 0), ("pocket_or_mouth", 4), ("none", -1))}
    allvis = int((P == 1).sum())
    res.append({"aperture_px": tot, **comp, "eyeball_in_aperture_pct": round(100 * comp["eyeball"] / tot, 1),
                "eyeball_visible_total_px": allvis, "visible_vs_aperture_pct": round(100 * allvis / tot, 1)})
print("EYECOMP", res)
