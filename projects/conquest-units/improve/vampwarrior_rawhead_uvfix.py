"""vampwarrior_rawhead_uvfix.py -- <raw.npz> <raw_islands.npy> <in.npz> <out.npz> : re-derive a decimated head's per-corner UVs
from the RAW parametrisation (keeps the raw UV layout / texture; fixes collapse-interpolated UVs that straddle islands).
Each candidate face is given ONE raw UV island (majority of the islands nearest to 4 points on it); each of its corners
gets the UV of the nearest raw surface point ON THAT ISLAND (barycentric in the raw face). Corners of faces sharing an
island and a vertex get the same UV, so islands stay continuous; seams fall on decimated edges."""
import sys, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def bary(p, A, B, C):
    v0, v1, v2 = B - A, C - A, p - A
    d00, d01, d11 = (v0 * v0).sum(-1), (v0 * v1).sum(-1), (v1 * v1).sum(-1)
    d20, d21 = (v2 * v0).sum(-1), (v2 * v1).sum(-1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-20)
    v = (d11 * d20 - d01 * d21) / den; w = (d00 * d21 - d01 * d20) / den
    b = np.clip(np.stack([1 - v - w, v, w], -1), 0, 1)
    return b / b.sum(-1, keepdims=True)


def fix(raw, isl, Vc, Fc):
    V, F, FUV = raw["V"], raw["F"], raw["FUV"]
    tree = BVHTree.FromPolygons(V.tolist(), F.tolist())
    BC = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6]])
    votes = np.zeros((len(Fc), 4), np.int64)
    for k, b in enumerate(BC):
        P = np.einsum("k,nkd->nd", b, Vc[Fc])
        for i, p in enumerate(P):
            votes[i, k] = isl[tree.find_nearest(Vector(p.tolist()))[2]]
    fis = np.empty(len(Fc), np.int64)
    for i, v in enumerate(votes):
        u, c = np.unique(v, return_counts=True); fis[i] = u[np.argmax(c)] if c.max() > 1 else v[0]
    # nearest raw face of each vertex (global) and per island when it differs
    gn = np.empty(len(Vc), np.int64); gq = np.empty((len(Vc), 3))
    for i, p in enumerate(Vc):
        h = tree.find_nearest(Vector(p.tolist())); gn[i] = h[2]; gq[i] = h[0]
    trees = {}
    out = np.empty((len(Fc), 3, 2)); moved = 0
    cache = {}
    for i in range(len(Fc)):
        s = fis[i]
        for k in range(3):
            v = Fc[i, k]
            if isl[gn[v]] == s:
                f, q = gn[v], gq[v]
            else:
                key = (v, s)
                if key not in cache:
                    if s not in trees:
                        idx = np.nonzero(isl == s)[0]
                        trees[s] = (BVHTree.FromPolygons(V.tolist(), F[idx].tolist()), idx)
                    t, idx = trees[s]
                    h = t.find_nearest(Vector(Vc[v].tolist()))
                    cache[key] = (idx[h[2]], np.array(h[0]))
                f, q = cache[key]; moved += 1
            T = V[F[f]]; b = bary(q[None], T[0][None], T[1][None], T[2][None])[0]
            out[i, k] = b @ FUV[f]
    return out, fis, moved


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    raw = np.load(a[0]); isl = np.load(a[1]); C = dict(np.load(a[2]))
    uv, fis, moved = fix(raw, isl, C["V"], C["F"])
    d = np.abs(uv - C["FUV"]).max(-1).max(-1)
    C["FUV"] = uv; C["fisland"] = fis
    np.savez_compressed(a[3], **C)
    print("UVFIX", a[2], "corners re-islanded", moved, "faces changed > 1 texel", int((d > 1 / 4096).sum()), "of", len(d), flush=True)
