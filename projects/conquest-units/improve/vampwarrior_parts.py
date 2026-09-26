"""Vampire Warrior parametric parts (pure numpy, deterministic): the outfit / hair / sword pieces that ride on the MPFB2
body. firefly_parts.py's lathe / torus / open_edges pattern, copied not imported (another unit's file).

Every generator returns (V (n,3) float, F list of vertex-index lists, R list of per-face region names). Every part is a
CLOSED solid (0 open edges): sheets (cape, fauld plates, collar) go through solidify(), which builds the outer skin, the
inner skin and a rim strip along every boundary (hem tears and slits included), so a double-coloured sheet (black outside,
red lining inside) needs no double-sided material.

    lathe          profile (r, h) revolved about an axis (poles on the axis); oval via su / sv; per-point radius mod
    torus          ring tube
    solidify       open sheet (consistently oriented) -> closed solid: outer / inner / rim regions
    loft           closed tube through a list of cross-section rings (pole or flat caps)
    lens_ring      pointed-lens cross-section (a hair lock clump)
    sword          leaf / flame blade (serrated edges, diamond section, dark centre vein strip) + guard + grip + pommel
    gem            faceted cabochon (low segment count lathe = facets)
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


# --------------------------------------------------------------------------- sword
def sword(cfg):
    """Sword in its own frame: +Z = pommel -> tip direction is -Z (the sword stands point-DOWN), origin = the guard centre,
    blade flat in the XZ plane (edges along +-X), thickness along Y. Returns V, F, R plus landmarks.
    cfg: blade_len, blade_w (max half width), blade_w_at (fraction where widest), base_w (half width at the guard),
    thick (centre half thickness), edge_t (edge half thickness), serr_n (teeth per edge), serr_depth (fraction of the
    local half width), vein_w (fraction of the local half width painted as the centre vein), n_st (stations),
    guard (half_x, half_y, half_z), grip_len, grip_r, pommel_r."""
    L = cfg["blade_len"]
    sn = cfg["serr_n"]
    teeth = [k / sn for k in range(1, sn)] + [(k + 0.5) / sn for k in range(sn)]
    s = np.unique(np.round(np.concatenate([np.linspace(0.0, 1.0, cfg["n_st"]), [t - 2e-3 for t in teeth], teeth]), 6))
    s = s[(s >= 0) & (s <= 1)]
    n = len(s)
    ws = cfg["blade_w_at"]
    # leaf profile: base_w at the guard, swell to blade_w at ws, ogee taper to the point
    prof = np.where(s <= ws,
                    cfg["base_w"] + (cfg["blade_w"] - cfg["base_w"]) * np.sin(0.5 * math.pi * s / ws) ** 1.3,
                    cfg["blade_w"] * np.clip((1.0 - s) / (1.0 - ws), 0, 1) ** cfg.get("tip_pow", 0.85))
    # serrations: each tooth ramps out and drops back (the tooth points toward the tip, like a leaf's serration toward its
    # apex); stations sit on every drop so the teeth are crisp; zero near the guard and at the tip
    env = np.clip((s - 0.06) / 0.08, 0, 1) * np.clip((0.96 - s) / 0.08, 0, 1)
    saw = (s * cfg["serr_n"]) % 1.0
    sp = cfg.get("serr_pow", 0.8)
    wl = prof * (1.0 - cfg["serr_depth"] * env * (1.0 - saw ** sp))
    wr = prof * (1.0 - cfg["serr_depth"] * env * (1.0 - ((s * cfg["serr_n"] + 0.5) % 1.0) ** sp))
    z = -L * s
    tc = cfg["thick"] * (1.0 - 0.75 * s ** 1.5)
    te = cfg["edge_t"]
    vw = cfg["vein_w"]
    V, F, R = [], [], []
    rows = []
    for k in range(n):
        if k == n - 1:
            rows.append([len(V)]); V.append((0.0, 0.0, z[k]))
            continue
        a, b = wl[k], wr[k]
        # cross-section ring (8 points): L edge, L vein front, C front, R vein front, R edge, R vein back, C back, L vein back
        ring = [(-a, 0.0, z[k]), (-a * vw, -tc[k] * 0.92, z[k]), (0.0, -tc[k], z[k]), (b * vw, -tc[k] * 0.92, z[k]),
                (b, 0.0, z[k]), (b * vw, tc[k] * 0.92, z[k]), (0.0, tc[k], z[k]), (-a * vw, tc[k] * 0.92, z[k])]
        # thin the edges: the edge points carry a tiny thickness via neighbours (edge bevel = the 1st/3rd segments)
        rows.append(list(range(len(V), len(V) + 8))); V += ring
    seg_reg = ["blade", "blade_vein", "blade_vein", "blade", "blade", "blade_vein", "blade_vein", "blade"]
    for k in range(n - 1):
        A, B = rows[k], rows[k + 1]
        for i in range(8):
            i1 = (i + 1) % 8
            if len(B) == 1:
                F.append([A[i], A[i1], B[0]])
            else:
                F.append([A[i], A[i1], B[i1], B[i]])
            R.append(seg_reg[i])
    # blade base cap (hidden inside the guard)
    F.append(rows[0][::-1]); R.append("blade")
    V = np.array(V, float)
    Fb = orient_outward(V, F)
    parts = [(V, Fb, R)]
    gx, gy, gz = cfg["guard"]
    # guard: a flattened lozenge block (8-sided lathe about Z, oval)
    gp = [(0.0, gz), (gx * 0.55, gz), (gx, 0.0), (gx * 0.55, -gz), (0.0, -gz)]
    Vg, Fg, Rg = lathe(gp, ["guard"] * 4, 8, (0, 0, 0), (0, 0, 1), up_hint=(1, 0, 0), su=1.0, sv=gy / gx, phase=math.pi / 8)
    parts.append((Vg, Fg, Rg))
    gl, gr = cfg["grip_len"], cfg["grip_r"]
    wraps = cfg.get("grip_wraps", 5)
    prof = [(0.0, gz * 0.9)]
    for k in range(wraps * 2 + 1):
        h = gz * 0.9 + gl * k / (wraps * 2)
        prof.append((gr * (1.08 if k % 2 == 1 else 0.94), h))
    prof.append((0.0, gz * 0.9 + gl))
    Vh, Fh, Rh = lathe(prof, ["handle"] * (len(prof) - 1), 10, (0, 0, 0), (0, 0, 1))
    parts.append((Vh, Fh, Rh))
    pr = cfg["pommel_r"]
    pz = gz * 0.9 + gl + pr * 0.7
    pp = [(0.0, pz + pr), (pr * 0.7, pz + pr * 0.7), (pr, pz), (pr * 0.7, pz - pr * 0.7), (0.0, pz - pr)]
    Vp, Fp, Rp = lathe(pp, ["pommel"] * 4, 8, (0, 0, 0), (0, 0, 1), phase=math.pi / 8)
    parts.append((Vp, Fp, Rp))
    V, F, R = merge(parts)
    lm = {"tip": np.array([0.0, 0.0, -L]), "guard": np.zeros(3), "grip_centre": np.array([0.0, 0.0, gz * 0.9 + gl * 0.42]),
          "pommel_top": np.array([0.0, 0.0, pz + pr]), "blade_len": L, "total_len": L + pz + pr}
    return V, F, R, lm


def tassel(top, cfg):
    """A red tassel hanging from 'top' along -Z: a cord (thin tube), a knot bead, and a skirt of lens strands.
    Returns V, F, R and the per-vertex drop s (0 at the top .. 1 at the strand tips) for chain weights."""
    top = np.asarray(top, float)
    cl, cr = cfg["cord_len"], cfg["cord_r"]
    parts, S = [], []
    rings = [top + np.array([0.0, 0.0, -cl * t]) + cr * np.array([[math.cos(a), math.sin(a), 0.0] for a in
                                                                    np.linspace(0, 2 * math.pi, 6, endpoint=False)])
             for t in np.linspace(0, 1, 4)]
    V, F, R = loft(rings, "flat", "flat", reg="tassel_cord")
    parts.append((V, F, R)); S.append(np.clip(-(V[:, 2] - top[2]) / (cl + cfg["len"]), 0, 1))
    kz = top[2] - cl
    kr = cfg["knot_r"]
    V, F, R = lathe([(0.0, kr), (kr * 0.8, kr * 0.5), (kr, 0.0), (kr * 0.8, -kr * 0.6), (0.0, -kr)], ["tassel_cord"] * 4, 8,
                    (top[0], top[1], kz), (0, 0, 1))
    parts.append((V, F, R)); S.append(np.clip(-(V[:, 2] - top[2]) / (cl + cfg["len"]), 0, 1))
    n_str = cfg["strands"]
    for k in range(n_str):
        a = 2 * math.pi * (k + 0.5) / n_str
        d = np.array([math.cos(a), math.sin(a), 0.0])
        p0 = np.array([top[0], top[1], kz - kr * 0.5]) + d * kr * 0.55
        ln = cfg["len"] * (0.85 + 0.15 * math.cos(3.1 * k))
        p1 = p0 + d * cfg["spread"] + np.array([0.0, 0.0, -ln])
        rr = []
        for t in np.linspace(0, 1, 5):
            c = p0 + (p1 - p0) * t
            hw = cfg["strand_w"] * (1.0 - 0.8 * t)
            rr.append(lens_ring(c, p1 - p0, np.cross(d, [0, 0, 1]), hw, hw * 0.45, 4))
        V, F, R = loft(rr, "flat", "pole", reg="tassel")
        parts.append((V, F, R)); S.append(np.clip(-(V[:, 2] - top[2]) / (cl + cfg["len"]), 0, 1))
    V, F, R = merge(parts)
    return V, F, R, np.concatenate(S)


def gem(centre, axis, r, h, n=8, up_hint=(0, 0, 1)):
    """Faceted cabochon: table + crown + girdle + pavilion (low n = facets)."""
    prof = [(0.0, h), (r * 0.55, h), (r, h * 0.35), (r, h * 0.05), (r * 0.5, -h * 0.25), (0.0, -h * 0.35)]
    return lathe(prof, ["gem"] * 5, n, centre, axis, up_hint=up_hint, phase=math.pi / n)
