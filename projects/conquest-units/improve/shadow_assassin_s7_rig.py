# Shadow Assassin build section 7 (adapted from elias_s7_rig.py): the rig -- MPFB2 game_engine skeleton + the BLADE bone
# (child of hand_r); no follow-through chains, NO clips (S3: movement intent is an open artist question); skin weights
# (<= 4 influences; trunk-hung garments copy from the TRUNK only, never the arm chain); the BLADE HOLD BAKED INTO THE BIND
# POSE (clipless law, Elias v7 precedent): the blade sits at its sheet pose (s6), the right hand's grip roll about the blade
# axis + the elbow pole are searched for the least wrist bend, the arm two-bone IK-solved onto it, the fingers closed
# (HOLD_CURL grip), the thumb wrap solved, every mesh LBS'd into the pose, the bones re-seated, the model re-centred; the
# recorded grip transform; the rigged save + the glb through the shared _GLOW path.
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS, "version": "v1 draft", "clips": "none (S3: movement intent is an open artist question)"}
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
GRIP_W = S_(BLADE_GRIP_P)                                  # the blade's grip centre (held pose; shifted frame)
BONES.append(("blade", GRIP_W, GRIP_W + _zb * 0.12, "hand_r", None))
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


# TORSO_HUNG (Wren / Elias v7 law): the trunk-hung garment parts copy their skin weights from the TRUNK only (the non-arm
# triangles, tie-invariant nearest), the arm-chain share (clavicle included) zeroed and renormalised. Shoulder-hung parts
# (capes, shoulder plates) keep the whole-body transfer (they ride the arm, style-guide law).
TORSO_HUNG = ("skirt", "skirtunder", "sash", "sashchevron", "belt", "buckle", "buckleinset", "beltring", "pouch", "pouchflap",
              "pouchstud", "chest_strap", "pendant", "pendantgem", "pendantbail", "brooch", "broochgem")
ARM_CHAIN = ("clavicle", "upperarm", "lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb")
_ARM_COLS = np.array([n.split("_")[0] in ARM_CHAIN for n in MB])
_TRI_TRUNK = TRI[~is_arm_f[np.asarray(TRI_F)]]
BVH_TRI_TRUNK = BVHTree.FromPolygons(BV.tolist(), _TRI_TRUNK.tolist())


def transfer_trunk(Ps):
    Wm = bary_weights(Ps, BVH_TRI_TRUNK, _TRI_TRUNK, BW, BV)
    Wm[:, _ARM_COLS] = 0.0
    _z = Wm.sum(1) < 1e-6                             # a source triangle carrying ONLY arm-chain weight (the armpit /
    Wm[_z, MBI["spine_03"]] = 1.0                     #   clavicle seam): the point rides spine_03
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
    if w == "collar":                                 # the scarf (Elias's cravat rule): skin weights, head share -> spine_03
        W = mpfb_to_rig(transfer(V))
        W[:, J["spine_03"]] += W[:, J["head"]] + W[:, J["neck_01"]] * 0.5; W[:, J["head"]] = 0.0
        W[:, J["neck_01"]] *= 0.5
        arm_ = [J[q] for q in DEFORM if q.split("_")[0] in ARM_CHAIN]
        W[:, J["spine_03"]] += W[:, arm_].sum(1); W[:, arm_] = 0.0
        return W
    if w == "hood":                                   # head / neck / trunk skin (never the arm chain): the hood follows the
        return mpfb_to_rig(transfer_trunk(V))        #   head, its shoulder flare the upper trunk
    if w == "cloak":                                  # Elias's coat rule: trunk skin above the belt, blending to the pelvis
        Wt = mpfb_to_rig(transfer_trunk(V))           #   alone below it (a long cloak must not stretch with each thigh)
        k_ = smoothstep(Z_BELT + 0.05, Z_BELT - 0.25, V[:, 2])[:, None]
        return (1.0 - k_) * Wt + k_ * onehot(n, "pelvis")
    if w == "fauld":
        b_ = 0.55 * smoothstep(0.2, 1.0, p["v_param"])[:, None]
        return (1 - b_) * onehot(n, "pelvis") + b_ * mpfb_to_rig(transfer(V))
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
for ob_, k_ in ((low, "main"), (bko, "blade")):
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
rep["weights"] = {"max_influences": int(max((W > 0).sum(1).max() for W in WOBJ.values())),
                  "unweighted": int(sum(((W > 0).sum(1) == 0).sum() for W in WOBJ.values())),
                  "sum_dev_max": float(max(np.abs(W.sum(1) - 1.0).max() for W in WOBJ.values())), "seconds": round(time.time() - t_, 1),
                  "rule": "body: MPFB game_engine weights; boots / bracers / knee guards / capes / shoulder plates: the nearest skin "
                          "point's weights (tie-invariant); boot feet: foot -> ball; face wrap: head; scarf: skin with head + half "
                          "the neck + any arm share moved to spine_03; hood: head / neck / trunk skin (arm chain never); cloak + "
                          "sigil: trunk skin above the belt blending to the pelvis below; TORSO_HUNG (" + ", ".join(TORSO_HUNG) +
                          "): TRUNK faces only (skirts / sash: pelvis -> trunk skin under the hem, <= 55 %); blade: 'blade' (child of hand_r)"}
_arm_j = [J[n] for n in DEFORM if n.split("_")[0] in ARM_CHAIN]


def _census(names):
    ids_ = np.array([i for nm in OBJ["main"]["RANGE"] if nm.split(".")[0] in names for i in range(*OBJ["main"]["RANGE"][nm])],
                    dtype=np.int64)
    a_ = WM[ids_][:, _arm_j].sum(1)
    return {"verts": int(len(ids_)), "arm_verts": int((a_ > 0).sum()), "arm_max": round(float(a_.max()), 4)}


rep["weights"]["census"] = {k_: _census(v_) for k_, v_ in (
    ("cloak+sigil", ("cloak", "cloaksigil", "cloakornament", "cloakornament_bar")), ("skirts+sash", ("skirt", "skirtunder", "sash", "sashchevron")),
    ("belt+buckle+rings+pouches", ("belt", "buckle", "buckleinset", "beltring", "pouch", "pouchflap", "pouchstud")),
    ("chest straps+brooch+pendant", ("chest_strap", "brooch", "broochgem", "pendant", "pendantgem", "pendantbail")),
    ("hood", ("hood",)), ("capes (shoulder-hung, report)", ("cape0", "cape1")), ("shoulder plates (shoulder-hung, report)", ("shoulderplate",)))}
print("WEIGHTS", json.dumps(rep["weights"]))

# =========================================================================== the BLADE HOLD, baked into the bind pose
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


def rot_angle(R):
    return math.degrees(math.acos(float(np.clip((np.trace(R) - 1) / 2, -1, 1))))


# the hand frame at rest (a = index -> pinky, e = toward the fingertips, n = palm normal toward the thumb) -> held: a along the
# blade axis (the blade exits the PINKY side: the sheet's reverse grip), the hand turned by `roll` about it (0 = the fingers'
# direction e along the blade's convex side), the fist centre on the grip centre
# GRIP_DIAG: the grip channel runs DIAGONALLY across the palm (the handle's blade end tilted from the knuckle line toward the
# wrist, about the palm normal -- a loose reverse hold / ulnar deviation); the sign is the one that shortens the wrist bend
def _grip_frame(beta_deg):
    hn = HANDS["R"]["n"]
    g_ = unit(rot(hn, beta_deg) @ HANDS["R"]["a"])
    c2 = unit(np.cross(hn, g_))
    c2 = c2 if float(c2 @ HANDS["R"]["e"]) > 0 else -c2
    return np.stack([g_, c2, hn], 1), float(np.sign(np.dot(hn, np.cross(g_, c2))))


def hand_hold(roll_deg, beta_deg=None):
    Fh0, sg_ = _grip_frame(GRIP_DIAG * GRIP_SIGN if beta_deg is None else beta_deg)
    a1 = _zb
    e1 = rot(a1, roll_deg) @ _xb
    n1 = sg_ * np.cross(a1, e1)
    R_ = np.stack([a1, e1, n1], 1) @ Fh0.T
    return TRT(GRIP_W, R_, HANDS["R"]["grip"])


def blade_hold(roll_deg, pole):
    Dh = hand_hold(roll_deg)
    wrist = xf(Dh, HEADP["hand_r"])
    Du, Dl, reached, err = two_bone("upperarm_r", "lowerarm_r", "hand_r", HEADP["upperarm_r"], wrist, unit(np.array(pole)))
    return rot_angle(Dl[:3, :3].T @ Dh[:3, :3]), err, (Dh, Du, Dl)


GRIP_SIGN = 1.0
_sgn_try = {}
for sg_try in (1.0, -1.0):
    GRIP_SIGN = sg_try
    _sgn_try[sg_try] = min(blade_hold(float(r_), p_)[0] + 1000.0 * max(blade_hold(float(r_), p_)[1], 0.0)
                           for p_ in HOLD_ELBOW_POLES for r_ in range(0, 360, 10))
GRIP_SIGN = min(_sgn_try, key=lambda k_: _sgn_try[k_])
_rs = []
for pk_, pole_ in enumerate(HOLD_ELBOW_POLES):
    for r_ in range(0, 360, 10):
        b_, e_, _ = blade_hold(float(r_), pole_)
        _rs.append((b_ + 1000.0 * max(e_, 0.0), r_, b_, e_, pk_))
_best = min(_rs)
HAND_ROLL = float(_best[1])
_, _, (D_H, D_U, D_L) = blade_hold(HAND_ROLL, HOLD_ELBOW_POLES[_best[4]])
AX_FA = unit(HEADP["hand_r"] - HEADP["lowerarm_r"])
_q = Matrix((D_L[:3, :3].T @ D_H[:3, :3]).tolist()).to_quaternion()
TWIST0 = (math.degrees(2.0 * math.atan2(float(np.dot([_q.x, _q.y, _q.z], AX_FA)), _q.w)) + 180.0) % 360.0 - 180.0
D_L2 = D_L @ Tr(HEADP["lowerarm_r"], rot(AX_FA, HOLD_TWIST_SHARE * TWIST0))
_q2 = Matrix((D_L2[:3, :3].T @ D_H[:3, :3]).tolist()).to_quaternion()
TWIST1 = (math.degrees(2.0 * math.atan2(float(np.dot([_q2.x, _q2.y, _q2.z], AX_FA)), _q2.w)) + 180.0) % 360.0 - 180.0
D = {n: np.eye(4) for n in DEFORM}
D["root"] = np.eye(4)
D["upperarm_r"], D["lowerarm_r"], D["hand_r"] = D_U, D_L2, D_H


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


def fingers(D_, side, curl):
    lo_ = side.lower()
    for f in ("index", "middle", "ring", "pinky"):
        ax = curl_axis(side, f)
        for k in range(3):
            fk(D_, "%s_%02d_%s" % (f, k + 1, lo_), K._rot(ax, math.radians(curl[k] * (0.92 if f == "index" else 1.0))))


def off_axis(p):
    """distance (m) of p from the blade's grip axis (the line through GRIP_W along _zb)."""
    d_ = np.asarray(p) - GRIP_W
    return float(np.linalg.norm(d_ - float(d_ @ _zb) * _zb))


fingers(D, "R", HOLD_CURL[0])
# per-finger WRAP on the SKINNED MESH (the diagonal grip: each finger meets the handle at its own distance): per finger the
# (knuckle, middle + tip) curl scales on a grid whose posed finger vertices (LBS, the weights above) come closest to the grip
# surface at FINGER_PAD, none inside it; then the thumb's 5-value pose the same way (its pad onto the handle / over the fingers)
FINGER_PAD = 0.0010
FINGER_SCALES = {}
_V0main = np.empty(len(low.data.vertices) * 3); low.data.vertices.foreach_get("co", _V0main); _V0main = _V0main.reshape(-1, 3)
_ax_d = _zb


def _lbs_sub(idx):
    W_ = WOBJ["main"][idx]; V_ = _V0main[idx]
    out_ = np.zeros_like(V_)
    for j_ in np.nonzero(W_.sum(0) > 0)[0]:
        n_ = DEFORM[j_]
        out_ += W_[:, j_:j_ + 1] * (V_ @ D[n_][:3, :3].T + D[n_][:3, 3])
    return out_


def _surf_d(P_):
    d_ = P_ - GRIP_W
    return np.linalg.norm(d_ - (d_ @ _ax_d)[:, None] * _ax_d, axis=1) - BLADE["grip_r"]


def _fverts(f):
    return np.nonzero(sum(WOBJ["main"][:, J["%s_%02d_r" % (f, q)]] for q in (1, 2, 3)) > 0.3)[0]


def _finger_set(f, k1, k2):
    ax = curl_axis("R", f)
    c_ = HOLD_CURL[0]
    kk = 0.92 if f == "index" else 1.0
    fk(D, "%s_01_r" % f, K._rot(ax, math.radians(c_[0] * kk * k1)))
    fk(D, "%s_02_r" % f, K._rot(ax, math.radians(c_[1] * kk * k2)))
    fk(D, "%s_03_r" % f, K._rot(ax, math.radians(c_[2] * kk * k2)))


for f in ("index", "middle", "ring", "pinky"):
    idx_ = _fverts(f)
    best_ = None
    for k1 in np.linspace(0.1, 1.9, 19):
        for k2 in np.linspace(0.1, 1.9, 19):
            _finger_set(f, float(k1), float(k2))
            d_ = _surf_d(_lbs_sub(idx_))
            sc_ = abs(float(d_.min()) - FINGER_PAD) + 5.0 * float(np.maximum(-d_, 0.0).max())
            if best_ is None or sc_ < best_[0] - 1e-12:
                best_ = (sc_, float(k1), float(k2))
    _finger_set(f, best_[1], best_[2])
    FINGER_SCALES[f] = [round(best_[1], 2), round(best_[2], 2)]
_tidx = _fverts("thumb")
_others = np.concatenate([_fverts(f) for f in ("index", "middle")])
_bvh_oth = None
_tbest = None
for f1_ in range(-60, 61, 10):
    for sw_ in range(-30, 61, 10):
        for rl_ in range(-30, 91, 15):
            for f2_ in range(0, 91, 15):
                th_ = (float(f1_), float(sw_), float(rl_), float(f2_), 0.8 * f2_)
                thumb_pose(D, "R", th_)
                d_ = _surf_d(_lbs_sub(_tidx))
                sc_ = abs(float(d_.min()) - FINGER_PAD) + 5.0 * float(np.maximum(-d_, 0.0).max())
                if _tbest is None or sc_ < _tbest[0] - 1e-12:
                    _tbest = (sc_, th_, float(d_.min()), float(np.maximum(-d_, 0.0).max()))
HOLD_THUMB = _tbest[1]
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
for ob_, k_ in ((low, "main"), (bko, "blade")):
    me_ = ob_.data
    V0 = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", V0); V0 = V0.reshape(-1, 3)
    HOLD_V[k_] = lbs(V0, WOBJ[k_])
# the fingertip PADS vs the grip (posed mesh, before the re-centre): per finger, its tip-bone-dominant vertices' least
# distance to the grip surface (axis distance - grip radius; < 0 = inside the grip)
_wmain = WOBJ["main"]
_pads = {}
for f in ("index", "middle", "ring", "pinky", "thumb"):
    m_ = _wmain[:, J["%s_03_r" % f]] > 0.5
    if m_.any():
        _pads[f] = round(1000.0 * (min(off_axis(p_) for p_ in HOLD_V["main"][m_]) - BLADE["grip_r"]), 1)
_fpen = {f: round(1000.0 * float(np.maximum(-_surf_d(HOLD_V["main"][_fverts(f)]), 0.0).max()), 2)
         for f in ("index", "middle", "ring", "pinky", "thumb")}
_all = np.vstack(list(HOLD_V.values()))
SHIFT2 = np.array([0.5 * (_all[:, 0].min() + _all[:, 0].max()), 0.5 * (_all[:, 1].min() + _all[:, 1].max()), 0.0])
for ob_, k_ in ((low, "main"), (bko, "blade")):
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
    low[key_] = (np.array(low[key_]) - SHIFT2).tolist()
_FOC = json.loads(low["conquest_focus"])
for k_, (lo_, hi_) in list(_FOC.items()):
    _FOC[k_] = [(np.array(lo_) - SHIFT2).tolist(), (np.array(hi_) - SHIFT2).tolist()]
_hp = xf(T_S2 @ D_H, HEADP["hand_r"])
_FOC["hold"] = [(_hp - 0.12).tolist(), (_hp + 0.12).tolist()]
low["conquest_focus"] = json.dumps(_FOC)
_gp = xf(D_H, HANDS["R"]["grip"])
_tipd = {f: round(1000.0 * (off_axis(xf(D["%s_03_r" % f], TAILP["%s_03_r" % f])) - BLADE["grip_r"]), 1)
         for f in ("index", "middle", "ring", "pinky", "thumb")}
HOLD_INFO = {"grip_centre_m": (GRIP_W - SHIFT2).round(4).tolist(), "grip_z_m": round(float(GRIP_W[2]), 4),
             "fist_off_axis_mm": round(1000.0 * off_axis(_gp), 3),
             "hand_roll_deg": HAND_ROLL, "grip_diag_deg": GRIP_DIAG * GRIP_SIGN, "elbow_pole": list(HOLD_ELBOW_POLES[_best[4]]),
             "wrist_bend_deg": round(rot_angle(D_L2[:3, :3].T @ D_H[:3, :3]), 2), "wrist_bend_before_twist_split_deg": round(_best[2], 2),
             "twist_deg": {"total": round(TWIST0, 2), "forearm": round(HOLD_TWIST_SHARE * TWIST0, 2), "wrist_left": round(TWIST1, 2)},
             "elbow_flex_deg": round(math.degrees(math.acos(float(np.clip(unit(xf(D_U, HEADP["lowerarm_r"]) - HEADP["upperarm_r"]) @
                                                                          unit(xf(D_L2, HEADP["hand_r"]) - xf(D_U, HEADP["lowerarm_r"])), -1, 1)))), 1),
             "upperarm_swing_deg": round(rot_angle(D_U[:3, :3]), 1),
             "fingertip_bone_to_grip_surface_mm": _tipd, "fingertip_pad_mesh_to_grip_surface_mm": _pads, "finger_mesh_max_penetration_mm": _fpen, "thumb": list(HOLD_THUMB), "finger_curl_scales_knuckle_midtip": FINGER_SCALES,
             "thumb_closest_to_grip_surface_mm": round(1000.0 * _tbest[2], 1), "bones_reseated": len(HOLD_BONES), "reseat_err": _seat,
             "recentre_shift_m": SHIFT2.round(5).tolist(), "reach_shortfall_m": round(max(_best[3], 0.0), 5),
             "bend_by_roll_deg_at_that_pole": {int(r_): round(b_, 1) for _, r_, b_, _, pk_ in _rs if pk_ == _best[4]}}
print("HOLD", json.dumps(HOLD_INFO))
G_HAND_TO_BLADE = np.linalg.inv(REST4["hand_r"]) @ REST4["blade"]
rep["grip"] = {"blade": {"rule": "the BIND POSE holds the blade (the sheet FRONT view's reverse hold): the blade at BLADE_GRIP vs the "
                                 "right shoulder joint, leaned by BLADE_TILT; the right hand's knuckle line along the blade axis (blade "
                                 "out of the pinky side), its roll about that axis x the elbow poles searched (10 deg) for the least "
                                 "wrist bend, the arm by analytic two-bone IK, HOLD_TWIST_SHARE of the twist on the forearm, the fingers "
                                 "closed (HOLD_CURL), the thumb wrap solved; bone 'blade' is a child of hand_r",
                         "hand_r_to_blade_bone_rest4": np.round(G_HAND_TO_BLADE, 6).tolist(), "hold": HOLD_INFO,
                         "wrist_bend_deg": HOLD_INFO["wrist_bend_deg"], "hand_roll_deg": HAND_ROLL,
                         "reach_shortfall_m": HOLD_INFO["reach_shortfall_m"]}}
print("GRIP", json.dumps({"hand_roll": HAND_ROLL, "wrist_bend": HOLD_INFO["wrist_bend_deg"], "shortfall": HOLD_INFO["reach_shortfall_m"],
                          "pads_mm": _pads}))
rig["conquest_rig"] = "shadow_assassin: root (contract) > MPFB2 game_engine skeleton + blade (hand_r child); no chains"
low["conquest_clips"] = []
low["conquest_clip_status"] = "none (S3: movement intent open); the BIND POSE is the sheet's blade hold (the default pose the game shows)"
low["conquest_look"] = ("shaded: Col x baked AO, baked normal map on the gloves only; all faces flat -- no outline shells, no cel bands; "
                        "stylisation drawn into the palette regions; no face (S5: wrap + hood void)")
low["conquest_blade_grip"] = json.dumps(np.round(G_HAND_TO_BLADE, 6).tolist())
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
set_tex_paths("//../improved/textures/" if not SCRATCH else "//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)
if WANT_GLB:
    # the shared game-ready path (Elias v4 / Varden s7): export_glb.add_glow_attr puts the float '_GLOW' copy of Glow.rgb on
    # every exported mesh in memory (the blend above is saved without it), then the shared EXPORT_KW minus animations (no clips)
    import export_glb as _EG
    for o in scene.objects:
        o.select_set(o in (rig, low, bko))
    bpy.context.view_layer.objects.active = rig
    _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
    _props = {q.identifier for q in bpy.ops.export_scene.gltf.get_rna_type().properties}
    _kw = {k_: v_ for k_, v_ in dict(_EG.EXPORT_KW, export_animations=False).items() if k_ in _props}
    bpy.ops.export_scene.gltf(filepath=OUT_GLB, **_kw)
    _au = _EG.audit(OUT_GLB)
    # _GLOW audit: per mesh, which regions carry nonzero glow and the peak value (read back from the source attribute)
    _ga = {}
    for o in (low, bko):
        gl_ = o.data.attributes.get("_GLOW")
        if gl_ is None:
            continue
        g_ = np.empty(len(gl_.data) * 3); gl_.data.foreach_get("vector", g_); g_ = g_.reshape(-1, 3)
        lt_ = np.empty(len(o.data.polygons), dtype=np.int64); o.data.polygons.foreach_get("loop_total", lt_)
        lp_ = np.repeat(np.arange(len(lt_)), lt_)
        names_ = list(o.data["conquest_regions"]); rid_ = np.empty(len(lt_), dtype=np.int32)
        o.data.attributes["region_id"].data.foreach_get("value", rid_)
        nz_ = g_.max(1) > 0
        _ga[o.name] = {"nonzero_regions": sorted(set(names_[rid_[lp_[i]]] for i in np.nonzero(nz_)[0])),
                       "peak": round(float(g_.max()), 4), "nonzero_corners": int(nz_.sum()), "corners": int(len(g_))}
    import hashlib as _hl
    rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "sha256_16": _hl.sha256(open(OUT_GLB, "rb").read()).hexdigest()[:16],
                  "tris": _au["tris"], "COLOR_0": _au["COLOR_0"], "COLOR_1": _au["COLOR_1"], "_GLOW": _au["_GLOW"], "skins": _au["skins"],
                  "joints": _au["joints"], "meshes": [q_["mesh"] for q_ in _au["primitives"]], "glow_audit": _ga}
    print("GLB", json.dumps(rep["glb"]))
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")})[:1500])
sys.stdout.flush()
os._exit(0)
