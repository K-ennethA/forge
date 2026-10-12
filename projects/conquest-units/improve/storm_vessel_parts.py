"""Storm Vessel parametric pieces (pure numpy, deterministic): the generators behind the body SDF, the closed parts and the
colour fields. The firefly_parts / ember_eye_parts idioms are COPIED (not imported) + the unit's own generators.

    Part / open_edges      the firefly_parts idioms (the build's open-edge table reads the same as every unit)
    Profile / sd_polygon2  the eye_totem elliptic lathe of a measured profile (the NECK collar) + IQ's exact 2D polygon
                           distance (copied from ember_eye_parts.py)
    poly_field             front-projected (X-Z) polygon field, > 0 inside, = the distance to the outline
    grid_mesh              a CLOSED mesh from a structured (rows, cols) grid that is periodic in the columns: rows whose
                           points all coincide (radius 0: a pole / a pointed tube end) weld to one vertex; 'loop_rows' makes
                           the rows periodic too (a torus: rings, bands). Faces left with < 3 distinct vertices drop; the
                           winding is fixed outward by the signed volume.
    revolve                a surface of revolution of an (r, h) profile about an axis (origin, axis, ref) at given azimuths
                           (explicit column angles: the seams / ticks / eyes get exact grid lines, no iso-cut needed)
    tube                   a closed tube along a 3D path: per-point radius (0 at both ends = pointed, closed), optional
                           flattened section (horns); parallel-transport frames (no twist flips)
    inset_polygon / earclip  the bolt plate: the 2D outline, its inset by a constant width (the drawn orange edge band)
                           and an ear-clipping triangulation of the inner polygon
    bolt_plate             the closed extruded zig-zag (front + back faces, side walls), regions edge band / inner
"""
import math
import numpy as np


class Part:
    def __init__(self, name, V, F, R, bone=None):
        self.name = name
        self.V = np.asarray(V, float)
        self.F = [list(map(int, f)) for f in F]
        self.R = list(R)
        self.bone = bone
        self.info = {}
        assert len(self.F) == len(self.R), name

    def tris(self):
        return int(sum(len(f) - 2 for f in self.F))


def open_edges(F):
    cnt = {}
    for f in F:
        for i in range(len(f)):
            e = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            cnt[e] = cnt.get(e, 0) + 1
    return int(sum(1 for c in cnt.values() if c == 1))


def signed_volume(V, F):
    V = np.asarray(V, float)
    s = 0.0
    for f in F:
        a = V[f[0]]
        for j in range(1, len(f) - 1):
            s += float(np.dot(a, np.cross(V[f[j]], V[f[j + 1]])))
    return s / 6.0


# --------------------------------------------------------------------------- lathe profile (the neck) -- copied
class Profile:
    """rows (z, x_right_edge, x_left_edge, half_depth) sorted by z (eye_totem_parts.Profile, copied)."""

    def __init__(self, rows):
        R = np.asarray(sorted(rows), float)
        self.z = R[:, 0]
        self.c = 0.5 * (R[:, 1] + R[:, 2])
        self.w = 0.5 * (R[:, 2] - R[:, 1])
        self.d = R[:, 3]
        pts = [(0.0, self.z[0])] + [(max(w, 0.0), z) for w, z in zip(self.w, self.z)] + [(0.0, self.z[-1])]
        out = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) > 1e-9:
                out.append(p)
        self.poly = np.array(out)

    def qz(self, P):
        P = np.atleast_2d(P)
        z = P[:, 2]
        c = np.interp(z, self.z, self.c)
        s = np.interp(z, self.z, np.maximum(self.w, 1e-3) / np.maximum(self.d, 1e-3))
        x = P[:, 0] - c
        yy = P[:, 1] * s
        return np.sqrt(x * x + yy * yy), z, np.arctan2(yy, x)

    def sd(self, P):
        q, z, _ = self.qz(P)
        return sd_polygon2(np.stack([q, z], 1), self.poly)


def sd_polygon2(Q, V, skip_axis=True):
    """IQ's exact signed distance to a closed 2D polygon V (n, 2), vectorised over points Q (m, 2); < 0 inside.
    skip_axis: segments lying on q = 0 (a lathe axis) count for the inside test but are not surface."""
    d = np.full(len(Q), 1e18)
    s = np.ones(len(Q))
    n = len(V)
    for i in range(n):
        a = V[i - 1]; b = V[i]
        e = a - b
        w = Q - b
        if not (skip_axis and abs(a[0]) < 1e-12 and abs(b[0]) < 1e-12):
            t = np.clip((w @ e) / max(float(e @ e), 1e-18), 0.0, 1.0)
            bb = w - np.outer(t, e)
            d = np.minimum(d, (bb * bb).sum(1))
        c1 = Q[:, 1] >= b[1]
        c2 = Q[:, 1] < a[1]
        c3 = e[0] * w[:, 1] > e[1] * w[:, 0]
        flip = (c1 & c2 & c3) | (~c1 & ~c2 & ~c3)
        s = np.where(flip, -s, s)
    return s * np.sqrt(d)


def poly_field(P, poly_xz):
    """> 0 inside the closed X-Z polygon (front projection, y-independent), = the distance to its outline"""
    Q = np.stack([P[:, 0], P[:, 2]], 1)
    return -sd_polygon2(Q, np.asarray(poly_xz, float), skip_axis=False)


# --------------------------------------------------------------------------- closed structured grids
def grid_mesh(P, loop_rows=False, weld=1e-7):
    """P (ni, nj, 3): columns periodic (j wraps). Rows whose points coincide weld to one vertex (poles). loop_rows: rows
    periodic too (torus). -> V, F (tris + quads), FIJ (grid cell (i, j) of every face)."""
    P = np.asarray(P, float)
    ni, nj = P.shape[:2]
    idx = -np.ones((ni, nj), int)
    V = []
    for i in range(ni):
        if np.ptp(P[i], axis=0).max() < weld:
            idx[i, :] = len(V); V.append(P[i].mean(0))
        else:
            for j in range(nj):
                idx[i, j] = len(V); V.append(P[i, j])
    V = np.array(V)
    F, FIJ = [], []
    nrow = ni if loop_rows else ni - 1
    for i in range(nrow):
        i2 = (i + 1) % ni
        for j in range(nj):
            j2 = (j + 1) % nj
            f = [idx[i, j], idx[i2, j], idx[i2, j2], idx[i, j2]]
            g = []
            for v in f:
                if v not in g:
                    g.append(v)
            if len(g) < 3:
                continue
            F.append(g); FIJ.append((i, j))
    if signed_volume(V, F) < 0:
        F = [f[::-1] for f in F]
    return V, F, np.array(FIJ)


def frame_of(axis, ref):
    a = np.asarray(axis, float); a = a / np.linalg.norm(a)
    u = np.asarray(ref, float); u = u - a * float(u @ a); u = u / np.linalg.norm(u)
    v = np.cross(a, u)
    return a, u, v


def revolve(prof, phis, origin, axis, ref):
    """profile rows (r, h) (r >= 0; r == 0 rows become poles) revolved about 'axis' through 'origin'; column angle phi
    measured from 'ref' toward axis x ref. -> grid (len(prof), len(phis), 3)"""
    a, u, v = frame_of(axis, ref)
    pr = np.asarray(prof, float)
    ph = np.asarray(phis, float)
    rad = np.cos(ph)[None, :, None] * u[None, None, :] + np.sin(ph)[None, :, None] * v[None, None, :]
    return np.asarray(origin, float)[None, None, :] + pr[:, 0][:, None, None] * rad + pr[:, 1][:, None, None] * a[None, None, :]


def phis_with(n, marks=(), half_widths=()):
    """n roughly uniform column angles in [0, 2 pi) with exact grid lines at every mark +- half_width (the narrow strips a
    region owns by column: frame seams, hand ticks)"""
    must = []
    for m, hw in zip(marks, half_widths):
        must += [(m - hw) % (2 * math.pi), (m + hw) % (2 * math.pi)]
    base = list(np.linspace(0.0, 2 * math.pi, n, endpoint=False))
    if must:
        gap = 2 * math.pi / n
        base = [b for b in base if min(abs(((b - m + math.pi) % (2 * math.pi)) - math.pi) for m in must) > 0.35 * gap]
    return np.array(sorted(set([round(x, 12) for x in base + must])))


# --------------------------------------------------------------------------- tubes
def tube(path, radii, sides=6, flat=1.0, up=(0.0, 0.0, 1.0)):
    """closed tube along path (n, 3) with radius per point (0 at an end = a pointed, welded tip). Section: an ellipse with
    the 'up'-ward semi-axis scaled by 'flat'. Parallel-transport frames."""
    C = np.asarray(path, float)
    r = np.asarray(radii, float)
    n = len(C)
    T = np.gradient(C, axis=0)
    T = T / np.maximum(np.linalg.norm(T, axis=1), 1e-12)[:, None]
    u0 = np.asarray(up, float)
    u0 = u0 - T[0] * float(u0 @ T[0])
    if np.linalg.norm(u0) < 1e-6:
        u0 = np.cross(T[0], (1.0, 0.0, 0.0))
    U = [u0 / np.linalg.norm(u0)]
    for i in range(1, n):
        u = U[-1] - T[i] * float(U[-1] @ T[i])
        U.append(u / np.linalg.norm(u))
    U = np.array(U)
    W = np.cross(T, U)
    ph = 2 * math.pi * np.arange(sides) / sides
    P = (C[:, None, :] + r[:, None, None] * (np.cos(ph)[None, :, None] * W[:, None, :] * 1.0
                                                + np.sin(ph)[None, :, None] * U[:, None, :] * flat))
    V, F, _ = grid_mesh(P)
    return V, F


def jag_path(a, b, n, amp, normal, phase=0.0):
    """a zig-zag polyline a -> b (n segments) with alternating sideways offsets (the spark crackles)"""
    a = np.asarray(a, float); b = np.asarray(b, float)
    d = b - a
    s = np.cross(d, np.asarray(normal, float)); s = s / max(np.linalg.norm(s), 1e-12)
    t = np.linspace(0.0, 1.0, n + 1)
    off = amp * np.array([0.0] + [(1 if k % 2 else -1) * (0.75 + 0.25 * math.sin(2.3 * k + phase)) for k in range(1, n)] + [0.0])
    return a[None, :] + t[:, None] * d[None, :] + off[:, None] * s[None, :]


# --------------------------------------------------------------------------- bolt plate
def _area2(P):
    P = np.asarray(P, float)
    return 0.5 * float(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]))


def inset_polygon(P, w):
    """offset a simple CCW polygon inward by w (each edge moved along its inward normal, consecutive lines intersected)"""
    P = np.asarray(P, float)
    n = len(P)
    out = []
    for i in range(n):
        a0, a1 = P[i - 1], P[i]
        b0, b1 = P[i], P[(i + 1) % n]
        da = (a1 - a0) / np.linalg.norm(a1 - a0); db = (b1 - b0) / np.linalg.norm(b1 - b0)
        na = np.array([-da[1], da[0]]); nb = np.array([-db[1], db[0]])          # CCW: left normal points inward
        pa = a0 + na * w; pb = b0 + nb * w
        M = np.array([da, -db]).T
        t = np.linalg.solve(M, pb - pa)
        out.append(pa + da * t[0])
    return np.array(out)


def _seg_cross(p, q, r, s):
    def o(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (o(p, q, r) * o(p, q, s) < 0) and (o(r, s, p) * o(r, s, q) < 0)


def is_simple(P):
    n = len(P)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _seg_cross(P[i], P[(i + 1) % n], P[j], P[(j + 1) % n]):
                return False
    return True


def earclip(P):
    """triangulate a simple CCW polygon (ear clipping; deterministic: the first valid ear each pass)"""
    P = np.asarray(P, float)
    idx = list(range(len(P)))
    tris = []

    def inside(p, a, b, c):
        def s(p1, p2, p3):
            return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
        d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
        return not (((d1 < 0) or (d2 < 0) or (d3 < 0)) and ((d1 > 0) or (d2 > 0) or (d3 > 0)))
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        best = None
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = P[i0], P[i1], P[i2]
            cr = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cr <= 1e-12:
                continue
            if any(inside(P[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            # prefer the fattest ear (min angle) -> fewer slivers
            ang = min(_angle(a, b, c), _angle(b, c, a), _angle(c, a, b))
            if best is None or ang > best[0] + 1e-12:
                best = (ang, k)
        assert best is not None, "earclip: no ear (polygon not simple / not CCW)"
        k = best[1]
        tris.append((idx[k - 1], idx[k], idx[(k + 1) % len(idx)]))
        idx.pop(k)
    tris.append(tuple(idx))
    return tris


def _angle(a, b, c):
    u = b - a; v = c - a
    return math.acos(max(-1.0, min(1.0, float(u @ v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-18))))


def bolt_plate(outline_xz, edge_w, half_t, y0=0.0):
    """closed extruded zig-zag: the outline (X, Z) CCW, its inset by edge_w; faces: front (y0 - half_t) + back
    (y0 + half_t) = inner polygon (region 'bolt_hi') + the edge band quads ('bolt'), side walls ('bolt').
    -> V, F, R, info"""
    O = np.asarray(outline_xz, float)
    if _area2(O) < 0:
        O = O[::-1]
    I = inset_polygon(O, edge_w)
    assert _area2(I) > 0 and is_simple(I), "bolt inset polygon degenerate: reduce BOLT_EDGE"
    n = len(O)
    tri_in = earclip(I)
    V, F, R = [], [], []
    for side, y in ((0, y0 - half_t), (1, y0 + half_t)):
        base = len(V)
        for p in O:
            V.append((p[0], y, p[1]))
        for p in I:
            V.append((p[0], y, p[1]))
        o_ = lambda k: base + (k % n)
        i_ = lambda k: base + n + (k % n)
        for (a, b, c) in tri_in:
            f = [base + n + a, base + n + b, base + n + c]
            F.append(f if side == 0 else f[::-1]); R.append("bolt_hi")
        for k in range(n):
            f = [o_(k), o_(k + 1), i_(k + 1), i_(k)]
            F.append(f if side == 0 else f[::-1]); R.append("bolt")
    for k in range(n):                                  # side walls: front outline k -> back outline k
        a, b = k, (k + 1) % n
        F.append([b, a, 2 * n + a, 2 * n + b]); R.append("bolt")
    V = np.array(V, float)
    if signed_volume(V, F) < 0:
        F = [f[::-1] for f in F]
    return V, F, R, {"outline_pts": n, "inner_tris": len(tri_in), "area_xz": round(_area2(O), 4),
                     "inner_area_xz": round(_area2(I), 4)}
