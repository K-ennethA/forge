# Wren build section 3: the solid outfit pieces (closed solids, skinned from the body under them): eyes, boots (foot
# shell + sole + shaft + folded cuff + buckled straps), the trouser blouse, the tunic tails with the vest panels, the rope
# sash (two wraps, knot, hanging ties), the belt pouch, the rolled sleeve cuffs, the left-forearm bracer (crossed straps,
# studs, the teal diamond in a brass setting), the crystal pendant on its cord, the vest's brass rivets.
PARTS = []          # dicts: name, V, F, R, w ('transfer' | 'rigid:<bone>' | 'rigid_transfer' | scheme), obj, + extras


def add_part(name, V, F, R, w="transfer", obj="main", **kw):
    d = {"name": name, "V": np.asarray(V, float), "F": [list(map(int, f)) for f in F], "R": list(R), "w": w, "obj": obj}
    d.update(kw)
    assert len(d["F"]) == len(d["R"]), name
    PARTS.append(d)
    return d


def comb_bvh(names, with_body=True):
    Vs, Fs, o = ([CV], [list(f) for f in CF], len(CV)) if with_body else ([np.zeros((0, 3))], [], 0)
    for p in PARTS:
        if p["name"].split(".")[0] in names:
            Vs.append(p["V"]); Fs += [[i + o for i in f] for f in p["F"]]; o += len(p["V"])
    return BVHTree.FromPolygons(np.vstack(Vs).tolist(), Fs)


def limb_grid(A, B, ts, clear_fn, bvh, nu, front=(0.0, -1.0, 0.0), rmax=0.3):
    """rings round the segment A -> B at fractions ts: per angle the skin radius (a ray from outside toward the axis
    against bvh) + clear_fn(t, theta). theta 0 = the 'front' direction. Closed in u, faces oriented outward."""
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


# ---- eyes
for s in "LR":
    V_, F_, R_ = EYE_MESH[s]
    add_part("eye." + s, V_, F_, R_, w="rigid:head")

# ---- boots: foot shell (loft along the foot), sole slab, shaft round the lower shin, folded cuff, two buckled straps
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
        if sk > ball_s:                                   # the toe box lowers toward a rounded toe
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
    # buckled straps: a band round the shell section (slanted), the buckle on the outer side
    out_ = lat if s == "L" else -lat
    for k, (ds, slant) in enumerate(STRAPS):
        sk = float(np.clip(ds, s0 + 0.01, s1 - 0.02))
        pk = min(prof, key=lambda p_: abs(p_[0] - sk))
        band = []
        for off in (-0.0085, 0.0085):
            r_ = shell_ring(sk, pk[1], pk[2], pk[3], pk[4], grow=0.0028)
            zc_ = 0.5 * (r_[:, 2].max() + r_[:, 2].min())
            band.append(r_ + fd[None] * (off + math.tan(math.radians(slant)) * (r_[:, 2:3] - zc_)))
        Vb = np.vstack(band)
        Fb = VP.grid_faces(len(band[0]), 2, closed_u=True)
        cb = Vb.mean(0)
        Fb = [f if np.dot(np.cross(Vb[f[1]] - Vb[f[0]], Vb[f[2]] - Vb[f[0]]), Vb[f].mean(0) - cb) > 0 else f[::-1] for f in Fb]
        V_, F_, R_ = VP.solidify(Vb, Fb, 0.0022, 0.0, "strap", "strap", "strap")
        add_part("bootstrap.%s%d" % (s, k), V_, F_, R_, w="toe", side=s, fd=fd, c0=C0b)
        ro_ = band[0][int(np.argmax(band[0] @ out_))]
        V_, F_, R_ = VP.rounded_box(ro_ + fd * 0.0085 + out_ * 0.0026, fd, out_, [0.0, 0.0, 1.0], 0.0085, 0.0022, 0.0095,
                                    nr=1, rows=3, bulge=0.05, region="brass")
        add_part("buckle.%s%d" % (s, k), V_, F_, R_, w="rigid:foot_" + lo_)
    # shaft + folded cuff round the lower shin
    sh = SHIN[s]
    Ls = sh["len"]
    ts_ = np.linspace(WRAP_T[1], 0.965, 6)
    clr = lambda t, th: float(np.interp(t, [WRAP_T[1], 0.965], [BOOT_CLEAR[1], BOOT_CLEAR[0]]))
    Vg, Fg, fr_ = limb_grid(KNEE[s], ANKLE[s], ts_, clr, LIMB_BVH["leg" + s], 16, front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.004, 0.0, "boot", "boot", "boot")
    add_part("bootshaft." + s, V_, F_, R_, w="transfer")
    tc = np.linspace(WRAP_T[1] - 0.004, WRAP_T[1] + CUFF[0] / Ls, 4)
    clr_c = lambda t, th: BOOT_CLEAR[1] + 0.004 + CUFF[1] * 0.3 + CUFF[2] * ((t - tc[0]) / (tc[-1] - tc[0])) ** 1.5 * \
        (1.0 + 0.25 * math.sin(3 * th + 0.7))
    Vg, Fg, _ = limb_grid(KNEE[s], ANKLE[s], tc, clr_c, LIMB_BVH["leg" + s], 16, front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, CUFF[3], 0.0, "boot_cuff", "boot", "boot_cuff")
    add_part("bootcuff." + s, V_, F_, R_, w="transfer")
    BOOT_RING[s] = {"fd": fd, "lat": lat, "s0": s0, "s1": s1, "ball_s": ball_s}
    BOOT_INFO[s] = {"foot_len": round(s1 - s0, 4), "shell_len": round(s1 + TOE_EXT - s0 + 0.014, 4),
                    "shaft_top_z": round(float((KNEE[s] + (ANKLE[s] - KNEE[s]) * WRAP_T[1])[2]), 4)}

# ---- cross lacing on the sock wraps: thin solid ribbons in X's over the shin front (a painted stroke this thin on the
# calf's ~1 cm triangles rendered as jagged cracks), LACE[0] apart up the shin, at +-LACE[2] deg, over |theta| < LACE_SPAN
LACE_INFO = {}
for s in "LR":
    sh = SHIN[s]
    Ls = sh["len"]
    h0, h1 = (WRAP_T[0] + 0.01) * Ls, (WRAP_T[1] - 0.012) * Ls
    k_ = math.tan(math.radians(LACE[2]))
    n_rib = 0
    for hand in (1.0, -1.0):
        for m in range(-2, 8):
            hm = h0 + m * LACE[0]
            pts, nrm = [], []
            for th in np.linspace(-math.radians(LACE_SPAN), math.radians(LACE_SPAN), 11):
                h = hm + hand * k_ * th * 0.045
                if not (h0 <= h <= h1):
                    if len(pts) >= 4:
                        break
                    pts, nrm = [], []
                    continue
                c_ = KNEE[s] + sh["ua"] * h
                d = math.cos(th) * sh["fr"] + math.sin(th) * sh["lat"]
                hit = LIMB_BVH["leg" + s].ray_cast(Vector(c_ + d * 0.25), Vector(-d), 0.25)
                if hit[0] is None:
                    continue
                pts.append(np.array(hit[0]) + d * 0.0016); nrm.append(d)
            if len(pts) >= 4:
                C_ = VP.resample(np.array(pts), max(4, len(pts)))[0]
                V_, F_, R_ = VP.ribbon(C_, np.array(nrm)[np.linspace(0, len(nrm) - 1, len(C_)).astype(int)], LACE[1], 0.0009,
                                       region="wrap_lace")
                add_part("lace.%s%d" % (s, n_rib), V_, F_, R_, w="transfer")
                n_rib += 1
    LACE_INFO[s] = n_rib

# ---- trouser blouse: a baggy tube from mid-thigh down over the knee, bloused over the wraps, tucked in
PUFF_INFO = {}
for s in "LR":
    lo_ = s.lower()
    A_ = HIP[s] + (KNEE[s] - HIP[s]) * PUFF_TOP
    Bt = KNEE[s] + (ANKLE[s] - KNEE[s]) * (WRAP_T[0] + 0.02)
    l1 = float(np.linalg.norm(KNEE[s] - A_)); l2 = float(np.linalg.norm(Bt - KNEE[s]))
    ps = np.concatenate([np.linspace(0.0, 0.90, PUFF_NV - 2), [0.955, 1.0]])
    Vg = []
    for p_ in ps:
        arc = p_ * (l1 + l2)
        c_ = A_ + (KNEE[s] - A_) * (arc / l1) if arc <= l1 else KNEE[s] + (Bt - KNEE[s]) * ((arc - l1) / l2)
        tg_ = unit(unit(KNEE[s] - A_) * (1.0 - smoothstep(-0.03, 0.03, arc - l1)) + unit(Bt - KNEE[s]) * smoothstep(-0.03, 0.03, arc - l1))
        if p_ > 0.93:                                   # the tuck ring sits just above the blouse's lowest ring
            c_ = c_ - tg_ * 0.010 * (p_ - 0.93) / 0.07
        u_ = unit(np.array([0.0, -1.0, 0.0]) - tg_ * float(-tg_[1]))
        v_ = np.cross(tg_, u_)
        cl = float(np.interp(p_, [0.0, 0.35, 0.90, 0.955, 1.0], [PUFF_CLEAR[0], PUFF_CLEAR[1], PUFF_CLEAR[2],
                                                                0.55 * PUFF_CLEAR[2], PUFF_CLEAR[3]]))
        for k in range(PUFF_NU):
            th = 2 * math.pi * k / PUFF_NU
            d = math.cos(th) * u_ + math.sin(th) * v_
            h = LIMB_BVH["leg" + s].ray_cast(Vector(c_ + d * 0.3), Vector(-d), 0.3)
            r = (0.3 - h[3]) if h[0] is not None else 0.06
            rip = 1.0 + PUFF_RIPPLE[0] * math.sin(PUFF_RIPPLE[1] * th + 2.6 * p_ + (0.0 if s == "L" else 1.3)) * smoothstep(0.1, 0.8, p_)
            Vg.append(c_ + d * (r + cl * rip))
    Vg = np.array(Vg)
    Fg = VP.grid_faces(PUFF_NU, len(ps), closed_u=True)
    axc = Vg.reshape(len(ps), PUFF_NU, 3).mean(1)
    Fg = [f if np.dot(np.cross(Vg[f[1]] - Vg[f[0]], Vg[f[2]] - Vg[f[0]]), Vg[f].mean(0) - axc[f[0] // PUFF_NU]) > 0 else f[::-1]
          for f in Fg]
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.0035, 0.0, "trousers", "trousers_shade", "trousers_shade")
    add_part("puff." + s, V_, F_, R_, w="transfer")
    PUFF_INFO[s] = {"top_z": round(float(A_[2]), 4), "tuck_z": round(float(Bt[2]), 4), "clear_m": list(PUFF_CLEAR)}

# ---- tunic tails (the shirt below the sash) with the vest's front panels
BVH_LOWER = BVHTree.FromPolygons(BV.tolist(), TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith("thigh")])
_cols = np.unique(np.round(np.concatenate([np.linspace(-180.0, 180.0, SKIRT_NU + 1)[:-1], [-SKIRT_SHIRT_DEG, SKIRT_SHIRT_DEG]]), 6))
_zsk = np.linspace(Z_SASH + 0.02, Z_SASH - max(SKIRT_LEN) - 0.02, 40)
_skp = {}
for a_ in _cols:
    _skp[a_] = np.maximum.accumulate(radial_profile(BVH_LOWER, 0.0, AX_Y, 180.0 + a_, _zsk))
Vsk, vrow_sk = [], []
for j in range(SKIRT_NV):
    v = j / (SKIRT_NV - 1)
    for a_ in _cols:
        back = 0.5 - 0.5 * math.cos(math.radians(a_))
        ln = SKIRT_LEN[0] + (SKIRT_LEN[1] - SKIRT_LEN[0]) * back + 0.008 * math.sin(math.radians(a_) * 5.0 + 0.6) * v
        z = Z_SASH + 0.006 - (ln + 0.006) * v
        r = float(np.interp(-z, -_zsk, _skp[a_])) + SKIRT_CLEAR + SKIRT_FLARE * v ** 1.3
        ph = math.radians(180.0 + a_)
        Vsk.append([r * math.sin(ph), AX_Y + r * math.cos(ph), z]); vrow_sk.append(v)
Vsk = np.array(Vsk); nsk = len(_cols)
Fsk = VP.grid_faces(nsk, SKIRT_NV, closed_u=True)
Fsk = [f if np.dot(np.cross(Vsk[f[1]] - Vsk[f[0]], Vsk[f[2]] - Vsk[f[0]]), np.array([Vsk[f].mean(0)[0], Vsk[f].mean(0)[1] - AX_Y, 0.0])) > 0
       else f[::-1] for f in Fsk]
Rsk = []
for f in Fsk:
    c_ = Vsk[f].mean(0)
    a_ = math.degrees(math.atan2(c_[0], -(c_[1] - AX_Y)))
    Rsk.append("shirt" if abs(a_) < SKIRT_SHIRT_DEG else "vest")
V_, F_, R_ = VP.solidify(Vsk, Fsk, SKIRT_T, 0.0, Rsk, "trousers_shade", "shirt")
add_part("skirt", V_, F_, R_, w="fauld", v_param=np.tile(np.array(vrow_sk), 2))
SKIRT_INFO = {"columns": int(nsk), "rows": SKIRT_NV, "hem_z_front": round(Z_SASH - SKIRT_LEN[0], 4),
              "hem_z_back": round(Z_SASH - SKIRT_LEN[1], 4)}

# ---- rope sash: two wraps round the waist over the tunic top, a knot at his front-left, two hanging ties
BVH_SASH = comb_bvh({"skirt"})
ROPE_INFO = {}
for k, dz in enumerate(ROPE_DROP):
    C_ = []
    for i in range(32):
        a_ = 360.0 * i / 32
        fr_ = max(0.0, math.cos(math.radians(a_))) ** 2
        z = Z_SASH + 0.004 - dz - 0.008 * fr_ + 0.003 * k * math.sin(math.radians(a_))
        r = float(radial_profile(BVH_SASH, 0.0, AX_Y, 180.0 + a_, [z - 0.008, z, z + 0.008]).max()) + ROPE_R * 0.85
        ph = math.radians(180.0 + a_)
        C_.append([r * math.sin(ph), AX_Y + r * math.cos(ph), z])
    V_, F_, R_, _ = VP.rope(np.array(C_), ROPE_R, n=6, pitch=0.020, closed=True)
    add_part("sash.%d" % k, V_, F_, R_, w="transfer")
_kh = BVH_SASH.ray_cast(Vector((KNOT_X, -0.8, Z_SASH - 0.004)), Vector((0.0, 1.0, 0.0)), 1.5)
KNOT_C = np.array(_kh[0]) + np.array([0.0, -ROPE_R * 1.9, 0.0])
V_, F_, R_ = VP.lathe([(0.0, 0.013), (0.011, 0.009), (0.016, 0.0), (0.012, -0.010), (0.0, -0.014)],
                      ["rope", "rope_dark", "rope", "rope_dark"], 10, KNOT_C, (0.0, -1.0, 0.0), up_hint=(0, 0, 1),
                      rmod=lambda k, t: 1.0 + 0.14 * math.cos(3 * t + k))
add_part("knot", V_, F_, R_, w="rigid_transfer")
TIE_PATHS = []
for j, (ln, dx, rr) in enumerate(TIES):
    pts = [KNOT_C + np.array([dx * 0.5, -0.004, -0.010])]
    zz = KNOT_C[2] - 0.02
    while zz > KNOT_C[2] - ln:
        h = BVH_SASH.ray_cast(Vector((KNOT_C[0] + dx, -0.8, zz)), Vector((0.0, 1.0, 0.0)), 1.5)
        y_ = (h[0][1] if h[0] is not None else pts[-1][1] + 0.004) - rr - 0.004
        pts.append(np.array([KNOT_C[0] + dx, min(y_, pts[-1][1] + 0.01), zz]))
        zz -= 0.03
    C_ = VP.resample(VP.catmull(np.array(pts), 8), 20)[0]
    V_, F_, R_, S_ = VP.rope(C_, rr, n=6, pitch=0.016, taper=lambda sf: 1.0 - 0.25 * smoothstep(0.85, 1.0, sf))
    add_part("tie.%d" % j, V_, F_, R_, w="tie", s=S_)
    # frayed end: a small cone of loose fibres
    tg_ = unit(C_[-1] - C_[-3])
    V_, F_, R_ = VP.lathe([(0.0, 0.0), (rr * 1.25, 0.004), (rr * 1.4, 0.018), (0.0, 0.030)], ["rope", "rope", "rope_dark"], 7,
                          C_[-1], tg_, up_hint=(0, -1, 0), rmod=lambda k, t: 1.0 + (0.3 if k == 2 else 0.0) * math.cos(4 * t))
    V_ = (V_, F_, R_)
    add_part("tieend.%d" % j, V_[0], V_[1], V_[2], w="tie", s=np.full(len(V_[0]), float(S_.max()) + 0.02))
    TIE_PATHS.append(C_)
ROPE_INFO = {"wraps": len(ROPE_DROP), "radius": ROPE_R, "knot": KNOT_C.round(4).tolist(),
             "ties_len_m": [round(float(np.linalg.norm(np.diff(c_, axis=0), axis=1).sum()), 3) for c_ in TIE_PATHS]}

# ---- belt pouch on his left hip
_pa = math.radians(POUCH["phi"])
_pd = np.array([math.sin(_pa), -math.cos(_pa), 0.0])
hx_, hy_, hz_ = POUCH["size"]
_pz = Z_SASH - POUCH["drop"] - hz_
_pr = float(radial_profile(BVH_SASH, 0.0, AX_Y, 180.0 - POUCH["phi"], np.linspace(_pz - hz_, _pz + hz_, 9)).max())
POUCH_C = np.array([0.0, AX_Y, _pz]) + _pd * (_pr + hy_ + 0.005)
_pt = np.array([math.cos(_pa), math.sin(_pa), 0.0])
V_, F_, R_ = VP.rounded_box(POUCH_C, _pt, _pd, [0.0, 0.0, 1.0], hx_, hy_, hz_, nr=3, rows=7, bulge=0.12, region="pouch")
add_part("pouch", V_, F_, R_, w="rigid_transfer")
_fc = POUCH_C + _pd * (hy_ * 0.95) + np.array([0.0, 0.0, hz_ * 0.42])
V_, F_, R_ = VP.rounded_box(_fc, _pt, _pd, [0.0, 0.0, 1.0], hx_ * 1.04, 0.0035, hz_ * 0.55, nr=2, rows=4, bulge=0.04,
                            region="pouch_flap")
add_part("pouchflap", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.gem(_fc + _pd * 0.004 - np.array([0.0, 0.0, hz_ * 0.40]), _pd, 0.0055, 0.003, n=8, region="brass")
add_part("pouchbutton", V_, F_, R_, w="rigid_transfer")

# ---- rolled sleeve cuffs
for s in "LR":
    fa = WRI[s] - ELB[s]
    c0_ = ELB[s] + fa * SLEEVE_T
    a_ = unit(fa)
    h_ = SLEEVE_ROLL[0]
    A_ = c0_ - a_ * h_ * 0.62; B_ = c0_ + a_ * h_ * 0.38
    Lab = float(np.linalg.norm(B_ - A_))
    clr = lambda t, th: SLEEVE_ROLL[2] + SLEEVE_ROLL[1] * math.sin(math.pi * t) ** 0.8 * (1.0 + 0.12 * math.sin(4 * th))
    Vg, Fg, _ = limb_grid(A_, B_, np.linspace(0.0, 1.0, 5), clr, LIMB_BVH["arm" + s], 14)
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.0025, 0.0, "sleeve_roll", "shirt", "sleeve_roll")
    add_part("sleeveroll." + s, V_, F_, R_, w="transfer")

# ---- bracer on the LEFT forearm (the sheet's front / side / back views all show it on his left arm)
BR = BRACER
fa = ELB["L"] - WRI["L"]
Lfa = float(np.linalg.norm(fa)); afa = fa / Lfa
A_ = WRI["L"] + fa * BR["t"][0]; B_ = WRI["L"] + fa * BR["t"][1]
clr = lambda t, th: BR["clear"] + 0.0025 * math.sin(math.pi * t)
Vg, Fg, (bu_, bv_, ba_) = limb_grid(A_, B_, np.linspace(0.0, 1.0, BR["nv"]), clr, LIMB_BVH["armL"], BR["nu"])
_bmid = Vg.reshape(BR["nv"], BR["nu"], 3)
V_, F_, R_ = VP.solidify(Vg, Fg, BR["t_leather"], 0.0, "bracer", "bracer", "bracer_strap")
add_part("bracer", V_, F_, R_, w="transfer")
BVH_BRACER = BVHTree.FromPolygons(V_.tolist(), F_)


def bracer_pt(t, th, off):
    """a point on the bracer's outer surface at fraction t (A -> B) and angle th round the forearm, + off outward."""
    c_ = A_ + (B_ - A_) * t
    d = math.cos(th) * bu_ + math.sin(th) * bv_
    h = BVH_BRACER.ray_cast(Vector(c_ + d * 0.2), Vector(-d), 0.2)
    return (np.array(h[0]) if h[0] is not None else c_ + d * 0.04) + d * off, d


STRAP_PTS = []
for j in range(BR["straps"]):
    hand = 1.0 if j % 2 == 0 else -1.0
    pts, nrm = [], []
    for i in range(16):
        t = 0.06 + 0.88 * i / 15
        th = 2 * math.pi * (j / BR["straps"] + hand * 0.55 * t)
        p_, d_ = bracer_pt(t, th, 0.0022)
        pts.append(p_); nrm.append(d_)
    V_, F_, R_ = VP.ribbon(np.array(pts), np.array(nrm), BR["strap_w"] * 0.5, 0.0013, region="bracer_strap")
    add_part("bracerstrap.%d" % j, V_, F_, R_, w="transfer")
    STRAP_PTS.append(np.array(pts))
_th_gem = math.radians(BR["gem_az"])
# azimuth from the forearm's front toward his lateral (+X): bu_ = the 'front' direction, bv_ = a x front
_th_gem = _th_gem if float(bv_[0]) > 0 else -_th_gem
GEM_C, GEM_D = bracer_pt(0.46, _th_gem, 0.0)
gw, gl, gd = BR["gem"]
V_, F_, R_ = VP.crystal(GEM_C + GEM_D * 0.0012, afa, gw * 1.42, gl * 1.30, gl * 1.30, n=4, up_hint=GEM_D, region="brass",
                        su=0.0034 / (gw * 1.42), phase=0.0)
add_part("bracerset", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.crystal(GEM_C + GEM_D * (0.0034 + 0.001), afa, gw, gl, gl, n=4, up_hint=GEM_D, region="crystal",
                        su=gd / gw, phase=0.0)
add_part("bracergem", V_, F_, R_, w="rigid_transfer")
for i, (t, dth) in enumerate(((0.12, 0.9), (0.12, -0.9), (0.84, 0.9), (0.84, -0.9))):
    p_, d_ = bracer_pt(t, _th_gem + dth, 0.0015)
    V_, F_, R_ = VP.gem(p_, d_, 0.0030, 0.0018, n=5, region="brass")
    add_part("bracerstud.%d" % i, V_, F_, R_, w="rigid_transfer")
BRACER_INFO = {"side": "L (sheet)", "span_m": round(float(np.linalg.norm(B_ - A_)), 4), "gem_centre": GEM_C.round(4).tolist()}

# ---- necklace: a cord from behind the neck down to the teal crystal pendant
PEND_Z = NECK0[2] - PENDANT[3]
_ph = BVH_BODY.ray_cast(Vector((0.0, -0.8, PEND_Z)), Vector((0.0, 1.0, 0.0)), 1.5)
PEND_TOP = np.array([0.0, _ph[0][1] - PENDANT[0] - 0.0025, PEND_Z + PENDANT[1] * 0.2])
_npts = []
for a_, dz in ((-40.0, -0.050), (-75.0, -0.022), (-110.0, -0.010), (-150.0, -0.004), (180.0, -0.002), (150.0, -0.004),
               (110.0, -0.010), (75.0, -0.022), (40.0, -0.050)):
    z = NECK0[2] + dz
    r = float(radial_profile(BVH_BODY, 0.0, NECK0[1], 180.0 + a_ if a_ != 180.0 else 0.0, [z])[0])
    ph = math.radians(180.0 + a_)
    _npts.append(np.array([(r + CORD_R + 0.0015) * math.sin(ph), NECK0[1] + (r + CORD_R + 0.0015) * math.cos(ph), z]))
_sgn0 = 1.0 if _npts[0][0] > 0 else -1.0             # start beside the pendant on the side the first neck point is on
_pre = [PEND_TOP + np.array([_sgn0 * 0.002, 0.0, 0.004]), PEND_TOP + np.array([_sgn0 * 0.010, -0.001, 0.02])]
_post = [PEND_TOP + np.array([-_sgn0 * 0.010, -0.001, 0.02]), PEND_TOP + np.array([-_sgn0 * 0.002, 0.0, 0.004])]
Cn = VP.resample(VP.catmull(np.array(_pre + _npts + _post), 6), 36)[0]
for _ in range(6):                                    # keep the cord off the skin (the shirt is painted on it)
    for i in range(len(Cn)):
        q_, n_, _, d_ = BVH_BODY.find_nearest(Vector(Cn[i]))
        if q_ is not None and float((Cn[i] - np.array(q_)) @ np.array(n_)) < CORD_R + 0.0012:
            Cn[i] = np.array(q_) + np.array(n_) * (CORD_R + 0.0012)
V_, F_, R_, _ = VP.tube_path(Cn, CORD_R, 5, "cord", cap0="flat", cap1="flat")
add_part("cord", V_, F_, R_, w="transfer")
V_, F_, R_ = VP.lathe([(0.0, 0.004), (0.0032, 0.0036), (0.0036, 0.0), (0.003, -0.0015), (0.0, -0.0018)], ["brass"] * 4, 8,
                      PEND_TOP, (0.0, 0.0, 1.0), up_hint=(0, -1, 0))
add_part("pendcap", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.crystal(PEND_TOP - np.array([0.0, 0.0, PENDANT[1] + 0.001]), (0.0, 0.0, 1.0), PENDANT[0], PENDANT[1],
                        PENDANT[2], n=6, up_hint=(0, -1, 0), region="crystal")
add_part("pendant", V_, F_, R_, w="rigid_transfer")
PENDANT_INFO = {"top": PEND_TOP.round(4).tolist(), "drop_below_neck_base_m": PENDANT[3],
                "size_mm": [round(2000 * PENDANT[0], 1), round(1000 * (PENDANT[1] + PENDANT[2]), 1)]}

# ---- vest rivets (brass) along the front edges
for s, sg in (("L", 1.0), ("R", -1.0)):
    for i, z in enumerate(np.linspace(Z_SASH + 0.045, NECK0[2] - 0.13, RIVETS)):
        x_ = sg * (float(np.interp(z, [Z_SASH, NECK0[2]], [VEST_X[1], VEST_X[0]])) + 0.0075)
        h = BVH_BODY.ray_cast(Vector((x_, -0.8, float(z))), Vector((0.0, 1.0, 0.0)), 1.5)
        V_, F_, R_ = VP.gem(np.array(h[0]) + np.array(h[1]) * 0.0008, np.array(h[1]), 0.0030, 0.0017, n=5, region="brass")
        add_part("rivet.%s%d" % (s, i), V_, F_, R_, w="rigid_transfer")
print("OUTFIT", json.dumps({"boots": BOOT_INFO, "puff": PUFF_INFO, "skirt": SKIRT_INFO, "rope": ROPE_INFO,
                            "bracer": BRACER_INFO, "pendant": PENDANT_INFO,
                            "open_edges": {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}}))
