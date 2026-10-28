"""vampwarrior_rawhead_texcheck.py -- <raw.npz> <out.json> <cand.npz> [...] : texture-mapping fidelity of a candidate vs the raw.
For 4 points per candidate face (centroid + 3 interior), the colour through the CANDIDATE's UVs is compared with the colour
through the RAW's UVs at the nearest raw surface point (same raw texture for both). Per face: max colour distance (sRGB
L2 / 255 * 100). Reports faces > 15 / > 30 and saves the bad-face mask (<cand>_texbad.npy) + the per-face error."""
import sys, json, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); OUT = a[1]
V, F, FUV, tex = Z["V"], Z["F"], Z["FUV"], Z["tex"].astype(np.float64)
th, tw = tex.shape[:2]
tree = BVHTree.FromPolygons(V.tolist(), F.tolist())


def look(uv):
    x = np.clip((uv[:, 0] * tw).astype(int), 0, tw - 1); y = np.clip((uv[:, 1] * th).astype(int), 0, th - 1)
    return tex[y, x]


def bary(p, A, B, C):
    v0, v1, v2 = B - A, C - A, p - A
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-20)
    v = (d11 * d20 - d01 * d21) / den; w = (d00 * d21 - d01 * d20) / den
    return np.clip(np.stack([1 - v - w, v, w], 1), 0, 1)


BC = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6]])
res = {}
for cp in a[2:]:
    C = np.load(cp); Vc, Fc, Uc = C["V"], C["F"], C["FUV"]
    err = np.zeros(len(Fc))
    for b in BC:
        P = np.einsum("k,nkd->nd", b, Vc[Fc]); uvc = np.einsum("k,nkd->nd", b, Uc)
        fi = np.empty(len(P), np.int64); Q = np.empty_like(P)
        for i, p in enumerate(P):
            h = tree.find_nearest(Vector(p.tolist()))
            fi[i] = h[2]; Q[i] = h[0]
        T = V[F[fi]]; bb = bary(Q, T[:, 0], T[:, 1], T[:, 2])
        uvr = np.einsum("nk,nkd->nd", bb, FUV[fi])
        d = np.linalg.norm(look(uvc) - look(uvr), axis=1) / 255 * 100
        err = np.maximum(err, d)
    np.save(cp.replace(".npz", "_texerr.npy"), err)
    r = {"faces": int(len(Fc)), "gt15": int((err > 15).sum()), "gt30": int((err > 30).sum()), "p99": round(float(np.percentile(err, 99)), 2)}
    res[cp] = r
    print("TEX", cp, json.dumps(r), flush=True)
json.dump(res, open(OUT, "w"), indent=1)
