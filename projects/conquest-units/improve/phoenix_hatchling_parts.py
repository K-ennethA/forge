"""Phoenix Hatchling parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF shell, plus the
SDF helpers the build's one body grid needs.

Every generator returns (V (n,3) float, F list of vertex-index lists, R list of per-face region names). Closed solids are
surfaces of revolution with poles on the axis or tori, so every part has 0 open edges.

    Part / open_edges     the firefly_parts idioms (kept so the open-edge table reads the same as every unit)
    lathe / torus         copied from firefly_parts (profile revolved about an axis, ends on the axis become poles; ring)
    ellipsoid_dome        closed flattened ellipsoid (eyes, eye highlights): semi-axes (half-height, half-width, depth)
    bipyramid             closed 4-sided diamond (the chest EMBER JEWEL + its inner core)
    taper                 closed tapered tube along a segment (beak, toes, legs): rounded root pole -> pointed / rounded tip
    teardrop              closed small flame fleck (embers)
    blob                  closed irregular low-poly fleck (ash), seeded radius jitter
    sd_ell2               two-half-height ellipsoid SDF (bombee / soot_puff ball idiom: fuller top or bottom)
    sd_oval_torus         elliptical-section torus on an oval ring (the smoke collar base)
    sd_torus              round torus about an arbitrary axis (crest swirl curls)
    torus_puffs           seeded rounded puffs on the collar torus surface (the soot_puff fuzz idiom: blunt, short, varied,
                          smooth-unioned deep -> clouds, never pinecones)
    sd_spheres_min        min distance to a set of spheres, chunked (region fields on the low shell)
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    """-> (a, u, v): a = axis, u = the up_hint made perpendicular (the local 'up' of an oval), v = a x u."""
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


# --------------------------------------------------------------------------- lathe / torus (firefly_parts, verbatim)
def lathe(profile, seg_regions, n, centre, axis, up_hint=(0, 0, 1), su=1.0, sv=1.0, rmod=None, phase=0.0):
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    th = phase + 2 * math.pi * np.arange(n) / n
    V, F, R, rings = [], [], [], []
    for k, (r, h) in enumerate(profile):
        if r <= 1e-9:
            rings.append([len(V)]); V.append(c + a * h)
            continue
        idx = []
        for t in th:
            m = rmod(k, t) if rmod is not None else 1.0
            idx.append(len(V))
            V.append(c + a * h + r * m * (math.cos(t) * su * u + math.sin(t) * sv * v))
        rings.append(idx)
    for k in range(len(profile) - 1):
        A, B = rings[k], rings[k + 1]
        reg = seg_regions[k]
        if len(A) == 1 and len(B) == 1:
            continue
        if len(A) == 1:
            for i in range(n):
                F.append([A[0], B[(i + 1) % n], B[i]]); R.append(reg)
        elif len(B) == 1:
            for i in range(n):
                F.append([A[i], A[(i + 1) % n], B[0]]); R.append(reg)
        else:
            for i in range(n):
                F.append([A[i], A[(i + 1) % n], B[(i + 1) % n], B[i]]); R.append(reg)
    V = np.array(V)
    F = orient_outward(V, F, c + a * np.mean([p[1] for p in profile]))
    return V, F, R


def orient_outward(V, F, inside_pt):
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def torus(R0, r, n_major, n_minor, centre, axis, up_hint=(0, 0, 1), su=1.0, sv=1.0, region="rim"):
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    V, F = [], []
    for i in range(n_major):
        t = 2 * math.pi * i / n_major
        radial = unit(math.cos(t) * su * u + math.sin(t) * sv * v)
        ring_c = c + R0 * (math.cos(t) * su * u + math.sin(t) * sv * v)
        for j in range(n_minor):
            p = 2 * math.pi * j / n_minor
            V.append(ring_c + r * (math.cos(p) * radial + math.sin(p) * a))
    for i in range(n_major):
        for j in range(n_minor):
            i1, j1 = (i + 1) % n_major, (j + 1) % n_minor
            F.append([i * n_minor + j, i1 * n_minor + j, i1 * n_minor + j1, i * n_minor + j1])
    V = np.array(V)
    f0 = F[0]
    nrm = np.cross(V[f0[1]] - V[f0[0]], V[f0[2]] - V[f0[0]])
    tube_c = c + R0 * (su * u)
    if np.dot(nrm, V[f0[0]] - tube_c) < 0:
        F = [f[::-1] for f in F]
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- the unit's closed parts
def ellipsoid_dome(centre, axis, half_h, half_w, depth, n=24, rings=8, region="eye", up_hint=(0, 0, 1)):
    """closed ellipsoid: semi-axis depth along `axis` (the dome's front pole), half_h along the local up, half_w across.
    Sunk into a surface it reads as a proud inset dome (the eyes, the highlight dots)."""
    prof = [(math.sin(t), depth * math.cos(t)) for t in np.linspace(0.0, math.pi, rings + 1)]
    prof[0] = (0.0, depth); prof[-1] = (0.0, -depth)
    return lathe(prof, [region] * rings, n, centre, axis, up_hint, su=half_h, sv=half_w)


def bipyramid(centre, axis, half_h, half_w, front, back, region="jewel", up_hint=(0, 0, 1)):
    """closed diamond: 4 rim points (up / side / down / side) + a front and a back apex on the axis (8 tris)"""
    return lathe([(0.0, front), (1.0, 0.0), (0.0, -back)], [region, region], 4, centre, axis, up_hint,
                 su=half_h, sv=half_w)


def taper(a, b, r_root, profile, n=10, region="leg", up_hint=(0, 0, 1), su=1.0, sv=1.0, root_cap=None):
    """closed tapered tube from a (root) to b (tip). profile: [(s, rel_r), ...] with s in [0, 1] along a -> b; a pole is
    added before the root (root_cap: how far behind a, default r_root) and at the tip (s = 1 must have rel_r 0)."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    L = float(np.linalg.norm(b - a))
    cap = r_root if root_cap is None else root_cap
    prof = [(0.0, -cap)] + [(r_root * rr, s * L) for s, rr in profile]
    if prof[-1][0] > 1e-9:
        prof.append((0.0, L + r_root * profile[-1][1]))
    return lathe(prof, [region] * (len(prof) - 1), n, a, b - a, up_hint, su=su, sv=sv)


def teardrop(centre, axis, length, width, n=6, region="ember"):
    """closed flame fleck: a rounded belly below, a pointed tip along +axis"""
    prof = [(0.0, 0.55 * length), (0.45 * width, 0.25 * length), (width, -0.05 * length), (0.75 * width, -0.28 * length),
            (0.0, -0.45 * length)]
    return lathe(prof, [region] * 4, n, centre, axis, (0, 0, 1) if abs(unit(axis)[2]) < 0.95 else (1, 0, 0))


def blob(centre, size, seed, n=6, region="ash"):
    """closed irregular fleck (ash): an ellipsoid of half-sizes size = (sx, sy, sz) with a seeded radius jitter per
    vertex (0.72 .. 1.18), deterministic"""
    sx, sy, sz = size
    prof = [(0.0, sz)] + [(math.sin(t), sz * math.cos(t)) for t in (math.pi / 4, math.pi / 2, 3 * math.pi / 4)] + [(0.0, -sz)]

    def rmod(k, t):
        return 0.72 + 0.46 * hash01(k * 17 + int(round(t * 100)), seed)
    return lathe(prof, [region] * 4, n, centre, (0, 0, 1), (0, 1, 0), su=sy, sv=sx, rmod=rmod, phase=0.3 * seed)


# --------------------------------------------------------------------------- SDF helpers
def sd_ell2(P, c, rxy, rz_top, rz_bot):
    """IQ's bound-correct ellipsoid with a top / bottom half-height (bombee / soot_puff ball)"""
    Q = np.atleast_2d(P) - np.asarray(c, float)
    r = np.stack([np.full(len(Q), rxy[0]), np.full(len(Q), rxy[1]), np.where(Q[:, 2] > 0.0, rz_top, rz_bot)], 1)
    k0 = np.linalg.norm(Q / r, axis=1)
    k1 = np.linalg.norm(Q / (r * r), axis=1)
    return np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -min(list(rxy) + [rz_top, rz_bot]))


def ell2_hit(c, rxy, rz_top, rz_bot, d):
    """surface point + outward normal of the sd_ell2 ellipsoid along the ray c + t d (vectorised over d (n, 3))"""
    d = np.atleast_2d(np.asarray(d, float))
    d = d / np.linalg.norm(d, axis=1, keepdims=True)
    rz = np.where(d[:, 2] > 0.0, rz_top, rz_bot)
    r = np.stack([np.full(len(d), rxy[0]), np.full(len(d), rxy[1]), rz], 1)
    t = 1.0 / np.sqrt(((d / r) ** 2).sum(1))
    S = np.asarray(c, float) + d * t[:, None]
    n = (S - c) / (r * r)
    return S, n / np.linalg.norm(n, axis=1, keepdims=True)


def ell2_at(c, rxy, rz_top, rz_bot, x, z, front=True):
    """the surface point at (x, z) on the front (-Y) or back (+Y) of the ellipsoid, and its outward normal"""
    c = np.asarray(c, float)
    rz = rz_top if z > c[2] else rz_bot
    s = 1.0 - (x - c[0]) ** 2 / rxy[0] ** 2 - (z - c[2]) ** 2 / rz ** 2
    y = c[1] + (-1.0 if front else 1.0) * rxy[1] * math.sqrt(max(s, 0.0))
    p = np.array([x, y, z])
    n = (p - c) / np.array([rxy[0] ** 2, rxy[1] ** 2, rz ** 2])
    return p, unit(n)


def taper_b(b, t, th):
    """vertical half-size tapered with the ring angle th (0 = +X): b at the sides (|X| max), t * b at the front / back
    centre line (X = 0); smooth: b * (t + (1 - t) * cos^2 th)"""
    return b * (t + (1.0 - t) * np.cos(th) ** 2)


def sd_oval_torus(P, c, R0x, sy, a, b, t=1.0):
    """elliptical-section torus: ring of radius R0x in X (R0x * sy in Y) about the vertical through c, tube half-sizes a
    (radial) x b (vertical); with t < 1 the vertical half-size tapers with azimuth (taper_b). Approximate (scaled) distance:
    the zero set is exact, the gradient within ~1.2x (/ t at the centre line)."""
    Q = np.atleast_2d(P) - np.asarray(c, float)
    rho = np.sqrt(Q[:, 0] ** 2 + (Q[:, 1] / sy) ** 2) - R0x
    bb = taper_b(b, t, np.arctan2(Q[:, 1] / sy, Q[:, 0]))
    k = np.sqrt((rho / a) ** 2 + (Q[:, 2] / bb) ** 2)
    return (k - 1.0) * np.minimum(a, bb)


def oval_torus_point(c, R0x, sy, a, b, th, ph, t=1.0):
    """surface point + normal on sd_oval_torus at ring angle th (0 = +X, 90 = +Y) and tube angle ph (0 = out, 90 = up)"""
    c = np.asarray(c, float)
    bb = taper_b(b, t, th)
    rad = np.stack([np.cos(th), np.sin(th) * sy, np.zeros_like(th)], 1)
    rad_n = rad / np.linalg.norm(rad, axis=1, keepdims=True)
    ring = c + R0x * rad
    S = ring + rad_n * (a * np.cos(ph))[:, None] + np.outer(bb * np.sin(ph), [0.0, 0.0, 1.0])
    N = rad_n * (np.cos(ph) / a)[:, None] + (np.sin(ph) / bb)[:, None] * np.array([0.0, 0.0, 1.0])
    return S, N / np.linalg.norm(N, axis=1, keepdims=True)


def sd_torus(P, c, axis, R0, r):
    a = unit(axis)
    Q = np.atleast_2d(P) - np.asarray(c, float)
    h = Q @ a
    rho = np.linalg.norm(Q - np.outer(h, a), axis=1)
    return np.sqrt((rho - R0) ** 2 + h ** 2) - r


def torus_puffs(c, R0x, sy, a, b, n, r_rng, h_rng, ph_rng, seed, t=1.0):
    """-> dict(C, R, H, S, N): n puffs on the collar torus, ring angle uniform (jittered), tube angle in ph_rng (deg; the
    inner side under the head is skipped). Each puff is a sphere of radius r standing h proud of the base surface. With a
    tapered tube (t < 1) radius and protrusion scale with the local vertical half-size (smaller puffs where it is thin)."""
    rng = np.random.default_rng(seed)
    k = np.arange(n) + 0.5
    th = 2 * math.pi * k / n + rng.uniform(-0.5, 0.5, n) * 2 * math.pi / n
    ph = np.radians(rng.uniform(ph_rng[0], ph_rng[1], n))
    S, N = oval_torus_point(c, R0x, sy, a, b, th, ph, t)
    sc = taper_b(1.0, t, th)
    R = rng.uniform(r_rng[0], r_rng[1], n)
    H = rng.uniform(h_rng[0], h_rng[1], n) * (R / r_rng[1]) ** 0.5
    R = R * sc; H = H * sc
    C = S + N * (H - R)[:, None]
    return {"C": C, "R": R, "H": H, "S": S, "N": N, "u": rng.uniform(0.0, 1.0, n), "scale": sc}


def sd_spheres_min(P, C, R, chunk=256):
    P = np.asarray(P, float)
    out = np.full(len(P), 1e9)
    for i in range(0, len(C), chunk):
        Cc, Rc = C[i:i + chunk], R[i:i + chunk]
        d = np.sqrt(((P[:, None, :] - Cc[None]) ** 2).sum(2)) - Rc[None]
        out = np.minimum(out, d.min(1))
    return out
