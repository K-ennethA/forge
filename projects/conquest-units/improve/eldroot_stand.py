"""Eldroot standing-rest rebuild + sit/stand state clips (artist 2026-09-25: "it is currently
sitting can we make it stand up ... so it can sit and stand?"; earlier: "should be able to stand
and walk with slow lumbering steps").

    blender --background rigged/eldroot.blend --factory-startup --python eldroot_stand.py --
            <out.blend> <out.json> [--dz 0.45] [--dy 0] [--band 3] [--rest-only]

Opens the SEATED rig (rigged/eldroot.blend), never saves over it (save_as_mainfile copy=True).

A. STANDING REST (rig-guided mesh work, topology + UVs untouched)
   The seated sculpt is a trunk resting on its base with two knee-up shin columns in front,
   fused to the trunk front along their upper-medial back (z 0.50-1.15, probe numbers in the
   report) and two knuckle arms resting on the floor. Standing = the trunk + arms rise DZ; the
   leg columns stretch between their foot pads (fixed on the floor) and the trunk junction.
     region R  = verts carrying any seated thigh/shin weight, grown BAND_RINGS edge rings into
                 the trunk so the junction faces do not take the whole stretch in one ring.
     h         = harmonic field on R (cotangent Laplacian): 0 on the foot pads (leg verts below
                 Z_PAD), 1 on every vertex outside R. h has no interior extremum, so the lift
                 grows monotonically from pad to trunk.
     standing  = seated + u * DV, DV = (0, -DY, DZ), u = h in R, 0 on pads, 1 elsewhere; then a
                 recentring shift S puts the rest bbox centre on the origin. Default DY = 0
                 (pure vertical): a forward shift folds faces where the trunk's lower front hits
                 the leg backs (sweep quoted at DY below).
   Rig: 14 bones = the seated root/trunk/arm bones (+ DV + S) with each leg rebuilt as
   thigh (hip->knee) / shin (knee->ankle) / foot (ankle->toe, flat, NEW). The knee is the
   thigh/shin joint moved to the mid-column ring (seated it sat at the column top, z 0.42, so
   the whole column rode the shin and its pad rocked 24 mm under the floor in the walk). Leg
   bones are NOT connected: sitting telescopes the column by bone translation.
   Leg weights are hat functions of h (foot 1-h/HK below HK, shin, then the "up" share above
   HK split thigh/trunk by the seated leg weight), so a pure translation pose
   (pelvis -DV, shin -HK*DV, foot 0) reproduces the SEATED sculpt exactly: sitting is a pose
   of the standing rest, measured in the report (sit_reconstruction_max_mm).
B. CLIPS, 24 fps, in place, every frame keyed (loc + quat), no constraints ship:
   sitting_idle (loop), stand_up (one-shot), idle (loop, standing creak-sway port),
   sit_down (one-shot), walk (loop, ~0.14 m/s two-beat lumber, feet flat on foot bones).
   Loops: last frame == first. One-shots: first/last frame == the neighbouring loop's frame 1
   (join residuals measured on the evaluated mesh).
C. MEASURES: stretch/UV/bake validity of the rebuild, per-clip seam/join, contact slide, rigid
   bone stretch + telescoping gap, IK reach, pad sink (foot-pad sole min z), extents, forge
   rigcheck.animation_check where it applies.
"""
import bpy, sys, os, json, math, time, hashlib
import numpy as np
from mathutils import Vector, Matrix, Euler, Quaternion

T0 = time.time()
argv = sys.argv[sys.argv.index("--") + 1:]
OUT_BLEND, OUT_JSON = argv[0], argv[1]


def opt(name, default, cast=float):
    return cast(argv[argv.index(name) + 1]) if name in argv else default


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "addon")))
import rigkit as K  # noqa: E402

FPS = K.FPS
TAU = 2 * math.pi
DZ = opt("--dz", 0.45)          # trunk rise seated -> standing (m); height cap 3.4 allows <= 0.69
Z_PAD = 0.10                    # foot pad = leg verts below this (seated z); stays on the floor
DY = opt("--dy", 0.0)           # optional forward shift of the trunk over the feet. MEASURED, rejected as a
                                # default: dy 0.15 / 0.20 / 0.30 fold 1 / 2 / 7 faces (band 5; 13 at band 2)
                                # where the trunk's lower front (seated z 0.3-0.5, behind the leg backs) is
                                # pushed INTO the leg columns' backs -- a real collision, not a gradient
                                # artifact (a scalar Jacobi relax of u made it worse: 3-26 folds left).
BAND_RINGS = int(opt("--band", 3))   # trunk rings added to the stretch region (sweep at dy 0: band 0/2/3/5 ->
                                # stretch max 4.17/2.67/2.58/2.44, folds 2/0/0/0, trunk lag 0/128/146/175 mm)
DV = np.array([0.0, -DY, DZ])   # telescope vector: standing = seated + u * DV (+ a recentring shift S)
report = {"unit": "eldroot", "stage": "standing-rest rebuild + state clips", "source": bpy.data.filepath,
          "fps": FPS, "dz_m": DZ, "dy_m": DY, "z_pad_m": Z_PAD, "band_rings": BAND_RINGS}

scene = bpy.context.scene
scene.render.fps = FPS
scene.render.fps_base = 1.0
low = bpy.data.objects["eldroot"]
old_rig = bpy.data.objects["eldroot_rig"]
report["seated_actions_removed"] = [a.name for a in bpy.data.actions]   # seated idle/walk key the old bones
for a in list(bpy.data.actions):
    bpy.data.actions.remove(a)
assert np.abs(np.array(low.matrix_world) - np.eye(4)).max() < 1e-9
assert np.abs(np.array(old_rig.matrix_world) - np.eye(4)).max() < 1e-9
me = low.data
n = len(me.vertices)


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def uv_array():
    uv = np.empty(len(me.loops) * 2); me.uv_layers["UVMap"].data.foreach_get("uv", uv)
    return uv.reshape(-1, 2)


W0 = np.empty(n * 3); me.vertices.foreach_get("co", W0); W0 = W0.reshape(-1, 3)
UV0 = uv_array()
FV = np.empty(len(me.polygons) * 3, dtype=np.int64); me.polygons.foreach_get("vertices", FV)   # all tris (25000)
F = FV.reshape(-1, 3)
LS = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", LS)
report["mesh_before"] = {"verts": n, "tris": len(F), "uv_sha": sha(UV0), "faces_sha": sha(F), "coords_sha": sha(W0),
                         "bbox": [W0.min(0).round(4).tolist(), W0.max(0).round(4).tolist()]}
M = K.MeshData(low)

# ------------------------------------------------------------------ seated weights
old_names = [g.name for g in low.vertex_groups]
Wold = np.zeros((n, len(old_names)))
for v in me.vertices:
    for g in v.groups:
        Wold[v.index, g.group] = g.weight
col = {nm: i for i, nm in enumerate(old_names)}
legw = {s: Wold[:, col["thigh." + s]] + Wold[:, col["shin." + s]] for s in ("L", "R")}
leg_any = legw["L"] + legw["R"]
nonleg = [nm for nm in old_names if not nm.startswith(("thigh", "shin"))]

# ------------------------------------------------------------------ stretch region + pads
R = leg_any > 1e-3
for _ in range(BAND_RINGS):
    grow = R.copy()
    a, b = M.ev[:, 0], M.ev[:, 1]
    grow[a[R[b]]] = True; grow[b[R[a]]] = True
    R = grow
pads = (leg_any >= 0.5) & (W0[:, 2] < Z_PAD)
# junction (seated): leg-dominant verts with a trunk-dominant neighbour -- quoted to explain the region
legdom = leg_any >= 0.5
a, b = M.ev[:, 0], M.ev[:, 1]
jmask = (legdom[a] & ~legdom[b] & (leg_any[b] < 0.5))
jv = np.unique(np.concatenate([a[jmask], b[jmask]]))
jv = jv[legdom[jv]]
report["seated_junction"] = {"verts": int(len(jv)), "z_range": [round(float(W0[jv, 2].min()), 3), round(float(W0[jv, 2].max()), 3)],
                             "note": "leg columns fuse to the trunk front along their upper-medial back; below z_min they are free columns"}

# ------------------------------------------------------------------ harmonic field (cotangent Laplacian)
def cot_weights(P):
    I, J, Wc = [], [], []
    for k in range(3):
        i, j, o = F[:, k], F[:, (k + 1) % 3], F[:, (k + 2) % 3]
        e1, e2 = P[i] - P[o], P[j] - P[o]
        cr = np.linalg.norm(np.cross(e1, e2), axis=1)
        c = (e1 * e2).sum(1) / np.maximum(cr, 1e-12)
        I.append(i); J.append(j); Wc.append(0.5 * c)
    I = np.concatenate(I); J = np.concatenate(J); Wc = np.maximum(np.concatenate(Wc), 1e-4)   # clamp obtuse -> max principle
    return I, J, Wc


I_, J_, Wc_ = cot_weights(W0)
unk = R & ~pads
ui = np.nonzero(unk)[0]
idx = -np.ones(n, dtype=np.int64); idx[ui] = np.arange(len(ui))
hb = np.ones(n); hb[pads] = 0.0                     # boundary values (outside R = 1, pads = 0)
A = np.zeros((len(ui), len(ui))); rhs = np.zeros(len(ui))
for (p, q) in ((I_, J_), (J_, I_)):
    m = unk[p]
    pi, qi, w = idx[p[m]], q[m], Wc_[m]
    np.add.at(A, (pi, pi), w)
    inner = unk[qi]
    np.add.at(A, (pi[inner], idx[qi[inner]]), -w[inner])
    np.add.at(rhs, pi[~inner], w[~inner] * hb[qi[~inner]])
h = hb.copy()
h[ui] = np.linalg.solve(A, rhs)
report["harmonic"] = {"region_verts": int(R.sum()), "unknowns": int(len(ui)), "pad_verts": int(pads.sum()),
                      "h_range_in_region": [round(float(h[ui].min()), 5), round(float(h[ui].max()), 5)]}
u = np.where(R, h, 1.0); u[pads] = 0.0
WS = W0 + u[:, None] * DV[None, :]
lo_s, hi_s = WS.min(0), WS.max(0)
S = np.array([-(lo_s[0] + hi_s[0]) / 2, -(lo_s[1] + hi_s[1]) / 2, 0.0])     # rest bbox centred on the origin (contract)
WS += S
report["recentre_shift_m"] = S.round(4).tolist()

# ------------------------------------------------------------------ rebuild measures (stretch + bake validity)
def tri_frames(P):
    e1 = P[F[:, 1]] - P[F[:, 0]]; e2 = P[F[:, 2]] - P[F[:, 0]]
    nrm = np.cross(e1, e2)
    return e1, e2, nrm


e1a, e2a, na = tri_frames(W0)
e1b, e2b, nb_ = tri_frames(WS)
area0 = 0.5 * np.linalg.norm(na, axis=1); area1 = 0.5 * np.linalg.norm(nb_, axis=1)
# principal stretches of the per-triangle deformation (2x2 in the seated triangle plane)
t1 = e1a / np.maximum(np.linalg.norm(e1a, axis=1), 1e-12)[:, None]
nn = na / np.maximum(np.linalg.norm(na, axis=1), 1e-12)[:, None]
t2 = np.cross(nn, t1)
Pm = np.stack([np.stack([(e1a * t1).sum(1), (e1a * t2).sum(1)], 1), np.stack([(e2a * t1).sum(1), (e2a * t2).sum(1)], 1)], 2)   # 2x2 cols e1,e2
Qm = np.stack([e1b, e2b], 2)          # 3x2
Pinv = np.linalg.inv(Pm + np.eye(2) * 1e-15)
Fg = Qm @ Pinv                        # 3x2 deformation gradient
sv = np.linalg.svd(Fg, compute_uv=False)
moved = (np.abs(u[F] - 1.0).max(1) > 1e-6) & ~(u[F] == 0).all(1)        # faces touched by the stretch
flip = ((na * nb_).sum(1) < 0)
# tangent-space handedness: sign of the UV-parametric orientation vs the face normal must not change
uvf = UV0[LS[:, None] + np.arange(3)[None, :]]
duv1 = uvf[:, 1] - uvf[:, 0]; duv2 = uvf[:, 2] - uvf[:, 0]
det_uv = duv1[:, 0] * duv2[:, 1] - duv1[:, 1] * duv2[:, 0]
report["rest_rebuild"] = {
    "faces_stretched": int(moved.sum()),
    "principal_stretch_max": round(float(sv[moved, 0].max()), 3),
    "principal_stretch_p95": round(float(np.percentile(sv[moved, 0], 95)), 3),
    "principal_stretch_median": round(float(np.median(sv[moved, 0])), 3),
    "compression_min": round(float(sv[moved, 1].min()), 3),
    "area_ratio_max": round(float((area1[moved] / np.maximum(area0[moved], 1e-15)).max()), 3),
    "area_ratio_p95": round(float(np.percentile(area1[moved] / np.maximum(area0[moved], 1e-15), 95)), 3),
    "flipped_faces": int(flip.sum()),
    "trunk_band_max_lag_mm": round(float(np.linalg.norm(DV) * (1 - u[R & (leg_any < 1e-3)]).max()) * 1000, 1) if (R & (leg_any < 1e-3)).any() else 0.0,
    "trunk_band_lag_note": "pure-trunk verts inside the band trail the trunk's rise by (1-h)*|DV| -- the thigh root forming out of the belly",
}

if "--rest-only" in argv:
    print("REST_ONLY", json.dumps({k: report.get(k) for k in ("dz_m", "dy_m", "band_rings", "harmonic", "rest_rebuild")}))
    sys.stdout.flush(); os._exit(0)
# ------------------------------------------------------------------ write standing coords
me.vertices.foreach_set("co", WS.ravel())
me.update()
UV1 = uv_array()
report["mesh_after"] = {"uv_sha": sha(UV1), "faces_sha": sha(F), "uv_identical": bool(np.array_equal(UV0, UV1)),
                        "coords_sha": sha(WS), "bbox": [WS.min(0).round(4).tolist(), WS.max(0).round(4).tolist()],
                        "height_m": round(float(WS[:, 2].max() - WS[:, 2].min()), 4)}

# bake sanity on the stretched faces: sample the packed AO / normal maps at each face's UV centroid
def sample(img_name, uvc):
    img = bpy.data.images[img_name]
    w, hh = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(hh, w, img.channels)
    x = np.clip((uvc[:, 0] % 1.0) * w, 0, w - 1).astype(int); y = np.clip((uvc[:, 1] % 1.0) * hh, 0, hh - 1).astype(int)
    return px[y, x]


uvc = uvf.mean(1)
ao = sample("eldroot_ao", uvc)[:, 0]
nm = sample("eldroot_normal", uvc)[:, :3] * 2 - 1
big = moved & (sv[:, 0] > 1.2)
dev = np.linalg.norm(nm - np.array([0, 0, 1.0]), axis=1)
report["bake_check"] = {
    "method": "UVs byte-identical (sha) -> the packed bakes index the same texels; tangent-space normal map stays valid "
              "under deformation iff no face flips and UV handedness vs geometry is unchanged; texel density change = 1/area ratio",
    "uv_handedness_changed_faces": int(flip.sum()),
    "zero_area_uv_faces": int((np.abs(det_uv) < 1e-12).sum()),
    "stretched_gt_1.2_faces": int(big.sum()),
    "ao_mean_all": round(float(ao.mean()), 3), "ao_mean_stretched": round(float(ao[big].mean()), 3) if big.any() else None,
    "normal_dev_mean_all": round(float(dev.mean()), 4),
    "normal_dev_mean_stretched": round(float(dev[big].mean()), 4) if big.any() else None,
    "texel_density_min_ratio_stretched": round(float((area0[big] / np.maximum(area1[big], 1e-15)).min()), 3) if big.any() else None,
}

# ------------------------------------------------------------------ standing rig
def ring_centroid(mask):
    return WS[mask].mean(0) if mask.any() else None


landmarks = {}
HK = {}
for s, sx in (("L", 1.0), ("R", -1.0)):
    legside = (legw[s] >= 0.5)
    padside = pads & (legw[s] >= 0.5)
    pc = WS[padside].mean(0)
    ankle = np.array([pc[0], pc[1], Z_PAD + 0.04])
    hq = np.percentile(h[legside & R], [50, 90, 97, 100])
    hip = ring_centroid(legside & (h >= hq[2]))          # the junction: the top 3 % of the leg's h
    zmid = 0.5 * (hip[2] + ankle[2])
    slab = legside & R & (np.abs(WS[:, 2] - zmid) < 0.04)
    knee = ring_centroid(slab)                           # knee = mid-column ring; its h is the weight knot
    HK[s] = float(np.median(h[slab]))
    report.setdefault("leg_h_profile", {})[s] = {"h_p50_p90_p97_max": hq.round(3).tolist(), "knee_h": round(HK[s], 4)}
    # rest chain must not be straight (IK pole): keep >= 30 mm forward knee offset from hip-ankle line
    dv = (ankle - hip) / np.linalg.norm(ankle - hip)
    off = (knee - hip) - dv * ((knee - hip) @ dv)
    fwd = np.array([0.0, -1.0, 0.0]); fwd -= dv * (fwd @ dv); fwd /= np.linalg.norm(fwd)
    if off @ fwd < 0.03:
        knee = knee - off + fwd * 0.03 + (off - fwd * (off @ fwd))   # keep lateral offset, set forward to 30 mm
    toe = ankle + np.array([0.0, -0.22, 0.0])
    landmarks[s] = {"hip": hip, "knee": knee, "ankle": ankle, "toe": toe, "pad_centroid": pc}
report["leg_landmarks_standing"] = {s: {k: [round(float(x), 4) for x in v] for k, v in d.items()} for s, d in landmarks.items()}

old_bones = {b.name: (np.array(b.head_local), np.array(b.tail_local), b.parent.name if b.parent else None)
             for b in old_rig.data.bones}
# delete the seated rig; the mesh keeps no armature until the new one is bound
low.modifiers.remove(low.modifiers["Armature"])
low.parent = None
bpy.data.objects.remove(old_rig, do_unlink=True)
arm_data = bpy.data.armatures.new("eldroot_rig")
rig = bpy.data.objects.new("eldroot_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
up = DV + S                     # trunk + arm bones ride exactly with their (u = 1) geometry
spec = [("root", old_bones["root"][0], old_bones["root"][1], None)]
for nm in ("pelvis", "chest", "crown", "upperarm.L", "forearm.L", "upperarm.R", "forearm.R"):
    hd, tl, par = old_bones[nm]
    spec.append((nm, hd + up, tl + up, par))
for s in ("L", "R"):
    L_ = landmarks[s]
    spec += [("thigh." + s, L_["hip"], L_["knee"], "pelvis"), ("shin." + s, L_["knee"], L_["ankle"], "thigh." + s),
             ("foot." + s, L_["ankle"], L_["toe"], "shin." + s)]
for nm, hd, tl, par in spec:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(hd); e.tail = Vector(tl)
    if par:
        e.parent = arm_data.edit_bones[par]
    e.use_connect = False
    e.use_deform = nm != "root"
    e.roll = 0.0
bpy.ops.object.mode_set(mode="OBJECT")
BONES = [b.name for b in arm_data.bones]
DEFORM = [b for b in BONES if b != "root"]
REST = {b.name: b.matrix_local.copy() for b in arm_data.bones}
RH = {b.name: Vector(b.head_local) for b in arm_data.bones}
RT = {b.name: Vector(b.tail_local) for b in arm_data.bones}

# ------------------------------------------------------------------ weights
Wn = np.zeros((n, len(DEFORM)))
dc = {nm: i for i, nm in enumerate(DEFORM)}
outside = ~R
for nm in nonleg:
    Wn[outside, dc[nm]] = Wold[outside, col[nm]]
leak = float(leg_any[outside].max())
old_nonleg = Wold[:, [col[x] for x in nonleg]]
dom_trunk = np.array(nonleg)[np.argmax(old_nonleg, 1)]
has_trunk = old_nonleg.max(1) > 0
side = np.where(legw["L"] > legw["R"], "L", np.where(legw["R"] > legw["L"], "R", np.where(WS[:, 0] >= 0, "L", "R")))
rv = np.nonzero(R)[0]
for i in rv:
    s = side[i]
    hh = h[i] if not pads[i] else 0.0
    hk_ = HK[s]
    if hh <= hk_:
        wf, ws_, wu = 1 - hh / hk_, hh / hk_, 0.0
    else:
        wf, ws_, wu = 0.0, (1 - hh) / (1 - hk_), (hh - hk_) / (1 - hk_)
    phi = min(max(legw[s][i], 0.0), 1.0)
    row = np.zeros(len(DEFORM))
    row[dc["foot." + s]] += wf; row[dc["shin." + s]] += ws_; row[dc["thigh." + s]] += wu * phi
    row[dc[dom_trunk[i] if has_trunk[i] else "pelvis"]] += wu * (1 - phi)
    Wn[i] = row
Wn /= np.maximum(Wn.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, nm in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=nm)
    for i in np.nonzero(Wn[:, j] > 1e-5)[0]:
        vg.add([int(i)], float(Wn[i, j]), "REPLACE")
# re-weight extent: verts whose weights differ from the seated rig (compare on shared names; legs by chain sum)
Wo_cmp = np.zeros_like(Wn)
for nm in nonleg:
    Wo_cmp[:, dc[nm]] = Wold[:, col[nm]]
for s in ("L", "R"):
    Wo_cmp[:, dc["thigh." + s]] = Wold[:, col["thigh." + s]] + Wold[:, col["shin." + s]]   # seated leg chain total
Wn_cmp = Wn.copy()
for s in ("L", "R"):
    Wn_cmp[:, dc["thigh." + s]] += Wn_cmp[:, dc["shin." + s]] + Wn_cmp[:, dc["foot." + s]]
    Wn_cmp[:, dc["shin." + s]] = 0; Wn_cmp[:, dc["foot." + s]] = 0
changed = np.abs(Wn_cmp - Wo_cmp).max(1) > 1e-3
report["reweight"] = {"region_verts": int(R.sum()), "verts_changed_gt_1e-3": int(changed.sum()),
                      "verts_changed_outside_region": int((changed & ~R).sum()),
                      "seated_leg_weight_leak_outside_region": leak,
                      "max_influences": int((Wn > 1e-5).sum(1).max()),
                      "per_bone_dominant": {nm: int((np.argmax(Wn, 1) == j).sum()) for j, nm in enumerate(DEFORM)},
                      "rule": "outside R: seated weights verbatim; in R: hat functions of h (foot/shin/up at h=0/HK/1), "
                              "'up' split thigh:trunk by the seated leg weight (trunk share -> the vertex's dominant seated trunk bone)"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
mod = low.modifiers.new("Armature", "ARMATURE")
mod.object = rig

# ------------------------------------------------------------------ pose machinery (world = armature space)
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
depth = {}
for b in arm_data.bones:
    d_, p = 0, b.parent
    while p:
        d_ += 1; p = p.parent
    depth[b.name] = d_
LEVELS = sorted(set(depth.values()))
L1 = {s: (RT["thigh." + s] - RH["thigh." + s]).length for s in "LR"}
L2 = {s: (RT["shin." + s] - RH["shin." + s]).length for s in "LR"}
A1 = {s: (RT["upperarm." + s] - RH["upperarm." + s]).length for s in "LR"}
A2 = {s: (RT["forearm." + s] - RH["forearm." + s]).length for s in "LR"}


def perp_pole(hd, kn, tl):
    dv = (tl - hd).normalized()
    p = (kn - hd) - dv * (kn - hd).dot(dv)
    return p.normalized()


POLE_LEG = {s: perp_pole(RH["thigh." + s], RH["shin." + s], RT["shin." + s]) for s in "LR"}
POLE_ARM = {s: perp_pole(RH["upperarm." + s], RH["forearm." + s], RT["forearm." + s]) for s in "LR"}
TV = Vector(DV)                                   # telescope vector
PIVOT = Vector((0.0, -0.35, 0.05)) + TV + Vector(S)   # trunk front-bottom edge (standing rest); seated it sits on the floor
SEAT_PAD = {s: RT["forearm." + s] - TV for s in "LR"}     # seated knuckle pads (rest tails - DV)
ANKLE = {s: RH["foot." + s].copy() for s in "LR"}


def Tm(v):
    return Matrix.Translation(Vector(v))


def G_about(head_new, R3, head_rest):
    """World delta mapping rest -> posed: rotate by R3, carry head_rest to head_new."""
    return Tm(head_new) @ R3.to_4x4() @ Tm(-Vector(head_rest))


def eul(deg):
    return Euler([math.radians(x) for x in deg], "XYZ").to_matrix()


def rot_of(G):
    return G.to_3x3().normalized()


def ik2(H, T, l1, l2, pole):
    d = T - H
    D = d.length
    Dc = min(max(D, abs(l1 - l2) + 1e-5), (l1 + l2) * 0.9999)
    dv = d.normalized()
    pp = pole - dv * pole.dot(dv); pp.normalize()
    a_ = (l1 * l1 - l2 * l2 + Dc * Dc) / (2 * Dc)
    hh = math.sqrt(max(l1 * l1 - a_ * a_, 0.0))
    return H + dv * a_ + pp * hh, H + dv * Dc, D / (l1 + l2)


def aim_R(Rcur, rest_dir, target_dir):
    cur = (Rcur @ rest_dir).normalized()
    q = cur.rotation_difference(target_dir.normalized())
    return q.to_matrix() @ Rcur


reach_log = {}
gap_log = []


def solve(P):
    """P -> {bone: world delta G} (pose matrix = G @ rest)."""
    G = {"root": Matrix.Identity(4)}
    lift = P.get("lift", 0.0)
    d = Vector((P.get("px", 0.0), P.get("py", 0.0), lift))
    Rp = eul(P.get("pelvis", (0, 0, 0)))
    G["pelvis"] = Tm(PIVOT + d) @ Rp.to_4x4() @ Tm(-PIVOT)
    hc = G["pelvis"] @ RH["chest"]
    G["chest"] = G_about(hc, eul(P.get("chest", (0, 0, 0))) @ Rp, RH["chest"])
    hk = G["chest"] @ RH["crown"]
    G["crown"] = G_about(hk, eul(P.get("crown", (0, 0, 0))) @ rot_of(G["chest"]), RH["crown"])
    c = P.get("comp", 0.0)
    for s in "LR":
        # ---- legs: IK from the de-telescoped hip, then telescope (thigh -c*DV, shin -c*HK*DV), foot flat at target
        H = G["pelvis"] @ RH["thigh." + s]
        Hv = H + TV * c
        A_ = Vector(P.get("feet", {}).get(s, ANKLE[s]))
        Kp, Tp, reach = ik2(Hv, A_, L1[s], L2[s], Rp @ POLE_LEG[s])
        reach_log.setdefault("leg." + s, []).append(reach)
        Rt = aim_R(Rp, RT["thigh." + s] - RH["thigh." + s], Kp - Hv)
        G["thigh." + s] = G_about(Hv - TV * c, Rt, RH["thigh." + s])
        Rs = aim_R(Rt, RT["shin." + s] - RH["shin." + s], Tp - Kp)
        G["shin." + s] = G_about(Kp - TV * (c * HK[s]), Rs, RH["shin." + s])
        yaw = P.get("foot_yaw", {}).get(s, 0.0)
        G["foot." + s] = G_about(A_, eul((0, 0, yaw)), RH["foot." + s])
        # ---- arms: FK hang (world euler about the shoulder) blended with planted IK
        Hs = G["pelvis"] @ RH["upperarm." + s]
        ar = P.get("arms", {}).get(s, {})
        Ru_fk = eul(ar.get("rot", (0, 0, 0))) @ Rp
        Gu_fk = G_about(Hs, Ru_fk, RH["upperarm." + s])
        he_fk = Gu_fk @ RH["forearm." + s]
        Rf_fk = eul(ar.get("fore", (0, 0, 0))) @ Ru_fk
        Gf_fk = G_about(he_fk, Rf_fk, RH["forearm." + s])
        plant = ar.get("plant", 0.0)
        if plant <= 0.0:
            G["upperarm." + s], G["forearm." + s] = Gu_fk, Gf_fk
            continue
        tip_fk = Gf_fk @ RT["forearm." + s]
        tgt = Vector(ar.get("target", SEAT_PAD[s]))
        tgt = tgt.lerp(tip_fk, ar.get("peel", 0.0))
        Ke, Te, rch = ik2(Hs, tgt, A1[s], A2[s], Rp @ POLE_ARM[s])
        reach_log.setdefault("arm." + s, []).append(rch)
        Ru_ik = aim_R(Rp, RT["upperarm." + s] - RH["upperarm." + s], Ke - Hs)
        Rf_ik = aim_R(Ru_ik, RT["forearm." + s] - RH["forearm." + s], Te - Ke)
        qu = Ru_fk.to_quaternion().slerp(Ru_ik.to_quaternion(), plant)
        Gu = G_about(Hs, qu.to_matrix(), RH["upperarm." + s])
        he = Gu @ RH["forearm." + s]
        qf = Rf_fk.to_quaternion().slerp(Rf_ik.to_quaternion(), plant)
        G["upperarm." + s], G["forearm." + s] = Gu, G_about(he, qf.to_matrix(), RH["forearm." + s])
    return G


def apply(G):
    for pb in pose:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    for lv in LEVELS:
        for nm in BONES:
            if depth[nm] == lv and nm in G and nm != "root":
                pose[nm].matrix = G[nm] @ REST[nm]
        bpy.context.view_layer.update()


def key_all(frame, prevq):
    for pb in pose:
        if pb.name == "root":
            continue
        q = pb.rotation_quaternion.copy()
        if pb.name in prevq and prevq[pb.name].dot(q) < 0:
            q.negate(); pb.rotation_quaternion = q
        prevq[pb.name] = q.copy()
        pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)
        pb.keyframe_insert("location", frame=frame, group=pb.name)


def mesh_coords():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = low.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


# ------------------------------------------------------------------ proof: sitting is a pose of the standing rest
SEATED = {"lift": -DZ, "py": DY, "comp": 1.0, "arms": {s: {"plant": 1.0} for s in "LR"}}
apply(solve(SEATED))
Cs = mesh_coords()
report["sit_reconstruction_max_mm"] = round(float(np.linalg.norm(Cs - (W0 + S), axis=1).max()) * 1000, 4)
apply({})
Cr = mesh_coords()
report["rest_equals_standing_mesh_max_mm"] = round(float(np.linalg.norm(Cr - WS, axis=1).max()) * 1000, 4)


# ------------------------------------------------------------------ motion
def sn(t, k=1.0, ph=0.0):
    return math.sin(TAU * k * t + ph)


def cs(t, k=1.0, ph=0.0):
    return math.cos(TAU * k * t + ph)


def s01(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (6 * x - 15) + 10)       # smootherstep: zero slope AND curvature at the ends


def win(t, a, b):
    return s01((t - a) / (b - a))


def bump(t, c, w):
    d = abs(t - c)
    return math.cos(0.5 * math.pi * d / w) ** 2 if d < w else 0.0


def pulse(t, c, w=0.25):
    d = (t - c) % 1.0
    return math.sin(math.pi * d / w) if d < w else 0.0


def lerp(a, b, x):
    if isinstance(a, (tuple, list)):
        return tuple(lerp(p, q, x) for p, q in zip(a, b))
    return a + (b - a) * x


def v3add(a, b):
    return tuple(p + q for p, q in zip(a, b))


def idle_P(t):
    """Standing idle: the seated wave's creak-sway ported to the standing rest. Knees carry a
    30 mm base dip (reach headroom) and the bob only ever lowers the hips."""
    return {"lift": -0.030 + 0.006 * sn(t) - 0.004,
            "pelvis": (0.6 * sn(t, 2), 1.2 * sn(t), 0.5 * sn(t, 1, 0.9)),
            "chest": (1.0 * sn(t, 2, -0.5), 1.5 * sn(t, 1, -0.5), 1.0 * sn(t, 1, -0.3)),
            "crown": (1.5 * sn(t, 1, -1.4), 3.0 * sn(t, 1, -1.2), 2.0 * sn(t, 1, -0.4)),
            "arms": {"L": {"rot": (1.2 * sn(t, 1, -0.9), 0.0, 1.0 * sn(t, 1, -0.4)), "fore": (1.5 * sn(t, 1, -1.5), 0, 0)},
                     "R": {"rot": (1.2 * sn(t, 1, -0.9), 0.0, -1.0 * sn(t, 1, -0.4)), "fore": (1.5 * sn(t, 1, -1.5), 0, 0)}}}


def sit_P(t):
    """Sitting idle: dormant, rooted, barely breathing. The base stays exactly on the floor (no
    pelvis motion); a slow chest swell and a crown that drifts a degree."""
    return {"lift": -DZ, "py": DY, "comp": 1.0,
            "chest": (0.6 * sn(t), 0.0, 0.3 * sn(t, 1, 1.1)),
            "crown": (0.9 * sn(t, 1, -0.7), 0.6 * sn(t, 1, -1.3), 0.0),
            "arms": {s: {"plant": 1.0} for s in "LR"}}


def blendP(Pa, Pb, x):
    """Parameter-space blend (x=0 -> Pa, 1 -> Pb). Arms: plant blends, FK rots blend."""
    out = {}
    for k in ("lift", "comp", "px", "py"):
        out[k] = lerp(Pa.get(k, 0.0), Pb.get(k, 0.0), x)
    for k in ("pelvis", "chest", "crown"):
        out[k] = lerp(Pa.get(k, (0, 0, 0)), Pb.get(k, (0, 0, 0)), x)
    out["arms"] = {}
    for s in "LR":
        a_, b_ = Pa.get("arms", {}).get(s, {}), Pb.get("arms", {}).get(s, {})
        out["arms"][s] = {"rot": lerp(a_.get("rot", (0, 0, 0)), b_.get("rot", (0, 0, 0)), x),
                          "fore": lerp(a_.get("fore", (0, 0, 0)), b_.get("fore", (0, 0, 0)), x),
                          "plant": lerp(a_.get("plant", 0.0), b_.get("plant", 0.0), x)}
    return out


I0, S0 = idle_P(0.0), sit_P(0.0)
STAND_S, SIT_S = 4.0, 3.5


def stand_up_P(ts):
    """Seated -> standing, 4.0 s. 0-0.7 stir (crown lifts, chest swells); 0.5-1.3 load (lean
    forward onto the planted knuckles); 1.1-3.0 rise, legs un-telescoping a beat AHEAD of the
    hips so the knees flex and push (roots tearing free: tremble on the hips during the pull);
    arms push while they reach, then peel off the floor; 2.9-4.0 settle (lean overshoots back,
    hips bob once) into idle frame 1."""
    rise = s01((ts - 1.1) / 1.9)
    lead = s01((ts - 0.95) / 1.9)                    # legs extend ahead of the hips -> knee flex
    P = blendP(S0, I0, rise)
    P["lift"] = lerp(S0["lift"], I0["lift"], rise) + 0.022 * bump(ts, 3.05, 0.35) - 0.014 * bump(ts, 3.5, 0.3)
    env = bump(ts, 1.75, 0.6)
    P["lift"] += 0.004 * math.sin(TAU * 7.0 * ts) * env
    P["comp"] = 1.0 - lead
    lean = 10.0 * win(ts, 0.5, 1.3) * (1 - win(ts, 2.6, 3.4)) - 1.8 * bump(ts, 3.45, 0.35)
    P["pelvis"] = v3add(P["pelvis"], (lean, 0.8 * math.sin(TAU * 5.0 * ts) * env, 0.0))
    P["chest"] = v3add(P["chest"], (3.5 * bump(ts, 0.45, 0.45) + 4.0 * bump(ts, 1.5, 0.5) - 3.0 * bump(ts, 2.8, 0.45), 0.0, 0.0))
    P["crown"] = v3add(P["crown"], (-4.0 * bump(ts, 0.4, 0.4) - 6.0 * bump(ts, 2.95, 0.5) + 1.2 * math.sin(TAU * 6.0 * ts) * env, 0.0, 0.0))
    peel = s01((ts - 2.0) / 0.9)
    for s in "LR":
        P["arms"][s]["plant"] = 1.0 - s01((ts - 2.55) / 0.55)
        P["arms"][s]["peel"] = peel
        P["arms"][s]["rot"] = I0["arms"][s]["rot"]
        P["arms"][s]["fore"] = I0["arms"][s]["fore"]
    return P


def sit_down_P(ts):
    """Standing -> seated, 3.5 s. 0-1.2 the knuckles reach down and plant (lean forward);
    0.9-2.7 the hips lower, legs telescoping a beat BEHIND (knees flex under the weight);
    2.6-3.5 settle: the lean rocks back past upright and the crown nods into dormancy."""
    low_ = s01((ts - 0.9) / 1.8)
    lag = s01((ts - 1.05) / 1.8)                     # compression lags the drop -> knees flex, never over-reach
    P = blendP(I0, S0, low_)
    P["lift"] = lerp(I0["lift"], S0["lift"], low_)
    P["comp"] = lag
    lean = 8.0 * win(ts, 0.2, 1.0) * (1 - win(ts, 2.2, 2.9)) - 1.5 * bump(ts, 3.0, 0.4)
    P["pelvis"] = v3add(P["pelvis"], (lean, 0.0, 0.0))
    P["chest"] = v3add(P["chest"], (2.5 * bump(ts, 1.8, 0.6), 0.0, 0.0))
    P["crown"] = v3add(P["crown"], (3.0 * bump(ts, 3.0, 0.45), 0.0, 0.0))
    # the knuckles cannot reach the seated pads from full height (shoulder->pad 1.49 m vs a 1.37 m
    # arm): they come down WITH the hips, touching the floor as the body is ~2/3 lowered
    reach = s01((ts - 0.8) / 1.4)
    for s in "LR":
        P["arms"][s]["plant"] = s01((ts - 0.3) / 0.5)
        P["arms"][s]["peel"] = 1.0 - reach
        P["arms"][s]["rot"] = lerp(I0["arms"][s]["rot"], (0, 0, 0), low_)
        P["arms"][s]["fore"] = lerp(I0["arms"][s]["fore"], (0, 0, 0), low_)
    return P


WALK = dict(N=64, duty=0.75, S=0.28, lift=0.10, offsets={"L": 0.75, "R": 0.25}, arm_swing=11.0)


def gait_target(F0, phase, duty, S, lift):
    """In-place foot path (rig_unit.py): stance = linear belt front->back on the floor; swing =
    cubic Hermite return with end tangents matching the belt, sine lift."""
    if phase < duty:
        u_ = phase / duty
        return (F0[0], F0[1] - S / 2 + S * u_, F0[2])
    u_ = (phase - duty) / (1.0 - duty)
    m = S * (1.0 - duty) / duty
    h00, h10 = 2 * u_ ** 3 - 3 * u_ ** 2 + 1, u_ ** 3 - 2 * u_ ** 2 + u_
    h01, h11 = -2 * u_ ** 3 + 3 * u_ ** 2, u_ ** 3 - u_ ** 2
    y = h00 * (S / 2) + h10 * m + h01 * (-S / 2) + h11 * m
    return (F0[0], F0[1] + y, F0[2] + lift * math.sin(math.pi * u_))


def walk_P(t):
    """Two-beat lumber on the standing rest: L swing [0,.25), double support, R swing [.5,.75),
    double support. Hips ride 65 mm low (bent knees), dip as each foot lands, roll + shift over
    the stance foot; arms swing contralaterally, forearms lag."""
    def lean(tt):
        tt %= 1.0
        if tt < 0.25:
            return -1.0
        if tt < 0.5:
            return -1.0 + 2.0 * s01((tt - 0.25) / 0.25)
        if tt < 0.75:
            return 1.0
        return 1.0 - 2.0 * s01((tt - 0.75) / 0.25)
    dip = pulse(t, 0.25) + pulse(t, 0.75)
    effort = pulse(t, 0.0) + pulse(t, 0.5)
    lw = lean(t)
    P = {"lift": -0.065 - 0.022 * dip + 0.010 * effort, "px": 0.030 * lean(t - 0.03),
         "pelvis": (2.0 + 1.5 * dip, 2.6 * lw, 1.6 * lw),
         "chest": (1.5 * pulse(t, 0.29) + 1.5 * pulse(t, 0.79), 1.6 * lean(t - 0.06), -0.8 * lw),
         "crown": (2.0 * pulse(t, 0.35) + 2.0 * pulse(t, 0.85), 1.8 * lean(t - 0.12), 0.0),
         "feet": {}, "arms": {}}
    wk = WALK
    for s, off in wk["offsets"].items():
        P["feet"][s] = gait_target(tuple(ANKLE[s]), (t + off) % 1.0, wk["duty"], wk["S"], wk["lift"])
    A_ = wk["arm_swing"]
    # R arm forward (negative X rot) while the L foot swings (t ~ .125), and vice versa
    P["arms"]["R"] = {"rot": (-A_ * cs(t, 1, -TAU * 0.125), 0.0, 0.0), "fore": (-0.45 * A_ * cs(t, 1, -TAU * 0.20), 0, 0)}
    P["arms"]["L"] = {"rot": (A_ * cs(t, 1, -TAU * 0.125), 0.0, 0.0), "fore": (0.45 * A_ * cs(t, 1, -TAU * 0.20), 0, 0)}
    return P


CLIPS = [  # name, kind, frames (cycle N for loops / duration for one-shots), param fn
    ("sitting_idle", "loop", 144, lambda f, N: sit_P((f - 1) / N % 1.0)),
    ("stand_up", "oneshot", int(round(STAND_S * FPS)), lambda f, N: stand_up_P((f - 1) / FPS)),
    ("idle", "loop", 96, lambda f, N: idle_P((f - 1) / N % 1.0)),
    ("sit_down", "oneshot", int(round(SIT_S * FPS)), lambda f, N: sit_down_P((f - 1) / FPS)),
    ("walk", "loop", WALK["N"], lambda f, N: walk_P((f - 1) / N % 1.0)),
]
clips = {}
for name, kind, N, fn in CLIPS:
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    reach_log.clear()
    prevq = {}
    for f in range(1, N + 2):
        apply(solve(fn(f, N)))
        key_all(f, prevq)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = kind == "loop"
    clips[name] = {"action": name, "kind": kind, "frames": N + 1,
                   ("cycle_frames" if kind == "loop" else "duration_frames"): N, "seconds": round(N / FPS, 4),
                   "ik_reach_max": {k: round(max(v), 4) for k, v in reach_log.items()}}
    if name == "walk":
        v = WALK["S"] / (WALK["duty"] * N) * FPS
        clips[name].update({"stride_m": WALK["S"], "duty": WALK["duty"], "speed_m_per_s": round(v, 4),
                            "belt_m_per_frame": WALK["S"] / (WALK["duty"] * N)})

# ------------------------------------------------------------------ measure
foot_dom = {s: np.argmax(Wn, 1) == dc["foot." + s] for s in "LR"}
sole = {s: foot_dom[s] & (WS[:, 2] < 0.03) for s in "LR"}    # the pad's sole (standing rest z < 30 mm)
report["pad_sole_verts"] = {s: int(sole[s].sum()) for s in "LR"}
first_frame = {}


def runs_cyclic(flags, cyclic):
    n_ = len(flags)
    if all(flags):
        return [list(range(n_))]
    if not cyclic:
        runs, cur = [], []
        for i in range(n_):
            if flags[i]:
                cur.append(i)
            elif cur:
                runs.append(cur); cur = []
        if cur:
            runs.append(cur)
        return runs
    start = next(i for i in range(n_) if not flags[i])
    runs, cur = [], []
    for k in range(1, n_ + 1):
        i = (start + k) % n_
        if flags[i]:
            cur.append(i)
        elif cur:
            runs.append(cur); cur = []
    if cur:
        runs.append(cur)
    return runs


frame_cache = {}
for name, kind, N, fn in CLIPS:
    info = clips[name]
    K.assign_action(rig, bpy.data.actions[name])
    feet = {s: [] for s in "LR"}
    hands = {s: [] for s in "LR"}
    stretch, gap, root_dev = 0.0, 0.0, 0.0
    lo_, hi_ = np.full(3, 1e9), np.full(3, -1e9)
    sole_min = {s: 1e9 for s in "LR"}
    last = None
    for f in range(1, N + 2):
        scene.frame_set(f)
        for s in "LR":
            feet[s].append(np.array(pose["foot." + s].head))
            hands[s].append(np.array(pose["forearm." + s].tail))
            gap = max(gap, (pose["shin." + s].head - pose["thigh." + s].tail).length,
                      (pose["foot." + s].head - pose["shin." + s].tail).length)
        root_dev = max(root_dev, pose["root"].head.length)
        for pb in pose:
            stretch = max(stretch, abs(pb.length - pb.bone.length) / pb.bone.length)
        C = mesh_coords()
        lo_ = np.minimum(lo_, C.min(0)); hi_ = np.maximum(hi_, C.max(0))
        for s in "LR":
            sole_min[s] = min(sole_min[s], float(C[sole[s], 2].min()))
        if f == 1:
            frame_cache[(name, "first")] = C
        last = C
    frame_cache[(name, "last")] = last
    if kind == "loop":
        info["seam_residual_mm"] = round(float(np.linalg.norm(frame_cache[(name, "first")] - last, axis=1).max()) * 1000, 4)
    info["root_max_offset_mm"] = round(root_dev * 1000, 4)
    info["bone_rigid_stretch_max_pct"] = round(stretch * 100, 5)
    info["leg_telescope_gap_max_mm"] = round(gap * 1000, 1)
    info["extent"] = {"footprint": round(float(max(hi_[0] - lo_[0], hi_[1] - lo_[1])), 4),
                      "width": round(float(hi_[0] - lo_[0]), 4), "depth": round(float(hi_[1] - lo_[1]), 4),
                      "height": round(float(hi_[2] - lo_[2]), 4), "top_z": round(float(hi_[2]), 4), "min_z": round(float(lo_[2]), 4)}
    info["pad_sole_min_z_mm"] = {s: round(v * 1000, 2) for s, v in sole_min.items()}
    belt = info.get("belt_m_per_frame", 0.0)
    slides = {}
    tracks = [("foot." + s, feet[s]) for s in "LR"]
    if name == "sitting_idle":
        tracks += [("forearm.%s:tail" % s, hands[s]) for s in "LR"]
    for lab_, Pt in tracks:
        Pt = np.array(Pt[:N] if kind == "loop" else Pt)
        zmin = Pt[:, 2].min()
        flags = list(Pt[:, 2] <= zmin + 0.0005)
        worst, nruns = 0.0, 0
        for run in runs_cyclic(flags, kind == "loop"):
            if len(run) < 2:
                continue
            nruns += 1
            q = [Pt[i, :2] - np.array([0.0, belt * k]) for k, i in enumerate(run)]
            worst = max(worst, max(np.linalg.norm(a_ - b_) for a_ in q for b_ in q))
        slides[lab_] = {"stance_runs": nruns, "stance_frames": int(sum(flags)), "worst_drift_mm": round(worst * 1000, 4),
                        "contact_z_mm": round(float(zmin) * 1000, 2)}
    info["contact_slide"] = slides

# one-shot joins: the evaluated mesh at the ends vs the neighbouring loop's frame 1
def join(a_, b_):
    return round(float(np.linalg.norm(frame_cache[a_] - frame_cache[b_], axis=1).max()) * 1000, 4)


clips["stand_up"]["join_mm"] = {"first_vs_sitting_idle_f1": join(("stand_up", "first"), ("sitting_idle", "first")),
                                "last_vs_idle_f1": join(("stand_up", "last"), ("idle", "first"))}
clips["sit_down"]["join_mm"] = {"first_vs_idle_f1": join(("sit_down", "first"), ("idle", "first")),
                                "last_vs_sitting_idle_f1": join(("sit_down", "last"), ("sitting_idle", "first"))}
clips["sitting_idle"]["vs_seated_sculpt_max_mm"] = round(float(np.linalg.norm(frame_cache[("sitting_idle", "first")] - (W0 + S), axis=1).max()) * 1000, 2)

# forge animation_check where it applies
try:
    from forge.tools import rigcheck
    for name, kind, N, fn in CLIPS:
        feet_ = ["foot.L", "foot.R"]
        mode = "in_place" if name == "walk" else "planted"
        if name == "sitting_idle":
            feet_ += ["forearm.L:tail", "forearm.R:tail"]
        res = rigcheck.cmd_animation_check({"rig": rig.name, "action": name, "mode": mode, "feet": feet_})
        seam = res.get("loop_seam_closure") or {}
        clips[name]["forge_animation_check"] = {
            "mode": res.get("mode"), "gate": res.get("gate"), "deformation_gate": res.get("deformation_gate"),
            "feet": [{"bone": f_["bone"], "steps": f_["steps_measured"], "worst_drift_mm": f_["worst_drift_mm"],
                      "verdict": f_["verdict"]} for f_ in res.get("feet", [])],
            "loop_seam_closure": {k: seam.get(k) for k in ("verdict", "says") if k in seam},
            "says": res.get("says")}
except Exception:
    import traceback
    report["forge_animation_check_error"] = traceback.format_exc()[-1500:]

# ------------------------------------------------------------------ finish
rig.animation_data.action = None
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
report["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                    "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]}
                   for b in arm_data.bones]
report["bone_count"] = len(arm_data.bones)
report["leg_chain_lengths_m"] = {s: {"thigh": round(L1[s], 4), "shin": round(L2[s], 4)} for s in "LR"}
report["clips"] = clips
rig["conquest_rig"] = "archetype-minimal v2 standing-rest (eldroot)"
low["conquest_clips"] = [c[0] for c in CLIPS]
low["conquest_state_clips"] = ["sitting_idle", "stand_up", "sit_down"]
low["conquest_rest_stance"] = "standing"
report["seconds"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_BLEND), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, copy=True, compress=True)
json.dump(report, open(OUT_JSON, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("STAND_DONE", json.dumps({k: report[k] for k in ("sit_reconstruction_max_mm", "rest_equals_standing_mesh_max_mm", "seconds")}))
sys.stdout.flush()
