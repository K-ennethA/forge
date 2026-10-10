"""Shadow Mole parametric parts + stalk sweep machinery (pure numpy, deterministic).

Closed parts (0 open edges) that ride on the SDF shell (burrow + mole body + claws + stalks):

    petal          one pointed PETAL blade (the stalk tips' flower tufts + the snout's petal crown): a lens cross-section
                   (12-vertex ring) swept along a gently curling spine, widest ~35% up, tapering to a pointed tip pole;
                   closed by a base pole buried in its socket. Regions per face: 'petal_pale' on the broad faces' middle
                   band over the lower ~70% (the sheet's pale-pink streaks), 'petal' (bright pink) on the edges + tip.
                   Also the claws' curved pointed tips (regions overridden to 'claw').
    oval_decal     a closed OVAL decal conforming to the shell (the small simple EYE: white oval inside a dark oval;
                   generalised from aquaoct_parts.dome_decal to two radii)

Stalk sweep (used by the build's SDF and its region / weight fields):

    Tube           one burrow-stalk: a dense spine (catmull through 3D control points) with a radius profile over the
                   arc fraction; .eval(P) -> the tube SDF (distance to the spine polyline minus the interpolated radius,
                   round caps at both ends) + the nearest arc length / fraction (the colour bands + the vine weights).

Copied from aquaoct_parts.py / firefly_parts.py: unit, hash01, frame_from_axis, Part, open_edges, orient_outward,
_catmull3 (the 2D catmull generalised to 3D).
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
    """Flip the whole face list if the signed volume about inside_pt is negative (the path ran the other way)."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def _signed_volume(V, F):
    c = V.mean(0)
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - c
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - c, V[f[i + 1]] - c))
    return vol


# --------------------------------------------------------------------------- petal blade
def petal_profile(s, base_frac, widest):
    """half-width share at arc fraction s: base_frac at the root, 1.0 at 'widest', a soft point (0) at the tip."""
    s = np.asarray(s, float)
    up = base_frac + (1.0 - base_frac) * np.sin(0.5 * math.pi * np.clip(s / widest, 0.0, 1.0))
    dn = np.clip((1.0 - s) / (1.0 - widest), 0.0, 1.0) ** 0.85
    return np.where(s <= widest, up, dn)


def petal(base, direction, face_n, length, half_w, thick, curl_deg, sink, n_st=10, n_ring=12, widest=0.35,
          base_frac=0.55, pale_s=0.72, pale_band=0.3, twist_deg=0.0, regions=("petal", "petal_pale")):
    """One closed pointed petal. base: root point (the blade starts 'sink' behind it, buried in its socket);
    direction: the petal's pointing direction; face_n: the blade's broad-face normal (made perpendicular to direction);
    the spine curls toward +face_n by curl_deg in total (an outward cup when face_n points away from the tuft axis);
    half_w / thick: the widest half-width / half-thickness. twist_deg rolls the blade about its spine root -> tip.
    regions = (edge / tip region, midband region) -- the claw tips pass ('claw', 'claw') and reuse the blade as a
    curved pointed cone. -> (V, F, R)."""
    d0 = unit(direction)
    n0 = np.asarray(face_n, float) - d0 * float(np.dot(face_n, d0))
    if np.linalg.norm(n0) < 1e-6:
        n0 = np.cross(d0, [1.0, 0.0, 0.0])
    n0 = unit(n0)
    w0 = np.cross(d0, n0)
    b = np.asarray(base, float) - d0 * sink
    Ltot = length + sink
    # spine: an arc in the (d0, n0) plane, total turn curl_deg (constant curvature), arc length Ltot
    st = np.linspace(0.0, 1.0, n_st + 1)                       # station fractions; the last station is the tip pole
    cr = math.radians(curl_deg)
    C, T, N = [], [], []
    for s in st:
        a = cr * s
        if abs(cr) < 1e-6:
            p = b + d0 * (Ltot * s)
        else:
            R_ = Ltot / cr
            p = b + d0 * (R_ * math.sin(a)) + n0 * (R_ * (1.0 - math.cos(a)))
        t = d0 * math.cos(a) + n0 * math.sin(a)
        nn = -d0 * math.sin(a) + n0 * math.cos(a)
        C.append(p); T.append(t); N.append(nn)
    C, T, N = np.array(C), np.array(T), np.array(N)
    W = np.cross(T, N)
    tw = np.radians(twist_deg * st)
    N2 = N * np.cos(tw)[:, None] + W * np.sin(tw)[:, None]
    W2 = np.cross(T, N2)
    # the blade's own arc fraction (0 at the visible root = sink behind the base)
    sv = np.clip((st * Ltot - sink) / length, 0.0, 1.0)
    hw = half_w * petal_profile(sv, base_frac, widest)
    ht = thick * (1.0 - 0.65 * sv)
    th = 2.0 * math.pi * np.arange(n_ring) / n_ring
    V = [b - d0 * 1e-3]                                         # base pole (just behind the first ring)
    ring_idx = []
    for i in range(n_st):                                       # stations 0..n_st-1 are rings, n_st is the tip pole
        idx = []
        for t in th:
            idx.append(len(V))
            V.append(C[i] + W2[i] * (hw[i] * math.cos(t)) + N2[i] * (ht[i] * math.sin(t)))
        ring_idx.append(idx)
    tip = len(V)
    V.append(C[-1])
    V = np.array(V)
    F, R = [], []
    mid_cos = np.abs(np.cos(th + math.pi / n_ring))             # |cos| at each ring face's angular middle
    r0 = ring_idx[0]
    for m in range(n_ring):
        F.append([0, r0[(m + 1) % n_ring], r0[m]]); R.append(regions[0])
    for i in range(n_st - 1):
        A, B = ring_idx[i], ring_idx[i + 1]
        smid = 0.5 * (sv[i] + sv[i + 1])
        for m in range(n_ring):
            pale = (smid >= 0.04) and (smid <= pale_s) and (mid_cos[m] < pale_band)
            F.append([A[m], A[(m + 1) % n_ring], B[(m + 1) % n_ring], B[m]]); R.append(regions[1] if pale else regions[0])
    Z = ring_idx[-1]
    for m in range(n_ring):
        F.append([Z[m], Z[(m + 1) % n_ring], tip]); R.append(regions[0])
    if _signed_volume(V, F) < 0:
        F = [f[::-1] for f in F]
    return V, F, R


# --------------------------------------------------------------------------- oval decal (the eye)
def oval_decal(anchor, normal, radii, project, t_up, t_dn, dome, rings, segs, regions_by_ring, wall_region,
               up_hint=(0.0, 0.0, 1.0)):
    """Closed OVAL decal conforming to the shell (aquaoct_parts.dome_decal with two radii): a polar grid
    (radius radii[0] across, radii[1] along the up_hint) projected onto the shell along the anchor normal
    (project(pts, n) -> surface hits); top skin = hit + n (t_up + dome (1 - rho^2)), bottom skin = hit - n t_dn,
    a side wall joins the rims. regions_by_ring[k-1] = the region of ring k's faces (top + bottom)."""
    n = unit(normal)
    e1 = np.asarray(up_hint, float) - n * float(np.dot(up_hint, n))
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(n, [1.0, 0.0, 0.0])
    e1 = unit(e1); e2 = np.cross(n, e1)
    a = np.asarray(anchor, float)
    ra, rb = radii
    pts, rho = [a.copy()], [0.0]
    for k in range(1, rings + 1):
        r_ = k / rings
        for m in range(segs):
            t = 2 * math.pi * m / segs
            pts.append(a + r_ * (rb * math.cos(t) * e1 + ra * math.sin(t) * e2)); rho.append(r_)
    pts = np.array(pts); rho = np.array(rho)
    hit = project(pts, n)
    top = hit + n[None] * (t_up + dome * (1.0 - rho ** 2))[:, None]
    bot = hit - n[None] * t_dn
    nt = len(pts)
    V = np.vstack([top, bot])
    F, ref, R = [], [], []

    def ring_idx(k):
        return [1 + (k - 1) * segs + m for m in range(segs)]
    for off, sgn in ((0, 1.0), (nt, -1.0)):
        r1 = ring_idx(1)
        for m in range(segs):
            F.append([off, off + r1[m], off + r1[(m + 1) % segs]]); ref.append(sgn * n); R.append(regions_by_ring[0])
        for k in range(1, rings):
            A, B = ring_idx(k), ring_idx(k + 1)
            for m in range(segs):
                F.append([off + A[m], off + B[m], off + B[(m + 1) % segs], off + A[(m + 1) % segs]]); ref.append(sgn * n)
                R.append(regions_by_ring[k])
    rim = ring_idx(rings)
    for m in range(segs):
        m1 = (m + 1) % segs
        F.append([rim[m], rim[m1], nt + rim[m1], nt + rim[m]])
        ref.append(unit(0.5 * (pts[rim[m]] + pts[rim[m1]]) - a)); R.append(wall_region)

    def fn(V_, f):
        q = V_[f]
        nn = np.cross(q[1] - q[0], q[2] - q[0])
        if len(f) == 4:
            nn = nn + np.cross(q[2] - q[0], q[3] - q[0])
        return nn
    Fo = [f if np.dot(fn(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, R


# --------------------------------------------------------------------------- stalk tube
def _catmull3(points, n_per):
    P = np.asarray(points, float)
    P = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0.0, 1.0, n_per, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P[-2])
    return np.array(out)


class Tube:
    """One burrow-stalk (see the module doc). ctrl: 3D control points root -> tip; r_knots: [(sf, radius)]."""

    def __init__(self, name, ctrl, r_knots, ns=48):
        self.name = name
        dense = _catmull3(np.array(ctrl, float), 16)
        al = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))])
        t = np.linspace(0.0, al[-1], ns)
        self.C = np.stack([np.interp(t, al, dense[:, k]) for k in range(3)], 1)
        self.arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(self.C, axis=0), axis=1))])
        self.L = float(self.arc[-1])
        self.sf = self.arc / self.L
        rk = np.array(r_knots, float)
        self.r = np.interp(self.sf, rk[:, 0], rk[:, 1])
        T = np.gradient(self.C, axis=0)
        self.T = T / np.linalg.norm(T, axis=1, keepdims=True)

    def bbox(self, pad):
        m = float(self.r.max()) + pad
        return self.C.min(0) - m, self.C.max(0) + m

    def at(self, s):
        """spine point + tangent at arc length s"""
        p = np.array([np.interp(s, self.arc, self.C[:, k]) for k in range(3)])
        t = np.array([np.interp(s, self.arc, self.T[:, k]) for k in range(3)])
        return p, unit(t)

    def eval(self, P):
        """-> dict(d tube SDF, s arc length, sf arc fraction)"""
        P = np.asarray(P, float)
        C = self.C
        best = np.full(len(P), 1e18); bi = np.zeros(len(P), np.int64); bu = np.zeros(len(P))
        for i in range(len(C) - 1):
            a = C[i]; ab = C[i + 1] - a
            u = np.clip(((P - a) @ ab) / float(ab @ ab), 0.0, 1.0)
            q = a + u[:, None] * ab
            d2 = np.einsum("ij,ij->i", P - q, P - q)
            m = d2 < best
            best[m] = d2[m]; bi[m] = i; bu[m] = u[m]
        r = self.r[bi] * (1 - bu) + self.r[bi + 1] * bu
        s = self.arc[bi] + bu * (self.arc[bi + 1] - self.arc[bi])
        return {"d": np.sqrt(best) - r, "s": s, "sf": s / self.L}
