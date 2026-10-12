"""Petal Cobra parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF body shell.

Copied from winged_cat_parts.py / firefly_parts.py (copied, not imported) + the unit's own generators. Every generator
returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every piece is a CLOSED solid
(0 open edges):

    pillow        a thin closed sheet over a structured (s, t) grid: the perimeter vertices are shared by the two skins
                  (knife-thin edge), the interior is split into a top skin (+N h) and a bottom skin (-N h); coincident
                  perimeter points (the petal's bottom tip and its curled top hook) are welded. The two HOOD PETALS are
                  pillows.
    grid_normals  per-point surface normals of a structured (s, t) grid (central differences, one consistent sign)
    spine_param   nearest-polyline arc parameter (tail chain weights)
    front_field   signed field of a front-projected (X-Z plane) rotated almond / ellipse, positive inside (eyes, markings)
    curve_field   signed field of a front-projected thick polyline, positive inside (the smile line)
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
        self.chain = None
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


# --------------------------------------------------------------------------- pillow (hood petals)
def pillow(P, N, H, region_fn, weld=1e-7):
    """P (ns+1, nt+1, 3) grid points, N (ns+1, nt+1, 3) unit offset directions, H (ns+1, nt+1) half-thickness (forced 0
    on the perimeter). Perimeter vertices are single (shared by both skins); interior vertices are doubled (top +N H,
    bottom -N H). Coincident perimeter points (a pointed tip) are welded; faces left with < 3 distinct vertices drop.
    region_fn(i, j, top) -> region of grid cell (i, j) on the top (+N) / bottom skin.
    Returns V, F, R, IJ (the grid index (i, j) of each vertex; welded / interior-pair vertices keep theirs)."""
    P = np.asarray(P, float); N = np.asarray(N, float); H = np.asarray(H, float)
    ns, nt = P.shape[0] - 1, P.shape[1] - 1
    assert ns >= 2 and nt >= 2
    V, IJ = [], []
    top = -np.ones((ns + 1, nt + 1), int); bot = -np.ones((ns + 1, nt + 1), int)
    key = {}
    for i in range(ns + 1):
        for j in range(nt + 1):
            if i in (0, ns) or j in (0, nt):
                k = tuple(np.round(P[i, j] / weld).astype(np.int64))
                if k not in key:
                    key[k] = len(V); V.append(P[i, j]); IJ.append((i, j))
                top[i, j] = bot[i, j] = key[k]
            else:
                top[i, j] = len(V); V.append(P[i, j] + N[i, j] * H[i, j]); IJ.append((i, j))
                bot[i, j] = len(V); V.append(P[i, j] - N[i, j] * H[i, j]); IJ.append((i, j))
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
    # orientation: the top skin's faces point along +N (checked on the first interior cell), flip all otherwise
    probe = [k for k, f in enumerate(F) if all(0 < IJ[v][0] < ns and 0 < IJ[v][1] < nt for v in f)]
    if probe:
        f = F[probe[0]]
        i_, j_ = IJ[f[0]]
        sgn = 1.0 if f[0] == top[i_, j_] else -1.0
        if np.dot(face_normal(V, f), N[i_, j_]) * sgn < 0:
            F = [f[::-1] for f in F]
    return V, F, R, np.array(IJ)


def grid_normals(P, hint):
    """unit normals of a structured grid P (ns+1, nt+1, 3): cross(dP/ds, dP/dt) by central differences; one sign for the
    whole grid (the parametrisation is regular, so the cross product never flips inside it), chosen so the mean normal
    points along 'hint'. Degenerate (welded) rows borrow their neighbour row's normal."""
    Ps = np.gradient(P, axis=0)
    Pt = np.gradient(P, axis=1)
    n = np.cross(Ps, Pt)
    ln = np.linalg.norm(n, axis=2)
    bad = ln < 1e-9
    n = n / np.maximum(ln, 1e-12)[..., None]
    for i in range(P.shape[0]):                       # welded end rows: copy the nearest healthy row
        for j in range(P.shape[1]):
            if bad[i, j]:
                k = i + 1 if i < P.shape[0] // 2 else i - 1
                n[i, j] = n[k, j]
    if float(np.mean(n.reshape(-1, 3) @ np.asarray(hint, float))) < 0:
        n = -n
    return n


# --------------------------------------------------------------------------- front-projected face / marking fields
def front_field(P, cx, cz, a, b, tilt_deg, sharp=0.0, grow=0.0):
    """field of a rotated almond / ellipse in the front (X-Z) projection, centre (cx, cz), half-axes a (along the tilted
    long axis) x b; tilt_deg rotates the long axis counter-clockwise as seen from the front camera (+X right, +Z up).
    sharp = 0: ellipse (field ~ b (1 - r)); sharp = 1: pointed almond (|v| <= b (1 - (u/a)^2), the lens_curve p = 1
    canthi). Positive inside, ~ distance near the outline. grow > 0 = the same shape grown by 'grow' on both axes (the
    densify zone test)."""
    a = a + grow; b = b + grow
    th = math.radians(tilt_deg)
    dx = P[:, 0] - cx; dz = P[:, 2] - cz
    u = dx * math.cos(th) + dz * math.sin(th)
    v = -dx * math.sin(th) + dz * math.cos(th)
    if sharp <= 0.0:
        r = np.sqrt((u / a) ** 2 + (v / b) ** 2)
        return b * (1.0 - r)
    w = 1.0 - (u / a) ** 2
    return np.where(w > 0.0, b * w - np.abs(v), -np.abs(v) - b * (np.abs(u) / a - 1.0) * 2.0)


def curve_field(P, pts_xz, half_w, grow=0.0):
    """thick front-projected polyline: half_w - distance (in X-Z) to the polyline; positive inside the stroke."""
    Q = np.stack([P[:, 0], P[:, 2]], 1)
    pts = np.asarray(pts_xz, float)
    best = np.full(len(P), 1e18)
    for a, b in zip(pts[:-1], pts[1:]):
        ab = b - a
        u = np.clip(((Q - a) @ ab) / max(float(ab @ ab), 1e-18), 0.0, 1.0)
        d = np.linalg.norm(Q - a - np.outer(u, ab), axis=1)
        best = np.minimum(best, d)
    return half_w + grow - best
