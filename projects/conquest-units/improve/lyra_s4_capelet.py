# Lyra build section 4 (the capelet machinery of elias_s4_mantle.py, re-shaped): the navy shoulder CAPELET (columns round the
# neck / shoulders, rows at equal arc length down each column's draped profile; the drop per azimuth -- the long V point at
# the back (the back view), short over the arms, shorter at the front), its hem + front edges gold-trimmed, the compass-star
# EMBLEM raised large on the back and small on each shoulder; the gold compass BROOCH on her left chest; the brown cross
# STRAP (her left shoulder -> her right hip); the leather SATCHEL on her right hip, hung from the strap; the leather SCROLL
# CASE behind it; the paper scroll roll at her left back hip. The capelet is SHOULDER-HUNG (arm-coupled weights, style
# guide); strap / satchel / scroll geometry is built against TRUNK-ONLY hosts (s7 weights them from the trunk only).
t_cape = time.time()
CP = CAPELET
_CAP_HOST = {"collarband", "collarflap", "tieknot", "tietail", "tiepin"}
BVH_CAPP = comb_bvh(_CAP_HOST, body=(BV, [f for f, n in zip(BF, fdomn) if n != "head" and not n.startswith(("lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb"))]))   # (no forearms / hands: the rest hands caught the side columns)
_zc0 = NECK0[2] + 0.010
_zcg = np.linspace(_zc0, SHO["L"][2] - 0.45, 220)
Vc, Uc = [], []
_dtab = CP["drop"]
for c in range(CP["nu"]):
    u = c / (CP["nu"] - 1)
    ph = -CP["front"] + u * 2.0 * CP["front"]
    ztop_ = float(np.interp(abs(ph), [0.0, 90.0, CP["front"]], [_zc0, NECK0[2] + 0.004, NECK0[2] - 0.026]))
    zz_ = _zcg[_zcg <= ztop_ + 1e-9]
    rr_ = np.maximum.accumulate(radial_profile(BVH_CAPP, 0.0, NECK0[1], ph, zz_)) + CP["clear"]
    for k_ in range(1, len(rr_)):                           # drape slope limit: the cloth falls, it never juts out sideways
        rr_[k_] = min(rr_[k_], rr_[k_ - 1] + 1.2 * abs(zz_[k_ - 1] - zz_[k_]))
    seg_ = np.hypot(np.diff(rr_), np.diff(zz_))
    arc_ = np.concatenate([[0.0], np.cumsum(seg_)])
    D_ = float(np.interp(abs(ph), [d_[0] for d_ in _dtab], [d_[1] for d_ in _dtab]))
    D_ = D_ + (_dtab[0][1] - _dtab[1][1]) * 0.25 * (1.0 - min(abs(ph) / _dtab[1][0], 1.0)) ** CP["v_sharp"]   # the sharper V tip
    for j in range(CP["nv"]):
        t_ = j / (CP["nv"] - 1)
        s_ = t_ * D_
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
_Gc = Vc.reshape(_nuc, _nvc, 3)
_Ac = np.concatenate([np.zeros((1, _nvc)), np.cumsum(np.linalg.norm(np.diff(_Gc, axis=0), axis=2), axis=0)], 0)   # along the rows
_de = np.minimum(_Ac, _Ac[-1:, :] - _Ac).reshape(-1)          # distance from the nearer front edge along the row
Vcc, Fcc, FLDc = cut_sheet(Vc, Fc, {"dhem": Uc[:, 2] - CP["trim"], "dedge": _de - CP["trim"] * 0.8}, [("dhem", 0.0), ("dedge", 0.0)])
Rcc = ["capelet_trim" if (np.mean(FLDc["dhem"][f]) < 0 or np.mean(FLDc["dedge"][f]) < 0) else "capelet" for f in Fcc]
Rcc = ["gold" if r_ == "capelet_trim" else r_ for r_ in Rcc]
V_, F_, R_ = VP.solidify(Vcc, Fcc, CP["t"] * 0.5, CP["t"] * 0.5, Rcc, "capelet_lining", "gold")
add_part("capelet", V_, F_, R_, w="transfer")
CAPELET_V, CAPELET_F = V_, F_
BVH_CAPELET = BVHTree.FromPolygons(V_.tolist(), F_)
CAP_HEM_Z_BACK = float(Vc[(_nuc // 2) * _nvc + _nvc - 1, 2])


def on_capelet(p, dirn):
    """the capelet's OUTER surface point along -dirn from outside p (+ its outward normal)."""
    o_ = np.asarray(p, float) + dirn * 0.4
    h_ = BVH_CAPELET.ray_cast(Vector(o_), Vector(-dirn), 0.8)
    if h_[0] is None:
        return np.asarray(p, float), dirn
    n_ = unit(np.array(h_[1]))
    return np.array(h_[0]), (n_ if float(n_ @ dirn) > 0 else -n_)


# ---- the compass-star EMBLEM on the capelet back (large) + on each shoulder (small)
_ez = NECK0[2] - CAPE_EMBLEM[1]
_eo, _en = on_capelet(np.array([0.0, AX_Y, _ez]), np.array([0.0, 1.0, 0.0]))
for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.compass_strokes(CAPE_EMBLEM[0]), _eo, np.array([-1.0, 0.0, 0.0]),
                                                         np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0]), CAPE_EMBLEM[2], 0.0010,
                                                         "gold", project=lambda p_: on_capelet(p_, np.array([0.0, 1.0, 0.0])))):
    add_part("capemblem.%d" % k, Vk_, Fk_, Rk_, w="transfer")
for s, sg_ in (("L", 1.0), ("R", -1.0)):
    d_ = unit(np.array([sg_, 0.15, 0.0]))
    p0_ = np.array([SHO[s][0], SHO[s][1], SHO[s][2] - 0.030])
    o_, n_ = on_capelet(p0_ + d_ * 0.25, d_)
    if BVH_CAPELET.ray_cast(Vector(p0_ + d_ * 0.65), Vector(-d_), 0.8)[0] is None:
        continue                                        # (no capelet there: never strokes in mid-air)
    ex_ = unit(np.cross([0.0, 0.0, 1.0], n_)); ey_ = unit(np.cross(n_, ex_))
    if ey_[2] < 0:
        ey_ = -ey_; ex_ = -ex_
    for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.compass_strokes(SHOULDER_EMBLEM[0], pendant=False), o_, ex_, ey_, n_,
                                                             SHOULDER_EMBLEM[1], 0.0008, "gold", project=lambda p_, d_=d_: on_capelet(p_, d_))):
        add_part("shemblem.%s%d" % (s, k), Vk_, Fk_, Rk_, w="transfer")

# ---- the gold compass BROOCH on her left chest (front view: on the capelet's front edge / shirt)
BVH_FRONT = comb_bvh({"capelet", "collarband", "collarflap", "tieknot", "tietail"})
_bz = NECK0[2] - BROOCH["drop"]
_bh = BVH_FRONT.ray_cast(Vector((BROOCH["x"], -0.8, _bz)), Vector((0.0, 1.0, 0.0)), 1.5)
_bn = unit(np.array(_bh[1])); _bn = _bn if _bn[1] < 0 else -_bn
_bn = unit(_bn + np.array([0.0, -0.6, 0.0]))
BROOCH_C = np.array(_bh[0]) + _bn * 0.003
_br = BROOCH["r"]
V_, F_, R_ = VP.lathe([(0.0, 0.0030), (_br * 0.80, 0.0030), (_br * 0.92, 0.0042), (_br, 0.0026), (_br * 0.96, 0.0), (0.0, 0.0)],
                      ["gold", "gold_dark", "gold", "gold", "gold_dark"], 16, BROOCH_C, _bn, up_hint=(0, 0, 1))
add_part("brooch", V_, F_, R_, w="rigid_transfer")
_bex = unit(np.cross([0.0, 0.0, 1.0], _bn)); _bey = unit(np.cross(_bn, _bex))
if _bex[0] < 0:
    _bex = -_bex
for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.compass_strokes(BROOCH["crest_h"], ring=False, pendant=False),
                                                         BROOCH_C + _bn * 0.0042, _bex, _bey, _bn, 0.0010, 0.0007, "gold_dark")):
    add_part("broochcrest.%d" % k, Vk_, Fk_, Rk_, w="rigid_transfer")

# ---- the cross STRAP: her left shoulder -> across the chest / back -> her right hip (a ring in the plane through both anchors)
_strap_host = {"capelet", "collarband", "collarflap", "tieknot", "tietail", "tiepin", "brooch", "broochcrest", "corset", "belt",
               "buckle", "capemblem", "shemblem"}   # (no skirt / front tab / pouch: the ring's low rays crossed the flared skirt far below the hip)
BVH_STRAPP = comb_bvh(_strap_host, body=(BV, [f for f, n in zip(BF, fdomn) if n in ("pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "clavicle_l", "clavicle_r")]))   # (trunk only: no arms, no legs -- the thighs caught the low rays)   # (the arms
#                                                     hang outside the strap: their faces would catch the side rays)
_S_top = np.array([SHO["L"][0] * STRAP["top"][0], AX_Y, SHO["L"][2] + STRAP["top"][1]])
_S_low = np.array([-abs(HIP["R"][0]) * abs(STRAP["low"][0]), AX_Y, HIP["R"][2] + STRAP["low"][1]])
_sc = 0.5 * (_S_top + _S_low)
_sn = unit(np.cross([0.0, 1.0, 0.0], _S_top - _S_low))
_se1 = unit(_S_top - _sc); _se2 = np.cross(_sn, _se1)
Cs, Ns = [], []
for k in range(72):
    ps_ = 2 * math.pi * k / 72
    d_ = math.cos(ps_) * _se1 + math.sin(ps_) * _se2
    h_ = BVH_STRAPP.ray_cast(Vector(_sc + d_ * 0.7), Vector(-d_), 0.7)
    if h_[0] is None:
        continue
    Cs.append(np.array(h_[0]) + d_ * (STRAP["t"] + 0.0025)); Ns.append(d_)
Cs = np.array(Cs); Ns = np.array(Ns)
for _ in range(3):                                          # a smoothed loop (no snagging on the brooch / folds)
    Cs = 0.5 * Cs + 0.25 * (np.roll(Cs, 1, 0) + np.roll(Cs, -1, 0))
_ring = np.vstack([Cs, Cs[:1]])
V_, F_, R_ = VP.ribbon(_ring, np.vstack([Ns, Ns[:1]]), STRAP["w"] * 0.5, STRAP["t"], region="strap")
add_part("strap", V_, F_, R_, w="transfer")
# ---- the SATCHEL on her right hip, hung from the strap's low point
SA = SATCHEL
_pd = dir_front(SA["phi"])
_jb = int(np.argmin(Cs[:, 2]))
hx_, hy_, hz_ = SA["size"]
BVH_BAGP = comb_bvh({"corset", "belt", "skirt", "buckle", "strap"}, body=(BV, TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith("thigh")]))
_bzc = float(Cs[_jb, 2]) - hz_ * 0.55 + SA["z_off"]
_br_ = float(rp_front(BVH_BAGP, SA["phi"], np.linspace(_bzc - hz_, _bzc + hz_, 9)).max())
SATCHEL_C = np.array([0.0, AX_Y, _bzc]) + _pd * (_br_ + hy_ + 0.004)
_pt = np.cross([0.0, 0.0, 1.0], _pd)
V_, F_, R_ = VP.rounded_box(SATCHEL_C, _pt, _pd, [0.0, 0.0, 1.0], hx_, hy_, hz_, nr=3, rows=6, bulge=0.10, region="satchel")
add_part("satchel", V_, F_, R_, w="rigid_transfer")
_fc = SATCHEL_C + _pd * (hy_ * 0.96) + np.array([0.0, 0.0, hz_ * (1.0 - SA["flap"])])
V_, F_, R_ = VP.rounded_box(_fc, _pt, _pd, [0.0, 0.0, 1.0], hx_ * 1.04, 0.0034, hz_ * SA["flap"], nr=2, rows=4, bulge=0.04,
                            region="satchel_flap")
add_part("satchelflap", V_, F_, R_, w="rigid_transfer")
for dx_ in (-0.45, 0.45):
    _bk = _fc + _pd * 0.0042 + _pt * hx_ * dx_ - np.array([0.0, 0.0, hz_ * SA["flap"] * 0.70])
    V_, F_, R_ = VP.rounded_box(_bk, _pt, _pd, [0.0, 0.0, 1.0], 0.011, 0.0022, 0.009, nr=1, rows=3, bulge=0.0, region="gold")
    add_part("satchelbuckle.%d" % (dx_ > 0), V_, F_, R_, w="rigid_transfer")
    V_, F_, R_ = VP.rounded_box(_bk + np.array([0.0, 0.0, 0.025]) - _pd * 0.001, _pt, _pd, [0.0, 0.0, 1.0], 0.007, 0.0018, 0.026,
                                nr=1, rows=3, bulge=0.0, region="strap")
    add_part("satchelstrapl.%d" % (dx_ > 0), V_, F_, R_, w="rigid_transfer")
V_, F_, R_, _ = VP.tube_path(np.array([SATCHEL_C + np.array([0.0, 0.0, hz_]) + _pt * hx_ * 0.6, Cs[_jb]]), 0.0034, 5, "strap", su=2.2)
add_part("satchelhang", V_, F_, R_, w="rigid_transfer")
# ---- the leather SCROLL CASE behind the satchel (diagonal) + the paper scroll roll at her left back hip
SCC = SCROLL_CASE
_pd = dir_front(SCC["phi"]); _pt = np.cross([0.0, 0.0, 1.0], _pd)
_ax = unit(np.array([0.0, 0.0, 1.0]) * math.cos(math.radians(SCC["tilt"])) + _pt * math.sin(math.radians(SCC["tilt"])))
_ztop = Z_LOWBELT + SCC["z"]
_c0 = np.array([0.0, AX_Y, _ztop - 0.5 * SCC["len"] * math.cos(math.radians(SCC["tilt"]))])
_rr = float(rp_front(comb_bvh({"skirt", "belt", "corset"}, body=(BV, TRUNK_F)), SCC["phi"], np.linspace(_c0[2] - 0.15, _c0[2] + 0.15, 9)).max())
SCROLL_C = _c0 + _pd * (_rr + SCC["r"] + 0.004)
hl_ = 0.5 * SCC["len"]; r_ = SCC["r"]
prof_ = [(0.0, -hl_ - 0.004), (r_ * 1.10, -hl_ - 0.004), (r_ * 1.10, -hl_ + 0.022), (r_, -hl_ + 0.024), (r_, hl_ - 0.024),
         (r_ * 1.10, hl_ - 0.022), (r_ * 1.10, hl_ + 0.004), (r_ * 0.6, hl_ + 0.004), (r_ * 0.6, hl_ + 0.040), (0.0, hl_ + 0.042)]
V_, F_, R_ = VP.lathe(prof_, ["scroll_cap", "scroll_cap", "scroll_cap", "scroll_tube", "scroll_cap", "scroll_cap", "scroll_cap",
                              "scroll_paper", "scroll_end"], 10, SCROLL_C, _ax, up_hint=_pd)
add_part("scrollcase", V_, F_, R_, w="rigid_transfer")
SR = SCROLL_ROLL
_pd = dir_front(SR["phi"]); _pt = np.cross([0.0, 0.0, 1.0], _pd)
_zr = Z_LOWBELT + SR["z"]
_rr = float(rp_front(comb_bvh({"skirt", "belt", "corset"}, body=(BV, TRUNK_F)), SR["phi"], np.linspace(_zr - 0.03, _zr + 0.03, 5)).max())
ROLL_C = np.array([0.0, AX_Y, _zr]) + _pd * (_rr + SR["r"] + 0.004)
hl_ = 0.5 * SR["len"]; r_ = SR["r"]
V_, F_, R_ = VP.lathe([(0.0, -hl_), (r_, -hl_), (r_, hl_), (0.0, hl_)], ["scroll_end", "scroll_paper", "scroll_end"], 10, ROLL_C, _pt,
                      up_hint=(0, 0, 1))
add_part("scrollroll", V_, F_, R_, w="rigid_transfer")
for zz_ in (-0.25, 0.25):
    V_, F_, R_ = VP.torus(r_ * 1.04, 0.0018, 10, 3, ROLL_C + _pt * hl_ * zz_ * 2.0, _pt, up_hint=(0, 0, 1), region="strap")
    add_part("scrollband.%d" % (zz_ > 0), V_, F_, R_, w="rigid_transfer")
CAPE_INFO = {"capelet_hem_z_back": round(CAP_HEM_Z_BACK, 4), "trim_faces": int(sum(1 for r_ in Rcc if r_ == "gold")),
             "emblem_centre_z": round(float(_ez), 4), "brooch": BROOCH_C.round(4).tolist(), "satchel": SATCHEL_C.round(4).tolist(),
             "scroll_case": SCROLL_C.round(4).tolist(), "strap_points": int(len(Cs)), "strap_low_z": round(float(Cs[_jb, 2]), 4),
             "seconds": round(time.time() - t_cape, 1)}
print("CAPELET", json.dumps(CAPE_INFO))
