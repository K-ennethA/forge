# Wren build section 5: messy brown hair -- a feathered scalp cap (hair grows from the scalp; no hood rim) + pointed lens
# locks lying on it: the FRINGE (to the FRINGE_TIPS zigzag over the forehead, one lock between the eyes), side locks over
# the ears flicking out at the cheekbone / jaw, back locks to the nape, crown flicks, the cowlick, the short tied nape tail.
t_hair = time.time()
cap_faces = [k for k, r in enumerate(reg) if r == "hair"]
used = sorted(set(i for k in cap_faces for i in CF[k]))
mp_ = {v: k for k, v in enumerate(used)}
Vcap = CV[used]
Fcap = [[mp_[i] for i in CF[k]] for k in cap_faces]
_capfe = smoothstep(0.0, HAIRLINE_FEATHER[0], Vcap[:, 2] - hairline_z(Vcap)[1])
_capt = np.maximum(HAIRLINE_FEATHER[1], HAIR_CAP_T * _capfe)
V_, F_, R_ = VP.solidify(Vcap, Fcap, _capt, -0.0015, "hair", "hair_shade", "hair")
CAP_V, CAP_F = V_, F_
add_part("hair_cap", V_, F_, R_, w="rigid:head")
CAP_FEATHER_INFO = {"rule": "cap outer thickness x smoothstep(0, %.3f, height above the hairline), floor %.4f m" % HAIRLINE_FEATHER,
                    "full_thickness_m": HAIR_CAP_T,
                    "rim_thickness_m_p50": round(float(np.median(_capt[_capfe < 0.05])), 4) if (_capfe < 0.05).any() else None}
BVH_HAIR = comb_bvh({"hair_cap", "cloak", "cowl", "hood", "cord", "pendant", "clasp"})
BVH_CAP = BVHTree.FromPolygons(CAP_V.tolist(), CAP_F)
EZ = float(EYE["L"]["c"][2])


def on_head(psi, el, off):
    """point on the hair cap at azimuth psi (deg from the FRONT toward +X) / elevation el about the head centre, + off."""
    d = np.array([math.sin(math.radians(psi)) * math.cos(math.radians(el)), -math.cos(math.radians(psi)) * math.cos(math.radians(el)),
                  math.sin(math.radians(el))])
    o = HC + d * 0.4
    hit = BVH_CAP.ray_cast(Vector(o), Vector(-d), 0.4)
    if hit[0] is None:
        hit = BVH_BODY.ray_cast(Vector(o), Vector(-d), 0.4)
    return np.array(hit[0]) + d * off


def side_pt(psi, z, off):
    """the head / face surface at azimuth psi (from the front) and height z, reached horizontally from outside, + off."""
    d = np.array([math.sin(math.radians(psi)), -math.cos(math.radians(psi)), 0.0])
    o = np.array([HC[0], HC[1], z]) + d * 0.4
    hit = BVH_HAIR.ray_cast(Vector(o), Vector(-d), 0.4)
    base = np.array(hit[0]) if hit[0] is not None else np.array([HC[0], HC[1], z]) + d * HR[0]
    return base + d * off


def relax_path(C, margin, iters=6, fix=1):
    C = C.copy()
    for _ in range(iters):
        Cn = C.copy()
        Cn[fix:-1] = C[fix:-1] + 0.35 * (0.5 * (C[fix - 1:-2] + C[fix + 1:]) - C[fix:-1])
        C = Cn
        for i in range(fix, len(C)):
            q, n_, _, d = BVH_HAIR.find_nearest(Vector(C[i]))
            if q is None:
                continue
            q = np.array(q); n_ = np.array(n_)
            if float((C[i] - q) @ n_) < margin:
                C[i] = q + n_ * margin
    return C


LOCK_INFO = {}


def hair_lock(name, ctl, W, T, chain=None, s_leave_k=2, root_k=0.55):
    """a pointed lens-section clump along the Catmull path through ctl: widest ~ a third of the way, sharp tip; lies flat
    on the surface under it (the lens's wide axis tangent to it). chain: follow-through chain name (None = rides the
    head); the chain takes over from control point s_leave_k on."""
    C = VP.resample(VP.catmull(np.asarray(ctl, float), 10), LOCK_RINGS)[0]
    C = relax_path(C, 0.0012 + T * 0.55)
    seg = np.linalg.norm(np.diff(C, axis=0), axis=1)
    sarc = np.concatenate([[0.0], np.cumsum(seg)])
    Lt = float(sarc[-1])
    Tg = np.gradient(C, axis=0)
    rings = []
    for i, p in enumerate(C):
        sfr = sarc[i] / Lt
        q, n_, _, _ = BVH_HAIR.find_nearest(Vector(p))
        nout = unit(p - np.array(q)) if np.linalg.norm(p - np.array(q)) > 1e-5 else unit(p - HC)
        wdir = np.cross(nout, unit(Tg[i]))
        hw = W * (root_k + (1.0 - root_k) * math.sin(0.5 * math.pi * min(sfr / 0.32, 1.0))) * (1.0 - 0.97 * smoothstep(0.45, 1.0, sfr) ** 0.9)
        ht = T * (0.75 + 0.25 * math.sin(math.pi * min(sfr / 0.5, 1.0))) * (1.0 - 0.8 * smoothstep(0.55, 1.0, sfr)) *             (HAIR_ROOT_K + (1.0 - HAIR_ROOT_K) * smoothstep(0.0, 0.22, sfr))
        rings.append(VP.lens_ring(p, Tg[i], wdir, max(hw, 0.0007), max(ht, 0.0005), 6))
    V_, F_, _ = VP.loft(rings, "pole", "pole", reg="hair", pole1=C[-1] + unit(Tg[-1]) * 0.006)
    svert = np.concatenate([np.repeat(sarc, 6), [0.0, Lt]])
    R_ = []
    for f in F_:
        q_ = V_[f]
        nn = np.cross(q_[1] - q_[0], q_[2] - q_[0])
        c_ = q_.mean(0)
        qq, _, _, _ = BVH_HAIR.find_nearest(Vector(c_))
        R_.append("hair_shade" if np.dot(unit(nn), unit(c_ - np.array(qq))) < -0.25 else "hair")
    ctl_s = [float(sarc[int(np.argmin(np.linalg.norm(C - np.asarray(c_), axis=1)))]) for c_ in ctl]
    s_leave = ctl_s[min(s_leave_k, len(ctl_s) - 1)]
    add_part(name, V_, F_, R_, w="lock" if chain else "rigid:head", s=svert, s_leave=s_leave, L=Lt, chain=chain,
             path=C[int(np.argmin(np.abs(sarc - s_leave))):])
    LOCK_INFO[name] = {"length": round(Lt, 3), "tip": C[-1].round(4).tolist(),
                       "min_clear": round(float(min(BVH_HAIR.find_nearest(Vector(p))[3] for p in C[1:])), 4)}


# ---- fringe: to the FRINGE_TIPS zigzag (the same tips the forehead shadow band follows)
for k, (xm, zm) in enumerate(FRINGE_TIPS):
    xt, zt = xm * 0.001, EZ + zm * 0.001
    h = BVH_BODY.ray_cast(Vector((xt, -0.8, zt)), Vector((0.0, 1.0, 0.0)), 1.5)
    yt = (h[0][1] if h[0] is not None else face_front_y) - LOCK_OFF - 0.0045
    psi_t = math.degrees(math.asin(float(np.clip(xt / (HR[0] * 1.02), -0.95, 0.95))))
    swirl = 7.0 * (1.0 if k % 2 else -1.0)
    ctl = [on_head(0.30 * psi_t + 4.0, 80.0, 0.0), on_head(0.75 * psi_t + swirl, 55.0, LOCK_OFF),
           on_head(0.95 * psi_t + 0.5 * swirl, 33.0, LOCK_OFF + 0.002), np.array([xt, yt, zt])]
    narrow = abs(zm) < 8.0 and abs(xm) < 12.0          # the long lock between the eyes is a narrower strand
    hair_lock("lock.fringe.%d" % k, ctl, HAIR_W["fringe"] * (0.72 if narrow else 1.0) * (1.0 + 0.1 * math.cos(2.7 * k)),
              HAIR_T["fringe"], chain="hair_fringe")
# ---- side locks: over the ears, flicking out at the cheekbone / jaw
for s, sg in (("L", 1.0), ("R", -1.0)):
    for k, (psi_t, zm, flick) in enumerate(SIDE_TIPS):
        tip = side_pt(sg * psi_t, EZ + zm * 0.001, flick)
        ctl = [on_head(sg * (0.55 * psi_t), 74.0, 0.0), on_head(sg * (0.92 * psi_t), 42.0, LOCK_OFF + HAIR_VOL["side"] * 0.7),
               on_head(sg * psi_t, 12.0, LOCK_OFF + HAIR_VOL["side"]), tip]
        hair_lock("lock.side.%s%d" % (s, k), ctl, HAIR_W["side"] * (1.0 - 0.08 * k), HAIR_T["side"], chain="hair_side." + s)
# ---- back locks: from the crown down the back of the head to the nape, tips flicking out
for k, (psi, elt) in enumerate(zip(BACK_PSI, BACK_TIP_EL)):
    ctl = [on_head(180.0 + (psi - 180.0) * 0.45, 76.0, 0.0), on_head(psi, 42.0, LOCK_OFF + HAIR_VOL["back"] * 0.7),
           on_head(psi, 2.0, LOCK_OFF + HAIR_VOL["back"]), on_head(psi, elt, LOCK_OFF + HAIR_VOL["back"] + HAIR_VOL["back_flick"] * (0.75 + 0.25 * math.cos(2.1 * k)))]
    hair_lock("lock.back.%d" % k, ctl, HAIR_W["back"], HAIR_T["back"])
# ---- outer locks: the spiky messy outline over the ears and round the back (both sides, mirrored with a hash wobble)
for s, sg in (("L", 1.0), ("R", -1.0)):
    for k, (psi, elt, flick) in enumerate(OUTER_LOCKS):
        w_ = 1.0 + 0.12 * math.cos(3.1 * k + (0.0 if s == "L" else 1.7))
        ctl = [on_head(sg * (0.5 * psi), 76.0, 0.0), on_head(sg * (0.85 * psi), 52.0, LOCK_OFF + OUTER_MID * 0.6),
               on_head(sg * psi, 0.5 * (52.0 + elt), LOCK_OFF + OUTER_MID), on_head(sg * (psi + 6.0), elt, LOCK_OFF + flick * w_)]
        hair_lock("lock.outer.%s%d" % (s, k), ctl, OUTER_W * w_, OUTER_T, chain=("hair_side." + s) if psi < 100.0 else None)
# ---- crown flicks (the messy top)
for k, psi in enumerate(CROWN_PSI):
    ctl = [on_head(0.35 * psi, 82.0, 0.0), on_head(0.8 * psi, 68.0, LOCK_OFF),
           on_head(psi, 54.0, LOCK_OFF + CROWN_LIFT)]
    hair_lock("lock.crown.%d" % k, ctl, HAIR_W["crown"], HAIR_T["crown"])
# ---- cowlick
_a0 = on_head(18.0, 80.0, 0.0)
hair_lock("lock.ahoge", [_a0, _a0 + np.array([0.0, -0.010, AHOGE_H * 0.55]), _a0 + np.array([0.004, 0.006, AHOGE_H]),
                         _a0 + np.array([0.006, 0.022, AHOGE_H * 0.82])], HAIR_W["ahoge"], HAIR_T["ahoge"], root_k=0.9)
# ---- the short nape tail + its tie
_t0 = on_head(180.0, -36.0, 0.004)
hair_lock("lock.tail", [_t0 + np.array([0.0, -0.012, 0.012]), _t0, _t0 + np.array([0.0, 0.010, -0.025]),
                        _t0 + np.array([0.0, 0.016, -TAIL[0]])], HAIR_W["tail"], HAIR_T["tail"], chain="hair_tail", s_leave_k=1,
          root_k=0.9)
_tt = unit(np.array([0.0, 0.010, -0.025]))
V_, F_, R_ = VP.torus(TAIL[1], 0.0032, 10, 4, _t0 + _tt * 0.006, _tt, up_hint=(0, 1, 0), region="hair_tie")
add_part("hairtie", V_, F_, R_, w="rigid:head")
HAIR_INFO = {"locks": len(LOCK_INFO), "cap_feather": CAP_FEATHER_INFO, "seconds": round(time.time() - t_hair, 1),
             "fringe_tips_mm": [list(t) for t in FRINGE_TIPS],
             "crown": (lambda HZ_: {"scalp_top_z": round(Z_TOP, 4), "hair_top_z_excl_cowlick": round(HZ_, 4),
                                    "crown_above_scalp_mm": round(1000 * (HZ_ - Z_TOP), 1),
                                    "cowlick_top_above_scalp_mm": round(1000 * (max(float(p["V"][:, 2].max()) for p in PARTS
                                                                                     if p["name"] == "lock.ahoge") - Z_TOP), 1),
                                    "rule": "highest hair vertex (cap + every lock but the cowlick) minus the MPFB scalp top; "
                                            "vampwarrior v4.2: 8.5 mm (v4 23.3 mm = 'too tall')"})(
                 max(float(p["V"][:, 2].max()) for p in PARTS if p["name"].startswith(("lock", "hair_cap")) and p["name"] != "lock.ahoge"))}
print("HAIR", json.dumps({k: v for k, v in HAIR_INFO.items()}))
