"""Fogeist parametric pieces (pure numpy, deterministic): the generators behind the ONE mist SDF.

The unit has no closed hard-surface parts: head, hood, neck, chest, the puddled body, the trailing tail, the mist crest
and every wisp are smooth-unioned into a single SDF shell (fogeist_build.py); the eyes are iso-cut into that shell.
This module holds the unit's own generators so the build stays a pipeline:

    Part / open_edges     the firefly_parts idioms (kept so the build's open-edge table reads the same as every unit)
    ellipsoid_hit         ray from an ellipsoid centre -> its surface point + outward normal (vectorised)
    fib_dirs              seeded, jittered Fibonacci directions: an even scatter over the sphere, deterministic
    lobe_bulges           the CLOUD LAYERING: blunt rounded bulges (sphere centre, radius, protrusion) scattered on the
                          upper surface of a set of ellipsoid lobes, only where that point is on the OUTER surface of
                          the lobe union (never buried). Contract hazard note: clouds = overlapping rounded bulges,
                          blunt, varied, smooth-unioned deep -> soft, never cones / polka dots.
    wisp_blobs            a WISP: a chain of flattened, tapering blobs along a curve (oriented ellipsoids), smooth-
                          unioned by the build into a closed lobed ribbon. Rounded blobs with a thickness floor keep
                          it a closed blob, never an edge-on spike / tentacle.
    ellipse_front         front-projected oval field (> 0 inside): the eyes, exactly as the front view draws them
"""
import math
import numpy as np

import magmoo_sdf as SD          # read-only use (the SDF kit)


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


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


def ellipsoid_hit(c, r, d):
    """surface point of the axis-aligned ellipsoid (centre c, radii r) along the rays c + t d, plus the outward unit
    normal. Vectorised over d (n, 3)."""
    d = np.atleast_2d(np.asarray(d, float))
    d = d / np.linalg.norm(d, axis=1, keepdims=True)
    r = np.asarray(r, float)
    t = 1.0 / np.sqrt(((d / r) ** 2).sum(1))
    S = np.asarray(c, float) + d * t[:, None]
    n = (S - c) / (r * r)
    return S, n / np.linalg.norm(n, axis=1, keepdims=True)


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


def lobe_bulges(lobes, per_area, r_rng, h_rng, seed, nz_min, sd_union, buried_tol=0.03, keep=None):
    """Bulges on the outer, upward-facing surface of ellipsoid lobes [(centre, radii), ...].
    per_area: bulge candidates per unit of ellipsoid surface area (deterministic count per lobe); nz_min: the
    surface normal's minimum z (the sheet layers its cloud lobes on the upper surface); sd_union(P): the lobe union's
    distance (a candidate whose surface point is more than buried_tol inside the union is buried -> dropped); keep(S, N)
    -> bool mask: an extra filter (e.g. clear of the face). Returns dict(C centres, R radii, H protrusions, S, N)."""
    out = {"C": [], "R": [], "H": [], "S": [], "N": []}
    for i, (c, r) in enumerate(lobes):
        r = np.asarray(r, float)
        p = 1.6075
        area = 4.0 * math.pi * (((r[0] * r[1]) ** p + (r[0] * r[2]) ** p + (r[1] * r[2]) ** p) / 3.0) ** (1.0 / p)
        n = max(4, int(round(area * per_area)))
        D, rng = fib_dirs(n, seed * 1000 + i, 0.35)
        S, N = ellipsoid_hit(c, r, D)
        R = rng.uniform(r_rng[0], r_rng[1], n)
        H = rng.uniform(h_rng[0], h_rng[1], n) * (R / r_rng[1]) ** 0.5
        m = (N[:, 2] >= nz_min) & (sd_union(S) > -buried_tol)
        if keep is not None:
            m &= keep(S, N)
        C = S + N * (H - R)[:, None]
        for k_, v_ in (("C", C), ("R", R), ("H", H), ("S", S), ("N", N)):
            out[k_].append(v_[m])
    return {k: np.concatenate(v, 0) for k, v in out.items()}


def ellipse_front(P, cx, cz, a, b):
    """> 0 inside the oval (half-width a, half-height b) centred at (x cx, z cz), seen from the front (-Y): the field is
    1 - ((x - cx) / a)^2 - ((z - cz) / b)^2, independent of y."""
    return 1.0 - ((P[:, 0] - cx) / a) ** 2 - ((P[:, 2] - cz) / b) ** 2


def wisp_blobs(ctrl, n, rx, rz, overlap=0.75, wobble=0.15):
    """a WISP as a chain of n flattened blobs along the Catmull-Rom curve through ctrl: blob i sits at arc fraction
    i / (n - 1), elongated along the local tangent (half-length = overlap x the blob spacing), half-width rx[0] -> rx[1]
    and half-thickness rz[0] -> rz[1] (root -> tip, linear); alternate blobs swell / shrink by +-wobble (a lumpy, puffy
    strand rather than a smooth tentacle). Returns [(centre, radii, frame)] for SD.sd_ellipsoid; the
    frame rows are the blob's local x (side), y (tangent), z (up) axes. Closed rounded blobs: never an edge-on spike."""
    pts = SD.catmull(np.asarray(ctrl, float), 16)
    s = SD.arclen(pts)
    L = float(s[-1])
    sp = L / max(n - 1, 1)
    out = []
    for i in range(n):
        t = i / max(n - 1, 1)
        si = t * L
        c = np.array([np.interp(si, s, pts[:, k]) for k in range(3)])
        j = min(int(np.searchsorted(s, si)), len(pts) - 1)
        j0, j1 = max(j - 2, 0), min(j + 2, len(pts) - 1)
        ty = unit(pts[j1] - pts[j0])
        tz = unit(np.array([0.0, 0.0, 1.0]) - ty * ty[2])
        tx = np.cross(ty, tz)
        w = 1.0 + wobble * (1.0 if i % 2 == 0 else -1.0) * (1.0 if 0 < i < n - 1 else 0.0)
        r = np.array([(rx[0] + (rx[1] - rx[0]) * t) * w, overlap * sp + 0.5 * (rx[0] + (rx[1] - rx[0]) * t),
                      (rz[0] + (rz[1] - rz[0]) * t) * w])
        if i == n - 1:                                   # a rounded end blob (an elongated last blob reads as a point)
            r[1] = 1.2 * max(r[0], r[2])
        out.append((c, r, np.stack([tx, ty, tz], 0)))
    return out


def blob_box(c, r):
    m = float(np.max(r))
    return np.asarray(c, float) - m, np.asarray(c, float) + m
