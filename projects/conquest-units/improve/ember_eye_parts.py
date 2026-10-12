"""Ember Eye parametric pieces (pure numpy, deterministic): the generators behind the body SDF, the closed sheet parts
and the colour fields. Copied from eye_totem_parts.py / petal_cobra_parts.py (copied, not imported) + the unit's own.

    Part / open_edges      the firefly_parts idioms (the build's open-edge table reads the same as every unit)
    sd_puck                a rounded cylinder (axis Y): the ORB is a thick coin / puck (the 3/4 side view proves it, see the
                           build header), flat front + back faces, rounded rim edge
    Profile / sd_polygon2  the eye_totem elliptic lathe of a measured profile (the STEM) + IQ's exact 2D polygon distance
    pillow / grid_normals  a CLOSED sheet over a structured (s, t) grid (petal_cobra): perimeter vertices shared by the two
                           skins, interior doubled (+-N H); coincident perimeter points welded. The upper flame, the two side
                           flames and the three ribbon strips are pillows.
    cos_cols               cosine-spaced column parameters: with H = h sin(phi) the pillow section is an exact ellipse
    teardrop               a 2D teardrop outline (round bottom + two tangent lines to a pointed top): the flame pupil
    poly_field             front-projected (X-Z) polygon field, > 0 inside, ~ distance near the outline
    resample_by_arc        arc-length resampling of a 3D polyline
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


def face_normal(V, f):
    q = V[f]
    n = np.cross(q[1] - q[0], q[2] - q[0])
    if len(f) == 4:
        n = n + np.cross(q[2] - q[0], q[3] - q[0])
    return n


def resample_by_arc(C, n):
    C = np.asarray(C, float)
    al = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    tt = np.linspace(0.0, al[-1], n)
    return np.stack([np.interp(tt, al, C[:, k]) for k in range(C.shape[1])], 1)


# --------------------------------------------------------------------------- orb
def sd_puck(P, c, R, D, rr):
    """rounded cylinder, axis Y through c: radius R (X-Z), half-depth D (Y), edge rounding rr. Exact distance."""
    Q = np.atleast_2d(P) - np.asarray(c, float)
    q = np.sqrt(Q[:, 0] ** 2 + Q[:, 2] ** 2)
    a = q - (R - rr)
    b = np.abs(Q[:, 1]) - (D - rr)
    out = np.sqrt(np.maximum(a, 0.0) ** 2 + np.maximum(b, 0.0) ** 2)
    return out + np.minimum(np.maximum(a, b), 0.0) - rr


# --------------------------------------------------------------------------- elliptic lathe (the stem)
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


# --------------------------------------------------------------------------- front-projected fields
def poly_field(P, poly_xz):
    """> 0 inside the closed X-Z polygon (front projection, y-independent), = the distance to its outline"""
    Q = np.stack([P[:, 0], P[:, 2]], 1)
    return -sd_polygon2(Q, np.asarray(poly_xz, float), skip_axis=False)


def teardrop(cx, zb, r, z_tip, n=96):
    """closed X-Z outline: a circle of radius r centred (cx, zb) + the two tangent lines to the pointed top (cx, z_tip)"""
    L = z_tip - zb
    assert L > r
    al = math.asin(r / L)                     # half-angle at the tip
    beta = math.pi / 2 - al                   # the tangent points' polar angle from +Z, measured at the circle centre
    pts = [(cx, z_tip)]
    for k in range(n + 1):
        ang = beta + (2 * math.pi - 2 * beta) * k / n       # sweep from the right tangent point round the bottom
        pts.append((cx + r * math.sin(ang), zb + r * math.cos(ang)))
    return np.array(pts[:1] + pts[1:][::-1])                # one consistent winding (sd_polygon2 is winding-free)


def stroke_poly(rows):
    """closed X-Z polygon from measured rows (z, x_left, x_right): left edge down, right edge up"""
    R = sorted(rows)
    left = [(xl, z) for z, xl, xr in R]
    right = [(xr, z) for z, xl, xr in R[::-1]]
    return np.array(left + right)


# --------------------------------------------------------------------------- pillow (closed sheets)
def cos_cols(nt):
    """column parameters T_j = (1 - cos(pi j / nt)) / 2 and the angle phi_j = pi j / nt: H = h sin(phi) across a row
    whose points are lerped by T gives an exact elliptic section (dense at the rounded edges)"""
    phi = np.pi * np.arange(nt + 1) / nt
    return 0.5 * (1.0 - np.cos(phi)), phi


def pillow(P, N, H, region_fn, weld=1e-7):
    """P (ns+1, nt+1, 3) grid points, N (ns+1, nt+1, 3) unit offset directions, H (ns+1, nt+1) half-thickness (forced 0
    on the perimeter). Perimeter vertices are single (shared by both skins); interior vertices are doubled (top +N H,
    bottom -N H). Coincident perimeter points (a pointed tip) are welded; faces left with < 3 distinct vertices drop.
    region_fn(i, j, top) -> region of grid cell (i, j) on the top (+N) / bottom skin (a placeholder when the build
    re-cuts regions by fields). Returns V, F, R, IJ (grid index of each vertex), TOP (bool: vertex on the +N skin or
    the perimeter)."""
    P = np.asarray(P, float); N = np.asarray(N, float); H = np.asarray(H, float)
    ns, nt = P.shape[0] - 1, P.shape[1] - 1
    assert ns >= 2 and nt >= 2
    V, IJ, SK = [], [], []
    top = -np.ones((ns + 1, nt + 1), int); bot = -np.ones((ns + 1, nt + 1), int)
    key = {}
    for i in range(ns + 1):
        for j in range(nt + 1):
            if i in (0, ns) or j in (0, nt):
                k = tuple(np.round(P[i, j] / weld).astype(np.int64))
                if k not in key:
                    key[k] = len(V); V.append(P[i, j]); IJ.append((i, j)); SK.append(0)
                top[i, j] = bot[i, j] = key[k]
            else:
                top[i, j] = len(V); V.append(P[i, j] + N[i, j] * H[i, j]); IJ.append((i, j)); SK.append(1)
                bot[i, j] = len(V); V.append(P[i, j] - N[i, j] * H[i, j]); IJ.append((i, j)); SK.append(-1)
    V = np.array(V)
    F, R = [], []
    for i in range(ns):
        for j in range(nt):
            for idx, is_top in ((top, True), (bot, False)):
                f = [idx[i, j], idx[i + 1, j], idx[i + 1, j + 1], idx[i, j + 1]]
                if not is_top:
                    f = f[::-1]
                g = []
                for v in f:
                    if v not in g:
                        g.append(v)
                if len(g) < 3:
                    continue
                F.append(g); R.append(region_fn(i, j, is_top))
    probe = [k for k, f in enumerate(F) if all(0 < IJ[v][0] < ns and 0 < IJ[v][1] < nt for v in f)]
    if probe:
        f = F[probe[0]]
        i_, j_ = IJ[f[0]]
        sgn = 1.0 if f[0] == top[i_, j_] else -1.0
        if np.dot(face_normal(V, f), N[i_, j_]) * sgn < 0:
            F = [f[::-1] for f in F]
    return V, F, R, np.array(IJ), np.array(SK)


def grid_normals(P, hint):
    """unit normals of a structured grid P (ns+1, nt+1, 3): cross(dP/ds, dP/dt) by central differences; one sign for the
    whole grid, chosen so the mean normal points along 'hint'. Degenerate (welded) rows borrow their neighbour's."""
    Ps = np.gradient(P, axis=0)
    Pt = np.gradient(P, axis=1)
    n = np.cross(Ps, Pt)
    ln = np.linalg.norm(n, axis=2)
    bad = ln < 1e-9
    n = n / np.maximum(ln, 1e-12)[..., None]
    for i in range(P.shape[0]):
        for j in range(P.shape[1]):
            if bad[i, j]:
                k = i + 1 if i < P.shape[0] // 2 else i - 1
                n[i, j] = n[k, j]
    if float(np.mean(n.reshape(-1, 3) @ np.asarray(hint, float))) < 0:
        n = -n
    return n
