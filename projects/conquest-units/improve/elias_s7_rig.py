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


def part_weights(p):
    V, w = p["V"], p["w"]
    n = len(V)
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
        # blends to the pelvis alone (a long coat must not stretch with each thigh) -- arms never pull it
        Wt = mpfb_to_rig(transfer(V))
        for nm_ in DEFORM:
            if nm_.startswith(("upperarm", "lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb")):
                Wt[:, J["spine_03"]] += Wt[:, J[nm_]]; Wt[:, J[nm_]] = 0.0
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
                          "(child of hand_l)"}
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
# the HOLD EVALUATION (not keyed): the staff planted upright where it rests; the right hand must take its grip. Per roll of
# the hand about the shaft (10-deg search) the arm reaches the wrist by analytic IK (elbow out / back / down) and the wrist
# bend is the angle between the lowerarm and the hand; the roll with the least bend (+ any reach shortfall x 1000) wins.
def staff_hold(roll_deg, pole):
    Dst = Tr(S_(STAFF_GRIP0), rot((0, 0, 1), roll_deg))     # the planted staff, turned about its own axis by the roll
    Dh = Dst @ G_STAFF_inv
    Hs = HEADP["upperarm_r"]
    wrist = xf(Dh, HEADP["hand_r"])
    Du, Dl, reached, err = two_bone("upperarm_r", "lowerarm_r", "hand_r", Hs, wrist, unit(np.array(pole)))
    Rw = Dl[:3, :3].T @ Dh[:3, :3]
    bend = math.degrees(math.acos(float(np.clip((np.trace(Rw) - 1) / 2, -1, 1))))
    return bend, err, float(np.linalg.norm(reached - wrist))


_rs = []
for pk_, pole_ in enumerate(STAFF_ELBOW_POLES):
    for r_ in range(0, 360, 10):
        b_, e_, m_ = staff_hold(float(r_), pole_)
        _rs.append((b_ + 1000.0 * max(e_, 0.0), r_, b_, e_, pk_))
_best = min(_rs)
STAFF_ROLL = float(_best[1])
G_HAND_TO_STAFF = np.linalg.inv(REST4["hand_r"]) @ REST4["staff"]
G_HAND_TO_BOOK = np.linalg.inv(REST4["hand_l"]) @ REST4["book"]
rep["grip"] = {"staff": {"rule": "hammer grip: the shaft along the right hand's knuckle line, the orb out of the thumb side; the bone "
                                 "'staff' is a child of hand_r; REST pose stands the staff upright beside the right hand, butt on "
                                 "the floor; the hold evaluation (unkeyed, the staff planted at rest) searched the hand's roll about "
                                 "the shaft in 10-deg steps for the least wrist bend",
                         "G_staff_rest_to_held": np.round(G_STAFF, 6).tolist(), "hand_r_to_staff_bone_rest4": np.round(G_HAND_TO_STAFF, 6).tolist(),
                         "roll_deg": STAFF_ROLL, "wrist_bend_deg": round(_best[2], 2), "reach_shortfall_m": round(max(_best[3], 0.0), 4),
                         "elbow_pole": list(STAFF_ELBOW_POLES[_best[4]]),
                         "bend_by_roll_deg_at_that_pole": {int(r_): round(b_, 1) for _, r_, b_, _, pk_ in _rs if pk_ == _best[4]},
                         "grip_at_m_from_butt": round(STAFF["grip_at"] * STAFF["len"], 4)},
               "book": {"rule": "held in the rest left hand by its spine edge (no clip pose): the bone 'book' is a child of hand_l at "
                                "this transform; roll about the palm normal picked in s6 (fit: no body penetration, least turn)",
                        "hand_l_to_book_bone_rest4": np.round(G_HAND_TO_BOOK, 6).tolist(), "roll_deg": BOOK_ROLL,
                        "penetrating_samples": BOOK_INFO["penetrating_samples"]}}
print("GRIP", json.dumps({"staff_roll": STAFF_ROLL, "wrist_bend": round(_best[2], 2), "shortfall": round(max(_best[3], 0.0), 4),
                          "book_roll": BOOK_ROLL}))
rig["conquest_rig"] = ("elias: root (contract) > MPFB2 game_engine skeleton + staff (hand_r child) + book (hand_l child) + "
                       "follow-through chains %s x %d bones (head children)" % (sorted(CHAIN_PTS), HAIR_BONES))
rep["chains"] = {ch: {"bones": HAIR_BONES, "length_m": round(float(Lc), 4)} for ch, (_, Lc, _) in CHAIN_PTS.items()}
low["conquest_clips"] = []
low["conquest_clip_status"] = "none (v1 draft: static build + rig; movement intent is an open artist question)"
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
