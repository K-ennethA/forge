"""Elias parametric parts: a copy of wren_parts.py (the reference implementation; wren files are read-only) + Elias's own
generators at the end: the armillary staff, the trident tome. Original wren_parts docstring:
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


def armillary_staff(cfg):
    """Professor Elias's staff in its own frame (+Z up the shaft, origin = the butt on the floor): an 8-facet hand-cut
    wooden shaft (alternate facets a grain tone) with a brass ferrule at the butt and a brass collar + cup at the top, the
    brass ARMILLARY SPHERE on the cup (equator ring, two meridians, the tilted ecliptic ring, a polar axis rod with a
    finial) holding the TEAL ORB at its centre. Returns V, F, R, landmarks."""
    L = cfg["len"]
    n = 8
    r0, r1 = cfg["shaft_r"]
    zt = L - cfg["head_h"]                                   # the shaft ends in the cup here
    f0, f1 = cfg["ferrule"]
    c0, c1 = zt - cfg["collar"], zt
    zs = sorted(set([0.0, 0.004, f0, f0 + 0.005, f1 - 0.005, f1, c0, c0 + 0.006, c1 - 0.006, c1] +
                    list(np.linspace(0.0, zt, cfg["rings"]))))
    zs = np.array([z for z in zs if 0.0 <= z <= zt])

    def rad(z, th):
        r = r0 + (r1 - r0) * z / zt
        if f0 <= z <= f1 or c0 <= z <= c1:
            r += cfg["brass_t"] * (1.4 if (min(abs(z - f0), abs(z - f1)) < 0.006 or min(abs(z - c0), abs(z - c1)) < 0.006) else 1.0)
        else:
            r *= 1.0 + 0.035 * math.sin(z * 31.0 + 1.3 * th) * math.sin(z * 9.0 + 0.4)
        return r

    def reg(z, i):
        if f0 - 1e-9 <= z < f1 - 1e-9 or c0 - 1e-9 <= z < c1 - 1e-9:
            return "brass"
        return "wood_dark" if i % 2 else "wood"
    V, F, R, rings = [], [], [], []
    for z in zs:
        idx = []
        for i in range(n):
            th = 2 * math.pi * i / n + math.pi / n
            idx.append(len(V)); V.append((rad(z, th) * math.cos(th), rad(z, th) * math.sin(th), z))
        rings.append(idx)
    for k in range(len(zs) - 1):
        zm = 0.5 * (zs[k] + zs[k + 1])
        for i in range(n):
            F.append([rings[k][i], rings[k][(i + 1) % n], rings[k + 1][(i + 1) % n], rings[k + 1][i]]); R.append(reg(zm, i))
    ib = len(V); V.append((0.0, 0.0, 0.0))
    for i in range(n):
        F.append([rings[0][(i + 1) % n], rings[0][i], ib]); R.append("brass")
    it = len(V); V.append((0.0, 0.0, zt + 0.002))
    for i in range(n):
        F.append([rings[-1][i], rings[-1][(i + 1) % n], it]); R.append("brass")
    V = np.array(V, float)
    parts = [(V, orient_outward(V, F), R)]
    # the cup on the collar (a brass tulip) and the sphere's centre above it
    Rs = cfg["sphere_r"]
    zc = zt + cfg["cup_h"] + Rs
    cr = r1 + cfg["brass_t"] * 1.4
    parts.append(lathe([(0.0, zt - 0.002), (cr, zt - 0.002), (cr * 1.25, zt + cfg["cup_h"] * 0.35), (cr * 1.9, zt + cfg["cup_h"]),
                        (cr * 1.5, zt + cfg["cup_h"] * 1.05), (0.0, zt + cfg["cup_h"] * 1.05)], ["brass", "brass", "brass",
                       "brass_dark", "brass_dark"], 10, (0, 0, 0), (0, 0, 1), up_hint=(0, 1, 0)))
    c = np.array([0.0, 0.0, zc])
    rr = cfg["ring_r"]
    nm, nn = cfg["ring_seg"]
    parts.append(torus(Rs, rr, nm, nn, c, (0, 0, 1), up_hint=(1, 0, 0), region="brass"))                  # equator
    parts.append(torus(Rs * 0.985, rr, nm, nn, c, (1, 0, 0), up_hint=(0, 0, 1), region="brass"))          # meridian (YZ)
    parts.append(torus(Rs * 0.97, rr, nm, nn, c, (0, 1, 0), up_hint=(0, 0, 1), region="brass_dark"))      # meridian (XZ)
    tl = math.radians(cfg["ecliptic_deg"])
    parts.append(torus(Rs * 1.035, rr * 1.25, nm, nn, c, (0.0, math.sin(tl), math.cos(tl)), up_hint=(1, 0, 0), region="brass"))
    # the polar rod through the sphere (stem from the cup to the finial) + the finial ball
    zb, ztop = zt + cfg["cup_h"], zc + Rs + cfg["finial"]
    parts.append(lathe([(0.0, zb), (rr * 1.1, zb), (rr * 1.1, ztop - 0.006), (0.0, ztop - 0.006)], ["brass"] * 3, 6, (0, 0, 0),
                       (0, 0, 1), up_hint=(0, 1, 0)))
    parts.append(uv_sphere(np.array([0.0, 0.0, ztop]), cfg["finial_r"], 8, 5, region="brass"))
    parts.append(uv_sphere(c, cfg["orb_r"], 14, 8, region="crystal"))
    V, F, R = merge(parts)
    lm = {"butt": np.zeros(3), "grip_centre": np.array([0.0, 0.0, cfg["grip_at"] * L]), "orb": c, "top": float(ztop + cfg["finial_r"]),
          "len": L, "shaft_top": zt}
    return V, F, R, lm


def trident_tome(cfg):
    """The research tome in its own frame (origin = the book's centre; +Z = up the spine, +X = across from the spine
    (x < 0) to the fore-edge, +Y = through the boards; the FRONT board faces -Y): two boards overhanging the page block,
    a rounded spine, the cream page block, the gold trident crest raised on the front board, a strap from the back board
    over the fore-edge to a gold clasp plate on the front. Returns V, F, R, landmarks."""
    w, h, th = cfg["size"]
    ct, ov = cfg["cover_t"], cfg["overhang"]
    parts = []
    for sgn, nm in ((-1.0, "front"), (1.0, "back")):
        yc = sgn * (0.5 * th - 0.5 * ct)
        parts.append(rounded_box(np.array([0.5 * ov, yc, 0.0]), (1, 0, 0), (0, 1, 0), (0, 0, 1), 0.5 * w + 0.5 * ov, 0.5 * ct,
                                 0.5 * h + ov, nr=1, rows=2, bulge=0.0, region="book_cover"))
    # the page block: inset from the boards, slightly concave fore-edge
    pw, ph, pt = w - 0.004, h, th - 2.0 * ct - 0.001
    parts.append(rounded_box(np.array([0.0015, 0.0, 0.0]), (1, 0, 0), (0, 1, 0), (0, 0, 1), 0.5 * pw, 0.5 * pt, 0.5 * ph - 0.001,
                             nr=1, rows=2, bulge=0.0, region="pages"))
    # the rounded spine: a half tube along Z at x = -w/2
    prof = []
    for k in range(9):
        a = math.pi * (0.5 + k / 8.0)
        prof.append((-0.5 * w + 0.002 + cfg["spine_bulge"] * math.cos(a) * 1.0, 0.5 * th * math.sin(a)))
    rings = []
    for z in np.linspace(-0.5 * h - ov, 0.5 * h + ov, 6):
        rings.append(np.array([[x, y, z] for x, y in prof]))
    Vg = np.vstack(rings); Fg = grid_faces(len(prof), len(rings))
    cg = np.array([-0.5 * w + 0.01, 0.0, 0.0])
    Fg = [f if np.dot(np.cross(Vg[f[1]] - Vg[f[0]], Vg[f[2]] - Vg[f[0]]), Vg[f].mean(0) - np.array([cg[0], 0.0, Vg[f].mean(0)[2]])) > 0
          else f[::-1] for f in Fg]
    parts.append(solidify(Vg, Fg, ct * 0.5, ct * 0.5, "book_spine", "book_spine", "book_spine"))
    # spine bands (raised ribs)
    for zb in np.linspace(-0.30 * h, 0.30 * h, cfg.get("spine_bands", 2)):
        parts.append(torus(0.5 * th + 0.001, 0.0022, 8, 3, np.array([-0.5 * w + 0.002, 0.0, zb]), (0, 0, 1), up_hint=(1, 0, 0),
                           su=max(cfg["spine_bulge"] / (0.5 * th + 0.001), 0.3), region="book_spine"))
    # the crest on the front board
    yf = -0.5 * th - 0.0004
    for part in strokes_to_parts(trident_strokes(cfg["crest_h"]), np.array([0.5 * ov + 0.006, yf, 0.01]), np.array([1.0, 0, 0]),
                                 np.array([0, 0, 1.0]), np.array([0, -1.0, 0]), cfg["crest_w"], 0.0009, "brass"):
        parts.append(part)
    # a gold frame line round the front board
    fx, fz = 0.5 * w - 0.010, 0.5 * h - 0.010
    frame = [np.array([[-fx, -fz], [fx, -fz]]), np.array([[fx, -fz], [fx, fz]]), np.array([[fx, fz], [-fx, fz]]),
             np.array([[-fx, fz], [-fx, -fz]])]
    for part in strokes_to_parts(frame, np.array([0.5 * ov, yf, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]),
                                 np.array([0, -1.0, 0]), 0.0018, 0.0006, "brass"):
        parts.append(part)
    # the strap + clasp over the fore-edge
    sx = 0.5 * w + ov + 0.0025
    pts = [np.array([sx - 0.030, 0.5 * th + 0.0015, 0.0]), np.array([sx - 0.004, 0.5 * th + 0.0012, 0.0]),
           np.array([sx + 0.001, 0.25 * th, 0.0]), np.array([sx + 0.0015, 0.0, 0.0]), np.array([sx + 0.001, -0.25 * th, 0.0]),
           np.array([sx - 0.004, -0.5 * th - 0.0012, 0.0]), np.array([sx - 0.026, -0.5 * th - 0.0015, 0.0])]
    Cs = resample(catmull(np.array(pts), 4), 14)[0]
    Ns = []
    for k in range(len(Cs)):
        d = Cs[k] - np.array([sx - 0.03, 0.0, 0.0]); d[2] = 0.0
        Ns.append(unit(d))
    parts.append(ribbon(Cs, np.array(Ns), cfg["strap_w"] * 0.5, 0.0012, region="book_strap"))
    parts.append(rounded_box(np.array([sx - 0.024, -0.5 * th - 0.0034, 0.0]), (1, 0, 0), (0, 1, 0), (0, 0, 1), 0.010, 0.0016,
                             cfg["strap_w"] * 0.62, nr=1, rows=3, bulge=0.05, region="brass"))
    V, F, R = merge(parts)
    lm = {"centre": np.zeros(3), "spine_x": -0.5 * w, "size": (w, h, th)}
    return V, F, R, lm
