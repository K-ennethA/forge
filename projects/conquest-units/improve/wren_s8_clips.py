# Wren build section 8: clips idle / walk / run (closed-form poses -> keyed FK; legs + the pitchfork arm by analytic IK), follow-through
# as the periodic steady state of damped springs, the clip gates (zero foot slip / drift, zero toe dips, exact seams,
# grip relation, cloak poke-through, the fork vs the cloak / body), the rigged save, the glb, the winter skin.
# HAS_FORK False (review-log 2026-10-03): the right arm still follows the carry frame (the approved path: the same IK
# targets, the same roll search), but no 'pitchfork' bone is keyed, the right fingers take the clip's left-hand pose
# (hands(): 2026-10-04 both hands from CLIP_HANDS / HAND_POSES), and every fork gate / report field is left out.
TAU = 2 * math.pi


def ss5(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * t * (t * (6 * t - 15) + 10)


def fk(D, n, R=None):
    D[n] = D[PARENT[n]] @ Tr(HEADP[n], np.eye(3) if R is None else R)


def two_bone(b1, b2, b3, H, T, pole):
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


LEGS = {}
for s in "LR":
    lo_ = s.lower()
    H0, A0 = HEADP["thigh_" + lo_], HEADP["foot_" + lo_]
    Kn = HEADP["calf_" + lo_]
    LEGS[s] = {"len": float(np.linalg.norm(Kn - H0) + np.linalg.norm(A0 - Kn)),
               "C0": np.array([HEADP["ball_" + lo_][0], HEADP["ball_" + lo_][1], 0.0]),
               "knee_dir": unit((Kn - H0) - float((Kn - H0) @ unit(A0 - H0)) * unit(A0 - H0))}
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
ARM_LEN = {s: float(np.linalg.norm(HEADP["lowerarm_" + s.lower()] - HEADP["upperarm_" + s.lower()]) +
                    np.linalg.norm(HEADP["hand_" + s.lower()] - HEADP["lowerarm_" + s.lower()])) for s in "LR"}
BOOT_PARTS = ("bootfoot", "sole", "bootstrap")
TOE_PTS = {}
for s in "LR":
    _tp = [p for p in PARTS if p["name"].split(".")[0] in BOOT_PARTS and p["name"].split(".")[1].startswith(s)]
    _V = np.vstack([p["V"] for p in _tp])
    _wb = np.concatenate([smoothstep(-0.022, 0.0, (p["V"] - p["c0"]) @ p["fd"]) for p in _tp])
    TOE_PTS[s] = (_V - SHIFT, _wb)
TOE_REACH = max(float(np.max((TOE_PTS[s][0] - LEGS[s]["C0"]) @ BOOT_RING[s]["fd"])) for s in "LR")
ROLL = {"idle": 0.0, "walk": 0.0, "run": 0.0}
TINE_ROLL = 0.0
AXROT = np.eye(4)


def set_tine_roll(deg):
    """the fork's rotation about its own shaft axis (rest: vertical through the butt) inside the hand."""
    global TINE_ROLL, AXROT
    TINE_ROLL = deg
    AXROT = Tr(S_(FORK_T), Rz(deg))
# the grip transform G: the (rest) pitchfork -> held in the (rest) right hand, hammer grip, the shaft along the knuckle
# line, the tines out of the thumb / index side (the sheet)
R_G = frame_to(HANDS["R"]["e"], -HANDS["R"]["a"])
G = TRT(HANDS["R"]["grip"], R_G, S_(FORK_GRIP0))
G_inv = np.linalg.inv(G)


def curl_axis(side, finger):
    lo_ = side.lower()
    a = HANDS[side]["a"]
    b1 = "%s_01_%s" % (finger, lo_)
    tip = TAILP["%s_03_%s" % (finger, lo_)] - HEADP[b1]
    moved = K._rot(a, 0.3) @ tip
    return a if float((moved - tip) @ HANDS[side]["n"]) > 0 else -a


CURL_AX = {(s, f): curl_axis(s, f) for s in "LR" for f in ("index", "middle", "ring", "pinky")}


def fingers(D, side, curl, thumb=(0.0, 0.0)):
    lo_ = side.lower()
    for f in ("index", "middle", "ring", "pinky"):
        ax = CURL_AX[(side, f)]
        for k in range(3):
            fk(D, "%s_%02d_%s" % (f, k + 1, lo_), K._rot(ax, math.radians(curl[k] * (0.92 if f == "index" else 1.0))))
    tax = unit(np.cross(HANDS[side]["n"], unit(TAILP["thumb_01_" + lo_] - HEADP["thumb_01_" + lo_])))
    tip0 = TAILP["thumb_03_" + lo_] - HEADP["thumb_01_" + lo_]
    if float((K._rot(tax, 0.3) @ tip0 - tip0) @ HANDS[side]["n"]) < 0:
        tax = -tax
    if len(thumb) == 5:
        # (2026-10-04) the 5-value thumb (base flex, swing toward the pinky, roll toward the palm side, middle flex, tip
        # flex): the swing / roll axes are the palm normal / the hand's long axis, signed by their effect (mirror-exact)
        f1, sw_, rl_, f2, f3 = thumb
        nn_, ee_ = HANDS[side]["n"], HANDS[side]["e"]
        nax = nn_ if float((K._rot(nn_, 0.3) @ tip0 - tip0) @ HANDS[side]["a"]) > 0 else -nn_
        eax = ee_ if float((K._rot(ee_, 0.3) @ tip0 - tip0) @ nn_) > 0 else -ee_
        fk(D, "thumb_01_" + lo_, K._rot(nax, math.radians(sw_)) @ K._rot(eax, math.radians(rl_)) @ K._rot(tax, math.radians(f1)))
        fk(D, "thumb_02_" + lo_, K._rot(tax, math.radians(f2)))
        fk(D, "thumb_03_" + lo_, K._rot(tax, math.radians(f3)))
        return
    fk(D, "thumb_01_" + lo_, K._rot(tax, math.radians(thumb[0])))
    fk(D, "thumb_02_" + lo_, K._rot(tax, math.radians(thumb[1])))
    fk(D, "thumb_03_" + lo_, K._rot(tax, math.radians(thumb[1] * 0.6)))


def hands(D, clip):
    """both hands' finger poses from CLIP_HANDS[clip] = (left, right) names in HAND_POSES (HAS_FORK: the right = "grip")."""
    for side, nm in zip("LR", CLIP_HANDS[clip]):
        if side == "R" and HAS_FORK:
            nm = "grip"
        fingers(D, side, HAND_POSES[nm]["curl"], HAND_POSES[nm]["thumb"])


ELB_AX = {}
for s in "LR":
    lo_ = s.lower()
    ua = unit(TAILP["upperarm_" + lo_] - HEADP["upperarm_" + lo_]); la = unit(TAILP["lowerarm_" + lo_] - HEADP["lowerarm_" + lo_])
    ax = unit(np.cross(ua, la))
    ELB_AX[s] = ax if float(ua @ (K._rot(ax, 0.2) @ la)) < float(ua @ la) else -ax


def free_arm(D, side, lower, fwd, bend, lag_bend=0.0):
    lo_ = side.lower()
    sg = 1.0 if side == "L" else -1.0
    fk(D, "clavicle_" + lo_)
    fk(D, "upperarm_" + lo_, Rx(fwd) @ Ry(sg * lower))
    fk(D, "lowerarm_" + lo_, K._rot(ELB_AX[side], math.radians(bend + lag_bend)))
    fk(D, "hand_" + lo_)


def fork_arm(D, Dfk):
    """the right hand follows the pitchfork (D_hand = D_fork G^-1); the arm reaches the wrist by analytic IK, the elbow
    down / back / out."""
    Dh = Dfk @ G_inv
    fk(D, "clavicle_r")
    Hs = xf(D["clavicle_r"], HEADP["upperarm_r"])
    wrist = xf(Dh, HEADP["hand_r"])
    pole = unit(np.array(FORK_ELBOW_POLE))            # the elbow out, down and a little FORWARD (back pushed it into the cloak)
    D["upperarm_r"], D["lowerarm_r"], reached, err = two_bone("upperarm_r", "lowerarm_r", "hand_r", Hs, wrist, pole)
    D["hand_r"] = Dh
    if HAS_FORK:
        D["pitchfork"] = Dfk @ AXROT
    return err, float(np.linalg.norm(reached - wrist))


def plant_legs(D, feet):
    info = {}
    for s in "LR":
        lo_ = s.lower()
        C, Rf, Rb = feet[s]
        A = C + Rf @ (HEADP["foot_" + lo_] - LEGS[s]["C0"])
        H = xf(D["pelvis"], HEADP["thigh_" + lo_])
        pole = Rf @ LEGS[s]["knee_dir"]
        D["thigh_" + lo_], D["calf_" + lo_], At, err = two_bone("thigh_" + lo_, "calf_" + lo_, "foot_" + lo_, H, A, pole)
        D["foot_" + lo_] = TRT(At, Rf, HEADP["foot_" + lo_])
        D["ball_" + lo_] = TRT(xf(D["foot_" + lo_], HEADP["ball_" + lo_]), Rb, HEADP["ball_" + lo_])
        info[s] = err
    return info


def pelvis_height(Rp, off_xy, feet, reach_k):
    best = math.inf
    for s in "LR":
        lo_ = s.lower()
        C, Rf, _ = feet[s]
        A = C + Rf @ (HEADP["foot_" + lo_] - LEGS[s]["C0"])
        h0 = HEADP["pelvis"] + Rp @ (HEADP["thigh_" + lo_] - HEADP["pelvis"]) + np.array([off_xy[0], off_xy[1], 0.0])
        r = reach_k * LEGS[s]["len"]
        dxy2 = float((A[0] - h0[0]) ** 2 + (A[1] - h0[1]) ** 2)
        best = min(best, A[2] + math.sqrt(max(r * r - dxy2, 1e-9)) - h0[2])
    return best


def ease_wave(x):
    return math.sin(x) if IDLE_EASE <= 0 else math.tanh(IDLE_EASE * math.sin(x)) / math.tanh(IDLE_EASE)


# ---- idle: the planted pitchfork (butt on the floor, leaning a little toward him), the right hand on its grip
_butt_rest = S_(FORK_T)
IDLE_BUTT = np.array([FORK_IDLE_BUTT[0] - SHIFT[0], FORK_IDLE_BUTT[1] - SHIFT[1], 0.0])
FORK_BUTT_R = FORK["shaft_r"][0] + FORK["ferrule_t"] * 1.35   # the butt ferrule's rim radius (wren_parts.pitchfork)
IDLE_FEET_C = {}
for s in "LR":
    dx, dy, yaw = IDLE_FEET[s]
    sg = 1.0 if s == "L" else -1.0
    IDLE_FEET_C[s] = (LEGS[s]["C0"] + np.array([sg * dx, dy, 0.0]), Rz(-sg * yaw))


def pose_idle(t):
    D = {"root": np.eye(4)}
    info = {}
    br = math.sin(TAU * t)
    sway = ease_wave(TAU * t + 0.7)
    sway_c = ease_wave(TAU * (t - IDLE_OVERLAP[0] / IDLE_N) + 0.7)
    sway_h = ease_wave(TAU * (t - IDLE_OVERLAP[1] / IDLE_N) + 0.7)
    feet = {s: (IDLE_FEET_C[s][0], IDLE_FEET_C[s][1], IDLE_FEET_C[s][1]) for s in "LR"}
    Rp = Ry(IDLE_WEIGHT[1] + 0.4 * sway) @ Rz(-2.0)
    off = np.array([IDLE_WEIGHT[0] + IDLE_SWAY * sway, 0.004, 0.0])
    dz = min(0.0, pelvis_height(Rp, off[:2], feet, IDLE_REACH))
    D["pelvis"] = Tt(off + np.array([0, 0, dz - 0.003 * (0.5 + 0.5 * br)])) @ Tr(HEADP["pelvis"], Rp)
    fk(D, "spine_01", Ry(-0.5 * IDLE_WEIGHT[1]) @ Rx(-0.3 * IDLE_BREATH_DEG * br))
    fk(D, "spine_02", Ry(-0.4 * IDLE_WEIGHT[1] - 0.3 * sway_c) @ Rx(-0.5 * IDLE_BREATH_DEG * br))
    fk(D, "spine_03", Rz(-2.5) @ Ry(-0.25 * sway_c) @ Rx(-IDLE_BREATH_DEG * br - 1.0))
    fk(D, "neck_01", Rx(0.5 * IDLE_HEAD[0]))
    fk(D, "head", Rz(IDLE_HEAD[1] * math.sin(TAU * t + 1.9)) @ Rx(0.5 * IDLE_HEAD[0] + 0.7 * math.sin(TAU * t + 0.4))
       @ Ry(-1.2 - 0.5 * sway_h))
    info.update(plant_legs(D, feet))
    Rl = Rx(-FORK_IDLE_LEAN[1] + FORK_IDLE_SWAY * br) @ Ry(FORK_IDLE_LEAN[0] + 0.4 * FORK_IDLE_SWAY * sway) @ Rz(ROLL["idle"])
    # the leaning shaft stands on the RIM of its butt ferrule: lift the butt by rim radius x sin(tilt) (no floor dip)
    tilt_ = math.acos(max(-1.0, min(1.0, float(Rl[2, 2]))))
    Dfk = TRT(IDLE_BUTT + np.array([0.0, 0.0, FORK_BUTT_R * math.sin(tilt_)]), Rl, _butt_rest)
    info["arm_R"], info["wrist_R"] = fork_arm(D, Dfk)
    free_arm(D, "L", IDLE_LARM[0] + 1.0 * br, IDLE_LARM[1] + 1.5 * sway_h, IDLE_LARM[2])
    hands(D, "idle")
    if HAS_FORK:
        info["fork_butt_z"] = float(xf(Dfk, _butt_rest)[2])
    return D, info


# ---- walk: in place; planted feet slide back at ground speed; a heel-first toes-up at the end of the swing; the fork
# carried upright in the right hand at chest height
WALK_X0 = float(SHIFT_V61[0] - SHIFT[0])                         # the approved walk's stance centre (s6 SHIFT_V61)
WALK_SPEED = 2 * WALK_STEP_A / (WALK_STANCE * WALK_N / K.FPS)
BALL_H = {s: float(HEADP["ball_" + s.lower()][2]) for s in "LR"}


def foot_walk(s, t):
    t0 = 0.0 if s == "L" else 0.5
    u = (t - t0) % 1.0
    sg = 1.0 if s == "L" else -1.0
    C0 = LEGS[s]["C0"]
    x = WALK_X0 + (C0[0] - WALK_X0) * WALK_FOOT_X    # (about the v6.1 frame's x = 0: = C0[0] x WALK_FOOT_X when HAS_FORK)
    Yc = C0[1]
    beta = WALK_STANCE
    if u < beta:
        p = u / beta
        y = Yc - WALK_STEP_A + 2 * WALK_STEP_A * p
        z = 0.0
        roll = WALK_TOEOFF[1] * ss5(beta - WALK_TOEOFF[0], beta, u) if u > beta - WALK_TOEOFF[0] else 0.0
        stance = True
    else:
        q = (u - beta) / (1 - beta)
        y = Yc + WALK_STEP_A - 2 * WALK_STEP_A * (q - math.sin(TAU * q) / TAU)
        z = WALK_LIFT * math.sin(math.pi * q) ** 1.2
        roll = WALK_TOEOFF[1] * (1.0 - ss5(0.0, 0.55, q)) - WALK_HEELSTRIKE * ss5(0.45, 0.85, q) * (1.0 - ss5(0.88, 1.0, q))
        stance = False
    yaw = Rz(-sg * 4.0)
    Rf = yaw @ Rx(roll)
    z += BALL_H[s] * (1.0 - math.cos(math.radians(roll))) if roll > 0 else 0.0
    toe = 0.0 if stance else (min(0.35 * roll, 0.3 * math.degrees(math.asin(min(1.0, max(z, 0.0) / TOE_REACH)))) if roll > 0 else roll)
    Rb = yaw @ Rx(toe)
    if not stance:
        # the swing-foot lift proof (vampwarrior v2's toe-dip fix, over the WHOLE boot here): pose the boot shell / sole /
        # straps rigidly (foot / ball blend as skinned) and lift the swing foot by whatever still reaches below z = 0
        lo_ = s.lower()
        C_ = np.array([x, y, z])
        pf = C_ + (TOE_PTS[s][0] - C0) @ Rf.T
        hb = C_ + Rf @ (HEADP["ball_" + lo_] - C0)
        pb = hb + (TOE_PTS[s][0] - HEADP["ball_" + lo_]) @ Rb.T
        zmin = float(((1.0 - TOE_PTS[s][1]) * pf[:, 2] + TOE_PTS[s][1] * pb[:, 2]).min())
        z += max(0.0, -zmin)
    return np.array([x, y, z]), Rf, Rb, stance


FORK_GRIP_REST = S_(FORK_GRIP0)


def pose_walk(t):
    D = {"root": np.eye(4)}
    info = {}
    c1 = math.cos(TAU * t)
    s1 = math.sin(TAU * (t - 0.05))
    feet, st = {}, {}
    for s in "LR":
        C, Rf, Rb, stc = foot_walk(s, t)
        feet[s] = (C, Rf, Rb); st[s] = stc
    yaw = -WALK_PELVIS[0] * c1
    roll = -WALK_PELVIS[1] * s1
    Rp = Rz(yaw) @ Ry(roll)
    off = np.array([WALK_PELVIS[2] * s1, 0.0, 0.0])
    dz = pelvis_height(Rp, off[:2], feet, WALK_REACH)
    bounce = -WALK_BOUNCE * (0.5 + 0.5 * math.cos(2 * TAU * (t - 0.04)))       # the dip just after each contact
    D["pelvis"] = Tt(off + np.array([0, 0, dz + bounce])) @ Tr(HEADP["pelvis"], Rp)
    tc, th = t - WALK_OVERLAP[0] / WALK_N, t - WALK_OVERLAP[1] / WALK_N
    c1c, c1h = math.cos(TAU * tc), math.cos(TAU * th)
    s1h = math.sin(TAU * (th - 0.05))
    fk(D, "spine_01", Rz(-0.35 * yaw) @ Ry(-0.4 * roll) @ Rx(-0.4 * WALK_CHEST[1]))
    fk(D, "spine_02", Rz(-0.45 * yaw + 0.5 * WALK_CHEST[0] * c1c) @ Ry(-0.4 * roll) @ Rx(-0.4 * WALK_CHEST[1]))
    fk(D, "spine_03", Rz(-0.3 * yaw + 0.5 * WALK_CHEST[0] * c1c) @ Ry(-0.2 * roll) @
       Rx(-0.2 * WALK_CHEST[1] + WALK_NOD[1] * math.sin(2 * TAU * tc)))
    fk(D, "neck_01", Rz(-0.5 * WALK_CHEST[0] * c1h) @ Rx(0.4 * WALK_CHEST[1]))
    fk(D, "head", Rz(-0.4 * WALK_CHEST[0] * c1h) @ Rx(0.6 * WALK_CHEST[1] - 1.0 - WALK_NOD[0] * math.sin(2 * TAU * th))
       @ Ry(-0.5 * WALK_PELVIS[1] * s1h))
    info.update(plant_legs(D, feet))
    # the fork: the grip rides the chest (a small bob + pendulum swing with the stride), shaft upright, top forward / out
    fw = FORK_WALK
    fk(D, "clavicle_r")
    Hs = xf(D["clavicle_r"], HEADP["upperarm_r"])
    Rch = D["spine_03"][:3, :3]
    grip = Hs + Rch @ np.array(fw["grip"]) + np.array([0.0, 0.0, fw["bob"] * math.sin(2 * TAU * (t - 0.12))])
    yaw_c = math.degrees(math.atan2(Rch[1, 0], Rch[0, 0]))
    Rfk = Rz(0.6 * yaw_c) @ Rx(fw["tilt_fwd"] + fw["swing"] * c1) @ Ry(-fw["tilt_out"]) @ Rz(ROLL["walk"])
    Dfk = TRT(grip, Rfk, FORK_GRIP_REST)
    info["arm_R"], info["wrist_R"] = fork_arm(D, Dfk)
    free_arm(D, "L", WALK_LARM[0], WALK_LARM[1] * c1, WALK_LARM[2], lag_bend=7.0 * math.cos(TAU * t - 0.6) + 7.0)
    hands(D, "walk")
    info["stance"] = st
    info["pelvis_dz"] = dz
    if HAS_FORK:
        info["fork_butt_z"] = float(xf(Dfk, _butt_rest)[2])
    return D, info


# ---- run (review-log 2026-10-03 "young hero sprint"): in place; each foot's BALL JOINT is the planted pivot -- in stance
# it slides back at exactly the ground speed at its rest height while the foot rolls about it (forefoot touchdown, heel
# down, push-off) and the toes stay flat (zero slip on the toe sole); the swing is a C1 Hermite path through the heel
# recovery, the knee drive and the reach, landing pawing back at ground speed; a flight phase between the steps; the
# pelvis rides a bounce lowest at mid-stance, its mean set so the stance leg never over-reaches; the fork charged forward
RUN_V_U = (RUN_STEP[0] + RUN_STEP[1]) / RUN_STANCE              # ball travel per unit cycle phase in stance (m)
RUN_SPEED = RUN_V_U * K.FPS / RUN_N                              # the ground speed (m/s)
RUN_CX = float(HEADP["pelvis"][0])
RUN_T_HI = RUN_STANCE + (0.5 - RUN_STANCE) / 2                   # mid-flight (pelvis high point), and + 0.5
RUN_PH = {"arm_L": RUN_STANCE + RUN_SWING[1][0] * (1 - RUN_STANCE) - 0.5, "pelvis": -0.08}   # the left arm forward-most
                                                                 #   when the RIGHT knee drives (the left's knee-drive key
                                                                 #   minus half a cycle); pelvis yaw: the left hip forward
                                                                 #   just before its touchdown


def _herm(keys, q):
    """C1 cubic Hermite through keys (q_i, value, tangent or None = Catmull-Rom) at q."""
    i = max(j for j in range(len(keys) - 1) if keys[j][0] <= q)
    i = min(i, len(keys) - 2)

    def tan(j):
        if keys[j][2] is not None:
            return np.asarray(keys[j][2], float)
        return (np.asarray(keys[j + 1][1], float) - np.asarray(keys[j - 1][1], float)) / (keys[j + 1][0] - keys[j - 1][0])
    q0, v0, _ = keys[i]; q1, v1, _ = keys[i + 1]
    h = q1 - q0
    s = (q - q0) / h
    h00, h10, h01, h11 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s, -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
    return h00 * np.asarray(v0, float) + h10 * h * tan(i) + h01 * np.asarray(v1, float) + h11 * h * tan(i + 1)


_vq = RUN_V_U * (1 - RUN_STANCE)                                 # stance ball speed in swing-q units (continuity at both ends)
def run_keys(z_drive=None, dy_drive=None):
    """the swing keys; z_drive / dy_drive (m) replace the KNEE-DRIVE key's ball height / fore-aft (RUN_SWING[1]) when given."""
    sw_ = [tuple(k_) for k_ in RUN_SWING]
    if z_drive is not None:
        sw_[1] = (sw_[1][0], sw_[1][1] if dy_drive is None else float(dy_drive), float(z_drive), sw_[1][3])
    return ([(0.0, (RUN_STEP[1], 0.0, RUN_ROLL[2]), (_vq, 0.0, 0.0))] +
            [(q_, (dy_, z_, r_), None) for q_, dy_, z_, r_ in sw_] +
            [(1.0, (-RUN_STEP[0], 0.0, RUN_ROLL[0]), (_vq, 0.0, 0.0))])


RUN_KEYS = run_keys()


def foot_run(s, t):
    t0 = 0.0 if s == "L" else 0.5
    u = (t - t0) % 1.0
    sg = 1.0 if s == "L" else -1.0
    lo_ = s.lower()
    C0 = LEGS[s]["C0"]
    x = RUN_CX + (C0[0] - RUN_CX) * RUN_FOOT_X
    beta = RUN_STANCE
    yaw = Rz(-sg * 3.0)
    if u < beta:
        p = u / beta
        dy = -RUN_STEP[0] + (RUN_STEP[0] + RUN_STEP[1]) * p
        z = 0.0
        roll = RUN_ROLL[0] + (RUN_ROLL[1] - RUN_ROLL[0]) * ss5(0.0, 0.4, p) + (RUN_ROLL[2] - RUN_ROLL[1]) * ss5(0.45, 1.0, p)
        bend = roll                                              # the toes stay flat on the floor
        stance = True
    else:
        q = (u - beta) / (1 - beta)
        dy, z, roll = (float(v_) for v_ in _herm(RUN_KEYS, q))
        bend = RUN_ROLL[2] * (1.0 - ss5(0.0, 0.3, q)) + RUN_ROLL[0] * ss5(0.7, 1.0, q)
        stance = False
    Rf = yaw @ Rx(roll)
    Rb = yaw @ Rx(roll - bend)
    hb = np.array([0.0, 0.0, BALL_H[s]])
    Pb = np.array([x, C0[1] + dy, BALL_H[s] + z])                # the ball joint (the stance pivot)
    if not stance:
        # the swing-foot lift proof (as the walk): the boot posed rigidly (foot / ball blend as skinned), lifted by whatever
        # still reaches below z = 0
        C_ = Pb - Rf @ hb
        pf = C_ + (TOE_PTS[s][0] - C0) @ Rf.T
        pb = Pb + (TOE_PTS[s][0] - HEADP["ball_" + lo_]) @ Rb.T
        zmin = float(((1.0 - TOE_PTS[s][1]) * pf[:, 2] + TOE_PTS[s][1] * pb[:, 2]).min())
        Pb[2] += max(0.0, -zmin)
    return Pb - Rf @ hb, Rf, Rb, stance


def plant_legs_run(D, feet):
    """plant_legs with the knee pole turned by the foot's YAW only (the swing foot rolls past 90 deg at the heel recovery:
    a pole rolled with it would point the knee down)."""
    info = {}
    for s in "LR":
        lo_ = s.lower()
        C, Rf, Rb = feet[s]
        A = C + Rf @ (HEADP["foot_" + lo_] - LEGS[s]["C0"])
        H = xf(D["pelvis"], HEADP["thigh_" + lo_])
        sg = 1.0 if s == "L" else -1.0
        pole = Rz(-sg * 3.0) @ LEGS[s]["knee_dir"]
        D["thigh_" + lo_], D["calf_" + lo_], At, err = two_bone("thigh_" + lo_, "calf_" + lo_, "foot_" + lo_, H, A, pole)
        D["foot_" + lo_] = TRT(At, Rf, HEADP["foot_" + lo_])
        D["ball_" + lo_] = TRT(xf(D["foot_" + lo_], HEADP["ball_" + lo_]), Rb, HEADP["ball_" + lo_])
        info[s] = err
    return info


def run_pelvis_R(t):
    yaw = -RUN_PELVIS[0] * math.cos(TAU * (t - RUN_PH["pelvis"]))
    drop = -RUN_PELVIS[1] * math.cos(TAU * (t - RUN_STANCE / 2))
    tilt = RUN_LEAN[0] + 0.5 * RUN_LEAN[2] * math.cos(2 * TAU * (t - RUN_STANCE * 0.8))
    off = np.array([RUN_PELVIS[2] * math.cos(TAU * (t - RUN_STANCE / 2)), 0.0, 0.0])
    return Rz(yaw) @ Ry(drop) @ Rx(tilt), off, yaw, drop


def run_bounce(t):
    return RUN_BOUNCE * math.cos(2 * TAU * (t - RUN_T_HI))


def run_room(t):
    """how far the pelvis may rise (from rest) with every STANCE foot within RUN_REACH; None in flight."""
    Rp, off, _, _ = run_pelvis_R(t)
    best = None
    for s in "LR":
        C, Rf, _, stc = foot_run(s, t)
        if not stc:
            continue
        lo_ = s.lower()
        A = C + Rf @ (HEADP["foot_" + lo_] - LEGS[s]["C0"])
        h0 = HEADP["pelvis"] + Rp @ (HEADP["thigh_" + lo_] - HEADP["pelvis"]) + off
        r = RUN_REACH * LEGS[s]["len"]
        dxy2 = float((A[0] - h0[0]) ** 2 + (A[1] - h0[1]) ** 2)
        v = A[2] + math.sqrt(max(r * r - dxy2, 1e-9)) - h0[2]
        best = v if best is None else min(best, v)
    return best


# the pelvis mean height: the highest that keeps the stance leg within reach over the WHOLE stance (sampled 20x the key
# rate, the keyed frames included)
_rz = [(run_room(i / (20 * RUN_N)), i / (20 * RUN_N)) for i in range(20 * RUN_N)]
RUN_Z0 = min(r_ - run_bounce(t_) for r_, t_ in _rz if r_ is not None)


def run_thigh_below(t, s):
    """the leg's thigh (hip joint -> knee, world) angle below horizontal at cycle phase t (deg; 0 = horizontal)."""
    Rp, off, _, _ = run_pelvis_R(t)
    D = {"root": np.eye(4), "pelvis": Tt(off + np.array([0, 0, RUN_Z0 + run_bounce(t)])) @ Tr(HEADP["pelvis"], Rp)}
    plant_legs_run(D, {s_: foot_run(s_, t)[:3] for s_ in "LR"})
    lo_ = s.lower()
    v = xf(D["calf_" + lo_], HEADP["calf_" + lo_]) - xf(D["thigh_" + lo_], HEADP["thigh_" + lo_])
    return math.degrees(math.atan2(-float(v[2]), math.hypot(float(v[0]), float(v[1]))))


def run_thigh_peak(dense=1):
    """the knee drive's peak = the smallest thigh angle below horizontal over the cycle (keyed frames at dense = 1)."""
    return min(run_thigh_below(i / (dense * RUN_N), s) for s in "LR" for i in range(dense * RUN_N))


# (2026-10-04, review-log "Wren in-game verdicts" (2)) the knee-drive key's ball height solved so the keyed-frame thigh
# peak sits RUN_KNEE_DRIVE_DEG below horizontal (bisection; the peak falls monotonically as the key rises)
RUN_DRIVE_V1 = (-0.20, 0.42)            # the v1 (2026-10-03) knee-drive key: ball dy / height (m) -- measured for the report
RUN_KEYS = run_keys(RUN_DRIVE_V1[1], RUN_DRIVE_V1[0])
RUN_KNEE_V1 = {"keyed_deg": round(run_thigh_peak(), 2), "dense_deg": round(run_thigh_peak(20), 2), "dy_z_drive_m": RUN_DRIVE_V1}
RUN_KEYS = run_keys()
RUN_KNEE_SOLVE = None
if RUN_KNEE_DRIVE_DEG is not None:
    _zlo, _zhi = 0.0, max(float(RUN_SWING[1][2]), 0.6)
    for _ in range(40):
        _zm = 0.5 * (_zlo + _zhi)
        RUN_KEYS = run_keys(_zm)
        if run_thigh_peak() > RUN_KNEE_DRIVE_DEG:
            _zlo = _zm                                           # too low a knee: raise the key
        else:
            _zhi = _zm
    RUN_KEYS = run_keys(0.5 * (_zlo + _zhi))
    RUN_KNEE_SOLVE = {"dy_drive_m": RUN_SWING[1][1], "z_drive_m": round(0.5 * (_zlo + _zhi), 5), "keyed_deg": round(run_thigh_peak(), 2),
                      "dense_deg": round(run_thigh_peak(20), 2)}
print("RUNKNEE", json.dumps({"target_deg_below_horizontal": RUN_KNEE_DRIVE_DEG, "v1": RUN_KNEE_V1, "solved": RUN_KNEE_SOLVE}))


def pose_run(t):
    D = {"root": np.eye(4)}
    info = {}
    feet, st = {}, {}
    for s in "LR":
        C, Rf, Rb, stc = foot_run(s, t)
        feet[s] = (C, Rf, Rb); st[s] = stc
    Rp, off, yaw, drop = run_pelvis_R(t)
    dz = RUN_Z0 + run_bounce(t)
    D["pelvis"] = Tt(off + np.array([0, 0, dz])) @ Tr(HEADP["pelvis"], Rp)
    tc = t - RUN_OVERLAP / RUN_N
    chest = RUN_CHEST * math.cos(TAU * (tc - RUN_PH["arm_L"] - 0.5))     # + = the left shoulder back (right arm forward)
    lean = RUN_LEAN[1] + 0.5 * RUN_LEAN[2] * math.cos(2 * TAU * (tc - RUN_STANCE * 0.8))
    trunk = RUN_LEAN[0] + lean
    fk(D, "spine_01", Rz(-0.5 * yaw) @ Ry(-0.5 * drop) @ Rx(0.30 * lean))
    fk(D, "spine_02", Rz(0.45 * chest) @ Ry(-0.3 * drop) @ Rx(0.35 * lean))
    fk(D, "spine_03", Rz(0.55 * chest) @ Ry(-0.2 * drop) @ Rx(0.35 * lean))
    # the head STABILISED (eyes locked ahead, the hero's focus): its world rotation = a fixed forward pitch + a small
    # share of the chest's yaw, whatever the trunk does -- the neck takes 40 % of the correction, the head the rest (the
    # fringe / side locks then lag only the head's small residual motion, not the whole trunk's rock)
    Rs3 = D["spine_03"][:3, :3]
    W_ = Rz(RUN_HEAD[1] * math.degrees(math.atan2(Rs3[1, 0], Rs3[0, 0]))) @ Rx(RUN_HEAD[0])
    Rtot = Rs3.T @ W_
    _ang = math.acos(float(np.clip((np.trace(Rtot) - 1) / 2, -1, 1)))
    _ax = np.array([Rtot[2, 1] - Rtot[1, 2], Rtot[0, 2] - Rtot[2, 0], Rtot[1, 0] - Rtot[0, 1]])
    Rn = K._rot(_ax / max(float(np.linalg.norm(_ax)), 1e-12), 0.4 * _ang) if _ang > 1e-9 else np.eye(3)
    fk(D, "neck_01", Rn)
    fk(D, "head", Rn.T @ Rtot)
    info.update(plant_legs_run(D, feet))
    # the fork: charged forward in the right hand, the hand pumping with the right arm's half of the swing (forward-most
    # half a cycle after the left arm's), the shaft rocking with it; the grip relation (hand -> fork) is the house one
    fr = FORK_RUN
    w_r = math.cos(TAU * (t - RUN_PH["arm_L"] - 0.5))
    fk(D, "clavicle_r")
    Hs = xf(D["clavicle_r"], HEADP["upperarm_r"])
    Rch = D["spine_03"][:3, :3]
    grip = Hs + Rch @ (np.array(fr["grip"]) + np.array([0.0, -fr["pump"][0] * w_r, fr["pump"][1] * w_r]))
    yaw_c = math.degrees(math.atan2(Rch[1, 0], Rch[0, 0]))
    Rfk = Rz(0.6 * yaw_c) @ Rx(fr["tilt_fwd"] + fr["swing"] * w_r) @ Ry(-fr["tilt_out"]) @ Rz(ROLL["run"])
    Dfk = TRT(grip, Rfk, FORK_GRIP_REST)
    info["arm_R"], info["wrist_R"] = fork_arm(D, Dfk)
    # the free (left) arm: a full pump, forward-most when the right knee drives; the elbow closes in front, opens behind,
    # the forearm trailing the upper arm by a frame (follow-through)
    w_l = math.cos(TAU * (t - RUN_PH["arm_L"]))
    w_lb = math.cos(TAU * (t - RUN_PH["arm_L"] - 1.0 / RUN_N))
    la = RUN_LARM
    free_arm(D, "L", la[0], la[1] - la[2] * w_l, la[3], lag_bend=la[4] * w_lb)
    hands(D, "run")
    info["stance"] = st
    info["pelvis_dz"] = dz
    if HAS_FORK:
        info["fork_butt_z"] = float(xf(Dfk, _butt_rest)[2])
    return D, info


def wrist_bend(D):
    Rw = D["lowerarm_r"][:3, :3].T @ D["hand_r"][:3, :3]
    return math.degrees(math.acos(float(np.clip((np.trace(Rw) - 1) / 2, -1, 1))))


ROLL_SEARCH = {}
for cn_, fn_ in (("idle", pose_idle), ("walk", pose_walk), ("run", pose_run)):
    best_ = None
    for r_ in range(0, 360, 10):
        ROLL[cn_] = float(r_)
        D_, info_ = fn_(0.0)
        b_ = wrist_bend(D_) + 1000.0 * info_["arm_R"]
        if best_ is None or b_ < best_[0] - 1e-9:
            best_ = (b_, r_)
    ROLL[cn_] = float(best_[1])
    ROLL_SEARCH[cn_] = {"roll_deg": best_[1], "wrist_bend_deg_at_t0": round(best_[0], 2)}
# the tines' roll INSIDE the hand (part of the recorded grip transform, the same in every clip): the fork turns about its
# own shaft relative to the hand -- the wrist is untouched -- so that in the idle (t = 0) the tine plane faces the front
# camera (edge-on tines read as one spike from the front, the sheet shows them face-on)
def tine_face(D_):
    Rf_ = D_["pitchfork"][:3, :3]
    return abs(float(Rf_[0, 0])) / max(math.hypot(float(Rf_[0, 0]), float(Rf_[1, 0])), 1e-9)


if HAS_FORK:
    _best_tr = None
    for tr_ in range(0, 180, 2):
        set_tine_roll(float(tr_))
        fc_ = tine_face(pose_idle(0.0)[0])
        if _best_tr is None or fc_ > _best_tr[1] + 1e-9:
            _best_tr = (tr_, fc_)
    set_tine_roll(float(_best_tr[0]))
    ROLL_SEARCH["tine_roll_in_hand"] = {"deg": _best_tr[0], "idle_t0_tine_plane_to_front_deg":
                                        round(math.degrees(math.acos(min(1.0, _best_tr[1]))), 1),
                                        "walk_t0_tine_plane_to_front_deg": round(math.degrees(math.acos(min(1.0, tine_face(pose_walk(0.0)[0])))), 1)}
print("ROLL", json.dumps(ROLL_SEARCH))
CLIP_N = {"idle": IDLE_N, "walk": WALK_N, "run": RUN_N}        # (insertion order = key / export order: idle, walk untouched)
POSE = {"idle": pose_idle, "walk": pose_walk, "run": pose_run}
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
ORDER = ["root"] + DEFORM


def basis(D, n):
    return np.linalg.inv(REST4[n]) @ np.linalg.inv(D[PARENT[n]]) @ D[n] @ REST4[n]


PARENT["root"] = None


def rotvec(R):
    q = Matrix(np.asarray(R).tolist()).to_quaternion()
    if q.w < 0:
        q.negate()
    ax_, ang_ = q.to_axis_angle()
    return np.array(ax_) * ang_


def exp_rv(v):
    a = float(np.linalg.norm(v))
    return np.eye(3) if a < 1e-12 else K._rot(v / a, a)


FT_CHAINS = sorted(CHAIN_PTS)
INTO_SIGN = {"hair_fringe": 1.0, "hair_side": 1.0, "hair_tail": -1.0, "tie": 1.0, "cape": -1.0}


def ft_kind(ch):
    return "cape" if ch.startswith("cape") else ch.split(".")[0]


def ft_nb(ch):
    return CAPE_BONES if ch.startswith("cape") else (2 if ch == "tie" else HAIR_BONES)


def secondary(cn, N):
    """per chain bone k: world rotation y_k = H_k * y_(k-1), y_(-1) = the driver's rotation + the pendulum drag of the
    chain root's acceleration, H = a damped spring (FT hz / zeta) applied as the PERIODIC steady state in the frequency
    domain (the loop stays exact). The keyed lag is y_k - (the chain parent's own rotation), soft-limited into the body."""
    Ds = [POSE[cn](f / N)[0] for f in range(N)]
    w = 2 * math.pi * np.fft.fftfreq(N, d=1.0 / K.FPS)
    out, lag = {}, {}
    lim = math.radians(FT_INTO_BODY_DEG)
    for ch in FT_CHAINS:
        P_ = FT[ft_kind(ch)]
        par = CHAIN_PTS[ch][2]
        rd = np.array([rotvec(D[P_["driver"]][:3, :3] @ Ds[0][P_["driver"]][:3, :3].T) for D in Ds])
        rp = np.array([rotvec(D[par][:3, :3] @ Ds[0][par][:3, :3].T) for D in Ds])
        anc = np.array([xf(D[par], HEADP[ch + ".0"]) for D in Ds])
        acc = np.real(np.fft.ifft(np.fft.fft(anc, axis=0) * (-(w ** 2))[:, None], axis=0))
        u = rd + P_["drag"] * (RUN_FT_DRAG if cn == "run" else 1.0) * np.stack([-acc[:, 1], acc[:, 0], np.zeros(N)], 1) / 9.81
        w0 = 2 * math.pi * P_["hz"]
        H = w0 ** 2 / (w0 ** 2 - w ** 2 + 2j * P_["zeta"] * w0 * w)
        y, rows = u, []
        sg_ = INTO_SIGN[ft_kind(ch)]
        for k in range(ft_nb(ch)):
            y = np.real(np.fft.ifft(np.fft.fft(y, axis=0) * H[:, None], axis=0))
            d = P_["gain"] * (y - rp) * np.asarray(P_.get("axes", (1.0, 1.0, 1.0)))[None]
            x_ = d[:, 0] * sg_
            d[:, 0] = sg_ * np.where(x_ > 0, lim * np.tanh(x_ / lim), x_)
            rows.append(d)
        out[ch] = [[exp_rv(rows[k][f]) for k in range(len(rows))] for f in range(N)]
        uu = u - u.mean(0)
        ax_ = int(np.argmax(uu.var(0)))
        tip = y[:, ax_] - y[:, ax_].mean()
        cc = [float(np.dot(np.roll(uu[:, ax_], s_), tip)) for s_ in range(N)]
        L_ = int(np.argmax(cc))
        L_ = L_ - N if L_ > N // 2 else L_
        lag[ch] = {"tip_lag_frames": L_, "axis": "xyz"[ax_], "driver_ptp_deg": round(math.degrees(float(np.ptp(uu[:, ax_]))), 2),
                   "tip_follow_through_ptp_deg": round(math.degrees(float(np.ptp(rows[-1][:, ax_]))), 2)}
    return out, lag


def drape_local(cn, ch, k, t, lift=None):
    dk = FT_DRIFT_K
    kind = ft_kind(ch)
    if cn == "run":
        # the sprint: the cloak streams back (relative to the leaning chest; the front-wrapping edges less), the chains
        # over the left arm follow its pump (lift["arm_L"], deg past rest, + = forward: a -X turn swings the drape forward),
        # the nape tail trails, the fringe lifts off the brow, the flutter rides the steps (2nd harmonic); the sash ties
        # (hanging in FRONT of the left hip) ride the left thigh's forward swing (lift["tie"], deg)
        if kind == "cape":
            c_ = int(ch[-1])
            arm_ = RUN_CAPE_ARM[c_] * lift["arm_L"] * (RUN_CAPE_ARM_FWD if lift["arm_L"] > 0 else 1.0) if k == 0 else 0.0
            return Rx(RUN_CAPE[0] * RUN_CAPE_SPREAD[c_] * (0.55 + 0.45 * k / (CAPE_BONES - 1)) - arm_ +
                      dk * K.vine_wave(t, k, CAPE_BONES, RUN_CAPE[1], RUN_CAPE[2], 0.9, 2, 0.8 * c_)) \
                @ Ry(0.5 * dk * K.vine_wave(t, k, CAPE_BONES, RUN_CAPE[1], RUN_CAPE[2], 0.9, 2, 1.7 + c_))
        nb = ft_nb(ch)
        wy = Ry(0.4 * dk * K.vine_wave(t, k, nb, RUN_HAIR[1], RUN_HAIR[2], 0.8, 2, 1.3 + len(ch)))
        if kind == "tie":
            return Rx(-lift["tie"] * (1.0 if k == 0 else 0.25)) @ wy
        a = RUN_HAIR[0] * (k + 1) / nb + 0.5 * dk * K.vine_wave(t, k, nb, RUN_HAIR[1], RUN_HAIR[2], 0.8, 2, 0.4 + len(ch))
        if kind == "hair_fringe":
            a = -RUN_HAIR[3] if k == 0 else 0.0
        return Rx(a if kind in ("hair_tail", "hair_fringe") else 0.0) @ wy
    if cn == "idle":
        if kind != "cape":
            nb = ft_nb(ch)
            return Rx(0.6 * dk * K.vine_wave(t, k, nb, IDLE_HAIR_DEG[0], IDLE_HAIR_DEG[1], 0.7, 1, 0.9 * len(ch))) \
                @ Ry(0.6 * dk * K.vine_wave(t, k, nb, IDLE_HAIR_DEG[0], IDLE_HAIR_DEG[1], 0.7, 1, 2.0 + len(ch)))
        return Rx(0.6 + dk * K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], 0.8, 1, 1.1 * int(ch[-1]))) \
            @ Ry(0.5 * dk * K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], 0.8, 1, 2.3 + int(ch[-1])))
    # (a world +X rotation swings a chain hanging BEHIND the body away from it, one hanging in FRONT into it)
    if kind != "cape":
        nb = ft_nb(ch)
        a = WALK_HAIR[0] * (k + 1) / nb + 0.5 * dk * K.vine_wave(t, k, nb, WALK_HAIR[1], WALK_HAIR[2], 0.8, 2, 0.4 + len(ch))
        return Rx(a if kind == "hair_tail" else 0.0) @ Ry(0.4 * dk * K.vine_wave(t, k, nb, WALK_HAIR[1], WALK_HAIR[2], 0.8, 1, 1.3 + len(ch)))
    return Rx(WALK_CAPE[0] * (0.55 + 0.45 * k / (CAPE_BONES - 1)) +
              dk * K.vine_wave(t, k, CAPE_BONES, WALK_CAPE[1], WALK_CAPE[2], 0.9, 2, 0.8 * int(ch[-1]))) \
        @ Ry(0.5 * dk * K.vine_wave(t, k, CAPE_BONES, WALK_CAPE[1], WALK_CAPE[2], 0.9, 1, 1.7 + int(ch[-1])))


def fwd_deg(D, n, rel=None):
    """bone n's forward swing past its rest direction (deg, sagittal angle from straight down: + = the tail forward),
    in the world or (rel) in that bone's posed frame."""
    d0 = TAILP[n] - HEADP[n]
    d = D[n][:3, :3] @ d0
    if rel is not None:
        d = D[rel][:3, :3].T @ d
    return math.degrees(math.atan2(-float(d[1]), -float(d[2])) - math.atan2(-float(d0[1]), -float(d0[2])))


def apply_chains(D, cn, t, f):
    lift = ({"tie": RUN_TIE_LIFT * max(0.0, fwd_deg(D, "thigh_l", "pelvis")), "arm_L": fwd_deg(D, "upperarm_l", "spine_03")}
            if cn == "run" else None)
    for ch in FT_CHAINS:
        Lp = np.eye(3)
        for k in range(ft_nb(ch)):
            n = "%s.%d" % (ch, k)
            Rpv = D[PARENT[n]][:3, :3]
            Lk = SEC[cn][ch][f][k]
            fk(D, n, Rpv.T @ Lk @ Lp.T @ Rpv @ drape_local(cn, ch, k, t, lift))
            Lp = Lk


t_ = time.time()
SEC, FT_LAG = {}, {}
for cn, N in CLIP_N.items():
    SEC[cn], FT_LAG[cn] = secondary(cn, N)
rep["follow_through"] = {"params": FT, "into_body_limit_deg": FT_INTO_BODY_DEG, "drift_k": FT_DRIFT_K, "per_clip": FT_LAG,
                         "seconds": round(time.time() - t_, 1)}
print("FOLLOW", json.dumps(FT_LAG))
t_ = time.time()
ACTS, key_rows, ik_worst, butt, KEYQ = {}, [], {}, {}, {}
for cn, N in CLIP_N.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    for f in range(N + 1):
        D, info = POSE[cn]((f % N) / N)
        apply_chains(D, cn, (f % N) / N, f % N)
        for k_, v_ in info.items():
            if isinstance(v_, float) and k_ not in ("fork_butt_z", "pelvis_dz"):
                ik_worst[(cn, k_)] = max(ik_worst.get((cn, k_), 0.0), abs(v_))
        if HAS_FORK:
            butt.setdefault(cn, []).append(info["fork_butt_z"])
        KEYQ.setdefault(cn, []).append([])
        for n in ORDER:
            Bm = np.eye(4) if n == "root" else basis(D, n)
            q = Matrix(Bm[:3, :3].tolist()).to_quaternion(); q.normalize()
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pb = rig.pose.bones[n]
            pb.location = Vector(Bm[:3, 3]); pb.rotation_quaternion = q
            pb.keyframe_insert("location", frame=f + 1)
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion))
            KEYQ[cn][-1].append(list(pb.rotation_quaternion) + list(pb.location))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    ACTS[cn] = act
DIG["keys"] = sha(np.array(key_rows))
print("KEYED", round(time.time() - t_, 1), json.dumps({"%s.%s" % k: round(v, 5) for k, v in ik_worst.items()}))


def eval_coords(ob):
    dg_ = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg_)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


from mathutils.kdtree import KDTree  # noqa: E402

RM = OBJ["main"]["RANGE"]


def rng(name, R_=RM):
    return np.arange(*R_[name])


def rngs(prefix):
    return np.concatenate([rng(n) for n in RM if n.split(".")[0] == prefix])


domM = np.array([DEFORM[j] for j in np.argmax(WM, 1)], dtype=object)
body_ids = rng("body")
LARM_IDS = body_ids[np.array([d.endswith("_l") and d.split("_")[0] in ("upperarm", "lowerarm", "hand", "index", "middle", "ring",
                                                                       "pinky", "thumb") for d in domM[body_ids]])]
RARM_IDS = body_ids[np.array([d.endswith("_r") and d.split("_")[0] in ("upperarm", "lowerarm", "hand", "index", "middle", "ring",
                                                                       "pinky", "thumb") for d in domM[body_ids]])]
LEG_IDS = np.concatenate([body_ids[np.isin(domM[body_ids], ["thigh_l", "thigh_r", "calf_l", "calf_r"])], rngs("puff"),
                          rngs("bootshaft"), rngs("bootcuff")])
TORSO_IDS = body_ids[np.isin(domM[body_ids], ["pelvis", "spine_01", "spine_02", "spine_03"])]
_clf = OBJ["main"]["FRANGE"]["cloak"]
CLOAK_TRIS = [list(f) for f in OBJ["main"]["F"][_clf[0]:_clf[1]]]
CLOAK_TRIS = [[f[0], f[k], f[k + 1]] for f in CLOAK_TRIS for k in range(1, len(f) - 1)]
_bf = OBJ["main"]["FRANGE"]["body"]
_rhand = {"hand_r"} | {"%s_%02d_r" % (f_, k_) for f_ in ("index", "middle", "ring", "pinky", "thumb") for k_ in (1, 2, 3)}
if HAS_FORK:
    BODY_TRIS = [list(f) for f in OBJ["main"]["F"][_bf[0]:_bf[1]] if not all(domM[i] in _rhand for i in f)]   # the gripping hand excluded
    BODY_TRIS = [[f[0], f[k], f[k + 1]] for f in BODY_TRIS for k in range(1, len(f) - 1)]
    # the fork's shaft axis samples (rest): butt -> tine tops along +Z
    FORK_AXIS = np.array([_butt_rest + np.array([0.0, 0.0, z]) for z in np.linspace(0.02, FORK["yoke_at"] * FORK["len"], 24)])
V0m = OBJ["main"]["V"]
CONTACT = {}
for s in "LR":
    so = rng("sole." + s)
    bot = so[np.abs(V0m[so, 2]) < 1e-6]
    CONTACT[s] = int(bot[np.argmin(np.linalg.norm(V0m[bot, :2] - LEGS[s]["C0"][:2], axis=1))])
    CONTACT["heel" + s] = bot[np.argsort(-((V0m[bot] - LEGS[s]["C0"]) @ -BOOT_RING[s]["fd"]))[:2]]
    # (run) the toe-sole contact: the sole-bottom vertex most weighted to the toe (ball) bone -- the run's stance pins the
    # ball joint and keeps the toe segment flat, so this vertex is the run's zero-slip witness
    CONTACT["toe" + s] = int(bot[np.argmax(WM[bot, J["ball_" + s.lower()]])])
SOLE_IDS = {s: rng("sole." + s) for s in "LR"}
BOOT_IDS = {s: np.concatenate([rng(n) for n in RM if n.split(".")[0] in BOOT_PARTS and n.split(".")[1].startswith(s)])
            for s in "LR"}
# (every clip) the fork's shaft vs EVERY main-mesh vertex but the gripping hand + its forearm (the clearance number for
# body + outfit + cloak + boots + hair together)
if HAS_FORK:
    _rfa = _rhand | {"lowerarm_r"}
    FORK_FREE_IDS = np.nonzero(np.array([d not in _rfa for d in domM]))[0]


def group_edges(ids, extra_parts=()):
    """unique mesh edges of the main object's faces whose vertices all belong to the group (ids) or to the extra parts."""
    m_ = np.zeros(len(OBJ["main"]["V"]), bool); m_[ids] = True
    for n in extra_parts:
        m_[rng(n)] = True
    E = set()
    for f in OBJ["main"]["F"]:
        if all(m_[i] for i in f):
            for k in range(len(f)):
                a_, b_ = f[k], f[(k + 1) % len(f)]
                E.add((min(a_, b_), max(a_, b_)))
    return np.array(sorted(E))


def crossings(bvh_c, C, E):
    """poke-through proof: how many of the group's mesh edges cross the cloak surface (an edge crossing = skin through
    the cloak); an arm leaving from under the cloak's edge crosses nothing."""
    n = 0
    for a_, b_ in E:
        d = C[b_] - C[a_]
        L = float(np.linalg.norm(d))
        if L > 1e-7 and bvh_c.ray_cast(Vector(C[a_]), Vector(d / L), L)[0] is not None:
            n += 1
    return n


# (the cowl, hood and stitches are the cloak's own parts -- the cowl IS its bunched top edge, the stitches sit on it --
# so they are not in the poke-through groups)
E_GROUPS = {"left_arm": group_edges(LARM_IDS, [n for n in RM if n.split(".")[0] in ("bracer", "bracerstrap", "sleeveroll")
                                               and (n.endswith("L") or "." not in n or n.startswith("bracer"))]),
            "right_arm": group_edges(RARM_IDS, ["sleeveroll.R"]),
            "legs": group_edges(LEG_IDS),
            "torso_outfit": group_edges(TORSO_IDS, [n for n in RM if n.split(".")[0] in ("skirt", "sash", "pouch", "pouchflap", "knot")])}


# v3 HAIR-INTO-HEAD gate: the chain-driven clump vertices (fringe / side / tail, follow-through weight > 0.05) inside the
# posed head: the reference surface is the head / neck skin + the hair cap (the scalp skin under the cap is removed, so
# skin alone has a hole there and its nearest-point sign flips at the hole's rim). Counted: > 1 mm inside the skin, or
# > 2 mm under the cap's surface (the cap is 2.2-5.5 mm of dark hair tone: a clump dipping less than 2 mm into it shows
# nothing). The rest pose is the baseline; a clip must not add any.
_chain_j = [J[n] for n in DEFORM if n.startswith(("hair_fringe", "hair_side", "hair_tail"))]
HAIR_FREE_IDS = np.nonzero(WM[:, _chain_j].sum(1) > 0.05)[0]
_HEAD_TRIS = [list(f) for f in OBJ["main"]["F"][_bf[0]:_bf[1]] if all(domM[i] in ("head", "neck_01") for i in f)]
_HEAD_TRIS = [[f[0], f[k], f[k + 1]] for f in _HEAD_TRIS for k in range(1, len(f) - 1)]
_NSKIN = len(_HEAD_TRIS)
_cf = OBJ["main"]["FRANGE"]["hair_cap"]
_HEAD_TRIS += [[f[0], f[k], f[k + 1]] for f in OBJ["main"]["F"][_cf[0]:_cf[1]] for k in range(1, len(f) - 1)]


_PART_OF = {}
for n_, (a_, b_) in RM.items():
    for i in range(a_, b_):
        _PART_OF[i] = n_


def hair_into_skin(C_, by_part=False):
    bvh_h = BVHTree.FromPolygons(C_.tolist(), _HEAD_TRIS)
    n_, parts_ = 0, {}
    for i in HAIR_FREE_IDS:
        # v5.1: s5's tie-invariant nearest() (the pseudo-normal at a shared edge / vertex). The raw find_nearest took ONE
        # tied face's normal for the sign test: a side-clump vertex 26.5 mm OUTSIDE the head, nearest a cap vertex shared
        # by 5 faces, read -2.4 mm "inside" (the pseudo-normal: +20.7 mm) -- measured on the decoupled build, idle f21
        q_, nn_, ti_, _ = nearest(bvh_h, C_[i])
        if q_ is not None and float((C_[i] - np.array(q_)) @ np.array(nn_)) < (-0.001 if ti_ < _NSKIN else -0.002):
            n_ += 1
            parts_[_PART_OF[int(i)]] = parts_.get(_PART_OF[int(i)], 0) + 1
    return (n_, parts_) if by_part else n_


def _qrel(a, b):
    """rotation vector of conj(a) * b (quaternions w, x, y, z; the short way)."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    w = a[0] * b[0] + a[1:] @ b[1:]
    v = a[0] * b[1:] - b[0] * a[1:] - np.cross(a[1:], b[1:])
    if w < 0:
        w, v = -w, -v
    s = float(np.linalg.norm(v))
    return v / s * 2 * math.atan2(s, w) if s > 1e-12 else np.zeros(3)


BOUNDARY_STEP_MIN = 0.25                # a boundary key interval below this x the clip's median step = a doubled key


def seam_joints(cn, N):
    """the loop seam on the KEYS: pose (first vs last key, every bone) and velocity (the angular step into the seam,
    key N-1 -> N, vs the step out of it, key 0 -> 1, compared with the largest step-to-step change anywhere inside)."""
    Q = np.array(KEYQ[cn])
    nb = Q.shape[1]
    W = np.array([[_qrel(Q[f, b, :4], Q[f + 1, b, :4]) for b in range(nb)] for f in range(N)])
    acc = np.linalg.norm(W[1:] - W[:-1], axis=2)
    # (2026-10-04, the in-game cycle defect: a doubled endpoint = a ~0 step at a boundary = a static hold every wrap) the
    # first and the last key INTERVAL must move like the rest of the cycle: each >= BOUNDARY_STEP_MIN x the median step
    stp = np.degrees(np.linalg.norm(W, axis=2).max(1))
    med = float(np.median(stp))
    return {"boundary_step_deg_first_last": [round(float(stp[0]), 4), round(float(stp[N - 1]), 4)],
            "step_median_deg": round(med, 4),
            "boundary_motion_ok": bool(min(stp[0], stp[N - 1]) >= BOUNDARY_STEP_MIN * med and min(stp[0], stp[N - 1]) > 1e-4),
            "pose_delta_deg_max": round(math.degrees(max(float(np.linalg.norm(_qrel(Q[0, b, :4], Q[N, b, :4]))) for b in range(nb))), 6),
            "loc_delta_m_max": round(float(np.abs(Q[0, :, 4:] - Q[N, :, 4:]).max()), 9),
            "ang_vel_jump_at_seam_deg_per_frame": round(math.degrees(float(np.linalg.norm(W[0] - W[N - 1], axis=1).max())), 4),
            "ang_vel_change_interior_max_deg_per_frame": round(math.degrees(float(acc.max())), 4)}


HAIR_SKIN_REST, HAIR_SKIN_REST_PARTS = hair_into_skin(OBJ["main"]["V"], True)
print("HAIRSKIN_REST", HAIR_SKIN_REST, json.dumps(HAIR_SKIN_REST_PARTS))
samples = []
clip_rep = {}
grip_rel = np.linalg.inv(REST4["hand_r"]) @ G @ AXROT @ REST4["pitchfork"] if HAS_FORK else None
for cn, N in CLIP_N.items():
    K.assign_action(rig, ACTS[cn])
    first = last = firstf = lastf = None
    minz, minz_f, root_off, grip_dev, wrist_max = 1e9, 1e9, 0.0, 0.0, 0.0
    tips, heels = {s: [] for s in "LR"}, {s: [] for s in "LR"}
    toes, sole_z, boot_z = {s: [] for s in "LR"}, {s: [] for s in "LR"}, {s: [] for s in "LR"}
    gate = {"cloak_crossings_" + g: 0 for g in E_GROUPS}
    gate["hair_into_skin_verts_max"] = 0
    gate.update({"fork_shaft_through_cloak": 0,
            "fork_shaft_through_body": 0, "fork_to_cloak_min_m": 1e9, "fork_to_legs_min_m": 1e9, "cloak_to_legs_min_m": 1e9,
            "fork_to_mesh_min_m": 1e9} if HAS_FORK else {"cloak_to_legs_min_m": 1e9})
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::9])
        if HAS_FORK:
            Fk = eval_coords(fko)
            samples.append(Fk[::5])
            minz_f = min(minz_f, float(Fk[:, 2].min()))
            Mh = np.array(rig.pose.bones["hand_r"].matrix); Mf = np.array(rig.pose.bones["pitchfork"].matrix)
            grip_dev = max(grip_dev, float(np.abs(np.linalg.inv(Mh) @ Mf - grip_rel).max()))
        else:
            Fk = None
            Mh = np.array(rig.pose.bones["hand_r"].matrix)
        if f == 1:
            first, firstf = C, Fk
        if f == N + 1:
            last, lastf = C, Fk
        minz = min(minz, float(C[:, 2].min()))
        root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        Ml = np.array(rig.pose.bones["lowerarm_r"].matrix)
        Rw = (np.linalg.inv(Ml) @ Mh)[:3, :3] @ np.linalg.inv((np.linalg.inv(REST4["lowerarm_r"]) @ REST4["hand_r"])[:3, :3])
        wrist_max = max(wrist_max, math.degrees(math.acos(float(np.clip((np.trace(Rw) - 1) / 2, -1, 1)))))
        for s in "LR":
            tips[s].append(C[CONTACT[s]].copy()); heels[s].append(C[CONTACT["heel" + s]].mean(0))
            toes[s].append(C[CONTACT["toe" + s]].copy()); sole_z[s].append(float(C[SOLE_IDS[s], 2].min()))
            boot_z[s].append(float(C[BOOT_IDS[s], 2].min()))
        if (f % 2 == 1 or cn != "idle") and f <= N:
            bvh_c = BVHTree.FromPolygons(C.tolist(), CLOAK_TRIS)
            for g_, E_ in E_GROUPS.items():
                _nc = crossings(bvh_c, C, E_)
                if _nc > gate["cloak_crossings_" + g_]:
                    gate["cloak_crossings_" + g_] = _nc
                    gate.setdefault("cloak_crossings_worst_frame", {})[g_] = f
            if HAS_FORK:
                Mfk = np.array(rig.pose.bones["pitchfork"].matrix) @ np.linalg.inv(REST4["pitchfork"])
                ax_p = np.array([xf(Mfk, p) for p in FORK_AXIS])
                bvh_b = BVHTree.FromPolygons(C.tolist(), BODY_TRIS)
                for i in range(len(ax_p) - 1):
                    d = ax_p[i + 1] - ax_p[i]; L = float(np.linalg.norm(d))
                    if bvh_c.ray_cast(Vector(ax_p[i]), Vector(d / L), L)[0] is not None:
                        gate["fork_shaft_through_cloak"] += 1
                    if bvh_b.ray_cast(Vector(ax_p[i]), Vector(d / L), L)[0] is not None:
                        gate["fork_shaft_through_body"] += 1
            kc = KDTree(len(CLOAK_TRIS)); cv_ = np.unique(np.array(CLOAK_TRIS).ravel())
            kc = KDTree(len(cv_))
            for i, vi in enumerate(cv_):
                kc.insert(C[vi], i)
            kc.balance()
            if HAS_FORK:
                gate["fork_to_cloak_min_m"] = min(gate["fork_to_cloak_min_m"], float(min(kc.find(p)[2] for p in ax_p)))
            _hn, _hp = hair_into_skin(C, True)
            if _hn > gate["hair_into_skin_verts_max"]:
                gate["hair_into_skin_verts_max"], gate["hair_into_skin_worst"] = _hn, dict(_hp, frame=f)
            gate["cloak_to_legs_min_m"] = min(gate["cloak_to_legs_min_m"], float(min(kc.find(p)[2] for p in C[LEG_IDS[::4]])))
            if HAS_FORK:
                kl = KDTree(len(LEG_IDS))
                for i, vi in enumerate(LEG_IDS):
                    kl.insert(C[vi], i)
                kl.balance()
                gate["fork_to_legs_min_m"] = min(gate["fork_to_legs_min_m"], float(min(kl.find(p)[2] for p in ax_p)))
                km = KDTree(len(FORK_FREE_IDS))
                for i, vi in enumerate(FORK_FREE_IDS):
                    km.insert(C[vi], i)
                km.balance()
                _fm = min((km.find(p)[2], i) for i, p in enumerate(ax_p))
                if _fm[0] < gate["fork_to_mesh_min_m"]:
                    gate["fork_to_mesh_min_m"] = float(_fm[0])
                    gate["fork_to_mesh_worst"] = {"frame": f, "axis_m_from_butt": round(float(np.linalg.norm(FORK_AXIS[_fm[1]] - _butt_rest)), 3),
                                                  "part": _PART_OF.get(int(FORK_FREE_IDS[km.find(ax_p[_fm[1]])[1]]), "?")}
    row = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 4), "cyclic": True,
           "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6)}
    if HAS_FORK:
        row["seam_fork_mm"] = round(float(np.linalg.norm(firstf - lastf, axis=1).max()) * 1000, 6)
    row["min_z_main"] = round(minz, 5)
    if HAS_FORK:
        row.update({"min_z_fork": round(minz_f, 5), "fork_butt_z_range": [round(min(butt[cn]), 4), round(max(butt[cn]), 4)]})
    row["root_offset_max"] = round(root_off, 8)
    if HAS_FORK:
        row["grip_relation_max_dev"] = round(grip_dev, 8)
    row.update({"right_wrist_bend_max_deg": round(wrist_max, 2),
                "ik_unreachable_max_m": {k[1]: round(v, 5) for k, v in ik_worst.items() if k[0] == cn},
                "gates": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in gate.items()}})
    row["seam_joints"] = seam_joints(cn, N)
    tips = {s: np.array(v) for s, v in tips.items()}; heels = {s: np.array(v) for s, v in heels.items()}
    if cn == "walk":
        gait = {}
        for s in "LR":
            fl = []
            for f in range(N):
                _, _, _, stc = foot_walk(s, f / N)
                t0 = 0.0 if s == "L" else 0.5
                u = (f / N - t0) % 1.0
                if stc and u <= WALK_STANCE - WALK_TOEOFF[0] + 1e-9:
                    fl.append(f)
            P_ = tips[s][fl]
            H_ = heels[s][fl]
            dy = P_[:, 1] - P_[0, 1]
            ideal = WALK_SPEED * (np.array(fl) - fl[0]) / K.FPS
            gait[s] = {"flat_stance_frames": len(fl), "ball_contact_z_range": [round(float(P_[:, 2].min()), 5), round(float(P_[:, 2].max()), 5)],
                       "heel_z_range": [round(float(H_[:, 2].min()), 5), round(float(H_[:, 2].max()), 5)],
                       "lateral_x_drift": round(float(np.ptp(P_[:, 0])), 6),
                       "slip_vs_ground_m": round(float(np.abs(dy - ideal).max()), 6),
                       "swing_contact_z_max": round(float(tips[s][:, 2].max()), 4)}
        step = 2 * WALK_STEP_A / WALK_STANCE * 0.5
        row.update({"gait": gait, "cadence_steps_per_min": round(2 * 60.0 * K.FPS / N, 2),
                    "stance_travel_m": round(2 * WALK_STEP_A, 4), "step_length_m": round(step, 4),
                    "stride_length_m": round(2 * step, 4), "ground_speed_m_per_s": round(WALK_SPEED, 4),
                    "ground_speed_body_heights_per_s": round(WALK_SPEED / Z_TOP, 4),
                    "stance_fraction": WALK_STANCE, "foot_lift_m": WALK_LIFT, "toe_off_roll_deg": WALK_TOEOFF[1],
                    "heel_strike_toes_up_deg": WALK_HEELSTRIKE, "bounce_m": WALK_BOUNCE,
                    "rule": "zero-slip: in flat stance (before toe-off) the ball-contact sole vertex moves back at exactly the "
                            "ground speed (slip = max deviation from that line), with no lift and no lateral drift"})
    elif cn == "run":
        gait, air = {}, []
        for s in "LR":
            fl = [f for f in range(N) if foot_run(s, f / N)[3]]    # (one contiguous stance per foot: touchdowns at t = 0 / 0.5)
            P_ = np.array(toes[s])[fl]
            dy = P_[:, 1] - P_[0, 1]
            ideal = RUN_SPEED * (np.array(fl) - fl[0]) / K.FPS
            sw = [f for f in range(N) if f not in fl]
            gait[s] = {"stance_frames": [f + 1 for f in fl], "toe_contact_vertex_ball_weight": round(float(WM[CONTACT["toe" + s], J["ball_" + s.lower()]]), 3),
                       "toe_contact_z_range": [round(float(P_[:, 2].min()), 6), round(float(P_[:, 2].max()), 6)],
                       "lateral_x_drift": round(float(np.ptp(P_[:, 0])), 6),
                       "slip_vs_ground_m": round(float(np.abs(dy - ideal).max()), 6),
                       "sole_min_z_m": round(float(min(sole_z[s])), 6), "boot_min_z_m": round(float(min(boot_z[s])), 6),
                       "swing_sole_min_z_m": round(float(min(sole_z[s][f] for f in sw)), 4),
                       "swing_sole_max_z_m": round(float(max(sole_z[s][f] for f in sw)), 4),
                       "heel_z_range_stance": [round(float(np.array(heels[s])[fl][:, 2].min()), 4), round(float(np.array(heels[s])[fl][:, 2].max()), 4)]}
        for f in range(N):
            cl = min(sole_z["L"][f], sole_z["R"][f])
            if cl > 0.001:
                air.append((f + 1, round(cl, 4)))
        T_ = N / K.FPS
        row.update({"gait": gait, "airborne_frames_both_soles_up": [a_[0] for a_ in air],
                    "airborne_min_sole_clearance_m": round(min(a_[1] for a_ in air), 4) if air else None,
                    "cadence_steps_per_min": round(2 * 60.0 * K.FPS / N, 2), "step_seconds": round(T_ / 2, 4),
                    "game_cells_per_step_at_0.12s": round(T_ / 2 / 0.12, 3),
                    "stance_fraction": RUN_STANCE, "flight_seconds_per_step": round((0.5 - RUN_STANCE) * T_, 4),
                    "stance_travel_m": round(RUN_STEP[0] + RUN_STEP[1], 4), "ground_speed_m_per_s": round(RUN_SPEED, 4),
                    "stride_length_m": round(RUN_SPEED * T_, 4), "step_length_m": round(RUN_SPEED * T_ / 2, 4),
                    "ground_speed_body_heights_per_s": round(RUN_SPEED / Z_TOP, 4), "pelvis_mean_dz_m": round(RUN_Z0, 4),
                    "bounce_m": RUN_BOUNCE,
                    "knee_drive": {"rule": "peak thigh (hip joint -> knee, world) angle below horizontal over the keyed "
                                   "frames (dense = 20x sampled); the knee-drive key's ball height solved to RUN_KNEE_DRIVE_DEG",
                                   "target_deg": RUN_KNEE_DRIVE_DEG, "v1": RUN_KNEE_V1, "solved": RUN_KNEE_SOLVE},
                    "rule": "zero-slip: through each stance the toe-sole contact vertex (ball-bone dominant; the ball joint is the "
                            "pinned pivot, the toes stay flat) moves back at exactly the ground speed = stance travel / stance "
                            "time; stride = ground speed x cycle; airborne = both soles above 1 mm"})
    else:
        row["feet_planted"] = {s: {"ball_contact_xy_drift": round(float(np.linalg.norm(tips[s][:, :2] - tips[s][0, :2], axis=1).max()), 6),
                                   "ball_contact_z_range": [round(float(tips[s][:, 2].min()), 5), round(float(tips[s][:, 2].max()), 5)],
                                   "heel_z_range": [round(float(heels[s][:, 2].min()), 5), round(float(heels[s][:, 2].max()), 5)]}
                               for s in "LR"}
    clip_rep[cn] = row
    print("CLIP", cn, json.dumps(row))
    sys.stdout.flush()
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["hair_into_skin"] = {"rule": "chain-driven clump vertices (fringe / side / tail follow-through weight > 0.05) > 1 mm inside "
                                 "the posed head / neck skin or > 2 mm under the hair cap's surface; sampled with the cloak gates "
                                 "(every other idle frame, every walk / run frame); gate: no clip exceeds the rest pose",
                         "free_verts": int(len(HAIR_FREE_IDS)), "rest": HAIR_SKIN_REST, "rest_by_part": HAIR_SKIN_REST_PARTS,
                         **{cn: clip_rep[cn]["gates"]["hair_into_skin_verts_max"] for cn in clip_rep},
                         "pass": all(clip_rep[cn]["gates"]["hair_into_skin_verts_max"] <= HAIR_SKIN_REST for cn in clip_rep)}
print("HAIRSKIN", json.dumps(rep["hair_into_skin"]))
K.assign_action(rig, ACTS["idle"]); scene.frame_set(1)
C1 = eval_coords(low)
if HAS_FORK:
    F1 = eval_coords(fko)
    FOCUS["fork_full"] = box(F1, 0.03)
    _tt_ = F1[np.argsort(-F1[:, 2])[:40]]
    FOCUS["fork_head_idle"] = box(np.vstack([_tt_, _tt_ - np.array([0.0, 0.0, 0.38])]), 0.05)   # ("fork_head" stays the rest-pose box)
_eyes1 = C1[np.concatenate([rng("eye.L"), rng("eye.R")])]
FOCUS["eyes"] = [(_eyes1.min(0) - np.array([0.012, 0.0, 0.004])).tolist(), (_eyes1.max(0) + np.array([0.012, 0.0, 0.016])).tolist()]
# v3: the v2-equivalent eyes frame (same centre, the extents shrunk by the eyeball growth) so v2 | v3 compare at one scale
_dr = EYE["L"]["r"] - EYE_V2["L"]["r"]
FOCUS["eyes_v2frame"] = [(np.array(FOCUS["eyes"][0]) + np.array([_dr, 0.0, _dr])).tolist(),
                         (np.array(FOCUS["eyes"][1]) - np.array([_dr, 0.0, _dr])).tolist()]
_eL = C1[rng("eye.L")]                                # v3: his left eye alone (the iris / lash / highlight close-up)
_eLc = 0.5 * (_eL.min(0) + _eL.max(0))
FOCUS["eye_L"] = [(_eLc - np.array([0.022, 0.0, 0.014])).tolist(), (_eLc + np.array([0.022, 0.0, 0.016])).tolist()]
FOCUS["brows"] = [(_eyes1.min(0) - np.array([0.016, 0.0, 0.002])).tolist(), (_eyes1.max(0) + np.array([0.016, 0.0, 0.022])).tolist()]
_mb0 = np.array(FOCUS["mouth"])                      # v2: the rest-pose mouth box's skin, re-boxed in the idle pose
_mi = np.nonzero(np.all((V0m >= _mb0[0]) & (V0m <= _mb0[1]), axis=1))[0]
FOCUS["mouth"] = box(C1[_mi], 0.002)
FOCUS["bracer"] = box(C1[np.concatenate([rng("bracer"), rng("bracergem")])], 0.03)
FOCUS["necklace"] = box(C1[np.concatenate([rng("pendant"), rng("clasp"), rng("pendcap")])], 0.035)
FOCUS["hand"] = box([xf(np.array(rig.pose.bones["hand_r"].matrix) @ np.linalg.inv(REST4["hand_r"]), HEADP["hand_r"])], 0.10)
low["conquest_focus"] = json.dumps(FOCUS)
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
if HAS_FORK:
  rep["grip"] = {"rule": "hammer grip: the shaft along the right hand's knuckle line, the tines out of the thumb / index side; "
                       "the pitchfork bone is a child of hand_r and every clip keys it at the SAME hand-relative transform "
                       "(grip_relation_max_dev); the hand+fork roll about the shaft is picked per clip (10-deg search, least wrist bend "
                       "at t = 0); inside the grip the fork is turned about its own shaft (tine_roll_in_hand, one value) so the idle "
                       "shows the tines face-on; the REST pose stands the fork upright beside the right hand, butt on the floor",
               "G_hand_to_fork_rest4": grip_rel.round(6).tolist(), "fork_roll_search": ROLL_SEARCH,
               "grip_at_m_from_butt": round(FORK["grip_at"] * FORK["len"], 4)}
else:
    rep["grip"] = {"rule": "no prop (HAS_FORK False, review-log 2026-10-03): the right arm keeps the approved carry path (the "
                           "hand frame the fork grip drove, roll search unchanged); the right fingers take the clip's left-hand "
                           "pose (2026-10-04: both hands from CLIP_HANDS / HAND_POSES)", "clip_hands": CLIP_HANDS, "hand_poses": HAND_POSES, "fork_roll_search": ROLL_SEARCH}
rep["tris"] = {"model": report["tris"]["total"], "outline_shells": 0, "budget": TRI_BUDGET}
print("TRIS", json.dumps(rep["tris"]))
rig["conquest_rig"] = ("wren: root (contract) > MPFB2 game_engine skeleton (pelvis, spine_01-03, neck_01, head, clavicle / upperarm / "
                       "lowerarm / hand + 15 finger bones per side, thigh / calf / foot / ball) + hair_fringe / hair_side.L/R / "
                       "hair_tail .0-2 (head children), tie.0-1 (pelvis child), cape.0-4.0-3 (spine_03 children)" +
                       (" + pitchfork (hand_r child)" if HAS_FORK else ""))
low["conquest_clips"] = list(CLIP_N)
low["conquest_clip_status"] = (("idle (leaning a little on the planted pitchfork, breath, eased weight shift, chin-up look) + walk "
                                "(boyish quick stride with a contact bounce, the fork carried upright, chest / head overlap) + run "
                                "(young hero sprint: forward lean, knee drive to RUN_KNEE_DRIVE_DEG below horizontal, flight phase, full left-arm pump, the fork charged "
                                "forward)") if HAS_FORK else
                               ("idle (breath, eased weight shift, chin-up look) + walk (boyish quick stride with a contact bounce, "
                                "chest / head overlap) + run (young hero sprint: forward lean, knee drive to RUN_KNEE_DRIVE_DEG below horizontal, flight phase, full "
                                "left-arm pump); empty-handed: the right arm keeps the approved carry path")) + \
                              ("; cloak / fringe / side hair / nape tail / sash ties follow-through (damped-spring lag); "
                               "no attack / hit / death")
low["conquest_look"] = ("shaded: Col x baked AO, baked normal map; the hair " +
                        ("faces smooth-shaded with CUSTOM SPLIT NORMALS (the smooth-proxy normals leaned toward each lock's own "
                         "-- the hairdo shades as one soft volume; research H5, 2026-10-03), its UV strip of the normal map flat "
                         "and white AO" if HAIR_NORMAL_CARRIER == "vertex" else
                         "UV strip of the normal map carries the smooth-proxy normals (one soft volume on flat facets), white AO") +
                        "; no outline shells, no cel bands; the stylisation is DRAWN into the palette regions (shadow shapes, "
                        "brows, lash band, iris shade + highlight, hair tiers: roots / angel ring / tips / dark inner cap)")
low["conquest_locomotion"] = "biped in flat boots: rest pose on the floor per contract (soles z 0), clips in place"
if HAS_FORK:
    low["conquest_pitchfork_grip"] = json.dumps(grip_rel.round(6).tolist())
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
for a in list(bpy.data.actions):
    if a.name not in CLIP_N:
        bpy.data.actions.remove(a)
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
if DIGEST_ONLY:
    json.dump({"digest": rep["digest"], "tris": report["tris"]["total"], "seconds": round(time.time() - T0, 1)},
              open(DIGEST_ONLY, "w"), indent=1)
    print("DIGEST", DIG_ALL, json.dumps(DIG))
    sys.stdout.flush(); os._exit(0)
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/" if not SCRATCH else "//textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== glb + skins
for o in scene.objects:
    o.select_set(o is rig or o in MESHES)
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, ACTS["idle"])
t_ = time.time()
import export_glb as _EG; _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False, export_attributes=True,
                          export_anim_slide_to_zero=True)
# (2026-10-04, the in-game cycle defect) every clip starts at t = 0: keyed at frames 1..N+1 the samplers began at 1/24 s
# and the game's importer held the first pose from 0 to 1/24 s -- a doubled leading key = a 42 ms static hold each wrap
rig.animation_data.action = None


def glb_loop_gate(path):
    """per glb animation: the sampler times start at 0 and end at the true period N / FPS with N + 1 keys (the last =
    the first: the closing key that carries the period in glTF), and BOTH boundary key intervals move (>= BOUNDARY_STEP_MIN
    x the median step over the densest rotation channels) -- no doubled key at either end."""
    import struct
    data = open(path, "rb").read()
    L_ = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L_]); b0 = 20 + L_ + 8

    def acc_(i):
        a_ = js["accessors"][i]; bv = js["bufferViews"][a_["bufferView"]]
        nc = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[a_["type"]]
        o_ = b0 + bv.get("byteOffset", 0) + a_.get("byteOffset", 0)
        return np.frombuffer(data[o_:o_ + a_["count"] * nc * 4], dtype=np.float32).reshape(a_["count"], nc)
    out = {}
    for an in js["animations"]:
        Nc = CLIP_N[an["name"]]
        tm = None; rq = []
        for ch in an["channels"]:
            s_ = an["samplers"][ch["sampler"]]
            t_in = acc_(s_["input"])[:, 0]
            if tm is None or len(t_in) > len(tm):
                tm = t_in
            if ch["target"]["path"] == "rotation":
                rq.append(acc_(s_["output"]).astype(float))
        rq = np.array([q_ for q_ in rq if len(q_) == len(tm)])          # (constant channels export 2 keys)
        dots = np.clip(np.abs(np.einsum("bki,bki->bk", rq[:, :-1], rq[:, 1:])), 0, 1)
        stp = np.degrees(2 * np.arccos(dots)).max(0)                    # per key interval, the largest bone step
        med = float(np.median(stp))
        close = float(np.degrees(2 * np.arccos(np.clip(np.abs(np.einsum("bi,bi->b", rq[:, 0], rq[:, -1])), 0, 1))).max())
        r_ = {"keys": int(len(tm)), "t_first_s": round(float(tm[0]), 6), "t_last_s": round(float(tm[-1]), 6),
              "period_s": round(Nc / K.FPS, 6), "boundary_step_deg_first_last": [round(float(stp[0]), 4), round(float(stp[-1]), 4)],
              "step_median_deg": round(med, 4), "closing_key_dev_deg": round(close, 4)}
        r_["pass"] = bool(len(tm) == Nc + 1 and abs(float(tm[0])) < 1e-6 and abs(float(tm[-1]) - Nc / K.FPS) < 1e-4 and
                          min(stp[0], stp[-1]) >= BOUNDARY_STEP_MIN * med and min(stp[0], stp[-1]) > 1e-4 and close < 0.1)
        out[an["name"]] = r_
    return out


GLB_LOOP = glb_loop_gate(OUT_GLB)
print("GLBLOOP", json.dumps(GLB_LOOP))


def glb_carries(path):
    import struct
    data = open(path, "rb").read()
    L = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L])
    prims = [(m.get("name"), p) for m in js.get("meshes", []) for p in m["primitives"]]
    return {"materials": [{"name": m.get("name"), "alphaMode": m.get("alphaMode", "OPAQUE"), "doubleSided": m.get("doubleSided", False),
                           "textures": sorted(k for k in m.get("pbrMetallicRoughness", {}) if k.endswith("Texture"))
                           + (["normalTexture"] if "normalTexture" in m else [])} for m in js.get("materials", [])],
            "primitives": [{"mesh": n, "material": js["materials"][p["material"]]["name"] if "material" in p else None,
                            "colour_sets": sorted(k for k in p["attributes"] if k.startswith("COLOR"))} for n, p in prims],
            "nodes": len(js.get("nodes", [])), "mesh_nodes": sorted(n.get("name") for n in js.get("nodes", []) if "mesh" in n),
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": [len(s_["joints"]) for s_ in js.get("skins", [])]}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t_, 1), "carries": glb_carries(OUT_GLB),
              "structure": "armature + skinned 'wren' (body, opaque)" +
                           (" + skinned 'wren_pitchfork' (own node, bone 'pitchfork')" if HAS_FORK else " (no prop: HAS_FORK False)") +
                           "; material Col x AO + normal map; natural scale (metres); report-only cell fit %.5f" % k_fit,
              "loop_keys": GLB_LOOP}
ALLM = list(MESHES)
geo0 = geometry_digest(ALLM)
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"]}}
pal_w = PAL.load(UNIT, "winter")
gt_w = glow_tiers(pal_w)
assert gt_w["grade_pass"] and gt_w["hue_pass"], gt_w
counts_w = repaint(ALLM, pal_w)
out_w = OUT_RIGGED[:-6] + "__winter.blend"
bpy.ops.wm.save_as_mainfile(filepath=out_w, copy=True, compress=True, relative_remap=False)
rep["skins"]["winter"] = {"file": out_w, "palette": PAL.table(pal_w), "palette_files": pal_w["files"], "glow_tiers": gt_w,
                          "region_faces": counts_w, "geometry_colour_uv_digest": geometry_digest(ALLM)}
repaint(ALLM, pal_default)
rep["skins"]["repaint_proof"] = {"rule": "a skin is a pure palette swap: same regions / faces / vertices; default restored",
                                 "default_digest_before": geo0, "default_digest_after_restore": geometry_digest(ALLM)}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
