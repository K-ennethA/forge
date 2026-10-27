"""Vampire Warrior v5 closed procedural parts (pure numpy, deterministic) for the Pixal3D LIGHT finish: the SWORD that
replaces the proxy's fused board sword (sheet SWORD DETAIL view + the front view's grip / finial / tassel).

The generators below (lathe / loft / lens_ring / sword / tassel and their helpers) are copied unchanged from the v4.2
procedural build's vampwarrior_parts.py (git 6fd5808); sword_part() is new: it scales the v4.2 sword (metres, 1.88 m
unit) into sheet units, adds the front view's flame finial above the grip and places the whole sword point-down in the
right hand (stage-1 frame). Every piece is a CLOSED solid (0 open edges); pieces overlap, never share vertices.
Returns (V (n,3), F list of vertex-index lists, R list of per-face palette region names).
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




# --------------------------------------------------------------------------- v5: the sword placed for the light finish
def sword_part(args):
    """The v4.2 sword (metres) x `scale` (sheet units per metre), the front view's flame finial above the pommel (a short
    copy of the blade, point UP), and the red tassel hanging from the grip below the hand; translated so the guard centre
    sits at `guard_at` (stage-1 frame). Blade flat in the XZ plane (edges along +-X: the sheet front shows its full width).
    args: scale, grip [x, y, z], sword {v4.2 SWORD dict, metres}, finial {blade_len, blade_w, base_w, serr_n, n_st},
    tassel {v4.2 TASSEL dict, metres}, tassel_top (m above the guard), tassel_side (m, +x = toward her),
    guard_at [x, y, z]: where the guard centre (the silver ring below the hand) lands, stage-1 frame."""
    k = float(args["scale"])
    sw = {kk: (tuple(np.asarray(v, float) * k) if isinstance(v, (list, tuple)) else
               (v * k if kk in ("blade_len", "blade_w", "base_w", "thick", "edge_t", "grip_len", "grip_r", "pommel_r")
                else v)) for kk, v in args["sword"].items()}
    V, F, R, lm = sword(sw)
    R = ["sword_ring" if r in ("guard", "pommel") else ("sword_handle" if r == "handle" else
         ("sword_vein" if r == "blade_vein" else "sword_blade")) for r in R]
    parts = [(V, F, R)]
    # finial: a short serrated leaf blade standing point-up on the pommel (front view: z 6.84 -> 7.74 sheet px 235 -> 145)
    fc = dict(sw); fc.update({kk: (v * k if kk in ("blade_len", "blade_w", "base_w", "thick") else v)
                              for kk, v in args["finial"].items()})
    Vf, Ff, Rf, _ = sword(fc)
    blade_faces = [i for i, r in enumerate(Rf) if r in ("blade", "blade_vein")]
    keep_v = sorted({j for i in blade_faces for j in Ff[i]})
    remap = {j: n for n, j in enumerate(keep_v)}
    Vf = Vf[keep_v] * np.array([1.0, 1.0, -1.0])                     # point up
    Vf = Vf + np.array([0.0, 0.0, float(lm["pommel_top"][2]) - 0.25 * float(sw["pommel_r"])])
    Ff = [[remap[j] for j in Ff[i]][::-1] for i in blade_faces]      # mirrored in z: winding reversed
    Rf = ["sword_vein" if Rf[i] == "blade_vein" else "sword_blade" for i in blade_faces]
    parts.append((Vf, Ff, Rf))
    tc = {kk: (v * k if isinstance(v, float) else v) for kk, v in args["tassel"].items()}
    top = np.array([float(args.get("tassel_side", 0.0)) * k, 0.0, float(args["tassel_top"]) * k])
    Vt, Ft, Rt, _ = tassel(top, tc)
    parts.append((Vt, Ft, ["sword_tassel"] * len(Ft)))
    V, F, R = merge(parts)
    V = V - np.asarray(lm["guard"], float) + np.asarray(args["guard_at"], float)
    info = {"total_len_u": round(float(V[:, 2].max() - V[:, 2].min()), 4), "tip_z_u": round(float(V[:, 2].min()), 4),
            "top_z_u": round(float(V[:, 2].max()), 4), "blade_len_u": round(float(sw["blade_len"]), 4),
            "blade_half_w_u": round(float(sw["blade_w"]), 4), "open_edges": open_edges(F)}
    return V, F, R, info


# --------------------------------------------------------------------------- v5.1: the sword traced from SWORD DETAIL
def _traced_blade(st_z, half_l, half_r, x0, y0, tc, u_samples, vein_u, reg_blade, reg_vein, tip_last=True,
                  tip_first=False):
    """A flat blade along z through stations st_z with per-station half widths (left = -x, right = +x) and a lens
    cross-section: front y0 - t(u), back y0 + t(u), t = tc_k (1 - |u|^1.5); the two edge points are shared (thin edges).
    A zero-width end station becomes a single pole (the point). Ring segments within +-vein_u are the vein region.
    Closed: a fan cap on a non-pole end."""
    us = list(u_samples)                                      # -1 .. 1 inclusive, front surface order
    nf = len(us)
    ring_n = 2 * nf - 2
    V, F, R, rings = [], [], [], []
    n = len(st_z)
    for k in range(n):
        a, b = float(half_l[k]), float(half_r[k])
        pole = (k == n - 1 and tip_last) or (k == 0 and tip_first)
        if pole:
            rings.append([len(V)]); V.append((x0 + 0.5 * (b - a), y0, st_z[k])); continue
        t_k = float(tc[k])
        front = [(x0 + (u * a if u < 0 else u * b), y0 - t_k * (1 - abs(u) ** 1.5), st_z[k]) for u in us]
        back = [(x0 + (u * a if u < 0 else u * b), y0 + t_k * (1 - abs(u) ** 1.5), st_z[k]) for u in us[-2:0:-1]]
        idx = list(range(len(V), len(V) + ring_n)); V += front + back; rings.append(idx)
    seg_u = [(us[i], us[i + 1]) for i in range(nf - 1)] + [(us[-1 - i], us[-2 - i]) for i in range(nf - 1)]
    seg_reg = [reg_vein if max(abs(p), abs(q)) <= vein_u + 1e-9 else reg_blade for p, q in seg_u]
    for k in range(n - 1):
        A, B = rings[k], rings[k + 1]
        for i in range(ring_n):
            i1 = (i + 1) % ring_n
            if len(B) == 1:
                F.append([A[i], A[i1], B[0]])
            elif len(A) == 1:
                F.append([A[0], B[i1], B[i]])
            else:
                F.append([A[i], A[i1], B[i1], B[i]])
            R.append(seg_reg[i])
    for end in (0, n - 1):
        if len(rings[end]) > 1:
            c = len(V); V.append(tuple(np.mean([V[j] for j in rings[end]], 0)))
            ring = rings[end]
            for i in range(ring_n):
                F.append([ring[(i + 1) % ring_n], ring[i], c]); R.append(reg_blade)
    V = np.array(V, float)
    return V, orient_outward(V, F), R


def _ribbon(top, drift, length, width, thick, twist_deg, n=6, reg="sword_tassel"):
    """A flat ribbon (closed thin box loft) hanging from `top`: drifts by `drift` (x, y) at its end, narrows to 40 %,
    twists about its own axis by twist_deg over its length."""
    top = np.asarray(top, float)
    rings = []
    for k in range(n + 1):
        t = k / n
        c = top + np.array([drift[0] * t * t, drift[1] * t * t, -length * t])
        a = math.radians(twist_deg * t)
        wv = np.array([math.cos(a), math.sin(a), 0.0]); tv = np.array([-math.sin(a), math.cos(a), 0.0])
        w = 0.5 * width * (1.0 - 0.6 * t ** 1.5); h = 0.5 * thick
        rings.append(np.array([c - wv * w - tv * h, c + wv * w - tv * h, c + wv * w + tv * h, c - wv * w + tv * h]))
    return loft(rings, "flat", "flat", reg=reg)


def sword_part_v51(args):
    """v5.1 sword (artist 2026-10-24 v5 verdict + orchestrator: the v4.2 saw-step blade did not match). The BLADE and
    FINIAL outlines are the sheet's SWORD DETAIL silhouette itself, traced offline from vampwarrior-character-sheet.png
    x 1040..1200 (rows 385..907 = the blade below the binding, rows 62..352 = the leaf above it; half widths about the
    smoothed centre line, every 3 px; args.profile), extruded as a lens section thinning to the edges with a dark centre
    vein. Blade length = the front view's (guard z 5.45 = sheet 370 px -> tip 0.23 = 895 px: 1 px = 0.01 u, the detail
    view's own scale); the finial scaled to the front view's 0.9 u (145..235 px). Silver guard ring, a mid-blade ring
    (front view 560 px), dark wrapped handle with silver rings below the finial and under the hand, red tassel RIBBONS
    (flat strips) from under the hand toward the guard. Point down at args.axis_xy. -> V, F, R, info"""
    pr = args["profile"]
    x0, y0 = [float(v) for v in args["axis_xy"]]
    zg, zt = float(args["guard_z"]), float(args["tip_z"])
    zf0, zf1 = float(args["finial_z"][0]), float(args["finial_z"][1])
    us = args.get("u_samples", [-1.0, -0.6, -0.25, -0.07, 0.0, 0.07, 0.25, 0.6, 1.0])
    vein_u = float(args.get("vein_u", 0.07))
    ws = float(args.get("width_scale", 1.0))
    parts = []
    bl = pr["blade"]; r0, r1, _ = bl["rows_px"]
    nb = len(bl["half_left_px"])
    s = np.linspace(0.0, 1.0, nb)
    zb = zg + (zt - zg) * s
    kpx = (zg - zt) / float(r1 - r0)
    hl = np.asarray(bl["half_left_px"], float) * kpx * ws
    hr = np.asarray(bl["half_right_px"], float) * kpx * ws
    tc0 = float(args["thick"])
    tcb = tc0 * (0.3 + 0.7 * (1.0 - s) ** 0.6)
    parts.append(_traced_blade(zb, hl, hr, x0, y0, tcb, us, vein_u, "sword_blade", "sword_vein", tip_last=True))
    fi = pr["finial"]; f0, f1, _ = fi["rows_px"]
    step = int(args.get("finial_every", 2))
    fl0 = np.asarray(fi["half_left_px"], float); fr0 = np.asarray(fi["half_right_px"], float)
    sel = list(range(0, len(fl0), step))
    if sel[-1] != len(fl0) - 1:
        sel.append(len(fl0) - 1)
    fl, fr = fl0[sel], fr0[sel]
    sf = np.asarray(sel, float) / float(len(fl0) - 1)
    zf = zf1 + (zf0 - zf1) * sf
    kf = (zf1 - zf0) / float(f1 - f0)
    tcf = tc0 * 0.6 * (0.3 + 0.7 * sf ** 0.6)
    parts.append(_traced_blade(zf, fl * kf * ws, fr * kf * ws, x0, y0, tcf, us, vein_u, "sword_blade", "sword_vein",
                               tip_last=False, tip_first=True))
    zr = float(args["ring_z"])
    kr = int(np.argmin(np.abs(zb - zr)))
    a_, b_ = hl[kr], hr[kr]
    hw = 0.5 * (a_ + b_) + 0.025; tr = tcb[kr] + 0.035; hh = 0.035
    Vr, Fr, Rr = lathe([(0.0, hh), (1.0, hh), (1.0, -hh), (0.0, -hh)], ["sword_ring"] * 3, 12,
                       (x0 + 0.5 * (b_ - a_), y0, zr), (0, 0, 1), up_hint=(1, 0, 0), su=hw, sv=tr, phase=math.pi / 12)
    parts.append((Vr, Fr, Rr))
    gx, gy, gz = [float(v) for v in args["guard"]]
    Vg, Fg, Rg = lathe([(0.0, gz), (gx * 0.6, gz), (gx, 0.0), (gx * 0.6, -gz), (0.0, -gz)], ["sword_ring"] * 4, 10,
                       (x0, y0, zg), (0, 0, 1), up_hint=(1, 0, 0), su=1.0, sv=gy / gx, phase=math.pi / 10)
    parts.append((Vg, Fg, Rg))
    hr_ = float(args["grip_r"]); wraps = int(args.get("grip_wraps", 9))
    prof = [(0.0, 0.0)]
    for k in range(wraps * 2 + 1):
        prof.append((hr_ * (1.08 if k % 2 else 0.94), (zf1 - zg) * k / (wraps * 2)))
    prof.append((0.0, zf1 - zg))
    Vh, Fh, Rh = lathe(prof, ["sword_handle"] * (len(prof) - 1), 10, (x0, y0, zg), (0, 0, 1))
    parts.append((Vh, Fh, Rh))
    for zc, rr_ in args.get("handle_rings", []):
        Vq, Fq, Rq = lathe([(0.0, 0.025), (rr_, 0.025), (rr_ * 1.06, 0.0), (rr_, -0.025), (0.0, -0.025)],
                           ["sword_ring"] * 4, 12, (x0, y0, float(zc)), (0, 0, 1))
        parts.append((Vq, Fq, Rq))
    for rb in args.get("ribbons", []):
        top = (x0 + rb["dx"], y0 + rb["dy"], float(args["tassel_top_z"]))
        parts.append(_ribbon(top, rb["drift"], rb["length"], rb["width"], rb.get("thick", 0.012), rb.get("twist", 0.0),
                             reg=rb.get("region", "sword_tassel")))
    V, F, R = merge(parts)
    info = {"total_len_u": round(float(V[:, 2].max() - V[:, 2].min()), 4), "tip_z_u": round(float(V[:, 2].min()), 4),
            "top_z_u": round(float(V[:, 2].max()), 4), "blade_stations": int(nb), "finial_stations": int(len(fl)),
            "blade_max_width_u": round(float((hl + hr).max()), 4), "open_edges": open_edges(F)}
    return V, F, R, info


# --------------------------------------------------------------------------- v5.1b: outline extrusion (true 2D silhouette)
def _seg_dist(P, A, B):
    """distance from each point P (n, 2) to the closed polygon edges A[i] -> B[i] (m, 2)"""
    AB = B - A
    t = np.clip(((P[:, None, :] - A[None]) * AB[None]).sum(2) / np.maximum((AB * AB).sum(1), 1e-12)[None], 0, 1)
    C = A[None] + t[..., None] * AB[None]
    return np.linalg.norm(P[:, None, :] - C, axis=2).min(1)


def _inside(P, poly):
    x, y = P[:, 0], P[:, 1]
    ins = np.zeros(len(P), bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % n]
        c = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1)
        ins ^= c
    return ins


def _outline_blade(poly_px, centre_px, row_top, row_bot, z_top, z_bot, x0, y0, tc, d_full_px, taper_to,
                   interior_px, vein_px, reg_blade="sword_blade", reg_vein="sword_vein", width_scale=1.0, neck=None):
    """A flat blade whose outline IS a drawn silhouette (closed polygon in sheet px, the sheet's x to the right, y down):
    constrained-Delaunay triangulated (Blender mathutils) with interior points on a grid + a vein strip along the
    centre line; front / back surfaces y0 -+ t, t = tc x min(1, d / d_full)^0.6 (d = px distance to the outline) x a
    linear taper from 1 at row_top to taper_to at row_bot; the outline vertices have t = 0 and are SHARED by both
    surfaces (thin edges, closed). Sheet px -> body: x = x0 + (u - centre(v)) k, z = z_top - (v - row_top) k with
    k = (z_top - z_bot) / (row_bot - row_top). Faces within vein_px of the centre line are the vein region."""
    from mathutils import Vector
    from mathutils.geometry import delaunay_2d_cdt
    poly = np.asarray(poly_px, float)
    cen = np.asarray(centre_px, float)

    def cx(v):
        return np.interp(v, cen[:, 0], cen[:, 1])
    nb = len(poly)
    A, B = poly, np.roll(poly, -1, 0)
    lo, hi = poly.min(0), poly.max(0)
    gx, gy = np.meshgrid(np.arange(lo[0], hi[0], interior_px), np.arange(lo[1], hi[1], interior_px))
    G = np.stack([gx.ravel() + 0.5 * interior_px * (np.arange(gx.size) // gx.shape[1] % 2), gy.ravel()], 1)
    vs = np.arange(lo[1] + 2, hi[1] - 1, max(2.0, interior_px * 0.5))
    vein = np.concatenate([np.stack([cx(vs) + o, vs], 1) for o in (-vein_px, 0.0, vein_px)])
    I = np.vstack([G, vein])
    I = I[_inside(I, poly)]
    I = I[_seg_dist(I, A, B) > 1.2]
    pts = np.vstack([poly, I])
    out = delaunay_2d_cdt([Vector((float(p[0]), float(p[1]))) for p in pts], [], [list(range(nb))], 1, 1e-4)
    Vo, Fo, orig = out[0], out[2], out[3]
    P2 = np.array([[v[0], v[1]] for v in Vo], float)
    d = _seg_dist(P2, A, B)
    is_b = d < 1e-3                                       # on the outline (incl. any point the CDT put on an edge)
    d[is_b] = 0.0
    k = (z_top - z_bot) / float(row_bot - row_top)
    kx = k * float(width_scale)
    if neck:                                              # opt-in: the cut end narrowed smoothly (neck [rows, min scale])
        tt = np.clip((P2[:, 1] - row_top) / float(neck[0]), 0, 1)
        kx = kx * (float(neck[1]) + (1.0 - float(neck[1])) * tt * tt * (3 - 2 * tt))
    tap = 1.0 + (taper_to - 1.0) * np.clip((P2[:, 1] - row_top) / float(row_bot - row_top), 0, 1)
    t = tc * np.clip(d / d_full_px, 0, 1) ** 0.6 * tap
    X = x0 + (P2[:, 0] - cx(P2[:, 1])) * kx
    Zc = z_top - (P2[:, 1] - row_top) * k
    nV = len(P2)
    Vf = np.stack([X, y0 - t, Zc], 1)
    Vb = np.stack([X, y0 + t, Zc], 1)
    back_idx = np.where(is_b, np.arange(nV), nV + np.arange(nV))
    V = np.vstack([Vf, Vb])
    F, R = [], []
    for f in Fo:
        f = list(f)
        uc = np.mean([P2[i, 0] - cx(P2[i, 1]) for i in f])
        reg = reg_vein if abs(uc) <= vein_px else reg_blade
        F.append(f); R.append(reg)                              # front (winding fixed below)
        F.append([int(back_idx[i]) for i in f[::-1]]); R.append(reg)
    used = np.unique(np.concatenate([np.asarray(f) for f in F]))
    remap = np.full(len(V), -1); remap[used] = np.arange(len(used))
    V = V[used]; F = [[int(remap[i]) for i in f] for f in F]
    return V, orient_outward(V, F), R


def sword_part_v51b(args):
    """v5.1 sword, outline version: the blade + finial outlines are the sheet SWORD DETAIL polygons themselves (args.
    profile from the lane's trace_sword.py: Moore trace of the view's mask, Douglas-Peucker 0.8 px), so the long spiky
    teeth and their overhangs survive; the rest (rings, handle, ribbons) as sword_part_v51."""
    pr = args["profile"]
    x0, y0 = [float(v) for v in args["axis_xy"]]
    zg, zt = float(args["guard_z"]), float(args["tip_z"])
    zf0, zf1 = float(args["finial_z"][0]), float(args["finial_z"][1])
    tc0 = float(args["thick"])
    bl = pr["blade"]; fi = pr["finial"]
    rt, rb = [float(v) for v in args.get("blade_rows", bl["rows_px"])]
    poly = np.asarray(bl["polygon_px"], float)
    poly[:, 1] = np.clip(poly[:, 1], rt, rb)
    keep = [i for i in range(len(poly)) if i == 0 or np.linalg.norm(poly[i] - poly[i - 1]) > 1e-6]
    poly = poly[keep]
    ws = float(args.get("width_scale", 1.0))
    parts = [_outline_blade(poly, bl["centre_px"], rt, rb, zg, zt, x0, y0, tc0, float(args.get("d_full_px", 9.0)),
                            0.35, float(args.get("interior_px", 7.0)), float(args.get("vein_px", 1.6)), width_scale=ws,
                            neck=args.get("blade_neck"))]
    fr0, fr1 = [float(v) for v in fi["rows_px"]]
    parts.append(_outline_blade(fi["polygon_px"], fi["centre_px"], fr0, fr1, zf1, zf0, x0, y0, tc0 * 0.6,
                                float(args.get("d_full_px", 9.0)), 1.0, float(args.get("interior_px", 7.0)) * 1.3,
                                float(args.get("vein_px", 1.6)) * 1.5, width_scale=float(args.get("finial_width_scale", 1.0))))
    k = (zg - zt) / (rb - rt)
    ring_row = rt + (zg - float(args["ring_z"])) / k
    rows_p = poly[:, 1]
    near = np.abs(rows_p - ring_row) < 6
    cxr = np.interp(ring_row, np.asarray(bl["centre_px"])[:, 0], np.asarray(bl["centre_px"])[:, 1])
    hw = (max(1.0, float(np.abs(poly[near, 0] - cxr).max())) if near.any() else 30.0) * k * ws + 0.02
    tr = tc0 + 0.03; hh = 0.035
    Vr, Fr, Rr = lathe([(0.0, hh), (1.0, hh), (1.0, -hh), (0.0, -hh)], ["sword_ring"] * 3, 12,
                       (x0, y0, float(args["ring_z"])), (0, 0, 1), up_hint=(1, 0, 0), su=hw, sv=tr, phase=math.pi / 12)
    parts.append((Vr, Fr, Rr))
    gx, gy, gz = [float(v) for v in args["guard"]]
    Vg, Fg, Rg = lathe([(0.0, gz), (gx * 0.6, gz), (gx, 0.0), (gx * 0.6, -gz), (0.0, -gz)], ["sword_ring"] * 4, 10,
                       (x0, y0, zg), (0, 0, 1), up_hint=(1, 0, 0), su=1.0, sv=gy / gx, phase=math.pi / 10)
    parts.append((Vg, Fg, Rg))
    hr_ = float(args["grip_r"]); wraps = int(args.get("grip_wraps", 9))
    ov = 0.05                                 # the handle runs 0.05 u INTO the blade root and the finial base, so its
    hz0, hz1 = zg - ov, zf0 + ov              # poles never coincide with their zero-thickness outline points
    prof = [(0.0, 0.0)]
    for kk in range(wraps * 2 + 1):
        prof.append((hr_ * (1.08 if kk % 2 else 0.94), (hz1 - hz0) * kk / (wraps * 2)))
    prof.append((0.0, hz1 - hz0))
    Vh, Fh, Rh = lathe(prof, ["sword_handle"] * (len(prof) - 1), 10, (x0, y0, hz0), (0, 0, 1))
    parts.append((Vh, Fh, Rh))
    for zc, rr_ in args.get("handle_rings", []):
        Vq, Fq, Rq = lathe([(0.0, 0.025), (rr_, 0.025), (rr_ * 1.06, 0.0), (rr_, -0.025), (0.0, -0.025)],
                           ["sword_ring"] * 4, 12, (x0, y0, float(zc)), (0, 0, 1))
        parts.append((Vq, Fq, Rq))
    for rbn in args.get("ribbons", []):
        top = (x0 + rbn["dx"], y0 + rbn["dy"], float(args["tassel_top_z"]))
        parts.append(_ribbon(top, rbn["drift"], rbn["length"], rbn["width"], rbn.get("thick", 0.012),
                             rbn.get("twist", 0.0), reg=rbn.get("region", "sword_tassel")))
    V, F, R = merge(parts)
    info = {"total_len_u": round(float(V[:, 2].max() - V[:, 2].min()), 4), "tip_z_u": round(float(V[:, 2].min()), 4),
            "top_z_u": round(float(V[:, 2].max()), 4), "blade_rows_px": [rt, rb], "px_to_u": round(k, 5),
            "open_edges": open_edges(F)}
    return V, F, R, info


# --------------------------------------------------------------------------- v5.2: flat boot feet
def boot_foot(args):
    """A closed FLAT boot foot (artist 2026-10-25: "can we just make a flat foot like our regular old model"): a lofted
    foot (flat sole at `sole_z`, rounded heel and toe in plan: superellipse power `plan_pow`, instep high at the back,
    low at the toe) + an ankle shaft (lathe tube) that swallows the cut shin end. Centre = the shin end (x, y);
    the toe points -y. Sizes in sheet units (the v4.2 boot proportions x 4.31: foot 0.24 m -> 1.0 u, width 0.085 m ->
    0.36 u). -> V, F, R, info"""
    x, y = [float(v) for v in args["centre_xy"]]
    zs = float(args.get("sole_z", 0.0))
    Lf, Lb = float(args.get("toe_len", 0.62)), float(args.get("heel_len", 0.36))
    W = float(args.get("width", 0.36)); p = float(args.get("plan_pow", 3.0))
    h_back, h_toe = float(args.get("h_back", 0.34)), float(args.get("h_toe", 0.13))
    n_s, n_r = int(args.get("sections", 16)), int(args.get("ring", 14))
    rings = []
    for k in range(n_s + 1):
        s = k / n_s
        yy = y + Lb - (Lb + Lf) * s
        hw = 0.5 * W * max(1.0 - abs(2 * s - 1) ** p, 0.0) ** (1.0 / p)
        tt = np.clip((s - 0.25) / 0.5, 0, 1); ht = h_back + (h_toe - h_back) * (tt * tt * (3 - 2 * tt))
        if k == 0 or k == n_s:
            rings.append(None); continue
        pts = []
        na = n_r - 4
        for j in range(na + 1):                          # top arc, x from +hw to -hw
            a = math.pi * j / na
            ca, sa = math.cos(a), math.sin(a)
            pts.append((x + hw * np.sign(ca) * abs(ca) ** 0.6, yy, zs + ht * abs(sa) ** 0.6))
        for j in range(1, 4):                            # flat sole, -hw -> +hw
            pts.append((x - hw + 2 * hw * j / 4, yy, zs))
        rings.append(np.array(pts))
    mid = [r for r in rings if r is not None]
    c0 = mid[0].mean(0); c1 = mid[-1].mean(0)
    pole0 = np.array([x, y + Lb, zs + 0.45 * h_back]); pole1 = np.array([x, y - Lf, zs + 0.5 * h_toe])
    Vf, Ff, Rf = loft(mid, "pole", "pole", reg="boot", pole0=pole0, pole1=pole1)
    r0, r1, hz = float(args.get("shaft_r0", 0.2)), float(args.get("shaft_r1", 0.17)), float(args.get("shaft_top", 0.62))
    prof = [(0.0, 0.0), (r0, 0.0), (r0 * 1.03, 0.08), (0.5 * (r0 + r1), 0.5 * hz), (r1, hz), (0.0, hz)]
    Vs, Fs, Rs = lathe(prof, ["boot"] * 5, 14, (x, y, zs), (0, 0, 1))
    V, F, R = merge([(Vf, Ff, Rf), (Vs, Fs, Rs)])
    return V, F, R, {"centre_xy": [x, y], "sole_z": zs, "length_u": round(Lf + Lb, 3), "width_u": W,
                     "open_edges": open_edges(F)}


# --------------------------------------------------------------------------- v5.3: low wedge flat feet
def wedge_foot(args):
    """A closed LOW WEDGE flat foot (artist / Codex review 2026-10-25: "low wedge-shaped feet: continuous flat soles,
    modest toe projection and no separate raised heel; taper the ankles into them"): one loft from heel to toe on a
    continuous flat sole (`sole_z`), rounded heel and toe in plan, the top falling from the ankle height `h_ankle` to
    `h_toe` (a wedge, no heel step), plus an oval ankle collar (frustum, front-heavy `collar_fwd`) that tapers from
    the foot top up to `collar_top`, swallowing the cut shin end and the low shin plate. Centre = the shin end (x, y),
    toe -y. -> V, F, R, info"""
    x, y = [float(v) for v in args["centre_xy"]]
    zs = float(args.get("sole_z", 0.0))
    Lf, Lb = float(args.get("toe_len", 0.66)), float(args.get("heel_len", 0.30))
    W = float(args.get("width", 0.36)); p = float(args.get("plan_pow", 2.6))
    ha, ht_ = float(args.get("h_ankle", 0.30)), float(args.get("h_toe", 0.10))
    n_s, n_r = int(args.get("sections", 18)), int(args.get("ring", 16))
    mid = []
    for k in range(1, n_s):
        s = k / n_s
        yy = y + Lb - (Lb + Lf) * s
        hw = 0.5 * W * max(1.0 - abs(2 * s - 1) ** p, 0.0) ** (1.0 / p)
        tt = np.clip((s - 0.3) / 0.65, 0, 1); hh = ha + (ht_ - ha) * tt ** 1.2
        pts = []
        na = n_r - 4
        for j in range(na + 1):
            a = math.pi * j / na
            ca, sa = math.cos(a), math.sin(a)
            pts.append((x + hw * np.sign(ca) * abs(ca) ** 0.7, yy, zs + hh * abs(sa) ** 0.5))
        for j in range(1, 4):
            pts.append((x - hw + 2 * hw * j / 4, yy, zs))
        mid.append(np.array(pts))
    pole0 = np.array([x, y + Lb, zs + 0.4 * ha]); pole1 = np.array([x, y - Lf, zs + 0.45 * ht_])
    Vf, Ff, Rf = loft(mid, "pole", "pole", reg="boot", pole0=pole0, pole1=pole1)
    rb, rt = float(args.get("collar_r0", 0.22)), float(args.get("collar_r1", 0.18))
    zt = float(args.get("collar_top", 0.62)); fwd = float(args.get("collar_fwd", 1.35))
    prof = [(0.0, 0.0), (rb, 0.0), (rb, 0.5 * ha), (0.5 * (rb + rt), 0.5 * (0.5 * ha + zt)), (rt, zt), (0.0, zt)]
    Vc, Fc, Rc = lathe(prof, ["boot"] * 5, 16, (x, y - 0.04, zs), (0, 0, 1), up_hint=(0, -1, 0), su=fwd, sv=1.0)
    V, F, R = merge([(Vf, Ff, Rf), (Vc, Fc, Rc)])
    return V, F, R, {"centre_xy": [x, y], "sole_z": zs, "length_u": round(Lf + Lb, 3), "width_u": W,
                     "h_ankle": ha, "h_toe": ht_, "collar_top": zt, "open_edges": open_edges(F)}
