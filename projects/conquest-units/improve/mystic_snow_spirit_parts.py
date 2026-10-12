"""Mystic Snow Spirit parametric parts (pure numpy, deterministic): the closed pieces of the snowman unit.

Every mesh generator returns V (n,3) float, F list of vertex-index lists (tris / quads), R list of per-face region names;
every part here is a CLOSED solid (0 open edges). The organic body (head, middle ball, snow-mound skirt) is ONE SDF in
mystic_snow_spirit_build.py; this module carries the hard pieces:

    Part / open_edges / orient_outward / frame_from_axis / hash01 / ell_point   the firefly_parts idioms
    ellipsoid       low-poly ellipsoid (poles on a local axis): the floating snowballs, the snow clumps on the wood
    branch          closed tapered branch along a Catmull-Rom spine (rounded caps, gentle knot bulges): the twig horns,
                    their twigs, the arm branches and the twig fingers
    snow_clump      a lumpy snow accent (two overlapping flattened ellipsoids) resting on the upper side of a branch
    conform_dome    a closed dome whose rings CONFORM to an ellipsoid surface (projected radially), concentric elliptic
                    rings of any aspect -> one smooth surface with exact region boundaries: the oval eyes (rim / iris /
                    pupil) and the "o" mouth (no layered decals, no z-fight)
    lick            closed flame lick: a lathe along a bent spine (pointed root buried in the snowball, rounded belly,
                    sharp licking tip), optionally flattened: one tongue of a snowball's mystical flame
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    """-> (a, u, v): a = axis, u = the up_hint made perpendicular, v = a x u."""
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


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


def orient_outward(V, F, inside_pt):
    """Flip the whole face list if the signed volume about inside_pt is negative."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def ell_point(c, radii, d):
    """The point of the axis-aligned ellipsoid (c, radii) on the ray from c along d, and the outward normal there."""
    c = np.asarray(c, float); r = np.asarray(radii, float); d = unit(d)
    t = 1.0 / np.linalg.norm(d / r)
    S = c + d * t
    return S, unit((S - c) / (r * r))


# --------------------------------------------------------------------------- ellipsoid
def ellipsoid(c, radii, n_seg, n_ring, region, frame=None):
    """Low-poly ellipsoid (poles on local z). frame = (ex, ey, ez) rows, default world axes."""
    c = np.asarray(c, float)
    ex, ey, ez = (np.eye(3) if frame is None else np.asarray(frame, float))
    V = [c - ez * radii[2]]
    for j in range(1, n_ring):
        ph = -math.pi / 2 + math.pi * j / n_ring
        for i in range(n_seg):
            t = 2 * math.pi * i / n_seg
            V.append(c + ex * radii[0] * math.cos(ph) * math.cos(t) + ey * radii[1] * math.cos(ph) * math.sin(t)
                     + ez * radii[2] * math.sin(ph))
    V.append(c + ez * radii[2])
    V = np.array(V)
    F = []
    for i in range(n_seg):
        F.append([0, 1 + (i + 1) % n_seg, 1 + i])
    for j in range(n_ring - 2):
        a0, b0 = 1 + j * n_seg, 1 + (j + 1) * n_seg
        for i in range(n_seg):
            i1 = (i + 1) % n_seg
            F.append([a0 + i, a0 + i1, b0 + i1, b0 + i])
    top = len(V) - 1; a0 = 1 + (n_ring - 2) * n_seg
    for i in range(n_seg):
        F.append([a0 + i, a0 + (i + 1) % n_seg, top])
    F = orient_outward(V, F, c)
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- spine helpers
def catmull(points, n_per):
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


def arclen(C):
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])


def resample(C, n):
    s = arclen(C)
    t = np.linspace(0.0, s[-1], n)
    return np.stack([np.interp(t, s, C[:, k]) for k in range(3)], 1)


def transport_frames(C, up_hint=(0.0, 0.0, 1.0)):
    """tangents + a parallel-transported normal along the polyline (no twist flips)"""
    T = np.gradient(C, axis=0)
    T = T / np.linalg.norm(T, axis=1)[:, None]
    _, u0, _ = frame_from_axis(T[0], up_hint)
    U = [u0]
    for k in range(1, len(C)):
        u = U[-1] - T[k] * float(np.dot(U[-1], T[k]))
        if np.linalg.norm(u) < 1e-8:
            u = frame_from_axis(T[k], up_hint)[1]
        U.append(unit(u))
    U = np.array(U)
    W = np.cross(T, U)
    return T, U, W


def tube_on_rings(C, T, U, W, radius, nseg, region, rmod=None, cap_k=0.55):
    """closed tube: one ring per spine station (radius[k], optional rmod(k, theta) multiplier), each end closed by a
    rounded cap (a shrunken ring cap_k x r beyond the end + a pole). -> V, F, R."""
    ns = len(C)
    V = []
    th = 2.0 * math.pi * np.arange(nseg) / nseg
    # start cap
    V.append(C[0] - T[0] * radius[0] * 0.95)
    for i in range(nseg):
        V.append(C[0] - T[0] * radius[0] * 0.62 + (U[0] * math.cos(th[i]) + W[0] * math.sin(th[i])) * radius[0] * 0.78)
    for k in range(ns):
        for i in range(nseg):
            m = rmod(k, th[i]) if rmod else 1.0
            V.append(C[k] + (U[k] * math.cos(th[i]) + W[k] * math.sin(th[i])) * radius[k] * m)
    for i in range(nseg):
        V.append(C[-1] + T[-1] * radius[-1] * 0.62 + (U[-1] * math.cos(th[i]) + W[-1] * math.sin(th[i])) * radius[-1] * 0.78)
    V.append(C[-1] + T[-1] * radius[-1] * 0.95)
    V = np.array(V)
    nr = ns + 2
    ring = lambda r_, i: 1 + r_ * nseg + (i % nseg)
    F = []
    for i in range(nseg):
        F.append([0, ring(0, i + 1), ring(0, i)])
    for r_ in range(nr - 1):
        for i in range(nseg):
            F.append([ring(r_, i), ring(r_, i + 1), ring(r_ + 1, i + 1), ring(r_ + 1, i)])
    top = len(V) - 1
    for i in range(nseg):
        F.append([ring(nr - 1, i), ring(nr - 1, i + 1), top])
    F = orient_outward(V, F, C[ns // 2])
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- branches (horns, twigs, arms, fingers)
def branch(points, radii, n_st, nseg, region, knots=(), seed=0.0, wobble=0.06):
    """Closed tapered branch through the control points (Catmull-Rom, resampled to n_st even stations); radius
    interpolated from radii (one per control point, by arc fraction); knots = [(s, amp, width)] gentle bulges; a
    seeded low-frequency section wobble (+- wobble, 2 lobes) keeps it woody, never ribbed. -> V, F, R, spine C, arc s."""
    D = catmull(points, 10)
    C = resample(D, n_st)
    s_c = arclen(np.asarray(points, float)); s_c = s_c / s_c[-1]
    s = arclen(C); L = s[-1]; s = s / L
    r = np.interp(s, s_c, np.asarray(radii, float))
    for s0, amp, w in knots:
        r = r * (1.0 + amp * np.exp(-((s - s0) / w) ** 2))
    T, U, W = transport_frames(C)
    ph = 2.0 * math.pi * hash01(3, seed)

    def rmod(k, t):
        return 1.0 + wobble * math.sin(2.0 * t + ph + 3.0 * s[k]) * math.sin(math.pi * min(1.0, 4.0 * s[k]))
    V, F, R = tube_on_rings(C, T, U, W, r, nseg, region, rmod)
    return V, F, R, C, s * L


def snow_clump(C, s_arc, s_at, r_branch, length, height, region="snow", up=(0.0, 0.0, 1.0), seed=0.0, n_seg=10, n_ring=5):
    """lumpy snow accent on the UPPER side of a branch at arc s_at: two overlapping flattened ellipsoids, long axis
    along the branch, resting on top (half sunk into the wood). -> list of (V, F, R)."""
    k = int(np.clip(np.searchsorted(s_arc, s_at), 1, len(C) - 2))
    t = unit(C[k + 1] - C[k - 1])
    upv = np.asarray(up, float) - t * float(np.dot(up, t))
    if np.linalg.norm(upv) < 1e-6:
        upv = frame_from_axis(t)[1]
    upv = unit(upv)
    side = np.cross(t, upv)
    out = []
    for j, (off, sc) in enumerate(((-0.25, 1.0), (0.30, 0.72))):
        h = hash01(j, seed)
        c = C[k] + upv * (r_branch * 0.55 + height * 0.25) + t * length * off + side * r_branch * 0.15 * (h - 0.5)
        rad = (length * 0.55 * sc, r_branch * (1.25 + 0.15 * h) * sc, height * sc)
        out.append(ellipsoid(c, rad, n_seg, n_ring, region, frame=(t, side, upv)))
    return out


# --------------------------------------------------------------------------- conforming domes (eyes, mouth)
def conform_dome(ell_c, ell_r, S, e_u, e_v, rings, depth, nseg, lift=0.004, sink=0.04):
    """Closed dome on the ellipsoid (ell_c, ell_r) centred at its surface point S: e_u / e_v span the tangent plane.
    rings = [(hu, hv, region_inside), ...] from the centre out (elliptic rings, any aspect; the last is the outline).
    Each ring point is projected radially onto the ellipsoid, then lifted along the surface normal by
    lift + depth sqrt(1 - rho^2) (rho = the ring's size relative to the outline): one smooth surface, exact region
    edges. The base (outline pushed 'sink' inward + a back pole) closes it inside the head. -> V, F, R."""
    c = np.asarray(ell_c, float); r = np.asarray(ell_r, float)
    S = np.asarray(S, float); e_u = unit(e_u); e_v = unit(e_v)
    HU, HV = rings[-1][0], rings[-1][1]
    th = 2.0 * math.pi * np.arange(nseg) / nseg

    def on_surf(T_, h):
        P_, n_ = ell_point(c, r, T_ - c)
        return P_ + n_ * h

    S0, n0 = ell_point(c, r, S - c)
    V = [S0 + n0 * (lift + depth)]
    for hu, hv, _ in rings:
        rho = 0.5 * (hu / HU + hv / HV)
        h = lift + depth * math.sqrt(max(0.0, 1.0 - rho * rho))
        for t in th:
            V.append(on_surf(S0 + e_u * hu * math.cos(t) + e_v * hv * math.sin(t), h))
    nr = len(rings)
    for t in th:
        V.append(on_surf(S0 + e_u * HU * math.cos(t) + e_v * HV * math.sin(t), -sink))
    V.append(S0 - n0 * sink * 1.5)
    V = np.array(V)
    ring = lambda j, i: 1 + j * nseg + (i % nseg)
    F, R = [], []
    for i in range(nseg):
        F.append([0, ring(0, i), ring(0, i + 1)]); R.append(rings[0][2])
    for j in range(nr - 1):
        for i in range(nseg):
            F.append([ring(j, i), ring(j + 1, i), ring(j + 1, i + 1), ring(j, i + 1)]); R.append(rings[j + 1][2])
    for i in range(nseg):                                                    # outline -> buried base ring (side wall)
        F.append([ring(nr - 1, i), ring(nr, i), ring(nr, i + 1), ring(nr - 1, i + 1)]); R.append(rings[-1][2])
    bp = len(V) - 1
    for i in range(nseg):
        F.append([ring(nr, i), bp, ring(nr, i + 1)]); R.append(rings[-1][2])
    F = orient_outward(V, F, S0 - n0 * sink * 0.5)
    return V, F, R


# --------------------------------------------------------------------------- flame body (the mass that engulfs a snowball)
def flame_body(c, a, e1, profile, regions, nseg, lobes, lobe_a):
    """Closed lathe about the axis a through c: profile [(h, r)] from the bottom pole (r 0) to the tip pole (r 0); the
    ring i -> i+1 band takes regions[i]. Lobes: r x (1 + lobe_a s cos(lobes theta)), s = the ring's height share (theta
    from e1; an EVEN lobe count keeps a lathe whose e1 is +X mirror-symmetric about X = 0). -> V, F, R."""
    c = np.asarray(c, float); a = unit(a); e1 = unit(np.asarray(e1, float) - a * float(np.dot(e1, a)))
    e2 = np.cross(a, e1)
    h0, h1 = profile[0][0], profile[-1][0]
    th = 2.0 * math.pi * np.arange(nseg) / nseg
    V = [c + a * profile[0][0]]
    for h, r in profile[1:-1]:
        s = (h - h0) / (h1 - h0)
        for t in th:
            m = 1.0 + lobe_a * s * math.cos(lobes * t)
            V.append(c + a * h + (e1 * math.cos(t) + e2 * math.sin(t)) * r * m)
    V.append(c + a * profile[-1][0])
    V = np.array(V)
    nr = len(profile) - 2
    ring = lambda j, i: 1 + j * nseg + (i % nseg)
    F, R = [], []
    for i in range(nseg):
        F.append([0, ring(0, i + 1), ring(0, i)]); R.append(regions[0])
    for j in range(nr - 1):
        for i in range(nseg):
            F.append([ring(j, i), ring(j, i + 1), ring(j + 1, i + 1), ring(j + 1, i)]); R.append(regions[j + 1])
    top = len(V) - 1
    for i in range(nseg):
        F.append([ring(nr - 1, i), ring(nr - 1, i + 1), top]); R.append(regions[-1])
    F = orient_outward(V, F, c + a * 0.5 * (h0 + h1))
    return V, F, R


# --------------------------------------------------------------------------- flame licks
def lick(root, direction, bend, length, radius, ns, nseg, region, flat=1.0, flat_axis=None, wide_s=0.30, tip_pow=1.4,
         root_pow=0.6):
    """Closed flame lick: spine c(s) = root + length (s d + s^2 bend) (bend = a vector: the curl toward the flame
    axis); radius r(s) = radius (s / wide_s)^root_pow below wide_s, radius ((1 - s) / (1 - wide_s))^tip_pow above
    (poles at both ends: a pointed root buried in the snowball, a sharp licking tip). flat squashes the section along
    flat_axis (made perpendicular to the spine). -> V, F, R, spine."""
    root = np.asarray(root, float)
    d = unit(direction)
    bend = np.asarray(bend, float)
    s_ = np.linspace(0.0, 1.0, ns + 1)
    C = root[None] + length * (s_[:, None] * d[None] + (s_ ** 2)[:, None] * bend[None])
    T = np.gradient(C, axis=0)
    T = T / np.linalg.norm(T, axis=1)[:, None]
    fa = np.asarray(flat_axis if flat_axis is not None else frame_from_axis(d)[1], float)
    V = [C[0]]
    for k in range(1, ns):
        t_ = T[k]
        uu = unit(fa - t_ * float(np.dot(fa, t_)))
        vv = np.cross(t_, uu)
        s = s_[k]
        r = radius * ((s / wide_s) ** root_pow if s <= wide_s else ((1.0 - s) / (1.0 - wide_s)) ** tip_pow)
        for i in range(nseg):
            th = 2.0 * math.pi * i / nseg
            V.append(C[k] + r * (uu * math.cos(th) * flat + vv * math.sin(th)))
    V.append(C[-1])
    V = np.array(V)
    tip = len(V) - 1
    ring = lambda k, i: 1 + (k - 1) * nseg + (i % nseg)
    F = []
    for i in range(nseg):
        F.append([0, ring(1, i + 1), ring(1, i)])
    for k in range(1, ns - 1):
        for i in range(nseg):
            F.append([ring(k, i), ring(k, i + 1), ring(k + 1, i + 1), ring(k + 1, i)])
    for i in range(nseg):
        F.append([ring(ns - 1, i), ring(ns - 1, i + 1), tip])
    F = orient_outward(V, F, C[ns // 3])
    return V, F, [region] * len(F), C
