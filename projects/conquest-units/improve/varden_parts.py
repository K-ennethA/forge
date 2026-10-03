"""Varden parametric parts: a copy of elias_parts.py (itself wren_parts.py; other units' files are read-only) with the
Elias staff / tome generators replaced by Varden's own at the end: the back-emblem crest strokes, the star motif plate,
the radial brooch, the longsword, the scabbard. Original wren_parts docstring:
Wren parametric parts (pure numpy, deterministic): the outfit / hair / pitchfork pieces that ride on the MPFB2 body.
vampwarrior_parts.py's generic generators (lathe / torus / solidify / grid / lens_ring / loft / catmull / resample) are
COPIED here (another unit's file is read-only), plus Wren's own: tube along a path (parallel-transport frames, per-facet
regions), braided rope, bipyramid crystal, rounded box (the belt pouch), pitchfork.

Every generator returns (V (n,3) float, F list of vertex-index lists, R list of per-face region names). Every part is a
CLOSED solid (0 open edges): sheets go through solidify() (outer skin, inner skin, rim strip along every boundary).
"""
import math
import numpy as np


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def frame_from_axis(axis, up_hint=(0.0, 0.0, 1.0)):
    a = unit(axis)
    u = np.asarray(up_hint, float) - a * float(np.dot(up_hint, a))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(a, [1.0, 0.0, 0.0])
    u = unit(u)
    return a, u, np.cross(a, u)


def open_edges(F):
    cnt = {}
    for f in F:
        for i in range(len(f)):
            e = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            cnt[e] = cnt.get(e, 0) + 1
    return int(sum(1 for c in cnt.values() if c == 1))


def signed_volume(V, F, ref=None):
    ref = V.mean(0) if ref is None else np.asarray(ref, float)
    vol = 0.0
    for f in F:
        p0 = V[f[0]] - ref
        for i in range(1, len(f) - 1):
            vol += np.dot(p0, np.cross(V[f[i]] - ref, V[f[i + 1]] - ref))
    return vol / 6.0


def orient_outward(V, F):
    return F if signed_volume(V, F) >= 0 else [f[::-1] for f in F]


def merge(parts):
    """[(V, F, R), ...] -> one (V, F, R)."""
    Vs, Fs, Rs, o = [], [], [], 0
    for V, F, R in parts:
        Vs.append(np.asarray(V, float)); Fs += [[i + o for i in f] for f in F]; Rs += list(R); o += len(V)
    return np.vstack(Vs), Fs, Rs


# --------------------------------------------------------------------------- lathe / torus
def lathe(profile, seg_regions, n, centre, axis, up_hint=(0, 0, 1), su=1.0, sv=1.0, rmod=None, phase=0.0):
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
    return V, orient_outward(V, F), R


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
    f0 = F[0]
    nrm = np.cross(V[f0[1]] - V[f0[0]], V[f0[2]] - V[f0[0]])
    tube_c = c + R0 * (su * u)
    if np.dot(nrm, V[f0[0]] - tube_c) < 0:
        F = [f[::-1] for f in F]
    return V, F, [region] * len(F)


# --------------------------------------------------------------------------- sheets
def vertex_normals(V, F):
    N = np.zeros_like(V)
    for f in F:
        q = V[f]
        n = np.cross(q[1] - q[0], q[2] - q[0])
        if len(f) == 4:
            n = n + np.cross(q[2] - q[0], q[3] - q[0])
        for i in f:
            N[i] += n
    return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


def solidify(V, F, t_out, t_in, reg_out, reg_in, reg_rim, N=None):
    """Open sheet (faces consistently oriented, normal = the OUTER side) -> closed solid. reg_out may be a per-face list.
    t_out / t_in: offsets along the vertex normal (scalars or per-vertex arrays). Rim quads along every boundary edge."""
    V = np.asarray(V, float)
    N = vertex_normals(V, F) if N is None else N
    to = np.broadcast_to(np.asarray(t_out, float), (len(V),))[:, None]
    ti = np.broadcast_to(np.asarray(t_in, float), (len(V),))[:, None]
    n = len(V)
    Vo = V + N * to
    Vi = V - N * ti
    Vs = np.vstack([Vo, Vi])
    ro = reg_out if isinstance(reg_out, (list, tuple, np.ndarray)) else [reg_out] * len(F)
    ri = reg_in if isinstance(reg_in, (list, tuple, np.ndarray)) else [reg_in] * len(F)
    Fs = [list(f) for f in F] + [[i + n for i in f[::-1]] for f in F]
    Rs = list(ro) + list(ri)
    cnt = {}
    for f in F:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            key = (min(a, b), max(a, b))
            cnt.setdefault(key, []).append((a, b))
    for key, uses in cnt.items():
        if len(uses) == 1:
            a, b = uses[0]
            Fs.append([b, a, a + n, b + n]); Rs.append(reg_rim)
    return Vs, Fs, Rs


def grid_faces(nu, nv, closed_u=False, skip=None):
    """Quad faces over a (nv, nu) vertex grid indexed j * nu + i. skip(i, j) -> True drops the quad (tears)."""
    F = []
    for j in range(nv - 1):
        for i in range(nu if closed_u else nu - 1):
            if skip is not None and skip(i, j):
                continue
            i1 = (i + 1) % nu
            F.append([j * nu + i, j * nu + i1, (j + 1) * nu + i1, (j + 1) * nu + i])
    return F


# --------------------------------------------------------------------------- tubes
def lens_ring(c, t_dir, w_dir, half_w, half_t, n=6):
    """Pointed-lens cross-section (n even >= 4): the two sharp corners at +-w_dir, the bulge along the normal."""
    t_dir = unit(t_dir)
    w = unit(np.asarray(w_dir, float) - t_dir * float(np.dot(w_dir, t_dir)))
    nn = np.cross(t_dir, w)
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n
        x = math.cos(a)
        y = math.sin(a) * (1.0 - 0.15 * x * x)
        pts.append(np.asarray(c, float) + w * half_w * x + nn * half_t * y)
    return np.array(pts)


def loft(rings, cap0="pole", cap1="pole", reg="tube", reg_cap=None, pole0=None, pole1=None):
    """Closed tube through rings (list of (m,3), same m). cap 'pole': a fan to pole0/pole1 (default: the ring centroid
    pushed along the tube axis), 'flat': the centroid itself."""
    m = len(rings[0])
    V = [np.asarray(r, float) for r in rings]
    Vf = np.vstack(V)
    F, R = [], []
    for k in range(len(rings) - 1):
        for i in range(m):
            i1 = (i + 1) % m
            F.append([k * m + i, k * m + i1, (k + 1) * m + i1, (k + 1) * m + i]); R.append(reg)
    rc = reg if reg_cap is None else reg_cap
    extra = []
    c0 = V[0].mean(0); c1 = V[-1].mean(0)
    ax0 = unit(c0 - V[1].mean(0)); ax1 = unit(c1 - V[-2].mean(0))
    p0 = pole0 if pole0 is not None else (c0 + ax0 * (0.0 if cap0 == "flat" else 0.3 * np.linalg.norm(V[0] - c0, axis=1).mean()))
    p1 = pole1 if pole1 is not None else (c1 + ax1 * (0.0 if cap1 == "flat" else 0.3 * np.linalg.norm(V[-1] - c1, axis=1).mean()))
    n0 = len(Vf); n1 = n0 + 1
    extra = [p0, p1]
    last = (len(rings) - 1) * m
    for i in range(m):
        i1 = (i + 1) % m
        F.append([i1, i, n0]); R.append(rc)
        F.append([last + i, last + i1, n1]); R.append(rc)
    Vall = np.vstack([Vf, np.array(extra)])
    return Vall, orient_outward(Vall, F), R


def catmull(P, n_per=12):
    P = np.asarray(P, float)
    Pp = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    for i in range(1, len(Pp) - 2):
        p0, p1, p2, p3 = Pp[i - 1], Pp[i], Pp[i + 1], Pp[i + 2]
        for t in np.linspace(0, 1, n_per, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P[-1])
    return np.array(out)


def resample(C, n):
    C = np.asarray(C, float)
    L = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    s = np.linspace(0, L[-1], n)
    return np.stack([np.interp(s, L, C[:, k]) for k in range(3)], 1), L[-1]



def gem(centre, axis, r, h, n=8, up_hint=(0, 0, 1), region="gem"):
    """Faceted cabochon: table + crown + girdle + pavilion (low n = facets)."""
    prof = [(0.0, h), (r * 0.55, h), (r, h * 0.35), (r, h * 0.05), (r * 0.5, -h * 0.25), (0.0, -h * 0.35)]
    return lathe(prof, [region] * 5, n, centre, axis, up_hint=up_hint, phase=math.pi / n)


# --------------------------------------------------------------------------- Wren generators
def transport_frames(C, up_hint=(0.0, 0.0, 1.0)):
    """Parallel-transport frames along a polyline: tangents T, normals N, binormals B (rotation-minimising)."""
    C = np.asarray(C, float)
    T = np.gradient(C, axis=0)
    T = T / np.maximum(np.linalg.norm(T, axis=1), 1e-12)[:, None]
    n0 = np.asarray(up_hint, float) - T[0] * float(np.dot(up_hint, T[0]))
    if np.linalg.norm(n0) < 1e-6:
        n0 = np.cross(T[0], [1.0, 0.0, 0.0])
    N = [unit(n0)]
    for k in range(1, len(C)):
        v = np.cross(T[k - 1], T[k])
        s = np.linalg.norm(v)
        if s < 1e-9:
            N.append(N[-1]); continue
        ang = math.atan2(s, float(np.dot(T[k - 1], T[k])))
        a = v / s
        n_ = N[-1]
        n_ = n_ * math.cos(ang) + np.cross(a, n_) * math.sin(ang) + a * float(np.dot(a, n_)) * (1 - math.cos(ang))
        N.append(unit(n_ - T[k] * float(np.dot(n_, T[k]))))
    N = np.array(N)
    return T, N, np.cross(T, N)


def tube_path(C, radius, n, region="tube", up_hint=(0.0, 0.0, 1.0), cap0="pole", cap1="pole", rmod=None,
              closed=False, su=1.0):
    """Tube along polyline C (open: pole / flat caps; closed=True: a ring, no caps). radius: scalar, per-point array or
    fn(s_frac); region: name or fn(k_ring, i_side, s_frac) -> name; rmod(k, theta, s_frac) -> radius multiplier.
    su: cross-section x-stretch (along N). Returns V, F, R, per-vertex arc s."""
    C = np.asarray(C, float)
    m = len(C)
    seg = np.linalg.norm(np.diff(C, axis=0), axis=1)
    S = np.concatenate([[0.0], np.cumsum(seg)])
    Lt = max(S[-1], 1e-9)
    T, N, B = transport_frames(C, up_hint)
    if closed:
        T = np.roll(C, -1, 0) - np.roll(C, 1, 0)
        T = T / np.maximum(np.linalg.norm(T, axis=1), 1e-12)[:, None]
        N = np.array([unit(np.asarray(up_hint, float) - t * float(np.dot(up_hint, t))) for t in T])
        B = np.cross(T, N)
    V, Sv = [], []
    for k in range(m):
        sf = S[k] / Lt
        r = radius(sf) if callable(radius) else (radius[k] if np.ndim(radius) else radius)
        for i in range(n):
            th = 2 * math.pi * i / n
            mm = rmod(k, th, sf) if rmod is not None else 1.0
            V.append(C[k] + r * mm * (math.cos(th) * su * N[k] + math.sin(th) * B[k]))
            Sv.append(S[k])
    F, R = [], []
    kmax = m if closed else m - 1
    for k in range(kmax):
        k1 = (k + 1) % m
        for i in range(n):
            i1 = (i + 1) % n
            F.append([k * n + i, k * n + i1, k1 * n + i1, k1 * n + i])
            R.append(region(k, i, S[k] / Lt) if callable(region) else region)
    V = np.array(V)
    if not closed:
        rc0 = region(0, 0, 0.0) if callable(region) else region
        rc1 = region(m - 1, 0, 1.0) if callable(region) else region
        c0, c1 = C[0], C[-1]
        r0 = np.linalg.norm(V[:n] - c0, axis=1).mean(); r1 = np.linalg.norm(V[-n:] - c1, axis=1).mean()
        p0 = c0 - T[0] * (0.0 if cap0 == "flat" else 0.6 * r0)
        p1 = c1 + T[-1] * (0.0 if cap1 == "flat" else 0.6 * r1)
        i0, i1_ = len(V), len(V) + 1
        V = np.vstack([V, p0, p1])
        Sv += [0.0, S[-1]]
        last = (m - 1) * n
        for i in range(n):
            j = (i + 1) % n
            F.append([j, i, i0]); R.append(rc0)
            F.append([last + i, last + j, i1_]); R.append(rc1)
    return V, orient_outward(V, F), R, np.array(Sv)


def rope(C, r, n=8, pitch=0.018, strands=3, closed=False, up_hint=(0.0, 0.0, 1.0), regs=("rope", "rope_dark"),
         taper=None):
    """Braided / twisted rope along C: strand bulges (strands lobes twisting along the arc, one turn per 'pitch' metres)
    and a two-tone spiral stripe (the groove between the strands darker)."""
    C = np.asarray(C, float)
    S = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])

    def rm(k, th, sf):
        ph = th * strands - 2 * math.pi * strands * S[k] / pitch
        t_ = 1.0 if taper is None else taper(sf)
        return t_ * (1.0 + 0.16 * math.cos(ph))

    def rg(k, i, sf):
        th = 2 * math.pi * (i + 0.5) / n
        ph = th * strands - 2 * math.pi * strands * S[k] / pitch
        return regs[1] if math.cos(ph) < -0.55 else regs[0]
    return tube_path(C, r, n, rg, up_hint=up_hint, rmod=rm, closed=closed)


def crystal(centre, axis, r, h_up, h_dn, n=6, up_hint=(0, 0, 1), region="crystal", su=1.0, sv=1.0, phase=None):
    """Elongated bipyramid (a cut crystal point): n facets round the axis, girdle at the centre."""
    prof = [(0.0, h_up), (r * 0.55, h_up * 0.55), (r, 0.0), (r * 0.6, -h_dn * 0.45), (0.0, -h_dn)]
    return lathe(prof, [region] * 4, n, centre, axis, up_hint=up_hint, su=su, sv=sv,
                 phase=math.pi / n if phase is None else phase)


def ribbon(C, n_out, hw, ht, region="strap"):
    """Flat strap along C lying on a surface: rectangular section (half width hw across, half thickness ht along the
    surface normals n_out), flat end caps."""
    C = np.asarray(C, float)
    T = np.gradient(C, axis=0)
    rings = []
    for k in range(len(C)):
        t = unit(T[k]); n = unit(np.asarray(n_out[k], float) - t * float(np.dot(n_out[k], t)))
        w = np.cross(t, n)
        rings.append(np.array([C[k] + w * hw + n * ht, C[k] - w * hw + n * ht, C[k] - w * hw - n * ht, C[k] + w * hw - n * ht]))
    return loft(rings, "flat", "flat", reg=region)


def rounded_box(centre, ex, ey, ez, hx, hy, hz, nr=3, rows=6, bulge=0.15, region="box"):
    """Pillow box: rounded-rectangle cross sections (corner radius ~ 0.35 x the smaller half size) lofted along ez,
    bulging at mid height (a stuffed pouch)."""
    ex, ey, ez = unit(ex), unit(ey), unit(ez)
    c = np.asarray(centre, float)
    rc = 0.35 * min(hx, hy)
    ring2 = []
    for (sx, sy, a0) in ((1, 1, 0.0), (-1, 1, 90.0), (-1, -1, 180.0), (1, -1, 270.0)):
        cx, cy = sx * (hx - rc), sy * (hy - rc)
        for j in range(nr + 1):
            a = math.radians(a0 + 90.0 * j / nr)
            ring2.append((cx + rc * math.cos(a), cy + rc * math.sin(a)))
    rings = []
    for k in range(rows):
        t = k / (rows - 1)
        z = -hz + 2 * hz * t
        b = 1.0 + bulge * math.sin(math.pi * t) - (0.1 if k in (0, rows - 1) else 0.0)
        rings.append(np.array([c + ex * x * b + ey * y * b + ez * z for x, y in ring2]))
    return loft(rings, "flat", "flat", reg=region)


def pitchfork(cfg):
    """Wooden three-tine pitchfork in its own frame: +Z up the shaft, origin = the butt end on the floor.
    Shaft (8 facets, alternate facets a grain tone), brass butt ferrule, a spiral-wrapped grip, a brass collar under the
    yoke, three tines (the outer two sweep out of the yoke and turn up, tapering to points).
    Returns V, F, R, landmarks (grip centre, collar, tine tips, top)."""
    L = cfg["len"]
    n = 8
    parts = []
    r0, r1 = cfg["shaft_r"]
    y0 = cfg["yoke_at"] * L

    def shaft_r(z):
        return r0 + (r1 - r0) * z / y0

    zs = sorted(set([0.0, 0.004] + list(np.linspace(0.0, y0, 18)) +
                    [cfg["butt"][0], cfg["butt"][0] + 0.004, cfg["butt"][1] - 0.004, cfg["butt"][1],
                     cfg["collar"][0] * L, cfg["collar"][1] * L] +
                    list(np.linspace(cfg["grip"][0] * L, cfg["grip"][1] * L, cfg["grip_rings"]))))
    zs = np.array([z for z in zs if 0.0 <= z <= y0])
    g0, g1 = cfg["grip"][0] * L, cfg["grip"][1] * L
    c0, c1 = cfg["collar"][0] * L, cfg["collar"][1] * L
    b0, b1 = cfg["butt"]

    def region_at(z, i, th):
        if b0 - 1e-9 <= z < b1 - 1e-9 or c0 - 1e-9 <= z < c1 - 1e-9:
            return "ferrule"
        if g0 - 1e-9 <= z < g1 - 1e-9:
            ph = (z - g0) / cfg["wrap_pitch"] - th / (2 * math.pi)
            return "grip_dark" if (ph % 1.0) < 0.28 else "grip"
        return "wood_dark" if i % 2 else "wood"

    def rad_at(z, th):
        r = shaft_r(z)
        if b0 <= z <= b1:
            r += cfg["ferrule_t"] * (1.35 if (z - b0 < 0.005 or b1 - z < 0.005) else 1.0)
        elif c0 <= z <= c1:
            r += cfg["ferrule_t"] * (1.35 if (z - c0 < 0.005 or c1 - z < 0.005) else 1.0)
        elif g0 <= z <= g1:
            ph = (z - g0) / cfg["wrap_pitch"] - th / (2 * math.pi)
            r += cfg["wrap_t"] * (0.55 + 0.45 * math.cos(2 * math.pi * ph))
        else:
            r *= 1.0 + 0.03 * math.sin(z * 37.0 + 1.3 * th) * math.sin(z * 11.0)   # a hand-cut shaft, not a lathe dowel
        return r
    V, F, R = [], [], []
    rings = []
    for k, z in enumerate(zs):
        idx = []
        for i in range(n):
            th = 2 * math.pi * i / n + math.pi / n
            r = rad_at(z, th)
            idx.append(len(V)); V.append((r * math.cos(th), r * math.sin(th), z))
        rings.append(idx)
    for k in range(len(zs) - 1):
        zm = 0.5 * (zs[k] + zs[k + 1])
        for i in range(n):
            i1 = (i + 1) % n
            F.append([rings[k][i], rings[k][i1], rings[k + 1][i1], rings[k + 1][i]])
            R.append(region_at(zm, i, 2 * math.pi * (i + 0.5) / n))
    ib = len(V); V.append((0.0, 0.0, 0.0))
    for i in range(n):
        F.append([rings[0][(i + 1) % n], rings[0][i], ib]); R.append("ferrule")
    it = len(V); V.append((0.0, 0.0, y0 + 0.004))
    for i in range(n):
        F.append([rings[-1][i], rings[-1][(i + 1) % n], it]); R.append("wood")
    V = np.array(V, float)
    parts.append((V, orient_outward(V, F), R))
    # tines: centre straight up out of the yoke, the outer two leave the yoke sideways (+-X), round the shoulder and run up
    tips = []
    tl = cfg["tine_len"]
    sp = cfg["tine_spread"]
    for side in (-1.0, 0.0, 1.0):
        if side == 0.0:
            P = np.array([[0.0, 0.0, y0 - 0.02], [0.0, 0.0, y0 + 0.05], [0.0, 0.0, y0 + 0.2], [0.0, 0.0, y0 + tl + 0.02]])
        else:
            P = np.array([[0.0, 0.0, y0 - 0.015], [side * sp * 0.45, 0.0, y0 + 0.012], [side * sp * 0.92, 0.0, y0 + 0.055],
                          [side * sp, 0.0, y0 + 0.12], [side * sp * 1.02, 0.0, y0 + tl * 0.6], [side * sp * 0.96, 0.0, y0 + tl]])
        Cc = resample(catmull(P, 10), 16)[0]
        rt0, rt1 = cfg["tine_r"]

        def trad(sf, rt0=rt0, rt1=rt1):
            return rt0 + (rt1 - rt0) * sf ** 1.3 if sf < 0.9 else (rt0 + (rt1 - rt0) * 0.9 ** 1.3) * (1.0 - (sf - 0.9) / 0.1 * 0.75)
        Vt, Ft, Rt, _ = tube_path(Cc, trad, 6, lambda k, i, sf: "wood_dark" if i % 2 else "wood", up_hint=(0.0, 1.0, 0.0),
                                  cap0="flat", cap1="pole")
        parts.append((Vt, Ft, Rt))
        tips.append(Cc[-1])
    V, F, R = merge(parts)
    lm = {"butt": np.zeros(3), "grip_centre": np.array([0.0, 0.0, cfg["grip_at"] * L]),
          "collar": np.array([0.0, 0.0, 0.5 * (c0 + c1)]), "tine_tips": np.array(tips),
          "top": float(max(t[2] for t in tips)), "len": L}
    return V, F, R, lm


# =========================================================================== Elias generators
def trident_strokes(h):
    """The royal trident crest as 2D polylines (x right, y up; total height h, centred on the origin): the shaft, the
    U of the side prongs + crossbar, the centre prong, and a V arrowhead on each of the three tips. Shared by the brooch,
    the mantle back and the tome cover (the sheet: the same crest on all three)."""
    S = []
    S.append(np.array([[0.0, -0.50], [0.0, 0.14]]))
    u = [(-0.27, 0.40), (-0.255, 0.27), (-0.20, 0.17), (-0.11, 0.125), (0.0, 0.115), (0.11, 0.125), (0.20, 0.17),
         (0.255, 0.27), (0.27, 0.40)]
    S.append(np.array(u))
    S.append(np.array([[0.0, 0.115], [0.0, 0.44]]))
    for cx, cy in ((-0.27, 0.40), (0.0, 0.44), (0.27, 0.40)):
        S.append(np.array([[cx - 0.065, cy - 0.04], [cx, cy + 0.07], [cx + 0.065, cy - 0.04]]))
    S.append(np.array([[-0.06, -0.47], [0.0, -0.55], [0.06, -0.47]]))      # the butt point
    return [s_ * h for s_ in S]


def strokes_to_parts(strokes2d, origin, ex, ey, ez, hw, ht, region, n_per=6, project=None):
    """2D polylines -> flat ribbon solids on a plane (origin, ex right, ey up, ez out). project(p) -> (surface point,
    normal) lays each sample on a curved surface instead (+ ht out along its normal)."""
    out = []
    ex, ey = np.asarray(ex, float), np.asarray(ey, float)
    for s_ in strokes2d:
        Q3 = np.array([np.asarray(origin, float) + ex * q[0] + ey * q[1] for q in s_])
        if len(s_) > 4:                                       # curves (the U): smoothed
            P = resample(catmull(Q3, n_per), len(s_) * 2)[0]
        else:                                                 # straight runs / the V tips: kept sharp, densified
            P = np.vstack([Q3[i] + (Q3[i + 1] - Q3[i]) * t for i in range(len(Q3) - 1)
                           for t in np.linspace(0.0, 1.0, 4)[:(None if i == len(Q3) - 2 else -1)]])
        C, N = [], []
        for p3 in P:
            if project is not None:
                p3, n3 = project(p3)
            else:
                n3 = np.asarray(ez, float)
            C.append(p3 + n3 * ht); N.append(n3)
        out.append(ribbon(np.array(C), np.array(N), hw, ht, region=region))
    return out


def uv_sphere(c, r, n_lon=12, n_lat=7, region="orb"):
    prof = [(r * math.sin(math.pi * k / n_lat), -r * math.cos(math.pi * k / n_lat)) for k in range(n_lat + 1)]
    prof[0] = (0.0, prof[0][1]); prof[-1] = (0.0, prof[-1][1])
    return lathe(prof, [region] * n_lat, n_lon, c, (0.0, 0.0, 1.0), up_hint=(0, 1, 0))


# =========================================================================== Varden generators
def varden_crest_strokes(h):
    """The angular kingdom TRIDENT-CREST EMBLEM of the cloak back (sheet: cloak emblem detail panel) as 2D polylines (x
    right, y up; total height h, centred): the centre spine under a four-point star, the inner chevron, the inner and outer
    prongs (verticals turning down-inward into the trident's bowl), the outer prongs' flicks, the lower diamond and the
    three tassel strokes under it."""
    S = []
    S.append(np.array([[0.0, 0.36], [0.0, -0.30]]))                                   # spine
    S.append(np.array([[-0.075, 0.42], [0.0, 0.52], [0.075, 0.42], [0.0, 0.33], [-0.075, 0.42]]))   # four-point star
    S.append(np.array([[-0.19, 0.22], [0.0, 0.07], [0.19, 0.22]]))                    # inner chevron
    for sg in (-1.0, 1.0):
        S.append(np.array([[sg * 0.17, 0.24], [sg * 0.17, 0.02], [sg * 0.05, -0.17]]))    # inner prong
        S.append(np.array([[sg * 0.34, 0.26], [sg * 0.34, -0.03], [sg * 0.11, -0.31]]))   # outer prong
        S.append(np.array([[sg * 0.34, 0.26], [sg * 0.40, 0.33]]))                        # outer flick
    S.append(np.array([[-0.065, -0.40], [0.0, -0.33], [0.065, -0.40], [0.0, -0.47], [-0.065, -0.40]]))   # lower diamond
    for x in (-0.03, 0.0, 0.03):
        S.append(np.array([[x, -0.49], [x, -0.58]]))                                     # tassel
    return [s_ * h for s_ in S]


def star_plate(centre, n_out, up, r_out, r_in, npts, t, region="brass", bulge=1.6):
    """A flat n-point star plate (the kingdom-emblem motif): star polygon in the plane (n_out = its normal), a raised centre
    (bulge x t), back face, rim. Closed solid."""
    n_out = unit(n_out)
    ex = unit(np.cross(up, n_out)); ey = np.cross(n_out, ex)
    c = np.asarray(centre, float)
    ring = []
    for k in range(2 * npts):
        a = math.pi * k / npts + math.pi / 2
        r = r_out if k % 2 == 0 else r_in
        ring.append(c + ex * r * math.cos(a) + ey * r * math.sin(a))
    ring = np.array(ring)
    m = len(ring)
    V = np.vstack([ring + n_out * t, ring - n_out * 0.0005, [c + n_out * t * bulge], [c - n_out * 0.0005]])
    F, R = [], []
    ct, cb = 2 * m, 2 * m + 1
    for k in range(m):
        k1 = (k + 1) % m
        F.append([k, k1, ct]); R.append(region)
        F.append([m + k1, m + k, cb]); R.append(region)
        F.append([k1, k, m + k, m + k1]); R.append(region)
    return V, orient_outward(V, F), R


def radial_brooch(cfg):
    """The round gold BROOCH in its own frame (origin = the disc centre on the cloth, +Z out of the disc): a rimmed disc,
    a raised pyramidal centre boss, radial ridges, studs round the rim (sheet: cloak / brooch detail panel)."""
    r = cfg["r"]
    parts = [lathe([(0.0, 0.0030), (r * 0.82, 0.0034), (r * 0.90, 0.0050), (r, 0.0038), (r * 0.98, 0.0), (0.0, 0.0)],
                   ["brass", "brass_dark", "brass", "brass", "brass_dark"], 20, (0, 0, 0), (0, 0, 1), up_hint=(0, 1, 0))]
    bh = cfg["boss_h"]
    parts.append(lathe([(0.0, 0.0030 + bh), (r * 0.36, 0.0032), (0.0, 0.0)], ["brass", "brass_dark"], 4, (0, 0, 0), (0, 0, 1),
                       up_hint=(0, 1, 0), phase=math.pi / 4))
    for k in range(cfg["ridges"]):
        a = 2 * math.pi * k / cfg["ridges"] + math.pi / cfg["ridges"]
        d = np.array([math.cos(a), math.sin(a), 0.0])
        parts.append(rounded_box(d * r * 0.58 + np.array([0, 0, 0.0036]), d, np.cross([0, 0, 1.0], d), (0, 0, 1),
                                 r * 0.20, 0.0012, 0.0014, nr=1, rows=2, bulge=0.0, region="brass"))
    for k in range(cfg["studs"]):
        a = 2 * math.pi * k / cfg["studs"]
        parts.append(gem(np.array([math.cos(a), math.sin(a), 0.0]) * r * 0.90 + np.array([0, 0, 0.0048]), (0, 0, 1),
                         0.0018, 0.0012, n=4, region="brass_dark"))
    return merge(parts)


def longsword(cfg):
    """The LONGSWORD in its own frame: origin = the guard centre, -Z down the blade to the tip, +Z up the grip to the
    pommel, X = the guard span, Y = through the blade's flat. Hexagonal blade section (bevels 'steel', the central fuller
    flat 'steel_dark'), angular gold cruciform guard with pointed ends + a diamond boss, dark cord-wrapped grip (cross-wrap
    in two tones), gold collars, faceted gold pommel with a dark gem on each broad face (sheet: sword detail panel).
    Returns V, F, R, landmarks."""
    parts = []
    L, (w0, w1), bt = cfg["blade_len"], cfg["blade_w"], cfg["blade_t"]
    zs = np.concatenate([np.linspace(0.0, -cfg["ricasso"], 2), np.linspace(-cfg["ricasso"] - 0.02, -0.86 * L, 8),
                         np.linspace(-0.90 * L, -0.985 * L, 4)])
    rings = []
    for z in zs:
        u = -z / L
        hw = 0.5 * (w0 + (w1 - w0) * min(u / 0.86, 1.0))
        if u > 0.86:
            hw *= max(0.06, 1.0 - (u - 0.86) / 0.14) ** 0.9
        ht = 0.5 * bt * (1.0 if u < 0.9 else max(0.25, 1.0 - (u - 0.9) / 0.1))
        rings.append(np.array([[hw, 0.0, z], [0.32 * hw, ht, z], [-0.32 * hw, ht, z], [-hw, 0.0, z], [-0.32 * hw, -ht, z],
                               [0.32 * hw, -ht, z]]))
    V, F, _ = loft(rings, "flat", "pole", reg="steel", pole1=np.array([0.0, 0.0, -L]))
    R = []
    for fi in range(len(F)):
        if fi < (len(rings) - 1) * 6:
            R.append("steel_dark" if fi % 6 in (1, 4) else "steel")
        else:
            R.append("steel")
    parts.append((V, F, R))
    # the crossguard: an angular bar along X (4-sided section), thick at the centre, pointed ends
    gw, gt = cfg["guard_w"], cfg["guard_t"]
    xs = np.linspace(-0.5 * gw, 0.5 * gw, 13)
    Cg = np.stack([xs, np.zeros_like(xs), 0.004 * (np.abs(xs) / (0.5 * gw)) ** 2], 1)

    def rg(sf):
        return 0.5 * gt * (0.55 + 0.45 * math.cos(math.pi * (sf - 0.5) * 1.6) ** 2) * (1.0 if 0.06 < sf < 0.94 else 0.55)
    Vg, Fg, Rg, _ = tube_path(Cg, rg, 4, "brass", up_hint=(0.0, 0.0, 1.0), cap0="pole", cap1="pole", su=0.75)
    parts.append((Vg, Fg, Rg))
    parts.append(crystal(np.array([0.0, 0.0, 0.004]), (0, 1, 0), gt * 0.95, 0.5 * bt + 0.006, 0.5 * bt + 0.006, n=4,
                         up_hint=(0, 0, 1), region="brass", phase=0.0))
    # the grip (8 sides, cross-wrapped cord) + collars
    gl, (gr0, gr1) = cfg["grip_len"], cfg["grip_r"]
    z0, z1 = 0.5 * gt + 0.004, 0.5 * gt + 0.004 + gl
    Cgr = np.stack([np.zeros(14), np.zeros(14), np.linspace(z0, z1, 14)], 1)
    pit = cfg["wrap_pitch"]

    def grip_reg(k, i, sf):
        th = 2 * math.pi * (i + 0.5) / 8
        z = z0 + sf * gl
        p1 = (z / pit + th / (2 * math.pi)) % 1.0
        p2 = (z / pit - th / (2 * math.pi)) % 1.0
        return "grip_dark" if (p1 < 0.22 or p2 < 0.22) else "grip"

    def rgr(sf):
        return gr0 + (gr1 - gr0) * sf + 0.0012 * math.sin(math.pi * sf)
    Vr, Fr, Rr, _ = tube_path(Cgr, rgr, 8, grip_reg, up_hint=(1.0, 0.0, 0.0), cap0="flat", cap1="flat")
    parts.append((Vr, Fr, Rr))
    for zc, rr in ((z0, gr0 + 0.0035), (z1, gr1 + 0.0035)):
        parts.append(lathe([(0.0, zc - 0.006), (rr, zc - 0.006), (rr * 1.08, zc), (rr, zc + 0.006), (0.0, zc + 0.006)],
                           ["brass", "brass", "brass", "brass"], 8, (0, 0, 0), (0, 0, 1), up_hint=(1, 0, 0), phase=math.pi / 8))
    # the pommel: a faceted hexagonal gold cap, a dark gem on the two broad faces
    pr = cfg["pommel_r"]
    zp = z1 + 0.006
    parts.append(lathe([(0.0, zp), (pr * 0.55, zp), (pr, zp + pr * 0.7), (pr * 0.95, zp + pr * 1.5), (pr * 0.5, zp + pr * 2.0),
                        (0.0, zp + pr * 2.15)], ["brass", "brass", "brass_dark", "brass", "brass"], 6, (0, 0, 0), (0, 0, 1),
                       up_hint=(1, 0, 0), su=1.0, sv=0.62, phase=0.0))
    for sg in (-1.0, 1.0):
        parts.append(gem(np.array([0.0, sg * pr * 0.62 * 0.90, zp + pr * 1.1]), (0, sg, 0), pr * 0.34, 0.0028, n=6,
                         up_hint=(0, 0, 1), region="gem_dark"))
    V, F, R = merge(parts)
    lm = {"guard": np.zeros(3), "tip": np.array([0.0, 0.0, -L]), "grip_centre": np.array([0.0, 0.0, 0.5 * (z0 + z1)]),
          "pommel_top": float(zp + pr * 2.15), "grip_span": (z0, z1)}
    return V, F, R, lm


def scabbard(cfg, sw):
    """The SCABBARD in the sword's frame (sheathed): a flattened 8-sided tube from the throat (just under the guard) past
    the tip, dark leather (alternate facets a darker tone) with a gold throat, gold bands and a pointed gold CHAPE (sheet:
    dark scabbard with gold chape). Returns V, F, R, landmarks."""
    L, (w0, w1), bt = sw["blade_len"], sw["blade_w"], sw["blade_t"]
    pad, t = cfg["pad"], cfg["t"]
    z_top = -0.5 * sw["guard_t"] - 0.002
    z_bot = -L - pad - 0.010
    span = z_top - z_bot
    zs = sorted(set(np.linspace(z_top, z_bot, 18).tolist() + [z_top - cfg["throat"], z_bot + cfg["chape"]] +
                    [z_top - b * span + d for b in cfg["bands"] for d in (-0.006, 0.006)]), reverse=True)

    def hw_at(z):
        u = min(max(-z / L, 0.0), 1.0)
        hw = 0.5 * (w0 + (w1 - w0) * min(u / 0.86, 1.0))
        if u > 0.86:
            hw *= max(0.30, 1.0 - (u - 0.86) / 0.14 * 0.7)
        return hw + pad + t
    ry = 0.5 * bt + pad + t
    rings = []
    for z in zs:
        hx = hw_at(z)
        rings.append(np.array([[hx * math.cos(a), ry * math.sin(a), z] for a in 2 * math.pi * (np.arange(8) + 0.5) / 8]))
    V, F, _ = loft(rings, "flat", "pole", reg="scabbard", pole1=np.array([0.0, 0.0, z_bot - 0.022]))
    R = []
    m = 8
    for fi in range(len(F)):
        if fi < (len(rings) - 1) * m:
            k, i = divmod(fi, m)
            zc = 0.5 * (zs[k] + zs[k + 1])
            d = z_top - zc
            if d < cfg["throat"] or zc < z_bot + cfg["chape"] or any(abs(d - b * span) < 0.006 for b in cfg["bands"]):
                R.append("brass")
            else:
                R.append("scabbard" if i % 2 == 0 else "scabbard_dark")
        else:
            R.append("brass")
    return V, F, R, {"top": z_top, "bottom": z_bot - 0.022}
