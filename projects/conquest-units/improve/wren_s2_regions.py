# Wren build section 2: painted regions on the body by iso-cut fields (vampwarrior's machinery): rolled-sleeve line,
# shirt V-neck, vest opening + armholes, sash line (trousers below), sock wraps, boot top; the face (lid
# liner contour, bold brows, subtle lips + mouth line), the authored shadow shapes (the chin's cast shadow on the neck, the
# fringe's zigzag shadow on the forehead), the scalp hairline.

Z_SASH = Z_WAIST
FIELDS = {}
X, Y, Z = BV[:, 0], BV[:, 1], BV[:, 2]
SHIN = {}
for s, sg in (("L", 1.0), ("R", -1.0)):
    la = ELB[s] - SHO[s]; fa = WRI[s] - ELB[s]
    FIELDS["sleeve_" + s] = (BV - ELB[s]) @ unit(fa) - SLEEVE_T * np.linalg.norm(fa)     # > 0 = below the rolled sleeve
    an = unit([sg, 0.0, ARMHOLE_TILT])
    FIELDS["arm_" + s] = (BV - (SHO[s] - an * ARMHOLE_IN)) @ an
    FIELDS["legm_" + s] = LEG_B[s].astype(float)
    FIELDS["armm_" + s] = ARM_B[s].astype(float)
    FIELDS["side_" + s] = (sg * X > 0).astype(float)
    # the shin frame: t along knee -> ankle, theta round the shin from the front centre (+ toward his left)
    sa = ANKLE[s] - KNEE[s]
    Ls = float(np.linalg.norm(sa)); ua = sa / Ls
    fr = unit(np.array([0.0, -1.0, 0.0]) - ua * float(ua @ np.array([0.0, -1.0, 0.0])))
    lat = np.cross(ua, fr)
    lat = lat if lat[0] > 0 else -lat
    tt = ((BV - KNEE[s]) @ ua) / Ls
    rel = BV - KNEE[s] - np.outer((BV - KNEE[s]) @ ua, ua)
    th = np.arctan2(rel @ lat, rel @ fr)
    SHIN[s] = {"ua": ua, "fr": fr, "lat": lat, "len": Ls}
    FIELDS["shin_" + s] = tt
    FIELDS["wraptop_" + s] = tt - WRAP_T[0]
    FIELDS["boottop_" + s] = tt - WRAP_T[1]


FIELDS["neck"] = Z - np.where(Y < NECK0[1] + 0.02,
                              np.minimum(NECK0[2] - NECK_DZ + 0.3 * (Y - NECK0[1]), NECK0[2] - VNECK[0] + VNECK[1] * np.abs(X)),
                              NECK0[2] - NECK_DZ + 0.3 * (Y - NECK0[1]))
FIELDS["headm"] = HEAD_B.astype(float)
FIELDS["sash"] = Z - Z_SASH
FIELDS["vopen"] = np.abs(X) - np.interp(Z, [Z_SASH, NECK0[2]], [VEST_X[1], VEST_X[0]])
FIELDS["z"] = Z.copy()
FIELDS["y"] = Y.copy()
if LIP is not None:                                   # (v4: None = no lip tint -- the 2D mouth is the line alone)
    FIELDS["lip"] = (X / LIP[0]) ** 2 + ((Z - Z_LIP) / np.where(Z > Z_LIP, LIP[1], LIP[2])) ** 2 - 1.0
if SEAM is not None:
    # v2 mouth line: a thin painted line ON the sealed seam (the sheet's closed mouth), tapering to the corners.
    # v4: the drawn SMIRK line -- painted over the middle MOUTH_LEN of the seam only (the anime mouth is shorter than the
    # modelled one), its corner on his MOUTH_SMIRK side lifted by MOUTH_SMIRK[0] (rising over the outer MOUTH_SMIRK[2] of
    # that half), the other corner level
    _sx0, _sx1 = float(SEAM["xs"].min()), float(SEAM["xs"].max())
    if MOUTH_LEN is not None:
        _sx0, _sx1 = max(_sx0, -MOUTH_LEN), min(_sx1, MOUTH_LEN)
    else:                                          # v6: the line runs MOUTH_LINE_EXT past each end of the seam (over the
        _sx0, _sx1 = _sx0 - MOUTH_LINE_EXT, _sx1 + MOUTH_LINE_EXT   # corner folds: no seam end is left unpainted)


    def mline_centre(x_):
        zc_ = np.interp(x_, SEAM["xs"], SEAM["zc"])
        if MOUTH_SMIRK is not None and MOUTH_SMIRK_GEO is None:   # (v6: the seam carries the smirk itself)
            h_ = MOUTH_SMIRK[1] * x_ / max(_sx1 if MOUTH_SMIRK[1] > 0 else -_sx0, 1e-6)       # 0 at the centre .. 1 at the corner
            zc_ = zc_ + MOUTH_SMIRK[0] * smoothstep(1.0 - MOUTH_SMIRK[2], 1.0, h_) ** 1.5
        return zc_


    def mline_field(P_):
        u_ = np.clip((P_[:, 0] - _sx0) / max(_sx1 - _sx0, 1e-6), 0.0, 1.0)
        hw_ = 0.5 * MOUTH_LINE[0] * (MOUTH_LINE[1] + (1.0 - MOUTH_LINE[1]) * np.sin(np.pi * u_) ** 0.6)
        return np.where((P_[:, 0] >= _sx0) & (P_[:, 0] <= _sx1), np.abs(P_[:, 2] - mline_centre(P_[:, 0])) - hw_, 1.0)


    FIELDS["mline"] = mline_field(BV)
# ---- eyes: the lathe eyeball (rendered) + the lid-edge aperture per 5 deg (front rays: first skin hit in front of it)
BN = VP.vertex_normals(BV, BF)
if float(np.mean(np.einsum("ij,ij->i", BN, BV - BV.mean(0)))) < 0:
    BN = -BN
EYE_MESH, EYE_AP = {}, {}
AP_STEP, AP_N = 0.0001, 72
EYE_PAINT = {}
for s in "LR":
    e = EYE[s]
    _im = 0.5 * (EYE_PUPIL_DEG + 4 + EYE_IRIS_DEG)
    angs = [0.0, EYE_PUPIL_DEG, EYE_PUPIL_DEG + 4, _im, EYE_IRIS_DEG, max(EYE_IRIS_DEG + 10.0, 60.0), 90.0] +         ([125.0, 155.0, 180.0] if EYE_BACK is None else [])
    if EYE_HILITE is not None:
        # v3: extra rings every EYE_HILITE_RING deg through the highlight dot's polar band, so the dot's cut resolves round
        _hc = EYE_HILITE[1] * EYE_IRIS_DEG
        angs += [a_ for a_ in np.arange(max(_hc - EYE_HILITE[2] - 1.0, 1.0), _hc + EYE_HILITE[2] + 1.0, EYE_HILITE_RING)
                 if min(abs(a_ - b_) for b_ in angs) > 0.6]
        angs = sorted(float(a_) for a_ in angs)
    prof = [(e["r"] * math.sin(math.radians(a)), e["r"] * math.cos(math.radians(a))) for a in angs]
    if EYE_BACK is not None:
        # v3: the hidden back of the ball (behind the socket, never visible) closes as a cone to a pole on the axis
        prof.append((0.0, e["r"] * math.cos(math.radians(EYE_BACK))))
        angs = angs + [180.0]
    regs = [("eye_pupil" if a1_ <= EYE_PUPIL_DEG + 1e-6 else ("eye_iris" if a1_ <= EYE_IRIS_DEG + 1e-6 else "eye_sclera"))
            for a1_ in angs[1:]]
    Ve_, Fe_, Re_ = VP.lathe(prof, regs, EYE_SEG, e["c"], (0.0, -1.0, 0.0), up_hint=(0, 0, 1))
    _ax = np.array([0.0, -1.0, 0.0])
    _ang = lambda P_, c_=e["c"]: np.degrees(np.arccos(np.clip((P_ - c_) @ _ax / np.maximum(np.linalg.norm(P_ - c_, axis=1), 1e-12), -1, 1)))
    _ncut = 0
    if IRIS_SHADE is not None:
        # v3 iris lid shadow: the iris above IRIS_SHADE x its radius (over the iris centre) = the darker tone
        _zc = e["c"][2] + IRIS_SHADE * e["r"] * math.sin(math.radians(EYE_IRIS_DEG))
        Ve_, Fe_, Re_, _, n_ = cut_part(Ve_, Fe_, Re_, lambda P_: P_[:, 2] - _zc, 0.0,
                                        vgate=_ang(Ve_) <= EYE_IRIS_DEG + 0.01)
        _ncut += n_
        _fc = np.array([Ve_[f].mean(0) for f in Fe_])
        Re_ = [("eye_iris_dark" if (r_ in ("eye_iris",) and fc_[2] > _zc) else r_) for r_, fc_ in zip(Re_, _fc)]
    if EYE_HILITE is not None:
        # v3 highlight dot: a small disc on the ball, up-and-OUTER from the iris centre (paint-only: the faces stay on the sphere)
        _sg = 1.0 if s == "L" else -1.0
        _ph, _al = math.radians(EYE_HILITE[0]), math.radians(EYE_HILITE[1] * EYE_IRIS_DEG)
        _hd = math.cos(_al) * _ax + math.sin(_al) * np.array([_sg * math.cos(_ph), 0.0, math.sin(_ph)])
        _hf = lambda P_, c_=e["c"], hd_=_hd: np.degrees(np.arccos(np.clip(((P_ - c_) / np.maximum(np.linalg.norm(P_ - c_, axis=1), 1e-12)[:, None]) @ hd_, -1, 1))) - EYE_HILITE[2]
        Ve_, Fe_, Re_, _, n_ = cut_part(Ve_, Fe_, Re_, _hf, 0.0)
        _ncut += n_
        _fc = np.array([Ve_[f].mean(0) for f in Fe_])
        _hv = _hf(_fc)
        Re_ = [("eye_hilite" if hv_ < 0 else r_) for r_, hv_ in zip(Re_, _hv)]
    EYE_MESH[s] = (Ve_, Fe_, Re_)
    EYE_PAINT[s] = {"faces": {r_: Re_.count(r_) for r_ in sorted(set(Re_))}, "edge_splits": _ncut, "tris": tri_count_F(Fe_)}


def eye_polar(P, s):
    c_ = EYE[s]["c"]
    d_ = P[:, [0, 2]] - c_[[0, 2]]
    rad_ = np.hypot(d_[:, 0], d_[:, 1])
    ang_ = np.degrees(np.arctan2(d_[:, 1], d_[:, 0] * (1.0 if s == "L" else -1.0))) % 360.0
    return rad_, ang_


def front_first_hit(s, bvh_skin, bvh_eye, x, z):
    hs = bvh_skin.ray_cast(Vector((x, -1.0, z)), Vector((0.0, 1.0, 0.0)), 2.0)
    he = bvh_eye.ray_cast(Vector((x, -1.0, z)), Vector((0.0, 1.0, 0.0)), 2.0)
    if hs[0] is None and he[0] is None:
        return None, hs
    if he[0] is None or (hs[0] is not None and hs[3] < he[3] - 1e-6):
        return "skin", hs
    return "eye", he


BVH_EYE = {s: BVHTree.FromPolygons(EYE_MESH[s][0].tolist(), EYE_MESH[s][1]) for s in "LR"}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    ap_ = np.zeros(AP_N)
    for k in range(AP_N):
        th_ = math.radians(360.0 * k / AP_N)
        for i in range(1, 300):
            rho = i * AP_STEP
            who, _ = front_first_hit(s, BVH_BODY, BVH_EYE[s], c_[0] + sg_ * rho * math.cos(th_), c_[2] + rho * math.sin(th_))
            if who == "skin":
                ap_[k] = rho
                break
    EYE_AP[s] = ap_


def aperture(ang_, s):
    return np.interp(ang_, 360.0 * np.arange(AP_N) / AP_N, EYE_AP[s], period=360.0)


# v3 EYEBALL TUCK: the lathe ball (radius = the MPFB eye helper's mean radius) stood through the lower lid / outer orbit
# skin by up to 1.45 mm already in v2 (9 vertices, measured with the v2 eye configuration) and more once scaled. Every
# eyeball vertex OUTSIDE the opening's front projection (polar radius > aperture + EYE_TUCK[0]) whose ray from the ball's
# centre meets the skin before it is pulled back along that ray to EYE_TUCK[1] inside the skin. The visible cap (inside
# the opening) is untouched, so the aperture and the painted iris are unchanged (re-measured below).
EYE_TUCK = (0.001, 0.0005)
EYE_TUCK_INFO = {}
for s in "LR":
    c_ = EYE[s]["c"]
    Ve_ = EYE_MESH[s][0].copy()
    rad_, ang_ = eye_polar(Ve_, s)
    moved_ = []
    for i, p_ in enumerate(Ve_):
        if rad_[i] <= aperture(ang_[i], s) + EYE_TUCK[0]:
            continue
        L_ = float(np.linalg.norm(p_ - c_))
        d_ = (p_ - c_) / max(L_, 1e-12)
        h_ = BVH_BODY.ray_cast(Vector(c_), Vector(d_), L_ + EYE_TUCK[1])
        # (only the OUTER skin -- facing away from the ball's centre -- counts: through the socket sleeve, whose normals
        # face the ball, lies the closed head interior)
        if h_[0] is not None and h_[3] < L_ + EYE_TUCK[1] and float(np.array(h_[1]) @ d_) > 0.0:
            Ve_[i] = c_ + d_ * max(h_[3] - EYE_TUCK[1], 0.3 * L_)
            moved_.append(L_ - float(np.linalg.norm(Ve_[i] - c_)))
    EYE_MESH[s] = (Ve_, EYE_MESH[s][1], EYE_MESH[s][2])
    EYE_TUCK_INFO[s] = {"verts": len(moved_), "max_pull_mm": round(1000 * max(moved_), 2) if moved_ else 0.0}
BVH_EYE = {s: BVHTree.FromPolygons(EYE_MESH[s][0].tolist(), EYE_MESH[s][1]) for s in "LR"}
_ap_before = {s: EYE_AP[s].copy() for s in "LR"}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    for k in range(AP_N):
        th_ = math.radians(360.0 * k / AP_N)
        for i in range(1, 300):
            rho = i * AP_STEP
            who, _ = front_first_hit(s, BVH_BODY, BVH_EYE[s], c_[0] + sg_ * rho * math.cos(th_), c_[2] + rho * math.sin(th_))
            if who == "skin":
                EYE_AP[s][k] = rho
                break
    EYE_TUCK_INFO[s]["aperture_change_mm_max"] = round(1000 * float(np.abs(EYE_AP[s] - _ap_before[s]).max()), 2)
report["eye_tuck"] = EYE_TUCK_INFO
print("EYETUCK", json.dumps(EYE_TUCK_INFO))


# v3 eye proofs. IRIS COVERAGE: front rays on a 0.25 mm grid over each eye; of the rays whose first hit is the eyeball
# (the visible opening), the share landing on the iris family (iris / lid-shadow iris / pupil / highlight). EYEBALL POKE:
# eyeball vertices standing > 0.3 mm outside the skin (nearest skin point, its face normal) OUTSIDE the opening's front
# projection (polar radius beyond the aperture + 1 mm) = the enlarged ball showing through the face somewhere else.
_IRIS_FAM = ("eye_iris", "eye_iris_dark", "eye_pupil", "eye_hilite")
EYE_PROOF = {}
for s in "LR":
    c_ = EYE[s]["c"]
    Re_ = EYE_MESH[s][2]
    n_eye, n_iris, n_hil = 0, 0, 0
    for x_ in np.arange(c_[0] - 0.026, c_[0] + 0.026, 0.00025):
        for z_ in np.arange(c_[2] - 0.016, c_[2] + 0.016, 0.00025):
            who, h_ = front_first_hit(s, BVH_BODY, BVH_EYE[s], float(x_), float(z_))
            if who == "eye":
                n_eye += 1
                n_iris += Re_[h_[2]] in _IRIS_FAM
                n_hil += Re_[h_[2]] == "eye_hilite"
    Ve_ = EYE_MESH[s][0]
    rad_, ang_ = eye_polar(Ve_, s)
    poke_, poke_at = 0, []
    for i, p_ in enumerate(Ve_):
        q_, n_, _, _ = BVH_BODY.find_nearest(Vector(p_))
        if q_ is not None and float((p_ - np.array(q_)) @ np.array(n_)) > 0.0003 and rad_[i] > aperture(ang_[i], s) + 0.001 \
                and float((np.array(q_) - c_) @ np.array(n_)) > 0.0:
            poke_ += 1
            poke_at.append([round(1000 * float(x), 1) for x in (p_ - c_)] + [round(1000 * float((p_ - np.array(q_)) @ np.array(n_)), 2)])
    EYE_PROOF[s] = {"visible_opening_mm2": round(n_eye * 0.0625, 1), "iris_coverage_pct": round(100.0 * n_iris / max(n_eye, 1), 1),
                    "highlight_visible_mm2": round(n_hil * 0.0625, 2), "eyeball_poke_verts": poke_,
                    "poke_at_mm_dxyz_out": poke_at[:12], "paint": EYE_PAINT[s]}
report["eye_proof"] = {"rule": "front rays on a 0.25 mm grid: iris coverage = iris-family hits / eyeball hits (the visible "
                                "opening); poke = eyeball vertices > 0.3 mm outside the OUTER skin (the nearest skin point "
                                "faces away from the ball's centre) beyond the opening's projection",
                       "iris_deg": EYE_IRIS_DEG, "pupil_deg": EYE_PUPIL_DEG, **EYE_PROOF}
print("EYEPROOF", json.dumps(report["eye_proof"]))


def liner_w(ang_):
    ang_ = np.asarray(ang_, float)
    up_ = smoothstep(-0.25, 0.25, np.sin(np.radians(ang_)))
    if LASH_PROFILE is not None:
        # v3 upper LASH band: the width profile round the upper lid (bold over the outer two thirds, tapering to the inner
        # corner) + the wing flick past the outer corner; the lower lid keeps the thin v2 line
        a_ = np.where(np.sin(np.radians(ang_)) >= 0, ang_ % 360.0, np.where(np.cos(np.radians(ang_)) >= 0, 0.0, 180.0))
        wu_ = 1e-3 * np.interp(a_, [p[0] for p in LASH_PROFILE], [p[1] for p in LASH_PROFILE])
        dw_ = np.minimum(np.abs(ang_ - LASH_WING[2]) % 360.0, 360.0 - np.abs(ang_ - LASH_WING[2]) % 360.0)
        return LINER_W[1] + (wu_ - LINER_W[1]) * up_ + 1e-3 * LASH_WING[0] * np.exp(-(dw_ / LASH_WING[1]) ** 2)
    dw_ = np.minimum(np.abs(ang_ - LINER_WING[2]) % 360.0, 360.0 - np.abs(ang_ - LINER_WING[2]) % 360.0)
    return LINER_W[1] + (LINER_W[0] - LINER_W[1]) * up_ + LINER_WING[0] * np.exp(-(dw_ / LINER_WING[1]) ** 2)


for s in "LR":
    rad_, ang_ = eye_polar(BV, s)
    gate_ = (np.linalg.norm(BV - EYE[s]["c"], axis=1) < EYE[s]["r"] + 0.012) & (Y < EYE[s]["c"][1] + 0.002) & HEAD_B
    FIELDS["eye_" + s] = np.where(gate_, rad_ - aperture(ang_, s) - liner_w(ang_), 1.0)
BROW_CURVE = {}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    half_w = 0.5 * (aperture(0.0, s) + aperture(180.0, s))
    top = float(aperture(90.0, s))
    pts = np.array([[c_[0] + sg_ * px * half_w, 0.0, c_[2] + top + pz] for px, pz in BROW_PTS])
    BROW_CURVE[s] = VP.resample(VP.catmull(pts, 16), 80)[0]


def brow_field(P, s):
    cur = BROW_CURVE[s][:, [0, 2]]
    q = P[:, [0, 2]]
    best = np.full(len(P), 1e9); tb = np.zeros(len(P))
    for i in range(len(cur) - 1):
        a_, b_ = cur[i], cur[i + 1]
        ab = b_ - a_
        t_ = np.clip(((q - a_) @ ab) / float(ab @ ab), 0, 1)
        d_ = np.linalg.norm(q - (a_ + t_[:, None] * ab), axis=1)
        m_ = d_ < best
        best[m_] = d_[m_]; tb[m_] = (i + t_[m_]) / (len(cur) - 1)
    hw = 0.5 * (BROW_W[0] + (BROW_W[1] - BROW_W[0]) * tb ** BROW_TAPER)
    return best - hw


for s in "LR":
    gate_ = HEAD_B & (Y < EYE[s]["c"][1] + 0.03) & (np.abs(X - EYE[s]["c"][0]) < 0.04) & (Z > EYE[s]["c"][2]) & \
        (Z < EYE[s]["c"][2] + 0.035) & (BN[:, 1] < -0.05)
    FIELDS["brow_" + s] = np.where(gate_, brow_field(BV, s), 1.0)
# ---- authored shadow (1): the chin's cast shadow on the neck under a stylised key light
_el = math.radians(JAW_LIGHT_DEG)
L_JAW = np.array([0.0, -math.cos(_el), math.sin(_el)])
BVH_HEADONLY = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n == "head"])
_jg = (Z < Z_CHIN + JAW_GATE[0] + JAW_GATE[1] * np.maximum(np.abs(X) - JAW_GATE[2], 0.0)) & (Z > NECK0[2] - 0.06) & \
    (Y < NECK0[1] + 0.02) & ~(ARM_B["L"] | ARM_B["R"])
_cast = np.zeros(len(BV))
for i in np.nonzero(_jg)[0]:
    if BVH_HEADONLY.ray_cast(Vector(BV[i] + BN[i] * 0.001), Vector(L_JAW), 0.3)[0] is not None:
        _cast[i] = 1.0
_bedges = set()
for f in BF:
    for k in range(len(f)):
        a_, b_ = f[k], f[(k + 1) % len(f)]
        _bedges.add((min(a_, b_), max(a_, b_)))
_bedges = np.array(sorted(_bedges))
_bdeg = np.bincount(_bedges.ravel(), minlength=len(BV)).astype(float)
for _ in range(JAW_SMOOTH):
    acc_ = np.zeros(len(BV))
    np.add.at(acc_, _bedges[:, 0], _cast[_bedges[:, 1]]); np.add.at(acc_, _bedges[:, 1], _cast[_bedges[:, 0]])
    _cast = 0.5 * _cast + 0.5 * acc_ / np.maximum(_bdeg, 1.0)
FIELDS["jawsh"] = np.where(_jg, 0.5 - _cast, 1.0)
FIELDS["jawgate"] = _jg.astype(float)
# ---- authored shadow (2): the fringe's shadow on the forehead -- a band BELOW the fringe's zigzag edge (the tips of
# FRINGE_TIPS, notches FRINGE_NOTCH above the higher neighbour), front projection; the band is deeper under the notches
_ez = float(EYE["L"]["c"][2])
_tips = sorted(FRINGE_TIPS)
_edge = [(_tips[0][0] - 18.0, _tips[0][1] + 34.0)]
for i, (x_, z_) in enumerate(_tips):
    _edge.append((x_, z_))
    if i + 1 < len(_tips):
        _edge.append((0.5 * (x_ + _tips[i + 1][0]), max(z_, _tips[i + 1][1]) + FRINGE_NOTCH))
_edge.append((_tips[-1][0] + 18.0, _tips[-1][1] + 34.0))
FRINGE_EDGE = np.array([[x_ * 0.001, _ez + z_ * 0.001] for x_, z_ in _edge])
_etip = np.array([1.0 if i % 2 == 1 else 0.0 for i in range(len(_edge))])   # 1 at a tip, 0 at a notch / end


def fringe_edge_field(P):
    """signed front-projected distance to the fringe edge (> 0 BELOW it, the face side) and the tip-ness there."""
    q_ = P[:, [0, 2]]
    best = np.full(len(P), 1e9); sgn = np.ones(len(P)); tipk = np.zeros(len(P))
    for i in range(len(FRINGE_EDGE) - 1):
        a_, b_ = FRINGE_EDGE[i], FRINGE_EDGE[i + 1]
        ab = b_ - a_
        t_ = np.clip(((q_ - a_) @ ab) / float(ab @ ab), 0, 1)
        v_ = q_ - (a_ + t_[:, None] * ab)
        d_ = np.linalg.norm(v_, axis=1)
        m_ = d_ < best
        cr = ab[0] * v_[:, 1] - ab[1] * v_[:, 0]
        best[m_] = d_[m_]; sgn[m_] = np.where(cr[m_] < 0, 1.0, -1.0)
        tipk[m_] = _etip[i] + (_etip[i + 1] - _etip[i]) * t_[m_]
    return sgn * best, tipk


_dface, _tipk = fringe_edge_field(BV)
FIELDS["fringe"] = _dface - (FRINGE_D[1] + (FRINGE_D[0] - FRINGE_D[1]) * _tipk)     # < 0: the band (and above the edge)
FIELDS["fringe_u"] = _dface + FRINGE_UNDER                                          # > 0: not too far above the edge
FIELDS["fringe_gate"] = (HEAD_B & (Y < HC[1] - 0.03) & (np.abs(X) < 0.085) & (Z > _ez - 0.015) & (BN[:, 1] < -0.1)).astype(float)


def hairline_z(P):
    ce_ = (HC[1] - P[:, 1]) / np.maximum(np.hypot(P[:, 0], P[:, 1] - HC[1]), 1e-9)          # 1 = front, -1 = back
    return ce_, np.where(ce_ >= 0, np.interp(ce_, [0.0, 0.55, 1.0], [_ez + 0.004, _ez + 0.040, _ez + HAIRLINE[0]]),
                         np.interp(ce_, [-1.0, -0.35, 0.0], [HEADJ[2] + HAIRLINE[1], HEADJ[2] + HAIRLINE[1] + 0.012, _ez + 0.004]))


_ce, _hl = hairline_z(BV)
FIELDS["hair"] = Z - _hl
FIELDS["ce"] = _ce

bm = bmesh.new()
for p in BV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in BF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k) for k in FIELDS}
for v in bm.verts:
    for k, arr in FIELDS.items():
        v[LAY[k]] = float(arr[v.index])


def iso_cut(key, tau, gate=None, snap=CUT_SNAP):
    L = LAY[key]
    eps = 1e-6 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < snap:
                a[L] = tau
            elif tt > 1 - snap:
                b[L] = tau
    cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
    pairs = []
    for f in bm.faces:
        vs = list(f.verts)
        sides = [side(v) for v in vs]
        if not (1 in sides and -1 in sides):
            continue
        cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
        if len(cv) == 2:
            pairs.append(cv)
    for cv in pairs:
        try:
            bmesh.ops.connect_verts(bm, verts=cv)
        except Exception:
            pass
    big = [f for f in bm.faces if len(f.verts) > 3]
    if big:
        bmesh.ops.triangulate(bm, faces=big)
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts), "face_connects": len(pairs)}


def g_and(*conds):
    return lambda a, b: all(c(a) and c(b) for c in conds)


def gv(key, lo=-1e9, hi=1e9):
    L = LAY[key]
    return lambda v: lo <= v[L] <= hi


def refine_thin(key, exact, passes=2, samples=11):
    """a stroke thinner than the mesh edges can pass BETWEEN vertices: split every gated edge whose interior dips below 0
    while both ends are outside, at the dip, with the EXACT field value there (vampwarrior v3)."""
    L = LAY[key]
    n_split = 0
    for _ in range(passes):
        todo = []
        for e in bm.edges:
            a, b = e.verts
            if not (0.0 <= a[L] <= 0.03 and 0.0 <= b[L] <= 0.03):
                continue
            pa, pb = np.array(a.co), np.array(b.co)
            ts = np.linspace(0.05, 0.95, samples)
            vals = exact(pa[None] + (pb - pa)[None] * ts[:, None])
            k = int(np.argmin(vals))
            if vals[k] < 0:
                todo.append((e, a, b, float(ts[k])))
        for e, a, b, tt in todo:
            vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
            _, nv = bmesh.utils.edge_split(e, a, tt)
            for k in LAY:
                nv[LAY[k]] = vals[k]
            nv[L] = float(exact(np.array(nv.co)[None])[0])
        n_split += len(todo)
        big = [f for f in bm.faces if len(f.verts) > 3]
        if big:
            bmesh.ops.triangulate(bm, faces=big)
        if not todo:
            break
    return n_split


CUTS = []
for s in "LR":
    CUTS.append(("sleeve_" + s, 0.0, g_and(gv("armm_" + s, 0.5))))
    CUTS.append(("arm_" + s, 0.0, g_and(gv("side_" + s, 0.5), gv("z", SHO[s][2] - 0.22, SHO[s][2] + 0.12), gv("headm", -1, 0.5),
                                          gv("armm_" + s, -1, 0.5))))
    CUTS.append(("wraptop_" + s, 0.0, g_and(gv("legm_" + s, 0.5))))
    CUTS.append(("boottop_" + s, 0.0, g_and(gv("legm_" + s, 0.5))))
CUTS.append(("neck", 0.0, g_and(gv("headm", -1, 0.5), gv("z", NECK0[2] - 0.12, HEADJ[2] + 0.06), gv("armm_L", -1, 0.5),
                                 gv("armm_R", -1, 0.5))))
CUTS.append(("sash", 0.0, g_and(gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5), gv("headm", -1, 0.5))))
CUTS.append(("vopen", 0.0, g_and(gv("y", -1, AX_Y), gv("z", Z_SASH - 0.01, NECK0[2] + 0.02), gv("armm_L", -1, 0.5),
                                  gv("armm_R", -1, 0.5), gv("headm", -1, 0.5))))
if LIP is not None:
    CUTS.append(("lip", 0.0, g_and(gv("headm", 0.5), gv("y", -1, Y_LIP + 0.03), gv("z", Z_LIP - 0.03, Z_LIP + 0.03))))
if SEAM is not None:
    CUTS.append(("mline", 0.0, g_and(gv("headm", 0.5), gv("y", -1, Y_LIP + 0.012), gv("z", Z_LIP - 0.015, Z_LIP + 0.015))))
for s in "LR":
    CUTS.append(("eye_" + s, 0.0, g_and(gv("headm", 0.5), gv("eye_" + s, -1, 0.03))))
    CUTS.append(("brow_" + s, 0.0, g_and(gv("headm", 0.5), gv("brow_" + s, -1, 0.03))))
CUTS.append(("jawsh", 0.0, g_and(gv("jawgate", 0.5))))
CUTS.append(("fringe", 0.0, g_and(gv("fringe_gate", 0.5), gv("hair", -0.12, 0.004), gv("fringe_u", 0.0))))
CUTS.append(("fringe_u", 0.0, g_and(gv("fringe_gate", 0.5), gv("hair", -0.12, 0.004), gv("fringe", -1.0, 0.0))))
CUTS.append(("hair", 0.0, g_and(gv("headm", 0.5))))
t_ = time.time()
REFINE = {"brow_" + s: refine_thin("brow_" + s, lambda P_, s=s: brow_field(P_, s)) for s in "LR"}
if SEAM is not None:
    REFINE["mline"] = refine_thin("mline", mline_field, **MOUTH_LINE_REFINE)
cut_log = [iso_cut(k_, tau_, gate_, **({"snap": MOUTH_LINE_SNAP} if k_ == "mline" else {})) for k_, tau_, gate_ in CUTS]
_ndeg = len(bm.faces)
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
for _ in range(3):
    _sl = {}
    for f in bm.faces:
        if f.calc_area() < SLIVER_AREA:
            e_ = min(f.edges, key=lambda e: e.calc_length())
            _sl[e_.index if e_.is_valid else id(e_)] = e_
    bm.edges.index_update()
    if not _sl:
        break
    _seen, _es = set(), []
    for e_ in _sl.values():
        if e_.is_valid and not (set(e_.verts) & _seen):
            _es.append(e_); _seen |= set(e_.verts)
    bmesh.ops.collapse(bm, edges=_es, uvs=False)
_ndeg -= len(bm.faces)
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FV = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
nF = len(CF)
reg = np.array(["skin"] * nF, dtype=object)
_head = FV["headm"] > 0.5
_armL, _armR = FV["armm_L"] > 0.5, FV["armm_R"] > 0.5
_arm = _armL | _armR
_legL, _legR = FV["legm_L"] > 0.5, FV["legm_R"] > 0.5
_leg = _legL | _legR
_torso = ~_head & ~_arm & ~_leg
_armhole_out = ((FV["arm_L"] > 0) & (FV["side_L"] > 0.5)) | ((FV["arm_R"] > 0) & (FV["side_R"] > 0.5))
_upper = _torso & (FV["neck"] < 0) & (FV["sash"] >= 0)
reg[_upper] = "shirt"
reg[_upper & ~_armhole_out & ((FV["y"] > AX_Y) | (FV["vopen"] > 0))] = "vest"
reg[(_armL & (FV["sleeve_L"] < 0)) | (_armR & (FV["sleeve_R"] < 0))] = "shirt"
reg[(_torso & (FV["sash"] < 0)) | _leg] = "trousers"
for s, lm_ in (("L", _legL), ("R", _legR)):
    reg[lm_ & (FV["wraptop_" + s] > 0) & (FV["boottop_" + s] <= 0)] = "wrap"
    reg[lm_ & (FV["boottop_" + s] > 0)] = "boot"          # under the solid boot (removed below where hidden)
SH_MASK = {"jaw_neck": (reg == "skin") & (FV["jawgate"] > 0.5) & (FV["jawsh"] < 0),
           "fringe": (reg == "skin") & _head & (FV["hair"] < 0) & (FV["fringe"] < 0) & (FV["fringe_u"] > 0) & (FV["fringe_gate"] > 0.5)}
for k_, m_ in SH_MASK.items():
    reg[m_] = "skin_shadow"
if LIP is not None:
    reg[_head & (FV["lip"] < 0) & (FV["y"] < Y_LIP + 0.03) & (np.abs(FV["z"] - Z_LIP) < 0.03)] = "lips"
if SEAM is None:                                   # v1 rule: lip-zone skin recessed behind the front = the mouth
    _mx = np.arange(-LIP[0] - 0.002, LIP[0] + 0.0021, 0.0005)
    _mfront = []
    for x_ in _mx:
        ys_ = [BVH_BODY.ray_cast(Vector((float(x_), -0.6, float(z_))), Vector((0.0, 1.0, 0.0)), 1.0)[0]
               for z_ in np.arange(Z_SLIT - 0.008, Z_SLIT + 0.008, 0.0005)]
        _mfront.append(min(h[1] for h in ys_ if h is not None))
    _mfront = np.array(_mfront)
    for fi in np.nonzero(reg == "lips")[0]:
        c_ = CV[CF[fi]].mean(0)
        if c_[1] > float(np.interp(c_[0], _mx, _mfront)) + MOUTH_IN_D:
            reg[fi] = "mouth"
else:                                              # v2: the thin line on the sealed seam
    # (v6: + every face whose centroid lies in the stroke by the EXACT field -- a cut vertex of a later field carries the
    # line's field linearly interpolated, and a rim face over the stroke averaged outside it: a gap in the line, rendered)
    _mfc = np.array([CV[f].mean(0) for f in CF])
    reg[_head & ((FV["mline"] < 0) | (mline_field(_mfc) < -MOUTH_LINE_CENTROID)) & (FV["y"] < Y_LIP + 0.012) &
        (np.abs(FV["z"] - Z_LIP) < 0.015)] = "mouth"
reg[_head & ((FV["eye_L"] < 0) | (FV["eye_R"] < 0))] = "liner"
if LASH_PROFILE is not None:
    # v3: the upper band (and the wing) is the LASH region (near-black); the thin lower line stays 'liner' (lighter)
    _fcen = np.array([CV[f].mean(0) for f in CF])
    for s in "LR":
        _rad, _ang = eye_polar(_fcen, s)
        _lash = (reg == "liner") & (FV["eye_" + s] < 0) & ((np.sin(np.radians(_ang)) > -0.05) |
                                                            (np.abs(((_ang + 180.0) % 360.0) - 180.0) < LASH_WING[1] * 1.6))
        reg[_lash] = "lash"
reg[_head & ((FV["brow_L"] < 0) | (FV["brow_R"] < 0)) & (FV["hair"] < 0)] = "brow"
# v7 (fold-in of the v6 "grown" finding): the hairline field (height over the front / back hairline, by the azimuth about
# the head's vertical axis) is ill-defined near that axis, and the mouth INTERIOR's back wall sits right on it, deep inside
# the head -- 42 of its faces scored "hair", joined the scalp cap (hair_cap) and coupled the hair digest to the mouth dials.
# Only the head's outer skin is scalp: a face whose centroid lies under HAIR_INTERIOR_R of the head ellipsoid (HC / HR) is
# never hair (measured: the interior faces 0.42-0.62, every scalp face >= 0.75, nothing between)
_fcr_h = np.array([CV[f].mean(0) for f in CF])
_hair_outer = (np.linalg.norm((_fcr_h - HC) / HR, axis=1) >= HAIR_INTERIOR_R) if HAIR_INTERIOR_R is not None else np.ones(len(CF), bool)
HAIR_INTERIOR_MASK = _head & (FV["hair"] > 0) & ~_hair_outer    # (hidden deep in the head: s6 still removes them from the
#   body exactly as v6 did when they counted as scalp -- the body mesh, its UVs and its bakes stay the v6 ones)
report["hair_interior_excluded"] = {"rule": "hairline faces with centroid under %s of the head ellipsoid: not scalp (no cap), "
                                            "removed from the body as hidden (as in v6)" % HAIR_INTERIOR_R,
                                    "faces": int(HAIR_INTERIOR_MASK.sum())}
reg[_head & (FV["hair"] > 0) & _hair_outer] = "hair"
report["iso_cuts"] = {"cuts": len(cut_log), "edge_splits": int(sum(c["edge_splits"] for c in cut_log)),
                      "thin_stroke_refine_splits": REFINE, "degenerate_faces_dissolved": int(_ndeg),
                      "seconds": round(time.time() - t_, 1), "body_tris_after_cuts": nF}
print("CUTS", json.dumps(report["iso_cuts"]))
CW = transfer(CV)
BVH_CUT = BVHTree.FromPolygons(CV.tolist(), CF)
# liner proof (vampwarrior v3's): the PAINTED cut mesh seen from the front, per 5-deg ray fan, visible liner width
_lp = {}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    rows_ = []
    for k in range(AP_N):
        th_ = math.radians(360.0 * k / AP_N)
        seen, edge_, first_ = [], None, None
        for i in range(1, int((float(EYE_AP[s].max()) + 0.008) / 0.00005)):   # (v3: past the enlarged outer corner + the wing)
            rho = i * 0.00005
            who, h_ = front_first_hit(s, BVH_CUT, BVH_EYE[s], c_[0] + sg_ * rho * math.cos(th_), c_[2] + rho * math.sin(th_))
            if who == "skin":
                r_ = reg[h_[2]]
                if edge_ is None:
                    edge_, first_ = rho, r_
                seen.append(r_ in ("liner", "lash"))
            elif edge_ is not None:
                seen.append(False)
        seen = np.array(seen, bool)
        rows_.append({"deg": 360.0 * k / AP_N, "width_mm": 0.05 * float(seen.sum()), "starts_at_lid_edge": first_ in ("liner", "lash")})
    _lp[s] = rows_
_all = [r for s in "LR" for r in _lp[s]]
_up = [r["width_mm"] for r in _all if 15.0 <= r["deg"] <= 165.0]
_lo = [r["width_mm"] for r in _all if 195.0 <= r["deg"] <= 345.0]
_prof = lambda lo_, hi_: round(float(np.median([r["width_mm"] for r in _all if lo_ <= r["deg"] <= hi_])), 2)
report["liner_proof"] = {"widths_mm_target": {"upper": (LASH_PROFILE if LASH_PROFILE is not None else LINER_W[0] * 1000),
                                              "lower": LINER_W[1] * 1000},
                         "upper_by_zone_mm_median": {"outer_0_40": _prof(0.0, 40.0), "mid_60_120": _prof(60.0, 120.0),
                                                     "inner_140_175": _prof(140.0, 175.0)},
                         "wing_mm_at_%d_deg" % (LASH_WING[2] if LASH_PROFILE is not None else LINER_WING[2]):
                             round(float(np.median([r["width_mm"] for r in _all if abs(r["deg"] - (LASH_WING[2] if LASH_PROFILE is not None else LINER_WING[2])) <= 5.0
                                                    or abs(r["deg"] - 360.0 - (LASH_WING[2] if LASH_PROFILE is not None else LINER_WING[2])) <= 5.0])), 2),
                         "upper_lid_width_mm_median": round(float(np.median(_up)), 2),
                         "lower_lid_width_mm_median": round(float(np.median(_lo)), 2),
                         "contour_closed_pct": round(100.0 * float(np.mean([r["width_mm"] >= 0.2 for r in _all])), 1),
                         "starts_at_lid_edge_pct": round(100.0 * float(np.mean([r["starts_at_lid_edge"] for r in _all])), 1),
                         "aperture_mm_L": {"outer": round(1000 * float(aperture(0.0, "L")), 2), "up": round(1000 * float(aperture(90.0, "L")), 2),
                                           "inner": round(1000 * float(aperture(180.0, "L")), 2), "down": round(1000 * float(aperture(270.0, "L")), 2)}}
_bw = {}
_cur = BROW_CURVE["L"][:, [0, 2]]
for tt_ in (0.05, 0.25, 0.5, 0.75, 0.95):
    i_ = int(round(tt_ * (len(_cur) - 1)))
    tg_ = unit(np.append(_cur[min(i_ + 1, len(_cur) - 1)] - _cur[max(i_ - 1, 0)], 0.0))[:2]
    nr_ = np.array([-tg_[1], tg_[0]])
    run_, best_ = 0, 0
    for k_ in range(-160, 161):
        p_ = _cur[i_] + nr_ * k_ * 0.00005
        h_ = BVH_CUT.ray_cast(Vector((float(p_[0]), -1.0, float(p_[1]))), Vector((0.0, 1.0, 0.0)), 2.0)
        if h_[0] is not None and reg[h_[2]] == "brow":
            run_ += 1; best_ = max(best_, run_)
        else:
            run_ = 0
    _bw["t%.2f" % tt_] = round(0.05 * best_, 2)
_eye_w = 1000 * float(aperture(0.0, "L") + aperture(180.0, "L"))
report["brow"] = {"BROW_PTS": [list(p) for p in BROW_PTS], "BROW_W_mm": [w * 1000 for w in BROW_W], "width_mm_measured": _bw,
                  "eye_width_mm": round(_eye_w, 1), "inner_width_over_eye_width": round(_bw["t0.05"] / max(_eye_w, 1e-6), 3),
                  "rule": "front view: at stations t along the stroke, the longest run of rays whose first hit is a brow face"}
_fa_cut = np.array([0.5 * np.linalg.norm(np.cross(CV[f[1]] - CV[f[0]], CV[f[2]] - CV[f[0]])) for f in CF])
report["shadow_shapes"] = {k: {"area_cm2": round(1e4 * float(_fa_cut[m & (reg == "skin_shadow")].sum()), 2),
                               "faces": int((m & (reg == "skin_shadow")).sum())} for k, m in SH_MASK.items()}
report["regions_body_faces"] = {r: int((reg == r).sum()) for r in sorted(set(reg))}
print("LINER", json.dumps(report["liner_proof"]), "BROW", json.dumps(report["brow"]))
print("SHADOWS", json.dumps(report["shadow_shapes"]), "BODYREG", json.dumps(report["regions_body_faces"]))
