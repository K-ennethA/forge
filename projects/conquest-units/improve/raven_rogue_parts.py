"""Raven Rogue parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF body shell.

Every generator returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every piece is a
CLOSED solid (0 open edges):

    loft          a closed lens / ellipse tube along a spine (poles where the width is 0): the BEAK (per-face region by
                  ring angle: upper bill / lower mandible split on an edge loop), the CLAWS, the FANG, the TAIL feathers
    drape         one layered CAPE slab: an outer + an inner skin over a (row v, column u) grid, joined by four side
                  walls (consistent winding, asserted), per-face region by the row band (colour boundaries are edge
                  loops: iso-cut by construction)
    front_decal   a closed conforming DECAL laid out in sheet FRONT-VIEW coordinates (x, z): a star-shaped outline
                  (rotated super-ellipse, optionally clipped by a lid line) cast along +Y onto the low shell, the top
                  skin domed off the surface along the smooth SDF normal, the bottom skin sunk under it, a side wall
    spine_param   nearest-polyline arc parameter (chain weights)
    soft_fringe   shallow rounded notches along an edge (copied from shadowbun_parts: soft fur / feather edges)
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
        self.drop = None                       # per-vertex arc parameter for chain weights (cape)
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


def winding_ok(F):
    """every directed edge used once and its reverse once (a consistently oriented closed surface)"""
    d = {}
    for f in F:
        for i in range(len(f)):
            e = (f[i], f[(i + 1) % len(f)])
            d[e] = d.get(e, 0) + 1
    return all(c == 1 for c in d.values()) and all((b, a) in d for a, b in d)


def signed_volume(V, F):
    v = 0.0
    for f in F:
        for k in range(1, len(f) - 1):
            v += float(np.dot(V[f[0]], np.cross(V[f[k]], V[f[k + 1]])))
    return v / 6.0


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


def soft_fringe(s, s0, s1, n, depth, width, seed):
    """edge multiplier along s: n SHALLOW ROUNDED notches (Gaussian dips) of varied depth / width at jittered positions in
    [s0, s1] -- a soft, irregular fringe (never a regular sawtooth)."""
    s = np.asarray(s, float)
    m = np.ones_like(s)
    for j in range(n):
        pos = s0 + (s1 - s0) * (j + 0.25 + 0.5 * hash01(j, seed)) / n
        dep = depth[0] + (depth[1] - depth[0]) * hash01(j, seed + 1.3)
        wid = width[0] + (width[1] - width[0]) * hash01(j, seed + 2.7)
        m = m - dep * np.exp(-((s - pos) / wid) ** 2)
    return m


def tri_quads(F):
    """split quads into two triangles (fixed diagonal: deterministic)"""
    out = []
    for f in F:
        if len(f) == 4:
            out += [[f[0], f[1], f[2]], [f[0], f[2], f[3]]]
        else:
            out.append(list(f))
    return out


# --------------------------------------------------------------------------- loft (beak, claws, fang, tail feathers)
def loft(spine, nrm_hint, hw_l, hw_r, ht, ring, regions=None, cup=0.0, ht_dn=None):
    """Closed tube along 'spine' (n,3). Per station: tangent T, section 'up' N (nrm_hint made perpendicular), lateral
    W = T x N. Section = half-width hw_l (W- side) / hw_r (W+ side), half-height ht (+N side) / ht_dn (-N side, default
    ht); nrm_hint may be one vector or one per station. cup > 0 curls the edges toward +N. Stations whose width is 0 become poles. regions: None -> 'part' or a
    function (ring angle th of the face's first vertex column, station fraction s) -> region name. Returns V, F (tris),
    R, s (arc fraction per vertex)."""
    C = np.asarray(spine, float)
    n = len(C)
    T = np.gradient(C, axis=0)
    T = np.array([unit(t) for t in T])
    nh_all = np.asarray(nrm_hint, float)
    ht_dn = ht if ht_dn is None else ht_dn
    V, S, rows = [], [], []
    al = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    sf = al / al[-1]
    th = 2 * math.pi * np.arange(ring) / ring
    for i in range(n):
        nh = unit(nh_all[i] if nh_all.ndim == 2 else nh_all)      # per-station section 'up' allowed (the beak)
        N = unit(nh - T[i] * float(nh @ T[i]))
        W = unit(np.cross(T[i], N))
        if max(hw_l[i], hw_r[i]) <= 1e-9:
            rows.append([len(V)]); V.append(C[i]); S.append(sf[i])
            continue
        idx = []
        for t in th:
            c, s_ = math.cos(t), math.sin(t)
            hw = hw_r[i] if c >= 0 else hw_l[i]
            hh = ht[i] if s_ >= 0 else ht_dn[i]
            idx.append(len(V)); V.append(C[i] + W * (c * hw) + N * (s_ * hh + cup * hw * c * c)); S.append(sf[i])
        rows.append(idx)
    F, R = [], []
    for k in range(n - 1):
        A, B = rows[k], rows[k + 1]
        sm = 0.5 * (sf[k] + sf[k + 1])
        if len(A) == 1 and len(B) == 1:
            continue
        for i in range(ring):
            tm = th[i] + math.pi / ring
            reg = "part" if regions is None else regions(tm, sm)
            if len(A) == 1:
                F.append([A[0], B[(i + 1) % ring], B[i]])
            elif len(B) == 1:
                F.append([A[i], A[(i + 1) % ring], B[0]])
            else:
                F += [[A[i], A[(i + 1) % ring], B[(i + 1) % ring]], [A[i], B[(i + 1) % ring], B[i]]]
                R.append(reg)
            R.append(reg)
    V = np.array(V)
    S = np.array(S)
    Fo = []
    for f in F:                                         # every face points away from its own spine point
        smv = float(np.mean(S[f]))
        j = int(np.clip(np.searchsorted(sf, smv), 0, n - 1))
        Fo.append(f if np.dot(face_normal(V, f), V[f].mean(0) - C[j]) >= 0 else f[::-1])
    return V, Fo, R, S


# --------------------------------------------------------------------------- cape drape slab
def drape(O, I, cell_regions):
    """Closed slab from an outer grid O (nv, nu, 3) and an inner grid I (same shape): outer + inner skins and four side
    walls, wound consistently (checked) and oriented outward (signed volume > 0). cell_regions[i][j]: the region of grid
    cell (row band i, column band j) -- outer + inner skin; each side wall takes its adjacent cell's. Region boundaries
    therefore run along grid edges (iso-cut by construction). -> V, F (tris), R."""
    nv, nu, _ = O.shape
    V = np.vstack([O.reshape(-1, 3), I.reshape(-1, 3)])
    o = lambda i, j: i * nu + j
    q = lambda i, j: nv * nu + i * nu + j
    F, R = [], []
    cr = cell_regions
    for i in range(nv - 1):
        for j in range(nu - 1):
            F.append([o(i, j), o(i + 1, j), o(i + 1, j + 1), o(i, j + 1)]); R.append(cr[i][j])
            F.append([q(i, j), q(i, j + 1), q(i + 1, j + 1), q(i + 1, j)]); R.append(cr[i][j])
        F.append([o(i + 1, 0), o(i, 0), q(i, 0), q(i + 1, 0)]); R.append(cr[i][0])                   # u = 0 wall
        F.append([o(i, nu - 1), o(i + 1, nu - 1), q(i + 1, nu - 1), q(i, nu - 1)]); R.append(cr[i][-1])  # u = 1 wall
    for j in range(nu - 1):
        F.append([o(0, j), o(0, j + 1), q(0, j + 1), q(0, j)]); R.append(cr[0][j])                   # top wall
        F.append([o(nv - 1, j + 1), o(nv - 1, j), q(nv - 1, j), q(nv - 1, j + 1)]); R.append(cr[-1][j])
    assert winding_ok(F), "drape winding"
    if signed_volume(V, F) < 0:
        F = [f[::-1] for f in F]
    F2, R2 = [], []
    for f, r in zip(F, R):
        t = tri_quads([f])
        F2 += t; R2 += [r] * len(t)
    return V, F2, R2


# --------------------------------------------------------------------------- front-view conforming decal
def superellipse_r(t, a, b, p):
    c, s = abs(math.cos(t)), abs(math.sin(t))
    return (max((c / a) ** p + (s / b) ** p, 1e-12)) ** (-1.0 / p)


def front_decal(centre, a, b, rot_deg, cast, t_up, t_dn, dome, rings, segs, region, p=2.0, clip=None):
    """Closed decal laid out in FRONT-VIEW sheet coordinates. centre (x, z); a, b = half-axes (along / across the
    rotated axis); rot_deg = rotation of the a-axis from +x toward +z; p = super-ellipse exponent (< 2 = pointed ends).
    clip = (x0, z0, x1, z1): keep only the side of that line containing the centre (an eyelid). cast((x, z) list) ->
    (surface points, smooth outward normals) for rays along +Y. Top skin = hit + n (t_up + dome (1 - rho^2)); bottom
    skin = hit - n t_dn; side wall joins the rims."""
    cx, cz = centre
    ra = math.radians(rot_deg)
    ea = np.array([math.cos(ra), math.sin(ra)]); eb = np.array([-math.sin(ra), math.cos(ra)])
    if clip is not None:
        l0 = np.array(clip[:2], float); l1 = np.array(clip[2:], float)
        ld = unit(l1 - l0); ln = np.array([-ld[1], ld[0]])
        cside = float(np.dot(np.array([cx, cz]) - l0, ln))
        ln = ln if cside < 0 else -ln                     # ln points from the centre toward the line
        dist_c = abs(cside)
    pts2, rho = [np.array([cx, cz])], [0.0]
    ang = [2 * math.pi * m / segs for m in range(segs)]
    rad = []
    for t in ang:
        d = math.cos(t) * ea + math.sin(t) * eb
        r_ = superellipse_r(t, a, b, p)
        if clip is not None:
            dn = float(np.dot(d, ln))
            if dn > 1e-9:
                r_ = min(r_, dist_c / dn)
        rad.append((d, r_))
    for k in range(1, rings + 1):
        f = k / rings
        for d, r_ in rad:
            pts2.append(np.array([cx, cz]) + d * r_ * f); rho.append(f)
    pts2 = np.array(pts2); rho = np.array(rho)
    hit, nrm = cast(pts2)
    top = hit + nrm * (t_up + dome * (1.0 - rho ** 2))[:, None]
    bot = hit - nrm * t_dn
    nt = len(pts2)
    V = np.vstack([top, bot])
    F, ref = [], []

    def ring_idx(k):
        return [1 + (k - 1) * segs + m for m in range(segs)]
    for off, sgn in ((0, 1.0), (nt, -1.0)):
        r1 = ring_idx(1)
        for m in range(segs):
            F.append([off, off + r1[m], off + r1[(m + 1) % segs]]); ref.append(sgn * nrm[0])
        for k in range(1, rings):
            A, B = ring_idx(k), ring_idx(k + 1)
            for m in range(segs):
                F += [[off + A[m], off + B[m], off + B[(m + 1) % segs]], [off + A[m], off + B[(m + 1) % segs],
                                                                          off + A[(m + 1) % segs]]]
                ref += [sgn * nrm[A[m]], sgn * nrm[A[m]]]
    rim = ring_idx(rings)
    for m in range(segs):
        m1 = (m + 1) % segs
        out = unit(0.5 * (top[rim[m]] + top[rim[m1]]) - top[0])
        F += [[rim[m], rim[m1], nt + rim[m1]], [rim[m], nt + rim[m1], nt + rim[m]]]
        ref += [out, out]
    Fo = [f if np.dot(face_normal(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, [region] * len(Fo)
