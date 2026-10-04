# Elias build section 7 (adapted from wren_s7_rig.py + the grip / roll / save parts of wren_s8_clips.py): the rig --
# MPFB2 game_engine skeleton + the STAFF bone (child of hand_r) + the BOOK bone (child of hand_l); no follow-through chains
# and NO clips this pass (movement intent is an open artist question); the skin weights (<= 4 influences); the recorded
# grip transforms (hand -> prop, rest frame) and the staff ROLL picked for the least wrist bend in an evaluated (unkeyed)
# hold configuration; the rigged save (+ the glb only with --glb).
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS, "version": "v1 draft", "clips": "none this pass (artist-gated)"}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def rot(axis, deg):
    return K._rot(axis, math.radians(deg))


def Tr(h, R):
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = np.asarray(h) - R @ np.asarray(h)
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
STAFF_GRIP0 = STAFF_T + SKL["grip_centre"]                 # the grip point on the rest (upright) staff
BONES.append(("staff", S_(STAFF_GRIP0), S_(STAFF_GRIP0 + np.array([0, 0, 0.30])), "hand_r", None))
BONES.append(("book", S_(BOOK_C), S_(BOOK_C + BOOK_R3[:, 2] * 0.12), "hand_l", None))
# v2 FOLLOW-THROUGH CHAINS (Wren's): per chain the mean path of its locks from where the chain takes over (s_leave),
# HAIR_BONES bones, children of the head -- hair_front, hair_side.L / .R, hair_back, beard.L / .C / .R (keyed by no clip
# this pass; the clip lane drives them with the damped springs)
CHAIN_PTS = {}
for ch in sorted(set(p["chain"] for p in PARTS if p["w"] == "lock")):
    paths = [VP.resample(p["path"], 40)[0] for p in PARTS if p["w"] == "lock" and p["chain"] == ch]
    rs, Lc = VP.resample(np.mean(paths, 0), HAIR_BONES + 1)
    CHAIN_PTS[ch] = (rs, Lc, "head")
for ch, bc_ in BEARD_CHAINS.items():                       # v6: beard.L / .C / .R = s5's mean path of each chain's clump locks
    CHAIN_PTS[ch] = (bc_["pts"], bc_["L"], "head")          #   (computed there by this same rule: identical values)
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


# (v7 weight-source fix, wren_s7's TORSO_HUNG port) the trunk-hung garment parts copy their skin weights from the TRUNK
# only: the nearest-skin search over the whole body found the hands hanging at the hips (the belt: 16 verts at 1.0 on the
# hand / fingers; the satchel strap 105 verts up to 1.0 on the arms). Same barycentric copy as s1's transfer(), on the non-arm
# triangles, the arm-chain share (clavicle included) zeroed and renormalised. The mantle ('coat') takes the same source.
TORSO_HUNG = ("robeskirt", "belt", "buckle", "buckleinset", "pouch", "pouchflap", "pouchbutton", "scrolltube", "scrollring",
              "satchelstrap", "satchel", "satchelflap", "satchelbuckle", "satchelscroll")
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
    Wm[:, _ARM_COLS] = 0.0                            # a trunk vertex's own armpit / clavicle share is not a source either
    return Wm / np.maximum(Wm.sum(1), 1e-30)[:, None]


def part_weights(p):
    V, w = p["V"], p["w"]
    n = len(V)
    if p["name"].split(".")[0] in TORSO_HUNG:
        if w == "transfer":
            return mpfb_to_rig(transfer_trunk(V))
        if w == "rigid_transfer":
            return np.repeat(mpfb_to_rig(transfer_trunk(V.mean(0)[None])), n, 0)
        if w == "fauld":
            b_ = 0.55 * smoothstep(0.2, 1.0, p["v_param"])[:, None]
            return (1 - b_) * onehot(n, "pelvis") + b_ * mpfb_to_rig(transfer_trunk(V))
        raise ValueError("TORSO_HUNG part %s has weight rule %s" % (p["name"], w))
    if w == "body":
        return mpfb_to_rig(CW)
    if w == "transfer":
        return mpfb_to_rig(transfer(V))
    if w.startswith("rigid:"):
        return onehot(n, w.split(":")[1])
    if w == "rigid_transfer":
        return np.repeat(mpfb_to_rig(transfer(V.mean(0)[None])), n, 0)
    if w == "toe":
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
    if w == "coat":
        # the mantle (draft rule; chains are the clip pass's): above the belt it rides the trunk skin under it; below, it
        # blends to the pelvis alone (a long coat must not stretch with each thigh) -- arms never pull it (v7: the source is
        # the trunk skin only, transfer_trunk; v6 moved the arm share to spine_03 but kept the clavicles: 186 verts <= 0.47)
        Wt = mpfb_to_rig(transfer_trunk(V))
        k_ = smoothstep(Z_BELT + 0.05, Z_BELT - 0.25, V[:, 2])[:, None]
        return (1.0 - k_) * Wt + k_ * onehot(n, "pelvis")
    if w == "lock":                                   # (Wren's: head until the chain takes over, then rigkit.vine_weights)
        s = p["s"]
        Lc = CHAIN_PTS[p["chain"]][1]
        sc = (s - p["s_leave"]) / max(p["L"] - p["s_leave"], 1e-6) * Lc
        W = chain_w(sc, Lc, p["chain"], HAIR_BONES, "head", s0=-0.03)
        W[s <= p["s_leave"] - 0.03] = onehot(1, "head")[0]
        return W
    if w == "beard_shell":                            # v4: the long beard shell -- head above the leave line, then each chain's
        W = np.zeros((n, len(DEFORM)))                #   vine weights by its own drop (s5 bs_chain: chain, blend weight, s)
        wsum_ = np.zeros(n)
        for ch_, wc_, sc_ in p["bs_chain"]:
            Wc_ = chain_w(sc_, CHAIN_PTS[ch_][1], ch_, HAIR_BONES, "head", s0=-0.03)
            Wc_[sc_ <= 0.0] = onehot(1, "head")[0]
            W += np.asarray(wc_, float)[:, None] * Wc_
            wsum_ += np.asarray(wc_, float)
        W[:, J["head"]] += np.clip(1.0 - wsum_, 0.0, 1.0)
        return W
    raise ValueError(w)


def prune(W):
    W = np.where(W > 1e-4, W, 0.0)
    if (W > 0).sum(1).max() > 4:
        idx_ = np.argsort(-W, 1, kind="stable")[:, 4:]
        np.put_along_axis(W, idx_, 0.0, 1)
    return W / np.maximum(W.sum(1), 1e-30)[:, None]


t_ = time.time()
WOBJ = {k_: np.zeros((len(OBJ[k_]["V"]), len(DEFORM))) for k_ in OBJ}
for p in ISL:
    a_, b_ = OBJ[p["obj"]]["RANGE"][p["name"]]
    WOBJ[p["obj"]][a_:b_] = part_weights(p)
WOBJ = {k_: prune(W) for k_, W in WOBJ.items()}
for ob_, k_ in ((low, "main"), (fko, "staff"), (bko, "book")):
    W = WOBJ[k_]
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
WM = WOBJ["main"]
DIG["weights"] = sha(np.vstack(list(WOBJ.values())))
infl = (WM > 0).sum(1)
rep["weights"] = {"max_influences": int(max((W > 0).sum(1).max() for W in WOBJ.values())),
                  "unweighted": int(sum(((W > 0).sum(1) == 0).sum() for W in WOBJ.values())),
                  "sum_dev_max": float(max(np.abs(W.sum(1) - 1.0).max() for W in WOBJ.values())), "seconds": round(time.time() - t_, 1),
                  "rule": "body: MPFB game_engine weights; boots / sleeve rolls / bracers / belt / strap / capelet: the nearest skin "
                          "point's weights; boot feet: foot -> ball; pouches / scroll tubes / buckle / brooch / satchel: rigid at "
                          "their centroid's skin weights; robe skirt: pelvis -> skin under the hem (<= 55 %); cravat: skin with head / "
                          "half the neck moved to spine_03; mantle + crest + motifs ('coat'): trunk skin above the belt (arm "
                          "influence moved to spine_03), blending to the pelvis alone below it; eyes / cap / every hair, beard and "
                          "glasses / the mustache locks / the cheek beard locks: head; scalp locks past their s_leave: Wren vine_weights onto hair_front / hair_side.L/R / hair_back (3 bones each); v6 BEARD: the chin / jaw clump locks past their s_leave (below BEARD_CHAIN_LEAVE) vine weights onto beard.C / beard.L / beard.R (3 bones each, chains = the mean path of their locks); the CORE shell head above BEARD_CHAIN_LEAVE, below it the same chains by drop (C <-> L/R blended over BEARD_CHAIN_PSI +-4 deg; underside ramps back to the head at the neck); staff: 'staff' (child of hand_r); tome: 'book' "
                          "(child of hand_l). v7 weight source: TORSO_HUNG parts (" + ", ".join(TORSO_HUNG) + ") and the coat "
                          "copy from the TRUNK faces only (the arm chain, clavicles included, is never a source)"}
_arm_j = [J[n] for n in DEFORM if n.split("_")[0] in ARM_CHAIN]
_leg_j = [J[n] for n in DEFORM if n.split("_")[0] in ("thigh", "calf", "foot", "ball")]


def _census(names):
    ids_ = np.array([i for nm in OBJ["main"]["RANGE"] if nm.split(".")[0] in names for i in range(*OBJ["main"]["RANGE"][nm])],
                    dtype=np.int64)
    a_ = WM[ids_][:, _arm_j].sum(1); l_ = WM[ids_][:, _leg_j].sum(1)
    return {"verts": int(len(ids_)), "arm_verts": int((a_ > 0).sum()), "arm_max": round(float(a_.max()), 4),
            "leg_verts": int((l_ > 0).sum()), "leg_max": round(float(l_.max()), 4)}


rep["weights"]["census"] = {k_: _census(v_) for k_, v_ in (
    ("mantle+crest+motifs", ("mantle", "crest", "motif")), ("robeskirt", ("robeskirt",)), ("belt", ("belt",)),
    ("buckle+pouches+scrolls", ("buckle", "buckleinset", "pouch", "pouchflap", "pouchbutton", "scrolltube", "scrollring")),
    ("satchel+strap", ("satchelstrap", "satchel", "satchelflap", "satchelbuckle", "satchelscroll")),
    ("capelet (shoulder-hung, report)", ("capelet",)))}
print("WEIGHTS", json.dumps(rep["weights"]))

# =========================================================================== grips: recorded transforms + the roll search
HANDS = {}
for s in "LR":
    lo_ = s.lower()
    wr = HEADP["hand_" + lo_]
    kn = np.mean([HEADP["%s_01_%s" % (f, lo_)] for f in ("index", "middle", "ring", "pinky")], 0)
    a_h = unit(HEADP["pinky_01_" + lo_] - HEADP["index_01_" + lo_])
    e_h = unit((HEADP["middle_01_" + lo_] - wr) - float((HEADP["middle_01_" + lo_] - wr) @ a_h) * a_h)
    n_p = unit(np.cross(e_h, a_h))
    if float((HEADP["thumb_02_" + lo_] - wr) @ n_p) < 0:
        n_p = -n_p
    HANDS[s] = {"a": a_h, "e": e_h, "n": n_p, "grip": 0.45 * wr + 0.55 * kn + n_p * 0.019}


def two_bone(b1, b2, b3, H, T, pole):
    """(Wren s8's analytic two-bone IK) -> world maps of b1 / b2, the reached point, the shortfall."""
    H0, K0, A0 = HEADP[b1], HEADP[b2], HEADP[b3]
    a, b = float(np.linalg.norm(K0 - H0)), float(np.linalg.norm(A0 - K0))
    d0 = unit(A0 - H0)
    k0 = unit((K0 - H0) - float((K0 - H0) @ d0) * d0)
    Dn = float(np.linalg.norm(T - H))
    Dc = float(np.clip(Dn, abs(a - b) + 1e-6, (a + b) * (1 - 1e-5)))
    d = unit(T - H)
    k = unit(pole - float(pole @ d) * d)
    al = math.acos(float(np.clip((a * a + Dc * Dc - b * b) / (2 * a * Dc), -1, 1)))
    al0 = math.atan2(float((K0 - H0) @ k0), float((K0 - H0) @ d0))
    F0 = np.stack([d0, k0, np.cross(d0, k0)], 1); F1 = np.stack([d, k, np.cross(d, k)], 1)
    Ral = F1 @ F0.T
    m = np.cross(d, k)
    R1 = K._rot(m, al - al0) @ Ral
    Kp = H + R1 @ (K0 - H0)
    At = H + d * Dc
    c1 = R1 @ (A0 - K0); c2 = At - Kp
    ang = math.atan2(float(np.dot(np.cross(c1, c2), m)), float(np.dot(c1, c2)))
    R2 = K._rot(m, ang) @ R1
    return TRT(H, R1, H0), TRT(Kp, R2, K0), At, Dn - Dc


# the STAFF grip transform G: hammer grip, the shaft along the right hand's knuckle line, the orb up out of the thumb side
R_G = frame_to(HANDS["R"]["e"], -HANDS["R"]["a"])
G_STAFF = TRT(HANDS["R"]["grip"], R_G, S_(STAFF_GRIP0))     # rest staff -> held in the rest right hand
G_STAFF_inv = np.linalg.inv(G_STAFF)
# =========================================================================== v7 the STAFF HOLD, baked into the bind pose
# (review-log 2026-10-04 "he is not holding his staff its just attached to his robe": v1-v6 only EVALUATED this hold -- the
# rest staff stood beside the empty hanging hand, and with no clips the rest is what the game shows.) The staff is planted
# upright at STAFF_HOLD (vs the right shoulder joint), butt on the floor. Per roll of the staff about its own axis (10-deg
# search) x elbow pole the right arm reaches the grip by analytic two-bone IK; the wrist bend = the angle between the
# lowerarm and the hand; the least bend (+ any reach shortfall x 1000) wins. Then: HOLD_TWIST_SHARE of the wrist's twist
# about the forearm axis moves to the forearm, the fingers close (HOLD_CURL, Wren's HAND_POSES "grip" machinery), every
# mesh is skinned into that pose (linear blend, the weights above) and the bones are re-seated there: the hold IS the bind
# pose (pose bones identity; the glb, the contract checker and the stills all read it). Last, the model is re-centred
# (bbox centre X/Y at the origin: the feet_origin contract; the root bone stays at the origin).
PLANT_XY = np.array([HEADP["upperarm_r"][0] + STAFF_HOLD["out"], HEADP["upperarm_r"][1] + STAFF_HOLD["fwd"]])


def staff_plant(roll_deg):
    """rest staff -> planted at PLANT_XY, turned about its own (vertical) axis by roll_deg."""
    g0 = S_(STAFF_GRIP0)
    M = Tr(g0, rot((0, 0, 1), roll_deg))
    M[:2, 3] += PLANT_XY - g0[:2]
    return M


def rot_angle(R):
    return math.degrees(math.acos(float(np.clip((np.trace(R) - 1) / 2, -1, 1))))


def staff_hold(roll_deg, pole):
    Dst = staff_plant(roll_deg)
    Dh = Dst @ G_STAFF_inv
    wrist = xf(Dh, HEADP["hand_r"])
    Du, Dl, reached, err = two_bone("upperarm_r", "lowerarm_r", "hand_r", HEADP["upperarm_r"], wrist, unit(np.array(pole)))
    return rot_angle(Dl[:3, :3].T @ Dh[:3, :3]), err, (Dst, Dh, Du, Dl)


_rs = []
for pk_, pole_ in enumerate(STAFF_ELBOW_POLES):
    for r_ in range(0, 360, 10):
        b_, e_, _ = staff_hold(float(r_), pole_)
        _rs.append((b_ + 1000.0 * max(e_, 0.0), r_, b_, e_, pk_))
_best = min(_rs)
STAFF_ROLL = float(_best[1])
_, _, (D_ST, D_H, D_U, D_L) = staff_hold(STAFF_ROLL, STAFF_ELBOW_POLES[_best[4]])
# the twist split: the wrist's rotation (rest frame) -> swing x twist about the rest forearm axis; the forearm takes
# HOLD_TWIST_SHARE of the twist about its own axis (the elbow and wrist joints lie on it: neither moves)
AX_FA = unit(HEADP["hand_r"] - HEADP["lowerarm_r"])
_q = Matrix((D_L[:3, :3].T @ D_H[:3, :3]).tolist()).to_quaternion()
TWIST0 = math.degrees(2.0 * math.atan2(float(np.dot([_q.x, _q.y, _q.z], AX_FA)), _q.w))
TWIST0 = (TWIST0 + 180.0) % 360.0 - 180.0
D_L2 = D_L @ Tr(HEADP["lowerarm_r"], rot(AX_FA, HOLD_TWIST_SHARE * TWIST0))
_q2 = Matrix((D_L2[:3, :3].T @ D_H[:3, :3]).tolist()).to_quaternion()
TWIST1 = (math.degrees(2.0 * math.atan2(float(np.dot([_q2.x, _q2.y, _q2.z], AX_FA)), _q2.w)) + 180.0) % 360.0 - 180.0
D = {n: np.eye(4) for n in DEFORM}
D["root"] = np.eye(4)
D["upperarm_r"], D["lowerarm_r"], D["hand_r"], D["staff"] = D_U, D_L2, D_H, D_ST


def fk(D_, n, R=None):
    D_[n] = D_[PARENT[n]] @ Tr(HEADP[n], np.eye(3) if R is None else R)


def curl_axis(side, finger):
    lo_ = side.lower()
    a = HANDS[side]["a"]
    tip = TAILP["%s_03_%s" % (finger, lo_)] - HEADP["%s_01_%s" % (finger, lo_)]
    return a if float((K._rot(a, 0.3) @ tip - tip) @ HANDS[side]["n"]) > 0 else -a


def thumb_pose(D_, side, thumb):
    """(Wren s8's thumb, both forms) 2 values = (base flex, middle+tip flex); 5 values = (base flex, swing toward the pinky,
    roll toward the palm side, middle flex, tip flex), the swing / roll axes the palm normal / the hand's long axis."""
    lo_ = side.lower()
    tax = unit(np.cross(HANDS[side]["n"], unit(TAILP["thumb_01_" + lo_] - HEADP["thumb_01_" + lo_])))
    tip0 = TAILP["thumb_03_" + lo_] - HEADP["thumb_01_" + lo_]
    if float((K._rot(tax, 0.3) @ tip0 - tip0) @ HANDS[side]["n"]) < 0:
        tax = -tax
    if len(thumb) == 5:
        f1, sw_, rl_, f2, f3 = thumb
        nn_, ee_ = HANDS[side]["n"], HANDS[side]["e"]
        nax = nn_ if float((K._rot(nn_, 0.3) @ tip0 - tip0) @ HANDS[side]["a"]) > 0 else -nn_
        eax = ee_ if float((K._rot(ee_, 0.3) @ tip0 - tip0) @ nn_) > 0 else -ee_
        fk(D_, "thumb_01_" + lo_, K._rot(nax, math.radians(sw_)) @ K._rot(eax, math.radians(rl_)) @ K._rot(tax, math.radians(f1)))
        fk(D_, "thumb_02_" + lo_, K._rot(tax, math.radians(f2)))
        fk(D_, "thumb_03_" + lo_, K._rot(tax, math.radians(f3)))
        return
    fk(D_, "thumb_01_" + lo_, K._rot(tax, math.radians(thumb[0])))
    fk(D_, "thumb_02_" + lo_, K._rot(tax, math.radians(thumb[1])))
    fk(D_, "thumb_03_" + lo_, K._rot(tax, math.radians(thumb[1] * 0.6)))


def fingers(D_, side, curl):
    """(Wren s8's fingers()) the four fingers curl about the knuckle line."""
    lo_ = side.lower()
    for f in ("index", "middle", "ring", "pinky"):
        ax = curl_axis(side, f)
        for k in range(3):
            fk(D_, "%s_%02d_%s" % (f, k + 1, lo_), K._rot(ax, math.radians(curl[k] * (0.92 if f == "index" else 1.0))))


fingers(D, "R", HOLD_CURL[0])
# the THUMB WRAP (v7): solved on the posed hand -- the 5-value thumb grid (base flex, swing, roll, middle flex; tip = 0.8 x
# middle) whose tip lands nearest the curled index's middle joint pushed THUMB_PAD out from the shaft axis (the thumb closes
# over the fingers round the shaft), every thumb joint kept >= the shaft radius + THUMB_PAD off the axis (no thumb in the wood)
THUMB_PAD = 0.008
_axp = PLANT_XY


def _thumb_pts(D_):
    return [xf(D_["thumb_%02d_r" % k], HEADP["thumb_%02d_r" % k]) for k in (2, 3)] + [xf(D_["thumb_03_r"], TAILP["thumb_03_r"])]


_tgt = xf(D["index_02_r"], TAILP["index_02_r"])
_rad = _tgt[:2] - _axp
_tgt = _tgt + np.array([*(unit(_rad) * THUMB_PAD), 0.0])
_tbest = None
if HOLD_CURL[1] == "wrap":
    for f1_ in range(-60, 61, 10):
        for sw_ in range(-30, 61, 10):
            for rl_ in range(-30, 91, 15):
                for f2_ in range(0, 91, 15):
                    th_ = (float(f1_), float(sw_), float(rl_), float(f2_), 0.8 * f2_)
                    thumb_pose(D, "R", th_)
                    pts_ = _thumb_pts(D)
                    r_shaft = float(np.interp(pts_[-1][2] / STAFF["len"], [0.0, 1.0], STAFF["shaft_r"]))
                    pen_ = sum(max(0.0, r_shaft + THUMB_PAD - float(np.linalg.norm(q_[:2] - _axp))) for q_ in pts_)
                    sc_ = float(np.linalg.norm(pts_[-1] - _tgt)) + 10.0 * pen_
                    if _tbest is None or sc_ < _tbest[0] - 1e-12:
                        _tbest = (sc_, th_, float(np.linalg.norm(pts_[-1] - _tgt)), pen_)
    HOLD_THUMB = _tbest[1]
else:
    HOLD_THUMB = HOLD_CURL[1]
thumb_pose(D, "R", HOLD_THUMB)
HOLD_BONES = [n for n in DEFORM if not np.allclose(D[n], np.eye(4), atol=1e-12)]
DIG["hold"] = sha(np.stack([D[n] for n in HOLD_BONES]))


def lbs(V, W):
    out = V.copy()
    for n in HOLD_BONES:
        w = W[:, J[n]]
        m = w > 0
        if m.any():
            Vm = V[m]
            out[m] += w[m][:, None] * ((Vm @ D[n][:3, :3].T + D[n][:3, 3]) - Vm)
    return out


HOLD_V = {}
for ob_, k_ in ((low, "main"), (fko, "staff"), (bko, "book")):
    me_ = ob_.data
    V0 = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", V0); V0 = V0.reshape(-1, 3)
    HOLD_V[k_] = lbs(V0, WOBJ[k_])
_all = np.vstack(list(HOLD_V.values()))
SHIFT2 = np.array([0.5 * (_all[:, 0].min() + _all[:, 0].max()), 0.5 * (_all[:, 1].min() + _all[:, 1].max()), 0.0])
for ob_, k_ in ((low, "main"), (fko, "staff"), (bko, "book")):
    HOLD_V[k_] = HOLD_V[k_] - SHIFT2
    ob_.data.vertices.foreach_set("co", HOLD_V[k_].ravel())
    ob_.data.update()
T_S2 = np.eye(4); T_S2[:3, 3] = -SHIFT2
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
for n in DEFORM:
    e = arm_data.edit_bones[n]
    if n in HOLD_BONES:
        e.matrix = Matrix((T_S2 @ D[n] @ REST4[n]).tolist())
    else:
        e.head = Vector(np.array(e.head) - SHIFT2); e.tail = Vector(np.array(e.tail) - SHIFT2)
bpy.ops.object.mode_set(mode="OBJECT")
REST4_0 = REST4
REST4 = {b.name: np.array(b.matrix_local) for b in arm_data.bones}
_seat = max(float(np.abs(REST4[n] - T_S2 @ D[n] @ REST4_0[n]).max()) for n in DEFORM)
for key_ in ("conquest_front_anchor", "conquest_front_landmark"):
    if key_ in low.keys():
        low[key_] = (np.array(low[key_]) - SHIFT2).tolist()
_FOC = json.loads(low["conquest_focus"])
for k_, (lo_, hi_) in list(_FOC.items()):
    c8 = np.array([[x, y, z] for x in (lo_[0], hi_[0]) for y in (lo_[1], hi_[1]) for z in (lo_[2], hi_[2])])
    if k_.startswith("staff"):
        c8 = c8 @ D_ST[:3, :3].T + D_ST[:3, 3]
    c8 = c8 - SHIFT2
    _FOC[k_] = [c8.min(0).tolist(), c8.max(0).tolist()]
_FOC["hold"] = [(xf(D_H, HEADP["hand_r"]) - SHIFT2 - 0.13).tolist(), (xf(D_H, HEADP["hand_r"]) - SHIFT2 + 0.13).tolist()]
low["conquest_focus"] = json.dumps(_FOC)
# ---- the hold, measured on the re-seated rig / skinned meshes
_gp = xf(T_S2 @ D_H, HANDS["R"]["grip"])                          # the fist centre
_ax_xy = PLANT_XY - SHIFT2[:2]                                     # the staff axis (vertical line)
_sb = REST4["staff"]
_tips = {f: TAILP["%s_03_r" % f] for f in ("index", "middle", "ring", "pinky", "thumb")}
_tipd = {f: round(1000.0 * (float(np.linalg.norm(xf(T_S2 @ D["%s_03_r" % f], p_)[:2] - _ax_xy)) -
                            float(np.interp(xf(T_S2 @ D["%s_03_r" % f], p_)[2] / STAFF["len"], [0.0, 1.0], STAFF["shaft_r"]))), 1)
         for f, p_ in _tips.items()}
HOLD_INFO = {"staff_axis_xy": _ax_xy.round(4).tolist(), "grip_z_m": round(float(_gp[2]), 4),
             "grip_off_axis_mm": round(1000.0 * float(np.linalg.norm(_gp[:2] - _ax_xy)), 3),
             "staff_tilt_deg": round(math.degrees(math.acos(float(np.clip(unit(_sb[:3, 1]) @ np.array([0, 0, 1.0]), -1, 1)))), 4),
             "ferrule_min_z_m": round(float(HOLD_V["staff"][:, 2].min()), 5),
             "wrist_bend_deg": round(rot_angle(D_L2[:3, :3].T @ D_H[:3, :3]), 2), "wrist_bend_before_twist_split_deg": round(_best[2], 2),
             "twist_deg": {"total": round(TWIST0, 2), "forearm": round(HOLD_TWIST_SHARE * TWIST0, 2), "wrist_left": round(TWIST1, 2)},
             "elbow_flex_deg": round(math.degrees(math.acos(float(np.clip(unit(xf(D_U, HEADP["lowerarm_r"]) - HEADP["upperarm_r"]) @
                                                                          unit(xf(D_L2, HEADP["hand_r"]) - xf(D_U, HEADP["lowerarm_r"])), -1, 1)))), 1),
             "upperarm_swing_deg": round(rot_angle(D_U[:3, :3]), 1),
             "fingertip_to_shaft_surface_mm": _tipd, "thumb": list(HOLD_THUMB),
             "thumb_tip_to_wrap_target_mm": round(1000.0 * _tbest[2], 1) if _tbest else None, "bones_reseated": len(HOLD_BONES), "reseat_err": _seat,
             "recentre_shift_m": SHIFT2.round(5).tolist(), "reach_shortfall_m": round(max(_best[3], 0.0), 5)}
print("HOLD", json.dumps(HOLD_INFO))
G_HAND_TO_STAFF = np.linalg.inv(REST4["hand_r"]) @ REST4["staff"]
G_HAND_TO_BOOK = np.linalg.inv(REST4["hand_l"]) @ REST4["book"]
rep["grip"] = {"staff": {"rule": "v7: the BIND POSE holds the staff (the sheet's front view): planted upright at STAFF_HOLD vs the right "
                                 "shoulder joint, butt on the floor, the right hand's hammer grip round it (the shaft along the knuckle "
                                 "line, the orb out of the thumb side), the arm by analytic two-bone IK, the staff's roll about its axis "
                                 "searched (10 deg x the elbow poles) for the least wrist bend, HOLD_TWIST_SHARE of the twist on the "
                                 "forearm, the fingers closed (HOLD_CURL); the bone 'staff' is a child of hand_r",
                         "G_staff_rest_to_held": np.round(G_STAFF, 6).tolist(), "hand_r_to_staff_bone_rest4": np.round(G_HAND_TO_STAFF, 6).tolist(),
                         "roll_deg": STAFF_ROLL, "elbow_pole": list(STAFF_ELBOW_POLES[_best[4]]), "hold": HOLD_INFO,
                         "wrist_bend_deg": HOLD_INFO["wrist_bend_deg"], "reach_shortfall_m": HOLD_INFO["reach_shortfall_m"],
                         "bend_by_roll_deg_at_that_pole": {int(r_): round(b_, 1) for _, r_, b_, _, pk_ in _rs if pk_ == _best[4]},
                         "grip_at_m_from_butt": round(STAFF["grip_at"] * STAFF["len"], 4)},
               "book": {"rule": "held in the rest left hand by its spine edge (no clip pose): the bone 'book' is a child of hand_l at "
                                "this transform; roll about the palm normal picked in s6 (fit: no body penetration, least turn)",
                        "hand_l_to_book_bone_rest4": np.round(G_HAND_TO_BOOK, 6).tolist(), "roll_deg": BOOK_ROLL,
                        "penetrating_samples": BOOK_INFO["penetrating_samples"]}}
print("GRIP", json.dumps({"staff_roll": STAFF_ROLL, "wrist_bend": HOLD_INFO["wrist_bend_deg"], "shortfall": round(max(_best[3], 0.0), 4),
                          "book_roll": BOOK_ROLL}))
rig["conquest_rig"] = ("elias: root (contract) > MPFB2 game_engine skeleton + staff (hand_r child) + book (hand_l child) + "
                       "follow-through chains %s x %d bones (head children)" % (sorted(CHAIN_PTS), HAIR_BONES))
rep["chains"] = {ch: {"bones": HAIR_BONES, "length_m": round(float(Lc), 4)} for ch, (_, Lc, _) in CHAIN_PTS.items()}
low["conquest_clips"] = []
low["conquest_clip_status"] = ("none (static build + rig; movement intent is an open artist question); v7: the BIND POSE is the "
                               "sheet's staff hold (the default pose the game shows)")
low["conquest_look"] = ("shaded: Col x baked AO, baked normal map on the skin; the hair family (scalp locks + cap + the v4 beard / "
                        "mustache SHELLS) smooth with custom split vertex normals (the per-group smooth-proxy normals: glTF NORMAL) "
                        "-- no outline shells, no cel bands; stylisation drawn into the palette regions")
low["conquest_staff_grip"] = json.dumps(np.round(G_HAND_TO_STAFF, 6).tolist())
low["conquest_book_grip"] = json.dumps(np.round(G_HAND_TO_BOOK, 6).tolist())
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["tris"] = {"model": report["tris"]["total"], "outline_shells": 0, "budget": TRI_BUDGET}
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/" if not SCRATCH else "//textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)
if WANT_GLB:
    # v4: the shared game-ready path (import wave 2026-10-02; Varden's s7): export_glb.add_glow_attr puts the float '_GLOW'
    # copy of Glow.rgb on every exported mesh in memory (the blend above is already saved without it), then the shared
    # EXPORT_KW flags (COLOR_0 = Col, COLOR_1 = Glow, export_attributes -> _GLOW) minus animations (no clips); audited after.
    # The hair faces' custom split normals ride glTF NORMAL.
    import export_glb as _EG
    for o in scene.objects:
        o.select_set(o in (rig, low, fko, bko))
    bpy.context.view_layer.objects.active = rig
    _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
    _props = {q.identifier for q in bpy.ops.export_scene.gltf.get_rna_type().properties}
    _kw = {k_: v_ for k_, v_ in dict(_EG.EXPORT_KW, export_animations=False).items() if k_ in _props}
    bpy.ops.export_scene.gltf(filepath=OUT_GLB, **_kw)
    _au = _EG.audit(OUT_GLB)
    rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "tris": _au["tris"], "COLOR_0": _au["COLOR_0"],
                  "COLOR_1": _au["COLOR_1"], "_GLOW": _au["_GLOW"], "skins": _au["skins"], "joints": _au["joints"],
                  "meshes": [q_["mesh"] for q_ in _au["primitives"]]}
    print("GLB", json.dumps(rep["glb"]))
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
sys.stdout.flush()
os._exit(0)
