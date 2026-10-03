# Wren build section 7: the rig -- MPFB2 game_engine skeleton + follow-through chains (fringe, side hair L/R, nape tail,
# the sash ties, five cloak chains) + the pitchfork bone (child of hand_r) -- and the skin weights (<= 4 influences).
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def rot(axis, deg):
    return K._rot(axis, math.radians(deg))


def Rx(d):
    return rot((1, 0, 0), d)


def Ry(d):
    return rot((0, 1, 0), d)


def Rz(d):
    return rot((0, 0, 1), d)


def Tr(h, R):
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = np.asarray(h) - R @ np.asarray(h)
    return M


def Tt(v):
    M = np.eye(4); M[:3, 3] = v
    return M


def TRT(pos, R, pivot):
    """rest point 'pivot' -> 'pos', rotated by R (a world rigid map)."""
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = np.asarray(pos) - R @ np.asarray(pivot)
    return M


def xf(M, p):
    return M[:3, :3] @ np.asarray(p) + M[:3, 3]


def frame_to(xa, za):
    za = unit(za); xa = unit(np.asarray(xa) - za * float(np.dot(xa, za)))
    return np.stack([xa, np.cross(za, xa), za], 1)


def S_(p):
    return np.asarray(p, float) - SHIFT


BONES = []
for n in MB:
    if n == "Root":
        continue
    b = BREST[n]
    BONES.append((n, S_(b["head"]), S_(b["tail"]), None, b["z"]))
_MP = {"pelvis": "root", "spine_01": "pelvis", "spine_02": "spine_01", "spine_03": "spine_02", "neck_01": "spine_03",
       "head": "neck_01", "thigh_l": "pelvis", "thigh_r": "pelvis"}
for n in MB:
    if n in _MP or n == "Root":
        continue
    if n.startswith("clavicle"):
        _MP[n] = "spine_03"
    elif n.startswith("upperarm"):
        _MP[n] = "clavicle_" + n[-1]
    elif n.startswith("lowerarm"):
        _MP[n] = "upperarm_" + n[-1]
    elif n.startswith("hand"):
        _MP[n] = "lowerarm_" + n[-1]
    elif n.startswith("calf"):
        _MP[n] = "thigh_" + n[-1]
    elif n.startswith("foot"):
        _MP[n] = "calf_" + n[-1]
    elif n.startswith("ball"):
        _MP[n] = "foot_" + n[-1]
    else:
        f_, k_, sd_ = n.split("_")
        _MP[n] = "hand_" + sd_ if k_ == "01" else "%s_%02d_%s" % (f_, int(k_) - 1, sd_)
BONES = [(n, h, t, _MP[n], z) for (n, h, t, _, z) in BONES]
# follow-through chains: hair (the mean path of each chain's locks from where the chain takes over), the ties, the cloak
CHAIN_PTS = {}
for ch in sorted(set(p["chain"] for p in PARTS if p["w"] == "lock")):
    paths = [VP.resample(p["path"], 40)[0] for p in PARTS if p["w"] == "lock" and p["chain"] == ch]
    rs, Lc = VP.resample(np.mean(paths, 0), HAIR_BONES + 1)
    CHAIN_PTS[ch] = (rs, Lc, "head")
_tie_rs, _tie_L = VP.resample(TIE_PATHS[0], 3)
CHAIN_PTS["tie"] = (_tie_rs, _tie_L, "pelvis")
_base_cols = [i for i, cl in enumerate(cols) if cl["gap"] <= 0]
CAPE_U = [(k + 0.5) / CAPE_CHAINS for k in range(CAPE_CHAINS)]
for k, uc in enumerate(CAPE_U):
    ci = min(_base_cols, key=lambda i: abs(cols[i]["u"] - uc))
    colp = np.array([Vcl[j * NUc + ci] for j in range(CLOAK_NV)])
    rs, Lc = VP.resample(colp, CAPE_BONES + 1)
    CHAIN_PTS["cape.%d" % k] = (rs, Lc, "spine_03")
FORK_GRIP0 = FORK_T + FKL["grip_centre"]
BONES.append(("pitchfork", S_(FORK_GRIP0), S_(FORK_GRIP0 + np.array([0, 0, 0.30])), "hand_r", None))
for ch, (pts, Lc, par) in sorted(CHAIN_PTS.items()):
    for k in range(len(pts) - 1):
        BONES.append(("%s.%d" % (ch, k), S_(pts[k]), S_(pts[k + 1]), par if k == 0 else "%s.%d" % (ch, k - 1), None))
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0.2, 0); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p, z) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_)
    if z is not None:
        e.align_roll(Vector(z))
    else:
        e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = False
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}
PARENT = {b[0]: b[3] for b in BONES}
HEADP = {b[0]: np.array(b[1], float) for b in BONES}
TAILP = {b[0]: np.array(b[2], float) for b in BONES}
HEADP["root"] = np.zeros(3)
REST4 = {b.name: np.array(b.matrix_local) for b in arm_data.bones}


def mpfb_to_rig(Wm):
    out = np.zeros((len(Wm), len(DEFORM)))
    for j, n in enumerate(MB):
        out[:, J["pelvis" if n == "Root" else n]] += Wm[:, j]
    return out


def onehot(n, bone):
    W = np.zeros((n, len(DEFORM))); W[:, J[bone]] = 1.0
    return W


def chain_w(s, L, ch, nb, parent_bone, s0=0.0):
    Wv = K.vine_weights(np.clip(s, 0, L), L, nb, parent_s0=s0)
    W = np.zeros((len(s), len(DEFORM)))
    W[:, J[parent_bone]] += Wv[:, 0]
    for k in range(nb):
        W[:, J["%s.%d" % (ch, k)]] += Wv[:, k + 1]
    return W


# (2026-10-03 weight-source fix) the torso-hung garment parts (TORSO_HUNG) copy their skin weights from the TRUNK only:
# the nearest-skin search over the whole body found the hand resting at the hip (12 sash verts per wrap at ~1.0 on the
# forearms, the pouch on thumb_02_l at 0.93). Same barycentric copy as s1's transfer(), on the non-arm triangles.
ARM_CHAIN = ("clavicle", "upperarm", "lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb")
_ARM_COLS = np.array([n.split("_")[0] in ARM_CHAIN for n in MB])
_TRI_TRUNK = TRI[~is_arm_f[np.asarray(TRI_F)]]
BVH_TRI_TRUNK = BVHTree.FromPolygons(BV.tolist(), _TRI_TRUNK.tolist())


def transfer_trunk(Ps):
    Ps = np.asarray(Ps, float)
    loc = np.empty_like(Ps); ti = np.empty(len(Ps), dtype=np.int64)
    for k, p in enumerate(Ps):
        l_, _, i_, _ = BVH_TRI_TRUNK.find_nearest(Vector(p))
        loc[k] = l_; ti[k] = i_
    T3 = _TRI_TRUNK[ti]
    a, b, c = BV[T3[:, 0]], BV[T3[:, 1]], BV[T3[:, 2]]
    v0, v1, v2 = b - a, c - a, loc - a
    d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
    d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-20)
    bv = (d11 * d20 - d01 * d21) / den; bw = (d00 * d21 - d01 * d20) / den
    B = np.clip(np.stack([1.0 - bv - bw, bv, bw], 1), 0, 1); B /= B.sum(1, keepdims=True)
    Wm = B[:, 0:1] * BW[T3[:, 0]] + B[:, 1:2] * BW[T3[:, 1]] + B[:, 2:3] * BW[T3[:, 2]]
    Wm[:, _ARM_COLS] = 0.0                            # a trunk vertex's own armpit share is not a source either
    return Wm / np.maximum(Wm.sum(1), 1e-30)[:, None]


FA_SEG = {s: (np.asarray(BREST["lowerarm_" + s]["head"], float), np.asarray(BREST["hand_" + s]["head"], float)) for s in "lr"}


def part_weights(p):
    V, w = p["V"], p["w"]
    n = len(V)
    if p["name"].split(".")[0] in TORSO_HUNG:
        if w == "transfer":
            return mpfb_to_rig(transfer_trunk(V))
        if w == "rigid_transfer":
            return np.repeat(mpfb_to_rig(transfer_trunk(V.mean(0)[None])), n, 0)
        raise ValueError("TORSO_HUNG part %s has weight rule %s" % (p["name"], w))
    if w == "body":
        return mpfb_to_rig(CW)
    if w == "transfer":
        return mpfb_to_rig(transfer(V))
    if w.startswith("rigid:"):
        return onehot(n, w.split(":")[1])
    if w == "rigid_transfer":
        return np.repeat(mpfb_to_rig(transfer(V.mean(0)[None])), n, 0)
    if w == "toe":                                    # ahead of the ball contact line: the toes bone (flat at toe-off)
        wb = smoothstep(-0.022, 0.0, (V - p["c0"]) @ p["fd"])[:, None]
        return (1 - wb) * onehot(n, "foot_" + p["side"].lower()) + wb * onehot(n, "ball_" + p["side"].lower())
    if w == "fauld":
        b_ = 0.55 * smoothstep(0.2, 1.0, p["v_param"])[:, None]
        return (1 - b_) * onehot(n, "pelvis") + b_ * mpfb_to_rig(transfer(V))
    if w == "collar":
        W = mpfb_to_rig(transfer(V))
        W[:, J["spine_03"]] += W[:, J["head"]] + W[:, J["neck_01"]] * 0.5; W[:, J["head"]] = 0.0
        W[:, J["neck_01"]] *= 0.5
        return W
    if w == "cape":
        uu, vv = p["uv"][:, 0], p["uv"][:, 1]
        W = np.zeros((n, len(DEFORM)))
        cu = np.array(CAPE_U)
        k1 = np.clip(np.searchsorted(cu, uu), 1, CAPE_CHAINS - 1)
        k0 = k1 - 1
        tt = np.clip((uu - cu[k0]) / (cu[k1] - cu[k0]), 0, 1)
        for kk, ww in ((k0, 1 - tt), (k1, tt)):
            for c in range(CAPE_CHAINS):
                m = kk == c
                if not m.any():
                    continue
                Lc = CHAIN_PTS["cape.%d" % c][1]
                W[m] += ww[m][:, None] * chain_w(vv[m] * Lc, Lc, "cape.%d" % c, CAPE_BONES, "spine_03")
        # where the cloak lies over a shoulder / arm it rides that arm: a blend toward the skin weights of the nearest body
        # point when that point belongs to an arm / clavicle, fading with the distance to it (the clips move the arms;
        # chain-only weights let them push through the cloak -- the poke-through gate)
        Wt = mpfb_to_rig(transfer(V))
        k_ = np.zeros(n)
        for i, pt in enumerate(V):
            q_, _, fi_, d_ = BVH_BODY.find_nearest(Vector(pt))
            if q_ is not None and fdomn[fi_].startswith(("upperarm", "lowerarm", "hand", "clavicle")):
                k_[i] = CLOAK_ARM_FOLLOW[0] * float(smoothstep(CLOAK_ARM_FOLLOW[2], CLOAK_ARM_FOLLOW[1], d_))
                if fdomn[fi_].startswith(("lowerarm", "hand")):
                    # (2026-10-03) down the forearm toward the wrist the coupling fades to CLOAK_FOREARM[0]: the posed hand
                    # comes in onto the hip, and a cloak glued to it was dragged through the sash / pouch
                    sd_ = fdomn[fi_][-1]
                    e_, h_ = FA_SEG[sd_]
                    cf_ = CLOAK_FOREARM[sd_]
                    tf_ = float(np.clip((pt - e_) @ (h_ - e_) / float((h_ - e_) @ (h_ - e_)), 0.0, 1.0))
                    k_[i] *= 1.0 - (1.0 - cf_[0]) * float(smoothstep(cf_[1], cf_[2], tf_))
        if any(h[0] > 0.0 for h in CLOAK_HIP.values()):
            # (2026-10-03) the side panels at the sash line ride the hip -- the chain-hung share (not the arm share)
            # blends toward the trunk's weights (no leg share): chain-only they hang from the chest, and the pelvis'
            # counter-twist drove the hip / sash through them
            dz_ = np.abs(V[:, 2] - Z_SASH)
            az_ = np.abs(np.degrees(np.arctan2(V[:, 0], V[:, 1] - AX_Y)))
            kh_ = np.zeros(n)
            for sd_, h_ in CLOAK_HIP.items():
                m_ = (V[:, 0] > 0.0) if sd_ == "l" else (V[:, 0] <= 0.0)
                kz_ = 1.0 - smoothstep(h_[1], h_[1] + h_[2], dz_)
                ka_ = smoothstep(h_[3] - h_[5], h_[3], az_) * (1.0 - smoothstep(h_[4], h_[4] + h_[5], az_))
                kh_ = np.where(m_, h_[0] * kz_ * ka_, kh_)
            Wh_ = transfer_trunk(V)
            Wh_[:, [j for j, nm in enumerate(MB) if nm.startswith(("thigh", "calf", "foot", "ball"))]] = 0.0
            Wh_ = mpfb_to_rig(Wh_ / np.maximum(Wh_.sum(1), 1e-30)[:, None])
            W = (1.0 - kh_)[:, None] * W + kh_[:, None] * Wh_
        W = (1.0 - k_)[:, None] * W + k_[:, None] * Wt
        return W
    if w == "lock":
        s = p["s"]
        Lc = CHAIN_PTS[p["chain"]][1]
        sc = (s - p["s_leave"]) / max(p["L"] - p["s_leave"], 1e-6) * Lc
        W = chain_w(sc, Lc, p["chain"], HAIR_BONES, "head", s0=-0.03)
        W[s <= p["s_leave"] - 0.03] = onehot(1, "head")[0]
        return W
    if w == "tie":
        return chain_w(p["s"], _tie_L, "tie", 2, "pelvis", s0=0.0)
    raise ValueError(w)


def prune(W):
    W = np.where(W > 1e-4, W, 0.0)
    if (W > 0).sum(1).max() > 4:
        idx_ = np.argsort(-W, 1, kind="stable")[:, 4:]
        np.put_along_axis(W, idx_, 0.0, 1)
    return W / np.maximum(W.sum(1), 1e-30)[:, None]


t_ = time.time()
WM = np.zeros((len(OBJ["main"]["V"]), len(DEFORM)))
WF = np.zeros((len(OBJ["fork"]["V"]), len(DEFORM)))
for p in ISL:
    a_, b_ = OBJ[p["obj"]]["RANGE"][p["name"]]
    (WM if p["obj"] == "main" else WF)[a_:b_] = part_weights(p)
WM, WF = prune(WM), prune(WF)
for ob_, W in ((low, WM), (fko, WF)):
    ob_.vertex_groups.clear()
    for j, n in enumerate(DEFORM):
        nz = np.nonzero(W[:, j] > 0)[0]
        if len(nz) == 0:
            continue
        vg = ob_.vertex_groups.new(name=n)
        for wv in np.unique(np.round(W[nz, j], 6)):
            ids = nz[np.round(W[nz, j], 6) == wv]
            vg.add(ids.tolist(), float(wv), "REPLACE")
    ob_.parent = rig
    ob_.matrix_parent_inverse = Matrix.Identity(4)
    am = ob_.modifiers.new("Armature", "ARMATURE"); am.object = rig
DIG["weights"] = sha(np.vstack([WM, WF]))
infl = (WM > 0).sum(1)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(WM.sum(1) - 1.0).max()), "seconds": round(time.time() - t_, 1),
                  "pitchfork_object": "every pitchfork vertex 1.0 on 'pitchfork' (child of hand_r)",
                  "rule": "body: MPFB game_engine weights (Root -> pelvis); boot shafts / cuffs, trouser blouse, sash wraps, "
                          "sleeve rolls, bracer + straps, cord: the MPFB weights of the nearest skin point (barycentric); "
                          "boot foot shells / soles / straps: foot, blending to ball ahead of the ball line; rivets / buckles "
                          "(foot) / knot / pouch / crystals / clasp: rigid at their centroid's skin weights; tunic tails: "
                          "pelvis at the top blending to the skin under the hem (<= 55 %); cowl: the skin weights with head / "
                          "half the neck moved to spine_03; eyes / cap / back + crown locks / cowlick / tail tie: head; "
                          "fringe / side / tail locks: head until their chain takes over, then rigkit.vine_weights; ties: "
                          "pelvis -> tie.0/1; cloak + hood + stitches: across-hat between the 5 chains x along-hat down each. "
                          "2026-10-03 weight-source knobs (all OFF = v6.1): TORSO_HUNG parts copy from the TRUNK faces only (no "
                          "arm-chain source); the cloak's forearm coupling fades elbow -> wrist (CLOAK_FOREARM, per side) and its "
                          "side panels at the sash line hang part from the hip (CLOAK_HIP)"}
_arm_j = [J[n] for n in DEFORM if n.split("_")[0] in ARM_CHAIN]
_th = np.array([i for nm in OBJ["main"]["RANGE"] if nm.split(".")[0] in TORSO_HUNG
                for i in range(*OBJ["main"]["RANGE"][nm])], dtype=np.int64)
_cl = np.arange(*OBJ["main"]["RANGE"]["cloak"])
_fa_j = [J[n] for n in DEFORM if n.split("_")[0] in ARM_CHAIN[2:]]
_la_j = [J[n] for n in DEFORM if n.endswith("_l") and n.split("_")[0] in ARM_CHAIN]
rep["weights"]["weight_sources"] = {"torso_hung_parts": list(TORSO_HUNG),
                                    "torso_hung_arm_chain_weight_max": round(float(WM[_th][:, _arm_j].sum(1).max()), 6) if len(_th) else None,
                                    "cloak_verts_forearm_hand_weight": int((WM[_cl][:, _fa_j].sum(1) > 0).sum()),
                                    "cloak_verts_left_arm_weight": int((WM[_cl][:, _la_j].sum(1) > 0).sum()),
                                    "cloak_left_arm_weight_max": round(float(WM[_cl][:, _la_j].sum(1).max()), 3)}
print("WEIGHTS", json.dumps(rep["weights"]))
