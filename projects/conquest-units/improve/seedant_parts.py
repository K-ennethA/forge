"""Seedant parametric parts (pure numpy, deterministic): the closed pieces that ride on the SDF body shell.

Copied from firefly_parts.py (Part, open_edges, frame_from_axis, lathe, orient_outward, ell_point) + the seedant
generators. Every generator returns V (n,3) float, F list of vertex-index lists, R list of per-face region names; every
part is a CLOSED solid (0 open edges):

    lathe        profile (r, h) path revolved about an axis; ends on the axis become poles; su / sv = an OVAL section
                 (the pot: tapered wall, rolled rim band, rim lip, inner wall, soil disc -- one closed solid of revolution)
    tube         closed tube along a polyline (parallel-transport frames, per-station radius, rounded end caps): the
                 sprout curl and the two arm-tip curls
    spiral_2d    a fiddlehead path in a plane: optional stem, then a spiral winding inward
    leaf_blade   tapered leaf with a lens cross-section, folded on the midrib; the surface grid is SHEARED along the
                 side-vein direction so the midrib (a column strip) and the side veins (row strips) are clean face strips
    decal        closed conforming decal: a domed top surface (centre + rings of an outline) over a sunk bottom fan,
                 joined by a side wall; the caller supplies the lift (projection onto the body)
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


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


def orient_outward(V, F, inside_pt):
    """Flip the whole face list if the signed volume about inside_pt is negative (the path ran the other way)."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


# --------------------------------------------------------------------------- lathe (firefly_parts.lathe, unchanged)
def lathe(profile, seg_regions, n, centre, axis, up_hint=(0, 0, 1), su=1.0, sv=1.0, rmod=None, phase=0.0):
    """profile: [(r, h), ...] path; an end with r == 0 becomes a pole. seg_regions: region per profile segment.
    su / sv scale the radius along the local up (u) / side (v) axes (oval)."""
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


# --------------------------------------------------------------------------- tube
def transport_frames(C, up_hint=(0.0, 0.0, 1.0)):
    """Parallel-transport frames along polyline C -> (T, N, B) per point (flip-free, deterministic)."""
    C = np.asarray(C, float)
    T = np.gradient(C, axis=0)
    T = np.array([unit(t) for t in T])
    n0 = np.asarray(up_hint, float) - T[0] * float(np.dot(up_hint, T[0]))
    if np.linalg.norm(n0) < 1e-6:
        n0 = np.cross(T[0], [1.0, 0.0, 0.0])
    N = [unit(n0)]
    for i in range(1, len(C)):
        n = N[-1] - T[i] * float(np.dot(N[-1], T[i]))
        if np.linalg.norm(n) < 1e-9:
            n = N[-1]
        N.append(unit(n))
    N = np.array(N)
    B = np.cross(T, N)
    return T, N, B


def tube(C, radii, n_ring, region, cap0=0.6, cap1=0.9, up_hint=(0.0, 0.0, 1.0)):
    """Closed tube along C (m,3) with per-point radius radii (m,). Both ends close on a pole pushed out along the end
    tangent by cap x radius (a rounded cap). Returns V, F, R, s (per-vertex arc fraction 0..1)."""
    C = np.asarray(C, float)
    r = np.asarray(radii, float)
    T, N, B = transport_frames(C, up_hint)
    al = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    sf = al / max(al[-1], 1e-12)
    V, S, rings = [], [], []
    for i in range(len(C)):
        ids = []
        for k in range(n_ring):
            t = 2 * math.pi * k / n_ring
            ids.append(len(V)); V.append(C[i] + r[i] * (math.cos(t) * N[i] + math.sin(t) * B[i])); S.append(sf[i])
        rings.append(ids)
    p0 = len(V); V.append(C[0] - T[0] * r[0] * cap0); S.append(0.0)
    p1 = len(V); V.append(C[-1] + T[-1] * r[-1] * cap1); S.append(1.0)
    F = []
    for a_, b_ in zip(rings[:-1], rings[1:]):
        for k in range(n_ring):
            F.append([a_[k], a_[(k + 1) % n_ring], b_[(k + 1) % n_ring], b_[k]])
    for k in range(n_ring):
        F.append([rings[0][(k + 1) % n_ring], rings[0][k], p0])
        F.append([rings[-1][k], rings[-1][(k + 1) % n_ring], p1])
    V = np.array(V)
    S = np.array(S)
    Fo = []
    for f in F:                                  # every face away from its local centre-line point
        q = V[f]
        nrm = np.cross(q[1] - q[0], q[2] - q[0])
        if len(f) == 4:
            nrm = nrm + np.cross(q[2] - q[0], q[3] - q[0])
        j = int(np.argmin(np.abs(sf - float(np.mean(S[f])))))
        ref = C[j]
        Fo.append(f if np.dot(nrm, q.mean(0) - ref) >= 0 else f[::-1])
    return V, Fo, [region] * len(Fo), S


def spiral_2d(r0, r1, turns, theta0, n, stem=None, sense=1.0):
    """Fiddlehead path in plane coords: optional stem polyline [(x, y), ...] that ends where the spiral starts, then a
    spiral of radius r0 -> r1 (exponential decay) over 'turns' turns starting at angle theta0 (deg), winding with
    'sense' (+1 counter-clockwise). Returns (m,2)."""
    th = np.linspace(0.0, 2 * math.pi * turns, n)
    rr = r0 * (r1 / r0) ** (th / th[-1])
    a = math.radians(theta0) + sense * th
    P = np.stack([rr * np.cos(a), rr * np.sin(a)], 1)
    if stem is not None:
        St = np.asarray(stem, float)
        P = np.vstack([St[:-1], P]) if np.linalg.norm(St[-1] - P[0]) < 1e-6 else np.vstack([St, P])
    return P


# --------------------------------------------------------------------------- leaf blade
def leaf_blade(C, face_n, half_w, thick, fold, n_sig, w_cols, mid_w, veins, vein_w, vein_reach, shear):
    """Tapered leaf along the midrib polyline C (dense, base -> tip).
    face_n: the blade's face normal hint (world); per station W = unit(face_n x T), N = T x W (re-orthogonalised).
    half_w(s), thick(s): callables on the midrib arc fraction s in [0, 1] (both 0 at the base and the tip poles).
    fold: the edges rise along +N by fold x |w| x half_w (folded on the midrib).
    Surface grid: rows sigma in (0, 1) (n_sig uniform rows + two rows per vein at sigma_k, sigma_k + vein_w), columns
    w_cols = increasing relative offsets in [0, 1] for one half (0 excluded, 1 = the edge); the midrib strip is
    |w| < mid_w. The arc position of a grid point is s = 1 - (1 - sigma)^(1 + shear |w|): rows bend toward the tip as
    they leave the midrib, so each vein is a clean row strip. Lens section: top = +thick (1 - w^2), bottom = -thick; the midrib + vein strips are painted on BOTH faces.
    Returns V, F, R (leaf / leaf_midrib / leaf_vein), s per vertex."""
    C = np.asarray(C, float)
    al = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    sf = al / al[-1]
    T = np.gradient(C, axis=0); T = np.array([unit(t) for t in T])
    W = np.array([unit(np.cross(face_n, t)) for t in T])
    N = np.array([unit(np.cross(t, w)) for t, w in zip(T, W)])

    def at(s):
        i = np.clip(np.searchsorted(sf, s) - 1, 0, len(sf) - 2)
        u = (s - sf[i]) / max(sf[i + 1] - sf[i], 1e-12)
        lerp = lambda A: A[i] * (1 - u) + A[i + 1] * u
        return lerp(C), unit(lerp(W)), unit(lerp(N))
    rows = list(np.linspace(0.0, 1.0, n_sig + 2)[1:-1])
    vein_rows = set()
    for sk in veins:
        rows += [sk, sk + vein_w]
    rows = sorted(set(round(r, 6) for r in rows))
    for sk in veins:
        vein_rows.add(rows.index(round(sk, 6)))
    half = [m for m in [mid_w] + list(w_cols)]
    wl = [-w for w in half[::-1]] + half                       # left edge ... -mid, +mid ... right edge
    nw = len(wl)
    V, S, ring = [], [], []
    base_id = len(V); V.append(C[0]); S.append(0.0)
    for sg in rows:
        ids_top, ids_bot = [], []
        for j, w in enumerate(wl):
            s = 1.0 - (1.0 - sg) ** (1.0 + shear * abs(w))
            c, wv, nv = at(s)
            hw = half_w(s)
            th = thick(s) * (1.0 - w * w)
            p = c + wv * (w * hw) + nv * (fold * abs(w) * hw)
            if j == 0 or j == nw - 1:                           # the edge: shared by top and bottom
                ids_top.append(len(V)); V.append(p); S.append(s)
                ids_bot.append(ids_top[-1])
            else:
                ids_top.append(len(V)); V.append(p + nv * th); S.append(s)
                ids_bot.append(len(V)); V.append(p - nv * th); S.append(s)
        ring.append((ids_top, ids_bot))
    tip_id = len(V); V.append(C[-1]); S.append(1.0)
    V = np.array(V); S = np.array(S)
    F, R, side = [], [], []

    def reg_top(i, j):
        wa, wb = wl[j], wl[j + 1]
        if wa < 0 < wb:
            return "leaf_midrib"
        if i in vein_rows and max(abs(wa), abs(wb)) <= vein_reach + 1e-9:
            return "leaf_vein"
        return "leaf"
    for i in range(len(rows) - 1):
        (ta, ba), (tb, bb) = ring[i], ring[i + 1]
        for j in range(nw - 1):
            F.append([ta[j], ta[j + 1], tb[j + 1], tb[j]]); R.append(reg_top(i, j)); side.append(1.0)
            F.append([ba[j], bb[j], bb[j + 1], ba[j + 1]]); R.append(reg_top(i, j)); side.append(-1.0)   # veins both faces
    t0, b0 = ring[0]
    t1, b1 = ring[-1]
    for j in range(nw - 1):
        F.append([base_id, t0[j + 1], t0[j]]); R.append("leaf"); side.append(1.0)
        F.append([base_id, b0[j], b0[j + 1]]); R.append("leaf"); side.append(-1.0)
        F.append([t1[j], t1[j + 1], tip_id]); R.append("leaf"); side.append(1.0)
        F.append([b1[j + 1], b1[j], tip_id]); R.append("leaf"); side.append(-1.0)
    Fo, Ro = [], []
    for f, reg, sd in zip(F, R, side):
        q = V[f]
        nrm = np.cross(q[1] - q[0], q[2] - q[0])
        if len(f) == 4:
            nrm = nrm + np.cross(q[2] - q[0], q[3] - q[0])
        s_m = float(np.clip(np.mean(S[f]), 0, 1))
        _, _, nv = at(s_m)
        Fo.append(f if np.dot(nrm, nv) * sd >= 0 else f[::-1]); Ro.append(reg)
    return V, Fo, Ro, S


# --------------------------------------------------------------------------- conforming decal
def decal(outline, n_ring, lift):
    """Closed decal from a star-shaped 2D outline (k,2) around its origin. Top surface = the origin + n_ring rings
    (ring j = outline x j / n_ring); bottom = the origin + the outer ring (a fan); a side wall joins the two outer rings.
    lift(P2 (m,2), rho (m,)) -> (top (m,3), bottom (m,3), normal (m,3)) projects the 2D points onto the body (rho = the
    relative radius 0..1, so the caller can dome the top). Faces: top along +normal, bottom along -normal, wall outward.
    Returns V, F (top faces first), n_top_faces."""
    O = np.asarray(outline, float)
    k = len(O)
    P2 = [np.zeros(2)] + [O[i] * (j / n_ring) for j in range(1, n_ring + 1) for i in range(k)]
    rho = np.array([0.0] + [j / n_ring for j in range(1, n_ring + 1) for _ in range(k)])
    P2 = np.array(P2)
    top, bot, nrm = lift(P2, rho)
    nt = len(P2)
    outer = list(range(1 + (n_ring - 1) * k, nt))
    V = np.vstack([top, bot[[0] + outer]])
    bc = nt
    bo = [nt + 1 + i for i in range(k)]
    F, want = [], []
    ringid = lambda j, i: 1 + (j - 1) * k + (i % k)
    for i in range(k):
        F.append([0, ringid(1, i), ringid(1, i + 1)]); want.append(nrm[0])
    for j in range(1, n_ring):
        for i in range(k):
            F.append([ringid(j, i), ringid(j + 1, i), ringid(j + 1, i + 1), ringid(j, i + 1)]); want.append(nrm[ringid(j, i)])
    n_top = len(F)
    for i in range(k):
        F.append([bc, bo[(i + 1) % k], bo[i]]); want.append(-nrm[0])
    for i in range(k):
        a, b = outer[i], outer[(i + 1) % k]
        F.append([a, bo[i], bo[(i + 1) % k], b])
        want.append(None)
    Fo = []
    ctr = top[0]
    for f, wv in zip(F, want):
        q = V[f]
        n_ = np.cross(q[1] - q[0], q[2] - q[0])
        if len(f) == 4:
            n_ = n_ + np.cross(q[2] - q[0], q[3] - q[0])
        ref = wv if wv is not None else q.mean(0) - ctr
        Fo.append(f if np.dot(n_, ref) >= 0 else f[::-1])
    return V, Fo, n_top
