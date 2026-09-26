"""Firefly parametric parts (pure numpy, deterministic): the hard-surface / sheet pieces that ride on the SDF body shell.

Every generator returns a Part: V (n,3) float, F list of vertex-index lists (tris / quads), R list of per-face region
names. Closed solids are built as surfaces of revolution with poles on the axis, or as closed tubes with caps, so each
closed part has 0 open edges; the only open pieces are the wing SHEETS (single surface, the smoke material is
double-sided) and the goggle lens DOMES (their open rim is buried under the goggle rim torus).

    lathe          profile (r, h) path revolved about an axis; ends on the axis become poles; optional anisotropic
                   radius (oval ports) and a per-vertex radius modulation (flame tongues)
    torus          ring tube (goggle rims), optional oval
    strap          flat rectangular band laid on an ellipsoid along a direction path (helmet straps)
    hex_lens       domed disc tiled by a honeycomb: per cell an inner hexagon (lens_cell) + a border ring (lens_grid)
    feather        flat serrated feather vane (diamond cross-section) along a curved rachis + the rachis tube
    wing_sheet     one smoke CURTAIN: a hole-sized neck that pours straight out of the side-port hole along the port
                   axis, then a top edge arcing out in a swept plane with the smoke hanging below it (ragged bottom and
                   outer end, billow + folds, trailing back); returns the chain line and each vertex's arc parameter s
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


# --------------------------------------------------------------------------- lathe
def lathe(profile, seg_regions, n, centre, axis, up_hint=(0, 0, 1), su=1.0, sv=1.0, rmod=None, phase=0.0):
    """profile: [(r, h), ...] path; an end with r == 0 becomes a pole. seg_regions: region per profile segment
    (len(profile) - 1). su / sv scale the radius along the local up (u) / side (v) axes (oval). rmod(k, theta) -> radius
    multiplier for profile point k (flame tongues). Faces are oriented outward for a path that runs from the top pole
    DOWN the outside (h decreasing) or, equivalently, any path whose left side is the solid's interior."""
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


def orient_outward(V, F, inside_pt):
    """Flip the whole face list if the signed volume about inside_pt is negative (the path ran the other way)."""
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - inside_pt
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - inside_pt, V[f[i + 1]] - inside_pt))
    return F if vol >= 0 else [f[::-1] for f in F]


def torus(R0, r, n_major, n_minor, centre, axis, up_hint=(0, 0, 1), su=1.0, sv=1.0, region="rim"):
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    V, F = [], []
    for i in range(n_major):
        t = 2 * math.pi * i / n_major
        radial = unit(math.cos(t) * su * u + math.sin(t) * sv * v)
        ring_c = c + R0 * (math.cos(t) * su * u + math.sin(t) * sv * v)
        for j in range(n_minor):
            p = 2 * math.pi * j / n_minor
            V.append(ring_c + r * (math.cos(p) * radial + math.sin(p) * a))
    for i in range(n_major):
        for j in range(n_minor):
            i1, j1 = (i + 1) % n_major, (j + 1) % n_minor
            F.append([i * n_minor + j, i1 * n_minor + j, i1 * n_minor + j1, i * n_minor + j1])
    V = np.array(V)
    # outward = away from the tube centre line
    f0 = F[0]
    nrm = np.cross(V[f0[1]] - V[f0[0]], V[f0[2]] - V[f0[0]])
    tube_c = c + R0 * (su * u)
    if np.dot(nrm, V[f0[0]] - tube_c) < 0:
        F = [f[::-1] for f in F]
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- ellipsoid straps
def ell_point(c, radii, d):
    """Ray from the ellipsoid centre along d -> the surface point and its outward normal."""
    d = unit(d)
    r = np.asarray(radii, float)
    lam = 1.0 / math.sqrt(float(np.sum((d / r) ** 2)))
    p = np.asarray(c, float) + lam * d
    n = unit((p - c) / (r * r))
    return p, n


def strap(c, radii, dirs, width, thick, sink, closed, region="strap"):
    """Rectangular band along the ellipsoid surface points hit by the direction list dirs."""
    P, N = zip(*[ell_point(c, radii, d) for d in dirs])
    P, N = np.array(P), np.array(N)
    m = len(P)
    T = np.zeros_like(P)
    for i in range(m):
        if closed:
            T[i] = P[(i + 1) % m] - P[i - 1]
        else:
            T[i] = P[min(i + 1, m - 1)] - P[max(i - 1, 0)]
    T = np.array([unit(t) for t in T])
    Bn = np.array([unit(np.cross(n, t)) for n, t in zip(N, T)])
    V = []
    for p, n, b in zip(P, N, Bn):
        V += [p - b * width / 2 - n * sink, p - b * width / 2 + n * thick,
              p + b * width / 2 + n * thick, p + b * width / 2 - n * sink]
    V = np.array(V)
    Cl = P + N * (thick - sink) / 2                     # the band's own centre line
    F, ref = [], []
    segs = m if closed else m - 1
    for i in range(segs):
        i1 = (i + 1) % m
        for k in range(4):
            k1 = (k + 1) % 4
            F.append([i * 4 + k, i * 4 + k1, i1 * 4 + k1, i1 * 4 + k]); ref.append((Cl[i] + Cl[i1]) / 2)
    if not closed:
        F.append([0, 1, 2, 3]); ref.append(Cl[1])
        F.append([(m - 1) * 4 + k for k in range(4)]); ref.append(Cl[m - 2])
    Fo = []
    for f, rp in zip(F, ref):                          # every face points away from its reference point
        q = V[f]
        nrm = np.cross(q[1] - q[0], q[2] - q[0]) + np.cross(q[2] - q[0], q[3] - q[0])
        Fo.append(f if np.dot(nrm, q.mean(0) - rp) >= 0 else f[::-1])
    return V, Fo, [region] * len(Fo)


# --------------------------------------------------------------------------- honeycomb lens
def hex_lens(centre, axis, radius, dome, cell, inset, up_hint=(0, 0, 1), rim_clamp=1.0):
    """Domed honeycomb disc. Cells whose centre lies within radius + cell are kept; any vertex beyond radius*rim_clamp is
    pulled back radially onto that circle (the goggle rim torus covers the edge). Returns V, F, R (lens_cell / lens_grid)."""
    a, u, v = frame_from_axis(axis, up_hint)
    c = np.asarray(centre, float)
    Rc = radius * rim_clamp
    w = math.sqrt(3.0) * cell
    n = int(math.ceil(radius / w)) + 2
    key, V2, F, R = {}, [], [], []

    def vid(p):
        k = (round(p[0] / cell * 64), round(p[1] / cell * 64))
        if k not in key:
            key[k] = len(V2); V2.append(p)
        return key[k]
    corners = [(cell * math.cos(math.pi / 6 + i * math.pi / 3), cell * math.sin(math.pi / 6 + i * math.pi / 3)) for i in range(6)]
    for q in range(-n, n + 1):
        for r_ in range(-n, n + 1):
            cx = w * (q + r_ / 2.0); cy = 1.5 * cell * r_
            if math.hypot(cx, cy) > radius + 0.5 * cell:
                continue
            ci = vid((cx, cy))
            outer = [vid((cx + x, cy + y)) for x, y in corners]
            inner = [vid((cx + inset * x, cy + inset * y)) for x, y in corners]
            for i in range(6):
                i1 = (i + 1) % 6
                F.append([ci, inner[i], inner[i1]]); R.append("lens_cell")
                F.append([inner[i], outer[i], outer[i1], inner[i1]]); R.append("lens_grid")
    P = np.array(V2)
    rr = np.hypot(P[:, 0], P[:, 1])
    k = np.where(rr > Rc, Rc / np.maximum(rr, 1e-12), 1.0)
    P = P * k[:, None]
    rr = np.minimum(rr, Rc)
    h = dome * (1.0 - (rr / radius) ** 2)
    V = c + P[:, 0:1] * u + P[:, 1:2] * v + h[:, None] * a
    # orient toward +axis (outward), drop faces the clamp collapsed
    Fo, Ro = [], []
    for f, reg in zip(F, R):
        q = V[f]
        nrm = np.cross(q[1] - q[0], q[2] - q[0])
        if len(f) == 4:
            nrm = nrm + np.cross(q[2] - q[0], q[3] - q[0])
        if np.linalg.norm(nrm) < 1e-10:
            continue
        Fo.append(f if np.dot(nrm, a) >= 0 else f[::-1]); Ro.append(reg)
    return V, Fo, Ro


# --------------------------------------------------------------------------- feather antenna
def feather(base, tip_dir, length, bend, vane_normal, vane_s0, half_w, serr_n, serr_depth, thick, stalk_r,
            n_st=30, n_stalk=6):
    """Flat serrated feather. Rachis: quadratic curve from base along tip_dir, bent by 'bend' (a world vector added at the
    middle). The vane spans vane_s0..1 of the rachis in the plane whose normal is vane_normal; half-width profile is a
    leaf (sin), each edge cut by serr_n sawtooth notches of relative depth serr_depth (teeth point toward the tip).
    Returns (V, F, R, s_of_vertex, rachis_points)."""
    b = np.asarray(base, float)
    tip = b + unit(tip_dir) * length
    mid = (b + tip) / 2 + np.asarray(bend, float)
    s = np.linspace(0.0, 1.0, n_st)
    C = ((1 - s) ** 2)[:, None] * b + (2 * (1 - s) * s)[:, None] * mid + (s ** 2)[:, None] * tip
    T = np.gradient(C, axis=0); T = np.array([unit(t) for t in T])
    nv = unit(vane_normal)
    W = np.array([unit(np.cross(nv, t)) for t in T])            # in-vane width direction
    Nn = np.array([unit(np.cross(t, w)) for t, w in zip(T, W)])  # vane normal, re-orthogonalised
    V, F, R, S = [], [], [], []
    # vane (diamond cross-section: left edge, front ridge, right edge, back ridge)
    u = np.clip((s - vane_s0) / (1 - vane_s0), 0, 1)
    prof = np.sin(np.pi * np.clip(u, 0, 1) ** 0.75) * (u > 0)
    saw = (u * serr_n) % 1.0                                     # 0 at a notch root .. 1 at the next tooth tip
    wl = half_w * prof * (1 - serr_depth * (1 - saw))
    wr = half_w * prof * (1 - serr_depth * (1 - ((u * serr_n + 0.5) % 1.0)))   # the other edge offset half a tooth
    iv = [i for i in range(n_st) if u[i] > 0]
    i0 = max(iv[0] - 1, 0)
    rows = []
    for i in range(i0, n_st):
        th = thick * (0.3 + 0.7 * prof[i])
        pts = [C[i] - W[i] * wl[i], C[i] + Nn[i] * th, C[i] + W[i] * wr[i], C[i] - Nn[i] * th]
        if i == n_st - 1:
            pts = [C[i]]
        rows.append(list(range(len(V), len(V) + len(pts))))
        V += pts; S += [s[i]] * len(pts)
    for a_, b_ in zip(rows[:-1], rows[1:]):
        if len(b_) == 1:
            for k in range(4):
                F.append([a_[k], a_[(k + 1) % 4], b_[0]]); R.append("antenna_vane")
        else:
            for k in range(4):
                F.append([a_[k], a_[(k + 1) % 4], b_[(k + 1) % 4], b_[k]]); R.append("antenna_vane")
    F.append(rows[0][::-1]); R.append("antenna_vane")
    # rachis tube (hexagonal), base -> tip, dark stalk; its radius exceeds the vane half-thickness so the dark line shows
    ring = []
    for i in range(0, n_st - 1):
        r_ = stalk_r * (1.0 - 0.75 * s[i])
        ids = []
        for k in range(n_stalk):
            t = 2 * math.pi * k / n_stalk
            ids.append(len(V)); V.append(C[i] + r_ * (math.cos(t) * W[i] + math.sin(t) * Nn[i])); S.append(s[i])
        ring.append(ids)
    tip_id = len(V); V.append(C[-1] + T[-1] * stalk_r); S.append(1.0)
    base_id = len(V); V.append(C[0] - T[0] * stalk_r); S.append(0.0)
    for a_, b_ in zip(ring[:-1], ring[1:]):
        for k in range(n_stalk):
            F.append([a_[k], a_[(k + 1) % n_stalk], b_[(k + 1) % n_stalk], b_[k]]); R.append("antenna_stalk")
    for k in range(n_stalk):
        F.append([ring[-1][k], ring[-1][(k + 1) % n_stalk], tip_id]); R.append("antenna_stalk")
        F.append([ring[0][(k + 1) % n_stalk], ring[0][k], base_id]); R.append("antenna_stalk")
    V = np.array(V)
    Fo = []
    for f in F:                                       # orient every face away from the local rachis point
        q = V[f]
        nrm = np.cross(q[1] - q[0], q[2] - q[0])
        sm = float(np.mean([S[i] for i in f]))
        j = int(np.clip(round(sm * (n_st - 1)), 0, n_st - 1))
        Fo.append(f if np.dot(nrm, q.mean(0) - C[j]) >= 0 else f[::-1])
    return V, Fo, R, np.array(S), C


# --------------------------------------------------------------------------- smoke wing sheet
def _hash01(i, seed):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


def wing_sheet(floor_pt, exit_pt, e_a, e_b, top2d, root_half, drop_max, ns, nt, rag_end, rag_edge, billow, fold, trail,
               seed, back=(0.0, 1.0, 0.0), tuck=0.0):
    """One smoke CURTAIN leaving a side-port hole (the reference's wings are broad hanging smoke, not strips).
    floor_pt -> exit_pt: the NECK -- a hole-sized vertical ribbon that pours straight out of the hole along the port axis
    (the root sits on the hole floor). From exit_pt the curtain's TOP EDGE follows top2d (plane coords (a, b) along
    e_a = outward-back sweep, e_b = up; first point (0, root_half)) and the curtain HANGS below it by drop(s): hole-sized at
    the root, growing to drop_max, a little shorter at the outer end. The hanging part trails back (trail) and billows /
    folds out of the plane (billow, fold). The bottom edge and the outer end are ragged (deterministic hash per column /
    row). Returns V, F, s (per vertex arc fraction along the top edge, 0 = hole floor .. 1 = outer end), spine3d (the
    chain line, 25 % down the curtain), faces_ij (per face (i along, j down)), info."""
    e_a, e_b = unit(e_a), unit(e_b)
    n_pl = unit(np.cross(e_a, e_b))
    bk = unit(back)
    floor_pt = np.asarray(floor_pt, float); exit_pt = np.asarray(exit_pt, float)
    P2 = np.asarray(top2d, float)
    P2e = np.vstack([2 * P2[0] - P2[1], P2, 2 * P2[-1] - P2[-2]])
    dense = []
    for i in range(1, len(P2e) - 2):
        p0, p1, p2, p3 = P2e[i - 1], P2e[i], P2e[i + 1], P2e[i + 2]
        for t in np.linspace(0, 1, 16, endpoint=False):
            t2, t3 = t * t, t * t * t
            dense.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    dense.append(P2[-1])
    D2 = np.array(dense)
    L2 = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(D2, axis=0), axis=1))])
    Ln = float(np.linalg.norm(exit_pt - floor_pt))
    Ltot = Ln + L2[-1]
    s_nk = Ln / Ltot
    rows = np.concatenate([[0.0, 0.5 * s_nk], s_nk + (1.0 - s_nk) * np.linspace(0.0, 1.0, ns - 1) ** 1.1])
    drop_rag = [1.0 - rag_end * (0.35 + 0.65 * _hash01(i + 1, seed)) * (1.0 if i % 2 == 0 else 0.3) for i in range(len(rows))]
    end_rag = [1.0 - rag_edge * (0.3 + 0.7 * _hash01(j + 3, seed + 2)) * (1.0 if j % 2 == 1 else 0.2) for j in range(nt + 1)]
    V, S = [], []
    grid = np.zeros((len(rows), nt + 1), dtype=int)
    spine3d = []
    for i, s_ in enumerate(rows):
        for j in range(nt + 1):
            t_ = j / nt
            s_j = s_ * (1.0 - (1.0 - end_rag[j]) * max(0.0, (s_ - 0.75) / 0.25) ** 2)   # ragged outer end
            arc = s_j * Ltot
            if arc <= Ln:
                c = floor_pt + (exit_pt - floor_pt) * (arc / max(Ln, 1e-9))
                top = c + e_b * root_half
                D = 2.0 * root_half
                p = top - e_b * (t_ * D)
                u = 0.0
            else:
                sl = arc - Ln
                u = sl / L2[-1]
                a_ = np.interp(sl, L2, D2[:, 0]); b_ = np.interp(sl, L2, D2[:, 1])
                top = exit_pt + a_ * e_a + b_ * e_b
                g = min(1.0, u / 0.45)
                D = 2.0 * root_half + (drop_max - 2.0 * root_half) * (g * g * (3 - 2 * g))
                D *= 1.0 - 0.40 * max(0.0, (u - 0.70) / 0.30) ** 1.5
                if u > 0.25:
                    D *= drop_rag[i] ** min(1.0, (u - 0.25) / 0.2)
                ramp = min(1.0, u / 0.25)
                off = ramp * (billow * math.sin(math.pi * min(u, 1.0)) * (0.45 + 0.55 * t_) +
                              fold * math.sin(2 * math.pi * 2.2 * u + 2.6 * t_ + seed) * t_)
                p = top - e_b * (t_ * D) + bk * (trail * ramp * (t_ ** 1.5) * D) + n_pl * off \
                    - e_a * (tuck * (t_ ** 1.5) * min(u, 1.0))     # leaf taper: the lower smoke tucks back inward
            V.append(p); S.append(s_j)
            grid[i, j] = len(V) - 1
        spine3d.append(V[grid[i, 0]] - e_b * 0.25 * D if arc > Ln else floor_pt + (exit_pt - floor_pt) * (arc / max(Ln, 1e-9)))
    F, IJ = [], []
    for i in range(len(rows) - 1):
        for j in range(nt):
            F.append([grid[i, j], grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]]); IJ.append((i, j))
    return np.array(V), F, np.array(S), np.array(spine3d), IJ, {"neck_len": Ln, "length": Ltot, "s_neck": s_nk,
                                                                 "rows": len(rows), "cols": nt}
