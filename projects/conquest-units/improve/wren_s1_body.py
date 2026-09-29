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
for rel, w in TARGETS.items():
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
_i_slit = _mins[0] + int(np.argmax(_ypf[_mins[0]:_mins[1] + 1]))
Z_SLIT = float(_zs_l[_i_slit])
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
_scalp = HEAD_B & (BV[:, 2] > EYE["L"]["c"][2])
HC = np.array([0.0, 0.5 * (BV[_scalp, 1].min() + BV[_scalp, 1].max()), EYE["L"]["c"][2]])
HR = np.array([np.abs(BV[_scalp, 0]).max(), 0.5 * (BV[_scalp, 1].max() - BV[_scalp, 1].min()), Z_TOP - HC[2]])
_head_h = Z_TOP - Z_CHIN
report["landmarks"].update({"waist_z": round(Z_WAIST, 4), "crotch_z": round(Z_CROTCH, 4), "chest_front_y": round(Y_CHEST, 4),
                            "lip_line_z": round(Z_LIP, 4), "head_centre": HC.round(4).tolist(), "head_radii": HR.round(4).tolist(),
                            "head_height_m": round(_head_h, 4), "heads_tall": round((Z_TOP - SOLE_T) / _head_h, 2),
                            "eye_spacing_m": round(float(EYE["L"]["c"][0] - EYE["R"]["c"][0]), 4)})
print("LANDMARKS", json.dumps(report["landmarks"]))
