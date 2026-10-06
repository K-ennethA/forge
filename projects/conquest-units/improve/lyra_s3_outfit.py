# Lyra build section 3 (adapted from elias_s3_outfit.py + shadow_assassin_s3_outfit.py's boot straps): the solid outfit
# pieces (closed solids, skinned from the body under them): eyes, boots (Elias's foot shell + sole + shaft + folded cuff +
# shadow-assassin's buckled straps), the puffed rolled shirt sleeves, the wrist bracelets, the shirt collar + the blue
# necktie ribbon (knot, two tails on a follow-through chain, gold diamond pin), the dark CORSET + two leather belts + gold
# buckles, the hanging vials + compass medallions + the hip bag, the cream SKIRT PANELS (open at the front over the trousers,
# navy panels, gold trims, pointed hem, patterned hem band + diamond motifs, the compass emblem on her left navy panel) and
# the navy front tab. TRUNK_HUNG law (style guide): the corset / belts / skirt / bag / vial / medallion geometry is built
# against TRUNK-ONLY BVHs (never the resting hands / arms) and s7 weights them from the trunk only.
PARTS = []          # dicts: name, V, F, R, w ('transfer' | 'rigid:<bone>' | 'rigid_transfer' | scheme), obj, + extras


ZERO_AREA_DROPPED = {}


def add_part(name, V, F, R, w="transfer", obj="main", **kw):
    V = np.asarray(V, float)
    keep_ = [float(np.linalg.norm(sum(np.cross(V[f[k]] - V[f[0]], V[f[k + 1]] - V[f[0]]) for k in range(1, len(f) - 1)))) > 2e-9 for f in F]
    if not all(keep_):                                  # (zero-area rim quads + slivers < 1e-9 m2 from the iso-cut snaps: no stable normal -> drop them)
        ZERO_AREA_DROPPED[name] = int(len(keep_) - sum(keep_))
        F = [f for f, k_ in zip(F, keep_) if k_]; R = [r_ for r_, k_ in zip(R, keep_) if k_]
    d = {"name": name, "V": V, "F": [list(map(int, f)) for f in F], "R": list(R), "w": w, "obj": obj}
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
    """horizontal unit direction at azimuth a from the FRONT (+ toward her left)."""
    a_ = math.radians(a_deg)
    return np.array([math.sin(a_), -math.cos(a_), 0.0])


def rp_front(bvh, a_deg, zs, ax=0.0, ay=None):
    """outermost surface radius from the vertical axis (ax, ay) at azimuth a from the FRONT (+ her left)."""
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


def ring_band(bvh, z0, z1, clear, nu=40, rows=3, ay=None, wobble=0.0, slope=0.0):
    """a band round the vertical axis between heights z0 .. z1 sitting `clear` off the outermost surface of bvh; slope =
    z drop per unit x toward her right (a slung hip belt)."""
    zs_ = np.linspace(z0, z1, rows)
    V = []
    for z in zs_:
        for i in range(nu):
            a_ = 360.0 * i / nu
            dz_ = slope * max(0.0, -float(dir_front(a_)[0]))
            r_ = float(rp_front(bvh, a_, [z + dz_ - 0.01, z + dz_, z + dz_ + 0.01], ay=ay).max()) + clear + wobble * math.sin(3.0 * math.radians(a_))
            V.append(np.array([0.0, AX_Y if ay is None else ay, z + dz_]) + dir_front(a_) * r_)
    V = np.array(V)
    F = VP.grid_faces(nu, rows, closed_u=True)
    cy_ = AX_Y if ay is None else ay
    F = [f if np.dot(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]), np.array([V[f].mean(0)[0], V[f].mean(0)[1] - cy_, 0.0])) > 0
         else f[::-1] for f in F]
    return V, F


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


# ---- eyes
for s in "LR":
    V_, F_, R_ = EYE_MESH[s]
    add_part("eye." + s, V_, F_, R_, w="rigid:head")

# ---- boots: foot shell (loft along the foot), sole slab, shaft round the lower shin, folded cuff, buckled straps
BOOT_INFO = {}
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
    ts_ = np.linspace(BOOT_TOP, 0.965, 7)
    clr = lambda t, th: float(np.interp(t, [BOOT_TOP, 0.965], [BOOT_CLEAR[1], BOOT_CLEAR[0]]))
    Vg, Fg, fr_ = limb_grid(KNEE[s], ANKLE[s], ts_, clr, LIMB_BVH["leg" + s], 16, front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.004, 0.0, "boot", "boot", "boot")
    add_part("bootshaft." + s, V_, F_, R_, w="transfer")
    tc = np.linspace(BOOT_TOP - 0.004, BOOT_TOP + CUFF[0] / Ls, 4)
    clr_c = lambda t, th: BOOT_CLEAR[1] + 0.004 + CUFF[1] * 0.3 + CUFF[2] * ((t - tc[0]) / (tc[-1] - tc[0])) ** 1.5
    Vg, Fg, _ = limb_grid(KNEE[s], ANKLE[s], tc, clr_c, LIMB_BVH["leg" + s], 16, front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, CUFF[3], 0.0, "boot_cuff", "boot_strap", "boot_cuff")
    add_part("bootcuff." + s, V_, F_, R_, w="transfer")
    straps_ = []
    for k, tq in enumerate(BOOT_STRAPS):
        t_c = BOOT_TOP + (0.965 - BOOT_TOP) * tq
        hh_ = 0.5 * BOOT_STRAP[0] / Ls
        clr_s = lambda t, th, t_c=t_c: float(np.interp(t_c, [BOOT_TOP, 0.965], [BOOT_CLEAR[1], BOOT_CLEAR[0]])) + 0.0042
        Vg, Fg, _ = limb_grid(KNEE[s], ANKLE[s], np.array([t_c - hh_, t_c + hh_]), clr_s, LIMB_BVH["leg" + s], 16, front=sh["fr"])
        V_, F_, R_ = VP.solidify(Vg, Fg, BOOT_STRAP[1], 0.0, "boot_strap", "boot_strap", "boot_strap")
        add_part("bootstrap.%s%d" % (s, k), V_, F_, R_, w="transfer")
        cen_ = KNEE[s] + (ANKLE[s] - KNEE[s]) * t_c
        out_ = unit(sh["lat"] * (1.0 if s == "L" else -1.0) * 0.8 + sh["fr"] * 0.6)
        ring_pts = Vg.reshape(2, 16, 3).mean(0)
        j_ = int(np.argmax((ring_pts - cen_) @ out_))
        bc_ = ring_pts[j_] + out_ * (BOOT_STRAP[1] + BOOT_STRAP[2][1])
        V_, F_, R_ = VP.rounded_box(bc_, unit(np.cross(sh["ua"], out_)), out_, -sh["ua"], *BOOT_STRAP[2], nr=1, rows=3, bulge=0.0,
                                    region="gold")
        add_part("bootbuckle.%s%d" % (s, k), V_, F_, R_, w="rigid_transfer")
        straps_.append(round(float(cen_[2]), 3))
    BOOT_INFO[s] = {"foot_len": round(s1 - s0, 4), "shaft_top_z": round(float((KNEE[s] + (ANKLE[s] - KNEE[s]) * BOOT_TOP)[2]), 4),
                    "strap_z": straps_}

# ---- the puffed shirt sleeves (upper arm -> elbow -> rolled cuff) + the rolled cuffs + the wrist bracelets
SP = SLEEVE_PUFF
for s in "LR":
    ua_ = ELB[s] - SHO[s]; fa = WRI[s] - ELB[s]
    c0_ = ELB[s] + fa * SLEEVE_T
    A1 = SHO[s] + ua_ * SP["t"][0]
    clr1 = lambda t, th: SP["clear"][0] + (SP["clear"][1] - SP["clear"][0]) * smoothstep(0.0, 1.0, t) ** 1.2
    Vg, Fg, _ = limb_grid(A1, ELB[s], np.linspace(0.0, 1.0, SP["nv"]), clr1, LIMB_BVH["arm" + s], SP["nu"])
    V_, F_, R_ = VP.solidify(Vg, Fg, SP["t_cloth"], 0.0, "shirt", "shirt_shade", "shirt")
    add_part("sleevepuff." + s, V_, F_, R_, w="transfer")
    B2 = c0_ - unit(fa) * SLEEVE_ROLL[0] * 0.3
    clr2 = lambda t, th: SP["clear"][1] * (1.0 - (1.0 - SP["fore"]) * t) * (1.0 - 0.15 * smoothstep(0.0, 1.0, t))
    Vg, Fg, _ = limb_grid(ELB[s] - unit(fa) * 0.004, B2, np.linspace(0.0, 1.0, max(SP["nv"] - 2, 3)), clr2, LIMB_BVH["arm" + s], SP["nu"])
    V_, F_, R_ = VP.solidify(Vg, Fg, SP["t_cloth"], 0.0, "shirt", "shirt_shade", "shirt")
    add_part("sleevelow." + s, V_, F_, R_, w="transfer")
    a_ = unit(fa)
    h_ = SLEEVE_ROLL[0]
    A_ = c0_ - a_ * h_ * 0.62; B_ = c0_ + a_ * h_ * 0.38
    clr = lambda t, th: SLEEVE_ROLL[2] + SLEEVE_ROLL[1] * math.sin(math.pi * t) ** 0.7 * (1.0 + 0.10 * math.sin(4 * th + 0.5)) + \
        SP["clear"][1] * SP["fore"] * 0.55 * (1.0 - t)
    Vg, Fg, _ = limb_grid(A_, B_, np.linspace(0.0, 1.0, 6), clr, LIMB_BVH["arm" + s], 16)
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.0025, 0.0, "shirt_roll", "shirt_shade", "shirt_roll")
    add_part("sleeveroll." + s, V_, F_, R_, w="transfer")
    # bracelet: a dark band just above the wrist joint (the sheet: both wrists)
    cb_ = WRI[s] - a_ * BRACELET[0]
    Vg, Fg, _ = limb_grid(cb_ - a_ * 0.004, cb_ + a_ * 0.004, np.array([0.0, 1.0]), lambda t, th: BRACELET[2], LIMB_BVH["arm" + s], 14)
    V_, F_, R_ = VP.solidify(Vg, Fg, BRACELET[1], 0.0, "bracelet", "bracelet", "bracelet")
    add_part("bracelet." + s, V_, F_, R_, w="transfer")

# ---- the shirt COLLAR (a stand band round the neck + two front points) + the NECKTIE ribbon (knot, two tails, gold pin)
CL = COLLAR
Z_COL = NECK0[2] + CL["z"]
_npts = []
for i in range(28):
    a_ = 360.0 * i / 28
    r_ = float(rp_front(BVH_BODY, a_, [Z_COL - 0.008, Z_COL, Z_COL + 0.008], ay=NECK0[1]).max())
    zz_ = Z_COL - 0.010 * max(0.0, math.cos(math.radians(a_))) ** 2      # dips at the front toward the knot
    _npts.append(np.array([0.0, NECK0[1], zz_]) + dir_front(a_) * (r_ + CL["band_r"] * 0.9))
V_, F_, R_, _ = VP.tube_path(np.array(_npts), CL["band_r"], 6, "collar", closed=True, su=CL["band_flat"], up_hint=(0.0, 0.0, 1.0))
add_part("collarband", V_, F_, R_, w="collar")
_kh = BVH_BODY.ray_cast(Vector((0.0, -0.8, Z_COL - 0.014)), Vector((0.0, 1.0, 0.0)), 1.5)
KNOT_C = np.array(_kh[0]) + np.array([0.0, -NECKTIE["knot"][1] - 0.005, 0.0])
for sg_ in (1.0, -1.0):                                    # the collar points: thin plates angled down-out from the knot
    fl_, fw_, ft_, sp_ = CL["flap"]
    d_ = unit(np.array([sg_ * math.sin(math.radians(sp_)), -0.15, -math.cos(math.radians(sp_))]))
    c_ = KNOT_C + np.array([sg_ * 0.012, -0.001, 0.004]) + d_ * (0.5 * fl_)
    ex_ = unit(np.cross(d_, [0.0, -1.0, 0.0])); ey_ = unit(np.cross(ex_, d_))
    if ey_[1] > 0:
        ey_ = -ey_
    V_, F_, R_ = VP.rounded_box(c_, ex_, ey_, d_, 0.5 * fw_, ft_, 0.5 * fl_, nr=1, rows=3, bulge=0.0, region="collar")
    V_ = V_ + (V_ - c_) @ np.outer(d_, ex_) * 0.0                      # (kept rectangular; the point is the taper below)
    tp_ = ((V_ - c_) @ d_) / (0.5 * fl_)                                # taper toward the tip: a pointed collar
    V_ = V_ - np.outer(((V_ - c_) @ ex_) * np.clip(0.5 + 0.5 * tp_, 0.0, 1.0) * 0.75, ex_)
    add_part("collarflap.%s" % ("L" if sg_ > 0 else "R"), V_, F_, R_, w="collar")
V_, F_, R_ = VP.rounded_box(KNOT_C, (1, 0, 0), (0, -1, 0), (0, 0, 1), *NECKTIE["knot"], nr=2, rows=5, bulge=0.25, region="tie")
add_part("tieknot", V_, F_, R_, w="collar")
_bvh_tie = comb_bvh({"tieknot", "collarband"})
TIE_TAILS = []
for k, (x_, ln_, hw_, spl_) in enumerate(NECKTIE["tails"]):
    p0_ = KNOT_C + np.array([x_, -0.002, -NECKTIE["knot"][2] * 0.6])
    pts_ = []
    for j in range(9):
        t_ = j / 8.0
        p_ = p0_ + np.array([math.sin(math.radians(spl_)) * ln_ * t_, 0.0, -math.cos(math.radians(spl_)) * ln_ * t_])
        h_ = BVH_BODY.ray_cast(Vector((float(p_[0]), -0.8, float(p_[2]))), Vector((0.0, 1.0, 0.0)), 1.5)
        if h_[0] is not None:
            p_[1] = min(float(h_[0][1]) - NECKTIE["t"] - 0.0035 - 0.004 * (1.0 - t_), p0_[1] + 0.002 * t_)
        pts_.append(p_)
    C_ = VP.resample(VP.catmull(np.array(pts_), 4), 12)[0]
    N_ = np.tile(np.array([0.0, -1.0, 0.0]), (len(C_), 1))
    tw_ = hw_ * np.concatenate([np.linspace(0.70, 1.0, 4), np.ones(len(C_) - 4)])
    rings_ = []
    T_ = np.gradient(C_, axis=0)
    for q in range(len(C_)):
        t3 = unit(T_[q]); n3 = unit(N_[q] - t3 * float(N_[q] @ t3)); w3 = np.cross(t3, n3)
        hwq = tw_[q] * (1.0 if q < len(C_) - 1 else 0.6)
        rings_.append(np.array([C_[q] + w3 * hwq + n3 * NECKTIE["t"], C_[q] - w3 * hwq + n3 * NECKTIE["t"],
                                C_[q] - w3 * hwq - n3 * NECKTIE["t"], C_[q] + w3 * hwq - n3 * NECKTIE["t"]]))
    # the swallow-tail cut at the end: the last ring's middle pushed up (a V notch reads at the tip)
    V_, F_, R_ = VP.loft(rings_, "flat", "flat", reg="tie")
    R_ = ["tie_shade" if (np.cross(V_[f[1]] - V_[f[0]], V_[f[2]] - V_[f[0]])[1] > 0) else r_ for f, r_ in zip(F_, R_)]
    sv_ = np.concatenate([np.repeat(np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C_, axis=0), axis=1))]), 4), [0.0, ln_]])
    add_part("tietail.%d" % k, V_, F_, R_, w="lock", s=sv_, s_leave=0.012, L=float(sv_.max()), chain="necktie",
             path=C_, chain_parent="spine_03", chain_def=True)
    TIE_TAILS.append({"len": ln_, "end": C_[-1].round(4).tolist()})
_pc = KNOT_C + np.array([0.0, -NECKTIE["knot"][1] - 0.0015, -0.002])
V_, F_, R_ = VP.crystal(_pc, (0.0, -1.0, 0.0), NECKTIE["pin"][0], NECKTIE["pin"][1], NECKTIE["pin"][1] * 0.6, n=4,
                        up_hint=(0.0, 0.0, 1.0), region="gold", su=1.0, sv=1.5, phase=0.0)
add_part("tiepin", V_, F_, R_, w="collar")

# ---- the CORSET (trunk-hung) + two leather belts + gold buckles
BVH_TRUNKONLY = BVHTree.FromPolygons(BV.tolist(), TRUNK_F)
_cz0, _cz1 = Z_WAIST + CORSET["z"][0], Z_WAIST + CORSET["z"][1]
Vb_, Fb_ = ring_band(BVH_TRUNKONLY, _cz0, _cz1, CORSET["clear"], nu=44, rows=6)
V_, F_, R_ = VP.solidify(Vb_, Fb_, CORSET["t"], 0.0, "corset", "corset", "corset_lace")
add_part("corset", V_, F_, R_, w="transfer")
BVH_CORSET = comb_bvh({"corset"}, body=(BV, TRUNK_F))
BELT_Z = []
for k, (dz_, h_, cl_, slope_) in enumerate(BELTS):
    zc_ = Z_WAIST + dz_
    Vb_, Fb_ = ring_band(BVH_CORSET, zc_ - 0.5 * h_, zc_ + 0.5 * h_, cl_, nu=44, rows=3, slope=slope_)
    V_, F_, R_ = VP.solidify(Vb_, Fb_, BELT_T, 0.0, "belt", "belt", "belt_edge")
    add_part("belt.%d" % k, V_, F_, R_, w="transfer")
    BELT_Z.append(zc_)
BVH_BELTS = comb_bvh({"corset", "belt"}, body=(BV, TRUNK_F))
for k, (bi_, az_, (hx_, hz_, hy_)) in enumerate(BUCKLES):
    zc_ = BELT_Z[bi_] + BELTS[bi_][3] * max(0.0, -float(dir_front(az_)[0]))
    _pr = float(rp_front(BVH_BELTS, az_, [zc_ - 0.006, zc_, zc_ + 0.006]).max())
    _pd = dir_front(az_); _pt = np.cross([0.0, 0.0, 1.0], _pd)
    C_ = np.array([0.0, AX_Y, zc_]) + _pd * (_pr + hy_ * 0.5 + 0.001)
    Vr_, Fr_, Rr_ = VP.torus(0.5 * (hx_ + hz_) * 0.8, 0.0022, 14, 4, C_, _pd, up_hint=(0.0, 0.0, 1.0), su=hx_ / (0.5 * (hx_ + hz_)), sv=hz_ / (0.5 * (hx_ + hz_)), region="gold")
    add_part("buckle.%d" % k, Vr_, Fr_, Rr_, w="rigid_transfer")
Z_LOWBELT = min(BELT_Z) - 0.5 * BELTS[int(np.argmin(BELT_Z))][1]

# ---- hanging vials (glass + cork) and compass medallions (gold ring + disc + star) off the lower belt at her left; the hip bag
_lbz = BELT_Z[int(np.argmin(BELT_Z))]
for k, (az_, ln_, r_) in enumerate(VIALS):
    _pd = dir_front(az_); _pt = np.cross([0.0, 0.0, 1.0], _pd)
    ztop_ = _lbz - 0.006
    _pr = float(rp_front(BVH_BELTS, az_, np.linspace(ztop_ - ln_, ztop_, 7)).max())
    C_ = np.array([0.0, AX_Y, ztop_ - 0.5 * ln_]) + _pd * (_pr + r_ + 0.002)
    hl_ = 0.5 * ln_
    prof_ = [(0.0, -hl_), (r_ * 0.8, -hl_), (r_, -hl_ + 0.006), (r_, hl_ - 0.016), (r_ * 0.55, hl_ - 0.010), (r_ * 0.55, hl_ - 0.008),
             (r_ * 0.62, hl_), (0.0, hl_ + 0.002)]
    V_, F_, R_ = VP.lathe(prof_, ["glass", "glass", "glass", "glass", "glass", "cork", "cork"], 8, C_, (0.0, 0.0, 1.0), up_hint=_pd)
    add_part("vial.%d" % k, V_, F_, R_, w="rigid_transfer")
MED_INFO = []
for k, (az_, drop_, r_) in enumerate(MEDALLIONS):
    _pd = dir_front(az_); _pt = np.cross([0.0, 0.0, 1.0], _pd)
    zc_ = _lbz - drop_
    _pr = float(rp_front(BVH_BELTS, az_, np.linspace(zc_ - r_, zc_ + r_, 7)).max())
    C_ = np.array([0.0, AX_Y, zc_]) + _pd * (_pr + 0.004)
    V_, F_, R_ = VP.torus(r_, 0.0024, 20, 5, C_, _pd, up_hint=(0.0, 0.0, 1.0), region="gold")
    add_part("medal.%d" % k, V_, F_, R_, w="rigid_transfer")
    V_, F_, R_ = VP.lathe([(0.0, 0.0012), (r_ * 0.92, 0.0012), (r_ * 0.92, -0.0012), (0.0, -0.0012)], ["gold_dark", "gold_dark", "gold_dark"],
                          16, C_, _pd, up_hint=(0, 0, 1))
    add_part("medaldisc.%d" % k, V_, F_, R_, w="rigid_transfer")
    V_, F_, R_, _ = VP.tube_path(np.array([C_ + np.array([0, 0, r_]), np.array([0.0, AX_Y, _lbz]) + _pd * (_pr + 0.004)]), 0.0016, 5, "strap")
    add_part("medalcord.%d" % k, V_, F_, R_, w="rigid_transfer")
    MED_INFO.append(C_.round(4).tolist())
_az, (hx_, hy_, hz_) = POUCH
_pd = dir_front(_az); _pt = np.cross([0.0, 0.0, 1.0], _pd)
_pz = _lbz - 0.012 - hz_ * 0.8
_pr = float(rp_front(BVH_BELTS, _az, np.linspace(_pz - hz_, _pz + hz_, 9)).max())
POUCH_C = np.array([0.0, AX_Y, _pz]) + _pd * (_pr + hy_ + 0.020)
V_, F_, R_ = VP.rounded_box(POUCH_C, _pt, _pd, [0.0, 0.0, 1.0], hx_, hy_, hz_, nr=3, rows=6, bulge=0.10, region="pouch")
add_part("pouch", V_, F_, R_, w="rigid_transfer")
_fc = POUCH_C + _pd * (hy_ * 0.95) + np.array([0.0, 0.0, hz_ * 0.40])
V_, F_, R_ = VP.rounded_box(_fc, _pt, _pd, [0.0, 0.0, 1.0], hx_ * 1.05, 0.0032, hz_ * 0.62, nr=2, rows=3, bulge=0.04, region="pouch_flap")
add_part("pouchflap", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.rounded_box(_fc + _pd * 0.0040 - np.array([0.0, 0.0, hz_ * 0.45]), _pt, _pd, [0.0, 0.0, 1.0], 0.009, 0.0020, 0.007,
                            nr=1, rows=3, bulge=0.0, region="gold")
add_part("pouchbuckle", V_, F_, R_, w="rigid_transfer")
V_, F_, R_, _ = VP.tube_path(np.array([POUCH_C + np.array([0.0, 0.0, hz_]) - _pd * hy_ * 0.3, np.array([0.0, AX_Y, _lbz - 0.008]) +
                                       _pd * (_pr + 0.003)]), 0.0030, 5, "strap", su=2.0)
add_part("pouchstrap", V_, F_, R_, w="rigid_transfer")

# ---- the SKIRT PANELS: an open-front skirt below the belts (columns about the BACK, from -(180 - open) to +(180 - open)),
# rows hanging off the trunk + thighs + corset / belts (TRUNK-ONLY hosts), pointed hem; painted by azimuth bands
SK = SKIRT
BVH_LOWER = comb_bvh({"corset", "belt"}, body=(BV, TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith("thigh")]))
Z_SK_TOP = Z_LOWBELT + 0.010
_pe = 180.0 - SK["open"]
_zsk = np.linspace(Z_SK_TOP + 0.02, Z_SK_TOP - max(SK["len"]) - 0.05, 60)
_phc = np.linspace(-_pe, _pe, SK["nu"])
_skp = {ph_: np.maximum.accumulate(radial_profile(BVH_LOWER, 0.0, AX_Y, ph_, _zsk)) for ph_ in _phc}
Vsk, UVsk = [], []
for j in range(SK["nv"]):
    v = j / (SK["nv"] - 1)
    for c, ph_ in enumerate(_phc):
        u = c / (SK["nu"] - 1)
        azf_ = (180.0 - abs(ph_)) * (1.0 if ph_ > 0 else -1.0)          # azimuth from the FRONT (+ her left)
        back = 0.5 - 0.5 * math.cos(math.radians(azf_))
        ln_ = SK["len"][0] + (SK["len"][1] - SK["len"][0]) * back
        saw_ = abs(((u * SK["points"][1]) % 1.0) * 2.0 - 1.0)              # 1 at the points, 0 between
        tear_ = SK["tear"][1] * (0.4 + 0.8 * hash01(c, 2.0)) + (SK["tear"][0] if hash01(c, 4.0) > 0.85 else 0.0)
        hem_ = Z_SK_TOP - ln_ - SK["points"][0] * (saw_ - 0.5) - tear_ * 0.3
        z = Z_SK_TOP + (hem_ - Z_SK_TOP) * v
        r = float(np.interp(-z, -_zsk, _skp[ph_])) + SK["clear"] + SK["flare"] * v ** 1.4
        a = math.radians(ph_)
        Vsk.append([r * math.sin(a), AX_Y + r * math.cos(a), z]); UVsk.append([u, v, azf_])
Vsk = np.array(Vsk); UVsk = np.array(UVsk)
Fsk = VP.grid_faces(SK["nu"], SK["nv"])
Fsk = [f if np.dot(np.cross(Vsk[f[1]] - Vsk[f[0]], Vsk[f[2]] - Vsk[f[0]]), np.array([Vsk[f].mean(0)[0], Vsk[f].mean(0)[1] - AX_Y, 0.0])) > 0
       else f[::-1] for f in Fsk]
_Gs = Vsk.reshape(SK["nv"], SK["nu"], 3)
_Bs = np.concatenate([np.zeros((1, SK["nu"])), np.cumsum(np.linalg.norm(np.diff(_Gs, axis=0), axis=2), axis=0)], 0)
_dhs = (_Bs[-1:, :] - _Bs).reshape(-1)                         # distance UP from the hem along the column
_rad_mid = float(np.mean([np.interp(-(Z_SK_TOP - 0.3), -_zsk, _skp[p_]) for p_ in _phc])) + SK["clear"]
_trim_deg = math.degrees(SKIRT_TRIM / max(_rad_mid, 0.05))
_navy_d = np.full(len(Vsk), 9e9)                              # signed degrees inside the nearest navy band (> 0 inside)
for a0_, a1_ in SKIRT_NAVY:
    az_ = UVsk[:, 2].copy()
    az_ = np.where(az_ < a0_ - 180.0, az_ + 360.0, np.where(az_ > a1_ + 180.0, az_ - 360.0, az_))
    ins_ = np.minimum(az_ - a0_, a1_ - az_)
    _navy_d = np.where(np.abs(ins_) < np.abs(_navy_d), ins_, _navy_d)
_edge_d = np.minimum(np.abs(UVsk[:, 2] - SK["open"]), np.abs(UVsk[:, 2] + SK["open"]))   # (the open front edges: trimmed too)
_fields = {"dh": _dhs, "navy": _navy_d, "edge": _edge_d}
_cuts = [("dh", SKIRT_TRIM), ("dh", SKIRT_BAND[0] - 0.5 * SKIRT_BAND[1]), ("dh", SKIRT_BAND[0] + 0.5 * SKIRT_BAND[1]),
         ("navy", 0.0), ("navy", _trim_deg), ("navy", -_trim_deg), ("edge", _trim_deg)]
Vsc, Fsc, FLs = cut_sheet(Vsk, Fsk, _fields, _cuts)
Rsc, Rsi = [], []
for f in Fsc:
    dh_ = float(np.mean(FLs["dh"][f])); nv_ = float(np.mean(FLs["navy"][f])); ed_ = float(np.mean(FLs["edge"][f]))
    if dh_ < SKIRT_TRIM or abs(nv_) < _trim_deg or ed_ < _trim_deg:
        Rsc.append("gold"); Rsi.append("skirt_shade" if nv_ < 0 else "skirt_navy_inner")
    elif nv_ > 0:
        Rsc.append("skirt_navy"); Rsi.append("skirt_navy_inner")
    elif abs(dh_ - SKIRT_BAND[0]) < 0.5 * SKIRT_BAND[1]:
        Rsc.append("skirt_band"); Rsi.append("skirt_shade")
    else:
        Rsc.append("skirt"); Rsi.append("skirt_shade")
V_, F_, R_ = VP.solidify(Vsc, Fsc, SK["t"] * 0.5, SK["t"] * 0.5, Rsc, Rsi, "skirt_shade")
_vpar = np.tile(np.clip((Z_SK_TOP - Vsc[:, 2]) / max(SK["len"]), 0.0, 1.0), 2)
add_part("skirt", V_, F_, R_, w="fauld", v_param=_vpar)
SKIRT_V, SKIRT_F = V_, F_
BVH_SKIRT = BVHTree.FromPolygons(V_.tolist(), F_)


def on_skirt(p, dirn):
    """the skirt's OUTER surface point along -dirn from outside p (+ its outward normal)."""
    o_ = np.asarray(p, float) + dirn * 0.4
    h_ = BVH_SKIRT.ray_cast(Vector(o_), Vector(-dirn), 0.8)
    if h_[0] is None:
        return np.asarray(p, float), dirn
    n_ = unit(np.array(h_[1]))
    return np.array(h_[0]), (n_ if float(n_ @ dirn) > 0 else -n_)


# gold diamond motifs along the hem band (cream panels) + the compass emblem on her left navy panel + a diamond on each navy
# panel above the hem (front / back views)
MOTIFS = 0
_band_rows = []
for k in range(SKIRT_MOTIFS):
    ph_ = -_pe + (k + 0.5) * 2.0 * _pe / SKIRT_MOTIFS
    azf_ = (180.0 - abs(ph_)) * (1.0 if ph_ > 0 else -1.0)
    c_ = int(round((ph_ + _pe) / (2.0 * _pe) * (SK["nu"] - 1)))
    dh_col = _Bs[-1, c_] - _Bs[:, c_]
    jj_ = int(np.argmin(np.abs(dh_col - SKIRT_BAND[0])))
    p_ = _Gs[jj_, c_]
    rad_ = unit(np.array([p_[0], p_[1] - AX_Y, 0.0]))
    q_, n_ = on_skirt(p_, rad_)
    V_, F_, R_ = VP.crystal(q_ + n_ * 0.0010, n_, 0.012, 0.0015, 0.0007, n=4, up_hint=(0.0, 0.0, 1.0), region="gold", su=1.5, phase=0.0)
    add_part("skmotif.%d" % k, V_, F_, R_, w="fauld", v_param=np.full(len(V_), 0.9))
    MOTIFS += 1
_ea, _ez, _eh = SKIRT_EMBLEM
_ed = dir_front(_ea)
_eo, _en = on_skirt(np.array([0.0, AX_Y, _ez]) + _ed * 0.2, _ed)
_ex = unit(np.cross([0.0, 0.0, 1.0], _en)); _ey = unit(np.cross(_en, _ex))
if _ey[2] < 0:
    _ey = -_ey; _ex = -_ex
for q, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(VP.compass_strokes(_eh), _eo, _ex, _ey, _en, 0.0022, 0.0008, "gold",
                                                       project=lambda p_: on_skirt(p_, _ed))):
    add_part("skemblem.%d" % q, Vk_, Fk_, Rk_, w="fauld", v_param=np.full(len(Vk_), 0.7))
# the FRONT TAB: a navy panel with gold edges hanging at the centre front from the lower belt
FT = FRONT_TAB
_fz0 = Z_LOWBELT + 0.004
Vt_, Ft_ = [], []
for j in range(FT["nv"]):
    t_ = j / (FT["nv"] - 1)
    z_ = _fz0 - FT["len"] * t_
    for xk_ in (-0.5, -0.25, 0.0, 0.25, 0.5):
        x_ = xk_ * FT["w"] * (1.0 - 0.15 * t_)
        h_ = BVH_LOWER.ray_cast(Vector((x_, -0.8, z_)), Vector((0.0, 1.0, 0.0)), 1.5)
        y_ = (float(h_[0][1]) if h_[0] is not None else AX_Y - 0.12) - 0.006 - 0.012 * t_
        Vt_.append([x_, y_, z_ - (0.018 * (1.0 - abs(xk_) * 2.0) if j == FT["nv"] - 1 else 0.0)])
Vt_ = np.array(Vt_)
Ft_ = VP.grid_faces(5, FT["nv"])
Ft_ = [f if np.cross(Vt_[f[1]] - Vt_[f[0]], Vt_[f[2]] - Vt_[f[0]])[1] < 0 else f[::-1] for f in Ft_]
_tx = np.abs(Vt_[:, 0]) - (0.5 * FT["w"] - FT["trim"])
_tz = (Vt_[:, 2] - (_fz0 - FT["len"])) - FT["trim"] * 2.0
Vtc_, Ftc_, FLt = cut_sheet(Vt_, Ft_, {"x": _tx, "z": _tz}, [("x", 0.0), ("z", 0.0)])
Rt_ = ["gold" if (np.mean(FLt["x"][f]) > 0 or np.mean(FLt["z"][f]) < 0) else "skirt_navy" for f in Ftc_]
V_, F_, R_ = VP.solidify(Vtc_, Ftc_, FT["t"] * 0.5, FT["t"] * 0.5, Rt_, "skirt_navy_inner", "gold")
add_part("fronttab", V_, F_, R_, w="fauld", v_param=np.tile(np.clip((_fz0 - Vtc_[:, 2]) / FT["len"], 0, 1) * 0.6, 2))
_ftc = np.array([0.0, float(Vt_[:, 1].min()) - 0.002, _fz0 - FT["len"] * 0.55])
V_, F_, R_ = VP.crystal(_ftc, (0.0, -1.0, 0.0), 0.011, 0.0016, 0.0008, n=4, up_hint=(0.0, 0.0, 1.0), region="gold", su=1.6, phase=0.0)
add_part("fronttabmotif", V_, F_, R_, w="fauld", v_param=np.full(len(V_), 0.4))
SKIRT_INFO = {"top_z": round(Z_SK_TOP, 4), "hem_z_front": round(Z_SK_TOP - SK["len"][0], 4), "hem_z_back": round(Z_SK_TOP - SK["len"][1], 4),
              "trim_deg": round(_trim_deg, 2), "regions": {r_: Rsc.count(r_) for r_ in sorted(set(Rsc))}, "motifs": MOTIFS}
print("OUTFIT", json.dumps({"zero_area_faces_dropped": ZERO_AREA_DROPPED, "boots": BOOT_INFO, "skirt": SKIRT_INFO, "belts_z": [round(z_, 4) for z_ in BELT_Z], "medallions": MED_INFO,
                            "necktie": TIE_TAILS,
                            "open_edges": {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}}))
