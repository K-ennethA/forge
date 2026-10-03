# Wren build section 5: messy brown hair, v3 = the CLUMP STACK (review-log 2026-09-29 "Wren v3 feedback", the Fire Emblem
# round). A feathered scalp cap painted as the DARK INNER CAP (gaps between clumps read as shadowed hair, never scalp) +
# fewer, bolder pointed lens clumps in three size tiers (HAIR_CLUMPS / CLUMP_TIER), every one radiating from the crown
# whorl along its great circle with an S-curve sway: the FRINGE (to the FRINGE_TIPS zigzag, the narrow lock between the
# eyes kept), side clumps over the ears, the spiky outer silhouette, the back mass to the nape, crown accents, the cowlick,
# the short tied nape tail. Painted tiers (palette regions): undersides (hair_shade), dark roots (hair_root), the angel-ring
# band at one consistent height (hair_ring, cut exactly into the clumps), lighter tips (hair_tip).
# v7 (review-log 2026-09-29 "Wren v7 hair feedback"): RIBBON = True builds every lock as a smooth ribbon (ribbon_plan /
# build_lock), resolves the layer order of the top sheets (LAYER RESOLVE), paints by whole segments (ring strokes at spine
# stations, the TUCK SHADE instead of the v4 cut bands); RIBBON = False keeps the v6 lens clumps (clump_lens) for A/B.
# ---------------------------------------------------------------------------------------------------------------------
# ELIAS: this section is wren_s5_hair.py's ribbon-lock stack spliced verbatim (the machinery above the ELIAS lock table:
# the cap, nearest() tie-invariance, ribbon_plan / build_lock, and below it the LAYER RESOLVE, clearance pass, tuck
# shade, sliver collapse, digest) with Elias's own lock table in the middle: the tousled grey scalp hair swept back from
# a receding hairline (HAIR_LOCKS), and the BEARD + MUSTACHE as groomed ribbon-lock masses rooted on the face skin over
# the painted under-beard zone (BEARD_LOCKS / MUSTACHE). Comments mentioning fringe / tail / cowlick / cloak are Wren's.
# ---------------------------------------------------------------------------------------------------------------------
RIBBON = True                         # (the v6 lens-clump path is kept below for reference only; never called)
RIBBON_KIND_W = HAIR_KIND_W
CLUMP_LAYER = HAIR_LAYER
HAIR_CREVICE = (5.0, 2.0, 0.5, 14.0, HAIR_CREVICE_MAX_EL, 0.5)   # (Wren's v4 crevice tuple: only [4] -- the tuck-shade
#                                                                    elevation limit -- is live with ribbons)
ANGEL_RING_JITTER = RIBBON_RING_JITTER
TAIL = (0.0, 0.0, 0.0)                                            # (no nape tail on Elias)
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
# v5.1 HAIR-FACE DECOUPLING (2): the hair's surroundings take the UNCUT body (BV / BF: the MPFB surface itself), never the
# painted cut mesh CV / CF. The paint cuts (brows, liner, mouth line, shadow shapes) re-tessellate the same surface, so a
# brow or smirk edit moved the nearest sub-triangle, its normal and location by float32 ULPs (measured: 1.2e-7 m at the
# nape for a smirk edit), and the clump stack's discrete steps (the cut snaps, the sliver collapse) amplified that into
# whole-vertex jumps. The uncut body is the same surface, tessellated by nothing but the MPFB base.
_hv, _hf, _ho = [BV], [list(f) for f in BF], len(BV)
for p in PARTS:
    if p["name"].split(".")[0] in {"hair_cap", "mantle", "capelet", "cravatband", "cravatknot", "cravatfall", "brooch",
                                   "broochgem", "broochcrest", "satchelstrap"}:
        _hv.append(p["V"]); _hf += [[i + _ho for i in f] for f in p["F"]]; _ho += len(p["V"])
BVH_HAIR = BVHTree.FromPolygons(np.vstack(_hv).tolist(), _hf)
BVH_CAP = BVHTree.FromPolygons(CAP_V.tolist(), CAP_F)
EZ = float(EYE["L"]["c"][2])


def nearest(bvh, p, dmax=None):
    """v5.1 HAIR-FACE DECOUPLING: the tie-invariant BVHTree.find_nearest. When the query's nearest surface point lies on
    an edge / vertex shared by several faces, their distances tie exactly and find_nearest returns whichever face the
    tree's traversal meets first -- an order set by EVERY polygon in the tree. BVH_HAIR / BVH_BODY hold the whole body, so
    a face edit (the v4 <-> v5 mouth profile, 0 hair inputs changed) re-balanced the tree, flipped which neighbour's
    normal relax_path pushed a clump along, and the divergence grew through the clump stack (measured: same query, same
    point, same distance, a different face + normal; one geometry-free triangle added 10 m away did the same to 30 of 33
    clumps). Here the tie set is gathered exhaustively (find_nearest_range to the nearest distance + NEAREST_TIE) and
    resolved canonically: location / face of the smallest (distance, normal, location, index) and, on a tie, the normal =
    the normalised sum of the tied faces' normals in that order (the edge / vertex pseudo-normal). The result is a function
    of the faces AT the nearest point only. Returns (location, normal, index, distance) like find_nearest."""
    p = Vector(p)
    h = bvh.find_nearest(p) if dmax is None else bvh.find_nearest(p, dmax)
    if h[0] is None:
        return h
    ts_ = sorted(((float(d_), tuple(n_), tuple(q_), int(i_)) for q_, n_, i_, d_ in bvh.find_nearest_range(p, h[3] + NEAREST_TIE)))
    if not ts_:
        return h
    d0_ = ts_[0][0]
    ts_ = [t_ for t_ in ts_ if t_[0] <= d0_ + NEAREST_TIE]
    d_, n_, q_, i_ = ts_[0]
    if len(ts_) > 1:
        n_ = tuple(unit(np.sum([t_[1] for t_ in ts_], axis=0)))
    return Vector(q_), Vector(n_), i_, d_


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
            q, n_, _, d = nearest(BVH_HAIR, C[i])
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
LOCK_SPINE = {}                                        # v7: every clump's section centres + the tip (the ribbon metrics)
_sway_k = [0]


def turn_deg(P):
    """v7 ribbon metric: the turning angle (deg) at every interior point of a polyline."""
    P = np.asarray(P, float)
    a_, b_ = P[1:-1] - P[:-2], P[2:] - P[1:-1]
    la_, lb_ = np.linalg.norm(a_, axis=1), np.linalg.norm(b_, axis=1)
    ok_ = (la_ > 1e-9) & (lb_ > 1e-9)
    c_ = np.einsum("ij,ij->i", a_[ok_], b_[ok_]) / (la_[ok_] * lb_[ok_])
    return np.degrees(np.arccos(np.clip(c_, -1.0, 1.0)))


def ribbon_metrics():
    """v7: smoothness of every clump as built (before paint): spine = the section centres to the tip; edges = the two
    lens-corner polylines (the silhouette edges); per point turning angle (deg), the discrete curvature jump between
    neighbouring points (turn / mean segment length, 1/m), the tip's included angle (deg: the two edges' last segments)
    and the last section's width (mm)."""
    sp_t, ed_t, kj_, tipa_, tipw_, seg_, nst_ = [], [], [], [], [], [], []
    worst_ = {"spine": (0.0, None, None), "edge": (0.0, None, None), "segment": (0.0, None, None)}
    for n_, S_ in LOCK_SPINE.items():
        if n_ == "lock.ahoge":
            continue
        t_ = turn_deg(S_)
        if len(t_) and float(t_.max()) > worst_["spine"][0]:
            worst_["spine"] = (round(float(t_.max()), 1), n_, int(np.argmax(t_)) + 1)
        sp_t += list(t_)
        sl_ = np.linalg.norm(np.diff(S_, axis=0), axis=1)
        seg_ += list(1000 * sl_); nst_.append(len(S_) - 1)
        if len(t_) > 1:
            k_ = np.radians(t_) / np.maximum(0.5 * (sl_[:-1] + sl_[1:])[:len(t_)], 1e-9)
            kj_.append(float(np.abs(np.diff(k_)).max()))
        P_, s_ = LOCK_EDGES[n_]
        m_ = len(P_) // 2
        eL, eR = P_[:m_ + 1], P_[m_:][::-1]
        ed_t += list(turn_deg(eL)) + list(turn_deg(eR))
        for e_ in (eL, eR):
            te_ = turn_deg(e_)
            if len(te_) and float(te_.max()) > worst_["edge"][0]:
                worst_["edge"] = (round(float(te_.max()), 1), n_, int(np.argmax(te_)) + 1)
        if 1000 * float(sl_.max()) > worst_["segment"][0]:
            worst_["segment"] = (round(1000 * float(sl_.max()), 1), n_, int(np.argmax(sl_)))
        a_, b_ = unit(eL[-2] - eL[-1]), unit(eR[-2] - eR[-1])
        tipa_.append(math.degrees(math.acos(float(np.clip(a_ @ b_, -1, 1)))))
        tipw_.append(1000 * float(np.linalg.norm(eL[-2] - eR[-2])))
    q_ = lambda a_, p_: round(float(np.percentile(a_, p_)), 2) if len(a_) else None
    return {"locks": len(nst_), "stations_per_lock_min_mean_max": [int(min(nst_)), round(float(np.mean(nst_)), 1), int(max(nst_))],
            "segment_mm_p50_max": [q_(seg_, 50), q_(seg_, 100)],
            "spine_turn_deg_p50_p90_max": [q_(sp_t, 50), q_(sp_t, 90), q_(sp_t, 100)],
            "spine_curvature_jump_per_m_p50_max": [q_(kj_, 50), q_(kj_, 100)],
            "edge_turn_deg_p50_p90_max": [q_(ed_t, 50), q_(ed_t, 90), q_(ed_t, 100)],
            "edge_turns_over_20deg": int(sum(1 for x_ in ed_t if x_ > 20.0)), "edge_points": len(ed_t),
            "tip_angle_deg_p50_max": [q_(tipa_, 50), q_(tipa_, 100)], "tip_last_width_mm_p50_max": [q_(tipw_, 50), q_(tipw_, 100)],
            "worst_value_lock_point": worst_}


def clump(name, tier, ctl, chain=None, s_leave_k=2, W=None, T=None, root_k=None, sway=True, free_k=None, layer=0.0):
    """v7: the ribbon lock (RIBBON) or the v6 lens clump."""
    if RIBBON:
        # (the nape tail's first stretch is tucked into the nape by design -- its tie: no clearance push there, which
        #   folded its spine 145 deg on the first ribbon build; the per-vertex pass below lifts just those vertices)
        return ribbon_plan(name, tier, ctl, chain, s_leave_k, W, T, root_k, sway, free_k, layer,
                           root_ramp=(RIBBON_TAIL_TUCK if name == "lock.tail" else None))
    return clump_lens(name, tier, ctl, chain, s_leave_k, W, T, root_k, sway, free_k, layer)


# =========================================================================== v7 RIBBON LOCKS
PLAN = {}                                              # per lock: the faired spine, stations, frames, profiles, lift
BEND_LOG = []                                          # per lock: the bend-limit fairing passes used
RING_BANDS = {}                                        # per lock: the angel-ring band (arc fractions) or None
_RX = np.array([-1.0, -RIBBON_TOP[0], 0.0, RIBBON_TOP[0], 1.0, 0.0])     # the section: corner, shoulder, ridge,
_RY = np.array([0.0, RIBBON_TOP[1], 1.0, RIBBON_TOP[1], 0.0, -RIBBON_UNDER])   # shoulder, corner, underside
_TOP_EDGES = (0, 1, 2, 3)                              # section edges i -> i+1 on the top (4, 5: the underside)


def gauss1(A, sig):
    """Gaussian smoothing along axis 0 (edge-padded); sig in samples."""
    A = np.asarray(A, float)
    if sig <= 0 or len(A) < 3:
        return A.copy()
    r_ = max(1, int(math.ceil(3 * sig)))
    k_ = np.exp(-0.5 * (np.arange(-r_, r_ + 1) / sig) ** 2); k_ /= k_.sum()
    pad_ = np.concatenate([np.repeat(A[:1], r_, 0), A, np.repeat(A[-1:], r_, 0)], 0)
    if A.ndim == 1:
        return np.convolve(pad_, k_, mode="valid")
    return np.stack([np.convolve(pad_[:, j], k_, mode="valid") for j in range(A.shape[1])], 1)


def maxfilt(a, r):
    a = np.asarray(a, float)
    return np.array([a[max(0, i - r):i + r + 1].max() for i in range(len(a))])


def taubin(C, it, lam, mu):
    """Taubin fairing of a polyline, both ends pinned (the root on the scalp, the authored tip)."""
    C = np.asarray(C, float).copy()
    for _ in range(it):
        for f_ in (lam, mu):
            C[1:-1] = C[1:-1] + f_ * (0.5 * (C[:-2] + C[2:]) - C[1:-1])
    return C


def arc_frac(C):
    seg_ = np.linalg.norm(np.diff(C, axis=0), axis=1)
    a_ = np.concatenate([[0.0], np.cumsum(seg_)])
    return a_ / max(float(a_[-1]), 1e-12), float(a_[-1])


def clear_spine(C, margin, iters=10, root_ramp=None):
    """the spine kept margin (per point) off the head / cap / cloak surface (BVH_HAIR, tie-invariant nearest()): the
    shortfall is dilated and Gaussian-smoothed (RIBBON_PUSH_SMOOTH points), applied along the smoothed surface normal,
    and each push is followed by a light Taubin fairing -- a smooth push, never the per-point projection that kinked
    the v6 paths. The root point stays (on the scalp); the margin ramps in from the root over RIBBON_ROOT_RAMP (the root
    petal rides into the cap instead of folding up off it)."""
    C = np.asarray(C, float).copy()
    af_, _ = arc_frac(C)
    margin = np.asarray(margin, float) * smoothstep(0.0, RIBBON_ROOT_RAMP if root_ramp is None else root_ramp, af_)
    worst_ = 0.0
    for it_ in range(iters):
        push_ = np.zeros(len(C)); dirs_ = np.zeros((len(C), 3))
        for i in range(1, len(C)):
            q_, n_, _, _ = nearest(BVH_HAIR, C[i])
            if q_ is None:
                continue
            n_ = np.array(n_)
            push_[i] = max(0.0, float(margin[i]) - float((C[i] - np.array(q_)) @ n_))
            dirs_[i] = n_
        worst_ = float(push_.max())
        if worst_ < 2e-5:
            break
        dirs_ = gauss1(dirs_, RIBBON_PUSH_SMOOTH)
        dirs_ = dirs_ / np.maximum(np.linalg.norm(dirs_, axis=1), 1e-12)[:, None]
        e_ = gauss1(maxfilt(push_, int(round(RIBBON_PUSH_SMOOTH))), RIBBON_PUSH_SMOOTH)
        d_ = e_[:, None] * dirs_
        d_[0] = 0.0
        C = C + d_
        if it_ < iters - 1:
            C = taubin(C, 2, RIBBON_FAIR[1], RIBBON_FAIR[2])
    return C, worst_


def spine_frames(Cd):
    """rotation-smooth section frames along the dense spine: T = the tangent; N = the outward normal -- a rotation-
    minimising frame (double reflection) twisted about T toward the target (away from the nearest head / cap surface,
    leaned RIBBON_RADIAL toward the head-centre radial) by a twist angle that is unwrapped and Gaussian-smoothed along
    the spine (RIBBON_FRAME_SMOOTH): the section can never flip or zigzag where the nearest surface jumps (cap -> ear ->
    neck -> cloak; measured on the first ribbon build: the nape tail's sections swung 11 mm sideways); Wd = N x T."""
    T_ = np.gradient(Cd, axis=0)
    T_ = T_ / np.maximum(np.linalg.norm(T_, axis=1), 1e-12)[:, None]
    G_ = np.zeros_like(Cd)
    for i, p_ in enumerate(Cd):
        q_, _, _, d_ = nearest(BVH_HAIR, p_)
        rad_ = unit(p_ - HC)
        n_ = unit(p_ - np.array(q_)) if (q_ is not None and d_ > 1e-5) else rad_
        G_[i] = unit((1.0 - RIBBON_RADIAL) * n_ + RIBBON_RADIAL * rad_)
    U_ = np.zeros_like(Cd)
    u0_ = G_[0] - T_[0] * float(G_[0] @ T_[0])
    U_[0] = unit(u0_ if np.linalg.norm(u0_) > 1e-9 else np.cross(T_[0], [0.0, 0.0, 1.0]))
    for i in range(len(Cd) - 1):
        v1_ = Cd[i + 1] - Cd[i]
        c1_ = float(v1_ @ v1_)
        if c1_ < 1e-18:
            U_[i + 1] = U_[i]; continue
        rL_ = U_[i] - (2.0 / c1_) * float(v1_ @ U_[i]) * v1_
        tL_ = T_[i] - (2.0 / c1_) * float(v1_ @ T_[i]) * v1_
        v2_ = T_[i + 1] - tL_
        c2_ = float(v2_ @ v2_)
        U_[i + 1] = unit(rL_ - (2.0 / c2_) * float(v2_ @ rL_) * v2_) if c2_ > 1e-18 else unit(rL_)
    th_ = np.array([math.atan2(float(np.cross(U_[i], G_[i]) @ T_[i]), float(U_[i] @ G_[i])) for i in range(len(Cd))])
    th_ = gauss1(np.unwrap(th_), RIBBON_FRAME_SMOOTH)
    B_ = np.cross(T_, U_)
    N_ = U_ * np.cos(th_)[:, None] + B_ * np.sin(th_)[:, None]
    N_ = N_ / np.maximum(np.linalg.norm(N_, axis=1), 1e-12)[:, None]
    W_ = np.cross(N_, T_)
    return T_, N_, W_ / np.maximum(np.linalg.norm(W_, axis=1), 1e-12)[:, None]


def station_fracs(Cd, n, forced):
    """n station arc fractions (0 .. 1): even in a blend of arc length and turning (RIBBON_CURV_K), crowded toward the
    tip (RIBBON_TIP_DENSE); the forced fractions (paint boundaries) replace their nearest station or are inserted."""
    af_, _ = arc_frac(Cd)
    tr_ = np.concatenate([[0.0], np.radians(turn_deg(Cd)), [0.0]])
    ct_ = np.cumsum(tr_)
    M_ = af_ if ct_[-1] < 0.2 else (1.0 - RIBBON_CURV_K) * af_ + RIBBON_CURV_K * ct_ / ct_[-1]
    t_ = np.linspace(0.0, 1.0, n)
    fr_ = np.interp(1.0 - (1.0 - t_) ** RIBBON_TIP_DENSE, M_, af_)
    fr_[0], fr_[-1] = 0.0, 1.0
    fr_ = list(fr_)
    for f_ in forced:
        if not (0.02 < f_ < 0.98):
            continue
        j_ = int(np.argmin(np.abs(np.array(fr_) - f_)))
        sp_ = 0.5 * min(abs(fr_[min(j_ + 1, len(fr_) - 1)] - fr_[j_]) or 1.0, abs(fr_[j_] - fr_[max(j_ - 1, 0)]) or 1.0)
        if 0 < j_ < len(fr_) - 1 and abs(fr_[j_] - f_) < 0.5 * sp_ and fr_[j_] not in forced:
            fr_[j_] = f_
        else:
            fr_.append(f_)
    fr_ = np.array(sorted(set(round(float(x_), 9) for x_ in fr_)))
    keep_ = np.concatenate([[True], np.diff(fr_) > 0.004])       # (no near-duplicate stations)
    return fr_[keep_]


def interp_rows(fr, af, A):
    return np.stack([np.interp(fr, af, A[:, k]) for k in range(A.shape[1])], 1)


def ribbon_plan(name, tier, ctl, chain, s_leave_k, W, T, root_k, sway, free_k, layer, root_ramp=None):
    """v7: plan one ribbon lock (spine, stations, frames, width / thickness profiles) and build it. On the scalp (up to
    the FREE point, control point free_k) a thin full-width petal (CLUMP_SCALP_T of its thickness, 'layer' further off the
    cap); past it full thickness, a slight swell (RIBBON_BELLY) and a clean taper to a sharp tip; one S-curve sway
    sideways (alternating hand) faded in toward the free point. chain / s_leave_k: as the v6 clump."""
    W0, T0, rk0 = RIBBON_TIER[tier]
    W = W0 * RIBBON_KIND_W.get(name.split(".")[1], 1.0) * (1.0 + CLUMP_W_VARY * (2.0 * hash01(len(LOCK_INFO), 3.7) - 1.0))         if W is None else W
    T = T0 if T is None else T
    root_k = rk0 if root_k is None else root_k
    free_k = s_leave_k if free_k is None else free_k
    _pf = np.asarray(ctl[min(free_k, len(ctl) - 1)], float)
    Cd, _ = VP.resample(VP.catmull(np.asarray(ctl, float), 16), RIBBON_DENSE)

    def sfree(Cd_):
        af_, _ = arc_frac(Cd_)
        return af_, min(max(float(af_[int(np.argmin(np.linalg.norm(Cd_ - _pf, axis=1)))]), 0.30), 0.85)

    def thick_k(s_, sf_):
        return CLUMP_SCALP_T + (1.0 - CLUMP_SCALP_T) * smoothstep(sf_ - 0.15, sf_ + 0.05, s_)

    af, s_free = sfree(Cd)
    Cd = taubin(Cd, RIBBON_FAIR[0] // 2, RIBBON_FAIR[1], RIBBON_FAIR[2])
    Cd, _ = clear_spine(Cd, 0.0012 + layer + 0.9 * T * thick_k(af, s_free), root_ramp=root_ramp)
    af, s_free = sfree(Cd)
    if sway and CLUMP_SWAY:
        hand = 1.0 if _sway_k[0] % 2 == 0 else -1.0
        _sway_k[0] += 1
        _, _, Wd_ = spine_frames(Cd)
        Cd = Cd + Wd_ * (hand * CLUMP_SWAY * W * np.sin(2 * math.pi * af) * smoothstep(max(s_free - 0.30, 0.0), s_free, af))[:, None]
    Cd = taubin(Cd, RIBBON_FAIR[0], RIBBON_FAIR[1], RIBBON_FAIR[2])
    af, s_free = sfree(Cd)
    Cd, clr_short = clear_spine(Cd, 0.0012 + layer + 0.9 * T * thick_k(af, s_free), root_ramp=root_ramp)

    def hw_of(fr_, sf_):
        u_ = np.clip((fr_ - sf_) / (1.0 - sf_), 0.0, 1.0)
        return 0.5 * W * (root_k + (1.0 - root_k) * smoothstep(0.0, CLUMP_ROOT_GROW, fr_)) * \
            (1.0 + RIBBON_BELLY * np.sin(math.pi * np.minimum(u_ / 0.35, 1.0))) * \
            (1.0 - (1.0 - RIBBON_TAPER[1]) * smoothstep(0.0, 1.0, u_) ** RIBBON_TAPER[0])
    # the BEND LIMIT (measured on the first ribbon build: where the spine bent sideways tighter than the ribbon's half
    # width, the inner edge folded back on itself -- a 150-165 deg zigzag in the silhouette edge): the in-plane
    # curvature x the half width is held under RIBBON_BEND[0] by local fairing of just the offending spine points
    af, _ = arc_frac(Cd)
    _, Nb_, _ = spine_frames(Cd)
    hwd_ = hw_of(af, s_free)
    bend_it_ = 0
    for bend_it_ in range(RIBBON_BEND[1]):
        Tb_ = np.gradient(Cd, axis=0)
        sl_ = np.maximum(np.linalg.norm(Tb_, axis=1), 1e-12)
        Tb_ = Tb_ / sl_[:, None]
        Wb_ = np.cross(Nb_, Tb_)
        kl_ = np.abs(np.einsum("ij,ij->i", np.gradient(Tb_, axis=0), Wb_)) / sl_
        bad_ = kl_ * hwd_ > RIBBON_BEND[0]
        bad_[0] = bad_[-1] = False
        if not bad_.any():
            break
        bad_ = np.convolve(bad_.astype(float), np.ones(5), mode="same") > 0
        bad_[0] = bad_[-1] = False
        Cd[bad_] = Cd[bad_] + 0.5 * (0.5 * (np.roll(Cd, 1, 0) + np.roll(Cd, -1, 0))[bad_] - Cd[bad_])
    BEND_LOG.append(bend_it_)
    Cd, clr2_ = clear_spine(Cd, 0.0012 + layer + 0.9 * T * thick_k(arc_frac(Cd)[0], s_free), iters=4, root_ramp=root_ramp)
    clr_short = max(clr_short, clr2_)
    af, Ld = arc_frac(Cd)
    Td, Nd, Wdd = spine_frames(Cd)
    # the angel-ring band on this lock: the first run of the spine inside the (per-lock jittered) ring elevations
    band_ = None
    if ANGEL_RING is not None and name.split(".")[1] not in ("beard", "beard_in", "beard_top", "must"):
        jr_ = RIBBON_RING_JITTER * (2.0 * hash01(len(LOCK_INFO), 5.3) - 1.0)
        el_ = ellip_el(Cd)
        ins_ = (el_ >= ANGEL_RING[0] + jr_) & (el_ <= ANGEL_RING[1] + jr_)
        if ins_.any():
            i0_ = int(np.argmax(ins_)); i1_ = i0_
            while i1_ + 1 < len(ins_) and ins_[i1_ + 1]:
                i1_ += 1
            a0_ = float(np.interp(0.5, [0, 1], [af[max(i0_ - 1, 0)], af[i0_]])) if i0_ > 0 else 0.0
            a1_ = float(np.interp(0.5, [0, 1], [af[i1_], af[min(i1_ + 1, len(af) - 1)]])) if i1_ < len(af) - 1 else 1.0
            if a1_ - a0_ > 0.02:
                band_ = (a0_, a1_)
    RING_BANDS[name] = band_
    forced_ = [HAIR_TIERS[0], HAIR_TIERS[1]] + (list(band_) if band_ else [])
    fr = station_fracs(Cd, RIBBON_STATIONS.get(tier, 16), forced_)
    C = interp_rows(fr, af, Cd)
    Tg = interp_rows(fr, af, Td); Tg /= np.linalg.norm(Tg, axis=1)[:, None]
    Nn = interp_rows(fr, af, Nd); Nn = Nn - Tg * np.einsum("ij,ij->i", Nn, Tg)[:, None]; Nn /= np.linalg.norm(Nn, axis=1)[:, None]
    Wn = np.cross(Nn, Tg); Wn /= np.linalg.norm(Wn, axis=1)[:, None]
    u_ = np.clip((fr - s_free) / (1.0 - s_free), 0.0, 1.0)
    hw = hw_of(fr, s_free)
    el_c = ellip_el(C)
    ht = T * thick_k(fr, s_free) * (1.0 - 0.8 * smoothstep(0.3, 1.0, u_)) * \
        (HAIR_ROOT_K + (1.0 - HAIR_ROOT_K) * smoothstep(0.0, 0.22, fr)) * \
        (1.0 - CLUMP_TOP_THIN[2] * smoothstep(CLUMP_TOP_THIN[0], CLUMP_TOP_THIN[1], el_c))
    PLAN[name] = {"C": C, "T": Tg, "N": Nn, "Wd": Wn, "hw": np.maximum(hw, 0.0004), "ht": np.maximum(ht, 0.0003), "fr": fr,
                  "lift": np.zeros(len(fr)), "tip_ext": 0.006 * CLUMP_S_TIP_K.get(tier, 1.0), "chain": chain,
                  "s_leave_k": s_leave_k, "ctl": [np.asarray(c_, float) for c_ in ctl], "tier": tier, "W": W,
                  "clear_short_mm": round(1000 * clr_short, 3), "dense_len": Ld}
    TIER_OF[name] = tier
    build_lock(name)


def ribbon_ring(p, n, w, hw, ht):
    """one section: corner, shoulder, ridge, shoulder, corner, underside (RIBBON_TOP / RIBBON_UNDER), bent with the head
    across its width (the v4 wrap: a parabola of the local head radius, CLUMP_WRAP)."""
    rc_ = max(float(np.linalg.norm(p - HC)), 0.06)
    x_ = _RX * hw
    return p + np.outer(x_, w) + np.outer(_RY * ht - x_ ** 2 / (2.0 * rc_) * CLUMP_WRAP, n)


def build_lock(name):
    """(re)build one ribbon lock's mesh from its PLAN (+ its resolve lift along the section normals): the loft, the
    whole-segment paint (top faces: hair_root / hair / hair_ring / hair_tip by the segment's arc fraction; the underside
    hair_shade), the outline (LOCK_EDGES) and spine (LOCK_SPINE); adds or updates the part."""
    P_ = PLAN[name]
    C = P_["C"] + P_["N"] * P_["lift"][:, None]
    rings = [ribbon_ring(C[k], P_["N"][k], P_["Wd"][k], P_["hw"][k], P_["ht"][k]) for k in range(len(C))]
    tip_ = C[-1] + P_["T"][-1] * P_["tip_ext"]
    V_, F_, _ = VP.loft(rings, "pole", "pole", reg="hair", pole1=tip_)
    m_ = len(_RX); ns_ = len(C)
    seg_ = np.linalg.norm(np.diff(C, axis=0), axis=1)
    sarc = np.concatenate([[0.0], np.cumsum(seg_)])
    Lt = float(sarc[-1])
    fr = P_["fr"]
    band_ = RING_BANDS.get(name)
    R_ = []
    for fi_ in range(len(F_)):
        if fi_ < (ns_ - 1) * m_:
            k_, i_ = divmod(fi_, m_)
            sf_ = 0.5 * (fr[k_] + fr[k_ + 1])
        else:
            j_ = fi_ - (ns_ - 1) * m_
            i_, cap_ = j_ // 2, j_ % 2                      # (loft: root fan, tip fan, interleaved per section edge)
            sf_ = 0.0 if cap_ == 0 else 1.0
        if i_ not in _TOP_EDGES:
            R_.append("hair_shade")
        elif band_ is not None and band_[0] <= sf_ <= band_[1] and sf_ <= HAIR_TIERS[1]:
            R_.append("hair_ring")
        else:
            R_.append("hair_root" if sf_ < HAIR_TIERS[0] else ("hair_tip" if sf_ > HAIR_TIERS[1] else "hair"))
    _ol = np.vstack([np.array([r_[0] for r_ in rings]), tip_[None], np.array([r_[4] for r_ in rings])[::-1]])
    _os = np.concatenate([fr, [1.0], fr[::-1]])
    LOCK_EDGES[name] = (_ol, _os)
    LOCK_SPINE[name] = np.vstack([C, tip_[None]])
    svert = np.concatenate([np.repeat(sarc, m_), [0.0, Lt]])
    ctl_s = [float(sarc[int(np.argmin(np.linalg.norm(C - c_, axis=1)))]) for c_ in P_["ctl"]]
    s_leave = ctl_s[min(P_["s_leave_k"], len(ctl_s) - 1)]
    chain = P_["chain"]
    kw_ = dict(w="lock" if chain else "rigid:head", s=svert, s_leave=s_leave, L=Lt, chain=chain,
               path=C[int(np.argmin(np.abs(sarc - s_leave))):])
    ex_ = next((p for p in PARTS if p["name"] == name), None)
    if ex_ is None:
        add_part(name, V_, F_, R_, **kw_)
    else:
        ex_.update({"V": np.asarray(V_, float), "F": [list(map(int, f)) for f in F_], "R": list(R_)}, **kw_)
    LOCK_INFO[name] = {"tier": P_["tier"], "width_mm": round(1000 * P_["W"], 1), "length": round(Lt, 3), "tip": C[-1].round(4).tolist(),
                       "chain": chain, "stations": int(ns_), "lift_max_mm": round(1000 * float(P_["lift"].max()), 2),
                       "min_clear": round(float(min(nearest(BVH_HAIR, p)[3] for p in C[1:])), 4)}


def clump_lens(name, tier, ctl, chain=None, s_leave_k=2, W=None, T=None, root_k=None, sway=True, free_k=None, layer=0.0):
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
            q, _, _, _ = nearest(BVH_HAIR, Cd[i])
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
        q, n_, _, _ = nearest(BVH_HAIR, p)
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
    LOCK_SPINE[name] = np.vstack([C, _tip[None]])
    svert = np.concatenate([np.repeat(sarc, 6), [0.0, Lt]])
    R_ = []
    for f in F_:
        q_ = V_[f]
        nn = np.cross(q_[1] - q_[0], q_[2] - q_[0])
        c_ = q_.mean(0)
        qq, _, _, _ = nearest(BVH_HAIR, c_)
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
                       "chain": chain, "min_clear": round(float(min(nearest(BVH_HAIR, p)[3] for p in C[1:])), 4)}


# =========================================================================== ELIAS LOCK TABLE
def radiate2(root_d, tip_d, kind, n_mid=3, f_end=0.86):
    """scalp lock control points: the root ON the cap at root_d, n_mid points along the great circle toward tip_d (to
    f_end), lifted off the cap by HAIR_LIFT[kind] down the sides (none over the top: elevation > 70 deg)."""
    pts = [on_dir(root_d, 0.0)]
    for j in range(1, n_mid + 1):
        f = f_end * j / n_mid
        d_ = slerp(root_d, tip_d, f)
        el_ = math.degrees(math.asin(float(np.clip(d_[2], -1.0, 1.0))))
        pts.append(on_dir(d_, LOCK_OFF + HAIR_LIFT[kind] * (1.0 - float(smoothstep(35.0, 70.0, el_)))))
    return pts


_nb = {}
SCALP_INFO = {}
WHORL_D = unit(hdir(*HAIR_WHORL))
for kind, tier, (p1_, e1_, fl_), mir_, chain_ in HAIR_LOCKS:
    for sg_, sn_ in (((1.0, "L"), (-1.0, "R")) if mir_ else ((1.0, ""),)):
        lay_ = HAIR_LAYER[kind] + CLUMP_STACK * _nb.get(kind, 0)
        tip_d = unit(hdir(sg_ * p1_, e1_))
        root_d = slerp(WHORL_D, tip_d, CLUMP_ROOT[kind])            # (Wren: radiating from the whorl, root CLUMP_ROOT out)
        ctl = radiate2(root_d, tip_d, kind) + [on_dir(tip_d, LOCK_OFF + fl_)]
        nm_ = "lock.%s.%s%d" % (kind, sn_, _nb.get(kind, 0))
        ch_ = None if chain_ is None else (chain_ + "." + (sn_ or "C") if chain_ == "hair_side" else chain_)
        clump(nm_, tier, ctl, chain=ch_, s_leave_k=3, layer=lay_, free_k=(3 if kind in ("back", "outer", "side") else 2))
        SCALP_INFO[nm_] = {"tip_psi_el_flick": [sg_ * p1_, e1_, fl_], "chain": ch_}
        _nb[kind] = _nb.get(kind, 0) + 1
# ---- the BEARD: groomed ribbon-lock masses rooted on the face skin (over the painted under-beard zone), falling from
# the sideburns / cheeks / chin to a soft point BEARD_LEN below the chin, standing BEARD_FWD in front of the chest
LANDMARK_Z = {"eye": EZ, "nose": Z_NOSE_BOTTOM, "slit": Z_SLIT, "chin": Z_CHIN_B}


def beard_env_pt(p, off):
    """the beard ENVELOPE: p moved out along the horizontal ray from the head's vertical axis to at least `off` outside the
    outermost surface there (head / neck / cravat / chest: BVH_HAIR) -- a lock path sampled straight from root to tip and
    pushed onto this envelope hugs the cheek and jaw, then hangs in front of the throat (one convex groomed mass)."""
    v_ = np.array([p[0], p[1] - HC[1]])
    r_ = float(np.linalg.norm(v_))
    d_ = np.array([v_[0] / max(r_, 1e-9), v_[1] / max(r_, 1e-9), 0.0])
    c_ = np.array([0.0, HC[1], p[2]])
    h_ = BVH_HAIR.ray_cast(Vector(c_ + d_ * 0.5), Vector(-d_), 0.5)
    rs_ = 0.5 - h_[3] if h_[0] is not None else 0.0
    return c_ + d_ * max(r_, rs_ + off)


def face_pt(psi, z, off):
    """the head skin at azimuth psi (from the front, + his left) and height z, reached horizontally from outside toward
    the head's vertical axis, + off along the skin normal."""
    d = np.array([math.sin(math.radians(psi)), -math.cos(math.radians(psi)), 0.0])
    o = np.array([0.0, HC[1], z]) + d * 0.4
    hit = BVH_HEADONLY.ray_cast(Vector(o), Vector(-d), 0.4)
    if hit[0] is None:
        hit = BVH_BODY.ray_cast(Vector(o), Vector(-d), 0.4)
    n_ = unit(np.array(hit[1]))
    n_ = n_ if float(n_ @ d) > 0 else -n_
    return np.array(hit[0]) + n_ * off


def front_pt(x, z, off, bvh=None):
    """the first surface hit by a front ray at (x, z) (default: the head skin), + off toward the camera (-y)."""
    hit = (bvh or BVH_HEADONLY).ray_cast(Vector((float(x), -0.8, float(z))), Vector((0.0, 1.0, 0.0)), 1.6)
    if hit[0] is None:
        return None
    return np.array(hit[0]) + np.array([0.0, -off, 0.0])


BEARD_INFO = {}
for kind, tier, psi_, (lm_, dz_), share_ in BEARD_LOCKS:
    if lm_ == "edge":                                           # v2: rooted ON the under-beard zone's top edge (no grey mask)
        x0_ = abs(float(face_pt(psi_, EZ - 0.05, 0.0)[0]))
        zr_ = float(beard_top_z(np.array([x0_]))[0]) + dz_
        x0_ = abs(float(face_pt(psi_, zr_, 0.0)[0]))
        zr_ = float(beard_top_z(np.array([x0_]))[0]) + dz_
    else:
        zr_ = LANDMARK_Z[lm_] + dz_
    root_ = face_pt(psi_, zr_, 0.0)
    xt_ = float(np.clip(root_[0] * (1.0 - BEARD_POINT * share_), -BEARD_TIP_W, BEARD_TIP_W))
    zt_ = Z_CHIN_B - BEARD_LEN * share_
    tip_ = beard_env_pt(np.array([xt_, HC[1] - 0.01, zt_]), BEARD_FWD)
    ctl = [root_] + [beard_env_pt(root_ + (tip_ - root_) * t_, LOCK_OFF + 0.002 + t_ * 0.6 * BEARD_FWD)
                     for t_ in (0.22, 0.45, 0.70)] + [tip_]
    nm_ = "lock.%s.%d" % (kind, _nb.get(kind, 0))
    ch_ = "beard.C" if abs(psi_) <= BEARD_CHAIN_PSI else ("beard.L" if psi_ > 0 else "beard.R")
    clump(nm_, tier, ctl, chain=ch_, s_leave_k=2, free_k=2, T=BEARD_T[kind], layer=HAIR_LAYER[kind] + CLUMP_STACK * _nb.get(kind, 0))
    BEARD_INFO[nm_] = {"root": (1000 * (root_ - HC)).round(1).tolist(), "tip": (1000 * (tip_ - HC)).round(1).tolist()}
    _nb[kind] = _nb.get(kind, 0) + 1
# ---- the BEARD CORE: the hanging beard's dark inner mass (the scalp's "dark inner cap" principle: gaps between locks read
# as shadowed beard, never as the cravat / chest behind) -- a sheet on the beard envelope from the jaw line down to near
# the point, narrowing like the beard, just inside the locks
_bc_rows, _bc_cols = 10, 13
_bcV = []
_jx = BEARD_CORE_W
for j in range(_bc_rows):
    t_ = j / (_bc_rows - 1)
    z_ = Z_CHIN_B + 0.012 - t_ * (0.012 + 0.90 * BEARD_LEN)
    hw_ = _jx * (1.0 - t_) + BEARD_TIP_W * (1.0 - BEARD_POINT) * 0.8 * t_
    for i in range(_bc_cols):
        x_ = -hw_ + 2.0 * hw_ * i / (_bc_cols - 1)
        _bcV.append(beard_env_pt(np.array([x_, HC[1] - 0.02, z_]), BEARD_CORE_OFF + 0.4 * t_ * BEARD_FWD))
_bcV = np.array(_bcV)
_bcF = VP.grid_faces(_bc_cols, _bc_rows)
_bcF = [f if np.dot(np.cross(_bcV[f[1]] - _bcV[f[0]], _bcV[f[2]] - _bcV[f[0]]), _bcV[f].mean(0) - np.array([0.0, HC[1], _bcV[f].mean(0)[2]])) > 0
        else f[::-1] for f in _bcF]
V_, F_, R_ = VP.solidify(_bcV, _bcF, 0.0010, 0.0010, "hair_inner", "hair_inner", "hair_inner")
add_part("beardcore", V_, F_, R_, w="rigid:head")
# ---- the MUSTACHE: from under the nose over the upper lip, drooping past the mouth corners; its lower edge kept above
# the drawn mouth line at the centre (MUSTACHE_LIP_CLEAR) so the one-line mouth still reads
_seam_hw = 0.5 * float(SEAM["xs"].max() - SEAM["xs"].min()) if SEAM is not None else 0.022
# each mustache lock runs ALONG the upper lip (centre -> corner) and droops past the corner: its ribbon width is then the
# lip's height (nose bottom down to MUSTACHE_LIP_CLEAR above the line), full at the centre (root_k 1), tapering to the tip
_zm0 = 0.5 * (Z_NOSE_BOTTOM + Z_SLIT) + 0.0008
for k, (xr_, xtp_, drop_, w_) in enumerate(MUSTACHE):
    for sg_, sn_ in ((1.0, "L"), (-1.0, "R")):
        off_ = LOCK_OFF + MUSTACHE_T * (1.0 + k)
        pts_ = [front_pt(sg_ * xr_, _zm0 + 0.0006 * k, off_),
                front_pt(sg_ * (0.5 * (xr_ + _seam_hw)), _zm0 - 0.0008, off_),
                front_pt(sg_ * (_seam_hw + 0.002), Z_SLIT + 0.0030 - 0.001 * k, off_ + 0.0005),
                front_pt(sg_ * xtp_, Z_SLIT - drop_ * (Z_SLIT - Z_CHIN_B), off_ + 0.0010)]
        if any(p_ is None for p_ in pts_):
            continue
        nm_ = "lock.must.%s%d" % (sn_, k)
        clump(nm_, "M", pts_, chain=None, s_leave_k=1, free_k=1, W=w_, T=MUSTACHE_T, sway=False, root_k=1.0,
              layer=HAIR_LAYER["must"] + CLUMP_STACK * k)
        BEARD_INFO[nm_] = {"root": (1000 * (pts_[0] - HC)).round(1).tolist(), "tip": (1000 * (pts_[-1] - HC)).round(1).tolist()}
_ncap = len(Fcap)
BVH_CAP_OUT = BVHTree.FromPolygons(CAP_V.tolist(), CAP_F[:_ncap] + CAP_F[2 * _ncap:])   # (outer shell + rim: the inner
#                                                              shell's normals face the scalp and would push INTO the head)
# ---- v7 LAYER RESOLVE (the "chopped" diagnosis: in v6 the locks' visible TOP sheets cut through each other all over the
# head -- every overlap printed a jagged crossing line and shards of the lock beneath poked through the one above). What
# the eye reads is the top sheet (corner - shoulder - ridge - shoulder - corner) of every lock; a lower lock tucked INSIDE
# the upper one's underside is hidden, the way a lock lies on the one beneath it. So: (1) every pair of locks whose top
# sheets overlap gets ONE order -- whichever top sheet already rides higher over their overlap (the summed height
# difference along the section normals), ties broken by the v4 stack order (CLUMP_LAYER per kind + CLUMP_STACK per
# clump); (2) rounds over every lock: each top-sheet vertex looks along its section's outward normal at the other locks'
# top sheets; under a lock ordered above it that is closer than LAYER_GAP it drops (only within its room over the skin /
# cap, HAIR_CLEAR, and half the need: the upper lock lifts the rest), over a lock ordered below it that is closer than
# LAYER_GAP (or above it) it lifts; the section takes the largest lift, else the drop; the moves are dilated and
# Gaussian-smoothed along the lock (LAYER_SMOOTH) and the lock is rebuilt -- it drapes over / tucks under its neighbour
# in one smooth ramp and stays a ribbon. The nape tail's root is tucked into the back hair by design (its tie): it is no
# obstacle, and only its free part (past LAYER_TAIL_FREE) resolves; every lock's ROOT (arc fraction < LAYER_ROOT: the
# petals converging on the part line and the whorl, where roots cross by nature) neither moves nor obstructs -- measured:
# kept in, the fringe's four roots sharing the part's front end chased each other up to the lift limit.
LAYER_INFO = {"enabled": RIBBON, "gap_mm": LAYER_GAP * 1000, "rounds": LAYER_ITERS, "moves": 0, "lift_max_mm": 0.0,
              "lift_max_part": None, "drop_max_mm": 0.0, "drop_max_part": None}
LAYER_TAIL_FREE = 0.30
_TOP_RING = (0, 1, 2, 3, 4)                            # the section vertices on the top sheet (5 = the underside)


def _top_tris(p):
    return [[f[0], f[k], f[k + 1]] for f, r in zip(p["F"], p["R"]) if r != "hair_shade" for k in range(1, len(f) - 1)]


def _sheets_bvh(names):
    """the top sheets of the named locks in one BVH + the owning lock and arc fraction (at the triangle's first vertex)
    of every triangle; a lock root (arc fraction < LAYER_ROOT) is no obstacle."""
    V_, F_, own_, o_ = [], [], [], 0
    for n_ in names:
        p = next(q for q in PARTS if q["name"] == n_)
        tt_ = [t for t in _top_tris(p) if float(p["s"][t[0]]) / p["L"] >= LAYER_ROOT]
        V_.append(p["V"]); F_ += [[i + o_ for i in t] for t in tt_]; own_ += [n_] * len(tt_); o_ += len(p["V"])
    return BVHTree.FromPolygons(np.vstack(V_).tolist(), F_), own_


def _sheet_hits(bvh_, own_, v_, n_):
    """{lock: delta}: every other top sheet crossing the line through v_ along n_ within +-LAYER_WINDOW, delta = its height
    above v_ along n_ (the nearest crossing per lock)."""
    o_ = v_ + n_ * LAYER_WINDOW
    d_ = -n_
    t_, res_ = 0.0, {}
    lim_ = 2.0 * LAYER_WINDOW
    for _ in range(40):
        h_ = bvh_.ray_cast(Vector(o_ + d_ * t_), Vector(d_), lim_ - t_)
        if h_[0] is None:
            break
        th_ = t_ + float(h_[3])
        lk_, dl_ = own_[h_[2]], LAYER_WINDOW - th_
        if lk_ not in res_ or abs(dl_) < abs(res_[lk_]):
            res_[lk_] = dl_
        t_ = th_ + 1e-6
        if t_ >= lim_:
            break
    return res_


def _skin_room(v_, n_):
    """-> (need, room): the lift along n_ that puts v_ HAIR_CLEAR[0] off the head / neck skin and HAIR_CLEAR[1] off the
    cap's surface, and how far it may drop along -n_ before breaking those clearances."""
    need_, room_ = 0.0, 9.0
    for bvh_, clr_, skin_ in ((BVH_BODY, HAIR_CLEAR[0], True), (BVH_CAP_OUT, HAIR_CLEAR[1], False)):
        q_, nn_, fi_, _ = nearest(bvh_, v_, 0.03)
        if q_ is None or (skin_ and fdomn[fi_] not in ("head", "neck_01")):
            continue
        nn_ = np.array(nn_)
        sd_ = float((v_ - np.array(q_)) @ nn_)
        c_ = max(float(nn_ @ n_), 0.3)
        if sd_ < clr_:
            need_ = max(need_, (clr_ - sd_) / c_)
        room_ = min(room_, max(sd_ - clr_, 0.0) / c_)
    return need_, room_


def lock_intersections(names):
    """lock-vs-lock triangle pairs cutting through each other (BVHTree.overlap): all faces, and TOP SHEETS only (the
    crossings the eye reads) -- + the lock pairs involved."""
    out_ = {}
    for key_, sel_ in (("all", lambda p: [[f[0], f[k], f[k + 1]] for f in p["F"] for k in range(1, len(f) - 1)]), ("top", _top_tris),
                       ("top_past_roots", lambda p: [t for t in _top_tris(p) if float(p["s"][t[0]]) / p["L"] >= LAYER_ROOT])):
        bv_ = {n_: BVHTree.FromPolygons(next(q for q in PARTS if q["name"] == n_)["V"].tolist(),
                                        sel_(next(q for q in PARTS if q["name"] == n_))) for n_ in names}
        pr_, tp_ = 0, 0
        for i, a_ in enumerate(names):
            for b_ in names[i + 1:]:
                ov_ = bv_[a_].overlap(bv_[b_])
                if ov_:
                    pr_ += 1; tp_ += len(ov_)
        out_[key_] = {"lock_pairs": pr_, "tri_pairs": tp_}
    return out_


def _top_verts(nm_, k_, ns_):
    p = next(q for q in PARTS if q["name"] == nm_)
    m_ = len(_RX)
    return [p["V"][k_ * m_ + j] for j in _TOP_RING] + ([p["V"][-1]] if k_ == ns_ - 1 else [])


if RIBBON:
    _key = {}
    _nk = {}
    for p in PARTS:
        n_ = p["name"]
        if not n_.startswith("lock."):
            continue
        kind_ = n_.split(".")[1]
        if n_ == "lock.ahoge":
            _key[n_] = (9.0, n_)
        elif n_ == "lock.tail":
            _key[n_] = (CLUMP_LAYER["back"] + TAIL[2], n_)
        else:
            _key[n_] = (CLUMP_LAYER[kind_] + CLUMP_STACK * _nk.get(kind_, 0), n_)
            _nk[kind_] = _nk.get(kind_, 0) + 1
    LAYER_ORDER = sorted(_key, key=lambda n_: _key[n_])
    _res = [n_ for n_ in LAYER_ORDER if n_ != "lock.tail"]
    LAYER_INFO["before"] = lock_intersections(_res)
    # (1) the pair orders: summed top-sheet height differences over the overlaps, both ways
    _score = {}
    for nm_ in LAYER_ORDER:
        bvh_, own_ = _sheets_bvh([n_ for n_ in _res if n_ != nm_])
        P_ = PLAN[nm_]
        for k_ in range(len(P_["C"])):
            if P_["fr"][k_] < LAYER_ROOT:
                continue
            for v_ in _top_verts(nm_, k_, len(P_["C"])):
                for lk_, dl_ in _sheet_hits(bvh_, own_, v_, P_["N"][k_]).items():
                    if abs(dl_) > LAYER_NEAR:
                        continue
                    a_, b_ = sorted((nm_, lk_))
                    _score[(a_, b_)] = _score.get((a_, b_), 0.0) + (dl_ if nm_ == a_ else -dl_)   # > 0: b above a
    # one TOTAL order (no cycles: pairwise majorities alone formed loops round the fringe's shared roots at the part's
    # front end, and each lock lifted over the next without end): every lock scores + |pair score| where it rides above,
    # - where below; ranked bottom -> top, ties by the stack order
    _pts = {n_: 0.0 for n_ in LAYER_ORDER}
    for (a_, b_), sc_ in _score.items():
        up_, dn_ = (b_, a_) if sc_ > 0 else (a_, b_)
        _pts[up_] += abs(sc_); _pts[dn_] -= abs(sc_)
    # within one kind the v4 designed stack stands (HAIR_CLUMPS lists each kind bottom -> top: the nape under-layer under
    # the flared back points, the fringe up to the between-eyes lock): the kind's scores are dealt back to its locks in
    # that order, so the kind keeps its place among the others
    for kind_ in set(n_.split(".")[1] for n_ in LAYER_ORDER):
        mem_ = sorted([n_ for n_ in LAYER_ORDER if n_.split(".")[1] == kind_], key=lambda n_: _key[n_])
        for n_, v_ in zip(mem_, sorted(_pts[m_] for m_ in mem_)):
            _pts[n_] = v_
    RANKED = sorted(LAYER_ORDER, key=lambda n_: (_pts[n_], _key[n_]))
    _rank = {n_: i for i, n_ in enumerate(RANKED)}
    ABOVE = {(x_, y_): _rank[y_] > _rank[x_] for x_ in LAYER_ORDER for y_ in LAYER_ORDER if x_ != y_}
    LAYER_INFO["pairs_ordered"] = len(_score)
    LAYER_INFO["pairs_against_natural"] = sum(1 for (a_, b_), sc_ in _score.items() if abs(sc_) > 1e-4 and ABOVE[(a_, b_)] != (sc_ > 0))
    LAYER_INFO["pairs_against_stack_order"] = sum(1 for (a_, b_) in _score if ABOVE[(a_, b_)] != (_key[b_] > _key[a_]))
    # (2) the rounds
    for rnd_ in range(LAYER_ITERS):
        worst_ = 0.0
        REQ_ = {n_: np.zeros(len(PLAN[n_]["C"])) for n_ in RANKED}   # lift requests from the locks beneath (a lower
        #   lock's vertex poking up through an upper lock BETWEEN the upper one's own vertices: only the lower one sees it)
        for nm_ in RANKED:
            P_ = PLAN[nm_]
            bvh_, own_ = _sheets_bvh([n_ for n_ in _res if n_ != nm_])
            ns_ = len(P_["C"])
            mv_ = np.zeros(ns_)
            for k_ in range(ns_):
                if P_["fr"][k_] < (LAYER_TAIL_FREE if nm_ == "lock.tail" else LAYER_ROOT):
                    continue
                U_, D_, need_, room_ = 0.0, 0.0, 0.0, 9.0
                for v_ in _top_verts(nm_, k_, ns_):
                    for lk_, dl_ in _sheet_hits(bvh_, own_, v_, P_["N"][k_]).items():
                        if abs(dl_) > LAYER_NEAR:
                            continue
                        if ABOVE[(nm_, lk_)]:
                            if dl_ < LAYER_GAP:
                                D_ = max(D_, LAYER_GAP - dl_)
                                Pk_ = PLAN[lk_]
                                hp_ = v_ + P_["N"][k_] * dl_
                                kk_ = int(np.argmin(np.linalg.norm(Pk_["C"] + Pk_["N"] * Pk_["lift"][:, None] - hp_, axis=1)))
                                if Pk_["fr"][kk_] >= LAYER_ROOT:
                                    REQ_[lk_][kk_] = max(REQ_[lk_][kk_], LAYER_GAP - dl_)
                        elif dl_ > -LAYER_GAP:
                            U_ = max(U_, LAYER_GAP + dl_)
                    if nm_ != "lock.tail":
                        nd_, rm_ = _skin_room(v_, P_["N"][k_])
                        need_, room_ = max(need_, nd_), min(room_, rm_)
                U_ = max(U_, float(REQ_[nm_][k_]))
                mv_[k_] = U_ if U_ > 0 else -min(0.5 * D_, room_)   # (the skin / cap clearance itself is the spine's
                #   margin + the per-vertex pass below: a lift along the section normal cannot fix a corner beside an ear)
            worst_ = max(worst_, float(np.abs(mv_).max()))
            if np.abs(mv_).max() < 2e-5:
                continue
            pos_ = gauss1(maxfilt(np.maximum(mv_, 0.0), 1), LAYER_SMOOTH)
            neg_ = gauss1(maxfilt(np.maximum(-mv_, 0.0), 1), LAYER_SMOOTH)
            dl_ = np.maximum(pos_ - neg_, mv_)             # (a section's own required lift is never cancelled by a
            #   neighbour's optional drop)
            P_["lift"] = np.clip(P_["lift"] + dl_, -LAYER_LIFT_MAX, LAYER_LIFT_MAX)
            LAYER_INFO["moves"] += 1
            build_lock(nm_)
        LAYER_INFO["round_%d_max_move_mm" % rnd_] = round(1000 * worst_, 2)
    for nm_ in LAYER_ORDER:
        lf_ = PLAN[nm_]["lift"]
        if 1000 * lf_.max() > LAYER_INFO["lift_max_mm"]:
            LAYER_INFO["lift_max_mm"] = round(1000 * float(lf_.max()), 2); LAYER_INFO["lift_max_part"] = nm_
        if -1000 * lf_.min() > LAYER_INFO["drop_max_mm"]:
            LAYER_INFO["drop_max_mm"] = round(-1000 * float(lf_.min()), 2); LAYER_INFO["drop_max_part"] = nm_
    LAYER_INFO["after"] = lock_intersections(_res)
    LAYER_INFO["ranked_bottom_to_top"] = RANKED
    LAYER_INFO["lift_profile_mm"] = {n_: [[round(float(f_), 2) for f_ in PLAN[n_]["fr"]], [round(1000 * float(l_), 1) for l_ in PLAN[n_]["lift"]]] for n_ in LAYER_ORDER}
print("LAYER", json.dumps({k_: v_ for k_, v_ in LAYER_INFO.items() if k_ not in ("order", "lift_profile_mm")}))
# ---- v4 CLEARANCE PASS: every clump vertex that ends up inside the head / neck skin (an ear under a side clump that now
# falls straight from the part, a swept fringe tip at the brow) or sunk under the cap's surface is pushed out along the
# surface normal to HAIR_CLEAR[0] off the skin / HAIR_CLEAR[1] off the cap (the hair-into-head gate's rest baseline).
# v7: the ribbons already clear by the section lift above; this per-vertex push is the fallback (count reported)
PUSH_INFO = {"skin_clear_mm": HAIR_CLEAR[0] * 1000, "cap_clear_mm": HAIR_CLEAR[1] * 1000, "verts": 0, "max_push_mm": 0.0}
for p in PARTS:
    if not p["name"].startswith("lock."):
        continue
    V_ = p["V"].copy()
    for i in range(len(V_)):
        for bvh_, clr_, skin_ in ((BVH_BODY, HAIR_CLEAR[0], True), (BVH_CAP_OUT, HAIR_CLEAR[1], False)):
            q_, n_, fi_, d_ = nearest(bvh_, V_[i], 0.03)
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
if RIBBON:
    # v7: the ring is painted by WHOLE SEGMENTS (build_lock): each crossing lock gets two stations at the arc fractions
    # where its spine enters / leaves the (jittered) ring elevations -- one clean stroke straight across the lock, no cut
    RING_INFO["rule"] = "v7: whole ribbon segments between the two stations placed where the spine crosses the ring elevations"
    RING_INFO["clumps_crossed"] = sum(1 for b_ in RING_BANDS.values() if b_ is not None)
    RING_INFO["faces"] = sum(p["R"].count("hair_ring") for p in PARTS if p["name"].startswith("lock."))
if ANGEL_RING is not None and not RIBBON:
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
CREVICE_INFO = {"enabled": HAIR_CREVICE is not None and not RIBBON}
# v7 TUCK SHADE (the ribbons' layer shadow; review-log 2026-09-29 "Wren v7 hair feedback", measured on the first ribbon
# build: the v4 bands, traced as thin cuts onto the ribbons, printed as scattered small squares -- a 2-5 mm band cut
# through ~10 mm facets, gated on / off vertex by vertex -- "chopped" again). Instead every ribbon SEGMENT (the strip
# between two stations) whose top sheet lies mostly UNDER the locks layered above it (the resolve's order: RIBBON_TUCK[0]
# of its sample points covered, a ray out along the section normal meeting their top sheets) takes the crevice tone on its
# whole top: the visible part of such a segment is exactly the strip beside the upper lock's edge, so the shadow's outline
# is that lock's own crisp edge plus straight station lines across the lock -- no cut, no sliver, no patch.
TUCK_INFO = {"enabled": bool(RIBBON and RIBBON_TUCK is not None)}
if TUCK_INFO["enabled"]:
    _m = len(_RX)
    TUCK_INFO.update({"rule": "segment top faces -> hair_crevice when >= %.2f of its %d sample points lie under a lock "
                              "layered above it (rays out along the section normal, within %.0f mm); segments above %.0f deg "
                              "elevation (the lit crown) none" % (RIBBON_TUCK[0], 4 * RIBBON_TUCK[1], 1000 * RIBBON_TUCK[2],
                                                                   HAIR_CREVICE[4]),
                      "segments": 0, "segments_total": 0, "faces": 0, "locks_receiving": 0, "pairs": 0})
    _tpairs = set()
    for p in PARTS:
        nm_ = p["name"]
        if not nm_.startswith("lock.") or nm_ in ("lock.ahoge",) or nm_ not in PLAN:   # (v2: the tuck shade on every lock, beard too)
            continue
        ups_ = [n_ for n_ in _res if n_ != nm_ and ABOVE.get((nm_, n_), False)]
        if not ups_:
            continue
        bvh_, own_ = _sheets_bvh(ups_)
        P_ = PLAN[nm_]
        ns_ = len(P_["C"])
        hit_any = False
        for k_ in range(ns_ - 1):
            if P_["fr"][k_] < LAYER_ROOT:
                continue
            if float(ellip_el(0.5 * (P_["C"][k_] + P_["C"][k_ + 1]))[0]) > HAIR_CREVICE[4]:
                continue                                   # (the lit crown keeps the angel ring: v4's receiver limit)
            TUCK_INFO["segments_total"] += 1
            pts_, cov_ = 0, 0
            nk_ = unit(P_["N"][k_] + P_["N"][k_ + 1])
            for i_ in _TOP_EDGES:
                a0_, a1_ = p["V"][k_ * _m + i_], p["V"][k_ * _m + (i_ + 1) % _m]
                b0_, b1_ = p["V"][(k_ + 1) * _m + i_], p["V"][(k_ + 1) * _m + (i_ + 1) % _m]
                for u_ in np.linspace(0.2, 0.8, RIBBON_TUCK[1]):
                    q_ = 0.5 * ((1 - u_) * (a0_ + a1_) + u_ * (b0_ + b1_))
                    h_ = bvh_.ray_cast(Vector(q_ + nk_ * 1e-5), Vector(nk_), RIBBON_TUCK[2])
                    pts_ += 1
                    if h_[0] is not None:
                        cov_ += 1
                        _tpairs.add((own_[h_[2]], nm_))
            if pts_ and cov_ / pts_ >= RIBBON_TUCK[0]:
                for i_ in _TOP_EDGES:
                    fi_ = k_ * _m + i_
                    if p["R"][fi_] in ("hair", "hair_root", "hair_tip", "hair_ring"):
                        p["R"][fi_] = "hair_crevice"; TUCK_INFO["faces"] += 1
                TUCK_INFO["segments"] += 1; hit_any = True
        TUCK_INFO["locks_receiving"] += int(hit_any)
    TUCK_INFO["pairs"] = len(_tpairs)
    TUCK_INFO["share_of_segments_pct"] = round(100.0 * TUCK_INFO["segments"] / max(TUCK_INFO["segments_total"], 1), 1)
    print("TUCK", json.dumps(TUCK_INFO))
if CREVICE_INFO["enabled"]:
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
RIBBON_INFO = ribbon_metrics()
print("RIBBON", json.dumps(RIBBON_INFO))
_kinds = sorted(set(p["name"].split(".")[1] for p in _clumps))
BEARD_KINDS = ("beard_in", "beard", "beard_top", "must")
_HZ = max(float(p["V"][:, 2].max()) for p in PARTS if p["name"].startswith(("lock", "hair_cap")))
HAIR_INFO = {"ribbon": RIBBON_INFO, "layer_resolve": {k_: v_ for k_, v_ in LAYER_INFO.items() if k_ != "lift_profile_mm"},
             "tuck_shade": TUCK_INFO, "locks": len(_clumps), "tiers": {t: sum(1 for n in TIER_OF.values() if n == t) for t in "LMS"},
             "by_kind": {k: sum(1 for p in _clumps if p["name"].split(".")[1] == k) for k in _kinds},
             "scalp_locks": sum(1 for p in _clumps if p["name"].split(".")[1] not in BEARD_KINDS),
             "beard_locks": sum(1 for p in _clumps if p["name"].split(".")[1] in BEARD_KINDS and p["name"].split(".")[1] != "must"),
             "mustache_locks": sum(1 for p in _clumps if p["name"].split(".")[1] == "must"),
             "sway_x_width": CLUMP_SWAY, "angel_ring": RING_INFO, "cap_feather": CAP_FEATHER_INFO,
             "clearance_pass": PUSH_INFO, "slivers": SLIVER_INFO, "scalp_table": SCALP_INFO, "beard_table": BEARD_INFO,
             "paint_faces": {r_: sum(p["R"].count(r_) for p in _clumps + [q for q in PARTS if q["name"] == "hair_cap"])
                             for r_ in ("hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice")},
             "seconds": round(time.time() - t_hair, 1),
             "crown": {"scalp_top_z": round(Z_TOP, 4), "hair_top_z": round(_HZ, 4), "crown_above_scalp_mm": round(1000 * (_HZ - Z_TOP), 1)}}
# v5.1 HAIR DIGEST: the exact bytes (float64 positions, faces, regions -- no rounding) of every hair part as s5 hands them to
# the assembly; the hair-face decoupling proof compares it across builds with different FACE constants (wren_run.ps1).
_hd = hashlib.sha256()
for p in PARTS:
    if p["name"].startswith(("lock.", "hair_cap", "hairtie")):
        _hd.update(p["name"].encode()); _hd.update(np.ascontiguousarray(p["V"], dtype=np.float64).tobytes())
        _hd.update(np.array([i for f in p["F"] for i in [len(f)] + list(f)], dtype=np.int64).tobytes()); _hd.update("|".join(p["R"]).encode())
DIG["hair_geometry"] = HAIR_INFO["digest_exact"] = _hd.hexdigest()[:16]
print("HAIR", json.dumps({k_: v_ for k_, v_ in HAIR_INFO.items() if k_ not in ("scalp_table", "beard_table")}))
