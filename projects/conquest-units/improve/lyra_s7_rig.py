# Lyra build section 7 (adapted from elias_s7_rig.py + shadow_assassin_s7_rig.py's per-finger solve): the rig -- MPFB2
# game_engine skeleton + the BOOKS bone (child of hand_r) + the follow-through chains (L4: hair_side.L / .R on the loose
# strands, hair_tail on the ponytail, ribbon on the ribbon tails + tassel, necktie on the tie tails); NO clips (L5: movement
# intent is an open artist question); the skin weights (<= 4 influences; TRUNK_HUNG garments from the trunk only, the
# capelet shoulder-hung); the BOOK HOLD baked into the bind pose; the rigged save + the glb (shared _GLOW path, audited:
# _GLOW all zero, L3).
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS, "version": "v1 draft", "clips": "none (L5: artist-gated)"}
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
if HAS_BOOKS:
    BONES.append(("books", S_(BOOK_C), S_(BOOK_C + BOOK_Z * 0.12), "hand_r", None))
# v2 FOLLOW-THROUGH CHAINS (Wren's): per chain the mean path of its locks from where the chain takes over (s_leave),
# HAIR_BONES bones, children of the head -- hair_front, hair_side.L / .R, hair_back, beard.L / .C / .R (keyed by no clip
# this pass; the clip lane drives them with the damped springs)
CHAIN_PTS = {}
CHAIN_NB = {}
for ch in sorted(set(p["chain"] for p in PARTS if p["w"] == "lock")):
    defs_ = [p for p in PARTS if p["w"] == "lock" and p["chain"] == ch and p.get("chain_def", True)]
    paths = [VP.resample(p["path"], 40)[0] for p in defs_]
    nb_ = TAIL_BONES if ch == "hair_tail" else HAIR_BONES
    rs, Lc = VP.resample(np.mean(paths, 0), nb_ + 1)
    CHAIN_PTS[ch] = (rs, Lc, defs_[0].get("chain_parent", "head"))
    CHAIN_NB[ch] = nb_
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
TORSO_HUNG = ("corset", "belt", "buckle", "vial", "medal", "medaldisc", "medalcord", "pouch", "pouchflap", "pouchbuckle", "pouchstrap",
              "skirt", "skmotif", "skemblem", "fronttab", "fronttabmotif", "strap", "satchel", "satchelflap", "satchelbuckle",
              "satchelstrapl", "satchelhang", "scrollcase", "scrollroll", "scrollband")
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
        Lc, par_ = CHAIN_PTS[p["chain"]][1], CHAIN_PTS[p["chain"]][2]
        sc = (s - p["s_leave"]) / max(p["L"] - p["s_leave"], 1e-6) * Lc
        W = chain_w(sc, Lc, p["chain"], CHAIN_NB[p["chain"]], par_, s0=-0.03)
        W[s <= p["s_leave"] - 0.03] = onehot(1, par_)[0]
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
for ob_, k_ in [(low, "main")] + ([(bko, "books")] if HAS_BOOKS else []):
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
                  "rule": "body: MPFB game_engine weights; boots / sleeves / bracelets / capelet (shoulder-hung) / emblems: the "
                          "nearest skin point's weights; boot feet: foot -> ball; collar / tie knot / pin: skin with head / half the "
                          "neck moved to spine_03; TRUNK_HUNG parts (" + ", ".join(TORSO_HUNG) + ") from the TRUNK faces only (the "
                          "arm chain, clavicles included, is never a source; the skirt / tab / motifs 'fauld': pelvis -> trunk skin "
                          "under the hem); eyes / cap / swept locks / tie wrap + bow + hairpiece: head; loose strands, ponytail, "
                          "ribbon tails + tassel, necktie tails past their s_leave: Wren vine_weights onto their chains; books: "
                          "'books' (child of hand_r)"}
_arm_j = [J[n] for n in DEFORM if n.split("_")[0] in ARM_CHAIN]
_leg_j = [J[n] for n in DEFORM if n.split("_")[0] in ("thigh", "calf", "foot", "ball")]


def _census(names):
    ids_ = np.array([i for nm in OBJ["main"]["RANGE"] if nm.split(".")[0] in names for i in range(*OBJ["main"]["RANGE"][nm])],
                    dtype=np.int64)
    a_ = WM[ids_][:, _arm_j].sum(1); l_ = WM[ids_][:, _leg_j].sum(1)
    return {"verts": int(len(ids_)), "arm_verts": int((a_ > 0).sum()), "arm_max": round(float(a_.max()), 4),
            "leg_verts": int((l_ > 0).sum()), "leg_max": round(float(l_.max()), 4)}


rep["weights"]["census"] = {k_: _census(v_) for k_, v_ in (
    ("skirt+tab+motifs", ("skirt", "skmotif", "skemblem", "fronttab", "fronttabmotif")), ("corset+belts", ("corset", "belt", "buckle")),
    ("vials+medallions+pouch", ("vial", "medal", "medaldisc", "medalcord", "pouch", "pouchflap", "pouchbuckle", "pouchstrap")),
    ("satchel+strap+scrolls", ("strap", "satchel", "satchelflap", "satchelbuckle", "satchelstrapl", "satchelhang", "scrollcase", "scrollroll", "scrollband")),
    ("capelet (shoulder-hung, report)", ("capelet",)))}
print("WEIGHTS", json.dumps(rep["weights"]))

# =========================================================================== the BOOK HOLD, baked into the bind pose (clipless law)
# (L1 default: the RIGHT arm, the sheet's front view; Elias v7 / shadow-assassin precedent.) The stack already stands in its
# hold place (s6). The right PALM goes flat onto the stack's OUTER cover at BOOK_GRIP (u from the front spine toward the fore-
# edge, v up from the bottom, palm standoff), the palm normal into the books; the hand's roll about that normal (10 deg) x
# the elbow poles is searched: the right arm reaches the wrist by analytic two-bone IK, score = the wrist bend + any reach
# shortfall x 1000 + any upper-arm / forearm axis point inside the stack box (arm radius allowed) x 500. Then: the forearm
# takes HOLD_TWIST_SHARE of the wrist's twist, every finger is curl-solved onto the stack (its posed mesh vertices -- LBS,
# the weights above -- come closest to the stack box at FINGER_PAD, none inside: the shadow-assassin per-finger rule), the
# thumb the same way; every mesh is skinned into that pose and the bones re-seated there (the hold IS the bind pose); the
# model is re-centred (bbox centre X/Y at the origin).
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
    HANDS[s] = {"a": a_h, "e": e_h, "n": n_p, "grip": 0.45 * wr + 0.55 * kn + n_p * 0.019, "palm": 0.5 * wr + 0.5 * kn, "kn": kn}


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


def rot_angle(R):
    return math.degrees(math.acos(float(np.clip((np.trace(R) - 1) / 2, -1, 1))))


def fk(D_, n, R=None):
    D_[n] = D_[PARENT[n]] @ Tr(HEADP[n], np.eye(3) if R is None else R)


def curl_axis(side, finger):
    lo_ = side.lower()
    a = HANDS[side]["a"]
    tip = TAILP["%s_03_%s" % (finger, lo_)] - HEADP["%s_01_%s" % (finger, lo_)]
    return a if float((K._rot(a, 0.3) @ tip - tip) @ HANDS[side]["n"]) > 0 else -a


def thumb_pose(D_, side, thumb):
    """(Wren s8's thumb) 5 values = (base flex, swing toward the pinky, roll toward the palm side, middle flex, tip flex)."""
    lo_ = side.lower()
    tax = unit(np.cross(HANDS[side]["n"], unit(TAILP["thumb_01_" + lo_] - HEADP["thumb_01_" + lo_])))
    tip0 = TAILP["thumb_03_" + lo_] - HEADP["thumb_01_" + lo_]
    if float((K._rot(tax, 0.3) @ tip0 - tip0) @ HANDS[side]["n"]) < 0:
        tax = -tax
    f1, sw_, rl_, f2, f3 = thumb
    nn_, ee_ = HANDS[side]["n"], HANDS[side]["e"]
    nax = nn_ if float((K._rot(nn_, 0.3) @ tip0 - tip0) @ HANDS[side]["a"]) > 0 else -nn_
    eax = ee_ if float((K._rot(ee_, 0.3) @ tip0 - tip0) @ nn_) > 0 else -ee_
    fk(D_, "thumb_01_" + lo_, K._rot(nax, math.radians(sw_)) @ K._rot(eax, math.radians(rl_)) @ K._rot(tax, math.radians(f1)))
    fk(D_, "thumb_02_" + lo_, K._rot(tax, math.radians(f2)))
    fk(D_, "thumb_03_" + lo_, K._rot(tax, math.radians(f3)))


D = {n: np.eye(4) for n in DEFORM}
D["root"] = np.eye(4)
HOLD_INFO = {"has_books": HAS_BOOKS}
if HAS_BOOKS:
    BK_C = S_(BOOK_C)                                      # the stack box (rig frame): centre, axes, half sizes
    BK_AX = np.stack([BOOK_X, BOOK_T, BOOK_Z], 0)
    BK_H = np.array([0.5 * BOOK_SIZE[0], 0.5 * BOOK_SIZE[2], 0.5 * BOOK_SIZE[1]])

    def box_sd(P_):
        """signed distance (m) of points to the stack's oriented box (> 0 outside)."""
        q_ = np.abs((np.atleast_2d(P_) - BK_C) @ BK_AX.T) - BK_H
        out_ = np.linalg.norm(np.maximum(q_, 0.0), axis=1)
        return out_ + np.minimum(q_.max(1), 0.0)
    _hr = HANDS["R"]
    GRIP_W = BK_C + BOOK_T * (BK_H[1] + BOOK_GRIP["palm"]) + BOOK_X * (-BK_H[0] + BOOK_GRIP["u"] * 2 * BK_H[0]) + \
        BOOK_Z * (-BK_H[2] + BOOK_GRIP["v"] * 2 * BK_H[2])
    _det0 = float(np.linalg.det(np.stack([_hr["a"], _hr["e"], _hr["n"]], 1)))

    def hand_hold(roll_deg):
        n1 = -BOOK_T                                      # the palm side faces INTO the books
        e1 = math.cos(math.radians(roll_deg)) * BOOK_X + math.sin(math.radians(roll_deg)) * BOOK_Z
        a1 = unit(np.cross(e1, n1))
        if float(np.linalg.det(np.stack([a1, e1, n1], 1))) * _det0 < 0:
            a1 = -a1
        R_ = np.stack([a1, e1, n1], 1) @ np.linalg.inv(np.stack([_hr["a"], _hr["e"], _hr["n"]], 1))
        return TRT(GRIP_W, R_, _hr["palm"])

    def arm_in_box(Du, Dl):
        """max penetration (m) of the upper-arm / forearm axis samples into the stack box (arm radius allowed)."""
        pts_ = [(xf(Du, HEADP["upperarm_r"] + (HEADP["lowerarm_r"] - HEADP["upperarm_r"]) * t_), 0.045) for t_ in np.linspace(0.2, 1.0, 6)] + \
               [(xf(Dl, HEADP["lowerarm_r"] + (HEADP["hand_r"] - HEADP["lowerarm_r"]) * t_), 0.032) for t_ in np.linspace(0.0, 0.85, 6)]
        return max(max(0.0, r_ - float(box_sd(p_)[0])) for p_, r_ in pts_)

    def book_hold(roll_deg, pole):
        Dh = hand_hold(roll_deg)
        wrist = xf(Dh, HEADP["hand_r"])
        Du, Dl, reached, err = two_bone("upperarm_r", "lowerarm_r", "hand_r", HEADP["upperarm_r"], wrist, unit(np.array(pole)))
        return rot_angle(Dl[:3, :3].T @ Dh[:3, :3]), err, arm_in_box(Du, Dl), (Dh, Du, Dl)

    _rs = []
    for pk_, pole_ in enumerate(HOLD_ELBOW_POLES):
        for r_ in range(0, 360, 10):
            b_, e_, pen_, _ = book_hold(float(r_), pole_)
            _rs.append((b_ + 1000.0 * max(e_, 0.0) + 3000.0 * pen_, r_, b_, e_, pk_, pen_))
    _best = min(_rs)
    HAND_ROLL = float(_best[1])
    _, _, _, (D_H, D_U, D_L) = book_hold(HAND_ROLL, HOLD_ELBOW_POLES[_best[4]])
    AX_FA = unit(HEADP["hand_r"] - HEADP["lowerarm_r"])
    _q = Matrix((D_L[:3, :3].T @ D_H[:3, :3]).tolist()).to_quaternion()
    TWIST0 = (math.degrees(2.0 * math.atan2(float(np.dot([_q.x, _q.y, _q.z], AX_FA)), _q.w)) + 180.0) % 360.0 - 180.0
    D_L2 = D_L @ Tr(HEADP["lowerarm_r"], rot(AX_FA, HOLD_TWIST_SHARE * TWIST0))
    _q2 = Matrix((D_L2[:3, :3].T @ D_H[:3, :3]).tolist()).to_quaternion()
    TWIST1 = (math.degrees(2.0 * math.atan2(float(np.dot([_q2.x, _q2.y, _q2.z], AX_FA)), _q2.w)) + 180.0) % 360.0 - 180.0
    D["upperarm_r"], D["lowerarm_r"], D["hand_r"] = D_U, D_L2, D_H
    # per-finger cradle solve on the SKINNED mesh
    _V0main = np.empty(len(low.data.vertices) * 3); low.data.vertices.foreach_get("co", _V0main); _V0main = _V0main.reshape(-1, 3)

    def _lbs_sub(idx):
        W_ = WOBJ["main"][idx]; V_ = _V0main[idx]
        out_ = np.zeros_like(V_)
        for j_ in np.nonzero(W_.sum(0) > 0)[0]:
            n_ = DEFORM[j_]
            out_ += W_[:, j_:j_ + 1] * (V_ @ D[n_][:3, :3].T + D[n_][:3, 3])
        return out_

    def _fverts(f):
        return np.nonzero(sum(WOBJ["main"][:, J["%s_%02d_r" % (f, q)]] for q in (1, 2, 3)) > 0.3)[0]

    def _finger_set(f, k1, k2):
        ax = curl_axis("R", f)
        c_ = HOLD_CURL[0]
        kk = 0.92 if f == "index" else 1.0
        fk(D, "%s_01_r" % f, K._rot(ax, math.radians(c_[0] * kk * k1)))
        fk(D, "%s_02_r" % f, K._rot(ax, math.radians(c_[1] * kk * k2)))
        fk(D, "%s_03_r" % f, K._rot(ax, math.radians(c_[2] * kk * k2)))

    FINGER_SCALES = {}
    for f in ("index", "middle", "ring", "pinky"):
        idx_ = _fverts(f)
        best_ = None
        for k1 in np.linspace(-0.3, 1.9, 23):
            for k2 in np.linspace(0.0, 2.2, 23):
                _finger_set(f, float(k1), float(k2))
                d_ = box_sd(_lbs_sub(idx_))
                sc_ = abs(float(d_.min()) - FINGER_PAD) + 5.0 * float(np.maximum(-d_, 0.0).max())
                if best_ is None or sc_ < best_[0] - 1e-12:
                    best_ = (sc_, float(k1), float(k2))
        _finger_set(f, best_[1], best_[2])
        FINGER_SCALES[f] = [round(best_[1], 2), round(best_[2], 2)]
    _tidx = _fverts("thumb")
    _tbest = None
    for f1_ in range(-60, 61, 10):
        for sw_ in range(-30, 61, 10):
            for rl_ in range(-30, 91, 15):
                for f2_ in range(0, 91, 15):
                    th_ = (float(f1_), float(sw_), float(rl_), float(f2_), 0.8 * f2_)
                    thumb_pose(D, "R", th_)
                    d_ = box_sd(_lbs_sub(_tidx))
                    sc_ = abs(float(d_.min()) - FINGER_PAD) + 5.0 * float(np.maximum(-d_, 0.0).max())
                    if _tbest is None or sc_ < _tbest[0] - 1e-12:
                        _tbest = (sc_, th_, float(d_.min()), float(np.maximum(-d_, 0.0).max()))
    HOLD_THUMB = _tbest[1]
    thumb_pose(D, "R", HOLD_THUMB)
HOLD_BONES = [n for n in DEFORM if not np.allclose(D[n], np.eye(4), atol=1e-12)]
DIG["hold"] = sha(np.stack([D[n] for n in HOLD_BONES])) if HOLD_BONES else "none"


def lbs(V, W):
    out = V.copy()
    for n in HOLD_BONES:
        w = W[:, J[n]]
        m = w > 0
        if m.any():
            Vm = V[m]
            out[m] += w[m][:, None] * ((Vm @ D[n][:3, :3].T + D[n][:3, 3]) - Vm)
    return out


OBS_K = [(low, "main")] + ([(bko, "books")] if HAS_BOOKS else [])
HOLD_V = {}
for ob_, k_ in OBS_K:
    me_ = ob_.data
    V0 = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", V0); V0 = V0.reshape(-1, 3)
    HOLD_V[k_] = lbs(V0, WOBJ[k_])
if HAS_BOOKS:
    # the capelet is SHOULDER-HUNG (arm-coupled weights, style guide): the raised right arm swings its front panel forward into
    # the stack -- in the bind pose those vertices are laid back behind the stack's inner board (along -t, BOOK_CAPELET_CLEAR
    # off it); weights unchanged, the books sit on the capelet against her chest
    _cr = OBJ["main"]["RANGE"]["capelet"]
    _cv = HOLD_V["main"][_cr[0]:_cr[1]]
    _loc = (_cv - BK_C) @ BK_AX.T
    _ins = np.all(np.abs(_loc) < BK_H + BOOK_CAPELET_CLEAR, axis=1)
    _dt = np.where(_ins, (_loc[:, 1] + BK_H[1] + BOOK_CAPELET_CLEAR), 0.0)
    _cv = _cv - np.outer(_dt, BOOK_T)
    HOLD_V["main"][_cr[0]:_cr[1]] = _cv
    HOLD_INFO["capelet_laid_behind_books_verts"] = int(_ins.sum())
    # the hold, measured on the posed mesh (before the re-centre): per-finger pads, finger penetration, arm / chest clearances
    _wm = WOBJ["main"]
    _dom = np.argmax(_wm, 1)
    _domn = np.array(DEFORM, dtype=object)[_dom]
    _Vh = HOLD_V["main"]
    _sd = box_sd(_Vh)
    _fing = np.array([n_.split("_")[0] in ("index", "middle", "ring", "pinky", "thumb") and n_.endswith("_r") for n_ in _domn])
    _hand = np.array([n_ == "hand_r" for n_ in _domn])
    _fore = np.array([n_ == "lowerarm_r" for n_ in _domn])
    _upper = np.array([n_ == "upperarm_r" for n_ in _domn])
    _armr = _fing | _hand | _fore | _upper | np.array([n_ == "clavicle_r" for n_ in _domn])
    _own = np.full(len(_Vh), "", dtype=object)
    for n_, (a_, b_) in OBJ["main"]["RANGE"].items():
        _own[a_:b_] = n_.split(".")[0]
    _pen_parts = {}
    for i_ in np.nonzero(_sd < 0)[0]:
        _pen_parts[_own[i_]] = _pen_parts.get(_own[i_], 0) + 1
    _pads = {}
    for f in ("index", "middle", "ring", "pinky", "thumb"):
        m_ = _wm[:, J["%s_03_r" % f]] > 0.5
        if m_.any():
            _pads[f] = round(1000.0 * float(_sd[m_].min()), 1)
    HOLD_INFO.update({
        "grip_point_m": (GRIP_W).round(4).tolist(), "hand_roll_deg": HAND_ROLL, "elbow_pole": list(HOLD_ELBOW_POLES[_best[4]]),
        "wrist_bend_deg": round(rot_angle(D_L2[:3, :3].T @ D_H[:3, :3]), 2), "wrist_bend_before_twist_split_deg": round(_best[2], 2),
        "twist_deg": {"total": round(TWIST0, 2), "forearm": round(HOLD_TWIST_SHARE * TWIST0, 2), "wrist_left": round(TWIST1, 2)},
        "elbow_flex_deg": round(math.degrees(math.acos(float(np.clip(unit(xf(D_U, HEADP["lowerarm_r"]) - HEADP["upperarm_r"]) @
                                                                     unit(xf(D_L2, HEADP["hand_r"]) - xf(D_U, HEADP["lowerarm_r"])), -1, 1)))), 1),
        "upperarm_swing_deg": round(rot_angle(D_U[:3, :3]), 1), "reach_shortfall_m": round(max(_best[3], 0.0), 5),
        "arm_axis_in_box_m": round(_best[5], 4),
        "fingertip_pad_to_books_mm": _pads, "finger_curl_scales_knuckle_midtip": FINGER_SCALES, "thumb": list(HOLD_THUMB),
        "finger_mesh_max_penetration_mm": round(1000.0 * float(np.maximum(-_sd[_fing], 0.0).max()), 2),
        "palm_hand_mesh_min_to_books_mm": round(1000.0 * float(_sd[_hand].min()), 2),
        "forearm_mesh_min_to_books_mm": round(1000.0 * float(_sd[_fore].min()), 2),
        "upperarm_mesh_min_to_books_mm": round(1000.0 * float(_sd[_upper].min()), 2),
        "chest_body_outfit_min_to_books_mm": round(1000.0 * float(_sd[~_armr].min()), 2),
        "penetrating_verts": {"chest_body_outfit": int((_sd[~_armr] < 0).sum()), "forearm": int((_sd[_fore] < 0).sum()),
                              "upperarm": int((_sd[_upper] < 0).sum()), "hand_palm": int((_sd[_hand] < 0).sum()),
                              "fingers": int((_sd[_fing] < 0).sum())},
        "penetrating_parts": _pen_parts,
        "bend_by_roll_deg_at_that_pole": {int(r_): round(b_, 1) for _, r_, b_, _, pk_, _ in _rs if pk_ == _best[4]}})
_all = np.vstack(list(HOLD_V.values()))
SHIFT2 = np.array([0.5 * (_all[:, 0].min() + _all[:, 0].max()), 0.5 * (_all[:, 1].min() + _all[:, 1].max()), 0.0])
for ob_, k_ in OBS_K:
    HOLD_V[k_] = HOLD_V[k_] - SHIFT2
    ob_.data.vertices.foreach_set("co", HOLD_V[k_].ravel())
    ob_.data.update()
T_S2 = np.eye(4); T_S2[:3, 3] = -SHIFT2
# the posed mesh's normals: the hair loops keep their (deformed) proxy normals, every other loop takes its NEW face normal (an
# LBS-moved sliver's old explicit normal otherwise reads as smooth to the contract checker)
_mh = low.data
if "conquest_smooth_regions" in _mh.keys():
    _cn7 = np.empty(len(_mh.loops) * 3); _mh.corner_normals.foreach_get("vector", _cn7); _cn7 = _cn7.reshape(-1, 3)
    _pn7 = np.empty(len(_mh.polygons) * 3); _mh.polygons.foreach_get("normal", _pn7); _pn7 = _pn7.reshape(-1, 3)
    _lt7 = np.empty(len(_mh.polygons), dtype=np.int64); _mh.polygons.foreach_get("loop_total", _lt7)
    _lp7 = np.repeat(np.arange(len(_lt7)), _lt7)
    _sm7 = np.empty(len(_mh.polygons), dtype=bool); _mh.polygons.foreach_get("use_smooth", _sm7)
    _mh.normals_split_custom_set(np.where(_sm7[_lp7][:, None], _cn7, _pn7[_lp7]).tolist())
    _mh.update()
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
    _FOC[k_] = [(np.array(lo_) - SHIFT2).tolist(), (np.array(hi_) - SHIFT2).tolist()]
if HAS_BOOKS:
    _bv = HOLD_V["books"]
    _hp = xf(T_S2 @ D_H, HEADP["hand_r"])
    _FOC["hold"] = [(np.minimum(_bv.min(0), _hp - 0.06) - 0.03).tolist(), (np.maximum(_bv.max(0), _hp + 0.06) + 0.03).tolist()]
low["conquest_focus"] = json.dumps(_FOC)
HOLD_INFO.update({"bones_reseated": len(HOLD_BONES), "reseat_err": _seat, "recentre_shift_m": SHIFT2.round(5).tolist()})
print("HOLD", json.dumps(HOLD_INFO))
G_HAND_TO_BOOKS = np.linalg.inv(REST4["hand_r"]) @ REST4["books"] if HAS_BOOKS else None
rep["grip"] = {"books": {"rule": "the BIND POSE holds the book stack (L1: the right arm, the sheet's front view): the stack stands against "
                                 "her right chest (s6, slid clear of the body / outfit), the right palm flat on its outer cover at "
                                 "BOOK_GRIP, the hand roll x elbow pole searched for the least wrist bend (arm axis kept out of the "
                                 "stack), HOLD_TWIST_SHARE of the twist on the forearm, each finger + the thumb curl-solved onto the "
                                 "stack; the bone 'books' is a child of hand_r",
                         "hand_r_to_books_bone_rest4": np.round(G_HAND_TO_BOOKS, 6).tolist() if HAS_BOOKS else None, "hold": HOLD_INFO}}
print("GRIP", json.dumps({"hand_roll": HOLD_INFO.get("hand_roll_deg"), "wrist_bend": HOLD_INFO.get("wrist_bend_deg"),
                          "shortfall": HOLD_INFO.get("reach_shortfall_m")}))
rig["conquest_rig"] = ("lyra: root (contract) > MPFB2 game_engine skeleton + books (hand_r child) + follow-through chains %s "
                       "(hair_tail x %d bones, the others x %d)" % (sorted(CHAIN_PTS), TAIL_BONES, HAIR_BONES))
rep["chains"] = {ch: {"bones": CHAIN_NB[ch], "length_m": round(float(Lc), 4), "parent": par} for ch, (_, Lc, par) in CHAIN_PTS.items()}
low["conquest_clips"] = []
low["conquest_clip_status"] = ("none (static build + rig; L5 movement intent is an open artist question); the BIND POSE is the sheet's "
                               "book-stack hold (the default pose the game shows)")
low["conquest_look"] = ("shaded: Col x baked AO, baked normal map on the skin; the hair family (locks + cap) smooth with custom split "
                        "vertex normals (per-group smooth-proxy normals: scalp + tie / ponytail; glTF NORMAL) -- no outline shells, no "
                        "cel bands; stylisation drawn into the palette regions")
if HAS_BOOKS:
    low["conquest_books_grip"] = json.dumps(np.round(G_HAND_TO_BOOKS, 6).tolist())
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
        o.select_set(o in (rig, low) or (HAS_BOOKS and o is bko))
    bpy.context.view_layer.objects.active = rig
    _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
    _props = {q.identifier for q in bpy.ops.export_scene.gltf.get_rna_type().properties}
    _kw = {k_: v_ for k_, v_ in dict(_EG.EXPORT_KW, export_animations=False).items() if k_ in _props}
    bpy.ops.export_scene.gltf(filepath=OUT_GLB, **_kw)
    _au = _EG.audit(OUT_GLB)
    # L3 GLOW GATE: every primitive's _GLOW accessor read back from the glb -- the values must be ALL ZERO (no emitter)
    _gd, _gjs, _gbo = _EG.read_glb(OUT_GLB)
    _gmax, _gcnt = 0.0, 0
    for _m in _gjs.get("meshes", []):
        for _pr in _m["primitives"]:
            if "_GLOW" in _pr["attributes"]:
                _vals = np.array(_EG.accessor_values(_gd, _gjs, _gbo, _pr["attributes"]["_GLOW"]))
                _gmax = max(_gmax, float(np.abs(_vals).max())); _gcnt += len(_vals)
    assert _au["_GLOW"] and _gmax == 0.0, ("GLOWGATE", _au["_GLOW"], _gmax)
    rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "sha256_16": hashlib.sha256(open(OUT_GLB, "rb").read()).hexdigest()[:16],
                  "tris": _au["tris"], "COLOR_0": _au["COLOR_0"], "COLOR_1": _au["COLOR_1"], "_GLOW": _au["_GLOW"],
                  "glow_audit": {"glow_values_read": _gcnt, "glow_abs_max": _gmax, "all_zero": _gmax == 0.0},
                  "skins": _au["skins"], "joints": _au["joints"], "meshes": [q_["mesh"] for q_ in _au["primitives"]]}
    print("GLOWGATE", json.dumps(rep["glb"]["glow_audit"]))
    print("GLB", json.dumps(rep["glb"]))
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
sys.stdout.flush()
os._exit(0)
