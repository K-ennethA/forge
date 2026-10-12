"""Eye Totem parametric pieces (pure numpy, deterministic): the generators behind the ONE body SDF + its colour fields.

The unit has no closed hard-surface parts: the spire, the disc and the neck + droplet are smooth-unioned into a single
SDF shell (eye_totem_build.py); the eye (ring, pupil, the pupil's thin loop) and the red rim band are iso-cut into it.

    Part / open_edges     the firefly_parts idioms (kept so the build's open-edge table reads the same as every unit)
    sd_ellipsoid2         IQ's bound-correct ellipsoid with a separate top / bottom half-height (the disc / orb)
    sd_profile            an ELLIPTIC LATHE of a measured profile: per height z a half-width w(z) (front view), a
                          half-depth d(z) (side view) and a centre shift c(z) (the sheet's lean); exact 2D polygon
                          distance in the (q, z) plane with q = |(x - c, y * w / d)|. Used for the spire and the
                          neck + droplet, so the silhouette follows the sheet's row-scanned edges point for point.
    wobble_field          seeded smooth periodic noise in (z, theta): the spire's "slightly uneven edges"
    paint_field           seeded low-amplitude smooth 3D sinusoid noise: the matte painterly surface (normal map)
    oval_front            front-projected oval field (> 0 inside, y-independent) with a seeded radial wobble: the
                          rough, hand-drawn eye ring / pupil edges
    oval_band             front-projected thin band (> 0 within half-width of an oval curve): the pupil's drawn loop
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


# --------------------------------------------------------------------------- disc / orb
def sd_ellipsoid2(P, c, rx, ry, rz_top, rz_bot):
    Q = np.atleast_2d(P) - np.asarray(c, float)
    r = np.stack([np.full(len(Q), rx), np.full(len(Q), ry), np.where(Q[:, 2] > 0.0, rz_top, rz_bot)], 1)
    k0 = np.linalg.norm(Q / r, axis=1)
    k1 = np.linalg.norm(Q / (r * r), axis=1)
    return np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -min(rx, ry, rz_top, rz_bot))


# --------------------------------------------------------------------------- elliptic lathe of a measured profile
class Profile:
    """rows (z, x_right_edge, x_left_edge, half_depth) sorted by z: x edges in world X (front view), half-depth from
    the side view. The closed (q, z) polygon is the axis + the half-width polyline; the ends sit on the axis (w = 0
    rows) or are closed flat (the end buried inside the disc)."""

    def __init__(self, rows):
        R = np.asarray(sorted(rows), float)
        self.z = R[:, 0]
        self.c = 0.5 * (R[:, 1] + R[:, 2])
        self.w = 0.5 * (R[:, 2] - R[:, 1])
        self.d = R[:, 3]
        # (q, z) polygon: the axis bottom -> the profile up -> the axis top (closing segment runs down the axis)
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
    skip_axis: segments lying on q = 0 (the lathe axis) count for the inside test but are not surface."""
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


# --------------------------------------------------------------------------- seeded smooth noise
def wobble_terms(seed, n, wl_rng, m_rng):
    """n terms (frequency along z, integer angular harmonic, phase, weight) -> a smooth periodic field"""
    rng = np.random.default_rng(seed)
    f = 2.0 * math.pi / rng.uniform(wl_rng[0], wl_rng[1], n)
    m = rng.integers(m_rng[0], m_rng[1] + 1, n)
    ph = rng.uniform(0.0, 2.0 * math.pi, n)
    wt = rng.uniform(0.6, 1.0, n)
    wt = wt / math.sqrt(float((wt * wt).sum()) / 2.0)     # unit RMS of the sum
    return f, m, ph, wt


def wobble_field(z, th, terms):
    f, m, ph, wt = terms
    out = np.zeros(len(z))
    for fk, mk, pk, wk in zip(f, m, ph, wt):
        out += wk * np.sin(fk * z + mk * th + pk)
    return out


def paint_terms(seed, n, wl_rng):
    rng = np.random.default_rng(seed)
    D = rng.normal(0.0, 1.0, (n, 3)); D /= np.linalg.norm(D, axis=1, keepdims=True)
    k = 2.0 * math.pi / rng.uniform(wl_rng[0], wl_rng[1], n)
    ph = rng.uniform(0.0, 2.0 * math.pi, n)
    return D * k[:, None], ph, math.sqrt(2.0 / n)


def paint_field(P, terms):
    K, ph, norm = terms
    return norm * np.sin(np.atleast_2d(P) @ K.T + ph[None]).sum(1)


# --------------------------------------------------------------------------- eye fields (front-projected)
def ring_terms(seed, n, k_rng):
    """radial wobble of a hand-drawn ring edge: integer harmonics k (closed in theta), unit RMS"""
    rng = np.random.default_rng(seed)
    k = rng.integers(k_rng[0], k_rng[1] + 1, n)
    ph = rng.uniform(0.0, 2.0 * math.pi, n)
    wt = rng.uniform(0.5, 1.0, n) / np.sqrt(k / k_rng[0])
    wt = wt / math.sqrt(float((wt * wt).sum()) / 2.0)
    return k, ph, wt


def oval_front(P, cx, cz, a, b, amp=0.0, terms=None):
    """> 0 inside the oval (half-width a, half-height b) at (x cx, z cz) seen from the front (-Y), y-independent.
    The field ~ 2 (1 - rho) near the edge, so a radial wobble of amp (units) moves the edge by ~amp."""
    u = (P[:, 0] - cx) / a
    v = (P[:, 2] - cz) / b
    rho = np.sqrt(u * u + v * v)
    f = 1.0 - rho * rho
    if amp and terms is not None:
        th = np.arctan2(v, u)
        k, ph, wt = terms
        wob = np.zeros(len(P))
        for kk, pp, ww in zip(k, ph, wt):
            wob += ww * np.sin(kk * th + pp)
        f = f + 2.0 * amp / math.sqrt(a * b) * wob
    return f


def oval_band(P, cx, cz, a, b, half_w, amp=0.0, terms=None):
    """> 0 within half_w (units, approx. Euclidean) of the oval curve, front-projected; optional radial wobble"""
    u = (P[:, 0] - cx) / a
    v = (P[:, 2] - cz) / b
    rho = np.maximum(np.sqrt(u * u + v * v), 1e-9)
    g = np.sqrt((u / a) ** 2 + (v / b) ** 2) / rho          # |grad rho|
    dist = (rho - 1.0) / np.maximum(g, 1e-9)
    if amp and terms is not None:
        th = np.arctan2(v, u)
        k, ph, wt = terms
        for kk, pp, ww in zip(k, ph, wt):
            dist = dist + amp * ww * np.sin(kk * th + pp)
    return half_w - np.abs(dist)
