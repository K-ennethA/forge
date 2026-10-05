"""Shadow Assassin parametric parts: the generic generators COPIED from elias_parts.py (itself a copy of wren_parts.py; other
units' files are read-only) minus the Elias props, plus the Shadow Assassin's own generators at the end: the curved
BLADE (crescent with a lens section, spine barbs, wrapped grip, guard, purple sigil strokes) and the TATTERED SHEET helpers.
Original docstring (elias_parts / wren_parts):
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


# =========================================================================== Shadow Assassin generators
def diamond_stroke(cx, cy, hw, hh):
    """a closed diamond outline as one 2D polyline (x right, y up)."""
    return np.array([[cx, cy + hh], [cx + hw, cy], [cx, cy - hh], [cx - hw, cy], [cx, cy + hh]])


def cloak_sigil_strokes(h):
    """The back-cloak sigil (sheet BACK view) as 2D polylines, total height h, origin = the diamond centre, y up: the
    vertical line from the gold ornament down into the diamond's top, the diamond outline, a small inner diamond, the line
    on down to the small circle, a short tail below it."""
    S = []
    dh, dw = 0.155 * h, 0.13 * h                       # diamond half height / half width (the sheet: ~0.07 / 0.057 m at h 0.46)
    S.append(np.array([[0.0, 0.42 * h], [0.0, dh]]))                    # the line from the ornament
    _d = diamond_stroke(0.0, 0.0, dw, dh)                               # the diamond: four straight sides (a
    S += [_d[i:i + 2] for i in range(4)]                                #   5-point stroke would be catmull-rounded)
    S.append(np.array([[0.0, 0.07 * h], [0.0, -0.07 * h]]))             # the inner mark
    cy = -0.47 * h                                                      # the small circle
    rc = 0.05 * h
    S.append(np.array([[0.0, -dh], [0.0, cy + rc]]))
    ang = np.linspace(math.pi / 2, math.pi / 2 + 2 * math.pi, 17)
    S.append(np.stack([rc * np.cos(ang), cy + rc * np.sin(ang)], 1))
    S.append(np.array([[0.0, cy - rc], [0.0, -0.58 * h]]))
    return S


def blade(cfg):
    """The Shadow Assassin's curved blade in its own frame: origin = the GRIP CENTRE (the fist), +Z = from the fist toward
    the blade (the blade exits the PINKY side: the sheet's reverse hold), the blade lies in the XZ plane, +X = its CONVEX
    (spine) side, the sharp edge on the concave side. Pieces: the wrapped grip + pommel + guard collar (lathe about Z), a
    gold ring at the guard, the crescent blade (lens section: edge -> bevel -> flat -> spine, lofted along the catmull
    centreline through cfg['ctrl'], width tapering to the point, spine barbs near the hilt), the purple sigil strokes
    (a centre vein + small diamonds) raised on both flats. Returns V, F, R, landmarks."""
    parts = []
    g = cfg
    gr = g["grip_r"]
    zp, zg0, zg1 = -g["grip_len"] * 0.5, g["grip_len"] * 0.5, g["grip_len"] * 0.5 + g["guard_h"]
    prof = [(0.0, zp - g["pommel_h"]), (gr * 1.15, zp - g["pommel_h"]), (gr * 1.25, zp - g["pommel_h"] * 0.4), (gr, zp),
            (gr, zg0), (g["guard_r"], zg0 + 0.002), (g["guard_r"], zg1 - 0.002), (g["guard_r"] * 0.62, zg1), (0.0, zg1)]
    regs = ["guard", "guard", "guard", "grip", "guard", "guard", "guard", "guard"]
    parts.append(lathe(prof, regs, 10, (0, 0, 0), (0, 0, 1), up_hint=(1, 0, 0)))
    for zz in (zp + 0.003, zg0 - 0.004):
        parts.append(torus(gr * 1.06, 0.0018, 12, 4, (0, 0, zz), (0, 0, 1), up_hint=(1, 0, 0), region="gold"))
    parts.append(torus(g["guard_r"] * 1.02, 0.0022, 14, 4, (0, 0, 0.5 * (zg0 + zg1)), (0, 0, 1), up_hint=(1, 0, 0), region="gold"))
    # ---- the crescent: centreline in the XZ plane (x = convex side out, z = along the blade from the guard)
    ctrl = np.array([[x, 0.0, zg1 - 0.004 + z] for x, z in g["ctrl"]])
    C = resample(catmull(ctrl, 16), g["stations"])[0]
    Lb = float(np.sum(np.linalg.norm(np.diff(C, axis=0), axis=1)))
    s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))]) / Lb
    T = np.gradient(C, axis=0); T /= np.linalg.norm(T, axis=1)[:, None]
    Nout = np.stack([T[:, 2], np.zeros(len(T)), -T[:, 0]], 1)        # in-plane normal (+X side at the base)
    Nout *= np.sign(Nout[0, 0]) if Nout[0, 0] != 0 else 1.0
    w = np.interp(s, [p[0] for p in g["width"]], [p[1] for p in g["width"]])
    barb = np.zeros(len(s))
    for (s0, s1, hgt) in g["barbs"]:                                  # sawtooth barbs on the spine: slow rise, sharp drop
        m = (s >= s0) & (s <= s1)
        barb[m] += hgt * (s[m] - s0) / (s1 - s0)
    ws = w * g["spine_share"] + barb                                  # centre -> spine
    we = w * (1.0 - g["spine_share"])                                 # centre -> edge
    t_s, t_b = g["thick"]                                             # half thickness at the spine / at the bevel line
    taper_t = np.interp(s, [0.0, 0.75, 1.0], [1.0, 0.8, 0.35])
    Y = np.array([0.0, 1.0, 0.0])
    rings = []
    for k in range(len(C)):
        edge = C[k] - Nout[k] * we[k]
        bev = C[k] - Nout[k] * we[k] * (1.0 - g["bevel"])
        sp = C[k] + Nout[k] * ws[k]
        tb, ts = t_b * taper_t[k], t_s * taper_t[k]
        rings.append(np.array([edge, bev + Y * tb, sp + Y * ts * 0.75 - Nout[k] * 0.0012, sp + Nout[k] * 0.0006 * 0,
                               sp - Y * ts * 0.75 - Nout[k] * 0.0012, bev - Y * tb]))
    # the tip: the last ring collapses to its centre point (pole); the base ring capped flat inside the guard
    Vb, Fb, Rb = loft(rings, "flat", "pole", reg="blade", pole1=C[-1] + T[-1] * 0.004)
    m6 = 6
    nr = len(rings)
    Rb = list(Rb)
    for q in range(len(Fb)):                                          # side quads: edge bevels lighter
        f = Fb[q]
        if len(f) == 4 and max(f) < nr * m6:
            ids = sorted({i % m6 for i in f})
            if ids in ([0, 1], [0, 5]):
                Rb[q] = "blade_edge"
    parts.append((Vb, Fb, Rb))
    # ---- sigils: a centre vein + small diamonds on both flats (projected onto the flat wedge, raised 0.35 mm)
    def flat_pt(k, frac, side):
        bev = C[k] - Nout[k] * we[k] * (1.0 - g["bevel"]); sp = C[k] + Nout[k] * ws[k] - Nout[k] * 0.0012
        tb, ts = t_b * taper_t[k], t_s * taper_t[k] * 0.75
        p = bev + (sp - bev) * frac + Y * side * (tb + (ts - tb) * frac)
        return p
    for side in (1.0, -1.0):
        ks = np.nonzero((s >= g["sigil_span"][0]) & (s <= g["sigil_span"][1]))[0]
        vein = np.array([flat_pt(k, g["sigil_frac"], side) for k in ks])
        nrm = np.array([Y * side] * len(vein))
        parts.append(ribbon(vein + Y * side * 0.00035, nrm, g["sigil_w"], 0.00035, region="blade_sigil"))
        for sd in g["sigil_diamonds"]:
            k = int(np.argmin(np.abs(s - sd)))
            c = flat_pt(k, g["sigil_frac"], side)
            hh, hw = g["diamond"]
            loop = [c + T[k] * hh, c + Nout[k] * hw, c - T[k] * hh, c - Nout[k] * hw, c + T[k] * hh]
            P = np.vstack([loop[i] + (loop[i + 1] - loop[i]) * t for i in range(4) for t in np.linspace(0, 1, 4)[:-1]] + [loop[-1]])
            parts.append(ribbon(P + Y * side * 0.0005, np.array([Y * side] * len(P)), g["sigil_w"] * 0.8, 0.0004, region="blade_sigil"))
    V, F, R = merge(parts)
    lm = {"tip": C[-1], "guard": np.array([0.0, 0.0, zg1]), "pommel": np.array([0.0, 0.0, zp - g["pommel_h"]]), "blade_len": Lb,
          "grip_r": gr, "centreline": C}
    return V, F, R, lm


def tear_hem(n_cols, base, long_amp, tooth_amp, long_share, seed):
    """per-column hem drop (m, >= 0) for a tattered edge: typical teeth tooth_amp x (0.35..1.25), every other column shorter,
    a share of columns torn into LONG points (long_amp x 0.55..1.0); deterministic hash."""
    def h01(i, sd):
        return (math.sin(i * 12.9898 + sd * 78.233) * 43758.5453) % 1.0
    out = np.zeros(n_cols)
    for c in range(n_cols):
        t = tooth_amp * (0.35 + 0.9 * h01(c, seed)) * (1.0 if c % 2 else 0.45)
        if h01(c, seed + 3.0) > 1.0 - long_share:
            t = long_amp * (0.55 + 0.45 * h01(c, seed + 7.0))
        out[c] = base + t
    return out
