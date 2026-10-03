# Elias build section 2 (adapted from wren_s2_regions.py): painted regions on the body by iso-cut fields -- the robe
# (V neckline, sleeves ending under the big roll), trousers below the belt line, boot tops; the face (lid liner + lash
# band, HEAVY brows, lip tint + the mouth line), the authored jaw shadow, the painted age lines (crow's feet, forehead
# lines: "lined kind face" as paint, not geometry), the UNDER-BEARD zone (dark inner tone under the beard locks, the lip
# band kept skin), the scalp hairline. (No fringe shadow: Elias's hair is swept back.)

Z_BELT = Z_WAIST + BELT["dz"]
# the CHIN for the beard / proportions: s1's Z_CHIN (Wren's jump rule) runs out of its 90 mm scan on both Wren and Elias
# (slit_to_chin = 0.090 exactly) -- the beard keys off the chin OUTLINE (the front-view jaw edge, s1's mouth placement)
Z_CHIN_B = Z_CHIN_OUTLINE
_hh_o = Z_TOP - Z_CHIN_B
report["proportions"] = {"heads_tall_house_rule": report["landmarks"]["heads_tall"], "house_rule": "s1: (top - sole) / (top - (slit - 90 mm)); Wren 5.94",
                         "heads_tall_chin_outline": round((Z_TOP - SOLE_T) / _hh_o, 2), "head_height_outline_m": round(_hh_o, 4),
                         "wren_chin_outline_heads": 6.90}
FIELDS = {}
X, Y, Z = BV[:, 0], BV[:, 1], BV[:, 2]
SHIN = {}
for s, sg in (("L", 1.0), ("R", -1.0)):
    fa = WRI[s] - ELB[s]
    FIELDS["sleeve_" + s] = (BV - ELB[s]) @ unit(fa) - SLEEVE_T * np.linalg.norm(fa)     # > 0 = below the robe sleeve
    FIELDS["legm_" + s] = LEG_B[s].astype(float)
    FIELDS["armm_" + s] = ARM_B[s].astype(float)
    sa = ANKLE[s] - KNEE[s]
    Ls = float(np.linalg.norm(sa)); ua = sa / Ls
    fr = unit(np.array([0.0, -1.0, 0.0]) - ua * float(ua @ np.array([0.0, -1.0, 0.0])))
    lat = np.cross(ua, fr)
    lat = lat if lat[0] > 0 else -lat
    tt = ((BV - KNEE[s]) @ ua) / Ls
    SHIN[s] = {"ua": ua, "fr": fr, "lat": lat, "len": Ls}
    FIELDS["boottop_" + s] = tt - BOOT_TOP
FIELDS["neck"] = Z - np.where(Y < NECK0[1] + 0.02,
                              np.minimum(NECK0[2] - NECK_DZ + 0.3 * (Y - NECK0[1]), NECK0[2] - VNECK[0] + VNECK[1] * np.abs(X)),
                              NECK0[2] - NECK_DZ + 0.3 * (Y - NECK0[1]))
FIELDS["headm"] = HEAD_B.astype(float)
FIELDS["neckm"] = dom_in(["neck_01"]).astype(float)
FIELDS["belt"] = Z - Z_BELT
FIELDS["z"] = Z.copy()
FIELDS["y"] = Y.copy()
if LIP is not None:
    FIELDS["lip"] = (X / LIP[0]) ** 2 + ((Z - Z_LIP) / np.where(Z > Z_LIP, LIP[1], LIP[2])) ** 2 - 1.0
if SEAM is not None:
    _sx0, _sx1 = float(SEAM["xs"].min()), float(SEAM["xs"].max())
    if MOUTH_LEN is not None:
        _sx0, _sx1 = max(_sx0, -MOUTH_LEN), min(_sx1, MOUTH_LEN)
    else:
        _sx0, _sx1 = _sx0 - MOUTH_LINE_EXT, _sx1 + MOUTH_LINE_EXT

    def mline_centre(x_):
        return np.interp(x_, SEAM["xs"], SEAM["zc"])

    def mline_field(P_):
        u_ = np.clip((P_[:, 0] - _sx0) / max(_sx1 - _sx0, 1e-6), 0.0, 1.0)
        hw_ = 0.5 * MOUTH_LINE[0] * (MOUTH_LINE[1] + (1.0 - MOUTH_LINE[1]) * np.sin(np.pi * u_) ** 0.6)
        return np.where((P_[:, 0] >= _sx0) & (P_[:, 0] <= _sx1), np.abs(P_[:, 2] - mline_centre(P_[:, 0])) - hw_, 1.0)

    FIELDS["mline"] = mline_field(BV)
# ---- eyes: the lathe eyeball (rendered) + the lid-edge aperture per 5 deg (Wren's code, unchanged)
BN = VP.vertex_normals(BV, BF)
if float(np.mean(np.einsum("ij,ij->i", BN, BV - BV.mean(0)))) < 0:
    BN = -BN
EYE_MESH, EYE_AP = {}, {}
AP_STEP, AP_N = 0.0001, 72
EYE_PAINT = {}
for s in "LR":
    e = EYE[s]
    _im = 0.5 * (EYE_PUPIL_DEG + 4 + EYE_IRIS_DEG)
    angs = [0.0, EYE_PUPIL_DEG, EYE_PUPIL_DEG + 4, _im, EYE_IRIS_DEG, max(EYE_IRIS_DEG + 10.0, 60.0), 90.0] + \
        ([125.0, 155.0, 180.0] if EYE_BACK is None else [])
    if EYE_HILITE is not None:
        _hc = EYE_HILITE[1] * EYE_IRIS_DEG
        angs += [a_ for a_ in np.arange(max(_hc - EYE_HILITE[2] - 1.0, 1.0), _hc + EYE_HILITE[2] + 1.0, EYE_HILITE_RING)
                 if min(abs(a_ - b_) for b_ in angs) > 0.6]
        angs = sorted(float(a_) for a_ in angs)
    prof = [(e["r"] * math.sin(math.radians(a)), e["r"] * math.cos(math.radians(a))) for a in angs]
    if EYE_BACK is not None:
        prof.append((0.0, e["r"] * math.cos(math.radians(EYE_BACK))))
        angs = angs + [180.0]
    regs = [("eye_pupil" if a1_ <= EYE_PUPIL_DEG + 1e-6 else ("eye_iris" if a1_ <= EYE_IRIS_DEG + 1e-6 else "eye_sclera"))
            for a1_ in angs[1:]]
    Ve_, Fe_, Re_ = VP.lathe(prof, regs, EYE_SEG, e["c"], (0.0, -1.0, 0.0), up_hint=(0, 0, 1))
    _ax = np.array([0.0, -1.0, 0.0])
    _ang = lambda P_, c_=e["c"]: np.degrees(np.arccos(np.clip((P_ - c_) @ _ax / np.maximum(np.linalg.norm(P_ - c_, axis=1), 1e-12), -1, 1)))
    _ncut = 0
    if IRIS_SHADE is not None:
        _zc = e["c"][2] + IRIS_SHADE * e["r"] * math.sin(math.radians(EYE_IRIS_DEG))
        Ve_, Fe_, Re_, _, n_ = cut_part(Ve_, Fe_, Re_, lambda P_: P_[:, 2] - _zc, 0.0, vgate=_ang(Ve_) <= EYE_IRIS_DEG + 0.01)
        _ncut += n_
        _fc = np.array([Ve_[f].mean(0) for f in Fe_])
        Re_ = [("eye_iris_dark" if (r_ in ("eye_iris",) and fc_[2] > _zc) else r_) for r_, fc_ in zip(Re_, _fc)]
    if EYE_HILITE is not None:
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


def measure_aperture():
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


measure_aperture()


def aperture(ang_, s):
    return np.interp(ang_, 360.0 * np.arange(AP_N) / AP_N, EYE_AP[s], period=360.0)


EYE_TUCK = (0.001, 0.0005)            # (Wren v3 eyeball tuck: the ball pulled back behind the skin outside the opening)
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
        if h_[0] is not None and h_[3] < L_ + EYE_TUCK[1] and float(np.array(h_[1]) @ d_) > 0.0:
            Ve_[i] = c_ + d_ * max(h_[3] - EYE_TUCK[1], 0.3 * L_)
            moved_.append(L_ - float(np.linalg.norm(Ve_[i] - c_)))
    EYE_MESH[s] = (Ve_, EYE_MESH[s][1], EYE_MESH[s][2])
    EYE_TUCK_INFO[s] = {"verts": len(moved_), "max_pull_mm": round(1000 * max(moved_), 2) if moved_ else 0.0}
BVH_EYE = {s: BVHTree.FromPolygons(EYE_MESH[s][0].tolist(), EYE_MESH[s][1]) for s in "LR"}
measure_aperture()
report["eye_tuck"] = EYE_TUCK_INFO
_IRIS_FAM = ("eye_iris", "eye_iris_dark", "eye_pupil", "eye_hilite")
EYE_PROOF = {}
for s in "LR":
    c_ = EYE[s]["c"]
    Re_ = EYE_MESH[s][2]
    n_eye, n_iris = 0, 0
    for x_ in np.arange(c_[0] - 0.026, c_[0] + 0.026, 0.00025):
        for z_ in np.arange(c_[2] - 0.016, c_[2] + 0.016, 0.00025):
            who, h_ = front_first_hit(s, BVH_BODY, BVH_EYE[s], float(x_), float(z_))
            if who == "eye":
                n_eye += 1
                n_iris += Re_[h_[2]] in _IRIS_FAM
    EYE_PROOF[s] = {"visible_opening_mm2": round(n_eye * 0.0625, 1), "iris_coverage_pct": round(100.0 * n_iris / max(n_eye, 1), 1)}
report["eye_proof"] = {"rule": "front rays on a 0.25 mm grid: iris-family hits / eyeball hits (the visible opening)",
                       "iris_deg": EYE_IRIS_DEG, "pupil_deg": EYE_PUPIL_DEG, **EYE_PROOF}
print("EYEPROOF", json.dumps(report["eye_proof"]))


def liner_w(ang_):
    ang_ = np.asarray(ang_, float)
    up_ = smoothstep(-0.25, 0.25, np.sin(np.radians(ang_)))
    a_ = np.where(np.sin(np.radians(ang_)) >= 0, ang_ % 360.0, np.where(np.cos(np.radians(ang_)) >= 0, 0.0, 180.0))
    wu_ = 1e-3 * np.interp(a_, [p[0] for p in LASH_PROFILE], [p[1] for p in LASH_PROFILE])
    dw_ = np.minimum(np.abs(ang_ - LASH_WING[2]) % 360.0, 360.0 - np.abs(ang_ - LASH_WING[2]) % 360.0)
    return LINER_W[1] + (wu_ - LINER_W[1]) * up_ + 1e-3 * LASH_WING[0] * np.exp(-(dw_ / LASH_WING[1]) ** 2)


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


def seg_field(P, cur, w0, w1, taper=1.0):
    """front-projected distance to the polyline cur (x, z) minus its half width (w0 at the start .. w1 at the end)."""
    q = P[:, [0, 2]]
    best = np.full(len(P), 1e9); tb = np.zeros(len(P))
    for i in range(len(cur) - 1):
        a_, b_ = cur[i], cur[i + 1]
        ab = b_ - a_
        t_ = np.clip(((q - a_) @ ab) / max(float(ab @ ab), 1e-18), 0, 1)
        d_ = np.linalg.norm(q - (a_ + t_[:, None] * ab), axis=1)
        m_ = d_ < best
        best[m_] = d_[m_]; tb[m_] = (i + t_[m_]) / max(len(cur) - 1, 1)
    return best - 0.5 * (w0 + (w1 - w0) * tb ** taper)


def brow_field(P, s):
    return seg_field(P, BROW_CURVE[s][:, [0, 2]], BROW_W[0], BROW_W[1], BROW_TAPER)


for s in "LR":
    gate_ = HEAD_B & (Y < EYE[s]["c"][1] + 0.03) & (np.abs(X - EYE[s]["c"][0]) < 0.042) & (Z > EYE[s]["c"][2]) & \
        (Z < EYE[s]["c"][2] + 0.040) & (BN[:, 1] < -0.05)
    FIELDS["brow_" + s] = np.where(gate_, brow_field(BV, s), 1.0)
# ---- the painted AGE LINES ("lined kind face"): crow's feet fanning from each outer eye corner, two soft forehead lines
EZ0 = float(EYE["L"]["c"][2])
FACE_LINE_CURVES = []
if FACE_LINES is not None:
    n_, ln_, w_, gap_ = FACE_LINES["crow"]
    for s in "LR":
        c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
        oc_ = np.array([c_[0] + sg_ * float(aperture(0.0, s)), c_[2] + 0.001])
        for k in range(n_):
            a_ = math.radians(-24.0 + 24.0 * k)
            d_ = np.array([sg_ * math.cos(a_), math.sin(a_)])
            p0_ = oc_ + d_ * gap_
            FACE_LINE_CURVES.append((np.array([p0_, p0_ + d_ * ln_ * (0.8 if k != 1 else 1.0)]), w_, s))
    nf_, hl_, wf_, _ = FACE_LINES["brow_furrow"]
    for k, zf_ in enumerate(FOREHEAD_LINES_Z[:nf_]):
        xs_ = np.linspace(-hl_, hl_, 9)
        FACE_LINE_CURVES.append((np.stack([xs_, EZ0 + zf_ + 0.0025 * (1.0 - (xs_ / hl_) ** 2) * (1 if k else 0.6)], 1), wf_, "C"))


def face_line_field(P):
    out_ = np.full(len(P), 1.0)
    for cur_, w_, _ in FACE_LINE_CURVES:
        out_ = np.minimum(out_, seg_field(P, cur_, w_, w_ * 0.45, 1.0))
    return out_


if FACE_LINE_CURVES:
    _flg = HEAD_B & (BN[:, 1] < -0.15) & (Z > EZ0 - 0.02) & (Y < HC[1] - 0.01)
    FIELDS["fline"] = np.where(_flg, face_line_field(BV), 1.0)
# ---- authored shadow: the chin's cast shadow on the neck under a stylised key light (Wren's)
_el = math.radians(JAW_LIGHT_DEG)
L_JAW = np.array([0.0, -math.cos(_el), math.sin(_el)])
BVH_HEADONLY = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n == "head"])
_jg = (Z < Z_CHIN + JAW_GATE[0] + JAW_GATE[1] * np.maximum(np.abs(X) - JAW_GATE[2], 0.0)) & (Z > NECK0[2] - 0.06) & \
    (Y < NECK0[1] + 0.02) & ~(ARM_B["L"] | ARM_B["R"])
_cast = np.zeros(len(BV))
for i in np.nonzero(_jg)[0]:
    if BVH_HEADONLY.ray_cast(Vector(BV[i] + BN[i] * 0.001), Vector(L_JAW), 0.3)[0] is not None:
        _cast[i] = 1.0
_bedges = np.array(sorted(set((min(f[k], f[(k + 1) % len(f)]), max(f[k], f[(k + 1) % len(f)])) for f in BF for k in range(len(f)))))
_bdeg = np.bincount(_bedges.ravel(), minlength=len(BV)).astype(float)
for _ in range(JAW_SMOOTH):
    acc_ = np.zeros(len(BV))
    np.add.at(acc_, _bedges[:, 0], _cast[_bedges[:, 1]]); np.add.at(acc_, _bedges[:, 1], _cast[_bedges[:, 0]])
    _cast = 0.5 * _cast + 0.5 * acc_ / np.maximum(_bdeg, 1.0)
FIELDS["jawsh"] = np.where(_jg, 0.5 - _cast, 1.0)
FIELDS["jawgate"] = _jg.astype(float)
# ---- the UNDER-BEARD zone: < 0 inside. Top edge (front projection, |x| from the midline): under the nose, down the cheek
# line, up to the sideburn in front of the ear (BEARD_ZONE); the lip band round the mouth kept skin; under the jaw down the
# neck front to BEARD_ZONE["neck_drop"] below the chin; never behind the ear line.
_zx = [0.0, 0.016] + [p_[0] for p_ in BEARD_ZONE["cheek"]]
_zz = [Z_NOSE_BOTTOM - 0.003, Z_NOSE_BOTTOM - 0.004] + [EZ0 + p_[1] for p_ in BEARD_ZONE["cheek"]]


def beard_top_z(ax_):
    return np.where(ax_ > _zx[-1], EZ0 + BEARD_ZONE["sideburn_z"], np.interp(ax_, _zx, _zz))


def beard_neck_z(ax_):
    """v4: the zone's lower edge on the neck -- BEARD_ZONE["neck_drop"] below the chin at the front, rising neck_rise[0]
    toward the jaw corners (|x| neck_rise[1] -> [2]): the long beard's side masses end on the jaw, so the painted zone must
    end there too (the shell's underside closes onto it; Varden v4's rule)."""
    nr_ = BEARD_ZONE.get("neck_rise", (0.0, 0.0, 1.0))
    return Z_CHIN_B - BEARD_ZONE["neck_drop"] + nr_[0] * smoothstep(nr_[1], nr_[2], ax_)


def beard_field(P):
    ax_ = np.abs(P[:, 0])
    f_ = P[:, 2] - beard_top_z(ax_)                                       # > 0 above the top edge
    ylim_ = np.where(P[:, 2] > Z_CHIN_B, HC[1] - 0.006, NECK0[1] - 0.004)
    f_ = np.maximum(f_, P[:, 1] - ylim_)                                  # > 0 behind the ear / neck line
    f_ = np.maximum(f_, beard_neck_z(ax_) - P[:, 2])                      # > 0 below the neck limit
    if SEAM is not None:
        hw_ = 0.5 * float(SEAM["xs"].max() - SEAM["xs"].min()) + BEARD_ZONE["lip_band"][0]
        dz_ = P[:, 2] - np.interp(P[:, 0], SEAM["xs"], SEAM["zc"])
        hz_ = np.where(dz_ > 0, BEARD_ZONE["lip_band"][1], BEARD_ZONE["lip_band"][2])
        lip_ = 1.0 - np.sqrt((P[:, 0] / hw_) ** 2 + (dz_ / hz_) ** 2)      # > 0 inside the lip band
        f_ = np.maximum(f_, 0.01 * lip_)
    return f_


_bg = (HEAD_B | dom_in(["neck_01"])) & (Z < EZ0 + 0.01) & (Z > Z_CHIN_B - BEARD_ZONE["neck_drop"] - 0.02)
FIELDS["beard"] = np.where(_bg, beard_field(BV), 1.0)
# v4 BEARD FADE (research H9, design/research/hair-face-best-practices.md: "a painted fade band of 3-6 mm on the skin just
# outside the edge ... gives the stubble read"; Varden v4's rule): BEARD_FADE[1] painted steps beard_fade1 (next to the
# shell, darkest) .. beard_fadeN (lightest) on the skin OUTSIDE the zone edge, each step's outer boundary an iso-line of the
# zone field minus a SERRATION (teeth along the edge, deterministic per-tooth jitter, amplitude growing outward) -- the
# shell's sunk edge sits on the zone edge, so the beard runs out into stubble, never into a flat painted patch. Painted by
# face centroid (no cuts: no body tris); the lip band rises steeply (x BEARD_FADE[4]) so the fade only rims the mouth.
_BF_W, _BF_N, _BF_A, _BF_TW, _BF_LIP = BEARD_FADE


def beard_fade_field(P, k):
    """zone field (beard_field's terms, the lip band scaled by _BF_LIP) minus the step-k serration (k = 1 .. _BF_N)."""
    ax_ = np.abs(P[:, 0])
    f_ = P[:, 2] - beard_top_z(ax_)
    ylim_ = np.where(P[:, 2] > Z_CHIN_B, HC[1] - 0.006, NECK0[1] - 0.004)
    f_ = np.maximum(f_, P[:, 1] - ylim_)
    f_ = np.maximum(f_, beard_neck_z(ax_) - P[:, 2])
    if SEAM is not None:
        hw_ = 0.5 * float(SEAM["xs"].max() - SEAM["xs"].min()) + BEARD_ZONE["lip_band"][0]
        dz_ = P[:, 2] - np.interp(P[:, 0], SEAM["xs"], SEAM["zc"])
        hz_ = np.where(dz_ > 0, BEARD_ZONE["lip_band"][1], BEARD_ZONE["lip_band"][2])
        f_ = np.maximum(f_, _BF_LIP * (1.0 - np.sqrt((P[:, 0] / hw_) ** 2 + (dz_ / hz_) ** 2)))
    s_ = (ax_ + (P[:, 2] - EZ0)) / _BF_TW + 0.37 * k                     # (runs along the cheek line, the sideburn, the neck)
    i_ = np.floor(s_)
    u_ = s_ - i_
    a_ = 0.55 + 0.9 * np.array([hash01(int(q_), 3.1 + k) for q_ in i_])
    return f_ - _BF_A * (k / _BF_N) * a_ * (1.0 - np.abs(2.0 * u_ - 1.0)) ** 1.3



def hairline_z(P):
    ce_ = (HC[1] - P[:, 1]) / np.maximum(np.hypot(P[:, 0], P[:, 1] - HC[1]), 1e-9)          # 1 = front, -1 = back
    return ce_, np.where(ce_ >= 0, np.interp(ce_, [0.0, 0.55, 1.0], [EZ0 + 0.006, EZ0 + 0.044, EZ0 + HAIRLINE[0]]),
                         np.interp(ce_, [-1.0, -0.35, 0.0], [HEADJ[2] + HAIRLINE[1], HEADJ[2] + HAIRLINE[1] + 0.012, EZ0 + 0.006]))


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
    while both ends are outside, at the dip, with the EXACT field value there (Wren / vampwarrior v3)."""
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
    CUTS.append(("boottop_" + s, 0.0, g_and(gv("legm_" + s, 0.5))))
CUTS.append(("neck", 0.0, g_and(gv("headm", -1, 0.5), gv("z", NECK0[2] - 0.12, HEADJ[2] + 0.06), gv("armm_L", -1, 0.5),
                                 gv("armm_R", -1, 0.5))))
CUTS.append(("belt", 0.0, g_and(gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5), gv("headm", -1, 0.5))))
if LIP is not None:
    CUTS.append(("lip", 0.0, g_and(gv("headm", 0.5), gv("y", -1, Y_LIP + 0.03), gv("z", Z_LIP - 0.03, Z_LIP + 0.03))))
if SEAM is not None:
    CUTS.append(("mline", 0.0, g_and(gv("headm", 0.5), gv("y", -1, Y_LIP + 0.012), gv("z", Z_LIP - 0.015, Z_LIP + 0.015))))
for s in "LR":
    CUTS.append(("eye_" + s, 0.0, g_and(gv("headm", 0.5), gv("eye_" + s, -1, 0.03))))
    CUTS.append(("brow_" + s, 0.0, g_and(gv("headm", 0.5), gv("brow_" + s, -1, 0.03))))
if FACE_LINE_CURVES:
    CUTS.append(("fline", 0.0, g_and(gv("headm", 0.5), gv("fline", -1, 0.03))))
CUTS.append(("jawsh", 0.0, g_and(gv("jawgate", 0.5))))
CUTS.append(("beard", 0.0, g_and(gv("beard", -0.2, 0.2))))
CUTS.append(("hair", 0.0, g_and(gv("headm", 0.5))))
t_ = time.time()
REFINE = {"brow_" + s: refine_thin("brow_" + s, lambda P_, s=s: brow_field(P_, s)) for s in "LR"}
if SEAM is not None:
    REFINE["mline"] = refine_thin("mline", mline_field, **MOUTH_LINE_REFINE)
if FACE_LINE_CURVES:
    REFINE["fline"] = refine_thin("fline", lambda P_: np.where(True, face_line_field(P_), 1.0), passes=3, samples=21)
cut_log = [iso_cut(k_, tau_, gate_, **({"snap": MOUTH_LINE_SNAP} if k_ in ("mline", "fline") else {})) for k_, tau_, gate_ in CUTS]
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
_upper = _torso & (FV["neck"] < 0) & (FV["belt"] >= 0)
reg[_upper] = "robe"
reg[(_armL & (FV["sleeve_L"] < 0)) | (_armR & (FV["sleeve_R"] < 0))] = "robe"
reg[(_torso & (FV["belt"] < 0)) | _leg] = "trousers"
for s, lm_ in (("L", _legL), ("R", _legR)):
    reg[lm_ & (FV["boottop_" + s] > 0)] = "boot"          # under the solid boot (removed below where hidden)
SH_MASK = {"jaw_neck": (reg == "skin") & (FV["jawgate"] > 0.5) & (FV["jawsh"] < 0)}
for k_, m_ in SH_MASK.items():
    reg[m_] = "skin_shadow"
if LIP is not None:
    reg[_head & (FV["lip"] < 0) & (FV["y"] < Y_LIP + 0.03) & (np.abs(FV["z"] - Z_LIP) < 0.03)] = "lips"
_mfc = np.array([CV[f].mean(0) for f in CF])
if SEAM is not None:
    reg[_head & ((FV["mline"] < 0) | (mline_field(_mfc) < -MOUTH_LINE_CENTROID)) & (FV["y"] < Y_LIP + 0.012) &
        (np.abs(FV["z"] - Z_LIP) < 0.015)] = "mouth"
reg[_head & ((FV["eye_L"] < 0) | (FV["eye_R"] < 0))] = "liner"
for s in "LR":
    _rad, _ang = eye_polar(_mfc, s)
    _lash = (reg == "liner") & (FV["eye_" + s] < 0) & ((np.sin(np.radians(_ang)) > -0.05) |
                                                        (np.abs(((_ang + 180.0) % 360.0) - 180.0) < LASH_WING[1] * 1.6))
    reg[_lash] = "lash"
if FACE_LINE_CURVES:
    reg[_head & (FV["fline"] < 0) & np.isin(reg, ["skin", "skin_shadow"])] = "face_line"
reg[_head & ((FV["brow_L"] < 0) | (FV["brow_R"] < 0)) & (FV["hair"] < 0)] = "brow"
_bz = (FV["beard"] < 0) & np.isin(reg, ["skin", "skin_shadow", "face_line"]) & ((_head | (FV["neckm"] > 0.5)))
reg[_bz] = "beard_inner"
# v4 stubble fade steps: outermost first, so each inner step paints over the outer ones (skin-family faces only, outside the
# zone, head / neck); each face takes the step of the fade field at its CENTROID (no cuts)
for _k in range(_BF_N, 0, -1):
    _fz = (FV["beard"] >= 0) & (beard_fade_field(_mfc, _k) < _BF_W * _k / _BF_N) & \
        np.isin(reg, ["skin", "skin_shadow", "face_line"] + ["beard_fade%d" % q_ for q_ in range(_k + 1, _BF_N + 1)]) & \
        (_head | (FV["neckm"] > 0.5))
    reg[_fz] = "beard_fade%d" % _k
_fcr_h = _mfc
_hair_outer = (np.linalg.norm((_fcr_h - HC) / HR, axis=1) >= HAIR_INTERIOR_R) if HAIR_INTERIOR_R is not None else np.ones(len(CF), bool)
HAIR_INTERIOR_MASK = _head & (FV["hair"] > 0) & ~_hair_outer
report["hair_interior_excluded"] = {"faces": int(HAIR_INTERIOR_MASK.sum())}
reg[_head & (FV["hair"] > 0) & _hair_outer] = "hair"
report["iso_cuts"] = {"cuts": len(cut_log), "edge_splits": int(sum(c["edge_splits"] for c in cut_log)),
                      "thin_stroke_refine_splits": REFINE, "degenerate_faces_dissolved": int(_ndeg),
                      "seconds": round(time.time() - t_, 1), "body_tris_after_cuts": nF}
print("CUTS", json.dumps(report["iso_cuts"]))
CW = transfer(CV)
BVH_CUT = BVHTree.FromPolygons(CV.tolist(), CF)
# liner proof (Wren's): the painted cut mesh seen from the front, per 5-deg ray fan, visible liner width
_lp = {}
for s in "LR":
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    rows_ = []
    for k in range(AP_N):
        th_ = math.radians(360.0 * k / AP_N)
        seen, edge_, first_ = [], None, None
        for i in range(1, int((float(EYE_AP[s].max()) + 0.008) / 0.00005)):
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
        rows_.append({"deg": 360.0 * k / AP_N, "width_mm": 0.05 * float(seen.sum())})
    _lp[s] = rows_
_all = [r for s in "LR" for r in _lp[s]]
_prof = lambda lo_, hi_: round(float(np.median([r["width_mm"] for r in _all if lo_ <= r["deg"] <= hi_])), 2)
report["liner_proof"] = {"upper_by_zone_mm_median": {"outer_0_40": _prof(0.0, 40.0), "mid_60_120": _prof(60.0, 120.0),
                                                     "inner_140_175": _prof(140.0, 175.0)},
                         "lower_lid_width_mm_median": _prof(195.0, 345.0),
                         "aperture_mm_L": {"outer": round(1000 * float(aperture(0.0, "L")), 2), "up": round(1000 * float(aperture(90.0, "L")), 2),
                                           "inner": round(1000 * float(aperture(180.0, "L")), 2), "down": round(1000 * float(aperture(270.0, "L")), 2)}}
_bw = {}
_cur = BROW_CURVE["L"][:, [0, 2]]
for tt_ in (0.05, 0.25, 0.5, 0.75, 0.95):
    i_ = int(round(tt_ * (len(_cur) - 1)))
    tg_ = unit(np.append(_cur[min(i_ + 1, len(_cur) - 1)] - _cur[max(i_ - 1, 0)], 0.0))[:2]
    nr_ = np.array([-tg_[1], tg_[0]])
    run_, best_ = 0, 0
    for k_ in range(-200, 201):
        p_ = _cur[i_] + nr_ * k_ * 0.00005
        h_ = BVH_CUT.ray_cast(Vector((float(p_[0]), -1.0, float(p_[1]))), Vector((0.0, 1.0, 0.0)), 2.0)
        if h_[0] is not None and reg[h_[2]] == "brow":
            run_ += 1; best_ = max(best_, run_)
        else:
            run_ = 0
    _bw["t%.2f" % tt_] = round(0.05 * best_, 2)
_eye_w = 1000 * float(aperture(0.0, "L") + aperture(180.0, "L"))
report["brow"] = {"BROW_W_mm": [w * 1000 for w in BROW_W], "width_mm_measured": _bw, "eye_width_mm": round(_eye_w, 1),
                  "inner_width_over_eye_width": round(_bw["t0.05"] / max(_eye_w, 1e-6), 3)}
_fa_cut = np.array([0.5 * np.linalg.norm(np.cross(CV[f[1]] - CV[f[0]], CV[f[2]] - CV[f[0]])) for f in CF])
report["regions_body_faces"] = {r: int((reg == r).sum()) for r in sorted(set(reg))}
report["beard_zone"] = {"faces": int((reg == "beard_inner").sum()), "area_cm2": round(1e4 * float(_fa_cut[reg == "beard_inner"].sum()), 1),
                        "fade_band": {"width_m": _BF_W, "steps": _BF_N, "serration_m": _BF_A, "tooth_w_m": _BF_TW,
                                      "faces": {"beard_fade%d" % k_: int((reg == "beard_fade%d" % k_).sum()) for k_ in range(1, _BF_N + 1)}}}
print("LINER", json.dumps(report["liner_proof"]), "BROW", json.dumps(report["brow"]))
print("BODYREG", json.dumps(report["regions_body_faces"]), "BEARDZONE", json.dumps(report["beard_zone"]))
