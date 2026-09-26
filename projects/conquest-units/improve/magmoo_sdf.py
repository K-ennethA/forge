"""Magmoo SDF modelling helpers (pure numpy, deterministic): signed-distance primitives, smooth booleans evaluated on a
sparse banded grid, and a vectorised marching-TETRAHEDRA polygoniser (Kuhn 6-tet split -> conforming, watertight).

The head+upper and tail segments are modelled as SDFs (blobby smooth unions = the lava-blob language of the source
main body), polygonised here, then sent through the SAME voxel remesh + collapse decimation as the source blob in
magmoo_build.py, so all three segments share one surface finish.

Grid convention: values are signed distances (negative inside). Cells never touched by a primitive's box hold +BAND
("far outside"); every primitive is evaluated only inside its own bounding box grown by (k + BAND), which is exact for
the zero set as long as BAND exceeds the grid step (asserted).
"""
import itertools
import numpy as np


# --------------------------------------------------------------------------- primitives (P: (n,3) -> (n,) distance)
def sd_sphere(P, c, r):
    return np.linalg.norm(P - np.asarray(c, float), axis=1) - r


def sd_ellipsoid(P, c, radii, frame=None):
    """IQ's bound-correct ellipsoid approximation. frame: 3x3 rows = local x, y, z axes (world vectors)."""
    Q = P - np.asarray(c, float)
    if frame is not None:
        Q = Q @ np.asarray(frame, float).T
    r = np.asarray(radii, float)
    k0 = np.linalg.norm(Q / r, axis=1)
    k1 = np.linalg.norm(Q / (r * r), axis=1)
    return np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -r.min())


def sd_round_cone(P, a, b, r1, r2):
    """IQ's exact round cone between sphere (a, r1) and sphere (b, r2)."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    ba = b - a
    l2 = float(ba @ ba)
    rr = r1 - r2
    a2 = l2 - rr * rr
    il2 = 1.0 / l2
    pa = P - a
    y = pa @ ba
    z = y - l2
    q = pa * l2 - np.outer(y, ba)
    x2 = np.einsum("ij,ij->i", q, q)
    y2 = y * y * l2
    z2 = z * z * l2
    k = np.sign(rr) * rr * rr * x2
    d_mid = (np.sqrt(np.maximum(x2 * a2 * il2, 0.0)) + y * rr) * il2 - r1
    d_b = np.sqrt(x2 + z2) * il2 - r2
    d_a = np.sqrt(x2 + y2) * il2 - r1
    out = np.where(np.sign(y) * a2 * y2 < k, d_a, d_mid)
    return np.where(np.sign(z) * a2 * z2 > k, d_b, out)


def smin(a, b, k):
    if k <= 0:
        return np.minimum(a, b)
    h = np.maximum(k - np.abs(a - b), 0.0) / k
    return np.minimum(a, b) - h * h * k * 0.25


def smax(a, b, k):
    return -smin(-a, -b, k)


def bezier(p0, p1, p2, n):
    t = np.linspace(0.0, 1.0, n)[:, None]
    p0, p1, p2 = (np.asarray(v, float) for v in (p0, p1, p2))
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t * t * p2


def catmull(points, n_per):
    """Centripetal-free uniform Catmull-Rom through points (ends duplicated) -> dense polyline."""
    P = np.asarray(points, float)
    P = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0.0, 1.0, n_per, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P[-2])
    return np.array(out)


def arclen(poly):
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(poly, axis=0), axis=1))])


def resample(poly, n):
    s = arclen(poly)
    t = np.linspace(0.0, s[-1], n)
    return np.stack([np.interp(t, s, poly[:, k]) for k in range(3)], 1)


# --------------------------------------------------------------------------- banded grid
class Grid:
    def __init__(self, lo, hi, step, band):
        assert band > 1.5 * step, "BAND must exceed the grid step"
        self.step = step
        self.band = band
        self.lo = np.asarray(lo, float) - 2 * step
        n = np.ceil((np.asarray(hi, float) + 2 * step - self.lo) / step).astype(int) + 1
        self.n = n
        self.F = np.full(tuple(n), band, dtype=np.float64)
        self.ops = []

    def _box(self, lo, hi):
        i0 = np.clip(np.floor((np.asarray(lo) - self.lo) / self.step).astype(int), 0, self.n - 1)
        i1 = np.clip(np.ceil((np.asarray(hi) - self.lo) / self.step).astype(int) + 1, 0, self.n)
        return i0, i1

    def _pts(self, i0, i1):
        axes = [self.lo[k] + self.step * np.arange(i0[k], i1[k]) for k in range(3)]
        X, Y, Z = np.meshgrid(*axes, indexing="ij")
        return np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1), tuple(i1 - i0)

    def apply(self, fn, lo, hi, k, mode="union"):
        """fn(P) -> distances; evaluated in [lo, hi] grown by k + band; combined by smooth union / subtraction."""
        g = k + self.band
        i0, i1 = self._box(np.asarray(lo) - g, np.asarray(hi) + g)
        if np.any(i1 <= i0):
            return
        P, shp = self._pts(i0, i1)
        d = np.clip(fn(P), -self.band * 4, self.band).reshape(shp)
        sl = tuple(slice(a, b) for a, b in zip(i0, i1))
        cur = self.F[sl]
        if mode == "union":
            self.F[sl] = smin(cur, d, k)
        elif mode == "subtract":
            self.F[sl] = smax(cur, -d, k)
        else:
            raise ValueError(mode)
        self.ops.append(mode)

    def floor(self, z0=0.0):
        """hard intersection with the half-space z >= z0 over the whole grid: the blob rests FLAT on the floor."""
        z = self.lo[2] + self.step * np.arange(self.n[2])
        self.F = np.maximum(self.F, (z0 - z)[None, None, :])
        self.ops.append("floor")


# --------------------------------------------------------------------------- marching tetrahedra
_CORNERS = np.array([(dx, dy, dz) for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)])   # index = dx*4 + dy*2 + dz
_TETS = []
for perm in itertools.permutations(range(3)):
    c = np.zeros(3, int)
    tet = [0]
    for ax in perm:
        c = c.copy(); c[ax] = 1
        tet.append(int(c[0] * 4 + c[1] * 2 + c[2]))
    _TETS.append(tet)
_TETS = np.array(_TETS)                                  # (6, 4) Kuhn split along the 0 -> 7 diagonal (conforming)
_CASES = {}
for code in range(16):
    ins = [i for i in range(4) if code >> i & 1]
    outs = [i for i in range(4) if not code >> i & 1]
    if len(ins) == 1:
        _CASES[code] = [[(ins[0], o) for o in outs]]
    elif len(ins) == 3:
        _CASES[code] = [[(outs[0], i) for i in ins]]
    elif len(ins) == 2:
        a, b = ins; c, d = outs
        _CASES[code] = [[(a, c), (a, d), (b, d)], [(a, c), (b, d), (b, c)]]


def polygonise(grid):
    """-> (V (n,3), F (m,3) int) outward-oriented triangles of the zero set (welded on grid edges)."""
    F = grid.F
    nx, ny, nz = F.shape
    mn = np.minimum.reduce([F[dx:nx - 1 + dx, dy:ny - 1 + dy, dz:nz - 1 + dz] for dx, dy, dz in _CORNERS])
    mx = np.maximum.reduce([F[dx:nx - 1 + dx, dy:ny - 1 + dy, dz:nz - 1 + dz] for dx, dy, dz in _CORNERS])
    ci, cj, ck = np.nonzero((mn < 0) & (mx >= 0))
    gid = lambda i, j, k: (i * ny + j) * nz + k
    corner_g = np.stack([gid(ci + dx, cj + dy, ck + dz) for dx, dy, dz in _CORNERS], 1)     # (n, 8)
    Ff = F.ravel()
    tris_pairs = []
    for t in _TETS:
        g4 = corner_g[:, t]                              # (n, 4)
        v4 = Ff[g4]
        code = ((v4 < 0).astype(np.int64) * (1 << np.arange(4))).sum(1)
        for cd, tris in _CASES.items():
            m = code == cd
            if not m.any():
                continue
            G = g4[m]
            for tri in tris:
                tris_pairs.append(np.stack([np.stack([G[:, a], G[:, b]], 1) for a, b in tri], 1))   # (k, 3, 2)
    TP = np.concatenate(tris_pairs, 0)
    lo_ = np.minimum(TP[..., 0], TP[..., 1]); hi_ = np.maximum(TP[..., 0], TP[..., 1])
    NP = nx * ny * nz
    key = lo_.astype(np.int64) * NP + hi_
    uk, inv = np.unique(key.ravel(), return_inverse=True)
    ea, eb = uk // NP, uk % NP

    def pos(g):
        i = g // (ny * nz); j = (g // nz) % ny; k = g % nz
        return grid.lo + grid.step * np.stack([i, j, k], 1)
    va, vb = Ff[ea], Ff[eb]
    t_ = va / (va - vb)
    V = pos(ea) + t_[:, None] * (pos(eb) - pos(ea))
    T = inv.reshape(-1, 3)
    # orientation: the normal must point from the inside corners to the outside corners (sign-change direction):
    # outward ~ gradient; per triangle use the mean of (outside - inside) edge endpoints
    ins_end = np.where(Ff[TP[..., 0]] < 0, TP[..., 0], TP[..., 1])
    out_end = np.where(Ff[TP[..., 0]] < 0, TP[..., 1], TP[..., 0])
    grad = (pos(out_end.ravel()) - pos(ins_end.ravel())).reshape(-1, 3, 3).sum(1)
    nrm = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    flip = np.einsum("ij,ij->i", nrm, grad) < 0
    T[flip] = T[flip][:, ::-1]
    ok = (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])
    return V, T[ok]
