# Wren build section 4: the green hooded cloak worn open (asymmetric wrap: draped over his left shoulder + arm to the
# brass clasp, thrown back behind the pitchfork arm), ragged pointed hem, slits, sun-faded mottle, stitched PATCHES (iso-cut
# regions on the sheet + solid cross stitches), the hood lying down the back, the bunched cowl round the neck, the clasp.
t_cloak = time.time()
BVH_CLOAKP = comb_bvh({"skirt", "sash", "knot", "tie", "tieend", "pouch", "pouchflap", "pouchbutton", "sleeveroll", "bracer",
                       "bracerstrap", "bracerset", "bracergem", "bracerstud", "puff", "rivet", "cord", "pendant", "pendcap"})
Z_CT_BACK = NECK0[2] + CLOAK_TOP[0]
Z_CT_SHO = SHO["L"][2] + CLOAK_TOP[1]
Z_CT_FRONT = NECK0[2] + CLOAK_TOP[2]
_zg = np.linspace(Z_CT_BACK + 0.03, 0.05, 110)
_phg = np.arange(-170.0, 170.01, 2.0)
_PROF = np.stack([np.maximum.accumulate(radial_profile(BVH_CLOAKP, 0.0, AX_Y, ph, _zg)) for ph in _phg])


def cloak_hang(phi_back, z):
    col = np.array([np.interp(phi_back, _phg, _PROF[:, k]) for k in range(len(_zg))])
    return float(np.interp(-z, -_zg, col))


def phi_edge(tab, v):
    return float(np.interp(v, [p_[0] for p_ in tab], [p_[1] for p_ in tab]))


def cloak_ztop(phi):
    return float(np.interp(abs(phi), [0.0, 90.0, 150.0], [Z_CT_BACK, Z_CT_SHO, Z_CT_FRONT]))


cols = []
slit_at = {int(round(uf * (CLOAK_NU - 1))): hf for uf, hf in CLOAK_SLITS}
for c in range(CLOAK_NU):
    u = c / (CLOAK_NU - 1)
    if c in slit_at:
        du_ = 0.12 / (CLOAK_NU - 1)
        cols.append({"u": u - du_, "c": c, "gap": -1, "slit": slit_at[c]})
        cols.append({"u": u + du_, "c": c, "gap": 1, "slit": slit_at[c]})
    else:
        cols.append({"u": u, "c": c, "gap": 0, "slit": None})
NUc = len(cols)
vrows = np.linspace(0.0, 1.0, CLOAK_NV) ** 1.1
Vcl, UVc = [], []
for j, v in enumerate(vrows):
    pR, pL = phi_edge(CLOAK_PHI_R, v), phi_edge(CLOAK_PHI_L, v)
    for cl in cols:
        u, c = cl["u"], cl["c"]
        # ragged hem: alternating pointed teeth (hash depth) + occasional long points
        tear = CLOAK_TEAR[1] * (0.35 + 0.9 * hash01(c, 5.0)) * (1.0 if c % 2 else 0.25)
        if hash01(c, 3.0) > 0.78:
            tear = CLOAK_TEAR[0] * (0.6 + 0.4 * hash01(c, 7.0))
        if cl["gap"]:
            tear -= 0.02
        ph = -pR + u * (pR + pL)
        ph0 = -phi_edge(CLOAK_PHI_R, 0.0) + u * (phi_edge(CLOAK_PHI_R, 0.0) + phi_edge(CLOAK_PHI_L, 0.0))
        ztop = cloak_ztop(ph0)
        hem = CLOAK_HEM_Z - tear + 0.05 * smoothstep(0.75, 1.0, abs(2 * u - 1))    # the front edges ride a little higher
        z = ztop + (hem - ztop) * v
        if cl["gap"] and v > 1.0 - cl["slit"]:
            ph += cl["gap"] * 2.4 * ((v - (1.0 - cl["slit"])) / cl["slit"]) ** 1.3
        edge_k = smoothstep(0.0, 0.08, min(u, 1.0 - u))     # the front edges fold a little less
        r = cloak_hang(ph, z) + CLOAK_CLEAR + CLOAK_FLARE * v ** 1.5 + \
            CLOAK_FOLD * v ** 1.2 * edge_k * math.sin(2 * math.pi * CLOAK_FOLDS * u + 1.3 * v)
        a = math.radians(ph)
        Vcl.append([r * math.sin(a), AX_Y + r * math.cos(a), z])
        UVc.append([u, v])
Vcl = np.array(Vcl); UVc = np.array(UVc)
pairs_ = {i: cols[i]["slit"] for i in range(NUc - 1) if cols[i]["gap"] == -1}
Fcl = VP.grid_faces(NUc, CLOAK_NV, skip=lambda i, j: i in pairs_ and vrows[j] >= 1.0 - pairs_[i] - 1e-9)
Fcl = [f if np.dot(np.cross(Vcl[f[1]] - Vcl[f[0]], Vcl[f[2]] - Vcl[f[0]]),
                   np.array([Vcl[f].mean(0)[0], Vcl[f].mean(0)[1] - AX_Y, 0.0])) > 0 else f[::-1] for f in Fcl]
# metric sheet coordinates for the patches: a = arc along the row from his right front edge, b = drop down the column
Gv = Vcl.reshape(CLOAK_NV, NUc, 3)
Acl = np.concatenate([np.zeros((CLOAK_NV, 1)), np.cumsum(np.linalg.norm(np.diff(Gv, axis=1), axis=2), axis=1)], 1).reshape(-1)
Bcl = np.concatenate([np.zeros((1, NUc)), np.cumsum(np.linalg.norm(np.diff(Gv, axis=0), axis=2), axis=0)], 0).reshape(-1)


def patch_field(a, b, k):
    a0, b0, hw, hh, rot, _ = PATCHES[k]
    cr, sr = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    x = (a - a0) * cr + (b - b0) * sr
    y = -(a - a0) * sr + (b - b0) * cr
    wob = 0.0035 * np.sin(61.0 * x + 3.0 * k) + 0.0030 * np.sin(53.0 * y + 1.7 * k)   # hand-cut, not ruled
    q = np.stack([np.abs(x) - hw, np.abs(y) - hh], -1)
    rr = 0.006
    return np.linalg.norm(np.maximum(q + rr, 0.0), axis=-1) + np.minimum(np.maximum(q[..., 0], q[..., 1]) + rr, 0.0) - rr + wob


def worn_field(a, b):
    s_ = CLOAK_WORN[1]
    n_ = (np.sin(a / s_ * 6.3 + 1.1) * np.sin(b / s_ * 4.1 + 0.4) + 0.6 * np.sin((a + b) / s_ * 9.7 + 2.3) *
          np.sin((a - 0.6 * b) / s_ * 7.9)) * 0.5 + 0.5
    return (CLOAK_WORN[0] - n_) * 0.05          # < 0 = worn / faded (scaled to metres-ish so the cut snaps sanely)


def cut_sheet(V, F, fields, cuts, snap=CUT_SNAP):
    """the body's iso-cut machinery on another sheet: split edges / connect faces along field = tau, carry every field."""
    bm2 = bmesh.new()
    for p in V:
        bm2.verts.new(p)
    bm2.verts.ensure_lookup_table()
    for f in F:
        bm2.faces.new([bm2.verts[i] for i in f])
    bm2.verts.index_update()                          # new verts carry index -1 until updated
    lay = {k: bm2.verts.layers.float.new(k) for k in fields}
    for vv in bm2.verts:
        for k, arr in fields.items():
            vv[lay[k]] = float(arr[vv.index])
    for key, tau in cuts:
        L = lay[key]
        side = lambda vv: 0 if abs(vv[L] - tau) <= 1e-7 else (1 if vv[L] > tau else -1)
        for e in bm2.edges:
            a_, b_ = e.verts
            if side(a_) * side(b_) < 0:
                tt = (tau - a_[L]) / (b_[L] - a_[L])
                if tt < snap:
                    a_[L] = tau
                elif tt > 1 - snap:
                    b_[L] = tau
        for e in [e for e in bm2.edges if side(e.verts[0]) * side(e.verts[1]) < 0]:
            a_, b_ = e.verts
            tt = (tau - a_[L]) / (b_[L] - a_[L])
            vals = {k: a_[lay[k]] * (1 - tt) + b_[lay[k]] * tt for k in lay}
            _, nv = bmesh.utils.edge_split(e, a_, tt)
            for k in lay:
                nv[lay[k]] = vals[k]
            nv[L] = tau
        pr = []
        for f in bm2.faces:
            sd = [side(vv) for vv in f.verts]
            if 1 in sd and -1 in sd:
                cv = [vv for vv, s_ in zip(f.verts, sd) if s_ == 0]
                if len(cv) == 2:
                    pr.append(cv)
        for cv in pr:
            try:
                bmesh.ops.connect_verts(bm2, verts=cv)
            except Exception:
                pass
    bmesh.ops.triangulate(bm2, faces=[f for f in bm2.faces if len(f.verts) > 3])
    bm2.verts.index_update()
    Vo = np.array([vv.co[:] for vv in bm2.verts])
    Fo = [[vv.index for vv in f.verts] for f in bm2.faces]
    Fld = {k: np.array([vv[lay[k]] for vv in bm2.verts]) for k in lay}
    bm2.free()
    return Vo, Fo, Fld


_fields = {"u": UVc[:, 0], "v": UVc[:, 1], "a": Acl, "b": Bcl, "worn": worn_field(Acl, Bcl)}
for k in range(len(PATCHES)):
    _fields["p%d" % k] = patch_field(Acl, Bcl, k)
Vcc, Fcc, FLD = cut_sheet(Vcl, Fcl, _fields, [("p%d" % k, 0.0) for k in range(len(PATCHES))] + [("worn", 0.0)])
Rcc = []
for f in Fcc:
    reg_ = "cloak"
    if np.mean(FLD["worn"][f]) < 0:
        reg_ = "cloak_worn"
    for k in range(len(PATCHES)):
        if np.mean(FLD["p%d" % k][f]) < 0:
            reg_ = "patch_" + PATCHES[k][5]
    Rcc.append(reg_)
V_, F_, R_ = VP.solidify(Vcc, Fcc, CLOAK_T * 0.5, CLOAK_T * 0.5, Rcc, "cloak_lining", "cloak")
UVcc = np.stack([FLD["u"], FLD["v"]], 1)
add_part("cloak", V_, F_, R_, w="cape", uv=np.vstack([UVcc, UVcc]))
CLOAK_V, CLOAK_F = V_, F_
BVH_CLOAK = BVHTree.FromPolygons(V_.tolist(), F_)
NCL = VP.vertex_normals(Vcc, Fcc)
if float(np.mean(np.einsum("ij,ij->i", NCL, np.stack([Vcc[:, 0], Vcc[:, 1] - AX_Y, np.zeros(len(Vcc))], 1)))) < 0:
    NCL = -NCL
# ---- cross stitches round every patch (small solid bars straddling the patch edge on the outer face)
KD_CL = __import__("mathutils").kdtree.KDTree(len(Vcc))
for i, p_ in enumerate(Vcc):
    KD_CL.insert(p_, i)
KD_CL.balance()
Vst, Fst, Rst, UVst = [], [], [], []
STITCH_N = {}
for k in range(len(PATCHES)):
    on = np.nonzero(np.abs(FLD["p%d" % k]) < 1e-6)[0]
    if len(on) < 4:
        STITCH_N[k] = 0
        continue
    a0, b0 = PATCHES[k][0], PATCHES[k][1]
    ang = np.arctan2(FLD["b"][on] - b0, FLD["a"][on] - a0)
    on = on[np.argsort(ang)]
    P_ = Vcc[on]
    seg = np.linalg.norm(np.diff(np.vstack([P_, P_[:1]]), axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    n_st = max(4, int(arc[-1] / STITCH[0]))
    cen3 = Vcc[on].mean(0)
    cnt = 0
    for m in range(n_st):
        s_ = arc[-1] * (m + 0.5) / n_st
        i_ = int(np.searchsorted(arc, s_, side="right") - 1) % len(on)
        j_ = (i_ + 1) % len(on)
        t_ = (s_ - arc[i_]) / max(seg[i_], 1e-9)
        p = P_[i_] * (1 - t_) + P_[j_] * t_
        nrm = unit(NCL[on[i_]] * (1 - t_) + NCL[on[j_]] * t_)
        along = unit(P_[j_] - P_[i_])
        across = unit(np.cross(nrm, along))
        if float(np.dot(across, p - cen3)) < 0:
            across = -across
        c_ = p + nrm * (CLOAK_T * 0.5 + STITCH[3] * 0.5 + 0.0002)
        hx, hy, hz = STITCH[1] * 0.5, STITCH[3] * 0.5, STITCH[2] * 0.5
        o = len(Vst)
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    Vst.append(c_ + across * hx * sx + nrm * hy * sy + along * hz * sz)
        for f in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            Fst.append([o + i for i in f]); Rst.append("stitch")
        uv_ = UVcc[on[i_]] * (1 - t_) + UVcc[on[j_]] * t_
        UVst += [uv_] * 8
        cnt += 1
    STITCH_N[k] = cnt
Vst = np.array(Vst)
Fst2 = []                                             # each bar is its own closed box (8 verts): orient per box
for b_ in range(len(Fst) // 6):
    cb_ = Vst[b_ * 8:(b_ + 1) * 8].mean(0)
    for f in Fst[b_ * 6:(b_ + 1) * 6]:
        nn = np.cross(Vst[f[1]] - Vst[f[0]], Vst[f[2]] - Vst[f[0]])
        Fst2.append(f if np.dot(nn, Vst[f].mean(0) - cb_) > 0 else f[::-1])
add_part("stitches", Vst, Fst2, Rst, w="cape", uv=np.array(UVst))

# ---- hood lying down the back (a pointed flap over the upper back, bulged, on the cloak)
HD = HOOD
BVH_HOODP = BVHTree.FromPolygons(np.vstack([CV, CLOAK_V]).tolist(), [list(f) for f in CF] + [[i + len(CV) for i in f] for f in CLOAK_F])
Vh, UVh = [], []
z_h0 = NECK0[2] + HD["top_dz"]
for j in range(HD["nv"]):
    t = j / (HD["nv"] - 1)
    for i in range(HD["nu"]):
        s_ = -1.0 + 2.0 * i / (HD["nu"] - 1)
        hw = HD["w"] * (1.0 - t) ** 0.75 * (1.0 - 0.15 * t) + 0.004
        x = s_ * hw
        z = z_h0 - HD["len"] * t
        h = BVH_HOODP.ray_cast(Vector((x, AX_Y + 0.8, z)), Vector((0.0, -1.0, 0.0)), 1.6)
        y0 = h[0][1] if h[0] is not None else AX_Y + 0.1
        bul = HD["bulge"] * (1.0 - s_ * s_) ** 0.7 * math.sin(math.pi * min(0.15 + t * 1.1, 1.0))
        Vh.append([x, y0 + HD["clear"] + bul, z])
        _, ii, _ = KD_CL.find(Vector((x, y0, z)))
        UVh.append(UVcc[ii])
Vh = np.array(Vh)
Fh = VP.grid_faces(HD["nu"], HD["nv"])
Fh = [f if np.cross(Vh[f[1]] - Vh[f[0]], Vh[f[2]] - Vh[f[0]])[1] > 0 else f[::-1] for f in Fh]
V_, F_, R_ = VP.solidify(Vh, Fh, CLOAK_T, 0.0, "cloak", "cloak_lining", "cloak")
add_part("hood", V_, F_, R_, w="cape", uv=np.vstack([UVh, UVh]))

# ---- cowl: the hood's bunched rim round the neck -- a folded fabric collar (up from the cloak's top edge, rolled over
# and down onto the shoulders), lumpy, open at the front; the brass clasp on his left end
CW_ = COWL
BVH_COWLP = BVHTree.FromPolygons(np.vstack([CV, CLOAK_V]).tolist(), [list(f) for f in CF] + [[i + len(CV) for i in f] for f in CLOAK_F])
_prof = np.array(CW_["fold"])                       # (outward m, up m) per row, at full size
Vk, Rk = [], []
ncol = CW_["cols"]
for i in range(ncol):
    phf = CW_["gap_deg"] * 0.5 + (360.0 - CW_["gap_deg"]) * i / (ncol - 1)     # from the front, via his left, round the back
    phf = phf if phf <= 180.0 else phf - 360.0
    ab = abs(phf)
    zb = float(np.interp(ab, [CW_["gap_deg"] * 0.5, 90.0, 180.0], [NECK0[2] - 0.042, SHO["L"][2] + 0.030, NECK0[2] - 0.006]))
    size = float(np.interp(ab, [CW_["gap_deg"] * 0.5, CW_["gap_deg"] * 0.5 + 25.0, 110.0, 180.0], [0.45, 0.80, 0.95, 1.0]))
    rb = 180.0 - phf
    r0 = float(radial_profile(BVH_COWLP, 0.0, NECK0[1], rb, [zb - 0.01, zb, zb + 0.01]).max()) + CW_["clear"]
    a_ = math.radians(rb)
    d_ = np.array([math.sin(a_), math.cos(a_), 0.0])
    lump = 1.0 + CW_["lump"] * (math.sin(0.61 * i + 0.3) * 0.6 + (hash01(i, 11.0) - 0.5))
    for j, (dr, dz) in enumerate(_prof):
        k_ = size * (lump if j >= 2 else 1.0)
        Vk.append(np.array([0.0, NECK0[1], zb]) + d_ * (r0 + dr * k_) + np.array([0.0, 0.0, dz * size]))
Vk = np.array(Vk)
nr_ = len(_prof)
Fk = []
for i in range(ncol - 1):
    for j in range(nr_ - 1):
        Fk.append([i * nr_ + j, (i + 1) * nr_ + j, (i + 1) * nr_ + j + 1, i * nr_ + j + 1])
_ax = np.array([0.0, NECK0[1], 0.0])
Fk = [f if np.dot(np.cross(Vk[f[1]] - Vk[f[0]], Vk[f[2]] - Vk[f[0]]),
                  np.array([Vk[f].mean(0)[0], Vk[f].mean(0)[1] - NECK0[1], 0.0]) + np.array([0.0, 0.0, 0.3 * 0.0])) > 0 else f[::-1]
      for f in Fk]
V_, F_, R_ = VP.solidify(Vk, Fk, CW_["t"], 0.0, "cloak", "cloak_lining", "cloak")
add_part("cowl", V_, F_, R_, w="collar")
_c0 = Vk[3]                                         # the left end, on the roll
_cd = unit(np.array([_c0[0], _c0[1] - NECK0[1], 0.0]) + np.array([0.0, -1.2, 0.45]))
CLASP_C = _c0 + _cd * 0.004
V_, F_, R_ = VP.lathe([(0.0, 0.006), (CLASP_R * 0.45, 0.0062), (CLASP_R * 0.62, 0.0048), (CLASP_R * 0.72, 0.0058),
                       (CLASP_R * 0.92, 0.0040), (CLASP_R, 0.0015), (CLASP_R * 0.9, -0.002), (0.0, -0.002)],
                      ["brass", "brass_dark", "brass", "brass", "brass", "brass_dark", "brass_dark"], 12, CLASP_C, _cd,
                      up_hint=(0, 0, 1))
add_part("clasp", V_, F_, R_, w="rigid_transfer")
CLOAK_INFO = {"columns": NUc, "rows": CLOAK_NV, "slits": len(pairs_), "top_z": [round(Z_CT_BACK, 4), round(Z_CT_SHO, 4),
              round(Z_CT_FRONT, 4)], "hem_z": CLOAK_HEM_Z, "patches": len(PATCHES), "stitches_per_patch": STITCH_N,
              "patch_faces": {r: int(sum(1 for x in Rcc if x == r)) for r in ("patch_a", "patch_b", "patch_c", "cloak_worn")},
              "row_arc_m": [round(float(Acl.reshape(CLOAK_NV, NUc)[0, -1]), 3), round(float(Acl.reshape(CLOAK_NV, NUc)[-1, -1]), 3)],
              "drop_m": round(float(Bcl.max()), 3), "clasp": CLASP_C.round(4).tolist(), "seconds": round(time.time() - t_cloak, 1)}
print("CLOAK", json.dumps(CLOAK_INFO))
