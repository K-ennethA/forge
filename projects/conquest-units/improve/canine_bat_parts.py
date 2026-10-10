"""Canine Bat parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF body shell.

Copied from firefly_parts.py / shadowbun_parts.py (copied, not imported) + the unit's own generators. Every generator
returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every piece is a CLOSED solid
(0 open edges):

    membrane      one OVERSIZED upright EAR: a thin cupped lens tube along a curved spine (a closed thin solid, poles at
                  the buried root and at the tip); per-face regions by where the face sits on the cross-section ring
                  (front face = the mauve inner, the outermost columns = the dark rim, the back face = fur) and along
                  the spine (faint vein strips on the inner face)
    lathe         closed surface of revolution (the two FANGS: blunt-tipped closed cones)
    dome_decal    a round closed DECAL conforming to the shell (eye rim / iris / highlight, nostrils): a polar grid
                  projected onto the shell along the anchor normal, the top skin domed, the bottom skin sunk under it
    soft_fringe   edge multiplier: shallow rounded notches of varied depth (soft fur at the ear base, never a sawtooth)
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
    return F if signed_volume(V, F) >= 0 else [f[::-1] for f in F]


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


def soft_fringe(s, s0, s1, n, depth, width, seed):
    """edge multiplier along s: n SHALLOW ROUNDED notches (Gaussian dips) of varied depth / width at jittered positions in
    [s0, s1] -- a soft, irregular fur fringe (never a regular sawtooth)."""
    s = np.asarray(s, float)
    m = np.ones_like(s)
    for j in range(n):
        pos = s0 + (s1 - s0) * (j + 0.25 + 0.5 * hash01(j, seed)) / n
        dep = depth[0] + (depth[1] - depth[0]) * hash01(j, seed + 1.3)
        wid = width[0] + (width[1] - width[0]) * hash01(j, seed + 2.7)
        m = m - dep * np.exp(-((s - pos) / wid) ** 2)
    return m


# --------------------------------------------------------------------------- ear membrane
def membrane(spine, nrm_hint, hw_l, hw_r, ht, ring, cup, region_fn):
    """Thin closed cupped tube along 'spine' (n,3). Per station: tangent T, ear-face normal N (nrm_hint made
    perpendicular to T), width axis W = T x N. Cross-section ring vertex at angle t: W cos t hw (hw_l on the W- side,
    hw_r on W+) + N (sin t ht + cup hw cos^2 t) -- a thin lens whose edges curl toward +N (the open, cupped front).
    Stations whose width is 0 become poles (the buried root and the tip). Winding is consistent by construction; the
    whole list is flipped once if inside-out (signed volume), which stays correct for a deep cup where the spine point
    leaves the cross-section (a per-face centroid test would not).
    region_fn(c, s, sf) -> region of a face whose mean ring cos / sin / spine fraction are c, s, sf.
    Returns V, F, R, S (arc fraction per vertex), spine."""
    C = np.asarray(spine, float)
    n = len(C)
    T = np.array([unit(t) for t in np.gradient(C, axis=0)])
    nh = unit(nrm_hint)
    V, S, CS, rows = [], [], [], []
    al = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    sf = al / al[-1]
    th = 2 * math.pi * (np.arange(ring) + 0.5) / ring       # half-step phase: no vertex exactly on the knife edge
    for i in range(n):
        N = unit(nh - T[i] * float(nh @ T[i]))
        W = unit(np.cross(T[i], N))
        if max(hw_l[i], hw_r[i]) <= 1e-9:
            rows.append([len(V)]); V.append(C[i]); S.append(sf[i]); CS.append((0.0, 0.0))
            continue
        idx = []
        for t in th:
            c, s_ = math.cos(t), math.sin(t)
            hw = hw_r[i] if c >= 0 else hw_l[i]
            idx.append(len(V)); V.append(C[i] + W * (c * hw) + N * (s_ * ht[i] + cup * hw * c * c))
            S.append(sf[i]); CS.append((c, s_))
        rows.append(idx)
    F, R = [], []
    CS = np.array(CS)
    for k in range(n - 1):
        A, B = rows[k], rows[k + 1]
        if len(A) == 1 and len(B) == 1:
            continue
        for i in range(ring):
            i1 = (i + 1) % ring
            if len(A) == 1:
                f = [A[0], B[i1], B[i]]
            elif len(B) == 1:
                f = [A[i], A[i1], B[0]]
            else:
                f = [A[i], A[i1], B[i1], B[i]]
            tm = 2 * math.pi * (i + 1.0) / ring                 # the face's mid ring angle
            F.append(f); R.append(region_fn(math.cos(tm), math.sin(tm), 0.5 * (sf[k] + sf[k + 1])))
    V = np.array(V)
    F = orient_closed(V, F)
    return V, F, R, np.array(S), C


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
    F = orient_closed(V - c, F)
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- conforming decal
def dome_decal(anchor, normal, radius, project, t_up, t_dn, dome, rings, segs, region, squash=(1.0, 1.0),
               up_hint=(0.0, 0.0, 1.0)):
    """Closed round decal on the shell. anchor: a surface point; normal: the decal axis (outward). project(pts, n) ->
    surface points hit by rays cast along -n through pts (the low shell). squash = (along up_hint, across) radius
    scale. Top skin = hit + n (t_up + dome (1 - rho^2)); bottom skin = hit - n t_dn; side wall joins the rims."""
    n = unit(normal)
    e1 = np.asarray(up_hint, float) - n * float(np.dot(up_hint, n))
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(n, [1.0, 0.0, 0.0])
    e1 = unit(e1); e2 = np.cross(n, e1)
    a = np.asarray(anchor, float)
    pts, rho = [a.copy()], [0.0]
    for k in range(1, rings + 1):
        r_ = k / rings
        for m in range(segs):
            t = 2 * math.pi * m / segs
            pts.append(a + radius * r_ * (math.cos(t) * squash[0] * e1 + math.sin(t) * squash[1] * e2)); rho.append(r_)
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
