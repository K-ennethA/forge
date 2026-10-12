"""Winged Cat parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF body shell.

Copied from firefly_parts.py / canine_bat_parts.py (copied, not imported) + the unit's own generators. Every generator
returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every piece is a CLOSED solid
(0 open edges):

    pillow        a thin closed sheet over a structured (s, t) grid: the perimeter vertices are shared by the two skins
                  (knife-thin edge), the interior is split into a top skin (+N h) and a bottom skin (-N h); coincident
                  perimeter points (a wing tip) are welded. The two WINGS and the ten TAIL BLADES are pillows.
    lathe         closed surface of revolution (the two FANGS: blunt-tipped closed cones)
    polar_decal   a closed DECAL conforming to the shell whose outline is any star-shaped 2D curve (the almond EYES and
                  the vertical slit PUPILS): a polar grid projected onto the shell along the anchor normal, the top skin
                  domed, the bottom skin sunk under it, a side wall joining the rims
    lens_curve    the pointed almond / slit outline (x = a cos t, y = b sin t |sin t|^p: pointed corners on the x axis)
    spine_param   nearest-polyline arc parameter (chain weights)
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


def signed_volume(V, F):
    vol = 0.0
    for f in F:
        p0 = V[f[0]]
        for i in range(1, len(f) - 1):
            vol += float(np.dot(p0, np.cross(V[f[i]], V[f[i + 1]])))
    return vol / 6.0


def orient_closed(V, F):
    """a consistently wound closed surface: flip the whole list if its signed volume is negative (inside-out)."""
    c = np.asarray(V, float).mean(0)
    return F if signed_volume(np.asarray(V, float) - c, F) >= 0 else [f[::-1] for f in F]


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


# --------------------------------------------------------------------------- pillow (wings, tail blades)
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


# --------------------------------------------------------------------------- lathe (fangs)
def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


def lathe(profile, region, n, centre, axis, up_hint=(0, 1, 0), su=1.0, sv=1.0):
    """profile: [(r, h), ...]; an end with r == 0 becomes a pole. A closed surface of revolution about 'axis' through
    'centre' (oval by su / sv along the local up / side axes), oriented outward by signed volume."""
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    th = 2 * math.pi * np.arange(n) / n
    V, F, rings = [], [], []
    for r, h in profile:
        if r <= 1e-9:
            rings.append([len(V)]); V.append(c + a * h)
            continue
        idx = []
        for t in th:
            idx.append(len(V)); V.append(c + a * h + r * (math.cos(t) * su * u + math.sin(t) * sv * v))
        rings.append(idx)
    for k in range(len(profile) - 1):
        A, B = rings[k], rings[k + 1]
        if len(A) == 1 and len(B) == 1:
            continue
        for i in range(n):
            i1 = (i + 1) % n
            if len(A) == 1:
                F.append([A[0], B[i1], B[i]])
            elif len(B) == 1:
                F.append([A[i], A[i1], B[0]])
            else:
                F.append([A[i], A[i1], B[i1], B[i]])
    V = np.array(V)
    F = orient_closed(V, F)
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- conforming decals (eyes, pupils)
def lens_curve(a, b, segs, p=1.0):
    """pointed almond / slit outline: x = a cos t, y = b sign(sin t) |sin t|^(1 + p) -> (segs, 2). p = 0 is an ellipse;
    p > 0 sharpens the two corners on the x axis (the almond's canthi / the slit's ends)."""
    t = 2 * math.pi * np.arange(segs) / segs
    s = np.sin(t)
    return np.stack([a * np.cos(t), b * np.sign(s) * np.abs(s) ** (1.0 + p)], 1)


def polar_decal(anchor, normal, outline, project, t_up, t_dn, dome, rings, region, up_hint=(0.0, 0.0, 1.0)):
    """Closed decal on the shell whose outline is a star-shaped 2D curve about the anchor (outline (segs, 2) in the
    decal frame: x along e2 = n x e1, y along e1 = up_hint made perpendicular to n). project(pts, n) -> surface points
    hit by rays cast along -n through pts (the low shell). Top skin = hit + n (t_up + dome (1 - rho^2)); bottom skin =
    hit - n t_dn; a side wall joins the rims."""
    n = unit(normal)
    e1 = np.asarray(up_hint, float) - n * float(np.dot(up_hint, n))
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(n, [1.0, 0.0, 0.0])
    e1 = unit(e1); e2 = np.cross(e1, n)
    a = np.asarray(anchor, float)
    O = np.asarray(outline, float)
    segs = len(O)
    pts, rho = [a.copy()], [0.0]
    for k in range(1, rings + 1):
        r_ = k / rings
        for m in range(segs):
            pts.append(a + r_ * (O[m, 0] * e2 + O[m, 1] * e1)); rho.append(r_)
    pts = np.array(pts); rho = np.array(rho)
    hit = project(pts, n)
    top = hit + n[None] * (t_up + dome * (1.0 - rho ** 2))[:, None]
    bot = hit - n[None] * t_dn
    nt = len(pts)
    V = np.vstack([top, bot])
    F, ref = [], []

    def ring_idx(k):
        return [1 + (k - 1) * segs + m for m in range(segs)]
    for off, sgn in ((0, 1.0), (nt, -1.0)):             # top (+n) and bottom (-n) skins
        r1 = ring_idx(1)
        for m in range(segs):
            F.append([off, off + r1[m], off + r1[(m + 1) % segs]]); ref.append(sgn * n)
        for k in range(1, rings):
            A, B = ring_idx(k), ring_idx(k + 1)
            for m in range(segs):
                F.append([off + A[m], off + B[m], off + B[(m + 1) % segs], off + A[(m + 1) % segs]]); ref.append(sgn * n)
    rim = ring_idx(rings)
    for m in range(segs):                                # side wall
        m1 = (m + 1) % segs
        F.append([rim[m], rim[m1], nt + rim[m1], nt + rim[m]])
        ref.append(unit(0.5 * (pts[rim[m]] + pts[rim[m1]]) - a))
    Fo = [f if np.dot(face_normal(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, [region] * len(Fo)
