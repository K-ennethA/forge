"""Fire Sprite parametric helpers (pure numpy + one bmesh cutter; deterministic). Used only by firesprite_build.py.

    sd_round_box / sd_round_rect   IQ rounded box (3D, per-point half extents allowed -> tapered block) / 2D rounded rect
    sd_poly2d                      IQ exact signed distance to a closed 2D polygon (eye + zigzag mouth outlines)
    sd_diamond                     bevelled rhombus boss (the crown's front / back diamond), roughly Lipschitz-normalised
    lick / cones_sdf               a flame LICK = a tapered chain of round cones along a quadratic bezier (magmoo's lick_cones
                                   language); cones_sdf evaluates the union of a cone list at arbitrary points
    spine_param                    nearest-point arc parameter of points on a polyline (0 root .. 1 tip) + distance
    keep_islands, open_edges       mesh hygiene
    IsoCutter                      firefly's iso-line cutter (vampito's), as a class: every region boundary is cut into the
                                   mesh along a vertex scalar field's iso-line, so colour edges are clean lines
"""
import math
import numpy as np

import magmoo_sdf as SD      # read-only use (bezier, sd_round_cone)


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


# --------------------------------------------------------------------------- banded grid, far-field safe
class Grid(SD.Grid):
    """magmoo_sdf.Grid whose smooth union never erodes the FAR field: where the current value and the new primitive are
    both >= +band (far outside both), the cell stays +band. The base class blends them to band - k/4 on every union, so
    N overlapping evaluation boxes shave N x k/4 off and the far field goes NEGATIVE -> a filled box (first firesprite
    flame preview: six tongues at k 0.10 on band 0.05 turned the crown flame into a solid block). The zero set is
    unaffected: two surfaces both >= band away have a true smooth union >= band - k/4 > 0 (asserted by the base)."""

    def apply(self, fn, lo, hi, k, mode="union"):
        if mode != "union":
            return SD.Grid.apply(self, fn, lo, hi, k, mode)
        assert self.band - k / 4.0 > 0.1 * self.band, "smooth-union k %.3f too large for band %.3f" % (k, self.band)
        g = k + self.band
        i0, i1 = self._box(np.asarray(lo) - g, np.asarray(hi) + g)
        if np.any(i1 <= i0):
            return
        P, shp = self._pts(i0, i1)
        d = np.clip(fn(P), -self.band * 4, self.band).reshape(shp)
        sl = tuple(slice(a, b) for a, b in zip(i0, i1))
        cur = self.F[sl]
        new = SD.smin(cur, d, k)
        self.F[sl] = np.where((cur >= self.band) & (d >= self.band), self.band, new)
        self.ops.append(mode)


# --------------------------------------------------------------------------- SDF primitives
def sd_round_box(P, c, half, r):
    """half: (3,) or (n,3) per-point half extents (a z-dependent half width gives the tapered block)."""
    q = np.abs(P - np.asarray(c, float)) - (np.asarray(half, float) - r)
    return np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(1), 0.0) - r


def sd_round_rect(Q2, half, r):
    q = np.abs(Q2) - (np.asarray(half, float) - r)
    return np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(1), 0.0) - r


def sd_poly2d(Q, V):
    """IQ's exact polygon SDF, vectorised over points Q (n,2); V (m,2) closed outline (any winding). < 0 inside."""
    Q = np.asarray(Q, float); V = np.asarray(V, float)
    d = np.sum((Q - V[0]) ** 2, 1)
    s = np.ones(len(Q))
    m = len(V)
    j = m - 1
    for i in range(m):
        e = V[j] - V[i]
        w = Q - V[i]
        t = np.clip((w @ e) / float(e @ e), 0.0, 1.0)
        b = w - np.outer(t, e)
        d = np.minimum(d, np.sum(b * b, 1))
        c1 = Q[:, 1] >= V[i, 1]
        c2 = Q[:, 1] < V[j, 1]
        c3 = e[0] * w[:, 1] > e[1] * w[:, 0]
        flip = (c1 & c2 & c3) | (~c1 & ~c2 & ~c3)
        s = np.where(flip, -s, s)
        j = i
    return s * np.sqrt(d)


def sd_diamond(P, c, a, b, proud, bevel, facing):
    """Rhombus boss on a vertical face: half width a (x), half height b (z), standing 'proud' out along facing = -1 (front,
    -Y) or +1 (back, +Y) from the plane y = c[1]; its outline shrinks by 'bevel' (fraction) toward the proud face. The
    body extends 'proud' behind the plane too so it roots into the band."""
    Q = P - np.asarray(c, float)
    out = -facing * Q[:, 1]                                     # > 0 in front of the plane
    h = np.clip(out / proud, 0.0, 1.0)
    rho = np.abs(Q[:, 0]) / a + np.abs(Q[:, 2]) / b - (1.0 - bevel * h)
    n = a * b / math.hypot(a, b)                                # ~ unit gradient across the rhombus edge
    return np.maximum(rho * n, np.maximum(out - proud, -out - proud))


# --------------------------------------------------------------------------- flame licks (tapered bezier cone chains)
def lick(root, ctrl, tip, r0, n=10, rtip=0.012, sharp=1.2):
    """-> (cones [(a, b, r1, r2)], spine pts (n,3)). Radius falls root -> tip as (1 - u)^sharp (magmoo lick_cones)."""
    pts = SD.bezier(root, ctrl, tip, n)
    u = np.linspace(0.0, 1.0, n)
    rr = rtip + (r0 - rtip) * (1 - u) ** sharp
    return [(pts[i], pts[i + 1], rr[i], rr[i + 1]) for i in range(n - 1)], pts


def lick_path(points, r0, n=12, rtip=0.012, sharp=1.5):
    """A flame TONGUE along a Catmull-Rom path through >= 3 points (S-curves a single bezier cannot make), resampled to n
    stations by arc length; radius (1 - u)^sharp (sharp > 1 = a concave whip tip, the flame shape) -> (cones, spine)."""
    pts = SD.resample(SD.catmull(np.asarray(points, float), 10), n)
    u = np.linspace(0.0, 1.0, n)
    rr = rtip + (r0 - rtip) * (1 - u) ** sharp
    return [(pts[i], pts[i + 1], rr[i], rr[i + 1]) for i in range(n - 1)], pts


def chain(points, radii):
    """Cones through explicit points with explicit radii (the wand's angular kinked shaft)."""
    P = np.asarray(points, float)
    return [(P[i], P[i + 1], radii[i], radii[i + 1]) for i in range(len(P) - 1)], P


def cones_box(cones, pad=0.0):
    A = np.array([c[0] for c in cones] + [c[1] for c in cones])
    R = max(max(c[2], c[3]) for c in cones)
    return A.min(0) - R - pad, A.max(0) + R + pad


def cones_sdf(P, cones):
    d = np.full(len(P), 1e9)
    for a, b, r1, r2 in cones:
        d = np.minimum(d, SD.sd_round_cone(P, a, b, r1, r2))
    return d


def spine_param(P, pts):
    """-> (t in [0,1] by arc length at the nearest polyline point, arc length s, distance)."""
    pts = np.asarray(pts, float)
    seg = np.diff(pts, axis=0)
    L = np.linalg.norm(seg, axis=1)
    acc = np.concatenate([[0.0], np.cumsum(L)])
    best_d = np.full(len(P), 1e18); best_s = np.zeros(len(P))
    for i in range(len(seg)):
        w = P - pts[i]
        u = np.clip((w @ seg[i]) / max(float(seg[i] @ seg[i]), 1e-18), 0.0, 1.0)
        d = np.linalg.norm(w - np.outer(u, seg[i]), axis=1)
        m = d < best_d
        best_d[m] = d[m]; best_s[m] = acc[i] + u[m] * L[i]
    return best_s / acc[-1], best_s, best_d


# --------------------------------------------------------------------------- mesh hygiene
def keep_islands(V, F, min_frac):
    n = len(V)
    par = np.arange(n)

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for f in F:
        r0 = find(f[0])
        for v in f[1:]:
            r1 = find(v)
            if r1 != r0:
                par[r1] = r0
    roots = np.array([find(i) for i in range(n)])
    _, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_v = (cnt >= min_frac * n)[inv]
    remap = -np.ones(n, dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "dropped_verts": int((~keep_v).sum())}


def open_edges(F):
    cnt = {}
    for f in F:
        for i in range(len(f)):
            e = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            cnt[e] = cnt.get(e, 0) + 1
    return int(sum(1 for c in cnt.values() if c == 1))


# --------------------------------------------------------------------------- iso-line cutter
class IsoCutter:
    """Cut a triangle mesh along iso-lines of per-vertex scalar fields (firefly_build.iso_cut, as a class).
    fields: {name: (n,) array}. cut(key, tau, gate) splits every edge crossing tau (crossings within 'snap' of a vertex
    snap to it: no sliver triangles), connects the split points across each face, re-triangulates; every field is
    interpolated onto the new vertices. finish() -> (V, F, per-face mean of every field)."""

    def __init__(self, V, F, fields, snap=0.18):
        import bmesh
        self.bmesh = bmesh
        self.snap = snap
        bm = bmesh.new()
        for p in V:
            bm.verts.new(p)
        bm.verts.ensure_lookup_table()
        for f in F:
            bm.faces.new([bm.verts[i] for i in f])
        bm.verts.index_update()
        self.LAY = {k: bm.verts.layers.float.new(k) for k in fields}
        for v in bm.verts:
            for k, arr in fields.items():
                v[self.LAY[k]] = float(arr[v.index])
        self.bm = bm

    def gate_pos(self, key, tol=0.0):
        L = self.LAY[key]
        return lambda a, b: a[L] > -tol and b[L] > -tol

    def cut(self, key, tau, gate=None):
        bm, LAY, bmesh = self.bm, self.LAY, self.bmesh
        L = LAY[key]
        eps = 1e-5 * max(1.0, abs(tau))
        on = lambda v: abs(v[L] - tau) <= eps
        side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
        ok = (lambda a, b: True) if gate is None else gate
        for e in bm.edges:
            a, b = e.verts
            sa, sb = side(a), side(b)
            if sa * sb < 0 and ok(a, b):
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < self.snap:
                    a[L] = tau
                elif tt > 1 - self.snap:
                    b[L] = tau
        cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
        for e in cuts:
            a, b = e.verts
            tt = (tau - a[L]) / (b[L] - a[L])
            vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
            _, nv = bmesh.utils.edge_split(e, a, tt)
            for k in LAY:
                nv[LAY[k]] = vals[k]
            nv[L] = tau
        pairs = []
        for f in bm.faces:
            vs = list(f.verts)
            sides = [side(v) for v in vs]
            if not (1 in sides and -1 in sides):
                continue
            cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
            if len(cv) == 2:
                pairs.append(cv)
        for cv in pairs:
            bmesh.ops.connect_verts(bm, verts=cv)
        big = [f for f in bm.faces if len(f.verts) > 3]
        if big:
            bmesh.ops.triangulate(bm, faces=big)
        return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts), "face_connects": len(pairs)}

    def finish(self):
        bm = self.bm
        self.bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        bm.verts.index_update(); bm.faces.index_update()
        V = np.array([v.co[:] for v in bm.verts])
        F = [[v.index for v in f.verts] for f in bm.faces]
        FV = {k: np.array([np.mean([v[L] for v in f.verts]) for f in bm.faces]) for k, L in self.LAY.items()}
        bm.free()
        return V, F, FV
