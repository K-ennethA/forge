# Shadow Assassin build section 4 (Elias's s4 cloak machinery, re-shaped): the HOOD (deep cowl with a pointed peak, the
# gold-trimmed face opening, near-black inside: the hood VOID), the long tattered BACK CLOAK (trunk-hung, behind the arms)
# with the purple diamond SIGIL + gold ornament, the two ragged SHOULDER CAPES (shoulder-hung), the layered leather
# SHOULDER PLATES, the round gold BROOCH. Sheet views judged: FRONT / SIDE / BACK + HEAD and CLOAK detail panels.
t_cloak = time.time()

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


def compact(V, F):
    """drop unused vertices (after a weld remap) -> V, F."""
    used = sorted(set(i for f in F for i in f))
    rm = {o: n for n, o in enumerate(used)}
    return np.asarray(V)[used], [[rm[i] for i in f] for f in F]


def orient_from(V, F, c, horiz=False):
    """faces oriented away from point c (horiz: away from the vertical axis through c)."""
    out = []
    for f in F:
        d_ = V[f].mean(0) - c
        if horiz:
            d_[2] = 0.0
        out.append(f if np.dot(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]), d_) > 0 else f[::-1])
    return out


# =========================================================================== the HOOD (sheet HEAD DETAIL + all three views)
# rows by elevation about the hood centre (el_top over the crown -> el_bot over the scarf / shoulders), columns round the
# head from one front edge of the face opening to the other (HOOD_OPEN: the opening's front-edge azimuth per elevation;
# above its top the rows close -- the two edge columns welded). Radius per direction = the head / wrap / scarf / trunk hull
# (a ray toward the centre) + clearance, + the pointed PEAK at the top-back, + low folds, + flare over the shoulders; the
# opening's edge pushed FORWARD + down (the deep cowl brim: the face sits in its shadow). Gold trim band along the opening;
# the inside is hood_inner (near-black: the void read from any front angle).
t_hood = time.time()
HD = HOOD
HOOD_C = HC + np.array([0.0, 0.006, 0.010])
_hood_host = comb_bvh({"facewrap", "scarf"}, body=(BV, [f for f, a in zip(BF, is_arm_f) if not a]))
_els = np.linspace(HD["el_top"], HD["el_bot"], HD["nv"])
_ope = np.array(HOOD_OPEN)


def hood_edge(el):
    return float(np.interp(el, _ope[:, 0], _ope[:, 1], left=_ope[0, 1], right=180.0))


Vh, Uh = [], []
for j, el in enumerate(_els):
    E = min(hood_edge(el), 180.0)
    for c in range(HD["nu"]):
        u = c / (HD["nu"] - 1)
        ph = -E + u * 2.0 * E
        a_, e_ = math.radians(ph), math.radians(el)
        d_ = np.array([math.sin(a_) * math.cos(e_), math.cos(a_) * math.cos(e_), math.sin(e_)])
        h_ = _hood_host.ray_cast(Vector(HOOD_C + d_ * 0.6), Vector(-d_), 0.6)
        r_ = (0.6 - h_[3]) if h_[0] is not None else float(np.linalg.norm(HR * d_))
        r_ += HD["clear"] + HD["bot_flare"] * smoothstep(-20.0, HD["el_bot"], el)
        r_ += HD["peak"][0] * math.exp(-((el - HD["peak"][1]) / HD["peak"][2]) ** 2) * math.exp(-(ph / HD["peak"][3]) ** 2)
        r_ += HD["fold"][0] * math.sin(math.radians(ph) * HD["fold"][1] + 0.7) * smoothstep(70.0, 20.0, el)
        p_ = HOOD_C + d_ * r_
        we_ = smoothstep(E - 34.0, E, abs(ph)) if E < 179.5 else 0.0
        kb_ = 0.35 + 0.65 * smoothstep(-45.0, -10.0, el)
        p_ = p_ + np.array([0.0, -HD["brim_fwd"] * kb_, -HD["brim_drop"] * smoothstep(10.0, 45.0, el)]) * we_
        Vh.append(p_); Uh.append([u, j])
Vh = np.array(Vh)
nuh = HD["nu"]
Fh = VP.grid_faces(nuh, HD["nv"])
_weld = {}
for j, el in enumerate(_els):
    if hood_edge(el) >= 179.999:                       # closed rows: the last column IS the first
        _weld[j * nuh + nuh - 1] = j * nuh
Fh = [[_weld.get(i, i) for i in f] for f in Fh]
Gh = Vh.reshape(HD["nv"], nuh, 3)
_Ah = np.concatenate([np.zeros((HD["nv"], 1)), np.cumsum(np.linalg.norm(np.diff(Gh, axis=1), axis=2), axis=1)], 1)
_open_row = np.array([hood_edge(el) < 179.999 for el in _els])
_de_h = np.where(_open_row[:, None], np.minimum(_Ah, _Ah[:, -1:] - _Ah), 1.0).reshape(-1)
Vh, Fh = Vh, Fh
_used = sorted(set(i for f in Fh for i in f))
_rmh = {o: n for n, o in enumerate(_used)}
Vh2 = Vh[_used]; Fh2 = [[_rmh[i] for i in f] for f in Fh]; _de_h2 = _de_h[_used]
Fh2 = orient_from(Vh2, Fh2, HOOD_C)
Vhc, Fhc, FLDh = cut_sheet(Vh2, Fh2, {"trim": _de_h2 - HD["trim"]}, [("trim", 0.0)])
Rhc = ["hood_trim" if np.mean(FLDh["trim"][f]) < 0 else "hood" for f in Fhc]
V_, F_, R_ = VP.solidify(Vhc, Fhc, HD["t"] * 0.5, HD["t"] * 0.5, Rhc, "hood_inner", "hood_trim")
add_part("hood", V_, F_, R_, w="hood")
HOOD_INFO = {"rows": HD["nv"], "cols": nuh, "welded_rows": int(len(_weld)), "top_z": round(float(Vhc[:, 2].max()), 4),
             "brim_front_y": round(float(Vhc[:, 1].min()), 4), "face_front_y": FACEWRAP_INFO["front_y"],
             "brim_ahead_of_wrap_mm": round(1000 * (FACEWRAP_INFO["front_y"] - float(Vhc[:, 1].min())), 1),
             "trim_faces": int(sum(r_ == "hood_trim" for r_ in Rhc)), "seconds": round(time.time() - t_hood, 1)}

# =========================================================================== the long BACK CLOAK (trunk-hung)
# columns round the BACK only (behind the arms at the top, wrapping out past the sides below the hands), rows from the
# shoulder line to the tattered hem; radius = the TRUNK + legs + skirt / belt / pouches profile (max-accumulated: it hangs),
# + clearance + flare + folds; long torn points and tear slits up from the hem (the sheet's BACK view)
CL = CLOAK
_cl_host = comb_bvh({"skirt", "skirtunder", "belt", "buckle", "pouch", "pouchflap", "chest_strap", "beltring", "bootshaft", "bootcuff"},
                    body=(BV, TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith(("thigh", "calf", "foot"))]))
_ztop_cl = SHO["L"][2] + CL["top_dz"]
_zg = np.linspace(_ztop_cl + 0.02, 0.05, 110)
_phg = np.arange(-120.0, 120.01, 2.0)
_PROF = np.stack([np.maximum.accumulate(radial_profile(_cl_host, 0.0, AX_Y, ph, _zg)) for ph in _phg])


def cloak_hang(ph, z):
    col = np.array([np.interp(ph, _phg, _PROF[:, k]) for k in range(len(_zg))])
    return float(np.interp(-z, -_zg, col))


_hem_cl = VP.tear_hem(CL["nu"], 0.0, CL["tear"][0], CL["tear"][1], CL["tear"][2], 31.0)
_azv = np.array(CL["az"])
Vcl, vrow_cl = [], []
for j in range(CL["nv"]):
    v = (j / (CL["nv"] - 1)) ** 1.05
    A_ = float(np.interp(v, _azv[:, 0], _azv[:, 1]))
    for c in range(CL["nu"]):
        u = c / (CL["nu"] - 1)
        ph = -A_ + u * 2.0 * A_
        hem = CL["hem_z"] - _hem_cl[c] + 0.04 * smoothstep(0.75, 1.0, abs(2 * u - 1))
        z = _ztop_cl - 0.03 * (1.0 - math.cos(math.radians(ph))) * 2.0 + (hem - _ztop_cl) * v
        edge_k = smoothstep(0.0, 0.10, min(u, 1.0 - u))
        r = cloak_hang(ph, z) + CL["clear"] + CL["flare"] * v ** 1.6 + \
            CL["folds"][0] * v ** 1.1 * edge_k * math.sin(2 * math.pi * CL["folds"][1] * u + 1.1 * v)
        a = math.radians(ph)
        Vcl.append([r * math.sin(a), AX_Y + r * math.cos(a), z]); vrow_cl.append(v)
Vcl = np.array(Vcl)
_slc = set(CL["slits"])
Fcl = VP.grid_faces(CL["nu"], CL["nv"], skip=lambda i, j: i in _slc and j >= CL["nv"] - 1 - CL["slit_rows"])
Fcl = orient_from(Vcl, Fcl, np.array([0.0, AX_Y, 0.0]), horiz=True)
V_, F_, R_ = VP.solidify(Vcl, Fcl, CL["t"] * 0.5, CL["t"] * 0.5, "cloak", "cloak_inner", "cloak_inner")
add_part("cloak", V_, F_, R_, w="cloak")
BVH_CLOAK = BVHTree.FromPolygons(V_.tolist(), F_)


def on_cloak(p_):
    o_ = np.array([p_[0], p_[1] + 0.5, p_[2]])
    h_ = BVH_CLOAK.ray_cast(Vector(o_), Vector((0.0, -1.0, 0.0)), 1.0)
    if h_[0] is None:
        return np.asarray(p_, float), np.array([0.0, 1.0, 0.0])
    n_ = unit(np.array(h_[1]))
    return np.array(h_[0]), (n_ if n_[1] > 0 else -n_)


CS = CLOAK_SIGIL
_sig_o, _ = on_cloak(np.array([0.0, AX_Y, CS["z"]]))
for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.cloak_sigil_strokes(CS["h"]), _sig_o, np.array([-1.0, 0.0, 0.0]),
                                                         np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0]), CS["w"], 0.0010,
                                                         "cloak_sigil", project=on_cloak)):
    add_part("cloaksigil.%d" % k, Vk_, Fk_, Rk_, w="cloak")
_orn_z = CS["z"] + 0.42 * CS["h"] - CS["ornament"][0] * 0.4
_oq, _on = on_cloak(np.array([0.0, AX_Y, _orn_z]))
V_, F_, R_ = VP.crystal(_oq + _on * 0.004, _on, CS["ornament"][1], 0.004, 0.002, n=4, up_hint=(0.0, 0.0, 1.0), region="gold",
                        su=CS["ornament"][0] / CS["ornament"][1] * 0.5, phase=0.0)
add_part("cloakornament", V_, F_, R_, w="cloak")
for k_orn, dz_ in enumerate((0.026, -0.026)):
    _oq2, _on2 = on_cloak(np.array([0.0, AX_Y, _orn_z + dz_ * 1.25]))
    V_, F_, R_ = VP.rounded_box(_oq2 + _on2 * 0.003, (1, 0, 0), _on2, (0, 0, 1), 0.0035, 0.0022, 0.009, nr=1, rows=2, bulge=0.0, region="gold")
    add_part("cloakornament_bar.%d" % k_orn, V_, F_, R_, w="cloak")
CLOAK_INFO = {"cols": CL["nu"], "rows": CL["nv"], "top_z": round(_ztop_cl, 4), "hem_z_min": round(float(Vcl[:, 2].min()), 4),
              "width_x_at_hem": round(float(np.ptp(Vcl[-CL["nu"]:, 0])), 4), "sigil_centre": _sig_o.round(4).tolist(),
              "ornament_z": round(float(_orn_z), 4)}

# =========================================================================== the SHOULDER CAPES (two ragged layers, shoulder-hung)
# Elias's capelet rule: columns round the neck / shoulders between the front edges, rows at equal ARC LENGTH down each
# column's draped profile (body incl. arms + hood + cloak + straps: it drapes over the shoulders and upper arms), the drop
# per azimuth (short at the back, long over the shoulders), a tattered hem
CAPE_INFO = []
for li, CP in enumerate(CAPES):
    _cap_host = comb_bvh({"hood", "cloak", "chest_strap", "scarf"} | ({"cape0"} if li else set()),
                         body=(BV, [f for f, n in zip(BF, fdomn) if n != "head"]))
    _zc0 = NECK0[2] + 0.012
    _zcg = np.linspace(_zc0 + 0.03, SHO["L"][2] - 0.45, 180)
    _hem_c = VP.tear_hem(CP["nu"], 0.0, CP["tear"][0], CP["tear"][1], CP["tear"][2], 41.0 + 7.0 * li)
    Vc, Uc = [], []
    for c in range(CP["nu"]):
        u = c / (CP["nu"] - 1)
        ph = -CP["front"] + u * 2.0 * CP["front"]
        ztop_ = float(np.interp(abs(ph), [0.0, 90.0, CP["front"]], [_zc0, NECK0[2] + 0.004, NECK0[2] - 0.030]))
        zz_ = _zcg[_zcg <= ztop_ + 1e-9]
        rr_ = np.maximum.accumulate(radial_profile(_cap_host, 0.0, NECK0[1], ph, zz_)) + CP["clear"]
        arc_ = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(rr_), np.diff(zz_)))])
        D_ = float(np.interp(abs(ph), [0.0, 90.0, CP["front"]], CP["drop"])) + _hem_c[c]
        for j in range(CP["nv"]):
            t_ = j / (CP["nv"] - 1)
            s_ = min(t_ * D_, float(arc_[-1]))
            rr1_ = float(np.interp(s_, arc_, rr_)) + CP["flare"] * t_ ** 1.6
            z1_ = float(np.interp(s_, arc_, zz_))
            a = math.radians(ph)
            Vc.append([rr1_ * math.sin(a), NECK0[1] + rr1_ * math.cos(a), z1_])
    Vc = np.array(Vc)
    _nvc, _nuc = CP["nv"], CP["nu"]
    Fc = []
    for j in range(_nvc - 1):
        for c in range(_nuc - 1):
            Fc.append([c * _nvc + j, (c + 1) * _nvc + j, (c + 1) * _nvc + j + 1, c * _nvc + j + 1])
    Fc = orient_from(Vc, Fc, np.array([0.0, NECK0[1], 0.0]), horiz=True)
    V_, F_, R_ = VP.solidify(Vc, Fc, CP["t"] * 0.5, CP["t"] * 0.5, "cape", "cape_inner", "cape_inner")
    add_part("cape%d" % li, V_, F_, R_, w="transfer")
    CAPE_INFO.append({"hem_z_back": round(float(Vc[(_nuc // 2) * _nvc + _nvc - 1, 2]), 4), "lowest_z": round(float(Vc[:, 2].min()), 4)})

# =========================================================================== the leather SHOULDER PLATES (layered lames)
# each lame an open band round the OUTSIDE of the upper arm (limb_sheet about the shoulder -> elbow axis, centred on the
# lateral direction) over the capes, its lower hem a gold band; shoulder-hung (arm-weighted: they ride the arm)
PLATE_INFO = []
for s in "LR":
    _pl_host = comb_bvh({"cape0", "cape1"}, body=(BV, [f for f, n in zip(BF, fdomn) if n in ("upperarm_" + s.lower(), "clavicle_" + s.lower(), "spine_03")]))
    lat_ = np.array([1.0 if s == "L" else -1.0, 0.0, 0.0])
    Lua = float(np.linalg.norm(ELB[s] - SHO[s]))
    for k, (t0_, t1_, clr_, half_) in enumerate(SHOULDER_PLATES):
        ts_ = np.concatenate([np.linspace(t0_, t1_ - PLATE_TRIM / Lua, 4), [t1_]])
        th_ = np.radians(np.linspace(-half_, half_, 11))
        cf_ = lambda t, th, clr_=clr_, t0_=t0_, t1_=t1_: clr_ + 0.010 * ((t - t0_) / (t1_ - t0_)) ** 1.5
        Vg, Fg, _ = limb_sheet(SHO[s], ELB[s], ts_, th_, cf_, _pl_host, lat_)
        Rg = ["gold" if q_ // (len(th_) - 1) == len(ts_) - 2 else "leather" for q_ in range(len(Fg))]
        V_, F_, R_ = VP.solidify(Vg, Fg, PLATE_T, 0.0, Rg, "leather_dark", "leather_dark")
        add_part("shoulderplate.%s%d" % (s, k), V_, F_, R_, w="transfer")
    PLATE_INFO.append(s)

# =========================================================================== the round gold BROOCH (his left upper chest,
# pinning the cape edge over the left-shoulder strap: the sheet FRONT view)
BVH_FRONT2 = comb_bvh({"chest_strap", "scarf", "cape0", "cape1"}, body=(BV, TRUNK_F))
_bz = NECK0[2] - BROOCH["drop"]
_bh, _bn = surf_hit(BVH_FRONT2, np.array([BROOCH["x"], AX_Y - 0.1, _bz]), np.array([0.0, -1.0, 0.0]), back=0.6, rng=1.2)
BROOCH_C = _bh + _bn * 0.002
_br = BROOCH["r"]
V_, F_, R_ = VP.lathe([(0.0, 0.0030), (_br * 0.62, 0.0030), (_br * 0.70, 0.0046), (_br * 0.92, 0.0046), (_br, 0.0026), (_br * 0.96, 0.0), (0.0, 0.0)],
                      ["gold", "gold", "gold", "gold", "gold_dark", "gold_dark"], 16, BROOCH_C, _bn, up_hint=(0, 0, 1))
add_part("brooch", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.gem(BROOCH_C + _bn * 0.0032, _bn, BROOCH["gem"][0], BROOCH["gem"][1], n=8, region="gem_dark")
add_part("broochgem", V_, F_, R_, w="rigid_transfer")
print("CLOAK", json.dumps({"hood": HOOD_INFO, "cloak": CLOAK_INFO, "capes": CAPE_INFO, "brooch": BROOCH_C.round(4).tolist(),
                           "open_edges": {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}}))
