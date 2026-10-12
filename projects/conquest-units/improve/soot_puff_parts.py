"""Soot Puff parametric pieces (pure numpy, deterministic): the generators behind the ONE body SDF.

The unit has no closed hard-surface parts: the puffball body, both dangling arms and every fuzz puff are smooth-unioned
into a single SDF shell (soot_puff_build.py), and the face (two eyes + the mouth) is iso-cut into that shell. This module
holds the unit's own generators so the build stays a pipeline:

    Part / open_edges     the firefly_parts idioms (kept so the build's open-edge table reads the same as every unit)
    ellipsoid_hit         ray from the ball centre -> the base ellipsoid's surface point + outward normal (fuller-top /
                          fuller-bottom half heights, like bombee's ball)
    fib_dirs              seeded, jittered Fibonacci directions: an even scatter over the sphere, deterministic
    ball_puffs            the body's FUZZ: overlapping rounded puffs (sphere centre, radius, protrusion) scattered on
                          the base ellipsoid; each puff stands proud of the surface by its protrusion h. The sheet's
                          "irregular, painterly, airy" rim is a scalloped ring of these (contract hazard note: clouds =
                          overlapping rounded bulges; blunt, short, varied, smooth-unioned deep -> soft, never cones)
    arm_puffs             the same, scattered on a round-cone arm surface (smaller: the arm's fuzzy edge)
    sd_spheres_min        min distance to a set of spheres, chunked (region fields on the low shell)
    ellipse_front         front-projected oval field (> 0 inside): the face features, exactly as the front view draws them
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


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


# --------------------------------------------------------------------------- base ellipsoid
def ellipsoid_hit(c, rxy, rz_top, rz_bot, d):
    """surface point of the ellipsoid (centre c, half-widths rxy = (rx, ry), half-height rz_top above the centre and
    rz_bot below it) along the ray c + t d, plus its outward unit normal. Vectorised over d (n, 3)."""
    d = np.atleast_2d(np.asarray(d, float))
    d = d / np.linalg.norm(d, axis=1, keepdims=True)
    rz = np.where(d[:, 2] > 0.0, rz_top, rz_bot)
    r = np.stack([np.full(len(d), rxy[0]), np.full(len(d), rxy[1]), rz], 1)
    t = 1.0 / np.sqrt(((d / r) ** 2).sum(1))
    S = np.asarray(c, float) + d * t[:, None]
    n = (S - c) / (r * r)
    n = n / np.linalg.norm(n, axis=1, keepdims=True)
    return S, n


def fib_dirs(n, seed, jitter):
    """n Fibonacci-sphere directions, each nudged by a seeded tangent jitter (fraction of the mean spacing)."""
    rng = np.random.default_rng(seed)
    k = np.arange(n) + 0.5
    z = 1.0 - 2.0 * k / n
    th = math.pi * (1.0 + 5.0 ** 0.5) * k
    rho = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    D = np.stack([rho * np.cos(th), rho * np.sin(th), z], 1)
    sp = math.sqrt(4.0 * math.pi / n)
    J = rng.normal(0.0, 1.0, (n, 3)) * sp * jitter
    J -= D * (J * D).sum(1, keepdims=True)
    D = D + J
    return D / np.linalg.norm(D, axis=1, keepdims=True), rng


def ball_puffs(c, rxy, rz_top, rz_bot, n, r_rng, h_rng, seed, jitter=0.35):
    """-> dict(C centres, R radii, H protrusions, S surface points, N normals). Each puff is a sphere of radius r whose
    centre sits at S + N (h - r): it stands h proud of the base surface (a rounded scallop on the silhouette)."""
    D, rng = fib_dirs(n, seed, jitter)
    S, N = ellipsoid_hit(c, rxy, rz_top, rz_bot, D)
    R = rng.uniform(r_rng[0], r_rng[1], n)
    H = rng.uniform(h_rng[0], h_rng[1], n) * (R / r_rng[1]) ** 0.5     # bigger puffs stand a little prouder
    C = S + N * (H - R)[:, None]
    return {"C": C, "R": R, "H": H, "S": S, "N": N, "u": rng.uniform(0.0, 1.0, n)}


def arm_puffs(a, b, ra, rb, n, r_rng, h_rng, seed, s_rng=(0.0, 1.0)):
    """puffs scattered on the round-cone arm a(ra) -> b(rb): a golden-angle spiral along the axis between the fractions
    s_rng (the part below the ball) plus the rounded tip cap. Same dict as ball_puffs."""
    rng = np.random.default_rng(seed)
    a = np.asarray(a, float); b = np.asarray(b, float)
    ax = unit(b - a)
    u = unit(np.cross(ax, [0.0, 1.0, 0.0]) if abs(ax[1]) < 0.9 else np.cross(ax, [1.0, 0.0, 0.0]))
    v = np.cross(ax, u)
    L = float(np.linalg.norm(b - a))
    k = np.arange(n) + 0.5
    s = s_rng[0] + (s_rng[1] - s_rng[0]) * k / n + rng.uniform(-0.5, 0.5, n) / n
    th = 2.399963 * k + rng.uniform(-0.4, 0.4, n)
    rad = ra + (rb - ra) * np.clip(s, 0.0, 1.0)
    S = a + np.outer(np.clip(s, 0.0, 1.0) * L, ax) + rad[:, None] * (np.outer(np.cos(th), u) + np.outer(np.sin(th), v))
    N = (np.outer(np.cos(th), u) + np.outer(np.sin(th), v))
    # puffs past the tip end (s > 1) wrap onto the rounded cap: tilt their normal toward the axis end
    cap = np.clip((s - 1.0) / 0.12, 0.0, 1.0)
    N = N * (1.0 - cap)[:, None] + ax[None] * cap[:, None]
    N = N / np.linalg.norm(N, axis=1, keepdims=True)
    S = np.where(cap[:, None] > 0, b + N * rb, S)
    R = rng.uniform(r_rng[0], r_rng[1], n)
    H = rng.uniform(h_rng[0], h_rng[1], n)
    C = S + N * (H - R)[:, None]
    return {"C": C, "R": R, "H": H, "S": S, "N": N, "u": rng.uniform(0.0, 1.0, n)}


def sd_spheres_min(P, C, R, chunk=256):
    """min over spheres (C, R) of |P - C| - R, chunked over the spheres (P (n, 3))."""
    P = np.asarray(P, float)
    out = np.full(len(P), 1e9)
    for i in range(0, len(C), chunk):
        Cc, Rc = C[i:i + chunk], R[i:i + chunk]
        d = np.sqrt(((P[:, None, :] - Cc[None]) ** 2).sum(2)) - Rc[None]
        out = np.minimum(out, d.min(1))
    return out


def ellipse_front(P, cx, cz, a, b):
    """> 0 inside the oval (half-width a, half-height b) centred at (x cx, z cz), seen from the front (-Y): the field is
    1 - ((x - cx) / a)^2 - ((z - cz) / b)^2, independent of y."""
    return 1.0 - ((P[:, 0] - cx) / a) ** 2 - ((P[:, 2] - cz) / b) ** 2
