# Elias build section 4 (the cloak machinery of wren_s4_cloak.py, re-shaped): the navy scholar MANTLE -- sleeveless, worn
# open (close at the chest, falling open at the sides so the robe / belt / pouches / scroll tubes show), hanging from the
# shoulders over the trunk and legs only (the arms stay outside it), ragged-ish hem, gold trim band along the hem and the
# front edges, gold diamond motifs above the hem, the gold TRIDENT crest raised on the back; the short CAPELET over the
# shoulders and upper arms (rows by arc length down each column, so the shoulder shelf is never one stretched face), its
# scalloped hem gold-trimmed; the gold trident BROOCH with the teal gem at the chest; the brown SATCHEL worn on the back
# (buckled flap, scrolls poking out) on its cross-body strap over his right shoulder.
t_mantle = time.time()
_MANTLE_HOST = {"robeskirt", "belt", "buckle", "buckleinset", "pouch", "pouchflap", "pouchbutton", "scrolltube", "scrollring",
                "bootshaft", "bootcuff", "boottrim", "cravatband", "cravatknot", "cravatfall"}
_legF = [f for f, n in zip(BF, fdomn) if n.startswith(("thigh", "calf", "foot", "ball"))]
_hv, _hf = [BV], [list(f) for f in TRUNK_F + _legF]
_o = len(BV)
for p in PARTS:
    if p["name"].split(".")[0] in _MANTLE_HOST:
        _hv.append(p["V"]); _hf += [[i + _o for i in f] for f in p["F"]]; _o += len(p["V"])
BVH_MANTLEP = BVHTree.FromPolygons(np.vstack(_hv).tolist(), _hf)
Z_MT_BACK = NECK0[2] + MANTLE_TOP[0]
Z_MT_SHO = SHO["L"][2] + MANTLE_TOP[1]
Z_MT_FRONT = NECK0[2] + MANTLE_TOP[2]
_zg = np.linspace(Z_MT_BACK + 0.03, 0.04, 120)
_phg = np.arange(-178.0, 178.01, 2.0)
_PROF = np.stack([np.maximum.accumulate(radial_profile(BVH_MANTLEP, 0.0, AX_Y, ph, _zg)) for ph in _phg])


def mantle_hang(phi_back, z):
    col = np.array([np.interp(phi_back, _phg, _PROF[:, k]) for k in range(len(_zg))])
    return float(np.interp(-z, -_zg, col))


def mantle_edge(v):
    return float(np.interp(v, [p_[0] for p_ in MANTLE_FRONT], [p_[1] for p_ in MANTLE_FRONT]))


def mantle_ztop(phi):
    return float(np.interp(abs(phi), [0.0, 90.0, 150.0], [Z_MT_BACK, Z_MT_SHO, Z_MT_FRONT]))


vrows = np.linspace(0.0, 1.0, MANTLE_NV) ** 1.1
Vm, UVm = [], []
for j, v in enumerate(vrows):
    pe = mantle_edge(v)
    for c in range(MANTLE_NU):
        u = c / (MANTLE_NU - 1)
        tear = MANTLE_TEAR[1] * (0.35 + 0.9 * hash01(c, 5.0)) * (1.0 if c % 2 else 0.30)
        if hash01(c, 3.0) > 0.80:
            tear = MANTLE_TEAR[0] * (0.6 + 0.4 * hash01(c, 7.0))
        ph = -pe + u * 2.0 * pe
        ph0 = -mantle_edge(0.0) + u * 2.0 * mantle_edge(0.0)
        ztop = mantle_ztop(ph0)
        hem = MANTLE_HEM_Z - tear + 0.03 * smoothstep(0.80, 1.0, abs(2 * u - 1))
        z = ztop + (hem - ztop) * v
        edge_k = smoothstep(0.0, 0.08, min(u, 1.0 - u))
        r = mantle_hang(ph, z) + MANTLE_CLEAR + MANTLE_FLARE * v ** 1.5 + \
            MANTLE_FOLD * v ** 1.2 * edge_k * math.sin(2 * math.pi * MANTLE_FOLDS * u + 1.3 * v)
        a = math.radians(ph)
        Vm.append([r * math.sin(a), AX_Y + r * math.cos(a), z])
        UVm.append([u, v])
Vm = np.array(Vm); UVm = np.array(UVm)
Fm = VP.grid_faces(MANTLE_NU, MANTLE_NV)
Fm = [f if np.dot(np.cross(Vm[f[1]] - Vm[f[0]], Vm[f[2]] - Vm[f[0]]), np.array([Vm[f].mean(0)[0], Vm[f].mean(0)[1] - AX_Y, 0.0])) > 0
      else f[::-1] for f in Fm]
Gv = Vm.reshape(MANTLE_NV, MANTLE_NU, 3)
Am = np.concatenate([np.zeros((MANTLE_NV, 1)), np.cumsum(np.linalg.norm(np.diff(Gv, axis=1), axis=2), axis=1)], 1)
Bm = np.concatenate([np.zeros((1, MANTLE_NU)), np.cumsum(np.linalg.norm(np.diff(Gv, axis=0), axis=2), axis=0)], 0)
_dh = (Bm[-1:, :] - Bm).reshape(-1)                         # distance UP from the hem along the column
_de = np.minimum(Am, Am[:, -1:] - Am).reshape(-1)           # distance from the nearer front edge along the row
_trim = np.minimum(_dh - MANTLE_TRIM[0], _de - MANTLE_TRIM[1])


def cut_sheet(V, F, fields, cuts, snap=CUT_SNAP):
    """the body's iso-cut machinery on another sheet (Wren's): split edges / connect faces along field = tau."""
    bm2 = bmesh.new()
    for p in V:
        bm2.verts.new(p)
    bm2.verts.ensure_lookup_table()
    for f in F:
        bm2.faces.new([bm2.verts[i] for i in f])
    bm2.verts.index_update()
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


Vmc, Fmc, FLD = cut_sheet(Vm, Fm, {"u": UVm[:, 0], "v": UVm[:, 1], "trim": _trim, "dh": _dh}, [("trim", 0.0)])
Rmc = ["mantle_trim" if np.mean(FLD["trim"][f]) < 0 else "mantle" for f in Fmc]
V_, F_, R_ = VP.solidify(Vmc, Fmc, MANTLE_T * 0.5, MANTLE_T * 0.5, Rmc, "mantle_lining", "mantle_trim")
add_part("mantle", V_, F_, R_, w="coat")
MANTLE_V, MANTLE_F = V_, F_
BVH_MANTLE = BVHTree.FromPolygons(V_.tolist(), F_)
NMC = VP.vertex_normals(Vmc, Fmc)
if float(np.mean(np.einsum("ij,ij->i", NMC, np.stack([Vmc[:, 0], Vmc[:, 1] - AX_Y, np.zeros(len(Vmc))], 1)))) < 0:
    NMC = -NMC
_bvh_mc = BVHTree.FromPolygons(Vmc.tolist(), Fmc)


def on_mantle(p, dirn):
    """the mantle's OUTER surface point along -dirn from outside p (+ its outward normal)."""
    o_ = np.asarray(p, float) + dirn * 0.4
    h_ = BVH_MANTLE.ray_cast(Vector(o_), Vector(-dirn), 0.8)
    if h_[0] is None:
        return np.asarray(p, float), dirn
    return np.array(h_[0]), unit(np.array(h_[1]))


# ---- the gold diamond motifs above the hem band
MOTIF_N = 0
for k in range(MANTLE_MOTIFS[0]):
    u_ = 0.06 + 0.88 * (k + 0.5) / MANTLE_MOTIFS[0]
    c_ = int(round(u_ * (MANTLE_NU - 1)))
    col_ = Gv[:, c_]
    dh_col = Bm[-1, c_] - Bm[:, c_]
    target_ = MANTLE_TRIM[0] + MANTLE_MOTIFS[2]
    j_ = int(np.argmin(np.abs(dh_col - target_)))
    p_ = col_[j_]
    rad_ = unit(np.array([p_[0], p_[1] - AX_Y, 0.0]))
    q_, n_ = on_mantle(p_, rad_)
    V_, F_, R_ = VP.crystal(q_ + n_ * 0.0012, n_, MANTLE_MOTIFS[1], 0.0018, 0.0008, n=4, up_hint=(0.0, 0.0, 1.0), region="brass",
                            su=1.45, phase=0.0)
    add_part("motif.%d" % k, V_, F_, R_, w="coat")
    MOTIF_N += 1

# ---- the CAPELET: columns round the neck / shoulders, rows at equal ARC LENGTH down each column's draped profile
CP = CAPELET
_CAP_HOST = {"mantle", "cravatband", "cravatknot"}
BVH_CAPP = comb_bvh(_CAP_HOST, body=(BV, [f for f, n in zip(BF, fdomn) if n != "head"]))
_zc0 = NECK0[2] + 0.012
_zcg = np.linspace(_zc0, SHO["L"][2] - 0.30, 160)
Vc, Uc = [], []
for c in range(CP["nu"]):
    u = c / (CP["nu"] - 1)
    ph = -CP["front"] + u * 2.0 * CP["front"]
    ztop_ = float(np.interp(abs(ph), [0.0, 90.0, CP["front"]], [_zc0, NECK0[2] + 0.006, NECK0[2] - 0.030]))
    zz_ = _zcg[_zcg <= ztop_ + 1e-9]
    rr_ = np.maximum.accumulate(radial_profile(BVH_CAPP, 0.0, NECK0[1], ph, zz_)) + CP["clear"]
    seg_ = np.hypot(np.diff(rr_), np.diff(zz_))
    arc_ = np.concatenate([[0.0], np.cumsum(seg_)])
    scal_ = CP["scallop"][0] * (np.abs(math.sin(math.pi * CP["scallop"][1] * u)) ** 0.5 - 0.5)
    D_ = CP["drop"] + scal_ - 0.02 * smoothstep(0.85, 1.0, abs(2 * u - 1))
    for j in range(CP["nv"]):
        t_ = j / (CP["nv"] - 1)
        s_ = t_ * D_ + (NECK0[2] + 0.04 - ztop_) * 0.0
        rr1_ = float(np.interp(s_, arc_, rr_)) + CP["flare"] * t_ ** 1.6
        z1_ = float(np.interp(s_, arc_, zz_))
        a = math.radians(ph)
        Vc.append([rr1_ * math.sin(a), NECK0[1] + rr1_ * math.cos(a), z1_])
        Uc.append([u, t_, D_ - s_])
Vc = np.array(Vc); Uc = np.array(Uc)
_nvc, _nuc = CP["nv"], CP["nu"]
Fc = []
for j in range(_nvc - 1):
    for c in range(_nuc - 1):
        Fc.append([c * _nvc + j, (c + 1) * _nvc + j, (c + 1) * _nvc + j + 1, c * _nvc + j + 1])
Fc = [f if np.dot(np.cross(Vc[f[1]] - Vc[f[0]], Vc[f[2]] - Vc[f[0]]), np.array([Vc[f].mean(0)[0], Vc[f].mean(0)[1] - NECK0[1], 0.0])) > 0
      else f[::-1] for f in Fc]
Vcc, Fcc, FLDc = cut_sheet(Vc, Fc, {"dhem": Uc[:, 2] - CP["trim"]}, [("dhem", 0.0)])
Rcc = ["capelet_trim" if np.mean(FLDc["dhem"][f]) < 0 else "capelet" for f in Fcc]
V_, F_, R_ = VP.solidify(Vcc, Fcc, CP["t"] * 0.5, CP["t"] * 0.5, Rcc, "mantle_lining", "capelet_trim")
add_part("capelet", V_, F_, R_, w="transfer")
CAPELET_V, CAPELET_F = V_, F_
CAP_HEM_Z_BACK = float(Vc[(_nuc // 2) * _nvc + _nvc - 1, 2])

# ---- the trident crest raised on the mantle back (below the capelet hem)
_crz = CAP_HEM_Z_BACK - CREST["z_below_capelet"]
_cr_o, _ = on_mantle(np.array([0.0, AX_Y, _crz]), np.array([0.0, 1.0, 0.0]))
for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.trident_strokes(CREST["h"]), _cr_o, np.array([-1.0, 0.0, 0.0]),
                                                         np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0]), CREST["w"], 0.0010,
                                                         "brass", project=lambda p_: on_mantle(p_, np.array([0.0, 1.0, 0.0])))):
    add_part("crest.%d" % k, Vk_, Fk_, Rk_, w="coat")

# ---- the BROOCH at the chest: a gold plate on the capelet / robe front, the trident raised on it, the teal gem above
BVH_FRONT = comb_bvh({"capelet", "mantle", "cravatknot", "cravatfall", "cravatband"})
_bz = NECK0[2] - BROOCH["drop"]
_bh = BVH_FRONT.ray_cast(Vector((0.0, -0.8, _bz)), Vector((0.0, 1.0, 0.0)), 1.5)
_bn = unit(np.array(_bh[1])); _bn = _bn if _bn[1] < 0 else -_bn
BROOCH_C = np.array(_bh[0]) + _bn * 0.003
_br = BROOCH["r"]
V_, F_, R_ = VP.lathe([(0.0, 0.0030), (_br * 0.80, 0.0030), (_br * 0.92, 0.0042), (_br, 0.0026), (_br * 0.96, 0.0), (0.0, 0.0)],
                      ["brass", "brass_dark", "brass", "brass", "brass_dark"], 16, BROOCH_C, _bn, up_hint=(0, 0, 1))
add_part("brooch", V_, F_, R_, w="rigid_transfer")
_bex = unit(np.cross([0.0, 0.0, 1.0], _bn)); _bey = unit(np.cross(_bn, _bex))
if _bex[0] < 0:
    _bex = -_bex
for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.trident_strokes(BROOCH["crest_h"]), BROOCH_C + _bn * 0.0042 - _bey * 0.002,
                                                         _bex, _bey, _bn, 0.0011, 0.0007, "brass_dark")):
    add_part("broochcrest.%d" % k, Vk_, Fk_, Rk_, w="rigid_transfer")
V_, F_, R_ = VP.gem(BROOCH_C + _bn * 0.0045 + _bey * (_br * 0.55), _bn, BROOCH["gem"][0], BROOCH["gem"][1], n=8, region="crystal")
add_part("broochgem", V_, F_, R_, w="rigid_transfer")

# ---- the SATCHEL on the back + its cross-body strap (over his right shoulder, across the chest, down to his left hip)
SA = SATCHEL
_strap_host = {"mantle", "capelet", "cravatband", "cravatknot", "cravatfall", "brooch", "broochgem", "robeskirt", "belt", "buckle",
               "pouch", "pouchflap", "scrolltube", "crest", "motif"}
BVH_STRAPP = comb_bvh(_strap_host, body=(BV, [f for f, n, a in zip(BF, fdomn, is_arm_f) if n != "head" and not a]))   # (the
#                                                     arms hang outside the strap: their faces would catch the side rays)
_S_top = np.array([SHO["R"][0] * 0.55, AX_Y, SHO["R"][2] + 0.08])
_S_low = np.array([0.20, AX_Y, HIP["L"][2] + 0.05])
_sc = 0.5 * (_S_top + _S_low)                               # the strap plane: through both anchors, containing the front-
_sn = unit(np.cross([0.0, 1.0, 0.0], _S_top - _S_low))      #   back axis (it crosses the chest and the back diagonally)
_se1 = unit(_S_top - _sc); _se2 = np.cross(_sn, _se1)
Cs, Ns = [], []
for k in range(64):
    ps_ = 2 * math.pi * k / 64
    d_ = math.cos(ps_) * _se1 + math.sin(ps_) * _se2
    h_ = BVH_STRAPP.ray_cast(Vector(_sc + d_ * 0.7), Vector(-d_), 0.7)
    if h_[0] is None:
        continue
    n_ = unit(np.array(h_[1]))
    n_ = n_ if float(n_ @ d_) > 0 else -n_
    Cs.append(np.array(h_[0]) + d_ * (SA["strap_t"] + 0.0025)); Ns.append(d_)
Cs = np.array(Cs); Ns = np.array(Ns)
for _ in range(3):                                          # a smoothed loop (no snagging on the brooch / folds)
    Cs = 0.5 * Cs + 0.25 * (np.roll(Cs, 1, 0) + np.roll(Cs, -1, 0))
_ring = np.vstack([Cs, Cs[:1]])
V_, F_, R_ = VP.ribbon(_ring, np.vstack([Ns, Ns[:1]]), SA["strap_w"] * 0.5, SA["strap_t"], region="strap")
add_part("satchelstrap", V_, F_, R_, w="transfer")
# the bag hangs from the strap's lowest point behind his left hip, on the mantle
_pa = math.radians(SA["phi"])
_pd = np.array([math.sin(_pa), math.cos(_pa), 0.0])         # azimuth from the BACK centre toward his left
_jb = int(np.argmin(np.linalg.norm((Cs - _sc)[:, :2] / np.maximum(np.linalg.norm((Cs - _sc)[:, :2], axis=1), 1e-9)[:, None] - _pd[:2], axis=1)))
hx_, hy_, hz_ = SA["size"]
_bzc = float(Cs[_jb, 2]) - hz_ * 0.70 + SA["z_off"]
_br_ = float(radial_profile(BVH_STRAPP, 0.0, AX_Y, math.degrees(_pa), np.linspace(_bzc - hz_, _bzc + hz_, 9)).max())
SATCHEL_C = np.array([0.0, AX_Y, _bzc]) + _pd * (_br_ + hy_ + 0.004)
_pt = np.cross([0.0, 0.0, 1.0], _pd)
V_, F_, R_ = VP.rounded_box(SATCHEL_C, _pt, _pd, [0.0, 0.0, 1.0], hx_, hy_, hz_, nr=3, rows=6, bulge=0.10, region="satchel")
add_part("satchel", V_, F_, R_, w="rigid_transfer")
_fc = SATCHEL_C + _pd * (hy_ * 0.96) + np.array([0.0, 0.0, hz_ * (1.0 - SA["flap"])])
V_, F_, R_ = VP.rounded_box(_fc, _pt, _pd, [0.0, 0.0, 1.0], hx_ * 1.04, 0.0034, hz_ * SA["flap"], nr=2, rows=4, bulge=0.04,
                            region="satchel_flap")
add_part("satchelflap", V_, F_, R_, w="rigid_transfer")
_bk = _fc + _pd * 0.0042 - np.array([0.0, 0.0, hz_ * SA["flap"] * 0.70])
V_, F_, R_ = VP.rounded_box(_bk, _pt, _pd, [0.0, 0.0, 1.0], 0.012, 0.0022, 0.009, nr=1, rows=3, bulge=0.0, region="brass")
add_part("satchelbuckle", V_, F_, R_, w="rigid_transfer")
for k, (dx_, ln_, r_, tl_) in enumerate(SA["scrolls"]):
    ax_ = unit(np.array([0.0, 0.0, 1.0]) * math.cos(math.radians(tl_)) + _pt * math.sin(math.radians(tl_)))
    c_ = SATCHEL_C + _pt * dx_ - _pd * hy_ * 0.25 + np.array([0.0, 0.0, hz_ - 0.5 * ln_ + 0.06])
    hl_ = 0.5 * ln_
    V_, F_, R_ = VP.lathe([(0.0, -hl_), (r_, -hl_), (r_, hl_ - 0.004), (r_ * 0.55, hl_), (0.0, hl_)],
                          ["scroll_paper", "scroll_paper", "scroll_paper", "scroll_end"], 10, c_, ax_, up_hint=_pd)
    add_part("satchelscroll.%d" % k, V_, F_, R_, w="rigid_transfer")
MANTLE_INFO = {"columns": MANTLE_NU, "rows": MANTLE_NV, "front_edge_deg_from_back": MANTLE_FRONT, "hem_z": MANTLE_HEM_Z,
               "trim_faces": int(sum(1 for r_ in Rmc if r_ == "mantle_trim")), "motifs": MOTIF_N,
               "capelet_hem_z_back": round(CAP_HEM_Z_BACK, 4), "crest_centre_z": round(float(_crz), 4),
               "brooch": BROOCH_C.round(4).tolist(), "satchel": SATCHEL_C.round(4).tolist(), "strap_points": int(len(Cs)),
               "seconds": round(time.time() - t_mantle, 1)}
print("MANTLE", json.dumps(MANTLE_INFO))
