"""vampwarrior_rawhead_vis.py -- <raw.npz> <out.npz> <z_cut> [ndirs] : which raw-head faces can ever be seen from outside.
f-frame (front -y, up +z, glb units). A face is VISIBLE when a ray leaving it (any direction of a Fibonacci sphere, plus +-
its normal; both sides, the raw winding is mixed) escapes to infinity. Occluders: every face that is not entirely below
z_cut, plus a CAP over the neck opening at z_cut (stands for the body: no view up into the neck counts).
Pass 1: face centroids. Pass 2: hidden faces within 3 rings of a visible face are re-tested from 3 extra interior points.
Writes vis (bool per face), cand (bool: face considered, i.e. not entirely below z_cut), cap (the cap triangles)."""
import sys, time, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def hull2d(p):
    o = np.lexsort((p[:, 1], p[:, 0])); p = p[o]
    cr = lambda a, b, c: (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    lo, up = [], []
    for q in p:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in p[::-1]:
        while len(up) >= 2 and cr(up[-2], up[-1], q) <= 0:
            up.pop()
        up.append(q)
    return np.array(lo[:-1] + up[:-1])


def grow(mask, F, nv, n):
    for _ in range(n):
        vm = np.zeros(nv, bool); vm[F[mask].ravel()] = True; mask = vm[F].any(1)
    return mask
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); ZC = float(a[2]); ND = int(a[3]) if len(a) > 3 else 200
V, F = Z["V"].astype(np.float64), Z["F"].astype(np.int64)
cand = V[F][:, :, 2].max(1) > ZC
# cap: hull of the plane crossings, pushed out 0.01
E = np.concatenate([F[cand][:, [0, 1]], F[cand][:, [1, 2]], F[cand][:, [2, 0]]])
za, zb = V[E[:, 0], 2] - ZC, V[E[:, 1], 2] - ZC
x = (za < 0) != (zb < 0)
w = za[x] / (za[x] - zb[x])
P = V[E[x, 0]] + (V[E[x, 1]] - V[E[x, 0]]) * w[:, None]
ring = hull2d(P[:, :2]); c2 = ring.mean(0)
ring = c2 + (ring - c2) * (1 + 0.01 / np.linalg.norm(ring - c2, axis=1))[:, None]
nV = len(V)
capV = np.vstack([[c2[0], c2[1], ZC], np.c_[ring, np.full(len(ring), ZC)]])
capF = np.array([[0, 1 + i, 1 + (i + 1) % len(ring)] for i in range(len(ring))]) + nV
allV = np.vstack([V, capV]); allF = np.vstack([F[cand], capF])
tree = BVHTree.FromPolygons(allV.tolist(), allF.tolist())
print("cap ring", len(ring), "radius", np.linalg.norm(ring - c2, axis=1).round(3).min(), np.linalg.norm(ring - c2, axis=1).round(3).max(), flush=True)
i = np.arange(ND) + 0.5
phi = np.arccos(1 - 2 * i / ND); th = np.pi * (1 + 5 ** 0.5) * i
D = np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)
Dv = [Vector(d.tolist()) for d in D]
T = V[F]
N = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]); N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
EPS = 2e-5


def escapes(o, n):
    for d in [Vector(n.tolist()), Vector((-n).tolist())] + Dv:
        if tree.ray_cast(o + d * EPS, d, 10.0)[0] is None:
            return True
    return False


vis = np.zeros(len(F), bool)
t0 = time.time()
idx = np.nonzero(cand)[0]
for k, f in enumerate(idx):
    vis[f] = escapes(Vector(T[f].mean(0).tolist()), N[f])
    if k % 20000 == 0:
        print("pass1", k, "of", len(idx), round(time.time() - t0), "s", flush=True)
print("pass1 visible", int(vis.sum()), "of", int(cand.sum()), round(time.time() - t0), "s", flush=True)
near = grow(vis.copy(), F, len(V), 3)
re = np.nonzero(near & ~vis & cand)[0]
BC = np.array([[0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6]])
for k, f in enumerate(re):
    for b in BC:
        if escapes(Vector((b @ T[f]).tolist()), N[f]):
            vis[f] = True
            break
print("pass2 retested", len(re), "visible now", int(vis.sum()), round(time.time() - t0), "s", flush=True)
np.savez_compressed(a[1], vis=vis, cand=cand, capV=capV, capF=capF - nV, zc=ZC)
print("VIS done", flush=True)
