"""Shield Rodent parametric parts (pure numpy, deterministic): the hard-surface pieces that ride on the SDF body shell.

Copied from firefly_parts.py / winged_cat_parts.py / crystal_canine_parts.py (copied, not imported) + the unit's own
generators. Every generator returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every
piece is a CLOSED solid (0 open edges):

    lathe         surface of revolution of an OPEN profile path whose ends sit on the axis (poles): the top PLATE (a
                  slightly domed lens), the centre BOSS (stepped collar + dome), the SKIRT pan, the RIVET domes, the
                  SNOUT ball, the TEETH (oval section via su / sv). region_fn(segment, angular index) -> region per face
                  (the plate's brushed-sheen wedges are whole angular segments: straight radial edges, no paint seams)
    ring_lathe    surface of revolution of a CLOSED profile loop (torus topology): the RIM band (bevelled rectangle)
    displace_axial  bake-source detail: pushes chosen vertices along the lathe axis by a height field (the brushed grooves
                  and light scratches of the HIGH plate / rim; the low parts stay smooth -- the lines live in the normal map)
    diamond_spike faceted 4-sided tail segment: a diamond section (corners up / down / left / right) tapering along an
                  axis from a buried root cap to an end cap or a sharp tip; tone per facet (upper = lit, lower = shade)
    lens_curve / polar_decal   closed conforming decals (the EYES + their highlight dots)
    spine_param   nearest-polyline arc parameter (leg / chain weights)
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


def face_normal(V, f):
    q = V[f]
    n = np.zeros(3)
    for i in range(1, len(f) - 1):
        n = n + np.cross(q[i] - q[0], q[i + 1] - q[0])
    return n


def signed_volume(V, F):
    vol = 0.0
    for f in F:
        p0 = V[f[0]]
        for i in range(1, len(f) - 1):
            vol += float(np.dot(p0, np.cross(V[f[i]], V[f[i + 1]])))
    return vol / 6.0


def orient_closed(V, F):
    """Consistently wound closed surface: flip every face if the signed volume is negative (any genus)."""
    return F if signed_volume(V, F) >= 0 else [f[::-1] for f in F]


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    """-> (a, u, v): a = axis, u = the up_hint made perpendicular, v = a x u."""
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


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


# --------------------------------------------------------------------------- surfaces of revolution
def _rings(profile, n, c, a, u, v, su, sv, phase):
    th = phase + 2 * math.pi * np.arange(n) / n
    V, rings = [], []
    for r, h in profile:
        if r <= 1e-9:
            rings.append([len(V)]); V.append(c + a * h)
            continue
        idx = []
        for t in th:
            idx.append(len(V)); V.append(c + a * h + r * (math.cos(t) * su * u + math.sin(t) * sv * v))
        rings.append(idx)
    return V, rings, th


def _band(A, B, n, F, R, reg_fn, k):
    if len(A) == 1 and len(B) == 1:
        return
    for i in range(n):
        i1 = (i + 1) % n
        if len(A) == 1:
            F.append([A[0], B[i1], B[i]])
        elif len(B) == 1:
            F.append([A[i], A[i1], B[0]])
        else:
            F.append([A[i], A[i1], B[i1], B[i]])
        R.append(reg_fn(k, i))


def lathe(profile, region, n, centre, axis, up_hint=(0, 1, 0), su=1.0, sv=1.0, phase=0.0):
    """profile: [(r, h), ...] OPEN path, both ends on the axis (r == 0 -> pole). region: a name, a per-segment list, or
    region(k, i) -> name (k = profile segment, i = angular segment; segment i spans theta_i .. theta_i+1 measured from u
    toward v). Closed, outward by signed volume. -> V, F, R, (a, u, v, theta)."""
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    V, rings, th = _rings(profile, n, c, a, u, v, su, sv, phase)
    if callable(region):
        reg_fn = region
    elif isinstance(region, str):
        reg_fn = lambda k, i: region
    else:
        reg_fn = lambda k, i: region[k]
    F, R = [], []
    for k in range(len(profile) - 1):
        _band(rings[k], rings[k + 1], n, F, R, reg_fn, k)
    V = np.array(V)
    F = orient_closed(V, F)
    return V, F, R, (a, u, v, th)


def ring_lathe(loop, region, n, centre, axis, up_hint=(0, 1, 0), phase=0.0):
    """loop: [(r, h), ...] CLOSED profile (r > 0 everywhere; the last point joins the first): a torus-topology solid
    (the rim band). region as in lathe (k = loop segment, the closing segment is len(loop) - 1)."""
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    V, rings, th = _rings(loop, n, c, a, u, v, 1.0, 1.0, phase)
    reg_fn = region if callable(region) else ((lambda k, i: region) if isinstance(region, str) else (lambda k, i: region[k]))
    F, R = [], []
    m = len(loop)
    for k in range(m):
        _band(rings[k], rings[(k + 1) % m], n, F, R, reg_fn, k)
    V = np.array(V)
    F = orient_closed(V, F)
    return V, F, R, (a, u, v, th)


def displace_axial(V, centre, frame, mask, height_fn):
    """Push the masked vertices along the lathe axis by height_fn(x, y) (local coordinates in the lathe plane,
    x along u, y along v). Used ONLY on the bake-source copies (grooves / scratches -> the normal map)."""
    a, u, v, _ = frame
    Q = V - np.asarray(centre, float)
    x, y = Q @ u, Q @ v
    dh = np.where(mask, height_fn(x, y), 0.0)
    return V + np.outer(dh, a)


# --------------------------------------------------------------------------- faceted tail segment
def diamond_spike(root, axis, stations, tone_of, up_hint=(0.0, 0.0, 1.0)):
    """Closed 4-facet segment along 'axis' from 'root': stations = [(s, half_width, half_height), ...] (s along the
    axis; a station with half sizes 0 is a sharp tip). Section = a diamond with corners at +-v (half_width, across) and
    +-u (half_height, up_hint side). The first station is capped by a pole on the axis (buried root), the last by a
    pole (a sharp tip when its size is 0, else a flat end cap). tone_of(face normal) -> region per face."""
    a, u, v = frame_from_axis(axis, up_hint)
    r0 = np.asarray(root, float)
    V, rings = [], []
    for s, hw, hh in stations:
        c = r0 + a * s
        if hw <= 1e-9 and hh <= 1e-9:
            rings.append([len(V)]); V.append(c)
            continue
        idx = []
        for cu, cv in ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)):
            idx.append(len(V)); V.append(c + u * hh * cu + v * hw * cv)
        rings.append(idx)
    F = []
    if len(rings[0]) > 1:                                  # root cap (flat, fan to a centre pole)
        pc = len(V); V.append(r0 + a * stations[0][0])
        for i in range(4):
            F.append([pc, rings[0][(i + 1) % 4], rings[0][i]])
    for k in range(len(rings) - 1):
        A, B = rings[k], rings[k + 1]
        for i in range(4):
            i1 = (i + 1) % 4
            if len(B) == 1:
                F.append([A[i], A[i1], B[0]])
            else:
                F.append([A[i], A[i1], B[i1], B[i]])
    if len(rings[-1]) > 1:                                 # flat end cap
        pc = len(V); V.append(r0 + a * stations[-1][0])
        for i in range(4):
            F.append([pc, rings[-1][i], rings[-1][(i + 1) % 4]])
    V = np.array(V)
    F = orient_closed(V, F)
    R = [tone_of(unit(face_normal(V, f))) for f in F]
    return V, F, R


# --------------------------------------------------------------------------- conforming decals (eyes, highlights)
def lens_curve(a, b, segs, p=1.0):
    """almond / ellipse outline: x = a cos t, y = b sign(sin t) |sin t|^(1 + p) -> (segs, 2). p = 0 is an ellipse."""
    t = 2 * math.pi * np.arange(segs) / segs
    s = np.sin(t)
    return np.stack([a * np.cos(t), b * np.sign(s) * np.abs(s) ** (1.0 + p)], 1)


def polar_decal(anchor, normal, outline, project, t_up, t_dn, dome, rings, region, up_hint=(0.0, 0.0, 1.0)):
    """Closed decal on the shell whose outline is a star-shaped 2D curve about the anchor (outline (segs, 2) in the
    decal frame: x along e2 = e1 x n, y along e1 = up_hint made perpendicular to n). project(pts, n) -> surface points
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
