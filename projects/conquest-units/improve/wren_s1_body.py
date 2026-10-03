# Wren build section 1: helpers, the MPFB2 body (stylised teen macros + face dials, arms lowered in the bind pose),
# landmarks, BVHs, the barycentric weight transfer. Executed inside wren_build.py's namespace.


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def hash01(i, seed=0.0):
    return (math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


def tri_count_F(F):
    return int(sum(len(f) - 2 for f in F))


CUT_DUP_FACES = [0]


def cut_part(V, F, R, fn, tau=0.0, attrs=(), vgate=None, snap=0.12):
    """v3: split a part's faces along the iso-line fn = tau (the body's iso_cut, for a standalone part): fn maps (n, 3)
    positions -> values; attrs = per-vertex float arrays carried onto the new vertices (linear along the split edge);
    vgate = per-vertex bool: an edge is cut only when both its ends pass. Untouched faces keep their topology; a face the
    cut crosses is split, pieces with more than 4 corners are triangulated. -> V, F, R, attrs, new vertex count."""
    V = np.asarray(V, float)
    bm_ = bmesh.new()
    names_ = sorted(set(R))
    rl_ = bm_.faces.layers.int.new("r")                 # (layers first: Blender 5 reallocates elements on a new layer)
    lf_ = bm_.verts.layers.float.new("f")
    la_ = [bm_.verts.layers.float.new("a%d" % k) for k in range(len(attrs))]
    lg_ = bm_.verts.layers.int.new("g")
    vs_ = [bm_.verts.new(p) for p in V]
    for f_, r_ in zip(F, R):
        try:
            fb_ = bm_.faces.new([vs_[i] for i in f_]); fb_[rl_] = names_.index(r_)
        except ValueError:                             # (a repeated face -- same vertex set -- is kept once; counted)
            CUT_DUP_FACES[0] += 1
    vals_ = np.asarray(fn(V), float)
    for i, v in enumerate(vs_):
        v[lf_] = float(vals_[i]); v[lg_] = 1 if vgate is None else int(bool(vgate[i]))
        for k, a in enumerate(attrs):
            v[la_[k]] = float(a[i])
    eps_ = 1e-9
    side_ = lambda v: 0 if abs(v[lf_] - tau) <= eps_ else (1 if v[lf_] > tau else -1)
    for e in bm_.edges:
        a, b = e.verts
        if side_(a) * side_(b) < 0 and a[lg_] and b[lg_]:
            tt = (tau - a[lf_]) / (b[lf_] - a[lf_])
            if tt < snap:
                a[lf_] = tau
            elif tt > 1 - snap:
                b[lf_] = tau
    cuts_ = [e for e in bm_.edges if side_(e.verts[0]) * side_(e.verts[1]) < 0 and e.verts[0][lg_] and e.verts[1][lg_]]
    for e in cuts_:
        a, b = e.verts
        tt = (tau - a[lf_]) / (b[lf_] - a[lf_])
        av = [a[l] * (1 - tt) + b[l] * tt for l in la_]
        _, nv = bmesh.utils.edge_split(e, a, tt)
        nv[lf_] = tau; nv[lg_] = 1
        for l, x in zip(la_, av):
            nv[l] = x
    pairs_ = []
    for f_ in bm_.faces:
        sd_ = [side_(v) for v in f_.verts]
        if 1 in sd_ and -1 in sd_:
            cv_ = [v for v, s_ in zip(f_.verts, sd_) if s_ == 0]
            if len(cv_) == 2:
                pairs_.append(cv_)
    for cv_ in pairs_:
        try:
            bmesh.ops.connect_verts(bm_, verts=cv_)
        except Exception:
            pass
    big_ = [f_ for f_ in bm_.faces if len(f_.verts) > 4]
    if big_:
        bmesh.ops.triangulate(bm_, faces=big_)
    bm_.verts.index_update()
    V2 = np.array([v.co[:] for v in bm_.verts])
    F2 = [[v.index for v in f_.verts] for f_ in bm_.faces]
    R2 = [names_[f_[rl_]] for f_ in bm_.faces]
    A2 = [np.array([v[l] for v in bm_.verts]) for l in la_]
    bm_.free()
    return V2, F2, R2, A2, len(cuts_)


def mesh_arrays(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


# =========================================================================== 1. MPFB2 body
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
assert addon_utils.enable("bl_ext.user_default.mpfb", default_set=True, handle_error=None) is not None
from bl_ext.user_default.mpfb.services.humanservice import HumanService      # noqa: E402
from bl_ext.user_default.mpfb.services.targetservice import TargetService    # noqa: E402
from bl_ext.user_default.mpfb.services.locationservice import LocationService  # noqa: E402
t_ = time.time()
macro = TargetService.get_default_macro_info_dict()
macro.update(MACRO)
macro["race"] = dict(RACE)
hb = HumanService.create_human(macro_detail_dict=macro)
tdir = LocationService.get_mpfb_data("targets")
for rel, w in {**TARGETS, **(TARGETS_EDIT or {})}.items():   # (v7: TARGETS_EDIT = the HAIRSTABLE mouth-dial probe)
    p_ = os.path.join(tdir, rel + ".target.gz")
    assert os.path.exists(p_), p_
    TargetService.load_target(hb, p_, weight=w)
rig0 = HumanService.add_builtin_rig(hb, "game_engine", import_weights=True)
for m in hb.modifiers:
    if m.type == "MASK":
        m.show_viewport = False; m.show_render = False
bpy.context.view_layer.update()


def eval_hb():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = hb.evaluated_get(dg)
    me_ = ev.to_mesh()
    co = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


V_rest0 = eval_hb()
me0 = hb.data
gi_body = hb.vertex_groups["body"].index
NV0 = len(me0.vertices)
GRP = {g.index: g.name for g in hb.vertex_groups}
MB = [b.name for b in rig0.data.bones]
MBI = {n: i for i, n in enumerate(MB)}
W0 = np.zeros((NV0, len(MB)))
in_body = np.zeros(NV0, bool)
helper = {}
for v in me0.vertices:
    for g in v.groups:
        nm = GRP[g.group]
        if nm in MBI:
            W0[v.index, MBI[nm]] = g.weight
        elif g.group == gi_body and g.weight > 0.5:
            in_body[v.index] = True
        elif nm in ("helper-l-eye", "helper-r-eye", "helper-upper-teeth", "joint-mouth") and g.weight > 0.5:
            helper.setdefault(nm, []).append(v.index)
Hbody = float(V_rest0[in_body, 2].max() - V_rest0[in_body, 2].min())
SCALE = BODY_H / Hbody
# the MPFB A-pose arm angle, measured before the drop (shoulder -> wrist below the horizontal)
_arm_deg0 = {}
for side in ("l", "r"):
    h_ = np.array(rig0.data.bones["upperarm_" + side].head_local); w_ = np.array(rig0.data.bones["hand_" + side].head_local)
    _arm_deg0[side] = math.degrees(math.atan2(h_[2] - w_[2], abs(w_[0] - h_[0])))
# ---- the bind pose: both upper arms lowered by REST_ARM_DOWN about the forward axis through the MPFB rig
for side, sg in (("l", 1.0), ("r", -1.0)):
    b = rig0.data.bones["upperarm_" + side]
    M3 = np.array(b.matrix_local)[:3, :3]
    Rh = K._rot([0.0, 1.0, 0.0], sg * math.radians(REST_ARM_DOWN))
    pb = rig0.pose.bones["upperarm_" + side]
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = Matrix((M3.T @ Rh @ M3).tolist()).to_quaternion()
    # the MPFB rest forearm is bent ~39 deg forward: open the elbow by REST_ELBOW_OPEN (a rotation about the elbow's own
    # hinge axis, expressed in the lowerarm's rest frame, so it composes with the upper-arm drop)
    bl = rig0.data.bones["lowerarm_" + side]
    ua_ = np.array(b.tail_local) - np.array(b.head_local); la_ = np.array(bl.tail_local) - np.array(bl.head_local)
    ax_ = unit(np.cross(ua_, la_))
    sgn_ = 1.0 if float(unit(ua_) @ (K._rot(ax_, 0.1) @ unit(la_))) > float(unit(ua_) @ unit(la_)) else -1.0
    Ml = np.array(bl.matrix_local)[:3, :3]
    pl = rig0.pose.bones["lowerarm_" + side]
    pl.rotation_mode = "QUATERNION"
    pl.rotation_quaternion = Matrix((Ml.T @ K._rot(ax_, sgn_ * math.radians(REST_ELBOW_OPEN)) @ Ml).tolist()).to_quaternion()
bpy.context.view_layer.update()
V_all = eval_hb() * SCALE
BREST = {}
for pb in rig0.pose.bones:
    Mp = np.array(pb.matrix)
    BREST[pb.name] = {"head": np.array(pb.head) * SCALE, "tail": np.array(pb.tail) * SCALE, "z": Mp[:3, 2].copy()}
_arm_deg1 = {s: math.degrees(math.atan2(BREST["upperarm_" + s]["head"][2] - BREST["hand_" + s]["head"][2],
                                        abs(BREST["hand_" + s]["head"][0] - BREST["upperarm_" + s]["head"][0]))) for s in "lr"}
body_idx = np.nonzero(in_body)[0]
LIFT = SOLE_T - float(V_all[body_idx, 2].min())
V_all[:, 2] += LIFT
for b in BREST.values():
    b["head"][2] += LIFT; b["tail"][2] += LIFT
report["mpfb"] = {"macro": {k: v for k, v in macro.items()}, "targets": TARGETS, "rig": "game_engine",
                  "age_rule": "MakeHuman age macro 0 = 1 yr, 0.1875 = 11 yr, 0.5 = 25 yr: 16 yr = 0.1875 + 5/14 x 0.3125 = 0.299",
                  "mpfb_bones": len(MB), "scale_to_body_h": round(SCALE, 5), "body_h_barefoot": BODY_H,
                  "arm_below_horizontal_deg": {"mpfb_a_pose": {k: round(v, 1) for k, v in _arm_deg0.items()},
                                               "bind_pose": {k: round(v, 1) for k, v in _arm_deg1.items()}},
                  "sole_lift": round(LIFT, 4), "seconds": round(time.time() - t_, 1)}
EYE = {}
for side, nm in (("L", "helper-l-eye"), ("R", "helper-r-eye")):
    P_ = V_all[helper[nm]]
    c_ = P_.mean(0)
    EYE[side] = {"c": c_, "r": float(np.linalg.norm(P_ - c_, axis=1).mean())}
MOUTH = V_all[helper["joint-mouth"]].mean(0)
TEETH = V_all[helper["helper-upper-teeth"]]
remap = -np.ones(NV0, dtype=np.int64); remap[body_idx] = np.arange(len(body_idx))
BF = []
for p in me0.polygons:
    vs = list(p.vertices)
    if all(in_body[vs]):
        BF.append([int(remap[i]) for i in vs])
BV = V_all[body_idx].copy()
BW = W0[body_idx].copy()


# ---- v2 lip seal (review-log 2026-09-28 "Wren v2 face feedback": the lips pair). The MPFB base mouth rests PARTED; v1
# closed it with the mouth-compression expression at 1.0, which rolled the upper lip in behind a protruding lower lip (the
# mismatch) and squeezed the corners (the pinch). v2 drops the compression and seals the relaxed lips geometrically: the
# gap (front rays that pass between the lips and hit deeper than LIP_SEAL[2] behind the lip front) is measured per x, and
# the upper lip moves down / the lower lip up by half of it each, full at the rim, fading out over LIP_SEAL[0] of lip
# height; the lips meet LIP_SEAL[1] past the midline (a closed seam, no slit hole). No MPFB dial closes the base mouth
# (measured: upperlip-middle-down / lowerlip-middle-up at 1.0 still leave a 64-70 mm deep gap at x = 4 mm).
def lip_gap(V, F, z_guess, xs, depth):
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    zs_ = np.arange(z_guess - 0.012, z_guess + 0.012, 0.0001)
    top, bot = np.full(len(xs), np.nan), np.full(len(xs), np.nan)
    for k, x_ in enumerate(xs):
        ys_ = np.array([(lambda h: h[0][1] if h[0] is not None else 9.0)(bvh_.ray_cast(Vector((float(x_), -0.6, float(z_))),
                                                                                          Vector((0.0, 1.0, 0.0)), 1.2)) for z_ in zs_])
        # local front: the most forward hit within +-5 mm (a well between two lips, not the face's own slope)
        loc_ = np.array([ys_[max(i - 50, 0):i + 51].min() for i in range(len(ys_))])
        deep_ = np.nonzero(ys_ > loc_ + depth)[0]
        if len(deep_):
            # the gap = the deep run nearest the guess (one contiguous run)
            i0 = deep_[np.argmin(np.abs(zs_[deep_] - z_guess))]
            lo_, hi_ = i0, i0
            while lo_ - 1 >= 0 and ys_[lo_ - 1] > loc_[lo_ - 1] + depth:
                lo_ -= 1
            while hi_ + 1 < len(zs_) and ys_[hi_ + 1] > loc_[hi_ + 1] + depth:
                hi_ += 1
            top[k], bot[k] = zs_[hi_] + 0.00005, zs_[lo_] - 0.00005
    return top, bot


def seam_curve(V, F, xs_, zc_):
    """the seam as SEEN from the front after the seal: per column the most recessed front hit within +-3 mm of the gap mid."""
    bvh2_ = BVHTree.FromPolygons(V.tolist(), F)
    zc2 = []
    for x_, z0_ in zip(xs_, zc_):
        zz_ = np.arange(z0_ - 0.003, z0_ + 0.003, 0.00005)
        yy_ = np.array([(lambda h: h[0][1] if h[0] is not None else -9.0)(bvh2_.ray_cast(Vector((float(x_), -0.6, float(z_))),
                                                                                         Vector((0.0, 1.0, 0.0)), 1.2)) for z_ in zz_])
        zc2.append(float(zz_[int(np.argmax(yy_))]))
    zc2 = np.convolve(np.pad(np.array(zc2), 2, mode="edge"), np.ones(5) / 5.0, mode="valid")
    return {"xs": np.asarray(xs_), "zc": zc2}


def seal_lips(V, F, z_guess):
    xs = np.arange(-0.030, 0.0301, 0.0005)
    top, bot = lip_gap(V, F, z_guess, xs, LIP_SEAL[2])
    has = ~np.isnan(top)
    if not has.any():
        return V, {"gap_max_mm": 0.0}
    zc = np.interp(xs, xs[has], 0.5 * (top[has] + bot[has]))
    half = np.where(has, 0.5 * (np.nan_to_num(top) - np.nan_to_num(bot)), 0.0)
    half = np.convolve(half, np.ones(5) / 5.0, mode="same")          # the gap tapers into the corners smoothly
    V = V.copy()
    box_ = (np.abs(V[:, 0]) < 0.032) & (np.abs(V[:, 2] - z_guess) < 0.03)
    y_front = float(V[box_ & (np.abs(V[:, 0]) < 0.02) & (np.abs(V[:, 2] - z_guess) < 0.015), 1].min())
    near = box_ & (V[:, 1] < y_front + 0.02)
    x_ = V[near, 0]; z_ = V[near, 2]
    zc_ = np.interp(x_, xs, zc); h_ = np.interp(x_, xs, half)
    up_ = z_ >= zc_
    global LIP_SIDE                                   # v4: which lip each sealed vertex belongs to (+1 upper, -1 lower)
    LIP_SIDE = np.zeros(len(V)); LIP_SIDE[np.nonzero(near)[0]] = np.where(up_, 1.0, -1.0)
    d_ = np.where(up_, z_ - (zc_ + h_), (zc_ - h_) - z_)                # distance past the rim (<= 0 inside the gap)
    w_ = 1.0 - smoothstep(0.0, LIP_SEAL[0], np.maximum(d_, 0.0))
    shift = w_ * np.where(h_ > 1e-5, h_ + LIP_SEAL[1], 0.0)
    V[np.nonzero(near)[0], 2] = z_ + np.where(up_, -shift, shift)
    top2, bot2 = lip_gap(V, F, z_guess, xs, LIP_SEAL[2])
    global SEAM, SEAM_GAP
    SEAM_GAP = (xs[has], zc[has])
    SEAM = seam_curve(V, F, xs[has], zc[has])        # the seam curve (the painted mouth line follows it)
    return V, {"rule": "front rays deeper than %.1f mm behind the lip front = the gap; half the gap closed from each lip, "
                       "falloff %.1f mm, overlap %.2f mm" % (LIP_SEAL[2] * 1000, LIP_SEAL[0] * 1000, LIP_SEAL[1] * 1000),
               "gap_max_mm_before": round(1000 * float(np.nanmax(top - bot)), 2), "gap_columns_before": int(has.sum()),
               "gap_width_mm_before": round(1000 * float(xs[has].max() - xs[has].min()), 1),
               "gap_columns_after": int((~np.isnan(top2)).sum()), "verts_moved": int((shift > 1e-6).sum()),
               "max_shift_mm": round(1000 * float(shift.max()), 2)}


SEAM = None
LIP_SIDE = None
if LIP_SEAL is not None:
    BV, report["lip_seal"] = seal_lips(BV, BF, float(TEETH[:, 2].min()))
    print("LIPSEAL", json.dumps(report["lip_seal"]))

def smirk_rise(x_, x0_, x1_):
    """v4 smirk profile (m, >= 0) at x on a seam spanning x0 .. x1: 0 over the middle, rising by MOUTH_SMIRK[0] over the
    outer MOUTH_SMIRK[2] of the MOUTH_SMIRK[1] side's half (the same curve s2 painted in v4/v5)."""
    h_ = MOUTH_SMIRK[1] * np.asarray(x_) / max(x1_ if MOUTH_SMIRK[1] > 0 else -x0_, 1e-6)
    return MOUTH_SMIRK[0] * smoothstep(1.0 - MOUTH_SMIRK[2], 1.0, h_) ** 1.5


if LIP_SEAL is not None and SEAM is not None and MOUTH_SMIRK is not None and MOUTH_SMIRK_GEO is not None:
    # v6 GEOMETRIC SMIRK (review-log 2026-09-29 "Wren v6 mouth feedback": ONE mouth -- the drawn line sits exactly on the
    # opening): the sealed lips near the seam are lifted by the smirk profile (full on the seam, fading to none
    # MOUTH_SMIRK_GEO above / below it and over 5 mm past the seam's end), the seam curve with them; s2 then paints the line
    # on the seam as it is (no painted-only offset). Only z moves, the same amount above and below the seam at each x: the
    # seal's overlap and the layer order are kept.
    _sx0, _sx1 = float(SEAM["xs"].min()), float(SEAM["xs"].max())
    _xc = np.clip(BV[:, 0], _sx0, _sx1)
    _rise = smirk_rise(_xc, _sx0, _sx1) * (1.0 - smoothstep(0.0, 0.005, np.maximum(np.abs(BV[:, 0]) - np.where(BV[:, 0] > 0, _sx1, -_sx0), 0.0)))
    _dzs = np.abs(BV[:, 2] - np.interp(BV[:, 0], SEAM["xs"], SEAM["zc"]))
    _yfr6 = float(BV[(np.abs(BV[:, 0]) < 0.02) & (_dzs < 0.015), 1].min())
    _wz = (1.0 - smoothstep(0.0, MOUTH_SMIRK_GEO, _dzs)) * (BV[:, 1] < _yfr6 + 0.030) * (np.abs(BV[:, 0]) < 0.06)
    _z0 = BV[:, 2].copy()
    BV[:, 2] += _rise * _wz
    SEAM["zc"] = SEAM["zc"] + smirk_rise(SEAM["xs"], _sx0, _sx1)
    report["lip_seal"]["smirk_geo"] = {"rise_mm": MOUTH_SMIRK[0] * 1000, "falloff_mm": MOUTH_SMIRK_GEO * 1000,
                                       "verts_moved": int((np.abs(BV[:, 2] - _z0) > 1e-7).sum()),
                                       "max_move_mm": round(1000 * float(np.abs(BV[:, 2] - _z0).max()), 3)}
if LIP_SEAL is not None and SEAM is not None:          # v5: the seam curve (m, build frame) for the face probe's zone masks
    report["lip_seal"]["seam"] = {"xs": np.round(SEAM["xs"], 5).tolist(), "zc": np.round(SEAM["zc"], 5).tolist()}
# ---- v3 GEOMETRIC eye enlargement (review-log 2026-09-29: the eye dial was exhausted at 1.0). Each eye's socket and
# eyeball scale TOGETHER by EYE_SCALE about the eyeball's front pole (the cornea apex): the lids keep their exact fit on the
# ball (a similar figure), the opening grows by the factor, and the cornea stays at its depth (no bug-eyed bulge). The
# scale blends out over the orbit: full within EYE_SCALE_ZONE[0] mm of the eye centre in the face plane, none beyond
# [1] mm; in depth full to [2] x the eyeball radius behind the centre (the socket sleeve), none beyond [3] x (the head
# behind never moves). Everything painted on the face (liner / lash, brows, fringe shadow) is derived afterwards from the
# scaled geometry, so it tracks.
EYE_V2 = {s: {"c": EYE[s]["c"].copy(), "r": EYE[s]["r"]} for s in "LR"}
EYE_SCALE_INFO = {"factor": EYE_SCALE, "zone": EYE_SCALE_ZONE if EYE_ORBIT is None else {"orbit_mm": EYE_ORBIT, "depth_x_r": EYE_ORBIT_DEPTH}}


def orbit_R(ang_deg, key):
    """v4 orbit ellipse radius (m) at polar angle ang (0 = outer corner, 90 = up, 180 = inner, 270 = down): quadrant
    ellipses through the per-direction radii EYE_ORBIT[dir][key] (key 0 = full scale within, 1 = no scale beyond)."""
    a_ = np.radians(np.asarray(ang_deg, float))
    c_, s_ = np.cos(a_), np.sin(a_)
    ah_ = np.where(c_ >= 0, EYE_ORBIT["outer"][key], EYE_ORBIT["inner"][key]) * 1e-3
    av_ = np.where(s_ >= 0, EYE_ORBIT["up"][key], EYE_ORBIT["down"][key]) * 1e-3
    return 1.0 / np.sqrt((c_ / ah_) ** 2 + (s_ / av_) ** 2)


def body_edges(F):
    e_ = set()
    for f in F:
        for k in range(len(f)):
            a_, b_ = f[k], f[(k + 1) % len(f)]
            e_.add((min(a_, b_), max(a_, b_)))
    return np.array(sorted(e_), dtype=np.int64)


BE = body_edges(BF)
if EYE_SCALE != 1.0:
    _moved = np.zeros(len(BV))
    _V_unscaled = BV.copy()
    _fold = []
    _slide = np.zeros(len(BV))                     # v4: per vertex, how much of its move slides along the original surface
    _core = np.zeros(len(BV))                      # v4: the similar-figure core weight (the approved socket)
    for s in "LR":
        c_, r_ = EYE[s]["c"], EYE[s]["r"]
        f_ = c_ + np.array([0.0, -r_, 0.0])
        d_ = np.hypot(BV[:, 0] - c_[0], BV[:, 2] - c_[2])
        if EYE_ORBIT is None:                          # v3 blend: circles in the face plane (it leaked onto the nose + side)
            w_ = (1.0 - smoothstep(EYE_SCALE_ZONE[0] * 1e-3, EYE_SCALE_ZONE[1] * 1e-3, d_)) * \
                (1.0 - smoothstep(c_[1] + EYE_SCALE_ZONE[2] * r_, c_[1] + EYE_SCALE_ZONE[3] * r_, BV[:, 1]))
            dv_ = (EYE_SCALE - 1.0) * w_[:, None] * (BV - f_)
            _core = np.maximum(_core, (d_ < EYE_SCALE_ZONE[0] * 1e-3) * w_)
        else:
            # v4 blend (review-log 2026-09-29 "Wren v4 feedback": the v3 blend leaked onto the nose and the side of the
            # face): (1) the face-plane falloff follows the ORBIT (quadrant ellipses: tight toward the nose, wider toward
            # the temple); (2) the depth part of the scale is gated tighter than the lateral part; (3) past the socket
            # core the move SLIDES along the original surface (EYE_SLIDE): the skin makes room for the bigger socket by
            # redistributing over the orbit, the orbit / cheek / temple / nose SHAPE stays the v2 surface.
            ang_ = np.degrees(np.arctan2(BV[:, 2] - c_[2], (BV[:, 0] - c_[0]) * (1.0 if s == "L" else -1.0))) % 360.0
            R0_ = orbit_R(ang_, 0)
            wl_ = 1.0 - smoothstep(R0_, orbit_R(ang_, 1), d_)
            dl_ = 1.0 - smoothstep(c_[1] + EYE_ORBIT_DEPTH[0] * r_, c_[1] + EYE_ORBIT_DEPTH[1] * r_, BV[:, 1])
            dd_ = 1.0 - smoothstep(c_[1] + EYE_ORBIT_DEPTH[2] * r_, c_[1] + EYE_ORBIT_DEPTH[3] * r_, BV[:, 1])
            dv_ = (EYE_SCALE - 1.0) * (BV - f_) * np.stack([wl_ * dl_, wl_ * dl_ * dd_, wl_ * dl_], 1)
            if EYE_SLIDE is not None:
                ts_ = smoothstep(R0_ - EYE_SLIDE[0] * 1e-3, R0_ + EYE_SLIDE[1] * 1e-3, d_) * (np.linalg.norm(dv_, axis=1) > 0)
                _slide = np.maximum(_slide, ts_)
            _core = np.maximum(_core, (d_ <= R0_) * dl_)
            # fold proof per direction: rho' = rho (1 + k w(rho)) stays monotone along every face-plane ray
            for a_s in np.arange(0.0, 360.0, 5.0):
                _dd = np.linspace(0.0, float(orbit_R(a_s, 1)) * 1.2, 400)
                _rho = _dd * (1.0 + (EYE_SCALE - 1.0) * (1.0 - smoothstep(float(orbit_R(a_s, 0)), float(orbit_R(a_s, 1)), _dd)))
                _fold.append(float(np.min(np.diff(_rho) / np.diff(_dd))))
        BV = BV + dv_
        EYE[s] = {"c": f_ + EYE_SCALE * (c_ - f_), "r": EYE_SCALE * r_}
    _bvh_un = BVHTree.FromPolygons(_V_unscaled.tolist(), BF)
    _nun = VP.vertex_normals(_V_unscaled, BF)
    if float(np.mean(np.einsum("ij,ij->i", _nun, _V_unscaled - _V_unscaled.mean(0)))) < 0:
        _nun = -_nun
    # the slide applies to the OUTER skin only (normal facing away from the nearer eye's centre; the socket sleeve behind
    # the lids faces the ball and is left to the blend)
    _eyec = np.where((_V_unscaled[:, 0] >= 0)[:, None], EYE_V2["L"]["c"], EYE_V2["R"]["c"])
    _outer = np.einsum("ij,ij->i", _nun, _V_unscaled - _eyec) > 0.0
    _sl = np.nonzero((_slide > 0) & _outer)[0]
    for i in _sl:
        q_ = np.array(_bvh_un.find_nearest(Vector(BV[i]))[0])
        BV[i] = (1.0 - _slide[i]) * BV[i] + _slide[i] * q_
    _moved = np.linalg.norm(BV - _V_unscaled, axis=1)
    # fold proof: the blended map must stay one-to-one -- the radial stretch d(rho')/d(rho) along face-plane rays from
    # the eye centre stays > 0 (sampled on the profile w(d))
    if EYE_ORBIT is None:
        _dd = np.linspace(0.0, EYE_SCALE_ZONE[1] * 1.2e-3, 400)
        _rho = _dd * (1.0 + (EYE_SCALE - 1.0) * (1.0 - smoothstep(EYE_SCALE_ZONE[0] * 1e-3, EYE_SCALE_ZONE[1] * 1e-3, _dd)))
        _fold = [float(np.min(np.diff(_rho) / np.diff(_dd)))]
    EYE_SCALE_INFO.update({"verts_moved": int((_moved > 1e-6).sum()), "max_move_mm": round(1000 * float(_moved.max()), 2),
                           "radial_stretch_min": round(float(min(_fold)), 3),
                           "slid_verts": int(len(_sl)), "slide_mm": EYE_SLIDE,
                           "eyeball_r_mm": {"v2": round(1000 * EYE_V2["L"]["r"], 2), "v3": round(1000 * EYE["L"]["r"], 2)},
                           "eyeball_centre_moved_back_mm": round(1000 * float(EYE["L"]["c"][1] - EYE_V2["L"]["c"][1]), 2)})
    # v4 leak proof: SHAPE deviation from the v2 (unscaled) surface -- every moved vertex's distance to the original
    # surface (a vertex sliding along the surface is no shape change) -- in fixed anatomical zones (nose, nose bridge,
    # side of the face, temple, cheek, forehead) and over everything outside the socket core
    _ec = EYE_V2["L"]["c"]
    _sd = np.zeros(len(BV))
    for i in np.nonzero(_moved > 1e-7)[0]:
        _sd[i] = _bvh_un.find_nearest(Vector(BV[i]))[3]
    _sd *= 1000
    _Xu, _Yu, _Zu = _V_unscaled[:, 0], _V_unscaled[:, 1], _V_unscaled[:, 2]
    _hd = np.isin(np.array([MB[j] for j in np.argmax(BW, 1)], dtype=object), ["head"])
    _fr = _Yu < _ec[1] + 0.01
    LEAK_ZONES = {"nose": _hd & (np.abs(_Xu) < 0.012) & (_Zu > _ec[2] - 0.040) & (_Zu < _ec[2] + 0.006) & _fr,
                  "nose_bridge": _hd & (np.abs(_Xu) < 0.009) & (_Zu > _ec[2] - 0.012) & (_Zu < _ec[2] + 0.006) & _fr,
                  "side": _hd & (np.abs(_Xu) > 0.052) & (np.abs(_Zu - _ec[2]) < 0.030) & _outer,
                  "temple": _hd & (np.abs(_Xu) > 0.056) & (_Zu > _ec[2]) & (_Zu < _ec[2] + 0.035),
                  "cheek": _hd & (np.abs(_Xu) > 0.014) & (np.abs(_Xu) < 0.055) & (_Zu > _ec[2] - 0.045) & (_Zu < _ec[2] - 0.022) & _fr,
                  "forehead": _hd & (_Zu > _ec[2] + 0.024) & _fr,
                  "outside_socket_core": _hd & _outer & (_core < 0.5)}
    EYE_SCALE_INFO["shape_dev_mm_vs_v2_surface"] = {k: {"verts": int(m.sum()), "max": round(float(_sd[m].max()), 3),
                                                        "p95": round(float(np.percentile(_sd[m], 95)), 3),
                                                        "vertex_move_max": round(1000 * float(_moved[m].max()), 3)}
                                                    for k, m in LEAK_ZONES.items()}
    EYE_SCALE_INFO["shape_dev_rule"] = ("distance (mm) of each vertex after the scale to the unscaled (v2) surface; zones in "
                                        "the unscaled frame; 'outside_socket_core' = outer skin beyond the full-scale core")
report["eye_scale"] = EYE_SCALE_INFO
print("EYESCALE", json.dumps(EYE_SCALE_INFO))


# ---- v4 RELIEF FLATTEN (review-log 2026-09-29 "Wren v4 feedback"): a zone's front surface keeps only a fraction K of its
# offset from the face's own profile WITHOUT the feature (profile_flatten: per vertical column a fit through anchor bands
# above and below the feature): K = 0 in the zone core = flat, fading to 1 at the border. Only depth (y) changes, so the
# lid edges, the seal overlap and the seam's position stay put; the offsets scale monotonically (no crossings).
# (Tried first and dropped, measured: a Laplacian membrane over the zone -- isotropic it flattened the face's horizontal
# curvature and sank the zone 3-7 mm; vertical-only it still anchored on the under-chin and receded the chin.)
def front_mask(V, F, cand, tol):
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    out_ = np.zeros(len(V), bool)
    for i in np.nonzero(cand)[0]:
        h_ = bvh_.ray_cast(Vector((float(V[i, 0]), -1.0, float(V[i, 2]))), Vector((0.0, 1.0, 0.0)), 2.0)
        out_[i] = h_[0] is not None and V[i, 1] <= h_[0][1] + tol
    return out_


def profile_flatten(V, F, xs, zc, anc_up, anc_dn, m, front, K, deg=1):
    """v4 relief flatten along the face's VERTICAL profile: per column x (xs, reference height zc(x)) the front-ray
    profile y(dz) is fitted (a line, deg = 1: a quadratic sagged into the gap and kept a crease) through two ANCHOR bands only -- dz in anc_up (above the feature)
    and anc_dn (below it), metres relative to zc -- i.e. the face as it would run without the feature; every front vertex
    in the zone then keeps K x its offset from that fit (m = zone weight: 1 core .. 0 border). Only y changes (the lid
    edge, the lip seal overlap and the seam's position stay put; offsets scale monotonically: no crossings); the face's
    horizontal curvature is untouched (each column is its own fit). -> V, per-vertex fit y, per-column fit residual."""
    V = V.copy()
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    co_, res_ = [], []
    for x_, z0_ in zip(xs, zc):
        dzs_ = np.arange(anc_dn[0], anc_up[1] + 1e-9, 0.00025)
        ys_ = np.array([(lambda h: h[0][1] if h[0] is not None else np.nan)(bvh_.ray_cast(Vector((float(x_), -1.0, float(z0_ + d_))),
                                                                                          Vector((0.0, 1.0, 0.0)), 2.0)) for d_ in dzs_])
        a_ = (((dzs_ >= anc_up[0]) & (dzs_ <= anc_up[1])) | ((dzs_ >= anc_dn[0]) & (dzs_ <= anc_dn[1]))) & ~np.isnan(ys_)
        c_ = np.polyfit(dzs_[a_], ys_[a_], deg)
        co_.append(c_)
        res_.append(float(np.abs(np.polyval(c_, dzs_[a_]) - ys_[a_]).max()))
    co_ = np.array(co_)
    upd_ = (m > 0) & front
    zc_v = np.interp(V[:, 0], xs, zc)
    cv_ = np.stack([np.interp(V[:, 0], xs, co_[:, k]) for k in range(deg + 1)], 1)
    yfit_ = sum(cv_[:, k] * (V[:, 2] - zc_v) ** (deg - k) for k in range(deg + 1))
    V[:, 1] = np.where(upd_, yfit_ + (1.0 - (1.0 - K) * m) * (V[:, 1] - yfit_), V[:, 1])
    return V, yfit_, res_


def bridge_edges(x):
    """v5 mouth BRIDGE zone edges (m above / below the seam) at x: full height to LIP_BRIDGE[2], narrowing linearly to
    [4] / [5] at [3] (the mouth corners), constant beyond."""
    t_ = np.clip((np.abs(x) - LIP_BRIDGE[2]) / (LIP_BRIDGE[3] - LIP_BRIDGE[2]), 0.0, 1.0)
    return 1e-3 * (LIP_BRIDGE[0] + (LIP_BRIDGE[4] - LIP_BRIDGE[0]) * t_), 1e-3 * (LIP_BRIDGE[1] + (LIP_BRIDGE[5] - LIP_BRIDGE[1]) * t_)


def profile_bridge(V, F, xs, zc, m, front, K):
    """v5 no-lip profile as a BRIDGE per column: the front convex hull of the skin in two anchor bands (LIP_BRIDGE[6] mm
    wide) just above and below the zone -- the nose base / philtrum top and the chin -- evaluated across the zone: one
    straight run of skin from the nose base to the chin, tangent to the chin's convex front (no lip volume, no lower-lip
    rim, no lip-chin sulcus notch). The hull is never behind the skin of either band (no dent at an anchor). Every front
    vertex in the zone keeps K x its offset from it (m = zone weight). Only y changes. -> V, per-vertex profile y, per-
    column (bridge length mm)."""
    V = V.copy()
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    B_ = LIP_BRIDGE[6] * 1e-3
    up_, dn_ = bridge_edges(xs)
    prof_ = []
    for x_, z0_, u_, d_ in zip(xs, zc, up_, dn_):
        dzs_ = np.concatenate([np.arange(-d_ - B_, -d_ + 1e-9, 0.00025), np.arange(u_, u_ + B_ + 1e-9, 0.00025)])
        ys_ = np.array([(lambda h: h[0][1] if h[0] is not None else np.nan)(bvh_.ray_cast(Vector((float(x_), -1.0, float(z0_ + q_))),
                                                                                          Vector((0.0, 1.0, 0.0)), 2.0)) for q_ in dzs_])
        ok_ = ~np.isnan(ys_)
        hull_ = []
        for p_ in np.stack([dzs_[ok_], -ys_[ok_]], 1):
            while len(hull_) >= 2 and np.cross(hull_[-1] - hull_[-2], p_ - hull_[-2]) >= 0:
                hull_.pop()
            hull_.append(p_)
        prof_.append(np.array(hull_))
    # per vertex: the hull of its column pair, linear across x
    fx_ = np.clip((V[:, 0] - xs[0]) / (xs[1] - xs[0]), 0, len(xs) - 1.000001)
    j_ = np.floor(fx_).astype(int); t_ = fx_ - j_
    dzv_ = V[:, 2] - np.interp(V[:, 0], xs, zc)
    yfit_ = V[:, 1].copy()
    for i in np.nonzero((m > 0) & front)[0]:
        h0_, h1_ = prof_[j_[i]], prof_[j_[i] + 1]
        yfit_[i] = -((1 - t_[i]) * np.interp(dzv_[i], h0_[:, 0], h0_[:, 1]) + t_[i] * np.interp(dzv_[i], h1_[:, 0], h1_[:, 1]))
    upd_ = (m > 0) & front
    # the FRONT skin lands on the bridge; a vertex b behind the front lands K x b behind it: within LIP_HIDDEN_K[2] of the
    # seam (the rims rolled into the sealed seam) K = LIP_HIDDEN_K[0] -- 0 folds each rim flat onto the bridge (its rolled
    # faces then face backward: culled, and left out of the bake high), so the seam past the drawn line is two flat sheets
    # meeting, no steep fold faces to catch the light; elsewhere (the lips' inner faces) K = LIP_HIDDEN_K[1]: the layers
    # keep their order
    b_ = np.zeros(len(V))
    for i in np.nonzero(upd_)[0]:
        h_ = bvh_.ray_cast(Vector((float(V[i, 0]), -1.0, float(V[i, 2]))), Vector((0.0, 1.0, 0.0)), 2.0)
        b_[i] = max(float(V[i, 1]) - h_[0][1], 0.0) if h_[0] is not None else 0.0
    kh_ = np.where(np.abs(dzv_) <= LIP_HIDDEN_K[2], LIP_HIDDEN_K[0], LIP_HIDDEN_K[1])
    # (a hidden vertex never lands closer than LIP_HIDDEN_K[3] behind the bridge: a folded rim exactly ON it z-fought
    # through the front skin wherever the fold turned forward again, rendered)
    tgt_ = yfit_ + np.where(b_ > 0.5 * LIP_HIDDEN_K[3], np.maximum(kh_ * b_, LIP_HIDDEN_K[3]), 0.0)
    V[:, 1] = np.where(upd_, V[:, 1] + (1.0 - K) * m * (tgt_ - V[:, 1]), V[:, 1])
    return V, yfit_, [round(1000 * float(u_ + d_), 1) for u_, d_ in zip(up_, dn_)]


def eye_aperture_s1(s, V, F, n=72):
    """the lid opening per polar angle (front rays: the skin in front of the analytic eyeball sphere) -- s1's own
    aperture for the under-eye zone (s2 re-measures it on the lathe ball)."""
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    c_, r_ = EYE[s]["c"], EYE[s]["r"]; sg_ = 1.0 if s == "L" else -1.0
    ap_ = np.zeros(n)
    for k in range(n):
        th_ = math.radians(360.0 * k / n)
        for i in range(1, 300):
            rho = i * 0.0001
            x_, z_ = c_[0] + sg_ * rho * math.cos(th_), c_[2] + rho * math.sin(th_)
            q_ = r_ ** 2 - (x_ - c_[0]) ** 2 - (z_ - c_[2]) ** 2
            h_ = bvh_.ray_cast(Vector((x_, -1.0, z_)), Vector((0.0, 1.0, 0.0)), 2.0)
            if q_ <= 0 or (h_[0] is not None and h_[0][1] < c_[1] - math.sqrt(q_) - 1e-6):
                ap_[k] = rho
                break
    return ap_


def undereye_profile(V, F, s, ap_down):
    """crease depth under the lower lid: front-ray profiles y(z) down columns through the eye (x = centre + -8 .. +8 mm),
    from 1 mm below the lid edge to 26 mm below the centre; depth = how far the profile sinks behind its own front convex
    hull (a crease / bag reads as a dent in that hull). -> per column depth (mm) + the max."""
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    out_ = {}
    for dx_ in (-8.0, -4.0, 0.0, 4.0, 8.0):
        x_ = c_[0] + sg_ * dx_ * 1e-3
        zs_ = np.arange(c_[2] - ap_down - 0.001, c_[2] - 0.026, -0.00025)
        ys_ = np.array([(lambda h: -h[0][1] if h[0] is not None else np.nan)(bvh_.ray_cast(Vector((float(x_), -1.0, float(z_))),
                                                                                          Vector((0.0, 1.0, 0.0)), 2.0)) for z_ in zs_])
        ok_ = ~np.isnan(ys_)
        P_ = np.stack([zs_[ok_], ys_[ok_]], 1)[::-1]            # (z ascending, forwardness)
        hull_ = []
        for p_ in P_:                                            # the upper (front) hull, monotone chain
            while len(hull_) >= 2 and np.cross(hull_[-1] - hull_[-2], p_ - hull_[-2]) >= 0:
                hull_.pop()
            hull_.append(p_)
        hull_ = np.array(hull_)
        hy_ = np.interp(P_[:, 0], hull_[:, 0], hull_[:, 1])
        k_ = int(np.argmax(hy_ - P_[:, 1]))
        out_["dx%+d" % dx_] = round(1000 * float((hy_ - P_[:, 1])[k_]), 3)
        out_["dx%+d_at_dz_mm" % dx_] = round(1000 * float(P_[k_, 0] - c_[2]), 1)
    out_["max"] = max(v_ for k_, v_ in out_.items() if not k_.endswith("_mm"))
    return out_


EYE_BAG_INFO = {"rule": "front-ray profiles down 5 columns under each eye; depth = max sink behind the profile's front convex hull (mm)"}
_ap_dn = {}
for s in "LR":
    _ap = eye_aperture_s1(s, BV, BF)
    _ap_dn[s] = float(np.interp(270.0, 360.0 * np.arange(72) / 72, _ap, period=360.0))
EYE_BAG_INFO["before"] = {s: undereye_profile(BV, BF, s, _ap_dn[s]) for s in "LR"}
if EYE_BAG_FLAT is not None:
    # the zone: under each lower lid, EYE_BAG_FLAT[0] .. [1] below the lid edge at full weight (fading in over 1.5 mm
    # above and out over EYE_BAG_FLAT[2] below), across EYE_BAG_FLAT[3] deg either side of straight down (fading 25 deg)
    _m = np.zeros(len(BV))
    for s in "LR":
        c_ = EYE[s]["c"]
        rad_ = np.hypot(BV[:, 0] - c_[0], BV[:, 2] - c_[2])
        ang_ = np.degrees(np.arctan2(BV[:, 2] - c_[2], (BV[:, 0] - c_[0]) * (1.0 if s == "L" else -1.0))) % 360.0
        dang_ = np.abs(ang_ - 270.0)
        lo_ = _ap_dn[s] + EYE_BAG_FLAT[0] * 1e-3
        hi_ = _ap_dn[s] + EYE_BAG_FLAT[1] * 1e-3
        w_ = smoothstep(max(lo_ - 0.0015, _ap_dn[s] + 0.0002), lo_, rad_) *(1.0 - smoothstep(hi_, hi_ + EYE_BAG_FLAT[2] * 1e-3, rad_)) * \
            (1.0 - smoothstep(EYE_BAG_FLAT[3], EYE_BAG_FLAT[3] + 25.0, dang_)) * (BV[:, 1] < c_[1])
        _m = np.maximum(_m, w_)
    _front = front_mask(BV, BF, _m > 0, 0.0015)
    _y0 = BV[:, 1].copy()
    _fitres = {}
    for s in "LR":
        # anchors: the lower-lid margin band just under the opening, and the cheek below the zone
        c_ = EYE[s]["c"]
        _xs = np.arange(c_[0] - 0.026, c_[0] + 0.0261, 0.001)
        _au = (-(_ap_dn[s] + EYE_BAG_FLAT[5][1] * 1e-3), -(_ap_dn[s] + EYE_BAG_FLAT[5][0] * 1e-3))
        _ad = (-(_ap_dn[s] + EYE_BAG_FLAT[5][3] * 1e-3), -(_ap_dn[s] + EYE_BAG_FLAT[5][2] * 1e-3))
        _ms = _m * (np.sign(BV[:, 0]) == (1.0 if s == "L" else -1.0))
        BV, _, _r = profile_flatten(BV, BF, _xs, np.full(len(_xs), c_[2]), _au, _ad, _ms, _front, EYE_BAG_FLAT[4])
        _fitres[s] = round(1000 * float(np.median(_r)), 3)
    EYE_BAG_INFO.update({"zone_mm_below_lid": EYE_BAG_FLAT[:3], "half_angle_deg": EYE_BAG_FLAT[3], "K": EYE_BAG_FLAT[4],
                         "anchors_mm_below_lid": EYE_BAG_FLAT[5], "anchor_fit_residual_mm_median": _fitres,
                         "verts": int(((_m > 0) & _front).sum()),
                         "max_move_mm": round(1000 * float(np.abs(BV[:, 1] - _y0).max()), 3),
                         "after": {s: undereye_profile(BV, BF, s, _ap_dn[s]) for s in "LR"}})


# ---- v5 UNDER-EYE HULL FILL (review-log 2026-09-29 "Wren v5 feedback + FE reference set": "we still have the eye bags"; the
# FE face has NO under-eye geometry -- the skin runs flat from the lower lash line to the cheek). Measured on v4: under the
# lateral half of each eye the lower lid stands up to 2.7 mm PROUD of a trough 10-15 mm below it (the x1.30 socket scale about
# the cornea kept the lid at the cornea's depth while the cheek stayed put), and the lid margin bulges 0.3-1.4 mm in front of
# the lid edge over its first mm (the "thin lower-lid line"). Per column under the lid the skin is replaced by the FRONT
# CONVEX HULL of the lash-line point and the cheek below the bag (EYE_BAG_FILL[1] .. [2] mm under the lid edge): one
# straight run of skin from the lash line, tangent to the convex cheek where it meets it (no border crease); everything in
# between -- the margin roll, the bag bulge, the trough -- lands on it (set back or filled). Only y moves (the lid edge and
# the aperture stay put); the move is a displacement FIELD applied to every vertex up to EYE_BAG_FILL[4] mm behind the
# front surface, so hidden layers (the lid's inner face, the socket sleeve) keep their order.
def eye_ball_bvh(s):
    """the RENDERED eyeball's surface (s2's lathe: the same ring angles, EYE_SEG segments) as a BVH, so the fill's lid edge
    is s2's aperture rule exactly. (The analytic sphere stands up to r(1 - cos 15 deg) = 0.75 mm in front of the lathe's
    chords between the 60 and 90 deg rings, where the lower lid rests; measured, the two rules gave the same fill here --
    the lid edge per column agrees with the probe's rendered-ball edge within 0.1 mm.)"""
    e_ = EYE[s]
    im_ = 0.5 * (EYE_PUPIL_DEG + 4 + EYE_IRIS_DEG)
    angs_ = [0.0, EYE_PUPIL_DEG, EYE_PUPIL_DEG + 4, im_, EYE_IRIS_DEG, max(EYE_IRIS_DEG + 10.0, 60.0), 90.0] + \
        ([125.0, 155.0, 180.0] if EYE_BACK is None else [])
    if EYE_HILITE is not None:
        hc_ = EYE_HILITE[1] * EYE_IRIS_DEG
        angs_ += [a_ for a_ in np.arange(max(hc_ - EYE_HILITE[2] - 1.0, 1.0), hc_ + EYE_HILITE[2] + 1.0, EYE_HILITE_RING)
                  if min(abs(a_ - b_) for b_ in angs_) > 0.6]
        angs_ = sorted(float(a_) for a_ in angs_)
    prof_ = [(e_["r"] * math.sin(math.radians(a)), e_["r"] * math.cos(math.radians(a))) for a in angs_]
    if EYE_BACK is not None:
        prof_.append((0.0, e_["r"] * math.cos(math.radians(EYE_BACK))))
    Ve_, Fe_, _ = VP.lathe(prof_, ["eye_sclera"] * (len(prof_) - 1), EYE_SEG, e_["c"], (0.0, -1.0, 0.0), up_hint=(0, 0, 1))
    return BVHTree.FromPolygons(np.asarray(Ve_).tolist(), Fe_)


def lid_columns(V, F, s, xs_mm, step=0.0001):
    """per column (mm from the eye centre, + = outer) the lid edge under the opening: scanning down from the eye centre's
    height, the first row whose first front hit is SKIN rather than the rendered eyeball (s2's aperture rule); NaN where
    the column has no opening at that height."""
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    be_ = eye_ball_bvh(s)
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    out_ = np.full(len(xs_mm), np.nan)
    for j, dx_ in enumerate(xs_mm):
        x_ = c_[0] + sg_ * dx_ * 1e-3
        opened_ = False
        for i in range(0, 300):
            z_ = c_[2] - i * step
            hs_ = bvh_.ray_cast(Vector((x_, -1.0, z_)), Vector((0.0, 1.0, 0.0)), 2.0)
            he_ = be_.ray_cast(Vector((x_, -1.0, z_)), Vector((0.0, 1.0, 0.0)), 2.0)
            if he_[0] is None and not opened_:
                break
            skin_ = hs_[0] is not None and (he_[0] is None or hs_[3] < he_[3] - 1e-6)
            if not skin_:
                opened_ = True
            elif opened_:
                out_[j] = z_
                break
            elif i == 0:
                break
    return out_


def hull_fill(V, F, s, P):
    """one pass of the under-eye hull fill for eye s (P = EYE_BAG_FILL) -> V, info."""
    c_ = EYE[s]["c"]; sg_ = 1.0 if s == "L" else -1.0
    xs_ = np.arange(-18.0, 26.01, 0.5)
    zl_ = lid_columns(V, F, s, xs_)
    ok_ = ~np.isnan(zl_)
    j_ok = np.nonzero(ok_)[0]
    # (one contiguous run of lid columns: the lower lid from the inner to the outer corner; past each corner the columns
    # continue at the corner's lid height for P[3] mm, fading out: the orbit hollow under the outer corner is part of the
    # bag -- measured 2.4-2.9 mm deep at the outer corner with the fade inside the lid run)
    jl0_, jl1_ = int(j_ok.min()), int(j_ok.max())
    ext_ = int(round(P[3] / 0.5))
    j0_, j1_ = max(jl0_ - ext_, 0), min(jl1_ + ext_, len(xs_) - 1)
    zl_ = np.interp(np.arange(len(xs_)), j_ok, zl_[j_ok])
    ds_ = np.arange(0.0, P[2] + 1e-6, 0.25)                      # mm under the lid edge (P[0] is a multiple of the step)
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    Y_ = np.full((len(ds_), len(xs_)), np.nan)
    DL_ = np.zeros(Y_.shape)
    sink_ = []
    for j in range(j0_, j1_ + 1):
        x_ = c_[0] + sg_ * xs_[j] * 1e-3
        for i, d_ in enumerate(ds_):
            h_ = bvh_.ray_cast(Vector((x_, -1.0, float(zl_[j] - d_ * 1e-3))), Vector((0.0, 1.0, 0.0)), 2.0)
            Y_[i, j] = h_[0][1] if h_[0] is not None else np.nan
        col_ = Y_[:, j]
        use_ = ~np.isnan(col_) & ((np.abs(ds_ - P[0]) < 1e-6) | (ds_ >= P[1]))
        pts_ = np.stack([ds_[use_], -col_[use_]], 1)
        hull_ = []
        for p_ in pts_:                                          # the FRONT (upper) hull, d ascending
            while len(hull_) >= 2 and np.cross(hull_[-1] - hull_[-2], p_ - hull_[-2]) >= 0:
                hull_.pop()
            hull_.append(p_)
        hull_ = np.array(hull_)
        good_ = ~np.isnan(col_) & (ds_ >= P[0])
        fh_ = np.interp(ds_, hull_[:, 0], hull_[:, 1])
        DL_[good_, j] = -(fh_[good_] + col_[good_])               # target y - y (< 0 = forward: the fill)
        sink_.append(float(np.max(np.where(good_ & (ds_ >= P[1]), fh_ + col_, 0.0))))
    # corner fade: full over the lid run, out over the P[3] mm past either corner
    wc_ = np.zeros(len(xs_))
    wc_[j0_:j1_ + 1] = (1.0 - smoothstep(0.0, P[3], xs_[jl0_] - xs_[j0_:j1_ + 1])) * \
        (1.0 - smoothstep(0.0, P[3], xs_[j0_:j1_ + 1] - xs_[jl1_]))
    DL_ *= wc_[None, :]
    # apply: bilinear on (column, depth-under-the-lid) to every head vertex in front of the eye centre within the band
    dxv_ = sg_ * (V[:, 0] - c_[0]) * 1e3
    fj_ = (dxv_ - xs_[0]) / 0.5
    in_ = (fj_ >= j0_) & (fj_ <= j1_) & (V[:, 1] < c_[1]) & HEAD_V
    fj_c = np.clip(fj_, 0, len(xs_) - 1.000001)
    jlo_ = np.floor(fj_c).astype(int); tj_ = fj_c - jlo_
    zlv_ = zl_[jlo_] * (1 - tj_) + zl_[jlo_ + 1] * tj_
    dv_ = (zlv_ - V[:, 2]) * 1e3
    in_ &= (dv_ >= 0.0) & (dv_ <= P[2])
    fi_ = np.clip(dv_ / 0.25, 0, len(ds_) - 1.000001)
    ilo_ = np.floor(fi_).astype(int); ti_ = fi_ - ilo_

    def bil(G_):
        g_ = np.nan_to_num(G_)
        return (g_[ilo_, jlo_] * (1 - ti_) * (1 - tj_) + g_[ilo_ + 1, jlo_] * ti_ * (1 - tj_) +
                g_[ilo_, jlo_ + 1] * (1 - ti_) * tj_ + g_[ilo_ + 1, jlo_ + 1] * ti_ * tj_)
    surf_ = bil(np.where(np.isnan(Y_), np.nanmax(Y_), Y_))
    mv_ = in_ & (V[:, 1] <= surf_ + P[4] * 1e-3)
    dl_ = bil(DL_)
    V = V.copy()
    V[mv_, 1] += dl_[mv_]
    return V, {"lid_columns_mm": [float(xs_[jl0_]), float(xs_[jl1_])], "fill_columns_mm": [float(xs_[j0_]), float(xs_[j1_])],
               "verts": int(mv_.sum()),
               "lid_edge_mm_below_centre": {"dx%+d" % d_: round(1000 * float(c_[2] - np.interp(d_, xs_, zl_)), 2)
                                            for d_ in (-8, 0, 8, 16)},
               "fill_forward_mm_max": round(-1000 * float(dl_[mv_].min()), 3) if mv_.any() else 0.0,
               "set_back_mm_max": round(1000 * float(dl_[mv_].max()), 3) if mv_.any() else 0.0,
               "hull_sink_mm_before_max": round(1000 * max(sink_), 3)}


HEAD_V = np.isin(np.array([MB[j] for j in np.argmax(BW, 1)], dtype=object), ["head"])
BV_POST_SCALE = BV.copy()                              # (v5: the v5 face ops' moves are measured from here, per orbit zone)
if EYE_BAG_FILL is not None:
    _y0 = BV[:, 1].copy()
    EYE_BAG_INFO["fill"] = {"rule": "per column under each lower lid: the skin -> the front convex hull of the lash-line point "
                                    "(%.2f mm under the lid edge) and the cheek (%.1f .. %.0f mm under it); continued %.0f mm past each lid corner, fading; "
                                    "vertices up to %.1f mm behind the front move with it" % tuple(EYE_BAG_FILL[:5]),
                            "passes": []}
    for _pass in range(EYE_BAG_FILL[5]):
        _pi = {}
        for s in "LR":
            BV, _pi[s] = hull_fill(BV, BF, s, EYE_BAG_FILL)
        EYE_BAG_INFO["fill"]["passes"].append(_pi)
    EYE_BAG_INFO["fill"]["max_move_mm"] = round(1000 * float(np.abs(BV[:, 1] - _y0).max()), 3)
    EYE_BAG_INFO["after"] = {s: undereye_profile(BV, BF, s, _ap_dn[s]) for s in "LR"}
report["undereye"] = EYE_BAG_INFO
print("UNDEREYE", json.dumps(EYE_BAG_INFO))


def lip_pairing(bvh_, z_slit, xs_mm=(0.0, 4.0, 8.0, 12.0)):
    """mouth proof (v2): per column x the front-ray profile across the seam: how far each lip stands proud of the seam
    (upper_fwd / lower_fwd), the lower lip's lead over the upper (lower_lead: v1's mismatch), whether rays pass between
    the lips (open), and the lip-to-lip vertical span of the proud parts (each lip's height where it stands >= 0.5 mm
    proud of the seam)."""
    out = {}
    zs_ = np.arange(z_slit - 0.012, z_slit + 0.012, 0.0001)
    for xm in xs_mm:
        ys_ = np.array([(lambda h: h[0][1] if h[0] is not None else np.nan)(bvh_.ray_cast(Vector((xm * 0.001, -0.6, float(z_))),
                                                                                           Vector((0.0, 1.0, 0.0)), 1.2)) for z_ in zs_])
        zs0_ = float(np.interp(xm * 0.001, SEAM["xs"], SEAM["zc"])) if SEAM is not None else z_slit
        near_ = np.abs(zs_ - zs0_) <= (0.0015 if SEAM is not None else 0.003)
        i_s = int(np.nanargmax(np.where(near_, ys_, -9.0)))
        ys_s = float(ys_[i_s])
        upm = (zs_ > zs_[i_s]) & (zs_ < zs_[i_s] + 0.008); lom = (zs_ < zs_[i_s]) & (zs_ > zs_[i_s] - 0.008)
        yu, yl = float(np.nanmin(ys_[upm])), float(np.nanmin(ys_[lom]))
        front_ = min(yu, yl)
        prd_u = upm & (ys_ < ys_s - 0.0005) & (np.abs(zs_ - zs_[i_s]) < 0.010)
        prd_l = lom & (ys_ < ys_s - 0.0005) & (np.abs(zs_ - zs_[i_s]) < 0.010)
        out["x%+d" % xm] = {"seam_z_offset_mm": round(1000 * float(zs_[i_s] - z_slit), 2),
                            "upper_fwd_mm": round(1000 * (ys_s - yu), 2), "lower_fwd_mm": round(1000 * (ys_s - yl), 2),
                            "lower_lead_mm": round(1000 * (yu - yl), 2),
                            "open_through_mm": round(1000 * (ys_s - front_), 2) if ys_s - front_ > 0.006 else 0.0,
                            "upper_proud_h_mm": round(0.1 * float(prd_u.sum()), 1), "lower_proud_h_mm": round(0.1 * float(prd_l.sum()), 1)}
    return out


# ---- v4 2D-ANIME MOUTH (review-log 2026-09-29 "Wren v4 feedback": thin, barely-there lips; the mouth reads as a drawn
# smirk LINE, not modelled lip volume). The sealed lips' relief is flattened toward the no-lip profile (profile_flatten,
# anchors LIP_FLAT[5] wide just above / below the zone): in the zone LIP_FLAT[0] half width x LIP_FLAT[1] above / [2]
# below the seam (fading over LIP_FLAT[3] at the border) the front surface keeps LIP_FLAT[4] of its relief. The seal stays closed (only depth changes); the seam is re-traced afterwards.
LIP_FLAT_INFO = None
if LIP_FLAT is not None and SEAM is not None:
    _zsl = float(np.interp(0.0, SEAM["xs"], SEAM["zc"]))
    _bvh0 = BVHTree.FromPolygons(BV.tolist(), BF)
    LIP_FLAT_INFO = {"before": lip_pairing(_bvh0, _zsl)}
    _zc_x = np.interp(BV[:, 0], SEAM["xs"], SEAM["zc"])
    _dz = BV[:, 2] - _zc_x
    _hz = np.where(_dz >= 0, LIP_FLAT[1], LIP_FLAT[2])
    _u = np.maximum(np.abs(BV[:, 0]) / LIP_FLAT[0], np.abs(_dz) / _hz)
    _yfr = float(BV[(np.abs(BV[:, 0]) < 0.005) & (np.abs(BV[:, 2] - _zsl) < 0.006), 1].min())   # the lips' front
    _m = (1.0 - smoothstep(1.0, 1.0 + LIP_FLAT[3] / min(LIP_FLAT[:3]), _u)) * (BV[:, 1] < _yfr + 0.014) * \
        (np.abs(BV[:, 0]) < LIP_FLAT[0] * 1.6)
    _front = front_mask(BV, BF, _m > 0, LIP_FRONT_TOL)    # (the lip rims tucked into the seam crease count: they ARE the crease)
    _y0 = BV[:, 1].copy()
    _xs = np.arange(-LIP_FLAT[0] * 1.6, LIP_FLAT[0] * 1.6 + 1e-4, 0.001)
    _au = (LIP_FLAT[1], LIP_FLAT[1] + LIP_FLAT[5])
    _ad = (-(LIP_FLAT[2] + LIP_FLAT[5]), -LIP_FLAT[2])
    if LIP_PROFILE == "bridge":
        # v5: the lens zone between the bridge edges (full height over the middle, narrowing to the corners), faded in
        # over LIP_BRIDGE[8] mm inside its top / bottom edges and out over LIP_BRIDGE[7] mm past the corners
        _eu, _ed = bridge_edges(BV[:, 0])
        _fz = LIP_BRIDGE[8] * 1e-3
        _m = (1.0 - smoothstep(LIP_BRIDGE[3] * 1e-3, (LIP_BRIDGE[3] + LIP_BRIDGE[7]) * 1e-3, np.abs(BV[:, 0]))) * \
            smoothstep(0.0, _fz, _eu - _dz) * smoothstep(0.0, _fz, _dz + _ed) * (BV[:, 1] < _yfr + 0.025)   # (the face
        #   wraps back 14+ mm by the mouth corners: v4's 14 mm depth gate cut the zone at |x| = 20 mm; the front mask
        #   excludes the mouth interior)
        _front = front_mask(BV, BF, _m > 0, LIP_FRONT_TOL)
        _xs = np.arange(-(LIP_BRIDGE[3] + LIP_BRIDGE[7]) * 1e-3 - 0.001, (LIP_BRIDGE[3] + LIP_BRIDGE[7]) * 1e-3 + 0.0011, 0.0005)
        BV, _ys, _r = profile_bridge(BV, BF, _xs, np.interp(_xs, SEAM["xs"], SEAM["zc"]), np.where(_front, _m, 0.0),
                                     _front, LIP_FLAT[4])
        _r = [0.0]                                             # (no anchor fit: the hull passes through the bands)
    else:
        BV, _ys, _r = profile_flatten(BV, BF, _xs, np.interp(_xs, SEAM["xs"], SEAM["zc"]), _au, _ad, np.where(_front, _m, 0.0),
                                      _front, LIP_FLAT[4])
    if LIP_RIM_STEP and LIP_SIDE is not None:
        # the two sealed rims keep a consistent order after the flatten (upper lip in front by LIP_RIM_STEP): with the
        # relief scaled down their facets interleaved into a sawtooth along the seam
        _dzr = BV[:, 2] - np.interp(BV[:, 0], SEAM["xs"], SEAM["zc"])
        _dzs = np.abs(_dzr)
        _rs = (1.0 - smoothstep(0.0006, 0.0025, _dzs)) * (_m > 0) * _front
        BV[:, 1] -= 0.5 * LIP_RIM_STEP * _rs * LIP_SIDE
        if LIP_RIM_TUCK is not None:
            # v6.1 RIM TUCK: each sheet's overhang past the seam goes BEHIND the other sheet. Measured on v6: the upper
            # lip's rolled rim hung 1.2-1.5 mm below the seam, 0.06-0.3 mm IN FRONT of the lower sheet, tilted 13 deg up --
            # lit slivers with a soft shadow under their edge (v6's "diamonds"; bigger once the lower lip rises). Every
            # upper-sheet vertex more than LIP_RIM_TUCK[0] below the seam (and lower-sheet vertex above it) lands
            # LIP_RIM_TUCK[1] behind the other sheet's surface there (front rays against that sheet's faces only)
            # (the UPPER sheet only, over the inner LIP_RIM_TUCK[2] of the seam's half-width: tucking the lower sheet's top
            # too, or at the corners, broke the line into notches and folded the corners, rendered)
            _tk = {}
            _hw = 0.5 * float(SEAM["xs"].max() - SEAM["xs"].min())
            for _sd in LIP_RIM_TUCK_SIDES:
                _Fo = [f for f in BF if (LIP_SIDE[f] == -_sd).any() and not (LIP_SIDE[f] == _sd).any()]
                _bo = BVHTree.FromPolygons(BV.tolist(), _Fo)
                _vi = np.nonzero((LIP_SIDE == _sd) & (_sd * _dzr < -LIP_RIM_TUCK[0]) & (_dzs < 0.004) &
                                 (np.abs(BV[:, 0]) < LIP_RIM_TUCK[2] * _hw))[0]
                _n = 0
                for _i in _vi:
                    _h = _bo.ray_cast(Vector((float(BV[_i, 0]), -1.0, float(BV[_i, 2]))), Vector((0.0, 1.0, 0.0)), 2.0)
                    if _h[0] is not None and abs(_h[0][1] - BV[_i, 1]) < 0.003 and BV[_i, 1] < _h[0][1] + LIP_RIM_TUCK[1]:
                        BV[_i, 1] = _h[0][1] + LIP_RIM_TUCK[1]; _n += 1
                _tk["upper_below_seam" if _sd > 0 else "lower_above_seam"] = {"candidates": int(len(_vi)), "tucked": _n}
            report["lip_rim_tuck"] = _tk
            print("RIMTUCK", json.dumps(_tk))
    # (the seam's height is kept from the seal: only y moved; re-tracing "the most recessed hit" after the flatten slid
    # 1.4 mm below the visible crease, measured -- the drawn line must sit ON it)
    _bvh1 = BVHTree.FromPolygons(BV.tolist(), BF)
    _zsl = float(np.interp(0.0, SEAM["xs"], SEAM["zc"]))
    _top, _bot = lip_gap(BV, BF, _zsl, np.arange(-0.030, 0.0301, 0.0005), LIP_SEAL[2])
    _zm = (_m > 0.99) & _front
    LIP_FLAT_INFO.update({"zone_m": LIP_FLAT[:4], "K": LIP_FLAT[4], "anchor_band_m": LIP_FLAT[5],
                          "anchor_fit_residual_mm_median": round(1000 * float(np.median(_r)), 3),
                          "verts": int(((_m > 0) & _front).sum()),
                          "max_move_mm": round(1000 * float(np.abs(BV[:, 1] - _y0).max()), 3),
                          "relief_vs_fit_mm": {"before_max": round(1000 * float(np.max(np.abs(_ys[_zm] - _y0[_zm]))), 3) if _zm.any() else None,
                                               "after_max": round(1000 * float(np.max(np.abs(_ys[_zm] - BV[_zm, 1]))), 3) if _zm.any() else None,
                                               "rule": "max |offset| of the lip-zone core's front surface from the per-column no-lip "
                                                       "profile fit (proud lips and the seam crease alike)"},
                          "after": lip_pairing(_bvh1, _zsl), "gap_columns_after": int((~np.isnan(_top)).sum())})
report["lip_flatten"] = LIP_FLAT_INFO
print("LIPFLAT", json.dumps(LIP_FLAT_INFO))
if EYE_SCALE != 1.0:
    # v5 leak proof for the new ops: how far the under-eye fill + the mouth bridge moved each v4 orbit-cleanup zone
    # (the zones' shape deviation above is measured at the scale step, before them)
    _mv5 = 1000 * np.linalg.norm(BV - BV_POST_SCALE, axis=1)
    report["eye_scale"]["v5_face_ops_move_mm_by_zone"] = {k: round(float(_mv5[m].max()), 3) for k, m in LEAK_ZONES.items()}
    print("V5MOVE", json.dumps(report["eye_scale"]["v5_face_ops_move_mm_by_zone"]))


def nose_bottom_z(V, F):
    """v6: going down the midline from the nose tip, the end of the nose's underside (front-ray hit normals facing down
    past -0.55) = the lowest edge of the nose in a front view (the drawn nose mark of the FE / anime faces)."""
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    zm_ = np.arange(EYE["L"]["c"][2] - 0.020, EYE["L"]["c"][2] - 0.150, -0.0001)
    hs_ = [bvh_.ray_cast(Vector((0.0, -0.6, float(z_))), Vector((0.0, 1.0, 0.0)), 1.0) for z_ in zm_]
    y_ = np.array([h[0][1] if h[0] is not None else np.nan for h in hs_])
    nz_ = np.array([h[1][2] if h[0] is not None else np.nan for h in hs_])
    i_ = int(np.nanargmin(np.where(zm_ > EYE["L"]["c"][2] - 0.080, y_, np.nan)))
    ztip_ = float(zm_[i_])
    while i_ + 1 < len(zm_) and not (nz_[i_] < -0.55):
        i_ += 1
    while i_ + 1 < len(zm_) and nz_[i_] < -0.55:
        i_ += 1
    return float(zm_[i_]), ztip_


def front_heightfield_smooth(V, F, gx, gz, sigma, z_max):
    """v6: the front heightfield y(x, z) of the mesh on the grid (front rays; valid = a front-facing hit within 2 mm of
    the local median front -- not a ray through the seam's crack -- below z_max), Gaussian-smoothed by normalised
    convolution over the valid samples. -> raw H (nan = invalid), smoothed S, valid mask, weight sum, H filled (the
    invalid samples -- the seam's crack -- filled by a 0.75 mm normalised blur of the valid ones)."""
    bvh_ = BVHTree.FromPolygons(V.tolist(), F)
    gs_ = float(gx[1] - gx[0])
    H_ = np.full((len(gz), len(gx)), np.nan)
    for a_, z_ in enumerate(gz):
        for b_, x_ in enumerate(gx):
            h_ = bvh_.ray_cast(Vector((float(x_), -1.0, float(z_))), Vector((0.0, 1.0, 0.0)), 2.0)
            if h_[0] is not None and abs(h_[1][1]) > 0.2:
                H_[a_, b_] = h_[0][1]
    Hn_ = np.where(np.isnan(H_), 9.0, H_)
    r_ = int(round(0.002 / gs_))
    med_ = np.median(np.lib.stride_tricks.sliding_window_view(np.pad(Hn_, r_, mode="edge"), (2 * r_ + 1, 2 * r_ + 1)), axis=(2, 3))
    val_ = ~np.isnan(H_) & (np.abs(Hn_ - med_) < 0.002) & (gz[:, None] < z_max)
    k_ = np.exp(-0.5 * (np.arange(-int(3 * sigma / gs_), int(3 * sigma / gs_) + 1) * gs_ / sigma) ** 2)

    def blur_(A):
        A = np.apply_along_axis(lambda r: np.convolve(r, k_, mode="same"), 1, A)
        return np.apply_along_axis(lambda c: np.convolve(c, k_, mode="same"), 0, A)
    ws_ = blur_(val_.astype(float))
    S_ = blur_(np.where(val_, H_, 0.0)) / np.maximum(ws_, 1e-9)
    k_ = np.exp(-0.5 * (np.arange(-int(3 * 0.00075 / gs_), int(3 * 0.00075 / gs_) + 1) * gs_ / 0.00075) ** 2)
    f_ = blur_(val_.astype(float))
    Hf_ = np.where(val_, H_, np.where(f_ > 1e-3, blur_(np.where(val_, H_, 0.0)) / np.maximum(f_, 1e-9), np.nan))
    return H_, S_, val_, ws_, Hf_


def grid_bilinear(A, gx, gz, x_, z_):
    gs_ = float(gx[1] - gx[0])
    ix_ = np.clip((x_ - gx[0]) / gs_, 0, len(gx) - 1.000001); iz_ = np.clip((z_ - gz[0]) / gs_, 0, len(gz) - 1.000001)
    j_, i_ = np.floor(ix_).astype(int), np.floor(iz_).astype(int); tx_, tz_ = ix_ - j_, iz_ - i_
    return (A[i_, j_] * (1 - tx_) * (1 - tz_) + A[i_, j_ + 1] * tx_ * (1 - tz_) + A[i_ + 1, j_] * (1 - tx_) * tz_ +
            A[i_ + 1, j_ + 1] * tx_ * tz_)


MOUTH_SMOOTH_INFO = None
if MOUTH_SMOOTH is not None and SEAM is not None:
    # v6 SMOOTH MOUTH SKIN (review-log 2026-09-29 "Wren v6 mouth feedback": nose to chin reads as smooth uninterrupted
    # skin): the mouth zone's front skin moves onto its own Gaussian-smoothed front heightfield (MOUTH_SMOOTH: full over
    # the zone core, faded out over the border; hidden layers up to the 7th mm behind move with it, none past the 8th --
    # every layer keeps its order and the upper rim its LIP_RIM_STEP lead). The bridge zone's edge kinks, the seam's rim
    # strip (tilted 5 deg down, measured on v5.1) and the lip-corner folds become one smooth surface; the line's iso-cut
    # slivers then lie in their parent facets' planes (same tangent frame as their neighbours: a sliver too small to own a
    # texel no longer decodes a neighbour's texel into a wrong normal -- the dark band along the seam, rendered).
    _sg, _X0, _U0, _D0, _FD, _NC, _DF, _DN = [v_ * 1e-3 for v_ in MOUTH_SMOOTH]
    Z_NOSE_BOTTOM_S1, _ = nose_bottom_z(BV, BF)
    _zs0 = float(np.interp(0.0, SEAM["xs"], SEAM["zc"]))
    _pad = _FD + 3.0 * _sg
    _gx = np.arange(-(_X0 + _pad), _X0 + _pad + 1e-9, 0.0005)
    _gz = np.arange(_zs0 - (_D0 + _pad), _zs0 + _U0 + _pad + 1e-9, 0.0005)
    _H, _S, _val, _ws, _Hf = front_heightfield_smooth(BV, BF, _gx, _gz, _sg, Z_NOSE_BOTTOM_S1 - _NC)
    _okg = ~np.isnan(_Hf) & (_ws > 0.5 * float(_ws.max())) & (_gz[:, None] < Z_NOSE_BOTTOM_S1 - _NC)
    _Dg = np.where(_okg, _S - np.where(_okg, _Hf, 0.0), 0.0)
    _dzv = BV[:, 2] - np.interp(BV[:, 0], SEAM["xs"], SEAM["zc"])
    _W = (1.0 - smoothstep(_X0, _X0 + _FD, np.abs(BV[:, 0]))) * (1.0 - smoothstep(_U0, _U0 + _FD, _dzv)) * \
        (1.0 - smoothstep(_D0, _D0 + _FD, -_dzv)) * (BV[:, 2] < Z_NOSE_BOTTOM_S1 - _NC)
    _inb = (_W > 0) & (grid_bilinear(_okg.astype(float), _gx, _gz, BV[:, 0], BV[:, 2]) > 0.999)   # (the seam's own
    #   vertices included: the crack's invalid rays are filled -- left out, they stayed put and tilted the tiny faces round them)
    _Hv = grid_bilinear(np.where(_okg, _Hf, 9.0), _gx, _gz, BV[:, 0], BV[:, 2])
    _beh = np.maximum(BV[:, 1] - _Hv, 0.0)
    _wd = 1.0 - smoothstep(_DF, _DN, _beh)
    _mv6 = np.where(_inb, _W * _wd * grid_bilinear(_Dg, _gx, _gz, BV[:, 0], BV[:, 2]), 0.0)
    _y06 = BV[:, 1].copy()
    BV[:, 1] += _mv6
    _core = _val & (np.abs(_gx)[None, :] <= _X0) & ((_gz[:, None] - np.interp(_gx, SEAM["xs"], SEAM["zc"])[None, :]) <= _U0) & \
        ((_gz[:, None] - np.interp(_gx, SEAM["xs"], SEAM["zc"])[None, :]) >= -_D0) & (_gz[:, None] < Z_NOSE_BOTTOM_S1 - _NC)
    MOUTH_SMOOTH_INFO = {"sigma_mm": MOUTH_SMOOTH[0], "zone_mm": {"half_width": MOUTH_SMOOTH[1], "above_seam": MOUTH_SMOOTH[2],
                                                                  "below_seam": MOUTH_SMOOTH[3], "fade": MOUTH_SMOOTH[4]},
                         "nose_clear_mm": MOUTH_SMOOTH[5], "depth_mm": MOUTH_SMOOTH[6:8], "nose_bottom_z": round(Z_NOSE_BOTTOM_S1, 5),
                         "grid": [len(_gz), len(_gx)], "valid_samples": int(_val.sum()),
                         "verts_moved": int((np.abs(_mv6) > 1e-7).sum()), "move_mm_max": round(1000 * float(np.abs(_mv6).max()), 3),
                         "move_mm_p95": round(1000 * float(np.percentile(np.abs(_mv6[np.abs(_mv6) > 1e-7]), 95)), 3) if (np.abs(_mv6) > 1e-7).any() else 0.0,
                         "front_vs_smooth_mm_in_core_before": {"max": round(1000 * float(np.abs(_S - _H)[_core].max()), 3),
                                                               "p95": round(1000 * float(np.percentile(np.abs(_S - _H)[_core], 95)), 3)}}
    if EYE_SCALE != 1.0:
        _mvz = 1000 * np.abs(BV[:, 1] - _y06)
        MOUTH_SMOOTH_INFO["move_mm_by_v4_zone"] = {k: round(float(_mvz[m].max()), 3) for k, m in LEAK_ZONES.items()}
    report["mouth_smooth"] = MOUTH_SMOOTH_INFO
    print("MOUTHSMOOTH", json.dumps(MOUTH_SMOOTH_INFO))


def lip_field(x, z):
    """v6.1 LIP_FORMS: the forward displacement (m, >= 0) of the lip volumes at (x, z) (build frame) about the seam."""
    x = np.asarray(x, float); z = np.asarray(z, float)
    if LIP_FORMS is None or SEAM is None:
        return np.zeros(np.broadcast(x, z).shape)
    xc_ = 0.5 * (float(SEAM["xs"].min()) + float(SEAM["xs"].max())); hw_ = 0.5 * (float(SEAM["xs"].max()) - float(SEAM["xs"].min()))
    dz_ = z - np.interp(x, SEAM["xs"], SEAM["zc"])
    out_ = np.zeros(np.broadcast(x, z).shape)
    for key_, sg_ in (("lower", -1.0), ("upper", 1.0)):
        A_, H_, p_, xr_, xf_ = LIP_FORMS[key_]
        t_ = np.clip(sg_ * dz_ / H_, 0.0, 1.0)
        prof_ = np.where(sg_ * dz_ > 0, np.sin(np.pi * t_ ** p_) ** 2, 0.0)
        r_ = xr_ * hw_
        lat_ = 1.0 - smoothstep(r_ * (1.0 - xf_), r_, np.abs(x - xc_))
        out_ = out_ + A_ * prof_ * lat_
    return out_


LIP_FORMS_INFO = None
if LIP_FORMS is not None and SEAM is not None:
    # v6.1 LIP FORMS (review-log 2026-09-29 "Wren v6.1 mouth feedback" + addendum, the archer figure): one soft lower-lip
    # volume under the line and a very small upper-lip plane above it, added ON the smoothed mouth skin (after
    # MOUTH_SMOOTH): every front vertex near the mouth moves forward by lip_field(x, z); a hidden layer up to 2 mm behind
    # the front moves with it, none past 5 mm (the layers keep their order: the field has zero slope across the seam band)
    _bl = BVHTree.FromPolygons(BV.tolist(), BF)
    _cand = np.nonzero((np.abs(BV[:, 0]) < 0.035) & (np.abs(BV[:, 2] - np.interp(BV[:, 0], SEAM["xs"], SEAM["zc"])) < 0.012))[0]
    _lf = lip_field(BV[_cand, 0], BV[_cand, 2])
    _mvl = np.zeros(len(BV))
    for _i, _l in zip(_cand, _lf):
        if _l <= 0:
            continue
        _h = _bl.ray_cast(Vector((float(BV[_i, 0]), -1.0, float(BV[_i, 2]))), Vector((0.0, 1.0, 0.0)), 2.0)
        _b = max(float(BV[_i, 1]) - _h[0][1], 0.0) if _h[0] is not None else 1.0
        _mvl[_i] = -_l * (1.0 - smoothstep(0.002, 0.005, _b))
    BV[:, 1] += _mvl
    _zs1 = float(np.interp(0.0, SEAM["xs"], SEAM["zc"]))
    _pz = np.arange(-0.010, 0.0101, 0.0001)
    _pf = lip_field(np.zeros(len(_pz)), _zs1 + _pz)
    LIP_FORMS_INFO = {"forms_mm": {k: [round(v[0] * 1000, 2), round(v[1] * 1000, 2), v[2], v[3], v[4]] for k, v in LIP_FORMS.items()},
                      "verts_moved": int((np.abs(_mvl) > 1e-7).sum()), "move_mm_max": round(1000 * float(np.abs(_mvl).max()), 3),
                      "lower_peak_mm_below_seam": round(-1000 * float(_pz[np.argmax(np.where(_pz < 0, _pf, 0))]), 2),
                      "upper_peak_mm_above_seam": round(1000 * float(_pz[np.argmax(np.where(_pz > 0, _pf, 0))]), 2),
                      "field_at_seam_band_mm(+-0.3)": round(1000 * float(lip_field(np.zeros(7), _zs1 + np.linspace(-3e-4, 3e-4, 7)).max()), 4)}
    if EYE_SCALE != 1.0:
        _mvz = 1000 * np.abs(_mvl)
        LIP_FORMS_INFO["move_mm_by_v4_zone"] = {k: round(float(_mvz[m].max()), 3) for k, m in LEAK_ZONES.items()}
    report["lip_forms"] = LIP_FORMS_INFO
    print("LIPFORMS", json.dumps(LIP_FORMS_INFO))
for o in (hb, rig0):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
DOM = np.argmax(BW, 1)
DOMN = np.array([MB[j] for j in DOM], dtype=object)


def dom_in(names):
    return np.isin(DOMN, list(names))


def P(n, key="head"):
    return BREST[n][key].copy()


HIP = {s: P("thigh_" + s.lower()) for s in "LR"}
KNEE = {s: P("calf_" + s.lower()) for s in "LR"}
ANKLE = {s: P("foot_" + s.lower()) for s in "LR"}
BALL = {s: P("ball_" + s.lower()) for s in "LR"}
TOE = {s: P("ball_" + s.lower(), "tail") for s in "LR"}
SHO = {s: P("upperarm_" + s.lower()) for s in "LR"}
ELB = {s: P("lowerarm_" + s.lower()) for s in "LR"}
WRI = {s: P("hand_" + s.lower()) for s in "LR"}
NECK0 = P("neck_01"); HEADJ = P("head")
PELVIS = P("pelvis")
ARM_B = {s: dom_in([n for n in MB if n.endswith("_" + s.lower()) and not n.startswith(("clavicle", "thigh", "calf", "foot", "ball"))])
         for s in "LR"}
LEG_B = {s: dom_in(["thigh_" + s.lower(), "calf_" + s.lower(), "foot_" + s.lower(), "ball_" + s.lower()]) for s in "LR"}
HEAD_B = dom_in(["head"])
Z_TOP = float(BV[:, 2].max())
face_front_y = float(BV[HEAD_B, 1].min())
report["landmarks"] = {"height_total": round(Z_TOP, 4), "hip_z": round(float(HIP["L"][2]), 4),
                       "knee_z": round(float(KNEE["L"][2]), 4), "shoulder_z": round(float(SHO["L"][2]), 4),
                       "neck_z": round(float(NECK0[2]), 4), "head_joint_z": round(float(HEADJ[2]), 4),
                       "eye_L": EYE["L"]["c"].round(4).tolist(), "eye_r": round(EYE["L"]["r"], 4),
                       "mouth": MOUTH.round(4).tolist(), "ankle_z": round(float(ANKLE["L"][2]), 4),
                       "shoulder_L": SHO["L"].round(4).tolist(), "elbow_L": ELB["L"].round(4).tolist(),
                       "wrist_L": WRI["L"].round(4).tolist(), "pelvis": PELVIS.round(4).tolist(),
                       "arm_deg": report["mpfb"]["arm_below_horizontal_deg"]}
print("BODY", json.dumps({"verts": len(BV), "tris": tri_count_F(BF), **report["landmarks"]}))

# =========================================================================== 1b. BVHs, profiles, transfer, landmarks
fcen = np.array([BV[f].mean(0) for f in BF])
fdom = np.array([np.bincount(DOM[f], minlength=len(MB)).argmax() for f in BF])
fdomn = np.array([MB[j] for j in fdom], dtype=object)
BVH_BODY = BVHTree.FromPolygons(BV.tolist(), BF)
is_arm_f = np.array([n.startswith(("upperarm", "lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb")) for n in fdomn])
near_sho = np.zeros(len(BF), bool)
for s in "LR":
    near_sho |= np.linalg.norm(fcen - SHO[s], axis=1) < 0.085
TORSO_F = [f for f, a, ns, n in zip(BF, is_arm_f, near_sho, fdomn) if (not a or ns) and n not in ("head",)]
BVH_TORSO = BVHTree.FromPolygons(BV.tolist(), TORSO_F)
TRUNK_F = [f for f, a, n in zip(BF, is_arm_f, fdomn) if not a and n != "head"]
BVH_TRUNK = BVHTree.FromPolygons(BV.tolist(), TRUNK_F)
LIMB_BVH = {}
for s in "LR":
    lo_ = s.lower()
    LIMB_BVH["thigh" + s] = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n in ("thigh_" + lo_, "pelvis")])
    LIMB_BVH["leg" + s] = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n in ("thigh_" + lo_, "calf_" + lo_, "foot_" + lo_)])
    LIMB_BVH["arm" + s] = BVHTree.FromPolygons(BV.tolist(), [f for f, n in zip(BF, fdomn) if n in ("upperarm_" + lo_, "lowerarm_" + lo_, "hand_" + lo_)])


def radial_profile(bvh, ax, ay, phi_deg, zs, rmax=0.9):
    """outermost surface radius from the vertical axis (ax, ay) along azimuth phi (0 = +Y back, +90 = +X his left)."""
    ph = math.radians(phi_deg)
    d = np.array([math.sin(ph), math.cos(ph), 0.0])
    out = np.zeros(len(zs))
    for k, z in enumerate(zs):
        o = Vector((ax + d[0] * rmax, ay + d[1] * rmax, z))
        hit, _, _, dist = bvh.ray_cast(o, Vector((-d[0], -d[1], 0.0)), rmax)
        out[k] = rmax - dist if hit is not None else 0.0
    return out


AX_Y = float(PELVIS[1])
TRI, TRI_F = [], []
for fi, f in enumerate(BF):
    for k in range(1, len(f) - 1):
        TRI.append([f[0], f[k], f[k + 1]]); TRI_F.append(fi)
TRI = np.array(TRI)
BVH_TRI = BVHTree.FromPolygons(BV.tolist(), TRI.tolist())


def transfer(Ps, W=None, V=None):
    """MPFB weights at the nearest skin point (barycentric on the nearest triangle) for every point in Ps."""
    W = BW if W is None else W
    V = BV if V is None else V
    Ps = np.asarray(Ps, float)
    loc = np.empty_like(Ps); ti = np.empty(len(Ps), dtype=np.int64)
    for k, p in enumerate(Ps):
        l_, _, i_, _ = BVH_TRI.find_nearest(Vector(p))
        loc[k] = l_; ti[k] = i_
    a, b, c = V[TRI[ti, 0]], V[TRI[ti, 1]], V[TRI[ti, 2]]
    v0, v1, v2 = b - a, c - a, loc - a
    d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
    d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-20)
    bv = (d11 * d20 - d01 * d21) / den; bw = (d00 * d21 - d01 * d20) / den
    bu = 1.0 - bv - bw
    B = np.clip(np.stack([bu, bv, bw], 1), 0, 1); B /= B.sum(1, keepdims=True)
    return B[:, 0:1] * W[TRI[ti, 0]] + B[:, 1:2] * W[TRI[ti, 1]] + B[:, 2:3] * W[TRI[ti, 2]]


def slab(mask, zlo, zhi):
    return mask & (BV[:, 2] >= zlo) & (BV[:, 2] <= zhi)


torso_v = ~(ARM_B["L"] | ARM_B["R"]) & ~HEAD_B
_zs = np.linspace(HIP["L"][2] + 0.03, SHO["L"][2] - 0.15, 40)
_wd = [np.abs(BV[slab(torso_v, z - 0.006, z + 0.006), 0]).max() for z in _zs]
Z_WAIST = float(_zs[int(np.argmin(_wd))])
crotch_m = slab(torso_v, KNEE["L"][2], HIP["L"][2] + 0.05) & (np.abs(BV[:, 0]) < 0.012)
Z_CROTCH = float(BV[crotch_m, 2].min())
chest_m = slab(torso_v, SHO["L"][2] - 0.20, SHO["L"][2] - 0.05) & (np.abs(BV[:, 0]) < 0.10)
Y_CHEST = float(BV[chest_m, 1].min())
# mouth slit: the front-surface profile down the midline; the two most forward bulges are the lips, the slit the most
# recessed point between them (vampwarrior v2's measured rule)
_zt = TEETH[:, 2].min()
_zs_l = np.arange(_zt - 0.020, _zt + 0.022, 0.00025)
_yp = []
for z_ in _zs_l:
    h_ = BVH_BODY.ray_cast(Vector((0.0, -0.6, float(z_))), Vector((0.0, 1.0, 0.0)), 1.0)
    _yp.append(h_[0][1] if h_[0] is not None else np.nan)
_yp = np.array(_yp)
_ypf = np.convolve(np.nan_to_num(_yp, nan=np.nanmax(_yp)), np.ones(5) / 5.0, mode="same")
_mins = [i for i in range(3, len(_ypf) - 3) if _ypf[i] <= _ypf[i - 1] and _ypf[i] <= _ypf[i + 1]]
_mins = sorted(sorted(_mins, key=lambda i: _ypf[i])[:2])
if len(_mins) == 2:
    _i_slit = _mins[0] + int(np.argmax(_ypf[_mins[0]:_mins[1] + 1]))
    Z_SLIT = float(_zs_l[_i_slit])
else:                                                  # (v4 flattened lips: no two bulges; the sealed seam decides below)
    Z_SLIT = float(_zt)
if SEAM is not None:                                   # v2: the slit IS the sealed seam at the midline
    Z_SLIT = float(np.interp(0.0, SEAM["xs"], SEAM["zc"]))
Z_LIP = Z_SLIT + LIP_DZ
Y_LIP = float(np.nanmin(_yp[np.abs(_zs_l - Z_SLIT) < 0.003]))
_zc_s = np.arange(Z_SLIT, Z_SLIT - 0.09, -0.00025)
_yc_s = np.array([(lambda h: h[0][1] if h[0] is not None else np.nan)(BVH_BODY.ray_cast(Vector((0.0, -0.6, float(z_))),
                                                                                         Vector((0.0, 1.0, 0.0)), 1.0)) for z_ in _zc_s])
_jump = np.nonzero(np.diff(np.nan_to_num(_yc_s, nan=9.0)) > 0.008)[0]
Z_CHIN = float(_zc_s[_jump[0]]) if len(_jump) else float(_zc_s[-1])
Y_CHIN = float(_yc_s[_jump[0]]) if len(_jump) else float(np.nanmin(_yc_s))
_hf = HEAD_B & (BV[:, 1] < float(BV[HEAD_B, 1].mean()))
report["chin"] = {"chin_bottom_z": round(Z_CHIN, 4), "chin_front_y": round(Y_CHIN, 4), "slit_z": round(Z_SLIT, 4),
                  "slit_to_chin_m": round(Z_SLIT - Z_CHIN, 4),
                  "jaw_half_width_m": {"chin+%dmm" % d: round(float(np.abs(BV[_hf & (np.abs(BV[:, 2] - Z_CHIN - d / 1000.0) < 0.0015), 0]).max()), 4)
                                       for d in (5, 10, 20, 35) if (_hf & (np.abs(BV[:, 2] - Z_CHIN - d / 1000.0) < 0.0015)).any()}}




# v6 MOUTH PLACEMENT (review-log 2026-09-29 "Wren v6 mouth feedback": re-placed per the FE portrait's proportions) -- the
# same landmarks improve/wren_mouth_probe.py measures on the delivered mesh: going down the midline from the nose tip, the
# NOSE BOTTOM = the end of the nose's underside (front-ray hit normals facing down past -0.55: the drawn nose mark of the
# FE / anime faces); the CHIN = below the seam, the first row whose hit is edge-on (|n_y| < 0.34: the jaw outline of a front
# view) or that jumps back > 8 mm. v_ratio = (nose bottom - seam) / (nose bottom - chin): the Ashe portrait 0.29.
Z_NOSE_BOTTOM, Z_NOSE_TIP = nose_bottom_z(BV, BF)
_zm6 = np.arange(EYE["L"]["c"][2] - 0.020, EYE["L"]["c"][2] - 0.150, -0.0001)
_h6 = [BVH_BODY.ray_cast(Vector((0.0, -0.6, float(z_))), Vector((0.0, 1.0, 0.0)), 1.0) for z_ in _zm6]
_y6 = np.array([h[0][1] if h[0] is not None else np.nan for h in _h6])
_ny6 = np.array([h[1][1] if h[0] is not None else np.nan for h in _h6])
_i6 = int(np.argmin(np.abs(_zm6 - Z_SLIT)))
while _i6 + 1 < len(_zm6) and not ((not np.isnan(_ny6[_i6]) and abs(_ny6[_i6]) < 0.34) or
                                   (not np.isnan(_y6[_i6 + 1]) and _y6[_i6 + 1] - _y6[_i6] > 0.008)):
    _i6 += 1
Z_CHIN_OUTLINE = float(_zm6[_i6])
report["mouth_placement"] = {"nose_tip_z": round(Z_NOSE_TIP, 5), "nose_bottom_z": round(Z_NOSE_BOTTOM, 5), "seam_z": round(Z_SLIT, 5),
                             "chin_outline_z": round(Z_CHIN_OUTLINE, 5),
                             "nose_bottom_to_seam_mm": round(1000 * (Z_NOSE_BOTTOM - Z_SLIT), 2),
                             "seam_to_chin_mm": round(1000 * (Z_SLIT - Z_CHIN_OUTLINE), 2),
                             "v_ratio": round((Z_NOSE_BOTTOM - Z_SLIT) / (Z_NOSE_BOTTOM - Z_CHIN_OUTLINE), 3),
                             "seam_width_mm": round(1000 * float(SEAM["xs"].max() - SEAM["xs"].min()), 1) if SEAM is not None else None,
                             "eye_spacing_mm": round(2000 * float(EYE["L"]["c"][0]), 2)}
if SEAM is not None:
    report["mouth_placement"]["w_eyes"] = round(float(SEAM["xs"].max() - SEAM["xs"].min()) / (2.0 * float(EYE["L"]["c"][0])), 3)
    report["mouth_placement"]["seam_level_mm"] = round(1000 * float(np.ptp(SEAM["zc"] - (smirk_rise(SEAM["xs"], SEAM["xs"].min(), SEAM["xs"].max())
                                                                                      if MOUTH_SMIRK is not None and MOUTH_SMIRK_GEO is not None else 0.0))), 2)
print("PLACEMENT", json.dumps(report["mouth_placement"]))
report["mouth"] = {"lip_pairing_pre_cut": lip_pairing(BVH_BODY, Z_SLIT)}
print("MOUTH", json.dumps(report["mouth"]))
_scalp = HEAD_B & (BV[:, 2] > EYE["L"]["c"][2])
HC = np.array([0.0, 0.5 * (BV[_scalp, 1].min() + BV[_scalp, 1].max()), EYE["L"]["c"][2]])
HR = np.array([np.abs(BV[_scalp, 0]).max(), 0.5 * (BV[_scalp, 1].max() - BV[_scalp, 1].min()), Z_TOP - HC[2]])
_head_h = Z_TOP - Z_CHIN
report["landmarks"].update({"waist_z": round(Z_WAIST, 4), "crotch_z": round(Z_CROTCH, 4), "chest_front_y": round(Y_CHEST, 4),
                            "lip_line_z": round(Z_LIP, 4), "head_centre": HC.round(4).tolist(), "head_radii": HR.round(4).tolist(),
                            "head_height_m": round(_head_h, 4), "heads_tall": round((Z_TOP - SOLE_T) / _head_h, 2),
                            "eye_spacing_m": round(float(EYE["L"]["c"][0] - EYE["R"]["c"][0]), 4)})
print("LANDMARKS", json.dumps(report["landmarks"]))
