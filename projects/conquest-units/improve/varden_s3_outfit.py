# Varden build section 3 (adapted from elias_s3_outfit.py): the solid outfit pieces (closed solids, skinned from the body
# under them): eyes, HEAVY boots (Wren's foot shell + sole + shaft + folded cuff with a gold edge + three buckled straps),
# the navy TUNIC SKIRT below the belt (gold hem band + gold front edges) with the white TABARD panel hanging in front (gold
# edges, a touch longer), the gold kingdom-emblem STAR motifs on the skirt's front panels, the wide brown BELT + square gold
# buckle + studs, the slanting lower SWORD BELT, the gold-trimmed leather VAMBRACES (dark gloves are paint), the cream
# under-sleeves bunched over the vambrace tops, the HIGH COLLAR (navy, gold-trimmed top edge, open at the throat).
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


def ring_band(bvh, z0, z1, clear, nu=40, rows=3, ay=None, wobble=0.0, zfn=None):
    """a band round the vertical axis between heights z0 .. z1 sitting `clear` off the outermost surface of bvh; zfn(a) ->
    a height offset per azimuth (the slanting sword belt)."""
    zs_ = np.linspace(z0, z1, rows)
    V = []
    for z in zs_:
        for i in range(nu):
            a_ = 360.0 * i / nu
            zz_ = z + (zfn(a_) if zfn is not None else 0.0)
            r_ = float(rp_front(bvh, a_, [zz_ - 0.01, zz_, zz_ + 0.01], ay=ay).max()) + clear + wobble * math.sin(3.0 * math.radians(a_))
            V.append(np.array([0.0, AX_Y if ay is None else ay, zz_]) + dir_front(a_) * r_)
    V = np.array(V)
    F = VP.grid_faces(nu, rows, closed_u=True)
    cy_ = AX_Y if ay is None else ay
    F = [f if np.dot(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]), np.array([V[f].mean(0)[0], V[f].mean(0)[1] - cy_, 0.0])) > 0
         else f[::-1] for f in F]
    return V, F


def cut_sheet(V, F, fields, cuts, snap=CUT_SNAP):
    """the body's iso-cut machinery on another sheet (Wren's / Elias's): split edges / connect faces along field = tau."""
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


# ---- eyes
for s in "LR":
    V_, F_, R_ = EYE_MESH[s]
    add_part("eye." + s, V_, F_, R_, w="rigid:head")

# ---- boots: foot shell (loft along the foot), sole slab, shaft round the lower shin, folded cuff + gold edge, straps
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
    st = np.concatenate([np.linspace(s0 - 0.016, s0, 4)[:-1], np.linspace(s0, s1, 12), np.linspace(s1, s1 + TOE_EXT, 5)[1:]])
    prof = []
    for sk in st:
        sl = Pf[np.abs(sp - float(np.clip(sk, s0 + 0.006, s1 - 0.006))) < 0.012]
        lc = (sl - ANKLE[s]) @ lat
        prof.append((sk, 0.5 * (lc.min() + lc.max()), 0.5 * (lc.max() - lc.min()), float(sl[:, 2].min()), float(sl[:, 2].max())))
    zt_heel = max(p_[4] for p_ in prof if p_[0] <= s0 + 0.04)

    def shell_ring(sk, c_lat, hw, zb, zt, grow=0.0, nring=12):
        f_ = 1.0
        if sk < s0:
            f_ = math.sqrt(max(0.0, 1.0 - ((s0 - sk) / 0.0165) ** 2))
        elif sk > s1:
            f_ = math.sqrt(max(0.0, 1.0 - ((sk - s1) / (TOE_EXT + 0.0005)) ** 2))
        f_ = max(f_, 0.16)
        w_ = (hw + BOOT_MARGIN[0]) * (f_ if sk > s1 else (0.75 + 0.25 * f_)) + grow
        bot = SOLE_T * 0.5
        top = max(zt, zt_heel if sk < s0 + 0.03 else zt) + BOOT_MARGIN[1]
        if sk > ball_s:
            top = bot + (top - bot) * (0.60 + 0.40 * f_) * (1.0 - 0.15 * smoothstep(ball_s, s1 + TOE_EXT, sk))
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
        w_ = 0.5 * float(np.ptp((r_ - ANKLE[s]) @ lat)) + 0.0045
        cen = ANKLE[s] + fd * sk + lat * c_lat
        sole.append(np.array([[cen[0] + lat[0] * w_ * x, cen[1] + lat[1] * w_ * x, z] for x, z in
                              ((-1.0, 0.0), (1.0, 0.0), (1.0, SOLE_T * 0.85), (-1.0, SOLE_T * 0.85))]))
    V_, F_, R_ = VP.loft(sole, "flat", "flat", reg="boot_sole")
    add_part("sole." + s, V_, F_, R_, w="toe", side=s, fd=fd, c0=C0b)
    sh = SHIN[s]
    Ls = sh["len"]
    ts_ = np.linspace(BOOT_TOP, 0.965, 7)
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
    # VARDEN: three buckled straps round the shaft (sheet: heavy buckled boots), the buckles alternating outer / inner
    out_ = np.array([1.0 if s == "L" else -1.0, 0.0, 0.0])
    for k, (tf_, bs_) in enumerate(BOOT_STRAPS):
        dt_ = 0.5 * BOOT_STRAP[0] / Ls
        clr_s = lambda t, th, tf_=tf_: float(np.interp(tf_, [BOOT_TOP, 0.965], [BOOT_CLEAR[1], BOOT_CLEAR[0]])) + 0.004 + BOOT_STRAP[2]
        Vg, Fg, _ = limb_grid(KNEE[s], ANKLE[s], np.array([tf_ - dt_, tf_ + dt_]), clr_s, LIMB_BVH["leg" + s], 12, front=sh["fr"])
        V_, F_, R_ = VP.solidify(Vg, Fg, BOOT_STRAP[1], 0.0, "strap", "strap", "strap_edge")
        add_part("bootstrap.%s%d" % (s, k), V_, F_, R_, w="transfer")
        ax_c = KNEE[s] + (ANKLE[s] - KNEE[s]) * tf_
        ring_ = Vg[:12]
        dirs_ = ring_ - ax_c
        j_ = int(np.argmax((dirs_ / np.linalg.norm(dirs_, axis=1)[:, None]) @ (out_ * bs_ + sh["fr"] * 0.35)))
        bc_ = Vg[j_] * 0.5 + Vg[12 + j_] * 0.5
        bn_ = unit(bc_ - ax_c - sh["ua"] * float((bc_ - ax_c) @ sh["ua"]))
        bc_ = bc_ + bn_ * (BOOT_STRAP[1] + 0.5 * BOOT_BUCKLE[2])
        V_, F_, R_ = VP.rounded_box(bc_, np.cross(sh["ua"], bn_), bn_, sh["ua"], BOOT_BUCKLE[0], 0.5 * BOOT_BUCKLE[2],
                                    BOOT_BUCKLE[1], nr=1, rows=3, bulge=0.05, region="brass")
        add_part("bootbuckle.%s%d" % (s, k), V_, F_, R_, w="rigid_transfer")
    BOOT_RING[s] = {"fd": fd, "lat": lat, "s0": s0, "s1": s1, "ball_s": ball_s}
    BOOT_INFO[s] = {"foot_len": round(s1 - s0, 4), "shaft_top_z": round(float((KNEE[s] + (ANKLE[s] - KNEE[s]) * BOOT_TOP)[2]), 4),
                    "straps": len(BOOT_STRAPS)}

# ---- the navy TUNIC SKIRT below the belt (Wren's tunic-tail generator): gold hem band, gold edges beside the tabard
BVH_LOWER = BVHTree.FromPolygons(BV.tolist(), TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith("thigh")])
_cols = np.linspace(-180.0, 180.0, SKIRT_NU + 1)[:-1]
_zsk = np.linspace(Z_BELT + 0.02, Z_BELT - max(SKIRT_LEN) - TABARD["len_extra"] - 0.03, 40)
_skp = {a_: np.maximum.accumulate(radial_profile(BVH_LOWER, 0.0, AX_Y, 180.0 + a_, _zsk)) for a_ in _cols}


def skirt_r(a_, z, v):
    """the skirt's radius at azimuth a_ (from the front) and height z (v = 0 at the belt .. 1 at the hem)."""
    k_ = np.interp(a_, list(_cols) + [180.0], list(range(len(_cols))) + [0])
    i0_ = int(math.floor(k_)) % len(_cols); i1_ = (i0_ + 1) % len(_cols); f_ = k_ - math.floor(k_)
    r0_ = float(np.interp(-z, -_zsk, _skp[_cols[i0_]])); r1_ = float(np.interp(-z, -_zsk, _skp[_cols[i1_]]))
    return r0_ * (1 - f_) + r1_ * f_ + SKIRT_CLEAR + SKIRT_FLARE * v ** 1.3


Vsk, vrow_sk, a_sk, dh_sk = [], [], [], []
for j in range(SKIRT_NV):
    v = j / (SKIRT_NV - 1)
    for a_ in _cols:
        back = 0.5 - 0.5 * math.cos(math.radians(a_))
        ln = SKIRT_LEN[0] + (SKIRT_LEN[1] - SKIRT_LEN[0]) * back + 0.006 * math.sin(math.radians(a_) * 5.0 + 0.6) * v
        z = Z_BELT + 0.012 - (ln + 0.012) * v
        r = skirt_r(a_, z, v)
        ph = math.radians(180.0 + a_)
        Vsk.append([r * math.sin(ph), AX_Y + r * math.cos(ph), z]); vrow_sk.append(v); a_sk.append(abs(a_))
        dh_sk.append((ln + 0.012) * (1.0 - v))
Vsk = np.array(Vsk); nsk = len(_cols)
Fsk = VP.grid_faces(nsk, SKIRT_NV, closed_u=True)
Fsk = [f if np.dot(np.cross(Vsk[f[1]] - Vsk[f[0]], Vsk[f[2]] - Vsk[f[0]]), np.array([Vsk[f].mean(0)[0], Vsk[f].mean(0)[1] - AX_Y, 0.0])) > 0
       else f[::-1] for f in Fsk]
_edge_a = TABARD["half_deg"] + TABARD["edge_trim"]
_Vs2, _Fs2, _FLs = cut_sheet(Vsk, Fsk, {"dh": np.array(dh_sk) - SKIRT_HEM_TRIM, "ae": np.array(a_sk) - _edge_a, "v": np.array(vrow_sk)},
                             [("dh", 0.0), ("ae", 0.0)])
_Rs2 = ["tunic_trim" if (np.mean(_FLs["dh"][f]) < 0 or np.mean(_FLs["ae"][f]) < 0) else "tunic" for f in _Fs2]
V_, F_, R_ = VP.solidify(_Vs2, _Fs2, SKIRT_T, 0.0, _Rs2, "tunic_shade", "tunic_trim")
add_part("skirt", V_, F_, R_, w="fauld", v_param=np.tile(_FLs["v"], 2))
SKIRT_INFO = {"hem_z_front": round(Z_BELT - SKIRT_LEN[0], 4), "hem_z_back": round(Z_BELT - SKIRT_LEN[1], 4)}
# ---- the white TABARD panel hanging in front of the skirt (gold edge strips), a touch longer
_tnu, _tnv = 9, 8
_Vt, _vt, _at = [], [], []
_tlen = SKIRT_LEN[0] + TABARD["len_extra"]
for j in range(_tnv):
    v = j / (_tnv - 1)
    z = Z_BELT + 0.010 - (_tlen + 0.010) * v
    for i in range(_tnu):
        a_ = -TABARD["half_deg"] + 2.0 * TABARD["half_deg"] * i / (_tnu - 1)
        r = skirt_r(a_, max(z, Z_BELT - SKIRT_LEN[0]), min(v * (_tlen + 0.01) / (SKIRT_LEN[0] + 0.012), 1.0)) + SKIRT_T + 0.004
        r += 0.010 * v * (1.0 - (a_ / TABARD["half_deg"]) ** 2)          # the panel hangs a little proud at the hem
        ph = math.radians(180.0 + a_)
        _Vt.append([r * math.sin(ph), AX_Y + r * math.cos(ph), z]); _vt.append(v); _at.append(abs(a_))
_Vt = np.array(_Vt)
_Ft = VP.grid_faces(_tnu, _tnv)
_Ft = [f if np.dot(np.cross(_Vt[f[1]] - _Vt[f[0]], _Vt[f[2]] - _Vt[f[0]]), np.array([_Vt[f].mean(0)[0], _Vt[f].mean(0)[1] - AX_Y, 0.0])) > 0
       else f[::-1] for f in _Ft]
_Vt2, _Ft2, _FLt = cut_sheet(_Vt, _Ft, {"ae": TABARD["half_deg"] - TABARD["trim_deg"] - np.array(_at), "v": np.array(_vt)}, [("ae", 0.0)])
_Rt2 = ["tunic_trim" if np.mean(_FLt["ae"][f]) < 0 else "tabard" for f in _Ft2]
V_, F_, R_ = VP.solidify(_Vt2, _Ft2, 0.0030, 0.0, _Rt2, "tabard_shade", "tunic_trim")
add_part("tabard", V_, F_, R_, w="fauld", v_param=np.tile(_FLt["v"], 2))
SKIRT_INFO["tabard_hem_z"] = round(Z_BELT - _tlen, 4)
# ---- the gold kingdom-emblem STARS on the skirt's front panels (mirrored)
BVH_SKIRT = comb_bvh({"skirt"}, with_body=False)
STAR_INFO = []
for a0_, hz_, rs_ in SKIRT_STARS:
    for sg_ in (1.0, -1.0):
        a_ = sg_ * a0_
        z_ = Z_BELT - SKIRT_LEN[0] + hz_
        d_ = dir_front(a_)
        o_ = np.array([0.0, AX_Y, z_]) + d_ * 0.8
        h_ = BVH_SKIRT.ray_cast(Vector(o_), Vector(-d_), 1.0)
        if h_[0] is None:
            continue
        n_ = unit(np.array(h_[1])); n_ = n_ if float(n_ @ d_) > 0 else -n_
        V_, F_, R_ = VP.star_plate(np.array(h_[0]) + n_ * 0.0035, n_, np.array([0.0, 0.0, 1.0]), rs_, rs_ * 0.38, 8, 0.0016)
        add_part("skirtstar.%d" % len(STAR_INFO), V_, F_, R_, w="fauld", v_param=np.full(len(V_), 0.75))
        STAR_INFO.append(np.array(h_[0]).round(4).tolist())

# ---- the wide belt (a band over the tunic top), the square gold buckle (frame + inset), studs
_TRUNK_BODY = (BV, TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith("thigh")])   # (the arms hang beside the belts: excluded)
BVH_BELTP = comb_bvh({"skirt", "tabard"}, body=_TRUNK_BODY)
_bz0, _bz1 = Z_BELT - 0.5 * BELT["h"], Z_BELT + 0.5 * BELT["h"]
Vb_, Fb_ = ring_band(BVH_BELTP, _bz0, _bz1, BELT["clear"], nu=36, rows=3)
V_, F_, R_ = VP.solidify(Vb_, Fb_, BELT["t"], 0.0, "belt", "belt", "belt_edge")
add_part("belt", V_, F_, R_, w="transfer")
BVH_BELT = comb_bvh({"skirt", "tabard", "belt"}, body=_TRUNK_BODY)
_bf = BVH_BELT.ray_cast(Vector((0.0, -0.8, Z_BELT)), Vector((0.0, 1.0, 0.0)), 1.5)
BUCKLE_C = np.array(_bf[0]) + np.array([0.0, -0.002 - BUCKLE[2] * 0.5, 0.0])
for k_, (dx_, dz_, hx_, hz_) in enumerate(((0.0, BUCKLE[1] - 0.004, BUCKLE[0], 0.004), (0.0, -BUCKLE[1] + 0.004, BUCKLE[0], 0.004),
                                           (BUCKLE[0] - 0.004, 0.0, 0.004, BUCKLE[1]), (-BUCKLE[0] + 0.004, 0.0, 0.004, BUCKLE[1]))):
    V_, F_, R_ = VP.rounded_box(BUCKLE_C + np.array([dx_, 0.0, dz_]), (1, 0, 0), (0, -1, 0), (0, 0, 1), hx_, BUCKLE[2] * 0.5, hz_,
                                nr=1, rows=2, bulge=0.0, region="brass")
    add_part("buckle.%d" % k_, V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.rounded_box(BUCKLE_C + np.array([0.0, BUCKLE[2] * 0.15, 0.0]), (1, 0, 0), (0, -1, 0), (0, 0, 1), 0.0022, BUCKLE[2] * 0.45,
                            BUCKLE[1] * 0.9, nr=1, rows=2, bulge=0.0, region="brass_dark")
add_part("buckle.prong", V_, F_, R_, w="rigid_transfer")
for k_ in range(BELT_STUDS[0]):
    a_ = 360.0 * (k_ + 0.5) / BELT_STUDS[0]
    if abs(((a_ + 180.0) % 360.0) - 180.0) < 16.0:
        continue                                            # (no studs behind the buckle)
    d_ = dir_front(a_)
    h_ = BVH_BELT.ray_cast(Vector(np.array([0.0, AX_Y, Z_BELT]) + d_ * 0.8), Vector(-d_), 1.0)
    if h_[0] is None:
        continue
    V_, F_, R_ = VP.gem(np.array(h_[0]) + d_ * 0.0006, d_, BELT_STUDS[1], 0.0022, n=4, region="brass")
    add_part("beltstud.%d" % k_, V_, F_, R_, w="rigid_transfer")
# ---- the SWORD BELT: a narrower band round the hips below the belt, slanting down to his left hip (the scabbard side)
_sbz = Z_BELT - SWORD_BELT["drop"]
_tt = math.tan(math.radians(SWORD_BELT["tilt_deg"]))
_hipr = 0.20
Vb_, Fb_ = ring_band(BVH_BELT, _sbz - 0.5 * SWORD_BELT["h"], _sbz + 0.5 * SWORD_BELT["h"], SWORD_BELT["clear"] + BELT["t"] * 0.2,
                     nu=36, rows=3, zfn=lambda a_: -_tt * _hipr * math.sin(math.radians(a_)))
V_, F_, R_ = VP.solidify(Vb_, Fb_, SWORD_BELT["t"], 0.0, "belt", "belt", "belt_edge")
add_part("swordbelt", V_, F_, R_, w="fauld", v_param=np.full(len(V_), 0.15))

# ---- the cream under-sleeves bunched over the vambrace tops
for s in "LR":
    fa = ELB[s] - WRI[s]
    c0_ = WRI[s] + fa * VAMBRACE["t"][1]
    a_ = unit(-fa)
    h_ = SLEEVE_ROLL[0]
    A_ = c0_ - a_ * h_ * 0.70; B_ = c0_ + a_ * h_ * 0.30
    clr = lambda t, th: SLEEVE_ROLL[2] + SLEEVE_ROLL[1] * math.sin(math.pi * t) ** 0.7 * (1.0 + 0.14 * math.sin(5 * th + 0.5))
    Vg, Fg, _ = limb_grid(A_, B_, np.linspace(0.0, 1.0, 6), clr, LIMB_BVH["arm" + s], 16)
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.0025, 0.0, "sleeve_roll", "sleeve", "sleeve_roll")
    add_part("sleeveroll." + s, V_, F_, R_, w="transfer")

# ---- the VAMBRACES on both forearms (dark leather, flared at the elbow end, gold rims at both ends + a gold centre ridge)
VAMB_INFO = {}
for s in "LR":
    VB = VAMBRACE
    fa = ELB[s] - WRI[s]
    A_ = WRI[s] + fa * VB["t"][0]; B_ = WRI[s] + fa * VB["t"][1]
    clr = lambda t, th: VB["clear"] + 0.0024 * math.sin(math.pi * t) + VB["flare"] * t ** 2.5
    Vg, Fg, _ = limb_grid(A_, B_, np.linspace(0.0, 1.0, VB["nv"]), clr, LIMB_BVH["arm" + s], VB["nu"])
    V_, F_, R_ = VP.solidify(Vg, Fg, VB["t_leather"], 0.0, "vambrace", "vambrace", "vambrace")
    add_part("vambrace." + s, V_, F_, R_, w="transfer")
    Lfa = float(np.linalg.norm(B_ - A_))
    for k, t0_ in enumerate((0.0, 1.0 - VB["trim_w"] / Lfa)):
        clr_r = lambda t, th, t0_=t0_: VB["clear"] + VB["t_leather"] + 0.0008 + 0.0024 * math.sin(math.pi * min(max(t0_, 0.0), 1.0)) + \
            VB["flare"] * min(max(t, 0.0), 1.0) ** 2.5
        Vg, Fg, _ = limb_grid(A_, B_, np.array([t0_, t0_ + VB["trim_w"] / Lfa]), clr_r, LIMB_BVH["arm" + s], VB["nu"])
        V_, F_, R_ = VP.solidify(Vg, Fg, 0.0016, 0.0, "brass", "brass", "brass")
        add_part("vambtrim.%s%d" % (s, k), V_, F_, R_, w="transfer")
    VAMB_INFO[s] = {"span_m": round(Lfa, 4)}

# ---- the HIGH COLLAR: a standing band round the neck (navy), gold-trimmed top edge, open at the throat
CO = COLLAR
_cz0, _cz1 = NECK0[2] - CO["below"], NECK0[2] + CO["h"]
_czs = [_cz0, 0.5 * (_cz0 + _cz1), _cz1 - CO["trim"], _cz1]
_cang = np.linspace(CO["gap_deg"], 360.0 - CO["gap_deg"], 30)
_Vc = []
for j, z in enumerate(_czs):
    t_ = (z - _cz0) / (_cz1 - _cz0)
    for a_ in _cang:
        zb_ = z + CO["back_rise"] * t_ * (0.5 - 0.5 * math.cos(math.radians(a_)))     # higher at the back of the neck
        r_ = float(rp_front(BVH_BODY, a_, [zb_ - 0.006, zb_, zb_ + 0.006], ay=NECK0[1]).max()) + CO["clear"] + CO["flare"] * t_ ** 1.5
        _Vc.append(np.array([0.0, NECK0[1], zb_]) + dir_front(a_) * r_)
_Vc = np.array(_Vc)
_Fc = VP.grid_faces(len(_cang), len(_czs))
_Fc = [f if np.dot(np.cross(_Vc[f[1]] - _Vc[f[0]], _Vc[f[2]] - _Vc[f[0]]), np.array([_Vc[f].mean(0)[0], _Vc[f].mean(0)[1] - NECK0[1], 0.0])) > 0
       else f[::-1] for f in _Fc]
_Rc = ["tunic_trim" if fi // (len(_cang) - 1) == len(_czs) - 2 else "tunic" for fi in range(len(_Fc))]
V_, F_, R_ = VP.solidify(_Vc, _Fc, CO["t"], 0.0, _Rc, "tunic_shade", "tunic_trim")
add_part("collar", V_, F_, R_, w="collar")
print("OUTFIT", json.dumps({"boots": BOOT_INFO, "skirt": SKIRT_INFO, "stars": STAR_INFO, "vambraces": VAMB_INFO,
                            "open_edges": {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}}))
