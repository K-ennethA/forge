# Varden build section 4 (Elias's mantle machinery from elias_s4_mantle.py, re-shaped + the new FUR MANTLE): the
# cross-body sword STRAP (his right shoulder -> his left hip, under the cloak) with its studs + buckle; the huge navy CLOAK
# -- open front (close under the fur at the chest, falling open at the sides so the tunic / tabard / belt / sword show),
# hanging from the shoulders BEHIND the arms down to the low shin, worn hem, gold trim band along the hem and the front
# edges, small gold emblem stars above the hem; the dark FUR MANTLE over the shoulders / collar -- a NEW garment class
# built as layered fur CLUMPS (a dark under-fur roll + rows of pointed tufts with real cross-section, the hem row making the
# shaggy silhouette), not cloth; the large angular gold TRIDENT-CREST EMBLEM on the cloak back; the round gold radial
# BROOCH at his left collar with its pendant + the CHAIN sagging across the chest.
t_cloak = time.time()
# ---- the cross-body sword STRAP: a plane through his right shoulder top and his left hip, ray-cast round the trunk
_strap_host = {"skirt", "tabard", "belt", "buckle", "beltstud", "swordbelt", "collar"}
BVH_STRAPP = comb_bvh(_strap_host, body=(BV, [f for f, n, a in zip(BF, fdomn, is_arm_f) if n != "head" and not a]))
_S_top = np.array([SHO["R"][0] * 0.55, AX_Y, SHO["R"][2] + 0.08])
_S_low = np.array([0.20, AX_Y, Z_BELT - SWORD_BELT["drop"] - 0.03])
_sc = 0.5 * (_S_top + _S_low)
_sn = unit(np.cross([0.0, 1.0, 0.0], _S_top - _S_low))
_se1 = unit(_S_top - _sc); _se2 = np.cross(_sn, _se1)
Cs, Ns = [], []
for k in range(64):
    ps_ = 2 * math.pi * k / 64
    d_ = math.cos(ps_) * _se1 + math.sin(ps_) * _se2
    h_ = BVH_STRAPP.ray_cast(Vector(_sc + d_ * 0.7), Vector(-d_), 0.7)
    if h_[0] is None:
        continue
    Cs.append(np.array(h_[0]) + d_ * (STRAP["t"] + 0.0025)); Ns.append(d_)
Cs = np.array(Cs); Ns = np.array(Ns)
for _ in range(3):                                          # a smoothed loop (no snagging on the buckle / studs)
    Cs = 0.5 * Cs + 0.25 * (np.roll(Cs, 1, 0) + np.roll(Cs, -1, 0))
V_, F_, R_ = VP.ribbon(np.vstack([Cs, Cs[:1]]), np.vstack([Ns, Ns[:1]]), STRAP["w"] * 0.5, STRAP["t"], region="strap")
add_part("strap", V_, F_, R_, w="transfer")
# the front run (shoulder -> hip, in front of the trunk): studs + one buckle
_fr = [i for i in range(len(Cs)) if Cs[i, 1] < AX_Y - 0.02 and Cs[i, 2] < _S_top[2] - 0.02 and Cs[i, 2] > _S_low[2] + 0.02]
_fr = sorted(_fr, key=lambda i: -Cs[i, 2])
STRAP_INFO = {"loop_points": int(len(Cs)), "front_points": len(_fr)}
if len(_fr) > 4:
    _fC = Cs[_fr]; _fN = Ns[_fr]
    _fa = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(_fC, axis=0), axis=1))])
    _fa = _fa / max(_fa[-1], 1e-9)
    for k_ in range(STRAP["studs"]):
        t_ = 0.08 + 0.84 * k_ / max(STRAP["studs"] - 1, 1)
        if abs(t_ - STRAP["buckle_t"]) < 0.08:
            continue
        p_ = np.array([np.interp(t_, _fa, _fC[:, j]) for j in range(3)]); n_ = unit(np.array([np.interp(t_, _fa, _fN[:, j]) for j in range(3)]))
        V_, F_, R_ = VP.gem(p_ + n_ * (STRAP["t"] + 0.0004), n_, STRAP["stud_r"], 0.0018, n=4, region="brass")
        add_part("strapstud.%d" % k_, V_, F_, R_, w="rigid_transfer")
    t_ = STRAP["buckle_t"]
    p_ = np.array([np.interp(t_, _fa, _fC[:, j]) for j in range(3)]); n_ = unit(np.array([np.interp(t_, _fa, _fN[:, j]) for j in range(3)]))
    tg_ = unit(np.array([np.interp(t_ + 0.02, _fa, _fC[:, j]) for j in range(3)]) - p_)
    ex_ = unit(np.cross(n_, tg_))
    for k_, (du_, dv_, hu_, hv_) in enumerate(((0.0, 0.020, 0.026, 0.0035), (0.0, -0.020, 0.026, 0.0035),
                                               (0.0225, 0.0, 0.0035, 0.020), (-0.0225, 0.0, 0.0035, 0.020))):
        V_, F_, R_ = VP.rounded_box(p_ + n_ * (STRAP["t"] + 0.003) + ex_ * du_ + tg_ * dv_, ex_, n_, tg_, hu_, 0.0026, hv_,
                                    nr=1, rows=2, bulge=0.0, region="brass")
        add_part("strapbuckle.%d" % k_, V_, F_, R_, w="rigid_transfer")
    STRAP_INFO["buckle"] = p_.round(4).tolist()

# ---- the CLOAK
_CLOAK_HOST = {"skirt", "tabard", "skirtstar", "belt", "buckle", "beltstud", "swordbelt", "bootshaft", "bootcuff", "boottrim",
               "bootstrap", "bootbuckle", "collar", "strap", "strapstud", "strapbuckle"}
_legF = [f for f, n in zip(BF, fdomn) if n.startswith(("thigh", "calf", "foot", "ball"))]
_hv, _hf = [BV], [list(f) for f in TRUNK_F + _legF]
_o = len(BV)
for p in PARTS:
    if p["name"].split(".")[0] in _CLOAK_HOST:
        _hv.append(p["V"]); _hf += [[i + _o for i in f] for f in p["F"]]; _o += len(p["V"])
BVH_CLOAKP = BVHTree.FromPolygons(np.vstack(_hv).tolist(), _hf)
Z_CL_BACK = NECK0[2] + CLOAK_TOP[0]
Z_CL_SHO = SHO["L"][2] + CLOAK_TOP[1]
Z_CL_FRONT = NECK0[2] + CLOAK_TOP[2]
_zg = np.linspace(Z_CL_BACK + 0.03, 0.04, 120)
_phg = np.arange(-178.0, 178.01, 2.0)
_PROF = np.stack([np.maximum.accumulate(radial_profile(BVH_CLOAKP, 0.0, AX_Y, ph, _zg)) for ph in _phg])


def cloak_hang(phi_back, z):
    col = np.array([np.interp(phi_back, _phg, _PROF[:, k]) for k in range(len(_zg))])
    return float(np.interp(-z, -_zg, col))


def cloak_edge(v):
    return float(np.interp(v, [p_[0] for p_ in CLOAK_FRONT], [p_[1] for p_ in CLOAK_FRONT]))


def cloak_ztop(phi):
    return float(np.interp(abs(phi), [0.0, 90.0, 150.0], [Z_CL_BACK, Z_CL_SHO, Z_CL_FRONT]))


vrows = np.linspace(0.0, 1.0, CLOAK_NV) ** 1.1
Vm, UVm = [], []
for j, v in enumerate(vrows):
    pe = cloak_edge(v)
    for c in range(CLOAK_NU):
        u = c / (CLOAK_NU - 1)
        tear = CLOAK_TEAR[1] * (0.35 + 0.9 * hash01(c, 5.0)) * (1.0 if c % 2 else 0.30)
        if hash01(c, 3.0) > 0.82:
            tear = CLOAK_TEAR[0] * (0.6 + 0.4 * hash01(c, 7.0))
        ph = -pe + u * 2.0 * pe
        ph0 = -cloak_edge(0.0) + u * 2.0 * cloak_edge(0.0)
        ztop = cloak_ztop(ph0)
        hem = CLOAK_HEM_Z - tear + 0.04 * smoothstep(0.80, 1.0, abs(2 * u - 1))
        z = ztop + (hem - ztop) * v
        edge_k = smoothstep(0.0, 0.08, min(u, 1.0 - u))
        r = cloak_hang(ph, z) + CLOAK_CLEAR + CLOAK_FLARE * v ** 1.5 + \
            CLOAK_FOLD * v ** 1.2 * edge_k * math.sin(2 * math.pi * CLOAK_FOLDS * u + 1.3 * v)
        a = math.radians(ph)
        Vm.append([r * math.sin(a), AX_Y + r * math.cos(a), z])
        UVm.append([u, v])
Vm = np.array(Vm); UVm = np.array(UVm)
# the shoulder clearance: every top-band vertex kept CLOAK_CLEAR outside the shoulders / upper arms (ray from the axis)
_bvh_sho = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n.startswith(("upperarm", "clavicle", "spine_03"))])
for i in range(len(Vm)):
    if UVm[i, 1] > 0.35:
        continue
    d_ = np.array([Vm[i, 0], Vm[i, 1] - AX_Y, 0.0]); r0_ = float(np.linalg.norm(d_)); d_ /= max(r0_, 1e-9)
    h_ = _bvh_sho.ray_cast(Vector(np.array([0.0, AX_Y, Vm[i, 2]]) + d_ * 0.6), Vector(-d_), 0.6)
    if h_[0] is not None:
        rs_ = 0.6 - h_[3] + CLOAK_CLEAR
        if rs_ > r0_:
            Vm[i, :2] = np.array([0.0, AX_Y]) + d_[:2] * (r0_ + (rs_ - r0_) * (1.0 - smoothstep(0.20, 0.35, UVm[i, 1])))
Fm = VP.grid_faces(CLOAK_NU, CLOAK_NV)
Fm = [f if np.dot(np.cross(Vm[f[1]] - Vm[f[0]], Vm[f[2]] - Vm[f[0]]), np.array([Vm[f].mean(0)[0], Vm[f].mean(0)[1] - AX_Y, 0.0])) > 0
      else f[::-1] for f in Fm]
Gv = Vm.reshape(CLOAK_NV, CLOAK_NU, 3)
Am = np.concatenate([np.zeros((CLOAK_NV, 1)), np.cumsum(np.linalg.norm(np.diff(Gv, axis=1), axis=2), axis=1)], 1)
Bm = np.concatenate([np.zeros((1, CLOAK_NU)), np.cumsum(np.linalg.norm(np.diff(Gv, axis=0), axis=2), axis=0)], 0)
_dh = (Bm[-1:, :] - Bm).reshape(-1)                         # distance UP from the hem along the column
_de = np.minimum(Am, Am[:, -1:] - Am).reshape(-1)           # distance from the nearer front edge along the row
_trim = np.minimum(_dh - CLOAK_TRIM[0], _de - CLOAK_TRIM[1])
Vmc, Fmc, FLD = cut_sheet(Vm, Fm, {"u": UVm[:, 0], "v": UVm[:, 1], "trim": _trim, "dh": _dh}, [("trim", 0.0)])
Rmc = ["cloak_trim" if np.mean(FLD["trim"][f]) < 0 else "cloak" for f in Fmc]
V_, F_, R_ = VP.solidify(Vmc, Fmc, CLOAK_T * 0.5, CLOAK_T * 0.5, Rmc, "cloak_lining", "cloak_trim")
add_part("cloak", V_, F_, R_, w="coat")
BVH_CLOAK = BVHTree.FromPolygons(V_.tolist(), F_)


def on_cloak(p, dirn):
    """the cloak's OUTER surface point along -dirn from outside p (+ its outward normal)."""
    o_ = np.asarray(p, float) + dirn * 0.4
    h_ = BVH_CLOAK.ray_cast(Vector(o_), Vector(-dirn), 0.8)
    if h_[0] is None:
        return np.asarray(p, float), dirn
    return np.array(h_[0]), unit(np.array(h_[1]))


# ---- small gold emblem stars above the hem band
MOTIF_N = 0
for k in range(CLOAK_MOTIFS[0]):
    u_ = 0.05 + 0.90 * (k + 0.5) / CLOAK_MOTIFS[0]
    c_ = int(round(u_ * (CLOAK_NU - 1)))
    col_ = Gv[:, c_]
    dh_col = Bm[-1, c_] - Bm[:, c_]
    j_ = int(np.argmin(np.abs(dh_col - (CLOAK_TRIM[0] + CLOAK_MOTIFS[2]))))
    p_ = col_[j_]
    rad_ = unit(np.array([p_[0], p_[1] - AX_Y, 0.0]))
    q_, n_ = on_cloak(p_, rad_)
    n_ = n_ if float(n_ @ rad_) > 0 else -n_
    V_, F_, R_ = VP.star_plate(q_ + n_ * 0.0010, n_, np.array([0.0, 0.0, 1.0]), CLOAK_MOTIFS[1], CLOAK_MOTIFS[1] * 0.30, 4, 0.0012)
    add_part("motif.%d" % k, V_, F_, R_, w="coat")
    MOTIF_N += 1

# ---- the FUR MANTLE: (1) the under-fur ROLL -- columns round the neck / shoulders, rows at equal ARC LENGTH down each
# column's draped profile over the cloak (Elias's capelet rule: the shoulder shelf is never one stretched face); its
# collar part stands up round the neck (FUR["rise"]); dark 'fur_inner' (gaps between tufts read as deep fur, never cloth:
# the hair's inner-cap principle). (2) the TUFTS -- FUR_ROWS rows of pointed clumps rooted on the roll, lying down its
# drape and standing off it (FUR_TUFT lift / out), sideways sway, length / width jitter (fixed hashes), real thickness;
# top faces 'fur', undersides 'fur_shade', the tip segment 'fur_tip'; the hem row hangs past the roll: the shaggy edge.
FU = FUR
_FUR_HOST = {"cloak", "collar", "strap", "strapstud", "strapbuckle"}
BVH_FURP = comb_bvh(_FUR_HOST, body=(BV, [f for f, n in zip(BF, fdomn) if n != "head"]))
_zc0 = NECK0[2] + FU["rise"]
_zcg = np.linspace(_zc0, SHO["L"][2] - 0.35, 180)
Vf, Uf = [], []
for c in range(FU["nu"]):
    u = c / (FU["nu"] - 1)
    ph = -FU["front"] + u * 2.0 * FU["front"]
    ztop_ = float(np.interp(abs(ph), [0.0, 90.0, FU["front"]], [_zc0, _zc0 - 0.006, NECK0[2] + 0.004]))
    zz_ = _zcg[_zcg <= ztop_ + 1e-9]
    rr_ = np.maximum.accumulate(radial_profile(BVH_FURP, 0.0, NECK0[1], ph, zz_)) + FU["clear"]
    seg_ = np.hypot(np.diff(rr_), np.diff(zz_))
    arc_ = np.concatenate([[0.0], np.cumsum(seg_)])
    D_ = FU["drop"] - 0.03 * smoothstep(0.85, 1.0, abs(2 * u - 1)) + (ztop_ - NECK0[2])
    for j in range(FU["nv"]):
        t_ = j / (FU["nv"] - 1)
        s_ = t_ * D_
        Vf.append([float(np.interp(s_, arc_, rr_)) * math.sin(math.radians(ph)),
                   NECK0[1] + float(np.interp(s_, arc_, rr_)) * math.cos(math.radians(ph)), float(np.interp(s_, arc_, zz_))])
        Uf.append([u, t_])
Vf = np.array(Vf); Uf = np.array(Uf)
_nvf, _nuf = FU["nv"], FU["nu"]
Ff = []
for j in range(_nvf - 1):
    for c in range(_nuf - 1):
        Ff.append([c * _nvf + j, (c + 1) * _nvf + j, (c + 1) * _nvf + j + 1, c * _nvf + j + 1])
Ff = [f if np.dot(np.cross(Vf[f[1]] - Vf[f[0]], Vf[f[2]] - Vf[f[0]]), np.array([Vf[f].mean(0)[0], Vf[f].mean(0)[1] - NECK0[1], 0.0])) > 0
      else f[::-1] for f in Ff]
_NF = VP.vertex_normals(Vf, Ff)
V_, F_, R_ = VP.solidify(Vf, Ff, FU["base_t"], 0.0, "fur_inner", "fur_inner", "fur_inner")
add_part("furroll", V_, F_, R_, w="coat")
GF = Vf.reshape(_nuf, _nvf, 3); GN = _NF.reshape(_nuf, _nvf, 3)
FUR_HEM_Z_BACK = float(GF[_nuf // 2, -1, 2])


def fur_at(u, t):
    """position / outward normal / down-the-drape tangent on the roll's outer skin at (u across, t down)."""
    x_ = u * (_nuf - 1); y_ = t * (_nvf - 1)
    i0, j0 = min(int(x_), _nuf - 2), min(int(y_), _nvf - 2)
    fx, fy = x_ - i0, y_ - j0
    bl = lambda G: (G[i0, j0] * (1 - fx) * (1 - fy) + G[i0 + 1, j0] * fx * (1 - fy) + G[i0, j0 + 1] * (1 - fx) * fy +
                    G[i0 + 1, j0 + 1] * fx * fy)
    n_ = unit(bl(GN))
    tg_ = unit((GF[i0, j0 + 1] - GF[i0, j0]) * (1 - fx) + (GF[i0 + 1, j0 + 1] - GF[i0 + 1, j0]) * fx)
    return bl(GF) + n_ * FU["base_t"], n_, unit(tg_ - n_ * float(tg_ @ n_))


FT = FUR_TUFT
FUR_INFO = {"tufts": 0, "rows": len(FUR_ROWS), "tris": 0}
_ft_k = 0
for ri_, (tr_, ln0_, nn_, phs_) in enumerate(FUR_ROWS):
    for i in range(nn_):
        u_ = (i + 0.5 + 0.5 * phs_ + 0.35 * (hash01(_ft_k, 2.1) - 0.5)) / nn_
        u_ = min(max(u_, 0.01), 0.99)
        t_ = min(max(tr_ - 0.04 * hash01(_ft_k, 4.4), 0.02), 0.985)
        p0_, n_, tg_ = fur_at(u_, t_)
        ln_ = ln0_ * (1.0 + FT["len_vary"] * (2.0 * hash01(_ft_k, 6.6) - 1.0))
        w_ = FT["w"] * (1.0 + FT["w_vary"] * (2.0 * hash01(_ft_k, 8.8) - 1.0))
        side_ = unit(np.cross(n_, tg_))
        sway_ = FT["sway"] * w_ * (2.0 * hash01(_ft_k, 9.9) - 1.0)
        lift_ = FT["lift"] * (0.75 + 0.5 * hash01(_ft_k, 1.3))
        # the path: out of the roll, along the drape, curling back down at the tip
        P_ = [p0_ - n_ * 0.003]
        for f_ in (0.30, 0.62, 1.0):
            P_.append(p0_ + tg_ * ln_ * f_ + n_ * ln_ * lift_ * (FT["out"] * f_ + (1 - FT["out"]) * math.sin(math.pi * 0.5 * f_)) *
                      (1.0 - FT["curl"] * f_ ** 2) + side_ * sway_ * f_ ** 1.5)
        C_ = VP.resample(VP.catmull(np.array(P_), 6), FT["stations"])[0]
        Tg_ = np.gradient(C_, axis=0)
        rings_ = []
        for k_, c_ in enumerate(C_):
            sf_ = k_ / (len(C_) - 1)
            hw_ = 0.5 * w_ * (0.85 + 0.25 * math.sin(math.pi * min(sf_ / 0.4, 1.0))) * (1.0 - 0.92 * smoothstep(0.25, 1.0, sf_))
            ht_ = FT["t"] * (1.0 - 0.75 * smoothstep(0.3, 1.0, sf_))
            t_dir_ = unit(Tg_[k_])
            n_k_ = unit(n_ - t_dir_ * float(n_ @ t_dir_))
            rings_.append(VP.lens_ring(c_, t_dir_, np.cross(n_k_, t_dir_), max(hw_, 0.0006), max(ht_, 0.0005), 4))
        tip_ = C_[-1] + unit(Tg_[-1]) * 0.006
        V_, F_, _ = VP.loft(rings_, "flat", "pole", reg="fur", pole1=tip_)
        m_ = 4; ns_ = len(C_)
        R_ = []
        for fi_ in range(len(F_)):
            if fi_ < (ns_ - 1) * m_:
                k_, e_ = divmod(fi_, m_)
                if e_ in (2, 3):
                    R_.append("fur_shade")
                else:
                    R_.append("fur_tip" if k_ >= ns_ - 2 else ("fur_root" if k_ == 0 else "fur"))
            else:
                R_.append("fur_tip" if fi_ % 2 else "fur_shade")
        add_part("fur.%d.%d" % (ri_, i), V_, F_, R_, w="coat")
        FUR_INFO["tufts"] += 1; FUR_INFO["tris"] += tri_count_F(F_)
        _ft_k += 1
FUR_INFO["roll_hem_z_back"] = round(FUR_HEM_Z_BACK, 4)

# ---- the trident-crest EMBLEM large on the cloak back (below the fur hem)
_crz = FUR_HEM_Z_BACK - CREST["z_below_fur"] - 0.5 * CREST["h"]
_cr_o, _ = on_cloak(np.array([0.0, AX_Y, _crz]), np.array([0.0, 1.0, 0.0]))
for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.varden_crest_strokes(CREST["h"]), _cr_o, np.array([-1.0, 0.0, 0.0]),
                                                         np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0]), CREST["w"], 0.0010,
                                                         "brass", project=lambda p_: on_cloak(p_, np.array([0.0, 1.0, 0.0])))):
    add_part("crest.%d" % k, Vk_, Fk_, Rk_, w="coat")

# ---- the BROOCH at his left collar (on top of the fur's front edge) + its pendant + the CHAIN across the chest
BVH_FRONT = comb_bvh({"furroll", "fur", "cloak", "collar", "strap"})
_bd = dir_front(BROOCH["phi_front"])
_bz = NECK0[2] - BROOCH["drop"]
_bh = BVH_FRONT.ray_cast(Vector(np.array([0.0, NECK0[1], _bz]) + _bd * 0.8), Vector(-_bd), 1.0)
_bn = unit(np.array(_bh[1])); _bn = _bn if float(_bn @ _bd) > 0 else -_bn
_bn = unit(_bn + 0.6 * _bd)
BROOCH_C = np.array(_bh[0]) + _bn * 0.004
_bex = unit(np.cross([0.0, 0.0, 1.0], _bn)); _bey = unit(np.cross(_bn, _bex))
Vb0_, Fb0_, Rb0_ = VP.radial_brooch(BROOCH)
_Rb = np.stack([_bex, _bey, _bn], 1)
add_part("brooch", Vb0_ @ _Rb.T + BROOCH_C, Fb0_, Rb0_, w="rigid_transfer")
# the pendant: a short chain drop + a steel-teal drop gem in a gold cap (sheet: the dangle under the brooch)
_pd0 = BROOCH_C - _bey * BROOCH["r"] * 0.95 + _bn * 0.002
_pd1 = _pd0 - np.array([0.0, 0.0, BROOCH["pendant"]]) + _bn * 0.004
V_, F_, R_, _ = VP.tube_path(np.array([_pd0, 0.5 * (_pd0 + _pd1) + _bn * 0.002, _pd1]), 0.0014, 5, "brass_dark", cap0="flat", cap1="flat")
add_part("pendant.cord", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.crystal(_pd1 - np.array([0.0, 0.0, 0.012]), (0, 0, 1), 0.0062, 0.010, 0.016, n=6, region="gem_teal")
add_part("pendant.gem", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.lathe([(0.0, 0.004), (0.0045, 0.002), (0.0066, -0.003), (0.0, -0.004)], ["brass", "brass", "brass"], 6,
                      _pd1 - np.array([0.0, 0.0, 0.002]), (0, 0, 1))
add_part("pendant.cap", V_, F_, R_, w="rigid_transfer")
# the chain: from the brooch's right edge across the chest to the fur's right front corner, sagging, gold links (beads)
_cd = dir_front(CHAIN["to_phi"])
_ch = BVH_FRONT.ray_cast(Vector(np.array([0.0, NECK0[1], _bz + 0.010]) + _cd * 0.8), Vector(-_cd), 1.0)
_ce = np.array(_ch[0]) + _cd * 0.006 if _ch[0] is not None else BROOCH_C * np.array([-1.0, 1.0, 1.0])
_cs = BROOCH_C - _bex * BROOCH["r"] * 0.9
_cpts = []
_bvh_ch = comb_bvh({"strap", "strapstud", "strapbuckle", "collar", "furroll", "fur"}, body=(BV, [f for f, n in zip(BF, fdomn) if n != "head"]))
for k in range(CHAIN["links"]):
    t_ = k / (CHAIN["links"] - 1)
    p_ = _cs + (_ce - _cs) * t_ - np.array([0.0, 0.0, CHAIN["sag"] * 4.0 * t_ * (1 - t_)])
    h_ = _bvh_ch.ray_cast(Vector(np.array([p_[0], -0.8, p_[2]])), Vector((0.0, 1.0, 0.0)), 1.6)
    if h_[0] is not None:
        p_[1] = min(p_[1], float(h_[0][1]) - CHAIN["r"] * 2.2)
    _cpts.append(p_)
_cpts = np.array(_cpts)
for k in range(len(_cpts)):
    V_, F_, R_ = VP.torus(CHAIN["r"] * 1.6, CHAIN["r"] * 0.55, 5, 3, _cpts[k],
                          unit(_cpts[min(k + 1, len(_cpts) - 1)] - _cpts[max(k - 1, 0)]) if k % 2 else np.array([0.0, -1.0, 0.0]),
                          up_hint=(0.0, 0.0, 1.0), region="brass")
    add_part("chain.%d" % k, V_, F_, R_, w="rigid_transfer")
CLOAK_INFO = {"columns": CLOAK_NU, "rows": CLOAK_NV, "front_edge_deg_from_back": CLOAK_FRONT, "hem_z": CLOAK_HEM_Z,
              "trim_faces": int(sum(1 for r_ in Rmc if r_ == "cloak_trim")), "motifs": MOTIF_N, "fur": FUR_INFO,
              "crest_centre_z": round(float(_crz), 4), "brooch": BROOCH_C.round(4).tolist(), "strap": STRAP_INFO,
              "chain_links": int(len(_cpts)), "seconds": round(time.time() - t_cloak, 1)}
print("CLOAK", json.dumps(CLOAK_INFO))
