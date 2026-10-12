"""Lava Komodo parametric parts (pure numpy, deterministic): the closed pieces + the SDF / field helpers of the lava-komodo unit.

Every mesh generator returns V (n,3) float, F list of vertex-index lists (tris / quads), R list of per-face region names;
every part here is a CLOSED solid (0 open edges). The organic body (head, neck, chest, legs, haunches, tail, drips, the
lava pool) is ONE SDF in lava_komodo_build.py; this module carries the SDF primitives it needs plus the hard pieces:

    Part / open_edges / orient_outward / frame_from_axis / hash01    the firefly_parts idioms (via volcano_turtle_parts)
    sd_xfold            evaluate a +X primitive on (|X|, Y, Z): an exactly mirror-symmetric pair in one call
    sd_lobed_slab       the LAVA POOL: a flat slab on the floor whose outline is a smooth union of 2D ellipse lobes,
                        top edge rounded (the floor cut in the grid closes the bottom)
    sd_teardrop         a hanging lava DRIP: IQ round cone from a thin top (buried in the body) to a round bulb below
    poisson_seeds       greedy Poisson-disc seeds on surface sample points (variable spacing, deterministic order),
                        kept off the mirror plane: the ROCK PLATE cells
    plate_cells         per point: the nearest seed (the plate) and h = the distance to the nearest Voronoi bisector plane
                        (piecewise LINEAR per seed pair, so an iso-cut at h = w follows the crack band edges exactly)
    plate_frame         closed chamfered plate from a star-shaped 2D outline in a (u, v, n) frame: eye socket, iris,
                        pupil, glint, nostrils (copied from volcano_turtle_parts)
    ribbon              closed box-section strip along a surface polyline: the mouth line (copied)
    tube_tongue         closed lathe along a curved spine: one FLAME CREST tongue (copied; lobes = 0 keeps a tongue on
                        the mirror plane exactly symmetric)
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


def mirror_part(p, name):
    """the X = 0 mirror image of a part (faces reversed so the winding stays outward)"""
    V = p.V.copy()
    V[:, 0] *= -1.0
    q = Part(name, V, [f[::-1] for f in p.F], p.R, bone=p.bone)
    for k, v in p.__dict__.items():
        if k not in q.__dict__:
            setattr(q, k, v)
    return q


# --------------------------------------------------------------------------- SDF helpers
def sd_xfold(fn):
    """fn(P) defined for a +X primitive -> the same primitive mirrored onto -X too (exact union: evaluated on |X|)."""
    def g(P):
        Q = P.copy()
        Q[:, 0] = np.abs(Q[:, 0])
        return fn(Q)
    return g


def _smin(a, b, k):
    h = np.maximum(k - np.abs(a - b), 0.0) / k
    return np.minimum(a, b) - h * h * k * 0.25


def sd_lobed_slab(P, lobes, k2, top, edge_r):
    """Flat slab: 2D outline = smooth union (k2) of ellipse lobes (cx, cy, ax, ay), top face at z = top, the top rim rounded
    with radius edge_r; unbounded below (the grid's floor cut closes it at z = 0)."""
    d2 = None
    for cx, cy, ax, ay in lobes:
        q = np.hypot((P[:, 0] - cx) / ax, (P[:, 1] - cy) / ay)
        e = (q - 1.0) * min(ax, ay)
        d2 = e if d2 is None else _smin(d2, e, k2)
    a = d2 + edge_r
    b = P[:, 2] - top + edge_r
    out = np.hypot(np.maximum(a, 0.0), np.maximum(b, 0.0)) + np.minimum(np.maximum(a, b), 0.0)
    return out - edge_r


def sd_teardrop(P, top, bulb, r_top, r_bulb, round_cone):
    """a hanging drip: round cone from a thin top sphere (buried in the body) down to the bulb sphere"""
    return round_cone(P, top, bulb, r_top, r_bulb)


# --------------------------------------------------------------------------- rock plate cells
def poisson_seeds(points, spacing, order_seed=3.7, min_x_frac=0.40):
    """Greedy Poisson-disc seeds from candidate surface points (the +X half): candidates visited in a deterministic hash
    order; one is accepted when every accepted seed is at least max(spacing(p), spacing(q)) away and it sits at least
    min_x_frac x spacing off the mirror plane (a seed closer to X = 0 would make a sliver plate against its own mirror).
    -> (S (m,3) the +X seeds, Sp (m,) their spacings)"""
    P = np.asarray(points, float)
    sp = np.asarray(spacing, float)
    key = np.array([hash01(i, order_seed) for i in range(len(P))])
    order = np.argsort(key, kind="stable")
    acc, acc_s = [], []
    for i in order:
        p, s = P[i], sp[i]
        if p[0] < min_x_frac * s:
            continue
        if acc:
            A = np.asarray(acc)
            d = np.linalg.norm(A - p, axis=1)
            if np.any(d < np.maximum(np.asarray(acc_s), s)):
                continue
        acc.append(p); acc_s.append(s)
    return np.array(acc), np.array(acc_s)


def plate_cells(P, S, chunk=8000):
    """-> (cell (n,) nearest seed index, h (n,) distance to the nearest bisector plane of that seed with any other).
    h is exact per seed pair ((|p - sj|^2 - |p - si|^2) / (2 |sj - si|)), continuous across cells (0 on every bisector)."""
    P = np.asarray(P, float)
    S = np.asarray(S, float)
    SS = np.linalg.norm(S[:, None, :] - S[None, :, :], axis=2)
    np.fill_diagonal(SS, np.inf)
    s2 = (S * S).sum(1)
    cell = np.empty(len(P), dtype=np.int64)
    h = np.empty(len(P))
    for a in range(0, len(P), chunk):
        Q = P[a:a + chunk]
        D2 = (Q * Q).sum(1)[:, None] - 2.0 * Q @ S.T + s2[None, :]
        i1 = np.argmin(D2, 1)
        d1 = D2[np.arange(len(Q)), i1]
        H = (D2 - d1[:, None]) / (2.0 * SS[i1])
        H[np.arange(len(Q)), i1] = np.inf
        cell[a:a + chunk] = i1
        h[a:a + chunk] = np.maximum(H.min(1), 0.0)
    return cell, h


# --------------------------------------------------------------------------- plates (eye socket, iris, pupil, glint, nostrils)
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
    rectangle (+-w across, +front proud / -back sunk), w = half_w tapering to taper x half_w at both ends; quad end caps."""
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


# --------------------------------------------------------------------------- flame crest tongue
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
