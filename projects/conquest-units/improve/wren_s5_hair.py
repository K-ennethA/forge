# Wren build section 5: messy brown hair, v3 = the CLUMP STACK (review-log 2026-09-29 "Wren v3 feedback", the Fire Emblem
# round). A feathered scalp cap painted as the DARK INNER CAP (gaps between clumps read as shadowed hair, never scalp) +
# fewer, bolder pointed lens clumps in three size tiers (HAIR_CLUMPS / CLUMP_TIER), every one radiating from the crown
# whorl along its great circle with an S-curve sway: the FRINGE (to the FRINGE_TIPS zigzag, the narrow lock between the
# eyes kept), side clumps over the ears, the spiky outer silhouette, the back mass to the nape, crown accents, the cowlick,
# the short tied nape tail. Painted tiers (palette regions): undersides (hair_shade), dark roots (hair_root), the angel-ring
# band at one consistent height (hair_ring, cut exactly into the clumps), lighter tips (hair_tip).
t_hair = time.time()
cap_faces = [k for k, r in enumerate(reg) if r == "hair"]
used = sorted(set(i for k in cap_faces for i in CF[k]))
mp_ = {v: k for k, v in enumerate(used)}
Vcap = CV[used]
Fcap = [[mp_[i] for i in CF[k]] for k in cap_faces]
_capfe = smoothstep(0.0, HAIRLINE_FEATHER[0], Vcap[:, 2] - hairline_z(Vcap)[1])
_capt = np.maximum(HAIRLINE_FEATHER[1], HAIR_CAP_T * _capfe)
V_, F_, R_ = VP.solidify(Vcap, Fcap, _capt, -0.0015, "hair_inner", "hair_inner", "hair_inner")
CAP_V, CAP_F = V_, F_
if not HAIR_CAP_INNER:
    # the inner (scalp-facing) shell faces into the closed head (skin + cap outer + rim seal it): provably hidden
    _nf = len(Fcap)
    F_ = F_[:_nf] + F_[2 * _nf:]; R_ = R_[:_nf] + R_[2 * _nf:]
add_part("hair_cap", V_, F_, R_, w="rigid:head")
CAP_FEATHER_INFO = {"rule": "cap outer thickness x smoothstep(0, %.3f, height above the hairline), floor %.4f m" % HAIRLINE_FEATHER,
                    "full_thickness_m": HAIR_CAP_T, "inner_shell": HAIR_CAP_INNER, "region": "hair_inner (the dark inner cap)",
                    "rim_thickness_m_p50": round(float(np.median(_capt[_capfe < 0.05])), 4) if (_capfe < 0.05).any() else None}
BVH_HAIR = comb_bvh({"hair_cap", "cloak", "cowl", "hood", "cord", "pendant", "clasp"})
BVH_CAP = BVHTree.FromPolygons(CAP_V.tolist(), CAP_F)
EZ = float(EYE["L"]["c"][2])


def hdir(psi, el):
    return np.array([math.sin(math.radians(psi)) * math.cos(math.radians(el)), -math.cos(math.radians(psi)) * math.cos(math.radians(el)),
                     math.sin(math.radians(el))])


def on_dir(d, off):
    """point on the hair cap along direction d from the head centre (a ray from outside), + off along d."""
    d = unit(d)
    o = HC + d * 0.4
    hit = BVH_CAP.ray_cast(Vector(o), Vector(-d), 0.4)
    if hit[0] is None:
        hit = BVH_BODY.ray_cast(Vector(o), Vector(-d), 0.4)
    return np.array(hit[0]) + d * off


def on_head(psi, el, off):
    return on_dir(hdir(psi, el), off)


def side_pt(psi, z, off):
    """the head / face surface at azimuth psi (from the front) and height z, reached horizontally from outside, + off."""
    d = np.array([math.sin(math.radians(psi)), -math.cos(math.radians(psi)), 0.0])
    o = np.array([HC[0], HC[1], z]) + d * 0.4
    hit = BVH_HAIR.ray_cast(Vector(o), Vector(-d), 0.4)
    base = np.array(hit[0]) if hit[0] is not None else np.array([HC[0], HC[1], z]) + d * HR[0]
    return base + d * off


def slerp(a, b, t):
    a, b = unit(a), unit(b)
    om = math.acos(float(np.clip(a @ b, -1.0, 1.0)))
    if om < 1e-6:
        return a
    return unit((math.sin((1 - t) * om) * a + math.sin(t * om) * b) / math.sin(om))


def relax_path(C, margin, iters=6, fix=1):
    C = C.copy()
    for _ in range(iters):
        Cn = C.copy()
        Cn[fix:-1] = C[fix:-1] + 0.35 * (0.5 * (C[fix - 1:-2] + C[fix + 1:]) - C[fix:-1])
        C = Cn
        for i in range(fix, len(C)):
            q, n_, _, d = BVH_HAIR.find_nearest(Vector(C[i]))
            if q is None:
                continue
            q = np.array(q); n_ = np.array(n_)
            m_ = float(margin[i]) if np.ndim(margin) else margin
            if float((C[i] - q) @ n_) < m_:
                C[i] = q + n_ * m_
    return C


def ellip_el(P):
    """elevation (deg) about the head centre on the head's own ellipsoid (the angel-ring height field)."""
    P = np.atleast_2d(P)
    q_ = (P - HC) / HR
    return np.degrees(np.arctan2(q_[:, 2], np.hypot(q_[:, 0], q_[:, 1])))


LOCK_INFO = {}
TIER_OF = {}
LOCK_EDGES = {}                                        # v4: every clump's outline (the two lens corners per section + the tip)
_sway_k = [0]


def clump(name, tier, ctl, chain=None, s_leave_k=2, W=None, T=None, root_k=None, sway=True, free_k=None, layer=0.0):
    """a pointed lens-section clump along the Catmull path through ctl, cross-sections at the CLUMP_S arc fractions. On
    the scalp (up to the FREE point, control point free_k, where it leaves the head) it is a thin full-width petal
    (CLUMP_SCALP_T of its thickness, riding 'layer' further off the cap so the kinds stack); past it it thickens, swells
    (CLUMP_BELLY) and tapers to a sharp tip; one S-curve sway sideways (alternating hand) faded in toward the free point.
    Lies flat on the surface under it (the lens's wide axis tangent to it). chain: follow-through chain (None = rides the
    head); the chain takes over from control point s_leave_k on. Paint: undersides hair_shade, top faces hair_root / hair /
    hair_tip by arc fraction; the angel-ring band is cut in after (ANGEL_RING)."""
    W0, T0, rk0 = CLUMP_TIER[tier]
    W = W0 * (1.0 + CLUMP_W_VARY * (2.0 * hash01(len(LOCK_INFO), 3.7) - 1.0)) if W is None else W
    T = T0 if T is None else T
    root_k = rk0 if root_k is None else root_k
    free_k = s_leave_k if free_k is None else free_k
    Cd = VP.catmull(np.asarray(ctl, float), 16)
    _pf = np.asarray(ctl[min(free_k, len(ctl) - 1)], float)

    def arcs(Cd_):
        seg_ = np.linalg.norm(np.diff(Cd_, axis=0), axis=1)
        sd_ = np.concatenate([[0.0], np.cumsum(seg_)]) / max(float(seg_.sum()), 1e-9)
        sf_ = min(max(float(sd_[int(np.argmin(np.linalg.norm(Cd_ - _pf, axis=1)))]), 0.30), 0.85)
        return sd_, sf_

    def thick_k(s_, sf_):
        return CLUMP_SCALP_T + (1.0 - CLUMP_SCALP_T) * smoothstep(sf_ - 0.15, sf_ + 0.05, s_)

    sd, s_free = arcs(Cd)
    Cd = relax_path(Cd, 0.0012 + layer + 0.9 * T * thick_k(sd, s_free))
    sd, s_free = arcs(Cd)
    if sway and CLUMP_SWAY:
        # the S: sideways (in the surface, across the path) offset A sin(2 pi s), faded in toward the free point
        hand = 1.0 if _sway_k[0] % 2 == 0 else -1.0
        _sway_k[0] += 1
        Tgd = np.gradient(Cd, axis=0)
        for i in range(1, len(Cd)):
            q, _, _, _ = BVH_HAIR.find_nearest(Vector(Cd[i]))
            nout = unit(Cd[i] - np.array(q)) if np.linalg.norm(Cd[i] - np.array(q)) > 1e-5 else unit(Cd[i] - HC)
            wdir = unit(np.cross(nout, unit(Tgd[i])))
            Cd[i] = Cd[i] + wdir * hand * CLUMP_SWAY * W * math.sin(2 * math.pi * sd[i]) * \
                float(smoothstep(max(s_free - 0.30, 0.0), s_free, sd[i]))
        Cd = relax_path(Cd, 0.0012 + layer + 0.9 * T * thick_k(sd, s_free), iters=2)
        sd, s_free = arcs(Cd)
    C = np.stack([np.interp(CLUMP_S, sd, Cd[:, k]) for k in range(3)], 1)
    Lt = float(np.linalg.norm(np.diff(C, axis=0), axis=1).sum())
    sarc = np.array(CLUMP_S) * Lt
    Tg = np.gradient(C, axis=0)
    rings = []
    for i, p in enumerate(C):
        sfr = CLUMP_S[i]
        u_ = min(max((sfr - s_free) / (1.0 - s_free), 0.0), 1.0)
        q, n_, _, _ = BVH_HAIR.find_nearest(Vector(p))
        nout = unit(p - np.array(q)) if np.linalg.norm(p - np.array(q)) > 1e-5 else unit(p - HC)
        wdir = np.cross(nout, unit(Tg[i]))
        hw = 0.5 * W * (root_k + (1.0 - root_k) * float(smoothstep(0.0, CLUMP_ROOT_GROW, sfr))) * (1.0 + CLUMP_BELLY * math.sin(math.pi * min(u_ / 0.35, 1.0))) * \
            (1.0 - 0.97 * float(smoothstep(0.0, 1.0, u_)) ** 0.8)
        el_p = float(ellip_el(p)[0])
        ht = T * float(thick_k(sfr, s_free)) * (1.0 - 0.8 * float(smoothstep(0.3, 1.0, u_))) * \
            (HAIR_ROOT_K + (1.0 - HAIR_ROOT_K) * smoothstep(0.0, 0.22, sfr)) * \
            (1.0 - CLUMP_TOP_THIN[2] * float(smoothstep(CLUMP_TOP_THIN[0], CLUMP_TOP_THIN[1], el_p)))
        ring_ = VP.lens_ring(p, Tg[i], wdir, max(hw, 0.0007), max(ht, 0.0005), 6)
        # the WRAP: the section bends with the head across its width (a parabola of the local head radius), so a wide
        # clump hugs the skull instead of standing off it at its edges (a flat 80 mm plate on the ~95 mm head: 9 mm sag)
        xs_ = np.cos(2 * math.pi * np.arange(6) / 6.0)
        rc_ = max(float(np.linalg.norm(p - HC)), 0.06)
        ring_ = ring_ - np.outer((max(hw, 0.0007) * xs_) ** 2 / (2.0 * rc_) * CLUMP_WRAP, unit(nout))
        rings.append(ring_)
    V_, F_, _ = VP.loft(rings, "pole", "pole", reg="hair", pole1=C[-1] + unit(Tg[-1]) * 0.006 * CLUMP_S_TIP_K[tier])
    # v4: the clump's OUTLINE (what it traces onto the layer beneath): the lens corners (+-w) of every section, joined at
    # the tip pole -- one polyline root(left) -> tip -> root(right), with each point's arc fraction
    _tip = C[-1] + unit(Tg[-1]) * 0.006 * CLUMP_S_TIP_K[tier]
    _ol = np.vstack([np.array([r_[0] for r_ in rings]), _tip[None], np.array([r_[3] for r_ in rings])[::-1]])
    _os = np.concatenate([np.array(CLUMP_S), [1.0], np.array(CLUMP_S)[::-1]])
    LOCK_EDGES[name] = (_ol, _os)
    svert = np.concatenate([np.repeat(sarc, 6), [0.0, Lt]])
    R_ = []
    for f in F_:
        q_ = V_[f]
        nn = np.cross(q_[1] - q_[0], q_[2] - q_[0])
        c_ = q_.mean(0)
        qq, _, _, _ = BVH_HAIR.find_nearest(Vector(c_))
        sf_ = float(np.mean(svert[f])) / Lt
        if np.dot(unit(nn), unit(c_ - np.array(qq))) < -0.25:
            R_.append("hair_shade")
        else:
            R_.append("hair_root" if sf_ < HAIR_TIERS[0] else ("hair_tip" if sf_ > HAIR_TIERS[1] else "hair"))
    ctl_s = [float(sarc[int(np.argmin(np.linalg.norm(C - np.asarray(c_), axis=1)))]) for c_ in ctl]
    s_leave = ctl_s[min(s_leave_k, len(ctl_s) - 1)]
    add_part(name, V_, F_, R_, w="lock" if chain else "rigid:head", s=svert, s_leave=s_leave, L=Lt, chain=chain,
             path=C[int(np.argmin(np.abs(sarc - s_leave))):])
    TIER_OF[name] = tier
    LOCK_INFO[name] = {"tier": tier, "width_mm": round(1000 * W, 1), "length": round(Lt, 3), "tip": C[-1].round(4).tolist(),
                       "chain": chain, "min_clear": round(float(min(BVH_HAIR.find_nearest(Vector(p))[3] for p in C[1:])), 4)}


WHORL_D = hdir(*HAIR_WHORL)
# v4 SIDE PART (review-log 2026-09-29 "Wren v4 feedback"): the part runs from the front hairline over the top back to the
# crown whorl (HAIR_PART, then the whorl); hair flows AWAY from it -- the fringe and the sweep side (his right) cross
# over from the part, the short side (his left) falls from it. HAIR_PART = None = the v3 centre flow from the whorl alone.
PART_D = ([unit(hdir(*p_)) for p_ in HAIR_PART] + [WHORL_D]) if HAIR_PART is not None else [WHORL_D]
_part_ang = [0.0] + [math.acos(float(np.clip(PART_D[i] @ PART_D[i + 1], -1, 1))) for i in range(len(PART_D) - 1)]
_part_cum = np.cumsum(_part_ang) / max(float(np.sum(_part_ang)), 1e-9)


def part_dir(t):
    """the part line at parameter t (0 = the front hairline end, 1 = the whorl), by arc angle."""
    if len(PART_D) == 1:
        return PART_D[0]
    t = float(np.clip(t, 0.0, 1.0))
    i = int(np.clip(np.searchsorted(_part_cum, t) - 1, 0, len(PART_D) - 2))
    u = (t - _part_cum[i]) / max(_part_cum[i + 1] - _part_cum[i], 1e-9)
    return slerp(PART_D[i], PART_D[i + 1], u)


def part_nearest(d):
    """the part point a clump toward direction d starts from: the one at the same front-back position (y) as d -- hair
    flows AWAY from the part across the head, square to the part line (which runs front to back); a direction ahead of
    the part's front end / behind the whorl takes that end."""
    ts_ = np.linspace(0.0, 1.0, 121)
    ys_ = np.array([float(part_dir(t)[1]) for t in ts_])
    return part_dir(float(ts_[int(np.argmin(np.abs(ys_ - float(unit(d)[1]))))]))


def radiate(tip_d, kind, n_mid=3, lift=None, f_end=0.86, origin=None):
    """control points from the origin (the whorl, or v4 a point on the part) toward the direction tip_d: the root
    CLUMP_ROOT[kind] of the way along the great circle, n_mid points on to f_end, lifted off the cap by the volume
    profile (0 at the root, CLUMP_LIFT mid-way)."""
    lift = CLUMP_LIFT[kind] if lift is None else lift
    o_ = WHORL_D if origin is None else origin
    f0 = CLUMP_ROOT[kind]
    pts = [on_dir(slerp(o_, tip_d, f0), 0.0)]
    for j in range(1, n_mid + 1):
        f = f0 + (f_end - f0) * j / n_mid
        d_ = slerp(o_, tip_d, f)
        el_ = math.degrees(math.asin(float(np.clip(d_[2], -1.0, 1.0))))
        pts.append(on_dir(d_, LOCK_OFF + lift * (1.0 - float(smoothstep(35.0, 70.0, el_)))))
    return pts


_nb = {"fringe": 0, "side": 0, "outer": 0, "back": 0, "crown": 0, "sweep": 0}
PART_USE = {}
for kind, tier, tip, chain in HAIR_CLUMPS:
    lay_ = CLUMP_LAYER[kind] + CLUMP_STACK * _nb[kind]           # v4: later clumps of a kind lie on top (a consistent stack)
    if kind == "fringe":
        # ---- fringe: to the FRINGE_TIPS zigzag (the same tips the forehead shadow band follows)
        xm, zm = FRINGE_TIPS[tip]
        xt, zt = xm * 0.001, EZ + zm * 0.001
        h = BVH_BODY.ray_cast(Vector((xt, -0.8, zt)), Vector((0.0, 1.0, 0.0)), 1.5)
        yt = (h[0][1] if h[0] is not None else face_front_y) - LOCK_OFF - FRINGE_TIP_OFF
        tip_pt = np.array([xt, yt, zt])
        psi_t = math.degrees(math.asin(float(np.clip(xt / (HR[0] * 1.02), -0.95, 0.95))))
        # the fringe bends DOWN over the forehead: its great circle aims at the forehead top above the tip, then drops.
        # v4: it starts on the part (FRINGE_PART_T per tip: the sweep's locks from further back reach further across)
        org_ = part_dir(FRINGE_PART_T[tip]) if HAIR_PART is not None else None
        aim_ = hdir(psi_t + (FRINGE_SWEEP * (1.0 if xm < FRINGE_SPLIT_X else -1.0) if HAIR_PART is not None else 0.0), 30.0)
        ctl = radiate(aim_, "fringe", n_mid=3, f_end=0.92, origin=org_) + [tip_pt]
        narrow = tip == FRINGE_NARROW                      # the long lock between the eyes
        clump("lock.fringe.%d" % tip, tier, ctl, chain=chain, s_leave_k=3,
              W=(0.032 if narrow else None), layer=lay_)
        PART_USE["lock.fringe.%d" % tip] = None if org_ is None else round(float(FRINGE_PART_T[tip]), 3)
        _nb["fringe"] += 1
        continue
    if kind == "sweep":
        # ---- v4 sweep: the combed-over top on his right (not mirrored): from the part point at the tip's front-back
        # position over the top down toward his right temple / side (tip = (psi, elevation, flick))
        psi_t, elt, flick = tip
        tip_pt = on_head(psi_t, elt, LOCK_OFF + flick)
        org_ = part_nearest(hdir(psi_t, elt)) if HAIR_PART is not None else None
        ctl = radiate(hdir(psi_t, elt), "sweep", n_mid=3, f_end=0.84, origin=org_) + [tip_pt]
        nm_ = "lock.sweep.%d" % _nb["sweep"]
        clump(nm_, tier, ctl, chain=chain, s_leave_k=3, layer=lay_, root_k=PART_ROOT_K)
        PART_USE[nm_] = None if org_ is None else [round(math.degrees(math.atan2(org_[0], -org_[1])), 1),
                                                   round(math.degrees(math.asin(float(np.clip(org_[2], -1, 1)))), 1)]
        _nb["sweep"] += 1
        continue
    if kind in ("side", "outer"):
        for s, sg in (("L", 1.0), ("R", -1.0)):
            ch_ = (chain + "." + s) if chain else None
            if kind == "side":
                psi_t, zm, flick = tip
                tip_pt = side_pt(sg * psi_t, EZ + zm * 0.001, flick)
                org_ = part_nearest(tip_pt - HC) if HAIR_PART is not None else None
                ctl = radiate(unit(tip_pt - HC), "side", n_mid=3, f_end=0.78, origin=org_) + [tip_pt]
                nm_ = "lock.side.%s%d" % (s, _nb["side"] // 2)
                clump(nm_, tier, ctl, chain=ch_, s_leave_k=3, layer=lay_, root_k=(PART_ROOT_K if HAIR_PART is not None else None))
            else:
                psi_t, elt, flick = tip
                w_ = 1.0 + 0.10 * math.cos(3.1 * _nb["outer"] + (0.0 if s == "L" else 1.7))
                tip_pt = on_head(sg * psi_t, elt, LOCK_OFF + flick * w_)
                org_ = part_nearest(hdir(sg * psi_t, elt)) if HAIR_PART is not None else None
                ctl = radiate(hdir(sg * psi_t, elt), "outer", n_mid=3, f_end=0.80, origin=org_) + [tip_pt]
                nm_ = "lock.outer.%s%d" % (s, _nb["outer"] // 2)
                clump(nm_, tier, ctl, chain=ch_, s_leave_k=3, layer=lay_, root_k=(PART_ROOT_K if HAIR_PART is not None else None))
            PART_USE[nm_] = None if org_ is None else [round(math.degrees(math.atan2(org_[0], -org_[1])), 1),
                                                       round(math.degrees(math.asin(float(np.clip(org_[2], -1, 1)))), 1)]
            _nb[kind] += 1
    else:
        psi_t, elt, flick = tip
        tip_pt = on_head(psi_t, elt, LOCK_OFF + flick)
        ctl = radiate(hdir(psi_t, elt), kind, n_mid=(3 if kind == "back" else 2), f_end=0.82) + [tip_pt]
        clump("lock.%s.%d" % (kind, _nb[kind]), tier, ctl, chain=chain, free_k=(3 if kind == "back" else 2), layer=lay_)
        _nb[kind] += 1
# ---- cowlick at the whorl's front (an S accent)
_a0 = on_dir(slerp(WHORL_D, hdir(0.0, 60.0), 0.35), 0.0)
clump("lock.ahoge", "S", [_a0, _a0 + np.array([0.0, -0.010, AHOGE_H * 0.55]), _a0 + np.array([0.004, 0.006, AHOGE_H]),
                          _a0 + np.array([0.006, 0.022, AHOGE_H * 0.82])], W=0.018, T=0.004, root_k=0.9, sway=False)
# ---- the short nape tail + its tie
_t0 = on_head(180.0, -36.0, 0.004 + TAIL[2])
clump("lock.tail", "S", [_t0 + np.array([0.0, -0.012, 0.012]), _t0, _t0 + np.array([0.0, 0.014, -0.025]),
                         _t0 + np.array([0.0, 0.030, -TAIL[0]])], chain="hair_tail", s_leave_k=1, W=0.040, T=0.010,
      root_k=0.9, sway=False)
_tt = unit(np.array([0.0, 0.010, -0.025]))
V_, F_, R_ = VP.torus(TAIL[1], 0.0032, 10, 4, _t0 + _tt * 0.006, _tt, up_hint=(0, 1, 0), region="hair_tie")
add_part("hairtie", V_, F_, R_, w="rigid:head")
# ---- v4 CLEARANCE PASS: every clump vertex that ends up inside the head / neck skin (an ear under a side clump that now
# falls straight from the part, a swept fringe tip at the brow) or sunk under the cap's surface is pushed out along the
# surface normal to HAIR_CLEAR[0] off the skin / HAIR_CLEAR[1] off the cap (the hair-into-head gate's rest baseline)
PUSH_INFO = {"skin_clear_mm": HAIR_CLEAR[0] * 1000, "cap_clear_mm": HAIR_CLEAR[1] * 1000, "verts": 0, "max_push_mm": 0.0}
_ncap = len(Fcap)
BVH_CAP_OUT = BVHTree.FromPolygons(CAP_V.tolist(), CAP_F[:_ncap] + CAP_F[2 * _ncap:])   # (outer shell + rim: the inner
#                                                              shell's normals face the scalp and would push INTO the head)
for p in PARTS:
    if not p["name"].startswith("lock."):
        continue
    V_ = p["V"].copy()
    for i in range(len(V_)):
        for bvh_, clr_, skin_ in ((BVH_BODY, HAIR_CLEAR[0], True), (BVH_CAP_OUT, HAIR_CLEAR[1], False)):
            q_, n_, fi_, d_ = bvh_.find_nearest(Vector(V_[i]), 0.03)
            if q_ is None or (skin_ and fdomn[fi_] not in ("head", "neck_01")):
                continue
            sd_ = float((V_[i] - np.array(q_)) @ np.array(n_))
            if sd_ < clr_:
                V_[i] = V_[i] + np.array(n_) * (clr_ - sd_)
                PUSH_INFO["verts"] += 1
                if 1000 * (clr_ - sd_) > PUSH_INFO["max_push_mm"]:
                    PUSH_INFO["max_push_mm"] = round(1000 * (clr_ - sd_), 2)
                    PUSH_INFO["max_at"] = {"part": p["name"], "s_frac": round(float(p["s"][i] / p["L"]), 2) if i < len(p["s"]) else None,
                                           "surface": "skin:" + str(fdomn[fi_]) if skin_ else "cap",
                                           "xyz_mm": (1000 * (V_[i] - HC)).round(1).tolist()}
    p["V"] = V_
print("HAIRPUSH", json.dumps(PUSH_INFO))
# ---- the ANGEL RING: one consistent height (ANGEL_RING elevations on the head's ellipsoid), cut exactly into every clump
# that crosses it; the band's TOP faces (not the undersides, not the tips) take the highlight tone
RING_INFO = {"elevation_deg": ANGEL_RING, "jitter_deg": ANGEL_RING_JITTER, "clumps_crossed": 0, "edge_splits": 0, "faces": 0}
if ANGEL_RING is not None:
    for k_, p in enumerate(PARTS):
        if not p["name"].startswith("lock.") or p["name"] in ("lock.ahoge", "lock.tail"):
            continue
        # v4: every clump's ring segment sits at its own height (+- ANGEL_RING_JITTER, a fixed per-clump hash): the band
        # reads as the broken per-lock highlight strokes of anime hair, not one stripe round a dome
        jr_ = ANGEL_RING_JITTER * (2.0 * hash01(k_, 5.3) - 1.0)
        ring_ = (ANGEL_RING[0] + jr_, ANGEL_RING[1] + jr_)
        el_ = ellip_el(p["V"])
        if el_.max() < ring_[0] or el_.min() > ring_[1]:
            continue
        V_, F_, R_, s_ = p["V"], p["F"], p["R"], p["s"]
        for tau_ in ring_:
            V_, F_, R_, (s_,), n_ = cut_part(V_, F_, R_, ellip_el, tau_, attrs=(s_,))
            RING_INFO["edge_splits"] += n_
        el_f = np.array([float(ellip_el(V_[f].mean(0)[None])[0]) for f in F_])
        R_ = [("hair_ring" if (r_ in ("hair", "hair_root") and ring_[0] <= e_ <= ring_[1]) else r_)
              for r_, e_ in zip(R_, el_f)]
        p["V"], p["F"], p["R"], p["s"] = np.asarray(V_), [list(map(int, f)) for f in F_], R_, s_
        RING_INFO["clumps_crossed"] += 1
        RING_INFO["faces"] += R_.count("hair_ring")
# ---- v4 LAYER SHADOWS (review-log 2026-09-29 "Wren v4 feedback": "shading between layers of hair so it doesn't look like
# a dome with lines"): the drawn crevice shadow of anime hair. Every clump's OUTLINE (LOCK_EDGES) is traced onto whatever
# hair lies beneath it: a point p of another clump is in the band when its distance to the outline, measured ALONG the
# head (the radial component about the head centre removed), is under the band width w(s) (HAIR_CREVICE[0] at the root
# tapering to [1] at the tip), and the outline is not below p (edge radius >= p's radius - HAIR_CREVICE[2]: the upper
# layer casts, the lower receives; up to HAIR_CREVICE[3] above). The band is cut EXACTLY into the receiving clump
# (cut_part at field = 0) and its top faces take the crevice tone (undersides stay hair_shade).
CREVICE_INFO = {"enabled": HAIR_CREVICE is not None}
if HAIR_CREVICE is not None:
    _occ = [n_ for n_ in LOCK_EDGES if n_ != "lock.ahoge"]
    _SA, _SB, _SS, _SO = [], [], [], []
    for k_, n_ in enumerate(_occ):
        P_, s_ = LOCK_EDGES[n_]
        _SA.append(P_[:-1]); _SB.append(P_[1:]); _SS.append(0.5 * (s_[:-1] + s_[1:])); _SO.append(np.full(len(P_) - 1, k_))
    _SA, _SB, _SS, _SO = np.vstack(_SA), np.vstack(_SB), np.concatenate(_SS), np.concatenate(_SO)
    _AB = _SB - _SA
    _ABl2 = np.maximum((_AB * _AB).sum(1), 1e-18)

    def crevice_field(P_, own_k):
        """< 0 inside the band traced by any OTHER clump's outline lying over P_ (see above)."""
        P_ = np.atleast_2d(P_)
        out_ = np.ones(len(P_))
        pair_ = np.full(len(P_), -1)
        ok_s = _SO != own_k
        A_, AB_, l2_, S_, O_ = _SA[ok_s], _AB[ok_s], _ABl2[ok_s], _SS[ok_s], _SO[ok_s]
        for i0 in range(0, len(P_), 256):
            Pc = P_[i0:i0 + 256]
            t_ = np.clip(np.einsum("pmk,mk->pm", Pc[:, None, :] - A_[None], AB_) / l2_[None], 0.0, 1.0)
            Q_ = A_[None] + t_[..., None] * AB_[None]
            v_ = Pc[:, None, :] - Q_
            rq_ = Q_ - HC
            rql_ = np.linalg.norm(rq_, axis=2)
            rh_ = rq_ / np.maximum(rql_, 1e-9)[..., None]
            vt_ = v_ - np.einsum("pmk,pmk->pm", v_, rh_)[..., None] * rh_
            dt_ = np.linalg.norm(vt_, axis=2)
            dh_ = rql_ - np.linalg.norm(Pc - HC, axis=1)[:, None]            # > 0: the outline stands above p
            w_ = (HAIR_CREVICE[0] + (HAIR_CREVICE[1] - HAIR_CREVICE[0]) * S_)[None] * 1e-3
            ok_ = (dh_ >= -HAIR_CREVICE[2] * 1e-3) & (dh_ <= HAIR_CREVICE[3] * 1e-3)
            # the shadow falls DOWNHILL of the edge (under a light from above: the receiver is not higher than the edge),
            # and only below the crown (receivers under HAIR_CREVICE[4] deg elevation: the lit top keeps the angel ring)
            ok_ &= (Pc[:, None, 2] <= Q_[..., 2] + HAIR_CREVICE[5] * 1e-3)
            ok_ &= (ellip_el(Pc) <= HAIR_CREVICE[4])[:, None]
            f_ = np.where(ok_, dt_ - w_, 1.0)
            j_ = np.argmin(f_, axis=1)
            out_[i0:i0 + 256] = f_[np.arange(len(Pc)), j_]
            pair_[i0:i0 + 256] = np.where(out_[i0:i0 + 256] < 0, O_[j_], -1)
        return out_, pair_

    CREVICE_INFO.update({"band_w_mm_root_tip": HAIR_CREVICE[:2], "edge_below_tol_mm": HAIR_CREVICE[2],
                         "edge_above_max_mm": HAIR_CREVICE[3], "receiver_max_elevation_deg": HAIR_CREVICE[4],
                         "downhill_tol_mm": HAIR_CREVICE[5], "clumps_receiving": 0, "edge_splits": 0, "faces": 0,
                         "area_cm2": 0.0, "top_area_cm2": 0.0, "pairs": 0})
    _pairs = set()
    for p in PARTS:
        if not p["name"].startswith("lock.") or p["name"] == "lock.ahoge":
            continue
        own_ = _occ.index(p["name"])
        f0_, _ = crevice_field(p["V"], own_)
        if not (f0_ < 0).any():
            continue
        V_, F_, R_, (s_,), n_ = cut_part(p["V"], p["F"], p["R"], lambda P_, o_=own_: crevice_field(P_, o_)[0], 0.0,
                                         attrs=(p["s"],), snap=HAIR_CREVICE_SNAP)
        cen_ = np.array([np.asarray(V_)[f].mean(0) for f in F_])
        fc_, pc_ = crevice_field(cen_, own_)
        area_ = np.array([0.5 * np.linalg.norm(np.cross(np.asarray(V_)[f[1]] - np.asarray(V_)[f[0]], np.asarray(V_)[f[2]] - np.asarray(V_)[f[0]]))
                          + (0.5 * np.linalg.norm(np.cross(np.asarray(V_)[f[2]] - np.asarray(V_)[f[0]], np.asarray(V_)[f[3]] - np.asarray(V_)[f[0]])) if len(f) == 4 else 0.0)
                          for f in F_])
        top_ = np.array([r_ in ("hair", "hair_root", "hair_ring", "hair_tip") for r_ in R_])
        hit_ = top_ & (fc_ < 0)
        R_ = [("hair_crevice" if h_ else r_) for r_, h_ in zip(R_, hit_)]
        for k2 in np.unique(pc_[hit_]):
            _pairs.add((_occ[int(k2)], p["name"]))
        p["V"], p["F"], p["R"], p["s"] = np.asarray(V_), [list(map(int, f)) for f in F_], R_, s_
        CREVICE_INFO["clumps_receiving"] += int(hit_.any())
        CREVICE_INFO["edge_splits"] += n_
        CREVICE_INFO["faces"] += int(hit_.sum())
        CREVICE_INFO["area_cm2"] += 1e4 * float(area_[hit_].sum())
        CREVICE_INFO["top_area_cm2"] += 1e4 * float(area_[top_].sum())
    CREVICE_INFO["pairs"] = len(_pairs)
    CREVICE_INFO["area_cm2"] = round(CREVICE_INFO["area_cm2"], 2)
    CREVICE_INFO["share_of_clump_top_area_pct"] = round(100.0 * CREVICE_INFO["area_cm2"] / max(CREVICE_INFO["top_area_cm2"], 1e-9), 1)
    CREVICE_INFO["top_area_cm2"] = round(CREVICE_INFO["top_area_cm2"], 2)
    CREVICE_INFO["casters_by_kind"] = {k: sum(1 for a_, b_ in _pairs if a_.split(".")[1] == k) for k in ("fringe", "sweep", "side", "outer", "back", "crown", "tail")}
print("CREVICE", json.dumps(CREVICE_INFO))


def collapse_slivers(V, F, R, s_, area_min=SLIVER_AREA, passes=3):
    """v4: the clearance pass + the ring / crevice cuts leave sliver faces (3D area ~ 1e-12 m2: zero UV area, the
    uv_health gate); collapse each sliver's shortest edge (s2's body rule), carrying the region + arc length."""
    bm_ = bmesh.new()
    rl_ = bm_.faces.layers.int.new("r"); sl_ = bm_.verts.layers.float.new("s")
    names_ = sorted(set(R))
    vs_ = [bm_.verts.new(p_) for p_ in V]
    for v_, sv_ in zip(vs_, s_):
        v_[sl_] = float(sv_)
    for f_, r_ in zip(F, R):
        try:
            fb_ = bm_.faces.new([vs_[i] for i in f_]); fb_[rl_] = names_.index(r_)
        except ValueError:
            CUT_DUP_FACES[0] += 1
    n0_ = len(bm_.faces)
    for _ in range(passes):
        es_, seen_ = [], set()
        for f_ in bm_.faces:
            if f_.calc_area() < area_min:
                e_ = min(f_.edges, key=lambda e: e.calc_length())
                if e_.is_valid and not (set(e_.verts) & seen_):
                    es_.append(e_); seen_ |= set(e_.verts)
        if not es_:
            break
        bmesh.ops.collapse(bm_, edges=es_, uvs=False)
    bm_.verts.index_update()
    V2 = np.array([v.co[:] for v in bm_.verts]); S2 = np.array([v[sl_] for v in bm_.verts])
    F2 = [[v.index for v in f_.verts] for f_ in bm_.faces]; R2 = [names_[f_[rl_]] for f_ in bm_.faces]
    bm_.free()
    return V2, F2, R2, S2, n0_ - len(F2)


SLIVER_INFO = {"faces_removed": 0, "rule": "faces under SLIVER_AREA collapse their shortest edge (3 passes)"}
for p in PARTS:
    if p["name"].startswith("lock."):
        p["V"], p["F"], p["R"], p["s"], n_ = collapse_slivers(p["V"], p["F"], p["R"], p["s"])
        SLIVER_INFO["faces_removed"] += n_
print("HAIRSLIVERS", json.dumps(SLIVER_INFO))
_clumps = [p for p in PARTS if p["name"].startswith("lock.")]
HAIR_INFO = {"clumps": len(_clumps), "tiers": {t: sum(1 for n in TIER_OF.values() if n == t) for t in "LMS"},
             "by_kind": {k: sum(1 for p in _clumps if p["name"].split(".")[1] == k) for k in ("fringe", "sweep", "side", "outer", "back", "crown", "ahoge", "tail")},
             "whorl": HAIR_WHORL, "sway_x_width": CLUMP_SWAY, "angel_ring": RING_INFO, "cap_feather": CAP_FEATHER_INFO,
             "part": {"line_psi_el_deg": HAIR_PART, "whorl": HAIR_WHORL, "fringe_part_t": FRINGE_PART_T,
                      "fringe_sweep_deg": FRINGE_SWEEP, "clump_origin_on_part": PART_USE, "stack_mm": CLUMP_STACK * 1000},
             "layer_shadows": CREVICE_INFO, "clearance_pass": PUSH_INFO, "slivers": SLIVER_INFO, "cut_repeated_faces_dropped": CUT_DUP_FACES[0],
             "paint_faces": {r_: sum(p["R"].count(r_) for p in _clumps + [q for q in PARTS if q["name"] == "hair_cap"])
                             for r_ in ("hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice")},
             "seconds": round(time.time() - t_hair, 1), "fringe_tips_mm": [list(t) for t in FRINGE_TIPS],
             "crown": (lambda HZ_: {"scalp_top_z": round(Z_TOP, 4), "hair_top_z_excl_cowlick": round(HZ_, 4),
                                    "crown_above_scalp_mm": round(1000 * (HZ_ - Z_TOP), 1),
                                    "cowlick_top_above_scalp_mm": round(1000 * (max(float(p["V"][:, 2].max()) for p in PARTS
                                                                                     if p["name"] == "lock.ahoge") - Z_TOP), 1),
                                    "top_part": max(((float(p["V"][:, 2].max()), p["name"]) for p in PARTS if p["name"].startswith(("lock", "hair_cap"))
                                                     and p["name"] != "lock.ahoge"))[1],
                                    "rule": "highest hair vertex (cap + every clump but the cowlick) minus the MPFB scalp top; "
                                            "vampwarrior v4.2: 8.5 mm (v4 23.3 mm = 'too tall'); v2 14.4 mm"})(
                 max(float(p["V"][:, 2].max()) for p in PARTS if p["name"].startswith(("lock", "hair_cap")) and p["name"] != "lock.ahoge"))}
_tops = sorted(((float(p["V"][:, 2].max()), p["name"], round(float(p["s"][int(np.argmax(p["V"][:, 2]))] / p["L"]), 2) if "s" in p else None)
                for p in PARTS if p["name"].startswith("lock") and p["name"] != "lock.ahoge"), reverse=True)[:5]
HAIR_INFO["crown"]["top5_mm_part_sfrac"] = [[round(1000 * (z_ - Z_TOP), 1), n_, s_] for z_, n_, s_ in _tops]
print("HAIR", json.dumps(HAIR_INFO))
