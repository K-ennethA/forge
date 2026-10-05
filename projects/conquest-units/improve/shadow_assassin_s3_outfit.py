# Shadow Assassin build section 3 (Elias's s3 helpers + boot stack, the rest new): the solid outfit pieces (closed solids,
# skinned from the body under them) -- boots with BUCKLED STRAPS + folded cuffs, knee guards, the FACE WRAP shell (S5:
# no face; the eye band painted hood_void), the purple SCARF fold loops, the CROSSED CHEST STRAPS, the two-layer TATTERED
# SKIRT, the BELT + gold ring buckle + gold rings + pouches, the purple SASH with the gold chevron, forearm BRACERS, the
# round gold BROOCH and the gold diamond PENDANT. Every trunk-hung piece is built against TRUNK-ONLY hosts (never the
# hanging hands: the Wren pouch / Elias belt disease, style-guide law).
PARTS = []          # dicts: name, V, F, R, w ('transfer' | 'rigid:<bone>' | 'rigid_transfer' | scheme), obj, + extras


def add_part(name, V, F, R, w="transfer", obj="main", **kw):
    d = {"name": name, "V": np.asarray(V, float), "F": [list(map(int, f)) for f in F], "R": list(R), "w": w, "obj": obj}
    d.update(kw)
    assert all(p_["name"] != name for p_ in PARTS), "duplicate part name " + name
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


def ring_band(bvh, z0, z1, clear, nu=40, rows=3, ay=None, wobble=0.0):
    """a band round the vertical axis between heights z0 .. z1 sitting `clear` off the outermost surface of bvh."""
    zs_ = np.linspace(z0, z1, rows)
    V = []
    for z in zs_:
        for i in range(nu):
            a_ = 360.0 * i / nu
            r_ = float(rp_front(bvh, a_, [z - 0.01, z, z + 0.01], ay=ay).max()) + clear + wobble * math.sin(3.0 * math.radians(a_))
            V.append(np.array([0.0, AX_Y if ay is None else ay, z]) + dir_front(a_) * r_)
    V = np.array(V)
    F = VP.grid_faces(nu, rows, closed_u=True)
    cy_ = AX_Y if ay is None else ay
    F = [f if np.dot(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]), np.array([V[f].mean(0)[0], V[f].mean(0)[1] - cy_, 0.0])) > 0
         else f[::-1] for f in F]
    return V, F



def limb_sheet(A, B, ts, thetas, clear_fn, bvh, front, rmax=0.3):
    """an OPEN grid over part of the rings round A -> B (limb_grid's skin-radius rule): thetas (rad) about the axis from
    the 'front' direction, rows at fractions ts; faces oriented away from the axis. -> V, F, (u, v, a)."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    a = unit(B - A)
    u = unit(np.asarray(front, float) - a * float(np.dot(front, a)))
    v = np.cross(a, u)
    V = []
    for t in ts:
        c = A + (B - A) * t
        for th in thetas:
            d = math.cos(th) * u + math.sin(th) * v
            h = bvh.ray_cast(Vector(c + d * rmax), Vector(-d), rmax)
            r = (rmax - h[3]) if h[0] is not None else 0.06
            V.append(c + d * (r + clear_fn(t, th)))
    V = np.array(V)
    F = VP.grid_faces(len(thetas), len(ts))
    F = [f if np.dot(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]),
                     V[f].mean(0) - (A + a * float(np.dot(V[f].mean(0) - A, a)))) > 0 else f[::-1] for f in F]
    return V, F, (u, v, a)


def surf_hit(bvh, p, dirn, back=0.5, rng=1.0):
    """the surface point + outward normal hit by a ray from p + dirn * back toward -dirn (None if missed)."""
    o_ = np.asarray(p, float) + np.asarray(dirn, float) * back
    h_ = bvh.ray_cast(Vector(o_), Vector(-np.asarray(dirn, float)), rng)
    if h_[0] is None:
        return None, None
    n_ = unit(np.array(h_[1]))
    return np.array(h_[0]), (n_ if float(n_ @ dirn) > 0 else -n_)


# ---- boots: Elias's foot shell (loft along the foot) + sole slab + shaft round the lower shin + folded cuff; the sheet's
# BUCKLED STRAPS (leather bands round the shaft, a gold buckle on the outer side) replace Elias's gold trim
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
        Vg, Fg, (bu_, bv_, ba_) = limb_grid(KNEE[s], ANKLE[s], np.array([t_c - hh_, t_c + hh_]), clr_s, LIMB_BVH["leg" + s], 16,
                                            front=sh["fr"])
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

# ---- knee guards: a curved leather plate over each knee (front half-ring) + a strap ring below it
KNEE_INFO = {}
for s in "LR":
    sh = SHIN[s]
    KG = KNEE_GUARD
    th_ = np.radians(np.linspace(-KG["half_deg"], KG["half_deg"], KG["nu"]))
    ts_ = np.linspace(KG["t"][0], KG["t"][1], KG["nv"])
    clr = lambda t, th: KG["clear"] + 0.010 * math.sin(math.pi * (t - KG["t"][0]) / (KG["t"][1] - KG["t"][0])) * math.cos(th) ** 2
    Vg, Fg, _ = limb_sheet(KNEE[s], ANKLE[s], ts_, th_, clr, LIMB_BVH["leg" + s], sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, KG["t_plate"], 0.0, "leather", "leather_dark", "leather_dark")
    add_part("kneeguard." + s, V_, F_, R_, w="transfer")
    clr_s = lambda t, th: KG["clear"] * 0.55
    Vg, Fg, _ = limb_grid(KNEE[s], ANKLE[s], np.array([KG["strap"] - 0.02, KG["strap"] + 0.02]), clr_s, LIMB_BVH["leg" + s], 14,
                          front=sh["fr"])
    V_, F_, R_ = VP.solidify(Vg, Fg, 0.0028, 0.0, "leather_dark", "leather_dark", "leather_dark")
    add_part("kneestrap." + s, V_, F_, R_, w="transfer")
    KNEE_INFO[s] = {"centre_z": round(float(KNEE[s][2]), 3)}

# ---- the FACE WRAP (S5: no face): a smooth dark shell over the whole face front -- columns round the front of the head,
# rows from above the eyes to below the chin; per sample the head skin's radius from the vertical axis (head + neck faces),
# an UPPER ENVELOPE smoothed (r <- max(r, neighbour mean), FACE_WRAP smooth passes: it drapes over the nose / brow / chin,
# never into the sockets), + clearance. The eye band (above void_dz) is the near-black HOOD VOID, below it the wrap tone.
FW = FACE_WRAP
_fw_a = np.linspace(-FW["az"], FW["az"], FW["nu"])
_fw_z = np.linspace(EYE["L"]["c"][2] + FW["top"], Z_CHIN - FW["bottom"], FW["nv"])
_rr = np.zeros((FW["nv"], FW["nu"]))
for j, z_ in enumerate(_fw_z):
    for i, a_ in enumerate(_fw_a):
        _rr[j, i] = float(radial_profile(BVH_HEAD, 0.0, HC[1], 180.0 + a_, [z_], rmax=0.4)[0])
_rr = np.maximum(_rr, 0.02)
for _ in range(FW["smooth"]):
    m_ = _rr.copy()
    m_[1:-1, :] = 0.5 * (_rr[:-2, :] + _rr[2:, :])
    m_[:, 1:-1] = 0.5 * (m_[:, 1:-1] + 0.5 * (_rr[:, :-2] + _rr[:, 2:]))
    _rr = np.maximum(_rr, m_)
Vw = []
for j, z_ in enumerate(_fw_z):
    for i, a_ in enumerate(_fw_a):
        ph = math.radians(180.0 + a_)
        r_ = _rr[j, i] + FW["clear"]
        Vw.append([r_ * math.sin(ph), HC[1] + r_ * math.cos(ph), z_])
Vw = np.array(Vw)
Fw = VP.grid_faces(FW["nu"], FW["nv"])
Fw = [f if np.dot(np.cross(Vw[f[1]] - Vw[f[0]], Vw[f[2]] - Vw[f[0]]), np.array([Vw[f].mean(0)[0], Vw[f].mean(0)[1] - HC[1], 0.0])) > 0
      else f[::-1] for f in Fw]
Rw = ["hood_void" if Vw[f].mean(0)[2] > EYE["L"]["c"][2] + FW["void_dz"] else "facewrap" for f in Fw]
V_, F_, R_ = VP.solidify(Vw, Fw, FW["t"], 0.0, Rw, "hood_inner", "facewrap")
add_part("facewrap", V_, F_, R_, w="rigid:head")
FACEWRAP_INFO = {"rows": FW["nv"], "cols": FW["nu"], "z_range": [round(float(_fw_z[-1]), 4), round(float(_fw_z[0]), 4)],
                 "front_y": round(float(Vw[:, 1].min()), 4), "void_faces": int(sum(r_ == "hood_void" for r_ in Rw))}

# ---- the PURPLE SCARF: fold loops round the neck (flattened closed tubes), each lower loop wider and dipping further at
# the front (the NECKLACE DETAIL panel's bunched horizontal folds, the FRONT view's cowl under the wrap)
_sc_host = comb_bvh({"facewrap"}, body=(BV, [f for f, a in zip(BF, is_arm_f) if not a]))
SCARF_INFO = []
for k, (dz_, tr_, dip_, so_) in enumerate(SCARF["loops"]):
    pts_ = []
    for i in range(SCARF["seg"]):
        a_ = 360.0 * i / SCARF["seg"]                  # from the front, + his left
        fr_k = 0.5 * (1.0 + math.cos(math.radians(a_)))
        z_ = NECK0[2] + dz_ - dip_ * fr_k ** 1.5
        z_ += 0.004 * math.sin(math.radians(a_) * 3.0 + 1.3 * k)        # bunched, not a stack of rings
        r_ = float(rp_front(_sc_host, a_, [z_ - 0.012, z_, z_ + 0.012], ay=NECK0[1]).max())
        pts_.append(np.array([0.0, NECK0[1], z_]) + dir_front(a_) * (r_ + so_ * (0.6 + 0.4 * fr_k) + tr_ * 0.6))
    pts_ = np.array(pts_)
    for _ in range(2):
        pts_ = 0.5 * pts_ + 0.25 * (np.roll(pts_, 1, 0) + np.roll(pts_, -1, 0))
    V_, F_, R_, _ = VP.tube_path(pts_, tr_, SCARF["sides"], (lambda kk, ii, sf, k=k: "scarf_shade" if (ii in (4, 5) or k == 3 and ii == 3) else "scarf"),
                                 closed=True, su=SCARF["flat"], up_hint=(0.0, 0.0, 1.0))
    add_part("scarf.%d" % k, V_, F_, R_, w="collar")
    SCARF_INFO.append({"front_z": round(float(pts_[0, 2]), 4), "front_y": round(float(pts_[0, 1]), 4)})

# ---- the CROSSED CHEST STRAPS (Elias's satchel-strap rule): per strap the plane through its shoulder + hip anchors that
# contains the front-back axis; rays in that plane from its centre outward against the TRUNK (+ scarf; never the arms) give a
# loop round the torso (front diagonal + back diagonal), smoothed, swept as a flat leather ribbon
_strap_host = comb_bvh({"scarf"}, body=(BV, TRUNK_F))
STRAP_INFO = []
STRAP_LOOPS = []
for k, ((sx_, sdz_), (hx_, hdz_)) in enumerate(STRAPS):
    _S_top = np.array([sx_ * SHO["L"][0], AX_Y, SHO["L"][2] + sdz_])
    _S_low = np.array([hx_ * HIP["L"][0], AX_Y, Z_WAIST + hdz_])
    _sc = 0.5 * (_S_top + _S_low)
    _sn = unit(np.cross([0.0, 1.0, 0.0], _S_top - _S_low))
    _se1 = unit(_S_top - _sc); _se2 = np.cross(_sn, _se1)
    Cs, Ns = [], []
    for q in range(72):
        ps_ = 2 * math.pi * q / 72
        d_ = math.cos(ps_) * _se1 + math.sin(ps_) * _se2
        h_ = _strap_host.ray_cast(Vector(_sc + d_ * 0.7), Vector(-d_), 0.7)
        if h_[0] is None:
            continue
        Cs.append(np.array(h_[0]) + d_ * (STRAP_T + 0.0030 + 0.0015 * k)); Ns.append(d_)
    Cs = np.array(Cs); Ns = np.array(Ns)
    for _ in range(3):
        Cs = 0.5 * Cs + 0.25 * (np.roll(Cs, 1, 0) + np.roll(Cs, -1, 0))
    V_, F_, R_ = VP.ribbon(np.vstack([Cs, Cs[:1]]), np.vstack([Ns, Ns[:1]]), STRAP_W * 0.5, STRAP_T, region="leather")
    add_part("chest_strap.%d" % k, V_, F_, R_, w="transfer")
    STRAP_LOOPS.append((Cs, Ns))
    STRAP_INFO.append({"points": int(len(Cs)), "top": _S_top.round(3).tolist(), "low": _S_low.round(3).tolist()})

# ---- the TATTERED SKIRT below the belt: two layers (under, then outer) of radial panels hanging from the belt line (Elias's
# robe-skirt generator: the trunk + leg profile, max-accumulated so it hangs, + clearance + flare), per-column TATTERED hems
# (long torn points among short teeth) and the outer layer SPLIT open at the front / sides / back (slits up from the hem)
BVH_LOWER = comb_bvh({"chest_strap"}, body=(BV, TRUNK_F + [f for f, n in zip(BF, fdomn) if n.startswith(("thigh", "calf"))]))
_cols = np.linspace(-180.0, 180.0, SKIRT["nu"] + 1)[:-1]
_zsk = np.linspace(Z_BELT + 0.03, Z_BELT - max(SKIRT["len"]) - SKIRT["tear"][0] - 0.05, 48)
_skp = {a_: np.maximum.accumulate(radial_profile(BVH_LOWER, 0.0, AX_Y, 180.0 + a_, _zsk)) for a_ in _cols}
SKIRT_INFO = {}
for layer, cfg in (("skirtunder", dict(SKIRT, **SKIRT_UNDER)), ("skirt", SKIRT)):
    nu_, nv_ = cfg["nu"], cfg["nv"]
    hem_ = VP.tear_hem(nu_, 0.0, cfg["tear"][0], cfg["tear"][1], cfg["tear"][2], 11.0 if layer == "skirt" else 23.0)
    Vsk, vrow = [], []
    for j in range(nv_):
        v = j / (nv_ - 1)
        for c, a_ in enumerate(_cols):
            back = 0.5 - 0.5 * math.cos(math.radians(a_))
            ln = cfg["len"][0] + (cfg["len"][1] - cfg["len"][0]) * back + hem_[c]
            z = Z_BELT + 0.012 - (ln + 0.012) * v
            r = float(np.interp(-z, -_zsk, _skp[a_])) + cfg["clear"] + cfg["flare"] * v ** 1.3 + \
                0.006 * math.sin(math.radians(a_) * 7.0 + 0.4) * v
            ph = math.radians(180.0 + a_)
            Vsk.append([r * math.sin(ph), AX_Y + r * math.cos(ph), z]); vrow.append(v)
    Vsk = np.array(Vsk)
    if layer == "skirt":
        sl_ = set(cfg["slits"])
        skip = lambda i, j, sl_=sl_, nv_=nv_: i in sl_ and j >= nv_ - 1 - cfg["slit_rows"]
    else:
        skip = None
    Fsk = VP.grid_faces(nu_, nv_, closed_u=True, skip=skip)
    Fsk = [f if np.dot(np.cross(Vsk[f[1]] - Vsk[f[0]], Vsk[f[2]] - Vsk[f[0]]), np.array([Vsk[f].mean(0)[0], Vsk[f].mean(0)[1] - AX_Y, 0.0])) > 0
           else f[::-1] for f in Fsk]
    reg_o = "skirt" if layer == "skirt" else "skirt_inner"
    V_, F_, R_ = VP.solidify(Vsk, Fsk, cfg["t"], 0.0, reg_o, "skirt_inner", "skirt_inner")
    add_part(layer, V_, F_, R_, w="fauld", v_param=np.tile(np.array(vrow), 2))
    SKIRT_INFO[layer] = {"hem_z_min": round(float(Vsk[:, 2].min()), 4), "hem_z_max_front": round(float(Z_BELT - cfg["len"][0]), 4)}

# ---- the BELT (a band over the skirt top; TRUNK + skirt + straps host -- never the hanging hands), the gold RING BUCKLE at
# the front, gold rings hanging off it, the POUCHES (flap + gold stud)
BVH_BELTP = comb_bvh({"skirt", "skirtunder", "chest_strap"}, body=(BV, TRUNK_F))
_bz0, _bz1 = Z_BELT - 0.5 * BELT["h"], Z_BELT + 0.5 * BELT["h"]
Vb_, Fb_ = ring_band(BVH_BELTP, _bz0, _bz1, BELT["clear"], nu=44, rows=3)
V_, F_, R_ = VP.solidify(Vb_, Fb_, BELT["t"], 0.0, "leather", "leather_dark", "leather_dark")
add_part("belt", V_, F_, R_, w="transfer")
BVH_BELT = comb_bvh({"skirt", "skirtunder", "belt", "chest_strap"}, body=(BV, TRUNK_F))
_bf = BVH_BELT.ray_cast(Vector((0.0, -0.8, Z_BELT)), Vector((0.0, 1.0, 0.0)), 1.5)
BUCKLE_C = np.array(_bf[0]) + np.array([0.0, -0.002 - RING_BUCKLE[1], 0.0])
V_, F_, R_ = VP.torus(RING_BUCKLE[0], RING_BUCKLE[1], 20, 6, BUCKLE_C, (0.0, -1.0, 0.0), up_hint=(0.0, 0.0, 1.0), region="gold")
add_part("buckle", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.rounded_box(BUCKLE_C + np.array([0.0, 0.0015, 0.0]), (1, 0, 0), (0, -1, 0), (0, 0, 1), RING_BUCKLE[0] * 0.75, 0.0015,
                            RING_BUCKLE[0] * 0.75, nr=2, rows=2, bulge=0.0, region="leather_dark")
add_part("buckleinset", V_, F_, R_, w="rigid_transfer")
for k, (a_, rr_) in enumerate(BELT_RINGS):
    _pd = dir_front(a_)
    _pr = float(rp_front(BVH_BELT, a_, [_bz0, Z_BELT]).max())
    C_ = np.array([0.0, AX_Y, _bz0 - rr_ * 0.85]) + _pd * (_pr + 0.004)
    V_, F_, R_ = VP.torus(rr_, 0.0028, 16, 5, C_, _pd, up_hint=(0.0, 0.0, 1.0), region="gold")
    add_part("beltring.%d" % k, V_, F_, R_, w="rigid_transfer")
for k, (a_, (hx_, hy_, hz_)) in enumerate(POUCHES):
    _pd = dir_front(a_)
    _pz = Z_BELT - 0.006 - hz_ * 0.55
    _pr = float(rp_front(BVH_BELT, a_, np.linspace(_pz - hz_, _pz + hz_, 9)).max())
    C_ = np.array([0.0, AX_Y, _pz]) + _pd * (_pr + hy_ + 0.002)
    _pt = np.cross([0.0, 0.0, 1.0], _pd)
    V_, F_, R_ = VP.rounded_box(C_, _pt, _pd, [0.0, 0.0, 1.0], hx_, hy_, hz_, nr=3, rows=6, bulge=0.12, region="leather")
    add_part("pouch.%d" % k, V_, F_, R_, w="rigid_transfer")
    fc_ = C_ + _pd * (hy_ * 0.95) + np.array([0.0, 0.0, hz_ * 0.40])
    V_, F_, R_ = VP.rounded_box(fc_, _pt, _pd, [0.0, 0.0, 1.0], hx_ * 1.05, 0.0032, hz_ * 0.58, nr=2, rows=3, bulge=0.04,
                                region="leather_dark")
    add_part("pouchflap.%d" % k, V_, F_, R_, w="rigid_transfer")
    V_, F_, R_ = VP.gem(fc_ + _pd * 0.0038 - np.array([0.0, 0.0, hz_ * 0.42]), _pd, 0.0046, 0.0026, n=8, region="gold")
    add_part("pouchstud.%d" % k, V_, F_, R_, w="rigid_transfer")

# ---- the PURPLE SASH hanging from the belt at the front (gold-trimmed edges, a pointed hem, the gold chevron near the hem)
SA_ = SASH
_sxs = np.concatenate([[SA_["x"] - 0.5 * SA_["w"]], np.linspace(SA_["x"] - 0.5 * SA_["w"] + SA_["trim"], SA_["x"] + 0.5 * SA_["w"] - SA_["trim"], 5),
                       [SA_["x"] + 0.5 * SA_["w"]]])
_svs = np.linspace(0.0, 1.0, SA_["nv"])
_sash_host = comb_bvh({"skirt", "skirtunder", "belt", "buckle", "buckleinset"}, with_body=False)
Vsa = []
for j, v in enumerate(_svs):
    for i, x_ in enumerate(_sxs):
        u_ = (x_ - SA_["x"]) / (0.5 * SA_["w"])
        z_ = _bz0 + 0.004 - (SA_["len"] - 0.035 * (1.0 - abs(u_))) * v
        h_ = _sash_host.ray_cast(Vector((float(x_), -0.9, float(z_))), Vector((0.0, 1.0, 0.0)), 1.6)
        y_ = (h_[0][1] if h_[0] is not None else (Vsa[-len(_sxs)][1] + 0.003 if Vsa else -0.15)) - 0.006 - 0.012 * v
        Vsa.append([x_, y_, z_])
Vsa = np.array(Vsa)
for j in range(1, SA_["nv"]):                          # the sash hangs: never back toward the body below a row in front of it
    row = slice(j * len(_sxs), (j + 1) * len(_sxs)); prev = slice((j - 1) * len(_sxs), j * len(_sxs))
    Vsa[row, 1] = np.minimum(Vsa[row, 1], Vsa[prev, 1] + 0.004)
Fsa = VP.grid_faces(len(_sxs), SA_["nv"])
Fsa = [f if np.cross(Vsa[f[1]] - Vsa[f[0]], Vsa[f[2]] - Vsa[f[0]])[1] < 0 else f[::-1] for f in Fsa]
Rsa = ["sash_trim" if (f_ % (len(_sxs) - 1) in (0, len(_sxs) - 2)) else "sash" for f_ in range(len(Fsa))]
V_, F_, R_ = VP.solidify(Vsa, Fsa, SA_["t"], 0.0, Rsa, "sash_inner", "sash_inner")
add_part("sash", V_, F_, R_, w="fauld", v_param=np.tile(np.repeat(_svs, len(_sxs)), 2))
_bvh_sash = BVHTree.FromPolygons(Vsa.tolist(), Fsa)
_chz = _bz0 - SA_["len"] + 0.075


def on_sash(p_):
    h_ = _bvh_sash.ray_cast(Vector((float(p_[0]), -0.9, float(p_[2]))), Vector((0.0, 1.0, 0.0)), 1.6)
    return (np.array(h_[0]), np.array([0.0, -1.0, 0.0])) if h_[0] is not None else (np.asarray(p_, float), np.array([0.0, -1.0, 0.0]))


_chv = [np.array([[-0.42 * SA_["w"], 0.020 + dzc], [0.0, -0.012 + dzc], [0.42 * SA_["w"], 0.020 + dzc]]) for dzc in (0.0, 0.026)]
for k, (Vk_, Fk_, Rk_) in enumerate(VP.strokes_to_parts(_chv, np.array([SA_["x"], -0.3, _chz]), np.array([1.0, 0.0, 0.0]),
                                                         np.array([0.0, 0.0, 1.0]), np.array([0.0, -1.0, 0.0]), SA_["chevron"][1],
                                                         0.0010, "gold", project=on_sash)):
    add_part("sashchevron.%d" % k, Vk_, Fk_, Rk_, w="fauld", v_param=np.ones(len(Vk_)))

# ---- forearm BRACERS (Elias's): leather over both forearms, gold rims at both ends, two dark strap bands
BRACER_INFO = {}
for s in "LR":
    BR = BRACER
    fa = ELB[s] - WRI[s]
    A_ = WRI[s] + fa * BR["t"][0]; B_ = WRI[s] + fa * BR["t"][1]
    clr = lambda t, th: BR["clear"] + 0.0030 * math.sin(math.pi * t)
    Vg, Fg, _ = limb_grid(A_, B_, np.linspace(0.0, 1.0, BR["nv"]), clr, LIMB_BVH["arm" + s], BR["nu"])
    V_, F_, R_ = VP.solidify(Vg, Fg, BR["t_leather"], 0.0, "leather", "leather_dark", "leather_dark")
    add_part("bracer." + s, V_, F_, R_, w="transfer")
    Lfa = float(np.linalg.norm(B_ - A_))
    for k, t0_ in enumerate((0.0, 1.0 - BR["trim_w"] / Lfa)):
        clr_r = lambda t, th, t0_=t0_: BR["clear"] + BR["t_leather"] + 0.0008 + 0.0030 * math.sin(math.pi * min(max(t0_, 0.0), 1.0))
        Vg, Fg, _ = limb_grid(A_, B_, np.array([t0_, t0_ + BR["trim_w"] / Lfa]), clr_r, LIMB_BVH["arm" + s], BR["nu"])
        V_, F_, R_ = VP.solidify(Vg, Fg, 0.0014, 0.0, "gold", "gold_dark", "gold_dark")
        add_part("bracertrim.%s%d" % (s, k), V_, F_, R_, w="transfer")
    for k, tq in enumerate(BR["straps"]):
        clr_r = lambda t, th, tq=tq: BR["clear"] + BR["t_leather"] + 0.0006 + 0.0030 * math.sin(math.pi * tq)
        Vg, Fg, _ = limb_grid(A_, B_, np.array([tq - 0.012 / Lfa, tq + 0.012 / Lfa]), clr_r, LIMB_BVH["arm" + s], BR["nu"])
        V_, F_, R_ = VP.solidify(Vg, Fg, 0.0018, 0.0, "leather_dark", "leather_dark", "leather_dark")
        add_part("bracerstrap.%s%d" % (s, k), V_, F_, R_, w="transfer")
    BRACER_INFO[s] = {"span_m": round(Lfa, 4)}

# ---- the gold diamond PENDANT below the scarf (the round BROOCH is placed in s4, over the cape edge)
BVH_FRONT = comb_bvh({"chest_strap", "scarf"}, body=(BV, TRUNK_F))
_pz = NECK0[2] - PENDANT["drop"]
_ph, _pn = surf_hit(BVH_FRONT, np.array([0.0, AX_Y - 0.1, _pz]), np.array([0.0, -1.0, 0.0]), back=0.6, rng=1.2)
PENDANT_C = _ph + _pn * (PENDANT["size"][2] + 0.002)
_pw, _phh, _pdd = PENDANT["size"]
V_, F_, R_ = VP.crystal(PENDANT_C, _pn, _pw, _pdd, _pdd * 0.6, n=4, up_hint=(0.0, 0.0, 1.0), region="gold", su=_phh / _pw, sv=1.0,
                        phase=0.0)
add_part("pendant", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.gem(PENDANT_C + _pn * (_pdd * 0.8), _pn, PENDANT["gem"], 0.0030, n=4, up_hint=(0.0, 0.0, 1.0), region="gem_dark")
add_part("pendantgem", V_, F_, R_, w="rigid_transfer")
V_, F_, R_ = VP.torus(0.0055, 0.0016, 10, 4, PENDANT_C + np.array([0.0, 0.0, _phh + 0.004]), (1.0, 0.0, 0.0), up_hint=(0, 0, 1), region="gold")
add_part("pendantbail", V_, F_, R_, w="rigid_transfer")
print("OUTFIT", json.dumps({"boots": BOOT_INFO, "knees": KNEE_INFO, "facewrap": FACEWRAP_INFO, "scarf": SCARF_INFO, "straps": STRAP_INFO,
                            "skirt": SKIRT_INFO, "bracers": BRACER_INFO,                             "pendant": PENDANT_C.round(4).tolist(),
                            "open_edges": {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}}))
