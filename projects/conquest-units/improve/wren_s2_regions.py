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
FIELDS["lip"] = (X / LIP[0]) ** 2 + ((Z - Z_LIP) / np.where(Z > Z_LIP, LIP[1], LIP[2])) ** 2 - 1.0
if SEAM is not None:
    # v2 mouth line: a thin painted line ON the sealed seam (the sheet's closed mouth), tapering to the corners
    _sx0, _sx1 = float(SEAM["xs"].min()), float(SEAM["xs"].max())
    _su = np.clip((X - _sx0) / max(_sx1 - _sx0, 1e-6), 0.0, 1.0)
    _shw = 0.5 * MOUTH_LINE[0] * (MOUTH_LINE[1] + (1.0 - MOUTH_LINE[1]) * np.sin(np.pi * _su) ** 0.6)
    _szc = np.interp(X, SEAM["xs"], SEAM["zc"])
    FIELDS["mline"] = np.where((X >= _sx0) & (X <= _sx1), np.abs(Z - _szc) - _shw, 1.0)


    def mline_field(P_):
        u_ = np.clip((P_[:, 0] - _sx0) / max(_sx1 - _sx0, 1e-6), 0.0, 1.0)
        hw_ = 0.5 * MOUTH_LINE[0] * (MOUTH_LINE[1] + (1.0 - MOUTH_LINE[1]) * np.sin(np.pi * u_) ** 0.6)
        return np.where((P_[:, 0] >= _sx0) & (P_[:, 0] <= _sx1), np.abs(P_[:, 2] - np.interp(P_[:, 0], SEAM["xs"], SEAM["zc"])) - hw_, 1.0)
# ---- eyes: the lathe eyeball (rendered) + the lid-edge aperture per 5 deg (front rays: first skin hit in front of it)
BN = VP.vertex_normals(BV, BF)
if float(np.mean(np.einsum("ij,ij->i", BN, BV - BV.mean(0)))) < 0:
    BN = -BN
EYE_MESH, EYE_AP = {}, {}
AP_STEP, AP_N = 0.0001, 72
for s in "LR":
    e = EYE[s]
    angs = [0.0, EYE_PUPIL_DEG, EYE_PUPIL_DEG + 4, EYE_IRIS_DEG, 60.0, 90.0, 125.0, 155.0, 180.0]
    prof = [(e["r"] * math.sin(math.radians(a)), e["r"] * math.cos(math.radians(a))) for a in angs]
    regs = ["eye_pupil", "eye_iris", "eye_iris", "eye_sclera", "eye_sclera", "eye_sclera", "eye_sclera", "eye_sclera"]
    EYE_MESH[s] = VP.lathe(prof, regs, EYE_SEG, e["c"], (0.0, -1.0, 0.0), up_hint=(0, 0, 1))


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


def liner_w(ang_):
    ang_ = np.asarray(ang_, float)
    up_ = smoothstep(-0.25, 0.25, np.sin(np.radians(ang_)))
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
    REFINE["mline"] = refine_thin("mline", mline_field)
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
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
reg[_head & (FV["lip"] < 0) & (FV["y"] < Y_LIP + 0.03) & (np.abs(FV["z"] - Z_LIP) < 0.03)] = "lips"
_mx = np.arange(-LIP[0] - 0.002, LIP[0] + 0.0021, 0.0005)
_mfront = []
for x_ in _mx:
    ys_ = [BVH_BODY.ray_cast(Vector((float(x_), -0.6, float(z_))), Vector((0.0, 1.0, 0.0)), 1.0)[0]
           for z_ in np.arange(Z_SLIT - 0.008, Z_SLIT + 0.008, 0.0005)]
    _mfront.append(min(h[1] for h in ys_ if h is not None))
_mfront = np.array(_mfront)
if SEAM is None:                                   # v1 rule: lip-zone skin recessed behind the front = the mouth
    for fi in np.nonzero(reg == "lips")[0]:
        c_ = CV[CF[fi]].mean(0)
        if c_[1] > float(np.interp(c_[0], _mx, _mfront)) + MOUTH_IN_D:
            reg[fi] = "mouth"
else:                                              # v2: the thin line on the sealed seam
    reg[_head & (FV["mline"] < 0) & (FV["y"] < Y_LIP + 0.012) & (np.abs(FV["z"] - Z_LIP) < 0.015)] = "mouth"
reg[_head & ((FV["eye_L"] < 0) | (FV["eye_R"] < 0))] = "liner"
reg[_head & ((FV["brow_L"] < 0) | (FV["brow_R"] < 0)) & (FV["hair"] < 0)] = "brow"
reg[_head & (FV["hair"] > 0)] = "hair"
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
        for i in range(1, 300):
            rho = i * 0.00005
            who, h_ = front_first_hit(s, BVH_CUT, BVH_EYE[s], c_[0] + sg_ * rho * math.cos(th_), c_[2] + rho * math.sin(th_))
            if who == "skin":
                r_ = reg[h_[2]]
                if edge_ is None:
                    edge_, first_ = rho, r_
                seen.append(r_ == "liner")
            elif edge_ is not None:
                seen.append(False)
        seen = np.array(seen, bool)
        rows_.append({"deg": 360.0 * k / AP_N, "width_mm": 0.05 * float(seen.sum()), "starts_at_lid_edge": first_ == "liner"})
    _lp[s] = rows_
_all = [r for s in "LR" for r in _lp[s]]
_up = [r["width_mm"] for r in _all if 15.0 <= r["deg"] <= 165.0]
_lo = [r["width_mm"] for r in _all if 195.0 <= r["deg"] <= 345.0]
report["liner_proof"] = {"widths_mm_target": {"upper": LINER_W[0] * 1000, "lower": LINER_W[1] * 1000},
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
