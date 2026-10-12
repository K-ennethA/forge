"""Eclipse Wolf parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF body shell.

Copied from crystal_canine_parts.py / firefly_parts.py (copied, not imported) + the unit's own generators. Every generator
returns V (n,3) float, F list of vertex-index lists, R list of per-face region names. Every piece is a CLOSED solid
(0 open edges):

    polar_decal      a closed DECAL conforming to the shell whose outline is any star-shaped 2D curve (the EYE whites, the
                     PUPILS, the NOSE): a polar grid projected onto the shell along the anchor normal, the top skin domed,
                     the bottom skin sunk under it, a side wall joining the rims
    clip_outline     a star-shaped outline = the intersection of convex 2D sets (an ellipse cut by a slanted brow line, a
                     pupil oval clipped to the eye), sampled by bisection along rays
    tooth_wedge      a closed solid TOOTH (front + back triangle, tapered point): the carved grin
    front_tri_decal  (unused since v1.2) a closed triangular decal whose three corners are given in FRONT-VIEW (x, z) and cast onto the shell
                     from the front (a barycentric grid, so it follows the curve): one TOOTH of the grin
    torus            a closed torus (the ORB RING, the four LEG BANDS)
    uv_sphere        a closed UV sphere (the dark ORB)
    horn_sweep       a closed swept CRESCENT HORN: a rounded (elliptical) section (in-plane width x slab depth) swept along
                     a path, a flat cap at the base and a single apex at the tip; one thin INNER-EDGE facet (toward the
                     crescent's concave side, ~17% of the width) is the glow stripe
    fib_dirs         seeded Fibonacci-sphere directions (the fleece clump scatter)
    spine_param      nearest-polyline arc parameter (chain weights)
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
            vol += np.dot(p0, np.cross(V[f[i]], V[f[i + 1]]))
    return vol / 6.0


def orient_outward(V, F, inside_pt):
    """Flip the whole face list if the signed volume about inside_pt is negative."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


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


# --------------------------------------------------------------------------- outlines
def lens_curve(a, b, segs, p=1.0):
    """pointed almond outline: x = a cos t, y = b sign(sin t) |sin t|^(1 + p) -> (segs, 2). p = 0 is an ellipse."""
    t = 2 * math.pi * np.arange(segs) / segs
    s = np.sin(t)
    return np.stack([a * np.cos(t), b * np.sign(s) * np.abs(s) ** (1.0 + p)], 1)


def clip_outline(inside, rmax, segs, shrink=1.0):
    """star-shaped outline about (0, 0) of the set inside(x, y) -> bool (an intersection of convex sets containing the
    origin): bisection along segs rays. -> (segs, 2)"""
    out = []
    for k in range(segs):
        t = 2 * math.pi * k / segs
        lo_, hi_ = 0.0, rmax
        for _ in range(32):
            m_ = 0.5 * (lo_ + hi_)
            lo_, hi_ = (m_, hi_) if inside(m_ * math.cos(t), m_ * math.sin(t)) else (lo_, m_)
        out.append((shrink * lo_ * math.cos(t), shrink * lo_ * math.sin(t)))
    return np.array(out)


# --------------------------------------------------------------------------- conforming decals
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


def front_tri_decal(corners_xz, cast, t_up, t_dn, sub, region, inset=0.0):
    """Closed triangular decal: corners_xz = 3 (x, z) points in the FRONT view, shrunk toward their centroid by 'inset';
    a barycentric grid of 'sub' divisions is cast from the front (cast(x, z) -> (surface point, normal)); top skin =
    hit + n t_up, bottom = hit - n t_dn with n = the corners' mean normal (one offset direction: straight walls)."""
    C2 = np.asarray(corners_xz, float)
    g2 = C2.mean(0)
    if inset > 0.0:
        C2 = g2 + (C2 - g2) * np.maximum(0.0, 1.0 - inset / np.linalg.norm(C2 - g2, axis=1))[:, None]
    A, B, C = C2
    idx = {}
    pts2 = []
    for i in range(sub + 1):
        for j in range(sub + 1 - i):
            idx[(i, j)] = len(pts2)
            pts2.append(A + (B - A) * i / sub + (C - A) * j / sub)
    hits = [cast(p[0], p[1]) for p in pts2]
    S = np.array([h[0] for h in hits])
    n = unit(np.mean([hits[idx[(0, 0)]][1], hits[idx[(sub, 0)]][1], hits[idx[(0, sub)]][1]], 0))
    nt = len(S)
    V = np.vstack([S + n * t_up, S - n * t_dn])
    tris = []
    for i in range(sub):
        for j in range(sub - i):
            tris.append([idx[(i, j)], idx[(i + 1, j)], idx[(i, j + 1)]])
            if i + j < sub - 1:
                tris.append([idx[(i + 1, j)], idx[(i + 1, j + 1)], idx[(i, j + 1)]])
    F, ref = [], []
    for t in tris:
        F.append(list(t)); ref.append(n)
        F.append([nt + v for v in t[::-1]]); ref.append(-n)
    loop = [idx[(i, 0)] for i in range(sub + 1)] + [idx[(sub - k, k)] for k in range(1, sub + 1)] + \
           [idx[(0, sub - k)] for k in range(1, sub)]
    cen = S.mean(0)
    for m in range(len(loop)):
        a_, b_ = loop[m], loop[(m + 1) % len(loop)]
        F.append([a_, b_, nt + b_, nt + a_]); ref.append(unit(0.5 * (S[a_] + S[b_]) - cen))
    Fo = [f if np.dot(face_normal(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, [region] * len(Fo)


def tooth_wedge(front, back, region):
    """one carved TOOTH: a closed wedge between a front triangle and a back triangle (3 points each, the third the
    point): front face, back face, three side quads. Closed."""
    V = np.vstack([np.asarray(front, float), np.asarray(back, float)])
    F = [[0, 1, 2], [5, 4, 3], [0, 3, 4, 1], [1, 4, 5, 2], [2, 5, 3, 0]]
    F = orient_outward(V, F, V.mean(0))
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- rings + spheres
def torus(R0, r, n_major, n_minor, centre, axis, up_hint=(0, 0, 1), region="rim"):
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    V, F = [], []
    for i in range(n_major):
        t = 2 * math.pi * i / n_major
        radial = math.cos(t) * u + math.sin(t) * v
        ring_c = c + R0 * radial
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
    if np.dot(nrm, V[f0[0]] - (c + R0 * u)) < 0:
        F = [f[::-1] for f in F]
    return V, F, [region] * len(F)


def uv_sphere(c, r, n_lat, n_lon, region):
    c = np.asarray(c, float)
    V = [c + np.array([0.0, 0.0, -r])]
    for i in range(1, n_lat):
        th = -math.pi / 2 + math.pi * i / n_lat
        for j in range(n_lon):
            ph = 2 * math.pi * j / n_lon
            V.append(c + r * np.array([math.cos(th) * math.cos(ph), math.cos(th) * math.sin(ph), math.sin(th)]))
    V.append(c + np.array([0.0, 0.0, r]))
    V = np.array(V)
    top = len(V) - 1
    ring = lambda i: [1 + (i - 1) * n_lon + j for j in range(n_lon)]
    F = []
    r1 = ring(1)
    for j in range(n_lon):
        F.append([0, r1[(j + 1) % n_lon], r1[j]])
    for i in range(1, n_lat - 1):
        A, B = ring(i), ring(i + 1)
        for j in range(n_lon):
            j1 = (j + 1) % n_lon
            F.append([A[j], A[j1], B[j1], B[j]])
    rl = ring(n_lat - 1)
    for j in range(n_lon):
        F.append([rl[j], rl[(j + 1) % n_lon], top])
    F = [f if np.dot(face_normal(V, f), V[f].mean(0) - c) >= 0 else f[::-1] for f in F]
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- crescent horn
def horn_sweep(path, width, depth, plane_n, centre_hint, glow_region, body_region, n_sec=10, glow_frac=0.17):
    """path (n, 3) dense polyline base -> tip; width(s), depth(s) at arc fraction s (s = 1 is the apex). The section is a
    ROUNDED (elliptical) solid in the frame B (in-plane, toward the crescent's convex OUTER side: away from centre_hint) x
    S (the slab normal, ~plane_n): n_sec points p(t) = B w/2 cos t + S d/2 sin t. Two of them straddle the INNER edge
    (t = pi -+ delta) so the facet between them is a thin stripe of width glow_frac x w: that facet alone is the glow band;
    the other n_sec - 2 points share the rest of the ellipse evenly. Flat base cap, single apex. Closed."""
    P = np.asarray(path, float)
    n = len(P)
    s_ = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    s_ = s_ / s_[-1]
    pn = unit(plane_n)
    ch = np.asarray(centre_hint, float)
    V, rings = [], []
    for i in range(n - 1):
        T = unit(P[min(i + 1, n - 1)] - P[max(i - 1, 0)])
        B = unit(np.cross(pn, T))
        if np.dot(B, P[i] - ch) < 0:
            B = -B
        S = unit(np.cross(T, B))
        if np.dot(S, pn) < 0:
            S = -S
        w, d = float(width(s_[i])), float(depth(s_[i]))
        dl = math.asin(min(0.9, glow_frac * w / max(d, 1e-9)))
        ts = [math.pi - dl, math.pi + dl] + [math.pi + dl + k * (2 * math.pi - 2 * dl) / (n_sec - 1) for k in range(1, n_sec - 1)]
        rings.append(list(range(len(V), len(V) + n_sec)))
        V += [P[i] + B * (w / 2) * math.cos(t) + S * (d / 2) * math.sin(t) for t in ts]
    base_c = len(V); V.append(P[0].copy())
    apex = len(V); V.append(P[-1])
    V = np.array(V)
    F, R, ref = [], [], []
    for i in range(len(rings) - 1):
        A, Bq = rings[i], rings[i + 1]
        for k in range(n_sec):
            k1 = (k + 1) % n_sec
            F.append([A[k], A[k1], Bq[k1], Bq[k]])
            R.append(glow_region if k == 0 else body_region)
            ref.append(0.25 * (V[A[k]] + V[A[k1]] + V[Bq[k]] + V[Bq[k1]]) - 0.5 * (P[i] + P[i + 1]))
    A = rings[0]
    for k in range(n_sec):
        F.append([base_c, A[(k + 1) % n_sec], A[k]]); R.append(body_region); ref.append(P[0] - P[1])
    A = rings[-1]
    for k in range(n_sec):
        k1 = (k + 1) % n_sec
        F.append([A[k], A[k1], apex]); R.append(glow_region if k == 0 else body_region)
        ref.append(0.5 * (V[A[k]] + V[A[k1]]) - P[-2] + 0.5 * (P[-1] - P[-2]))
    Fo = [f if np.dot(face_normal(V, f), r) >= 0 else f[::-1] for f, r in zip(F, ref)]
    return V, Fo, R


# --------------------------------------------------------------------------- fleece scatter
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


def sd_spheres_min(P, C, R, chunk=256):
    """min over spheres (C, R) of |P - C| - R, chunked over the spheres (P (n, 3))."""
    P = np.asarray(P, float)
    out = np.full(len(P), 1e9)
    for i in range(0, len(C), chunk):
        Cc, Rc = C[i:i + chunk], R[i:i + chunk]
        d = np.sqrt(((P[:, None, :] - Cc[None]) ** 2).sum(2)) - Rc[None]
        out = np.minimum(out, d.min(1))
    return out


def sd_spheres_smin(P, C, R, k, chunk=128):
    """smooth union (polynomial smin, k) of the spheres (C, R) in index order, starting from +inf (the order the grid
    applies them). P (n, 3)."""
    P = np.asarray(P, float)
    out = np.full(len(P), 1e9)
    for i in range(0, len(C), chunk):
        Cc, Rc = C[i:i + chunk], R[i:i + chunk]
        d = np.sqrt(((P[:, None, :] - Cc[None]) ** 2).sum(2)) - Rc[None]
        for j in range(d.shape[1]):
            a, b = out, d[:, j]
            h = np.maximum(k - np.abs(a - b), 0.0) / k
            out = np.minimum(a, b) - h * h * k * 0.25
    return out
