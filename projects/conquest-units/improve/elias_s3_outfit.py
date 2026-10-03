# Elias build section 3 (adapted from wren_s3_outfit.py): the solid outfit pieces (closed solids, skinned from the body
# under them): eyes, boots (Wren's foot shell + sole + shaft + folded cuff, the cuff's top edge a gold trim band), the
# cream robe skirt below the belt, the wide brown belt + gold buckle + pouches + leather scroll tubes tucked in, the big
# rolled robe sleeves, the dark bracers on both forearms (gold rims), the blue cravat (band + knot + puffed fall).
PARTS = []          # dicts: name, V, F, R, w ('transfer' | 'rigid:<bone>' | 'rigid_transfer' | scheme), obj, + extras


def add_part(name, V, F, R, w="transfer", obj="main", **kw):
    d = {"name": name, "V": np.asarray(V, float), "F": [list(map(int, f)) for f in F], "R": list(R), "w": w, "obj": obj}
    d.update(kw)
    assert len(d["F"]) == len(d["R"]), name
    PARTS.append(d)
    return d


def comb_bvh(names, with_body=True, body=None):
    if with_body:
        bv_, bf_ = body if body is not None else (CV, CF)
        Vs, Fs, o = [bv_], [list(f) for f in bf_], len(bv_)
    else:
        Vs, Fs, o = [np.zeros((0, 3))], [], 0
    for p in PARTS:
        if p["name"].split(".")[0] in names:
            Vs.append(p["V"]); Fs += [[i + o for i in f] for f in p["F"]]; o += len(p["V"])
    return BVHTree.FromPolygons(np.vstack(Vs).tolist(), Fs)


def dir_front(a_deg):
    """horizontal unit direction at azimuth a from the FRONT (+ toward his left)."""
    a_ = math.radians(a_deg)
    return np.array([math.sin(a_), -math.cos(a_), 0.0])


def rp_front(bvh, a_deg, zs, ax=0.0, ay=None):
    """outermost surface radius from the vertical axis (ax, ay) at azimuth a from the FRONT (+ his left)."""
    return radial_profile(bvh, ax, AX_Y if ay is None else ay, 180.0 - a_deg, zs)


def limb_grid(A, B, ts, clear_fn, bvh, nu, front=(0.0, -1.0, 0.0), rmax=0.3):
    """rings round the segment A -> B at fractions ts: per angle the skin radius (a ray from outside toward the axis
    against bvh) + clear_fn(t, theta). theta 0 = the 'front' direction. Closed in u, faces oriented outward. (Wren's)"""
    A, B = np.asarray(A, float), np.asarray(B, float)
    a = unit(B - A)
    u = unit(np.asarray(front, float) - a * float(np.dot(front, a)))
    v = np.cross(a, u)
    V = []
    last = None
    for t in ts:
        c = A + (B - A) * t
        ring = []
        for k in range(nu):
            th = 2 * math.pi * k / nu
            d = math.cos(th) * u + math.sin(th) * v
            h = bvh.ray_cast(Vector(c + d * rmax), Vector(-d), rmax)
            r = (rmax - h[3]) if h[0] is not None else (last[k] if last is not None else 0.05)
            ring.append(r)
        last = ring
        for k in range(nu):
            th = 2 * math.pi * k / nu
            d = math.cos(th) * u + math.sin(th) * v
            V.append(c + d * (ring[k] + clear_fn(t, th)))
    V = np.array(V)
    F = VP.grid_faces(nu, len(ts), closed_u=True)
    F = [f if np.dot(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]),
                     V[f].mean(0) - (A + a * float(np.dot(V[f].mean(0) - A, a)))) > 0 else f[::-1] for f in F]
    return V, F, (u, v, a)


def ring_band(bvh, z0, z1, clear, nu=40, rows=3, ay=None, wobble=0.0):
    """a band round the vertical axis between heights z0 .. z1 sitting `clear` off the outermost surface of bvh."""
    zs_ = np.linspace(z0, z1, rows)
    V = []
    for z in zs_:
        for i in range(nu):
            a_ = 360.0 * i / nu
            r_ = float(rp_front(bvh, a_, [z - 0.01, z, z + 0.01], ay=ay).max()) + clear + wobble * math.sin(3.0 * math.radians(a_))
            V.append(np.array([0.0, AX_Y if ay is None else ay, z]) + dir_front(a_) * r_)
    V = np.array(V)
    F = VP.grid_faces(nu, rows, closed_u=True)
    cy_ = AX_Y if ay is None else ay
    F = [f if np.dot(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]), np.array([V[f].mean(0)[0], V[f].mean(0)[1] - cy_, 0.0])) > 0
         else f[::-1] for f in F]
    return V, F


# ---- eyes
for s in "LR":
    V_, F_, R_ = EYE_MESH[s]
    add_part("eye." + s, V_, F_, R_, w="rigid:head")

# ---- boots: foot shell (loft along the foot), sole slab, shaft round the lower shin, folded cuff + gold trim band
BOOT_INFO = {}
BOOT_RING = {}
for s in "LR":
    lo_ = s.lower()
    fm = LEG_B[s] & np.isin(DOMN, ["foot_" + lo_, "ball_" + lo_])
    Pf = BV[fm]
    fd = unit([TOE[s][0] - ANKLE[s][0], TOE[s][1] - ANKLE[s][1], 0.0])
    lat = unit(np.cross([0.0, 0.0, 1.0], fd))
    sp = (Pf - ANKLE[s]) @ fd
    s0, s1 = float(sp.min()), float(sp.max())
    ball_s = float((BALL[s] - ANKLE[s]) @ fd)
    st = np.concatenate([np.linspace(s0 - 0.014, s0, 4)[:-1], np.linspace(s0, s1, 12), np.linspace(s1, s1 + TOE_EXT, 5)[1:]])
    prof = []
    for sk in st:
        sl = Pf[np.abs(sp - float(np.clip(sk, s0 + 0.006, s1 - 0.006))) < 0.012]
        lc = (sl - ANKLE[s]) @ lat
        prof.append((sk, 0.5 * (lc.min() + lc.max()), 0.5 * (lc.max() - lc.min()), float(sl[:, 2].min()), float(sl[:, 2].max())))
    zt_heel = max(p_[4] for p_ in prof if p_[0] <= s0 + 0.04)

    def shell_ring(sk, c_lat, hw, zb, zt, grow=0.0, nring=12):
        f_ = 1.0
        if sk < s0:
            f_ = math.sqrt(max(0.0, 1.0 - ((s0 - sk) / 0.0145) ** 2))
        elif sk > s1:
            f_ = math.sqrt(max(0.0, 1.0 - ((sk - s1) / (TOE_EXT + 0.0005)) ** 2))
        f_ = max(f_, 0.14)
        w_ = (hw + BOOT_MARGIN[0]) * (f_ if sk > s1 else (0.75 + 0.25 * f_)) + grow
        bot = SOLE_T * 0.5
        top = max(zt, zt_heel if sk < s0 + 0.03 else zt) + BOOT_MARGIN[1]
        if sk > ball_s:
            top = bot + (top - bot) * (0.55 + 0.45 * f_) * (1.0 - 0.18 * smoothstep(ball_s, s1 + TOE_EXT, sk))
        top += grow
        cen = ANKLE[s] + fd * sk + lat * c_lat
        ring = []
        for a in np.linspace(0, 2 * math.pi, nring, endpoint=False):
            ca, sa = math.cos(a), math.sin(a)
            zz = 0.5 * (top + bot) + 0.5 * (top - bot) * (sa if sa > 0 else sa ** 3)
            ring.append([cen[0] + lat[0] * w_ * ca, cen[1] + lat[1] * w_ * ca, max(zz, bot - grow)])
        return np.array(ring)
    rings = [shell_ring(*p_) for p_ in prof]
    V_, F_, R_ = VP.loft(rings, "flat", "pole", reg="boot")
    C0b = np.array([BALL[s][0], BALL[s][1], 0.0])
    add_part("bootfoot." + s, V_, F_, R_, w="toe", side=s, fd=fd, c0=C0b)
    sole = []
    for (sk, c_lat, hw, zb, zt) in prof:
        r_ = shell_ring(sk, c_lat, hw, zb, zt)
        w_ = 0.5 * float(np.ptp((r_ - ANKLE[s]) @ lat)) + 0.0035
        cen = ANKLE[s] + fd * sk + lat * c_lat
        sole.append(np.array([[cen[0] + lat[0] * w_ * x, cen[1] + lat[1] * w_ * x, z] for x, z in
                              ((-1.0, 0.0), (1.0, 0.0), (1.0, SOLE_T * 0.85), (-1.0, SOLE_T * 0.85))]))
    V_, F_, R_ = VP.loft(sole, "flat", "flat", reg="boot_sole")
    add_part("sole." + s, V_, F_, R_, w="toe", side=s, fd=fd, c0=C0b)
    sh = SHIN[s]
    Ls = sh["len"]
    ts_ = np.linspace(BOOT_TOP, 0.965, 6)
    clr = lambda t, th: float(np.interp(t, [BOOT_TOP, 0.965], [BOOT_CLEAR[1], BOOT_CLEAR[0]]))
    Vg, Fg, fr_ = limb_grid(KNEE[s], ANKLE[s], ts_, clr, LIMB_BVH["leg" + s], 16, front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.004, 0.0, "boot", "boot", "boot")
    add_part("bootshaft." + s, V_, F_, R_, w="transfer")
    tc = np.linspace(BOOT_TOP - 0.004, BOOT_TOP + CUFF[0] / Ls, 4)
    clr_c = lambda t, th: BOOT_CLEAR[1] + 0.004 + CUFF[1] * 0.3 + CUFF[2] * ((t - tc[0]) / (tc[-1] - tc[0])) ** 1.5
    Vg, Fg, _ = limb_grid(KNEE[s], ANKLE[s], tc, clr_c, LIMB_BVH["leg" + s], 16, front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, CUFF[3], 0.0, "boot_cuff", "boot", "boot_cuff")
    add_part("bootcuff." + s, V_, F_, R_, w="transfer")
    tb_ = np.linspace(BOOT_TOP - 0.006, BOOT_TOP - 0.006 + BOOT_TRIM / Ls, 2)
    clr_t = lambda t, th: BOOT_CLEAR[1] + 0.004 + CUFF[1] * 0.3 + CUFF[3] + 0.0012
    Vg, Fg, _ = limb_grid(KNEE[s], ANKLE[s], tb_, clr_t, LIMB_BVH["leg" + s], 16, front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.0018, 0.0, "boot_trim", "boot_trim", "boot_trim")
    add_part("boottrim." + s, V_, F_, R_, w="transfer")
    BOOT_RING[s] = {"fd": fd, "lat": lat, "s0": s0, "s1": s1, "ball_s": ball_s}
    BOOT_INFO[s] = {"foot_len": round(s1 - s0, 4), "shaft_top_z": round(float((KNEE[s] + (ANKLE[s] - KNEE[s]) * BOOT_TOP)[2]), 4)}

# ---- the cream robe skirt below the belt (Wren's tunic-tail generator, all robe)
BVH_LOWER = BVHTree.FromPolygons(BV.tolist(), TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith("thigh")])
_cols = np.linspace(-180.0, 180.0, ROBE_NU + 1)[:-1]
_zsk = np.linspace(Z_BELT + 0.02, Z_BELT - max(ROBE_LEN) - 0.02, 40)
_skp = {a_: np.maximum.accumulate(radial_profile(BVH_LOWER, 0.0, AX_Y, 180.0 + a_, _zsk)) for a_ in _cols}
Vsk, vrow_sk = [], []
for j in range(ROBE_NV):
    v = j / (ROBE_NV - 1)
    for a_ in _cols:
        back = 0.5 - 0.5 * math.cos(math.radians(a_))
        ln = ROBE_LEN[0] + (ROBE_LEN[1] - ROBE_LEN[0]) * back + 0.008 * math.sin(math.radians(a_) * 5.0 + 0.6) * v
        z = Z_BELT + 0.012 - (ln + 0.012) * v
        r = float(np.interp(-z, -_zsk, _skp[a_])) + ROBE_CLEAR + ROBE_FLARE * v ** 1.3
        ph = math.radians(180.0 + a_)
        Vsk.append([r * math.sin(ph), AX_Y + r * math.cos(ph), z]); vrow_sk.append(v)
Vsk = np.array(Vsk); nsk = len(_cols)
Fsk = VP.grid_faces(nsk, ROBE_NV, closed_u=True)
Fsk = [f if np.dot(np.cross(Vsk[f[1]] - Vsk[f[0]], Vsk[f[2]] - Vsk[f[0]]), np.array([Vsk[f].mean(0)[0], Vsk[f].mean(0)[1] - AX_Y, 0.0])) > 0
       else f[::-1] for f in Fsk]
V_, F_, R_ = VP.solidify(Vsk, Fsk, ROBE_T, 0.0, "robe", "robe_shade", "robe")
add_part("robeskirt", V_, F_, R_, w="fauld", v_param=np.tile(np.array(vrow_sk), 2))
ROBE_INFO = {"hem_z_front": round(Z_BELT - ROBE_LEN[0], 4), "hem_z_back": round(Z_BELT - ROBE_LEN[1], 4)}

# ---- the wide belt (a band over the robe top), the gold buckle, pouches, scroll tubes tucked in at his right hip
BVH_BELTP = comb_bvh({"robeskirt"})
_bz0, _bz1 = Z_BELT - 0.5 * BELT["h"], Z_BELT + 0.5 * BELT["h"]
Vb_, Fb_ = ring_band(BVH_BELTP, _bz0, _bz1, BELT["clear"], nu=44, rows=3)
V_, F_, R_ = VP.solidify(Vb_, Fb_, BELT["t"], 0.0, "belt", "belt", "belt_edge")
add_part("belt", V_, F_, R_, w="transfer")
BVH_BELT = comb_bvh({"robeskirt", "belt"})
_bf = BVH_BELT.ray_cast(Vector((0.0, -0.8, Z_BELT)), Vector((0.0, 1.0, 0.0)), 1.5)
BUCKLE_C = np.array(_bf[0]) + np.array([0.0, -0.002 - BUCKLE[2] * 0.5, 0.0])
V_, F_, R_ = VP.rounded_box(BUCKLE_C, (1, 0, 0), (0, -1, 0), (0, 0, 1), BUCKLE[0], BUCKLE[2] * 0.5, BUCKLE[1], nr=2, rows=3,
                            bulge=0.04, region="brass")
add_part("buckle", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.rounded_box(BUCKLE_C + np.array([0.0, -BUCKLE[2] * 0.5, 0.0]), (1, 0, 0), (0, -1, 0), (0, 0, 1), BUCKLE[0] * 0.55,
                            0.0012, BUCKLE[1] * 0.55, nr=1, rows=2, bulge=0.0, region="belt")
add_part("buckleinset", V_, F_, R_, w="rigid_transfer")
for k, (a_, (hx_, hy_, hz_)) in enumerate(POUCHES):
    _pd = dir_front(a_)
    _pz = Z_BELT - 0.010 - hz_ * 0.55
    _pr = float(rp_front(BVH_BELT, a_, np.linspace(_pz - hz_, _pz + hz_, 9)).max())
    C_ = np.array([0.0, AX_Y, _pz]) + _pd * (_pr + hy_ + 0.002)
    _pt = np.cross([0.0, 0.0, 1.0], _pd)
    V_, F_, R_ = VP.rounded_box(C_, _pt, _pd, [0.0, 0.0, 1.0], hx_, hy_, hz_, nr=3, rows=6, bulge=0.12, region="pouch")
    add_part("pouch.%d" % k, V_, F_, R_, w="rigid_transfer")
    fc_ = C_ + _pd * (hy_ * 0.95) + np.array([0.0, 0.0, hz_ * 0.40])
    V_, F_, R_ = VP.rounded_box(fc_, _pt, _pd, [0.0, 0.0, 1.0], hx_ * 1.05, 0.0032, hz_ * 0.58, nr=2, rows=3, bulge=0.04,
                                region="pouch_flap")
    add_part("pouchflap.%d" % k, V_, F_, R_, w="rigid_transfer")
    V_, F_, R_ = VP.gem(fc_ + _pd * 0.0038 - np.array([0.0, 0.0, hz_ * 0.42]), _pd, 0.0050, 0.0028, n=8, region="brass")
    add_part("pouchbutton.%d" % k, V_, F_, R_, w="rigid_transfer")
SCROLL_INFO = []
for k, (a_, tilt_, ln_, r_) in enumerate(SCROLLS):
    _pd = dir_front(a_)
    _pr = float(rp_front(BVH_BELT, a_, [_bz0, Z_BELT, _bz1]).max())
    _pt = np.cross([0.0, 0.0, 1.0], _pd)
    ax_ = unit(np.array([0.0, 0.0, 1.0]) * math.cos(math.radians(tilt_)) + _pt * math.sin(math.radians(tilt_)))
    C_ = np.array([0.0, AX_Y, Z_BELT + 0.25 * ln_ - 0.05]) + _pd * (_pr + r_ * 0.55)
    hl_ = 0.5 * ln_
    prof_ = [(0.0, -hl_ - 0.003), (r_ * 1.12, -hl_ - 0.003), (r_ * 1.12, -hl_ + 0.016), (r_, -hl_ + 0.018), (r_, hl_ - 0.018),
             (r_ * 1.12, hl_ - 0.016), (r_ * 1.12, hl_ + 0.003), (0.0, hl_ + 0.003)]
    V_, F_, R_ = VP.lathe(prof_, ["scroll_cap", "scroll_cap", "scroll_cap", "scroll_tube", "scroll_cap", "scroll_cap", "scroll_cap"],
                          10, C_, ax_, up_hint=_pd)
    add_part("scrolltube.%d" % k, V_, F_, R_, w="rigid_transfer")
    for zz_ in (-0.35, 0.35):
        V_, F_, R_ = VP.torus(r_ * 1.03, 0.0016, 8, 3, C_ + ax_ * hl_ * zz_, ax_, up_hint=_pd, region="brass")
        add_part("scrollring.%d%s" % (k, "ab"[zz_ > 0]), V_, F_, R_, w="rigid_transfer")
    SCROLL_INFO.append({"centre": C_.round(4).tolist(), "len": ln_})

# ---- the big rolled robe sleeves
for s in "LR":
    fa = WRI[s] - ELB[s]
    c0_ = ELB[s] + fa * SLEEVE_T
    a_ = unit(fa)
    h_ = SLEEVE_ROLL[0]
    A_ = c0_ - a_ * h_ * 0.62; B_ = c0_ + a_ * h_ * 0.38
    clr = lambda t, th: SLEEVE_ROLL[2] + SLEEVE_ROLL[1] * math.sin(math.pi * t) ** 0.7 * (1.0 + 0.10 * math.sin(4 * th + 0.5))
    Vg, Fg, _ = limb_grid(A_, B_, np.linspace(0.0, 1.0, 6), clr, LIMB_BVH["arm" + s], 16)
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.0025, 0.0, "robe_roll", "robe_shade", "robe_roll")
    add_part("sleeveroll." + s, V_, F_, R_, w="transfer")

# ---- the dark bracers on both forearms (gold rims at both ends)
BRACER_INFO = {}
for s in "LR":
    BR = BRACER
    fa = ELB[s] - WRI[s]
    A_ = WRI[s] + fa * BR["t"][0]; B_ = WRI[s] + fa * BR["t"][1]
    clr = lambda t, th: BR["clear"] + 0.0022 * math.sin(math.pi * t)
    Vg, Fg, _ = limb_grid(A_, B_, np.linspace(0.0, 1.0, BR["nv"]), clr, LIMB_BVH["arm" + s], BR["nu"])
    V_, F_, R_ = VP.solidify(Vg, Fg, BR["t_leather"], 0.0, "bracer", "bracer", "bracer")
    add_part("bracer." + s, V_, F_, R_, w="transfer")
    Lfa = float(np.linalg.norm(B_ - A_))
    for k, t0_ in enumerate((0.0, 1.0 - BR["trim_w"] / Lfa)):
        clr_r = lambda t, th: BR["clear"] + BR["t_leather"] + 0.0008 + 0.0022 * math.sin(math.pi * min(max(t0_, 0.0), 1.0))
        Vg, Fg, _ = limb_grid(A_, B_, np.array([t0_, t0_ + BR["trim_w"] / Lfa]), clr_r, LIMB_BVH["arm" + s], BR["nu"])
        V_, F_, R_ = VP.solidify(Vg, Fg, 0.0014, 0.0, "brass", "brass", "brass")
        add_part("bracertrim.%s%d" % (s, k), V_, F_, R_, w="transfer")
    BRACER_INFO[s] = {"span_m": round(Lfa, 4)}

# ---- the blue cravat: a flattened band round the neck, the knot at the throat, the puffed fall into the robe's V
CR = CRAVAT
Z_CRAV = NECK0[2] + CRAVAT["z"]
_npts = []
for i in range(28):
    a_ = 360.0 * i / 28
    r_ = float(rp_front(BVH_BODY, a_, [Z_CRAV - 0.008, Z_CRAV, Z_CRAV + 0.008], ay=NECK0[1]).max())
    zz_ = Z_CRAV - 0.012 * max(0.0, math.cos(math.radians(a_))) ** 2       # dips at the front toward the knot
    _npts.append(np.array([0.0, NECK0[1], zz_]) + dir_front(a_) * (r_ + CR["band_r"] * 0.9))
V_, F_, R_, _ = VP.tube_path(np.array(_npts), CR["band_r"], 6, "cravat", closed=True, su=CR["band_flat"],
                             up_hint=(0.0, 0.0, 1.0))
add_part("cravatband", V_, F_, R_, w="collar")
_kh = BVH_BODY.ray_cast(Vector((0.0, -0.8, Z_CRAV - 0.016)), Vector((0.0, 1.0, 0.0)), 1.5)
KNOT_C = np.array(_kh[0]) + np.array([0.0, -CR["knot"][1] - 0.006, 0.0])
V_, F_, R_ = VP.rounded_box(KNOT_C, (1, 0, 0), (0, -1, 0), (0, 0, 1), *CR["knot"], nr=2, rows=5, bulge=0.25, region="cravat")
add_part("cravatknot", V_, F_, R_, w="collar")
_fz = KNOT_C[2] - CR["fall_drop"]
_fh = BVH_BODY.ray_cast(Vector((0.0, -0.8, _fz)), Vector((0.0, 1.0, 0.0)), 1.5)
FALL_C = np.array([0.0, min(float(_fh[0][1]) - CR["fall"][1] - 0.005, KNOT_C[1] + 0.004), _fz])
_fax = unit(KNOT_C - FALL_C)
V_, F_, R_ = VP.rounded_box(FALL_C, (1, 0, 0), unit(np.cross([1.0, 0.0, 0.0], _fax)), _fax, CR["fall"][0], CR["fall"][1],
                            CR["fall"][2], nr=2, rows=6, bulge=0.30, region="cravat_shade")
add_part("cravatfall", V_, F_, R_, w="collar")
print("OUTFIT", json.dumps({"boots": BOOT_INFO, "robe": ROBE_INFO, "scrolls": SCROLL_INFO, "bracers": BRACER_INFO,
                            "open_edges": {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}}))
