"""Volcano Turtle parametric parts (pure numpy, deterministic): the closed pieces + the SDF helpers of the volcano-turtle unit.

Every mesh generator returns V (n,3) float, F list of vertex-index lists (tris / quads), R list of per-face region names;
every part here is a CLOSED solid (0 open edges). The organic pieces (body, arms, shell) are SDFs in
volcano_turtle_build.py; this module carries the SDF primitives they need plus the hard pieces:

    Part / open_edges / orient_outward / frame_from_axis / hash01    the firefly_parts idioms (copied from ember_stump_parts)
    sd_capped_cone      exact capped cone on a vertical axis (IQ): the volcano crater
    sd_superellipsoid   rounded-box blob |x/a|^p + |y/b|^p + |z/c|^p = 1 (approximate distance): the oversized fists
    sd_chamfer_box      oriented box with chamfered edges + clipped corners (exact box, plane cuts): angular rock blocks
    plate_frame         closed chamfered plate from a star-shaped 2D outline in a (u, v, n) frame: eyes, sockets, the
                        rocky brow blocks, the nostrils (copied from ember_stump_parts)
    ribbon              closed box-section strip along a surface polyline (width + proud + sink, tapered ends): the mouth
    tube_tongue         closed lathe along a curved spine (poles at both ends, optional lobing): one flame tongue / flick
                        (copied from ember_stump_parts)
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


# --------------------------------------------------------------------------- SDF primitives
def sd_capped_cone(P, cx, cy, z0, z1, r0, r1):
    """Exact capped cone (IQ sdCappedCone) on the vertical axis through (cx, cy): bottom z0 radius r0, top z1 radius r1."""
    h = 0.5 * (z1 - z0)
    qx = np.hypot(P[:, 0] - cx, P[:, 1] - cy)
    qy = P[:, 2] - 0.5 * (z0 + z1)
    k1 = np.array([r1, h]); k2 = np.array([r1 - r0, 2.0 * h])
    cax = qx - np.minimum(qx, np.where(qy < 0.0, r0, r1))
    cay = np.abs(qy) - h
    t = np.clip(((k1[0] - qx) * k2[0] + (k1[1] - qy) * k2[1]) / float(k2 @ k2), 0.0, 1.0)
    cbx = qx - k1[0] + k2[0] * t
    cby = qy - k1[1] + k2[1] * t
    s = np.where((cbx < 0.0) & (cay < 0.0), -1.0, 1.0)
    return s * np.sqrt(np.minimum(cax * cax + cay * cay, cbx * cbx + cby * cby))


def sd_superellipsoid(P, c, radii, p):
    """|x/a|^p + |y/b|^p + |z/c|^p = 1 (p > 2 = a rounded box); distance ~ (f - 1) x min radius (sign-exact, slope ~1)."""
    Q = np.abs(P - np.asarray(c, float)) / np.asarray(radii, float)
    f = np.power(np.sum(np.power(Q, p), 1), 1.0 / p)
    return (f - 1.0) * float(np.min(radii))


def sd_chamfer_box(P, c, frame, half, chamfer, corner):
    """Oriented box (frame rows = local x, y, z world axes; half extents) with every edge chamfered by 'chamfer' and every
    corner clipped by 'corner' (both measured along the box axes): an angular rock block."""
    Q = (P - np.asarray(c, float)) @ np.asarray(frame, float).T
    A = np.abs(Q)
    h = np.asarray(half, float)
    q = A - h
    d = np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(1), 0.0)
    r2 = math.sqrt(2.0)
    for i, j in ((0, 1), (1, 2), (0, 2)):
        d = np.maximum(d, (A[:, i] + A[:, j] - (h[i] + h[j] - chamfer)) / r2)
    d = np.maximum(d, (A.sum(1) - (h.sum() - corner)) / math.sqrt(3.0))
    return d


# --------------------------------------------------------------------------- plates (eyes, sockets, brow blocks, nostrils)
def plate_frame(outline, origin, e_u, e_v, e_n, front, back, chamfer, region, side_region=None):
    """Closed plate from a star-shaped 2D outline (u, v offsets about origin) in the frame (e_u, e_v, e_n), e_n = the
    outward face normal: the back face at -back along e_n (buried), a straight side wall up to (front - chamfer), then a
    chamfer to the front face at +front inset by 'chamfer'. Caps are fans to the outline centroid."""
    o = np.asarray(origin, float)
    e_u, e_v, e_n = (np.asarray(x, float) for x in (e_u, e_v, e_n))
    outline = np.asarray(outline, float)
    n = len(outline)
    c2 = outline.mean(0)
    sz = np.linalg.norm(outline - c2, axis=1)
    shrink = np.clip(1.0 - chamfer / np.maximum(sz, 1e-9), 0.0, 1.0)
    inner = c2 + (outline - c2) * shrink[:, None]
    rings = [(outline, -back), (outline, front - chamfer), (inner, front)]
    V = []
    for pts, h in rings:
        for u, v in pts:
            V.append(o + e_u * u + e_v * v + e_n * h)
    pb = len(V); V.append(o + e_u * c2[0] + e_v * c2[1] - e_n * back)
    pf = len(V); V.append(o + e_u * c2[0] + e_v * c2[1] + e_n * front)
    F, R = [], []
    sreg = side_region or region
    for r_ in range(2):
        for i in range(n):
            i1 = (i + 1) % n
            F.append([r_ * n + i, r_ * n + i1, (r_ + 1) * n + i1, (r_ + 1) * n + i]); R.append(sreg if r_ == 0 else region)
    for i in range(n):
        i1 = (i + 1) % n
        F.append([pb, i1, i]); R.append(sreg)
        F.append([pf, 2 * n + i, 2 * n + i1]); R.append(region)
    V = np.array(V, float)
    F = orient_outward(V, F, o + e_u * c2[0] + e_v * c2[1] + e_n * (0.5 * (front - back)))
    return V, F, R


def circle(r, n, sx=1.0, sy=1.0):
    return np.array([(r * sx * math.cos(2 * math.pi * i / n), r * sy * math.sin(2 * math.pi * i / n)) for i in range(n)])


# --------------------------------------------------------------------------- mouth line
def ribbon(points, normals, half_w, front, back, region, taper=0.35):
    """Closed box-section strip along a polyline of SURFACE points with their outward normals: each section is a
    rectangle (+-w across, +front proud / -back sunk), w = half_w tapering to taper x half_w at both ends; quad end caps.
    -> V, F, R."""
    P = np.asarray(points, float)
    N = np.array([unit(n) for n in normals])
    T = np.gradient(P, axis=0)
    m = len(P)
    V = []
    for i in range(m):
        t = unit(T[i] - N[i] * float(T[i] @ N[i]))
        s = unit(np.cross(N[i], t))
        u = i / (m - 1)
        w = half_w * (taper + (1.0 - taper) * math.sin(math.pi * u) ** 0.5)
        V += [P[i] + s * w + N[i] * front, P[i] - s * w + N[i] * front, P[i] - s * w - N[i] * back, P[i] + s * w - N[i] * back]
    V = np.array(V)
    F = []
    for i in range(m - 1):
        a, b = 4 * i, 4 * (i + 1)
        for j in range(4):
            j1 = (j + 1) % 4
            F.append([a + j, a + j1, b + j1, b + j])
    F.append([0, 3, 2, 1])
    e = 4 * (m - 1)
    F.append([e, e + 1, e + 2, e + 3])
    F = orient_outward(V, F, P.mean(0) - N.mean(0) * 0.5 * (back - front))
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- flame tongue
def tube_tongue(root, axis, up, length, radius, ns, nseg, wave, phase, region, flat=1.0, wide_s=0.35, tip_pow=1.35,
                lobes=0, lobe_a=0.0):
    """Closed tongue: a lathe along the spine c(s) = root + length (s axis + wave sin(pi (1.4 s) + phase) s up_perp),
    radius r(s) = radius (s / wide_s)^0.7 below wide_s, radius ((1 - s) / (1 - wide_s))^tip_pow above (poles at both
    ends: a pointed root and a sharp licking tip). 'flat' squashes the section across the 'up' direction (1 = round);
    optional lobes ripple the section. -> V, F, R, spine."""
    root = np.asarray(root, float)
    a, u0, v0 = frame_from_axis(axis, up)
    s_ = np.linspace(0.0, 1.0, ns + 1)
    C = root[None] + length * (s_[:, None] * a[None]
                               + (wave * np.sin(math.pi * 1.4 * s_ + phase) * s_)[:, None] * u0[None])
    T = np.gradient(C, axis=0)
    T = T / np.linalg.norm(T, axis=1)[:, None]
    V = [C[0]]
    for k in range(1, ns):
        t_ = T[k]
        uu = unit(u0 - t_ * float(np.dot(u0, t_)))
        vv = np.cross(t_, uu)
        s = s_[k]
        r = radius * ((s / wide_s) ** 0.7 if s <= wide_s else ((1.0 - s) / (1.0 - wide_s)) ** tip_pow)
        for i in range(nseg):
            th = 2.0 * math.pi * i / nseg
            rm = 1.0 + (lobe_a * s * math.cos(lobes * th + phase + 3.0 * s) if lobes else 0.0)
            V.append(C[k] + r * rm * (uu * math.cos(th) + vv * math.sin(th) * flat))
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
