"""Eldroot standing v4 = v3 (below) + the artist's v3 verdict (review-log 2026-09-25, "yes split the skirt front. also
can we make the arms and shoulder wider/larger it looks small now in comparison to his long legs, we can also make the
legs a little smaller"):
  I. PROPORTIONS. Arms: every arm-weighted vertex of the seated sculpt (and the high master through the low surface,
     barycentric) is displaced by a smooth field: radial growth x K_ARM about the upperarm/forearm bone chain (the
     knuckle club grows about the forearm axis, the strut about its own; nothing below the knuckle floor contact) +
     a lateral shoulder shift DX_SH; the blend weight is the seated arm weight diffused over the mesh, faded out over
     the face (|x| < 0.55). Legs: RATIO (standing/seated) 2.0 -> RATIO_V4 (shorter chain), thinner columns (LEG_SLIM)
     with the feet re-widened by the same factor (soles unchanged). Cell budget: widening the arms breaks the approved
     3.8 m 4-cell footprint that set the sculpt's scale (improve_unit cell-budget rule, kf = 3.8 / footprint); the
     same rule is re-applied as a final uniform refit of mesh + rig + keys (--no-cell-refit skips it). Numbers are
     quoted pre- and post-refit.
  J. TASSETS: the tree-skirt's FRONT half (side to side, the traced W) is split into 3 bark plates per side; the back
     half stays the pelvis-riding shell (bone 'skirt', v3 floor ride). Plate seams are bark splits: jagged, V-opening
     toward the hem, irregular (seeded, like the collar crowns); the W hem is evaluated at every plate sample so the
     traced outline survives the split. Each plate has its own bone (tasset.<side>.<k>, child of its thigh), hinged on
     the belt (its top edge at the attach line, horizontal tangent axis): the plate swings OUT about the belt as its
     thigh folds - the sit target is searched per plate on the sitting pose (smallest swing that clears thighs, shins,
     feet, knuckles, the trunk and the floor), every other frame takes max(fold-driven, clearance-driven) along the
     rest -> sit path, smoothed in time.
  Everything else (collars, trace calibration, bake groups, clip choreography, gates) is v3's, re-derived on the new
  proportions. v3 files stay untouched.

Eldroot standing v3 = v2 (below, unchanged) + the artist's v2-sheet verdict (review-log 2026-09-25, annotated
refs design/refs/eldroot-v3-*.png, strokes registered onto the v2 front render and back-projected through the exact
sheet camera by eldroot3_trace_refs.py -> design/refs/eldroot-v3-trace.json):
  G. TREE-SKIRT: a rigid bark skirt plate (closed shell, 5 cm) around the lower trunk, emerging from the trunk at the
     drawn attach height and flaring over the thigh tops; hem = the traced W (outer corners low over the outer thighs,
     notches over the inner thighs, a pointed centre tip). Its inner surface clears the thighs swept through the
     walk/idle range (pelvis space) by SK_CL. The hem is calibrated IN THE DRAWING'S VIEW (projected through the v2
     sheet camera onto the traced stroke's pixel rows). One bone 'skirt' (child of pelvis): identity in idle/walk
     (the hem IS the trunk there); in the sit states it rides on the floor (lift = exactly what keeps the hem off
     the ground) and takes a small searched front-up pitch (SK_SIT) blended in by the thigh fold - a
     rigid-with-pelvis hem would sit ~0.7 m under the floor in sitting_idle (measured in the report).
  H. TRUNK COLLARS: the drawn M points UP (trace: tips 0.20-0.41 m above the knee joint, legs of the M 0.1-0.3 m
     below it) -> the LOWER piece's top rim rises as a jagged bark crown around the upper piece's end (shin crown at
     the knee, foot crown at the ankle), 4-7 irregular teeth each, hollow (the upper piece telescopes into it).
  Bake / weights / clips / measurements extended accordingly. v2's numbers (ratio, feet, clips) are untouched inputs.

Eldroot standing v2: FULL leg extension (artist 2026-09-25 v2 spec: "the two legs in front should be
fully extended, meaning he should double in height, hes just sitting in the air now" + "we may need model
changes to make the feet wider as well to sell it better"). v1 (rigged/eldroot_standing.blend, a 0.45 m
harmonic lift) is the REJECTED baseline and stays on disk untouched.

    blender --background rigged/eldroot.blend --factory-startup --python eldroot_stand2.py --
            <out.blend> <out.json> [--ratio 2.0] [--geo-only]

Opens the SEATED rig (rigged/eldroot.blend), never saves over it (save_as_mainfile copy=True). Reads the
high-poly master from source-copies/ancient_tree.blend (byte copy) and rebuilds it into game units with
improve_unit.py's exact steps (world transform, eldroot Taubin fix, cell-budget scale) + a measured
translation fit to the approved low.

WHY A MODEL CHANGE (measured, see report "measure"): the sculpted front legs are 1.2 m knee-up columns
whose upper half (z 0.47-1.16) is FUSED to the trunk front along a 2.9 m junction loop (0.35 m2 per leg),
and they stand 1.0 m IN FRONT of the trunk's centre of mass. Unfolded as sculpted they give <1.5x height,
and no deformation of the fused surface can put a vertical column under the body without passing through
it (v1 measured the folds). So:
  A. LEG COLUMN PROFILE: star-ray sampling of the high-poly column (180 azimuths x 6 mm rings + a 30-ring
     hemispherical cap from the knee-cap centre); rays whose hit lands on trunk-owned surface (nearest low
     vertex not leg-dominant) are the fused back -> filled by periodic interpolation + the opposite side's
     high-frequency detail. Result: the sculpted column, closed.
  B. TRUNK: the low's leg faces (+ junction faces + the trunk-weighted inner knee lobes in front of the belly)
     removed; each front hole closed by a constrained-Delaunay membrane (bi-Laplacian lift, boundary 1-ring
     fixed; closure audited: every edge shared once per direction). Trunk + arms move RIGIDLY (stretch 1.000,
     0 flips).
  C. LEGS (new, rigid shells, one bone each): thigh (hip -> knee), shin (knee -> ankle, the sculpted column
     with its mossy knee cap on top), foot (the column's pad, WIDENED + flattened: flat sole, root-toe lobes).
     Leg lengths are SOLVED so that (i) standing height = RATIO x seated height with straight, vertical-plane
     columns under the trunk's centre of mass and (ii) sitting (trunk back on its sculpted base, feet planted)
     keeps the shins vertical in front where the sculpt has them.
  D. LOOK: fresh Smart-UV (improve_unit params) + normal/AO re-bake of the WHOLE standing low from a standing
     high master (trunk high minus legs + membrane high + shell highs); trunk corner colours copied verbatim,
     new faces coloured by improve_unit's eldroot region rules (cavity from the shell highs).
  E. RIG: 14 bones (seated trunk/arm bones moved rigidly + thigh/shin/foot per leg); trunk weights verbatim
     (seated thigh/shin shares -> pelvis), membrane weights from its boundary, shells rigid (weight 1).
  F. CLIPS, 24 fps, in place, every frame keyed: sitting_idle, stand_up, idle, sit_down, walk.
"""
import bpy, bmesh, sys, os, json, math, time, hashlib
import numpy as np
from mathutils import Vector, Matrix, Euler, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils import geometry as mgeo

T0 = time.time()
argv = sys.argv[sys.argv.index("--") + 1:]
OUT_BLEND, OUT_JSON = argv[0], argv[1]


def opt(name, default, cast=float):
    return cast(argv[argv.index(name) + 1]) if name in argv else default


HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "addon")))
import rigkit as K  # noqa: E402

SRC_HIGH = os.path.join(PRJ, "source-copies", "ancient_tree.blend")
TEX_DIR = os.path.join(PRJ, "improved", "textures")
FPS = K.FPS
TAU = 2 * math.pi
RATIO = opt("--ratio", 1.88)         # standing height / seated height, pre-refit (v2/v3: 2.0; v4 "legs a little
                                    # smaller": the hip->ankle chain 2.916 -> 2.591 m, -11 %)
X_HIP = 0.45                        # hip joints (trunk coords): inside the trunk base, 0.35 above it
Z_HIP = 0.35
Z_ANK = 0.15                        # ankle joint height (inside the 0.30 m foot shell)
LEG_SLIM = opt("--leg-slim", 0.88)   # v4: leg columns thinner by this (v3 1.25 x 1.45 thickening)
LEG_SX, LEG_SY = 1.25 * LEG_SLIM, 1.45 * LEG_SLIM
LEG_CUT = 0.05                      # trunk faces with ANY seated leg weight > this go with the legs (the crease band)
FOOT_W = 1.42 / LEG_SLIM            # foot pad radius factor at the sole, vs the (thinner) leg column: v3's sole kept
                                    # (artist: feet stay wide and flat)
K_ARM = opt("--arm-k", 1.22)         # v4 arm growth about the bone chain (cross-section area x K^2 = +49 %)
DX_SH = opt("--shoulder-dx", 0.12)   # v4 shoulder widening per side (m, sculpt scale)
CELL_FP = 3.8                       # approved 4-cell footprint (improve_unit SPEC eldroot max_fp)
CELL_REFIT = "--no-cell-refit" not in argv
# ---- v4 tassets
TS_SEAMS = {"L": (0.0, 22.0, 47.0, 70.0), "R": (0.0, 25.0, 45.0, 70.0)}   # plate seams (deg from the front centre,
                                    # per side; irregular like the crowns' teeth); the last = the back shell's edge
                                    # (front arc 140 deg: at 90 the thigh-carried side plates swung their belt lips out
                                    # of the trunk and into the floor-riding back shell, 36-45 mm, first v4 sit)
TS_GAP = (0.040, 0.014)             # bark split opening at the hem / at the belt (m)
TS_GAP_BACK = (0.080, 0.050)        # ... at the two tasset / back-shell seams: the shell pitches and floor-rides while the
                                    # plates fold about the hip line, so that split opens wider
TS_ZIG = 0.035                      # split zigzag amplitude (m, tangential)
TS_MODE = "thigh" if "--tasset-belt" not in argv else "belt"
TS_FOLD = (26.0, 60.0) if TS_MODE == "thigh" else (35.0, 110.0)   # thigh fold (deg off the pelvis-down axis) window:
                                    # 'thigh' mode - the fraction of the thigh's swing a plate follows (0 through the
                                    # walk's 24.3 deg, all of it past 60); 'belt' mode - the swing to the searched sit
TS_MARGIN = 0.012                   # clearance the plate search keeps from legs / knuckles / trunk (m)
TS_TUCK_LAMBDA = opt("--tuck-lambda", 0.0)    # tuck DP: outside probe points per metre of neighbour relative motion
FOOT_COLLAR = 1.1                   # foot top collar radius factor (encloses the shin through the walk tilt)
FOOT_FWD = 0.15                     # sole centre ahead of the ankle (toes forward) -> pads centred under the COM
TOE_A = 0.28                        # root-toe lobe amplitude (5 lobes, one straight ahead)
THIGH_TAPER = (0.85, 1.15)          # thigh radius factor knee -> hip (narrower at the knee: the cap lip shows)
NT_HI, NT_LO = 180, 28              # azimuth samples: high master / low game mesh
DZ_HI = 0.006                       # high ring spacing (m)
# ---- v3: trunk collars (trace: design/refs/eldroot-v3-trace.json, knee strokes on the plane y = -0.22)
CR_T = 0.045                        # crown wall thickness (m)
CR_CL = 0.035                       # crown inner wall -> upper piece air gap at rest (m)
KNEE_TEETH = {"L": 5, "R": 6}       # teeth per collar (brief: 4-7, irregular)
ANK_TEETH = {"L": 4, "R": 7}
KNEE_TIP = (0.20, 0.41)             # traced tip heights above the knee joint (m)
KNEE_VAL = (-0.02, 0.10)            # traced valley heights above the knee joint (m)
ANK_TIP = (0.20, 0.32)              # ankle crown tips above the foot's 0.30 m collar ring
ANK_VAL = (0.05, 0.11)
# ---- v3: tree-skirt (trace: skirt strokes on the plane y = -0.90, heights below the trunk base Z_BASE)
SK_N, SK_NHI = 48, 288              # skirt azimuths (sample 0 = front centre, the traced tip), high
SK_T = 0.05                         # plate thickness (m)
SK_CL = 0.03                        # inner surface -> swept thigh air gap (m)
SK_TR = 0.02                        # outer surface proud of the trunk at the attach line (m)
SK_ATT = 0.58                       # attach height above Z_BASE (traced side-line tops z 3.29-3.33)
SK_ATT_SIDE = 0.08                  # attach raised this much at the sides (x cos^2 phi): clears the thigh hip dome
SK_TIP, SK_PEAK = 0.44, (0.11, 0.18)   # traced centre tip / inner-thigh notch depths below Z_BASE (-x, +x side)
SK_CORNER = (0.48, 0.55)            # traced outer corner depths below Z_BASE (-x, +x side)
SK_BACK = 0.70                      # back half: the front profile's depths x this (no drawing of the back)
SK_BACK_TEETH = [(37.5, 0.0), (45.0, 0.13), (52.5, 0.0), (67.5, 0.09), (82.5, 0.0), (90.0, 0.17), (97.5, 0.0),
                 (112.5, 0.0), (120.0, 0.11), (135.0, 0.0), (142.5, 0.15), (150.0, 0.0)]   # (azimuth deg, extra depth m)
SK_FLARE_MIN = (0.04, 0.10)         # minimum hem-vs-attach flare, front/back and sides (x cos^2 phi)
SK_SWEEP_PITCH = (-27.0, 6.0)       # thigh pitch rel. pelvis swept by the clearance hull (v2 walk/idle: -23.1..+2.7)
SK_SWEEP_ROLL = 3.0                 # thigh roll rel. pelvis (v2 walk: +-1.6)
SK_FLOOR = 0.010                    # sit: the hem rides this far above the floor (the seated base clears it by 10.6 mm)
report = {"unit": "eldroot", "stage": "standing v4 - thigh-hinged tasset plates + proportion rebalance (bigger arms/shoulders, shorter legs) on v3",
          "source": bpy.data.filepath, "fps": FPS, "ratio_target_pre_refit": RATIO,
          "rejected_baseline": "rigged/eldroot_standing.blend (v1, 0.45 m harmonic lift, height 3.16 m = 1.17x)"}

scene = bpy.context.scene
scene.render.fps = FPS
scene.render.fps_base = 1.0
low = bpy.data.objects["eldroot"]
old_rig = bpy.data.objects["eldroot_rig"]
report["seated_actions_removed"] = [a.name for a in bpy.data.actions]
for a in list(bpy.data.actions):
    bpy.data.actions.remove(a)
assert np.abs(np.array(low.matrix_world) - np.eye(4)).max() < 1e-9
me0 = low.data
n0 = len(me0.vertices)


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


W0 = np.empty(n0 * 3); me0.vertices.foreach_get("co", W0); W0 = W0.reshape(-1, 3)
F0 = np.empty(len(me0.polygons) * 3, dtype=np.int64); me0.polygons.foreach_get("vertices", F0); F0 = F0.reshape(-1, 3)
H_SEAT = float(W0[:, 2].max() - W0[:, 2].min())
old_names = [g.name for g in low.vertex_groups]
Wold = np.zeros((n0, len(old_names)))
for v in me0.vertices:
    for g in v.groups:
        Wold[v.index, g.group] = g.weight
col_ = {nm: i for i, nm in enumerate(old_names)}
legw = {s: Wold[:, col_["thigh." + s]] + Wold[:, col_["shin." + s]] for s in "LR"}
leg_any = legw["L"] + legw["R"]
legdom = leg_any >= 0.5
report["seated"] = {"verts": n0, "tris": len(F0), "height_m": round(H_SEAT, 4), "coords_sha": sha(W0),
                    "bbox": [W0.min(0).round(4).tolist(), W0.max(0).round(4).tolist()]}
old_bones = {b.name: (np.array(b.head_local), np.array(b.tail_local), b.parent.name if b.parent else None)
             for b in old_rig.data.bones}
report["seated_leg_bone_chain_m"] = {s: {"thigh": round(float(np.linalg.norm(old_bones["thigh." + s][1] - old_bones["thigh." + s][0])), 4),
                                         "shin": round(float(np.linalg.norm(old_bones["shin." + s][1] - old_bones["shin." + s][0])), 4)}
                                     for s in "LR"}

# ====================================================================== 1. high-poly master (improve_unit steps)
t1 = time.time()
with bpy.data.libraries.load(SRC_HIGH) as (src_, dst_):
    dst_.objects = list(src_.objects)
tmpc = bpy.data.collections.new("hi_src"); scene.collection.children.link(tmpc)
for o in dst_.objects:
    if o is not None:
        tmpc.objects.link(o)
bpy.context.view_layer.update()
srco = max([o for o in dst_.objects if o is not None and o.type == "MESH"], key=lambda o: len(o.data.vertices))
me_hi = srco.data.copy(); me_hi.name = "eldroot2_high_src"
me_hi.transform(srco.matrix_world)
hi_smooth_frac = float(np.mean([p.use_smooth for p in me_hi.polygons]))
for o in list(tmpc.objects):
    bpy.data.objects.remove(o, do_unlink=True)
bpy.data.collections.remove(tmpc)


def read_mesh(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co)
    ev = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev)
    return co.reshape(-1, 3), ev.reshape(-1, 2)


def neighbour_mean(W, ev, deg):
    s = np.zeros_like(W)
    for k in range(W.shape[1]):
        s[:, k] = np.bincount(ev[:, 0], W[ev[:, 1], k], minlength=len(W)) + np.bincount(ev[:, 1], W[ev[:, 0], k], minlength=len(W))
    return s / np.maximum(deg, 1)[:, None]


def taubin(W, ev, w, iters, lam=0.5, mu=-0.53):
    deg = np.bincount(ev.ravel(), minlength=len(W)).astype(float)
    W = W.copy(); ww = w[:, None]
    for _ in range(iters):
        W += lam * ww * (neighbour_mean(W, ev, deg) - W)
        W += mu * ww * (neighbour_mean(W, ev, deg) - W)
    return W


WH, EVH = read_mesh(me_hi)
lo_, hi_ = WH.min(0), WH.max(0)
hh = (WH[:, 2] - lo_[2]) / (hi_[2] - lo_[2])
nv_ = np.empty(len(me_hi.vertices) * 3); me_hi.vertex_normals.foreach_get("vector", nv_); NRMH = nv_.reshape(-1, 3)
w_side = smoothstep(0.50, 0.58, hh) * smoothstep(0.30, 0.60, np.abs(NRMH[:, 0]))
WH = taubin(WH, EVH, w_side, 60)                                   # improve_unit step 2 (eldroot)
lo_, hi_ = WH.min(0), WH.max(0); size = hi_ - lo_
kf = min(3.4 / size[2], 3.8 / max(size[0], size[1]))              # improve_unit step 3 (cell budget)
WH = (WH - np.array([(lo_[0] + hi_[0]) / 2, (lo_[1] + hi_[1]) / 2, lo_[2]])) * kf
me_hi.calc_loop_triangles()
FH = np.empty(len(me_hi.loop_triangles) * 3, dtype=np.int64); me_hi.loop_triangles.foreach_get("vertices", FH); FH = FH.reshape(-1, 3)
# translation fit high -> approved low (improve_unit's feet shift came from the decimated low's bbox)
tfit = np.zeros(3)
sub = W0[:: max(1, n0 // 4000)]
bvh0 = BVHTree.FromPolygons(WH.tolist(), FH.tolist())
for it in range(4):
    res = np.array([np.array(p - tfit) - np.array(bvh0.find_nearest(Vector(p - tfit))[0]) for p in sub])
    tfit += res.mean(0)
res = np.array([np.linalg.norm(np.array(p - tfit) - np.array(bvh0.find_nearest(Vector(p - tfit))[0])) for p in sub])
WH = WH + tfit

# ====================================================================== 1b. v4 ARMS + SHOULDERS (seated coords)
# Smooth displacement field on the low: D = ws * ((K_ARM - 1) (p - c) + DX_SH x_side), c = the nearest point of the
# side's upperarm -> forearm chain (perpendicular-only past the shoulder end, so the root swells instead of pulling
# into the chest; radial about the knuckle end below the forearm tail). ws = the seated arm weight diffused over the
# mesh (12 x neighbour mean, smoothstep 0.1-0.9), faded to 0 over |x| < 0.55 (the face). The high master follows the
# low surface (barycentric field at each high vertex's nearest low point). The knuckles keep their floor contact
# (nothing below the seated arm minimum).
t1b = time.time()
ev0_ = np.empty(len(me0.edges) * 2, dtype=np.int64); me0.edges.foreach_get("vertices", ev0_); ev0_ = ev0_.reshape(-1, 2)
deg0_ = np.bincount(ev0_.ravel(), minlength=n0).astype(float)
DARM = np.zeros((n0, 3))
arm_rep = {}
W0_seat = W0.copy()
for s_, sx_ in (("L", 1.0), ("R", -1.0)):
    wa_ = Wold[:, col_["upperarm." + s_]] + Wold[:, col_["forearm." + s_]]
    wd_ = wa_[:, None].copy()
    for _ in range(12):
        wd_ = 0.5 * wd_ + 0.5 * neighbour_mean(wd_, ev0_, deg0_)
    ws_ = smoothstep(0.1, 0.9, wd_[:, 0]) * smoothstep(0.55, 0.75, sx_ * W0[:, 0])
    A_, B_, C_ = (old_bones["upperarm." + s_][0], old_bones["upperarm." + s_][1], old_bones["forearm." + s_][1])
    ab_ = B_ - A_; bc_ = C_ - B_
    t_ab = ((W0 - A_) @ ab_) / (ab_ @ ab_)
    c_ab = A_ + np.minimum(t_ab, 1.0)[:, None] * ab_                      # unclamped below 0: perpendicular only
    t_bc = np.clip(((W0 - B_) @ bc_) / (bc_ @ bc_), 0.0, 1.0)
    c_bc = B_ + t_bc[:, None] * bc_
    use_bc = np.linalg.norm(W0 - c_bc, axis=1) < np.linalg.norm(W0 - c_ab, axis=1)
    c_ = np.where(use_bc[:, None], c_bc, c_ab)
    D_ = ws_[:, None] * ((K_ARM - 1.0) * (W0 - c_) + np.array([sx_ * DX_SH, 0.0, 0.0]))
    DARM += D_
    arm_rep[s_] = {"blend_verts_0.02_0.98": int(((ws_ > 0.02) & (ws_ < 0.98)).sum()), "full_verts": int((ws_ >= 0.98).sum()),
                   "max_disp_m": round(float(np.linalg.norm(D_, axis=1).max()), 4)}
arm_dom0 = np.array([nm.startswith(("upperarm", "forearm")) for nm in old_names])[np.argmax(Wold, 1)]
ZMIN_ARM = float(W0[arm_dom0, 2].min())
W0 = W0 + DARM
W0[:, 2] = np.where(np.abs(DARM).sum(1) > 0, np.maximum(W0[:, 2], np.minimum(W0_seat[:, 2], ZMIN_ARM)), W0[:, 2])
DARM = W0 - W0_seat
me0.vertices.foreach_set("co", W0.ravel()); me0.update()
for s_, sx_ in (("L", 1.0), ("R", -1.0)):                     # the arm chain moves out with the shoulders
    for nm_ in ("upperarm." + s_, "forearm." + s_):
        hd_, tl_, par_ = old_bones[nm_]
        old_bones[nm_] = (hd_ + np.array([sx_ * DX_SH, 0, 0]), tl_ + np.array([sx_ * DX_SH, 0, 0]), par_)
# high master: barycentric displacement at the nearest low point (only where the low field is non-zero nearby)
bvh_seat = BVHTree.FromPolygons(W0_seat.tolist(), F0.tolist())
near_ = np.abs(WH[:, 0]) > 0.45
DH = np.zeros_like(WH)
for i in np.nonzero(near_)[0]:
    loc, _, fi, _ = bvh_seat.find_nearest(Vector(WH[i]))
    if fi is None:
        continue
    tri = F0[fi]
    a3_, b3_, c3_ = W0_seat[tri]
    bw = mgeo.barycentric_transform(loc, Vector(a3_), Vector(b3_), Vector(c3_), Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    DH[i] = bw.x * DARM[tri[0]] + bw.y * DARM[tri[1]] + bw.z * DARM[tri[2]]
WH = WH + DH
lo_s, hi_s = W0_seat.min(0), W0_seat.max(0)
lo_a, hi_a = W0.min(0), W0.max(0)
ARM_AXIS_R = {}
for s_ in "LR":
    fm_ = np.argmax(Wold, 1) == col_["forearm." + s_]
    A_, C_ = old_bones["forearm." + s_][0], old_bones["forearm." + s_][1]
    u_ = (C_ - A_) / np.linalg.norm(C_ - A_)
    for tag_, P_, off_ in (("before", W0_seat[fm_], np.array([(1 if s_ == "L" else -1) * DX_SH, 0, 0])), ("after", W0[fm_], np.zeros(3))):
        q_ = P_ + off_ - A_
        rad_ = q_ - np.outer(q_ @ u_, u_)
        ARM_AXIS_R.setdefault(s_, {})[tag_] = {"knuckle_club_radius_mean_m": round(float(np.linalg.norm(rad_, axis=1).mean()), 4),
                                              "knuckle_club_radius_max_m": round(float(np.linalg.norm(rad_, axis=1).max()), 4)}
report["v4_arms"] = {"K_ARM": K_ARM, "shoulder_dx_per_side_m": DX_SH, "per_side": arm_rep,
                     "cross_section_area_factor": round(K_ARM ** 2, 4),
                     "seated_width_m": {"before": round(float(hi_s[0] - lo_s[0]), 4), "after": round(float(hi_a[0] - lo_a[0]), 4)},
                     "shoulder_span_m (upperarm heads)": {"before": round(2 * (float(old_bones["upperarm.L"][0][0]) - DX_SH), 4),
                                                          "after": round(2 * float(old_bones["upperarm.L"][0][0]), 4)},
                     "forearm_club_radius": ARM_AXIS_R, "knuckle_floor_z_m": round(ZMIN_ARM, 4),
                     "seated_min_z_after_m": round(float(W0[:, 2].min()), 4),
                     "high_verts_displaced": int((np.abs(DH).sum(1) > 1e-7).sum()), "seconds": round(time.time() - t1b, 1),
                     "rule": "D = ws ((K-1)(p - nearest chain point) + DX x_side); ws = diffused seated arm weight x face fade; high by barycentric field"}
bvhH = BVHTree.FromPolygons(WH.tolist(), FH.tolist())
report["high_master"] = {"source": SRC_HIGH, "tris": int(len(FH)), "verts": int(len(WH)),
                         "scale_factor": round(float(kf), 6), "scale_factor_improve_report": 0.056205,
                         "translation_fit_m": tfit.round(5).tolist(),
                         "low_to_high_residual_mm": {"mean": round(float(res.mean()) * 1000, 2), "p95": round(float(np.percentile(res, 95)) * 1000, 2),
                                                     "max": round(float(res.max()) * 1000, 2)},
                         "smooth_face_fraction": round(hi_smooth_frac, 3), "seconds": round(time.time() - t1, 1)}
# cavity field normalisation of the approved look (improve_unit: CAV / p95|CAV|)
degH = np.bincount(EVH.ravel(), minlength=len(WH)).astype(float)
me_hi.vertices.foreach_set("co", WH.ravel()); me_hi.update()
nv_ = np.empty(len(me_hi.vertices) * 3); me_hi.vertex_normals.foreach_get("vector", nv_); NRMH = nv_.reshape(-1, 3)
elH = np.linalg.norm(WH[EVH[:, 0]] - WH[EVH[:, 1]], axis=1).mean()
CAVH = ((neighbour_mean(WH, EVH, degH) - WH) * NRMH).sum(1) / elH
for _ in range(6):
    CAVH = neighbour_mean(CAVH[:, None], EVH, degH)[:, 0] * 0.5 + CAVH * 0.5
CAV_SCALE = float(np.percentile(np.abs(CAVH), 95) + 1e-9)

# ====================================================================== 2. MEASURE the sculpted legs
kd0 = KDTree(n0)
for i, p in enumerate(W0):
    kd0.insert(p, i)
kd0.balance()
measure = {}
AXIS = {}
for s in "LR":
    m = legw[s] >= 0.5
    P = W0[m]
    # centroid polyline of the leg column (5 cm slabs) -> arc length; free vs fused span from the junction loop
    zs = np.arange(0.0, P[:, 2].max() + 1e-9, 0.10)
    cents = [P[(P[:, 2] >= z) & (P[:, 2] < z + 0.10)].mean(0) for z in zs if ((P[:, 2] >= z) & (P[:, 2] < z + 0.10)).any()]
    cents = np.array(cents)
    cents[1:-1] = (cents[:-2] + 2 * cents[1:-1] + cents[2:]) / 4.0     # slab centroids jitter where the column fuses
    arc = float(np.linalg.norm(np.diff(cents, axis=0), axis=1).sum())
    free_rings = P[(P[:, 2] > 0.10) & (P[:, 2] < 0.45)]
    AXIS[s] = np.array([np.median(free_rings[:, 0]), np.median(free_rings[:, 1])])
    measure[s] = {"column_top_z_m": round(float(P[:, 2].max()), 4), "centroid_polyline_arc_m": round(arc, 4),
                  "leg_verts": int(m.sum())}
# junction loops (seated): boundary of the leg-dominant face set
from collections import defaultdict
for s in "LR":
    fm = (legw[s] >= 0.5)[F0].all(1)
    ec = defaultdict(int)
    for f in F0[fm]:
        for k in range(3):
            a_, b_ = sorted((int(f[k]), int(f[(k + 1) % 3]))); ec[(a_, b_)] += 1
    bd = np.array([e for e, c in ec.items() if c == 1])
    bv = np.unique(bd)
    measure[s].update({"junction_loop_verts": int(len(bv)), "junction_loop_length_m": round(float(np.linalg.norm(W0[bd[:, 0]] - W0[bd[:, 1]], axis=1).sum()), 3),
                       "fused_z_range_m": [round(float(W0[bv, 2].min()), 3), round(float(W0[bv, 2].max()), 3)],
                       "free_column_below_m": round(float(W0[bv, 2].min()), 3)})
measure["unfolded_as_sculpted"] = {
    "bone_chain_m": round(float(np.mean([sum(v.values()) for v in report["seated_leg_bone_chain_m"].values()])), 4),
    "geometry_arc_m": round(float(np.mean([measure[s]["centroid_polyline_arc_m"] for s in "LR"])), 4),
    "note": "the column is a knee-up shin; its thigh is not sculpted (fused into the trunk front). Standing on the "
            "sculpted column alone lifts the trunk base by at most its arc length"}
arc_mean = measure["unfolded_as_sculpted"]["geometry_arc_m"]
measure["unfolded_as_sculpted"]["max_height_m"] = round(H_SEAT + arc_mean, 3)
measure["unfolded_as_sculpted"]["max_ratio"] = round((H_SEAT + arc_mean) / H_SEAT, 3)
report["measure"] = measure

# ====================================================================== 3. column profile (star rays on the high)
Z0S, ZC = 0.04, 0.95
ZTOP = float(np.mean([measure[s]["column_top_z_m"] for s in "LR"]))
ZS = np.arange(Z0S, ZC + 1e-9, DZ_HI)
TH = np.arange(NT_HI) * TAU / NT_HI
PHI = np.radians(np.linspace(0.0, 88.0, 30))
COL = {}


def fill_rings(R, V):
    """Per ring: periodic interpolation across invalid azimuths + the opposite side's detail."""
    R = R.copy(); N = R.shape[1]
    th = np.arange(N)
    filled = ~V
    good = V.sum(1) >= 0.3 * N
    for i in range(R.shape[0]):
        if not good[i]:
            continue
        idx = np.nonzero(V[i])[0]
        if len(idx) == N:
            continue
        xp = np.concatenate([idx - N, idx, idx + N]); fp = np.tile(R[i, idx], 3)
        sm = np.interp(th, xp, fp)
        base = np.where(V[i], R[i], sm)
        k = 15
        pad = np.concatenate([base[-k:], base, base[:k]])
        avg = np.convolve(pad, np.ones(2 * k + 1) / (2 * k + 1), mode="same")[k:-k]
        det = base - avg
        opp = (th + N // 2) % N
        R[i] = np.where(V[i], R[i], sm + np.where(V[i, opp], det[opp], 0.0))
    for i in np.nonzero(~good)[0]:                    # whole ring unusable -> nearest usable rings
        gi = np.nonzero(good)[0]
        j = gi[np.argmin(np.abs(gi - i))]
        R[i] = R[j]; filled[i] = True
    for _ in range(2):                                # blend filled cells along the column
        Rs = R.copy()
        Rs[1:-1] = 0.25 * R[:-2] + 0.5 * R[1:-1] + 0.25 * R[2:]
        R = np.where(filled, Rs, R)
    return R, filled


for s in "LR":
    ax, ay = AXIS[s]
    own = legw[s] >= 0.5

    def ray(o, d):
        hit = bvhH.ray_cast(Vector(o), Vector(d), 0.9)
        if hit[0] is None:
            return 0.0, False
        p = hit[0]
        _, idx, _ = kd0.find(p)
        return float(hit[3]), bool(own[idx])
    RSd = np.zeros((len(ZS), NT_HI)); VS = np.zeros_like(RSd, dtype=bool)
    for i, z in enumerate(ZS):
        for j, t in enumerate(TH):
            RSd[i, j], VS[i, j] = ray((ax, ay, z), (math.cos(t), math.sin(t), 0.0))
    RCd = np.zeros((len(PHI), NT_HI)); VC = np.zeros_like(RCd, dtype=bool)
    for i, ph in enumerate(PHI):
        for j, t in enumerate(TH):
            RCd[i, j], VC[i, j] = ray((ax, ay, ZC), (math.cos(ph) * math.cos(t), math.cos(ph) * math.sin(t), math.sin(ph)))
    ref = np.nanmedian(np.where(VS[(ZS > 0.1) & (ZS < 0.45)], RSd[(ZS > 0.1) & (ZS < 0.45)], np.nan), axis=0)
    VS &= (RSd <= 1.35 * np.nan_to_num(ref, nan=0.3)[None, :]) & (RSd > 0.02)
    VC &= (RCd < 0.5) & (RCd > 0.01)
    RS, fS = fill_rings(RSd, VS)
    RCf, fC = fill_rings(RCd, VC)
    RCf[0] = RS[-1]                                   # phi = 0 ring IS the side ring at ZC (same rays)
    COL[s] = {"RS": RS, "RC": RCf, "axis": (ax, ay)}
    report.setdefault("column_profile", {})[s] = {
        "axis_xy": [round(ax, 4), round(ay, 4)], "rings_side": len(ZS), "rings_cap": len(PHI), "azimuths": NT_HI,
        "fused_or_missed_filled_pct_side": round(100 * float(fS.mean()), 1),
        "fused_filled_pct_above_z0.47": round(100 * float(fS[ZS > 0.47].mean()), 1),
        "fused_filled_pct_below_z0.45": round(100 * float(fS[ZS < 0.45].mean()), 1),
        "cap_filled_pct": round(100 * float(fC.mean()), 1),
        "pad_half_extent_m": {"x": round(float(max(RS[0] * np.abs(np.cos(TH)))), 4), "y": round(float(max(RS[0] * np.abs(np.sin(TH)))), 4)},
        "radius_median_m": round(float(np.median(RS)), 4)}


def r_side(s, z, lowpass=False):
    RS = COL[s]["RS_lp"] if lowpass else COL[s]["RS"]
    z = min(max(z, ZS[0]), ZS[-1])
    f = (z - ZS[0]) / DZ_HI
    i = min(int(f), len(ZS) - 2); w = f - i
    return RS[i] * (1 - w) + RS[i + 1] * w


def r_cap(s, k, lowpass=False):
    return (COL[s]["RC_lp"] if lowpass else COL[s]["RC"])[k]


def lp_theta(R, k):
    pad = np.concatenate([R[:, -k:], R, R[:, :k]], axis=1)
    ker = np.ones(2 * k + 1) / (2 * k + 1)
    return np.array([np.convolve(r, ker, mode="same")[k:-k] for r in pad])


for s in "LR":
    RS_lp = lp_theta(COL[s]["RS"], 3)
    ker = np.ones(7) / 7
    RS_lp = np.array([np.convolve(np.pad(RS_lp[:, j], 3, mode="edge"), ker, mode="valid") for j in range(NT_HI)]).T
    COL[s]["RS_lp"] = RS_lp
    COL[s]["RC_lp"] = lp_theta(COL[s]["RC"], 3)


def at_theta(r_hi, nt):
    """High ring (NT_HI azimuths) -> nt azimuths (bin mean, bins centred on the output azimuths)."""
    if nt == NT_HI:
        return r_hi
    step = NT_HI / nt
    out = np.empty(nt)
    for j in range(nt):
        c = j * step
        idx = (np.arange(int(round(c - step / 2)), int(round(c + step / 2))) % NT_HI)
        out[j] = r_hi[idx].mean()
    return out


# ====================================================================== 4. trunk: remove legs, close the holes
bm = bmesh.new(); bm.from_mesh(me0)
oi = bm.verts.layers.int.new("oi")
for v in bm.verts:
    v[oi] = v.index
bm.verts.ensure_lookup_table()
legcut = leg_any > LEG_CUT
# knee lobes: the sculpt's leg anatomy continues past the weighted column - an inner knee lobe (z 1.0-1.3,
# x 0.2-0.45) protrudes to y -0.90 and is weighted to the TRUNK (ray slices of the seated low, lane report).
# Left on, it reads as a pair of horns on the standing chest (first v2 render). Rule: trunk faces entirely in
# front of the trunk's front surface (y < LOBE_Y; the belly/chin front measures -0.42..-0.52 at |x| < 0.45)
# below the chin (z < LOBE_Z) belong to the legs.
LOBE_Y, LOBE_Z = -0.58, 1.45
lobe = (W0[:, 1] < LOBE_Y) & (W0[:, 2] < LOBE_Z) & (np.abs(W0[:, 0]) < 0.95)
legcut_all = legcut | lobe
kill = [f for f in bm.faces if any(legcut[v.index] for v in f.verts) or all(lobe[v.index] for v in f.verts)]
report["trunk_edit"] = {"leg_cut_weight": LEG_CUT,
                        "faces_removed_leg": int(sum(1 for f in kill if all(legdom[v.index] for v in f.verts))),
                        "faces_removed_junction_band": int(sum(1 for f in kill if any(legcut[v.index] for v in f.verts)
                                                               and not all(legdom[v.index] for v in f.verts))),
                        "faces_removed_knee_lobe": int(sum(1 for f in kill if not any(legcut[v.index] for v in f.verts))),
                        "knee_lobe_rule": "trunk faces with every vertex at y < %.2f, z < %.2f, |x| < 0.95 (seated coords)" % (LOBE_Y, LOBE_Z)}
bmesh.ops.delete(bm, geom=kill, context="FACES_ONLY")
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
# drop slivers the cut isolated (face islands < 2% of the trunk) and any non-manifold fans they leave
isl = []
seen_f = set()
for f0 in bm.faces:
    if f0.index in seen_f:
        continue
    comp, st = [], [f0]; seen_f.add(f0.index)
    while st:
        f = st.pop(); comp.append(f)
        for e in f.edges:
            for g in e.link_faces:
                if g.index not in seen_f:
                    seen_f.add(g.index); st.append(g)
    isl.append(comp)
small = [f for comp in isl if len(comp) < 0.02 * len(bm.faces) for f in comp]
report["trunk_edit"]["islands_after_cut"] = [len(c) for c in isl]
report["trunk_edit"]["sliver_faces_dropped"] = len(small)
if small:
    bmesh.ops.delete(bm, geom=small, context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
def unpinch():
    for _ in range(4):                                  # faces hanging by one edge into the hole (boundary pinches)
        bm.verts.ensure_lookup_table()
        pinch = [v for v in bm.verts if sum(1 for e in v.link_edges if len(e.link_faces) == 1) > 2]
        if not pinch:
            break
        drop = {f for v in pinch for f in v.link_faces if sum(1 for e in f.edges if len(e.link_faces) == 1) >= 1}
        report["trunk_edit"]["pinch_faces_dropped"] = report["trunk_edit"].get("pinch_faces_dropped", 0) + len(drop)
        bmesh.ops.delete(bm, geom=list(drop), context="FACES_ONLY")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")


def bm_hole_loops():
    nb_ = defaultdict(list)
    for e in bm.edges:
        if len(e.link_faces) == 1:
            a_, b_ = e.verts
            nb_[a_].append(b_); nb_[b_].append(a_)
    out, seen_ = [], set()
    for st in list(nb_):
        if st in seen_:
            continue
        lp, prev, cur = [st], None, st; seen_.add(st)
        while True:
            nx = [x for x in nb_[cur] if x is not prev and x not in seen_]
            if not nx:
                break
            prev, cur = cur, nx[0]; lp.append(cur); seen_.add(cur)
        out.append(lp)
    return out


def projects_simply(P3):
    """Does the loop project to a SIMPLE polygon on the best-fit plane or a blend toward the front (-Y) view?"""
    c = P3.mean(0)
    n_fit = np.linalg.svd(P3 - c)[2][2]
    if n_fit[1] > 0:
        n_fit = -n_fit
    m = len(P3)
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):
        nrm = (1 - w) * n_fit + w * np.array([0.0, -1.0, 0.0]); nrm /= np.linalg.norm(nrm)
        a = np.array([0.0, 0.0, 1.0]) if abs(nrm[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        e1 = a - nrm * (a @ nrm); e1 /= np.linalg.norm(e1); e2 = np.cross(nrm, e1)
        Q = np.stack([(P3 - c) @ e1, (P3 - c) @ e2], 1)
        ok = True
        for i in range(m):
            p1, p2 = Q[i], Q[(i + 1) % m]
            for j in range(i + 2, m):
                if (j + 1) % m == i:
                    continue
                q1, q2 = Q[j], Q[(j + 1) % m]
                d1 = np.cross(p2 - p1, q1 - p1); d2 = np.cross(p2 - p1, q2 - p1)
                d3 = np.cross(q2 - q1, p1 - q1); d4 = np.cross(q2 - q1, p2 - q1)
                if d1 * d2 < 0 and d3 * d4 < 0:
                    ok = False; break
            if not ok:
                break
        if ok:
            return True
    return False


unpinch()
# a folded hole boundary (no simple projection) fills as a fin; grow that hole ring by ring until it projects simply
grown = []
for _ in range(6):
    bad = [lp for lp in bm_hole_loops() if not projects_simply(np.array([v.co[:] for v in lp]))]
    if not bad:
        break
    for lp in bad:
        grown.append([round(float(np.mean([v.co.x for v in lp])), 3), len(lp)])
    drop = {f for lp in bad for v in lp for f in v.link_faces}
    bmesh.ops.delete(bm, geom=list(drop), context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    unpinch()
report["trunk_edit"]["hole_rings_grown_to_simple_projection"] = grown
bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
for i, v in enumerate(bm.verts):
    v.index = i
col_l = bm.loops.layers.float_color.get("Col"); glow_l = bm.loops.layers.float_color.get("Glow")
TV = np.array([v.co[:] for v in bm.verts])
TOI = np.array([v[oi] for v in bm.verts])
TF = np.array([[v.index for v in f.verts] for f in bm.faces])
TCOL = np.array([[l[col_l][:] for l in f.loops] for f in bm.faces])
TGLOW = np.array([[l[glow_l][:] for l in f.loops] for f in bm.faces])
bedges = [e for e in bm.edges if len(e.link_faces) == 1]
adj = defaultdict(list)
for e in bedges:
    a_, b_ = e.verts[0].index, e.verts[1].index
    adj[a_].append(b_); adj[b_].append(a_)
report["trunk_edit"]["hole_boundary_max_degree"] = int(max(len(v) for v in adj.values()))
loops_, seen = [], set()
for st in sorted(adj):
    if st in seen:
        continue
    lp = [st]; seen.add(st); prev = None; cur = st
    while True:
        nx = [x for x in adj[cur] if x != prev and x not in seen]
        if not nx:
            break
        prev, cur = cur, nx[0]; lp.append(cur); seen.add(cur)
    loops_.append(lp)
# direction the trunk faces traverse each boundary edge (the membrane must run the other way)
edge_dir = {}
for e in bedges:
    f = e.link_faces[0]
    vs = [v.index for v in f.verts]
    a_, b_ = e.verts[0].index, e.verts[1].index
    i_ = vs.index(a_)
    edge_dir[(a_, b_)] = vs[(i_ + 1) % len(vs)] == b_
    edge_dir[(b_, a_)] = not edge_dir[(a_, b_)]
bm.free()
report["trunk_edit"]["holes"] = [{"verts": len(lp), "centroid": TV[lp].mean(0).round(3).tolist()} for lp in loops_]
assert len(loops_) == 2, "expected one front hole per leg, got %d" % len(loops_)


def cdt_patch(P3, spacing, plane=None, Q2=None):
    """Constrained-Delaunay membrane over a closed 3D loop P3 (ordered). Returns (V3, tris, nb, new_pts,
    plane, V2) with the first nb vertices = the loop. Interior lifted harmonically (uniform Laplacian,
    boundary fixed); V2 = the plane coordinates (same order). Q2: explicit 2D loop coordinates (a disk
    parameterisation) instead of a planar projection."""
    if plane is None:
        c = P3.mean(0)
        U, Sv, Vt = np.linalg.svd(P3 - c)
        e1, e2 = Vt[0], Vt[1]
    else:
        c, e1, e2 = plane
    Q = np.stack([(P3 - c) @ e1, (P3 - c) @ e2], 1) if Q2 is None else np.asarray(Q2, float)
    nb = len(Q)
    path = [(Q[i], Q[(i + 1) % nb]) for i in range(nb)]

    def inside(pt):
        x, y = pt; ins = False
        for (a, b) in path:
            if (a[1] > y) != (b[1] > y):
                xi = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if xi > x:
                    ins = not ins
        return ins

    def segd(pt):
        best = 1e9
        for (a, b) in path:
            ab = b - a; t = np.clip(((pt - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
            best = min(best, np.linalg.norm(pt - (a + t * ab)))
        return best
    lo2, hi2 = Q.min(0), Q.max(0)
    xs = np.arange(lo2[0] + spacing / 2, hi2[0], spacing); ys = np.arange(lo2[1] + spacing / 2, hi2[1], spacing)
    inner = []
    for j, y in enumerate(ys):
        for x in xs:
            pt = np.array([x + (spacing / 2 if j % 2 else 0.0), y])
            if inside(pt) and segd(pt) > 0.55 * spacing:
                inner.append(pt)
    inner = np.array(inner) if inner else np.zeros((0, 2))
    allq = [Vector(q) for q in Q] + [Vector(q) for q in inner]
    out = mgeo.delaunay_2d_cdt(allq, [], [list(range(nb))], 1, 1e-9, True)
    ov, oe, of_, orig_v = out[0], out[1], out[2], out[3]
    new_pts = len(ov) - len(allq)
    tris = []
    for f in of_:
        for k in range(1, len(f) - 1):
            tris.append([f[0], f[k], f[k + 1]])
    tris = np.array(tris)
    # map output verts -> input order when possible
    mp = np.array([ov_[0] if ov_ else -1 for ov_ in orig_v])
    V2 = np.array([v[:] for v in ov])
    n_all = len(V2)
    X = np.zeros((n_all, 3))
    fixed = np.zeros(n_all, bool)
    for i in range(n_all):
        if 0 <= mp[i] < nb:
            X[i] = P3[mp[i]]; fixed[i] = True
        else:
            X[i] = c + V2[i, 0] * e1 + V2[i, 1] * e2
    # uniform Laplacian lift
    nbrs = defaultdict(set)
    for t in tris:
        for k in range(3):
            nbrs[t[k]].add(t[(k + 1) % 3]); nbrs[t[k]].add(t[(k + 2) % 3])
    free = np.nonzero(~fixed)[0]
    fidx = -np.ones(n_all, dtype=np.int64); fidx[free] = np.arange(len(free))
    if len(free):
        A = np.zeros((len(free), len(free))); B = np.zeros((len(free), 3))
        for i in free:
            r = fidx[i]; A[r, r] = len(nbrs[i])
            for j in nbrs[i]:
                if fixed[j]:
                    B[r] += X[j]
                else:
                    A[r, fidx[j]] -= 1.0
        X[free] = np.linalg.solve(A, B)
    # reorder: loop verts first (in loop order), then interior
    order = np.concatenate([np.array([int(np.nonzero(mp == i)[0][0]) for i in range(nb)]), free])
    inv = np.empty(n_all, dtype=np.int64); inv[order] = np.arange(n_all)
    return X[order], inv[tris], nb, new_pts, (c, e1, e2), V2[order]


ntv_ = len(TV)


def lift_bilap(Vp, Tp, nb, lp):
    """Least-squares bi-Laplacian lift: the interior minimises sum ||L x||^2 over interior + loop rows with the
    loop verts' trunk 1-ring fixed -> the membrane continues the trunk's surface tangentially."""
    ni = len(Vp) - nb
    gid = np.concatenate([np.array(lp), ntv_ + np.arange(ni)])
    nbrs = defaultdict(set)
    for t in Tp:
        g = gid[t]
        for k in range(3):
            nbrs[int(g[k])].add(int(g[(k + 1) % 3])); nbrs[int(g[k])].add(int(g[(k + 2) % 3]))
    for f in TF[np.isin(TF, lp).any(1)]:
        for k in range(3):
            nbrs[int(f[k])].add(int(f[(k + 1) % 3])); nbrs[int(f[k])].add(int(f[(k + 2) % 3]))
    Xall = {int(g): (TV[g] if g < ntv_ else Vp[nb + g - ntv_]) for g in nbrs}
    rows = [int(g) for g in gid]
    A = np.zeros((len(rows), ni)); B = np.zeros((len(rows), 3))
    for r, g in enumerate(rows):
        terms = [(g, float(len(nbrs[g])))] + [(j, -1.0) for j in nbrs[g]]
        for j, w in terms:
            if j >= ntv_:
                A[r, j - ntv_] += w
            else:
                B[r] -= w * TV[j]
    X, *_ = np.linalg.lstsq(A, B, rcond=None)
    out = Vp.copy(); out[nb:] = X
    return out


def bary_map(Q2, V2, T, X3):
    """2D points Q2 -> 3D on the piecewise-linear membrane (V2 plane coords, T tris, X3 lifted coords)."""
    a, b, c = V2[T[:, 0]], V2[T[:, 1]], V2[T[:, 2]]
    den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    fn = np.cross(X3[T[:, 1]] - X3[T[:, 0]], X3[T[:, 2]] - X3[T[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1), 1e-12)[:, None]
    P = np.zeros((len(Q2), 3)); N = np.zeros((len(Q2), 3))
    for i, q in enumerate(Q2):
        l1 = ((b[:, 1] - c[:, 1]) * (q[0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (q[1] - c[:, 1])) / den
        l2 = ((c[:, 1] - a[:, 1]) * (q[0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (q[1] - c[:, 1])) / den
        l3 = 1 - l1 - l2
        k = int(np.argmax(np.minimum(np.minimum(l1, l2), l3)))
        P[i] = l1[k] * X3[T[k, 0]] + l2[k] * X3[T[k, 1]] + l3[k] * X3[T[k, 2]]
        N[i] = fn[k]
    return P, N


def bark_noise(P):
    """Deterministic bark-like relief (vertical furrows), metres."""
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    return (0.6 * np.sin(38.0 * x + 2.5 * np.sin(7.0 * z) + 11.0 * y) + 0.3 * np.sin(71.0 * x - 23.0 * y + 5.0 * z)
            + 0.25 * np.sin(17.0 * z + 29.0 * x)) * 0.004


def plane_of(P3, nrm):
    nrm = nrm / np.linalg.norm(nrm)
    a = np.array([0.0, 0.0, 1.0]) if abs(nrm[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = a - nrm * (a @ nrm); e1 /= np.linalg.norm(e1)
    return (P3.mean(0), e1, np.cross(nrm, e1))


def best_patch(P3, spacing):
    """The loop must project WITHOUT self-intersection (a CDT intersection point splits a boundary edge and the
    membrane no longer shares the trunk's edges). Candidates: the best-fit plane, then blends toward the front
    (-Y) projection; the first with zero intersection points wins."""
    c = P3.mean(0)
    n_fit = np.linalg.svd(P3 - c)[2][2]
    if n_fit[1] > 0:
        n_fit = -n_fit
    tried = []
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):
        nrm = (1 - w) * n_fit + w * np.array([0.0, -1.0, 0.0])
        pl = plane_of(P3, nrm)
        res = cdt_patch(P3, spacing, pl)
        tried.append((w, res[3]))
        if res[3] == 0:
            return res, tried, None
    # no planar projection is simple: arc-length disk parameterisation (a circle of the loop's perimeter - always
    # a simple polygon; the bi-Laplacian lift places the interior from connectivity alone)
    seg = np.linalg.norm(np.roll(P3, -1, 0) - P3, axis=1)
    per = float(seg.sum())
    th = TAU * np.concatenate([[0.0], np.cumsum(seg)[:-1]]) / per
    Qc = (per / TAU) * np.stack([np.cos(th), np.sin(th)], 1)
    pl = plane_of(P3, n_fit)
    Qp = np.stack([(P3 - pl[0]) @ pl[1], (P3 - pl[0]) @ pl[2]], 1)
    a_proj = 0.5 * abs(float(np.sum(Qp[:, 0] * np.roll(Qp[:, 1], -1) - np.roll(Qp[:, 0], -1) * Qp[:, 1])))
    a_disk = per * per / (2 * TAU)
    dscale = math.sqrt(a_disk / max(a_proj, 1e-9))
    res = cdt_patch(P3, spacing * dscale, pl, Q2=Qc)   # same interior density as a planar fill
    tried.append(("disk", res[3]))
    return res, tried, (Qc, dscale)


patches = []
for lp in loops_:
    P3 = TV[lp]
    (Vp, Tp, nb, newp, plane, V2p), tried, Qdisk = best_patch(P3, 0.035)
    report["trunk_edit"].setdefault("membrane_projection_tries", []).append([[w if isinstance(w, str) else round(w, 2), k] for w, k in tried])
    Vp = lift_bilap(Vp, Tp, nb, lp)
    # winding: the loop edge (lp[0] -> lp[1]) must run opposite to the trunk face
    a_, b_ = 0, 1
    t_has = None
    for t in Tp:
        for k in range(3):
            if t[k] == a_ and t[(k + 1) % 3] == b_:
                t_has = True
            if t[k] == b_ and t[(k + 1) % 3] == a_:
                t_has = False
    trunk_ab = edge_dir[(lp[0], lp[1])]
    if t_has is not None and t_has == trunk_ab:
        Tp = Tp[:, ::-1]
    Pd = np.concatenate([np.linspace(P3[i], P3[(i + 1) % len(P3)], 4, endpoint=False) for i in range(len(P3))])
    Qd, dsc = None, 1.0
    if Qdisk is not None:
        Qc_, dsc = Qdisk
        Qd = np.concatenate([np.linspace(Qc_[i], Qc_[(i + 1) % len(Qc_)], 4, endpoint=False) for i in range(len(Qc_))])
    _, Th, nbh, newh, _, V2h = cdt_patch(Pd, 0.012 * dsc, plane, Q2=Qd)
    Vh, Nh = bary_map(V2h, V2p, Tp, Vp)                   # the high membrane lies ON the low membrane ...
    dB = np.array([np.min(np.linalg.norm(V2p[:nb] - q, axis=1)) for q in V2h])
    Vh = Vh + Nh * (bark_noise(Vh) * smoothstep(0.0, 0.05, dB))[:, None]     # ... plus bark relief for the bake
    patches.append({"loop": lp, "V": Vp, "T": Tp, "nb": nb, "Vh": Vh, "Th": Th, "cdt_new_points": newp + newh,
                    "side": "L" if P3[:, 0].mean() > 0 else "R"})
report["trunk_edit"]["membranes"] = [{"side": p["side"], "low_verts_added": int(len(p["V"]) - p["nb"]), "low_tris": int(len(p["T"])),
                                      "high_tris": int(len(p["Th"])), "cdt_intersection_points": int(p["cdt_new_points"])}
                                     for p in patches]
report["trunk_edit"]["membrane_rule"] = ("constrained Delaunay in the loop's best-fit plane (3.5 cm spacing; blends toward the "
                                         "front projection, then an arc-length disk parameterisation, until the loop is simple), "
                                         "least-squares bi-Laplacian lift with the trunk 1-ring fixed (tangent-continuous); high = "
                                         "the same surface at 1.2 cm + 4 mm deterministic bark relief")

# centre of mass of the closed trunk (seated coords): divergence theorem over trunk + membranes
CV = [TV]; CF = [TF]; off_ = len(TV)
for p in patches:
    ids = np.concatenate([np.array(p["loop"]), off_ + np.arange(len(p["V"]) - p["nb"])])
    CV.append(p["V"][p["nb"]:]); CF.append(ids[p["T"]]); off_ += len(p["V"]) - p["nb"]
CVa = np.vstack(CV); CFa = np.vstack(CF)
a_, b_, c_ = CVa[CFa[:, 0]], CVa[CFa[:, 1]], CVa[CFa[:, 2]]
vol_t = np.einsum("ij,ij->i", a_, np.cross(b_, c_)) / 6.0
VOL = float(vol_t.sum())
COM = ((vol_t[:, None] * (a_ + b_ + c_) / 4.0).sum(0) / VOL)
report["trunk_mass"] = {"closed_volume_m3": round(VOL, 4), "com_trunk_coords": COM.round(4).tolist(),
                        "rule": "uniform density; divergence theorem over trunk + arms + membranes"}

# ====================================================================== 5. solve the legs
Y_ANK_SEAT = float(np.mean([AXIS[s][1] for s in "LR"]))
X_ANK = float(np.mean([abs(AXIS[s][0]) for s in "LR"]))
Y_HIP = float(COM[1] + FOOT_FWD)                       # pads (sole centre = ankle - FOOT_FWD) centred under the COM
Z_B = (RATIO - 1.0) * H_SEAT                           # trunk base rise
DXS = X_ANK - X_HIP
S_TOT = math.sqrt((Z_B + Z_HIP - Z_ANK) ** 2 + DXS ** 2)
cc = Z_ANK - Z_HIP
DYS = Y_ANK_SEAT - Y_HIP
L2 = (S_TOT ** 2 - DXS ** 2 - DYS ** 2 - cc ** 2) / (2 * (S_TOT + cc))
L1 = S_TOT - L2
J_OFF = 0.5 * (ZTOP - ZC)                              # knee joint sits mid-cap
H_B = L2 - J_OFF                                       # shin band end = cap centre
DY_SIT = Y_HIP - Y_ANK_SEAT                            # seated trunk displacement (rest frame): back by this, down by Z_B
report["leg_solve"] = {"hip_trunk_coords": [X_HIP, round(Y_HIP, 4), Z_HIP], "ankle_x": round(X_ANK, 4), "ankle_z": Z_ANK,
                       "seated_ankle_y_trunk_coords": round(Y_ANK_SEAT, 4), "trunk_rise_m": round(Z_B, 4),
                       "thigh_L1_m": round(L1, 4), "shin_L2_m": round(L2, 4), "leg_total_m": round(S_TOT, 4),
                       "seated_knee_joint_z_m": round(Z_ANK + L2, 4), "sculpt_knee_cap_top_z_m": round(ZTOP, 4),
                       "seated_knee_cap_top_z_m": round(Z_ANK + L2 + (ZTOP - ZC) - J_OFF, 4),
                       "seated_trunk_offset_from_stand_m": [0.0, round(DY_SIT, 4), round(-Z_B, 4)],
                       "rule": "standing: straight legs hip->ankle, height = RATIO x seated; seated: trunk on its sculpted base, "
                               "feet planted, shin vertical over the sculpted ankle -> L2 = (S^2-dx^2-dy^2-c^2)/(2(S+c)), L1 = S-L2"}
report["length_added_m"] = {"leg_total_new": round(S_TOT + 0.0, 4), "sculpted_arc": arc_mean,
                            "added": round(S_TOT - arc_mean, 4), "note": "hip->ankle; plus the 0.30 m foot shell below the ankle"}

# ====================================================================== 6. leg shells (rest frame, pre-recentre)
T_TRUNK = np.array([0.0, 0.0, Z_B])


def frame(u):
    ex = np.array([1.0, 0.0, 0.0]); ex = ex - u * (ex @ u); ex /= np.linalg.norm(ex)
    ey = np.cross(u, ex)
    return ex, ey


def loft(rings, pole_a=None, pole_b=None):
    """rings: list of (M,3) arrays bottom -> top, azimuth increasing CCW about the axis."""
    M = len(rings[0]); R = len(rings)
    V = np.vstack(rings)
    F = []
    for i in range(R - 1):
        for j in range(M):
            a = i * M + j; b = i * M + (j + 1) % M; c = (i + 1) * M + (j + 1) % M; d = (i + 1) * M + j
            F += [[a, b, c], [a, c, d]]
    extra = []
    if pole_a is not None:
        pa = len(V) + len(extra); extra.append(pole_a)
        F += [[pa, (j + 1) % M, j] for j in range(M)]
    if pole_b is not None:
        pb = len(V) + len(extra); extra.append(pole_b)
        top = (R - 1) * M
        F += [[pb, top + j, top + (j + 1) % M] for j in range(M)]
    if extra:
        V = np.vstack([V, np.array(extra)])
    return V, np.array(F)


def aniso(nt):
    """Leg thickening: the sculpted column profile scaled (LEG_SX, LEG_SY) about its axis (azimuth kept)."""
    th = np.arange(nt) * TAU / nt
    return np.sqrt((LEG_SX * np.cos(th)) ** 2 + (LEG_SY * np.sin(th)) ** 2)


def ring_pts(C, ex, ey, r, nt):
    th = np.arange(nt) * TAU / nt
    r = r * aniso(nt)
    return C[None, :] + r[:, None] * (np.cos(th)[:, None] * ex[None, :] + np.sin(th)[:, None] * ey[None, :])


def band_z(t, n):
    """t in [0,1] along a shell band -> source z, mirror-tiled n times over [0.25, ZC]."""
    zb0, zb1 = 0.25, ZC
    tt = min(max(t, 0.0), 1.0) * n
    k = min(int(tt), n - 1); f = tt - k
    return zb0 + f * (zb1 - zb0) if k % 2 == 0 else zb1 - f * (zb1 - zb0)


def loft_k(rings, kinds, pole_a=None, pole_b=None):
    """loft() + a kind per face: a strip takes its UPPER ring's kind, a pole fan its ring's kind."""
    V, F = loft(rings, pole_a, pole_b)
    M = len(rings[0])
    fk = []
    for i in range(len(rings) - 1):
        fk += [kinds[i + 1]] * (2 * M)
    if pole_a is not None:
        fk += [kinds[0]] * M
    if pole_b is not None:
        fk += [kinds[-1]] * M
    return V, F, np.array(fk)


def teeth(K, seed, tip_rng, val_rng):
    """Irregular crown rim: K sharp teeth, tips/valleys on the low mesh's NT_LO azimuth grid (so the 28-sided game
    ring carries every point), irregular spacing (each gap >= 3 samples) and irregular heights (deterministic).
    Returns h(theta) (periodic piecewise-linear through the knots) + the knot table for the report."""
    rng = np.random.default_rng(seed)
    extra = rng.multinomial(NT_LO - 3 * K, rng.dirichlet(np.full(K, 3.0)))
    gaps = 3 + extra
    off = int(rng.integers(0, NT_LO))
    tips = (off + np.concatenate([[0], np.cumsum(gaps)[:-1]])) % NT_LO
    knots = []
    for i in range(K):
        g = int(gaps[i])
        v = (tips[i] + int(np.clip(round(g * rng.uniform(0.35, 0.65)), 1, g - 1))) % NT_LO
        knots.append((int(tips[i]), float(rng.uniform(*tip_rng)), "tip"))
        knots.append((int(v), float(rng.uniform(*val_rng)), "valley"))
    knots.sort()
    ang = np.array([k[0] for k in knots]) * TAU / NT_LO
    hv = np.array([k[1] for k in knots])

    def h(th):
        return np.interp(np.mod(th, TAU), np.concatenate([ang - TAU, ang, ang + TAU]), np.tile(hv, 3))
    return h, [{"azimuth_deg": round(a * 360 / NT_LO, 1), "height_m": round(z, 3), "kind": k} for a, z, k in knots]


KNEE_H, ANK_H, CROWN_KNOTS = {}, {}, {}
for s_, sd in (("L", 11), ("R", 23)):
    KNEE_H[s_], CROWN_KNOTS["knee." + s_] = teeth(KNEE_TEETH[s_], sd, KNEE_TIP, KNEE_VAL)
    ANK_H[s_], CROWN_KNOTS["ankle." + s_] = teeth(ANK_TEETH[s_], sd + 100, ANK_TIP, ANK_VAL)


def ring_h(A, u, hq, ex, ey, r, nt):
    """ring_pts with a per-azimuth height hq (along u) - the jagged crown rims."""
    th = np.arange(nt) * TAU / nt
    r = r * aniso(nt)
    return A[None, :] + np.asarray(hq)[:, None] * u[None, :] + r[:, None] * (np.cos(th)[:, None] * ex[None, :] + np.sin(th)[:, None] * ey[None, :])


def shell_leg(s, hi_res):
    nt = NT_HI if hi_res else NT_LO
    lp = not hi_res
    sx = 1.0 if s == "L" else -1.0
    H = np.array([sx * X_HIP, Y_HIP, Z_B + Z_HIP]); A = np.array([sx * X_ANK, Y_HIP, Z_ANK])
    u = (H - A) / np.linalg.norm(H - A)
    ex, ey = frame(u)
    Kn = A + u * L2
    out = {}
    # ---- shin: dome (inside the foot) + band (stretched once over [0.25, ZC]) + sculpted cap
    rings = []
    rb = at_theta(r_side(s, 0.25, lp), nt)
    for hq in ((-0.03, 0.0) if not hi_res else np.linspace(-0.03, 0.0, 8)[:-1]):
        d = (0.08 - hq) / 0.12                          # shallow dome: stays above the sole through the shin tilt
        rings.append(ring_pts(A + u * hq, ex, ey, rb * math.sqrt(max(1 - d * d, 0.05)), nt))
    th = np.arange(nt) * TAU / nt
    an = aniso(nt)
    nt2 = 2
    tap = lambda t: THIGH_TAPER[0] + (THIGH_TAPER[1] - THIGH_TAPER[0]) * float(smoothstep(0.0, 1.0, t))
    # ---- v3 KNEE CROWN: the shin's top rim rises past the joint as a hollow jagged bark crown around the thigh's
    # knee end (traced M: tips 0.20-0.41 m above the joint). Knee pit (back, +ey) teeth scaled down to 45 % - the
    # knee folds the shin back against the thigh there. The shin flares into it over its top KC_FLARE m like a
    # trunk's crown (a sudden ledge read as a cup on first render).
    hk = KNEE_H[s](th) * (1.0 - 0.55 * smoothstep(0.3, 0.9, np.sin(th)))
    r_bt = at_theta(r_side(s, band_z(1.0, 1), lp), nt)
    r_th = np.max([at_theta(r_side(s, band_z(hq_ / L1, nt2), lp), nt) * tap(hq_ / L1)
                   for hq_ in np.linspace(0.0, KNEE_TIP[1] + 0.03, 12)], axis=0)       # thigh inside the crown
    r_in = np.maximum(r_th + CR_CL / an, r_bt * 1.03)
    r_out = r_in + CR_T / an
    KC_FLARE = 0.45
    nband = max(2, int(round((H_B - 0.08) / (DZ_HI if hi_res else 0.075))))
    for i in range(nband + 1):
        t = i / nband
        hq = 0.08 + t * (H_B - 0.08)
        rr = at_theta(r_side(s, band_z(t, 1), lp), nt)
        fl = float(smoothstep(H_B - KC_FLARE, H_B, hq)) ** 1.5
        rings.append(ring_pts(A + u * hq, ex, ey, rr * (1.0 + (r_out / r_bt - 1.0) * fl), nt))
    Cc = A + u * H_B
    kinds = ["band"] * len(rings)
    jq = H_B + J_OFF                                          # knee joint (= L2 from the ankle)
    for sf in ((0.5, 1.0) if not hi_res else np.linspace(0.0, 1.0, 22)[1:]):
        rings.append(ring_h(A, u, H_B + sf * (J_OFF + hk), ex, ey, r_out * (1.0 + 0.04 * sf), nt)); kinds.append("cout")
    r_in = r_out * 1.04 - CR_T / an
    rings.append(ring_h(A, u, jq + hk, ex, ey, r_in, nt)); kinds.append("rim")
    fl_q = jq - 0.16                                          # crown floor (below the thigh's knee dome, 1 cm)
    for sf in ((0.0,) if not hi_res else np.linspace(1.0, 0.0, 16)[1:]):
        rings.append(ring_h(A, u, fl_q + sf * (jq + hk - fl_q), ex, ey, r_in, nt)); kinds.append("cin")
    ks = range(1, len(PHI)) if hi_res else (6, 12, 18, 24)
    for k in ks:                                              # the sculpted mossy knee cap = the crown's floor dome
        ph = PHI[k]; rr = at_theta(r_cap(s, k, lp), nt)
        dirs = math.cos(ph) * aniso(nt)[:, None] * (np.cos(th)[:, None] * ex + np.sin(th)[:, None] * ey) + math.sin(ph) * u
        rings.append(Cc[None, :] + rr[:, None] * dirs); kinds.append("cap")
    top = Cc + u * float(np.mean(r_cap(s, len(PHI) - 1)))
    bot = A + u * (-0.045)
    V_, F_, fk_ = loft_k(rings, kinds, bot, top)
    out["shin"] = (V_, F_); out["shin_fk"] = fk_
    out["shin_stretch"] = (H_B - 0.08) / (ZC - 0.25)
    out["knee_crown"] = {"teeth": KNEE_TEETH[s], "rim_above_joint_m": [round(float(hk.min()), 3), round(float(hk.max()), 3)],
                         "inner_clearance_to_thigh_m_min": round(float(((r_in - r_th) * an).min()), 4),
                         "wall_m": CR_T, "outer_radius_vs_band_top": round(float((r_out / r_bt).mean()), 3),
                         "floor_below_joint_m": 0.16}
    # ---- thigh: knee dome + band (mirror-tiled x2) + hip dome
    rings = []
    r0 = at_theta(r_side(s, band_z(0.0, nt2), lp), nt) * tap(0.0)
    for hq in ((-0.12, -0.07) if not hi_res else np.linspace(-0.12, 0.0, 14)[:-1]):
        d = (0.0 - hq) / 0.15
        rings.append(ring_pts(Kn + u * hq, ex, ey, r0 * math.sqrt(max(1 - d * d, 0.05)), nt))
    nb2 = max(2, int(round(L1 / (DZ_HI if hi_res else 0.08))))
    for i in range(nb2 + 1):
        t = i / nb2
        rings.append(ring_pts(Kn + u * (t * L1), ex, ey, at_theta(r_side(s, band_z(t, nt2), lp), nt) * tap(t), nt))
    r1 = at_theta(r_side(s, band_z(1.0, nt2), lp), nt) * tap(1.0)
    for hq in ((0.08, 0.15) if not hi_res else np.linspace(0.0, 0.18, 16)[1:]):
        d = hq / 0.20
        rings.append(ring_pts(Kn + u * (L1 + hq), ex, ey, r1 * math.sqrt(max(1 - d * d, 0.05)), nt))
    out["thigh"] = loft(rings, Kn + u * (-0.15), Kn + u * (L1 + 0.20))
    out["thigh_detail_scale"] = L1 / (nt2 * (ZC - 0.25))
    # ---- foot: the sculpted pad band, widened at the sole, flat sole, root toes, collar + shoulder
    rings = []
    th = np.arange(nt) * TAU / nt
    zf = ([0.0, 0.015, 0.05, 0.10, 0.16, 0.23, 0.30] if not hi_res else list(np.arange(0.0, 0.30 + 1e-9, DZ_HI)))
    toe = np.maximum(0.0, np.cos(5 * (th + math.pi / 2))) ** 4          # lobe straight ahead (-Y) + 4 around
    # v3 ankle crown radii (needed by the foot's upper rings, which now flare into the crown from z 0.14)
    rtop = at_theta(r_side(s, 0.53, lp), nt) * FOOT_COLLAR
    an = aniso(nt)
    lean = abs(u[0] / u[2]) * 0.55                    # shin axis drift inside the crown (it leans inward)
    r_sh = np.max([at_theta(r_side(s, band_z((zq - Z_ANK - 0.08) / (H_B - 0.08), 1), lp), nt) for zq in np.linspace(0.30, 0.70, 9)], axis=0)
    r_ob = np.maximum(rtop, r_sh + (CR_CL + lean + CR_T) / an)
    for z in zf:
        # profile = the sculpted COLUMN band (z 0.25-0.53), not its tapering pad tip (half-extent 0.10 m, narrower
        # than the column: a 1.6x widening of the tip gave a sole no wider than the leg on the first v2 render)
        zsrc = 0.25 + z * (0.28 / 0.30)
        w = 1.0 - float(smoothstep(0.0, 0.26, z))
        fac = FOOT_COLLAR + (FOOT_W - FOOT_COLLAR) * w
        rr = at_theta(r_side(s, zsrc, lp), nt) * fac * (1.0 + TOE_A * toe * (1.0 - float(smoothstep(0.0, 0.14, z))))
        kf = float(smoothstep(0.14, 0.30, z)) ** 1.2                     # v3: flare into the crown (sole untouched)
        rr = rr * (1.0 - kf) + r_ob * kf
        C = np.array([A[0], A[1] - FOOT_FWD * w, z])
        rings.append(ring_pts(C, np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), rr, nt))
    # ---- v3 ANKLE CROWN (replaces v2's closed shoulder dome): the foot's top rim rises as a hollow jagged crown
    # around the shin's lower end; front/back teeth (the walk's shin-tilt plane) scaled to 70 %.
    kinds = ["foot"] * len(rings)
    ha = ANK_H[s](th) * (1.0 - 0.30 * np.abs(np.sin(th)))
    Ex, Ey = np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    A0 = np.array([A[0], A[1], 0.30])
    Up = np.array([0.0, 0.0, 1.0])
    for sf in ((0.25, 1.0) if not hi_res else np.linspace(0.0, 1.0, 18)[1:]):
        rings.append(ring_h(A0, Up, sf * ha, Ex, Ey, r_ob * (1.0 + 0.06 * sf), nt)); kinds.append("cout")
    rings.append(ring_h(A0, Up, ha, Ex, Ey, r_ob * 1.06 - CR_T / an, nt)); kinds.append("rim")
    for sf in ((0.25,) if not hi_res else np.linspace(1.0, 0.25, 12)[1:]):
        rings.append(ring_h(A0, Up, sf * ha, Ex, Ey, r_ob * (1.0 + 0.06 * sf) - CR_T / an, nt)); kinds.append("cin")
    V_, F_, fk_ = loft_k(rings, kinds, np.array([A[0], A[1] - FOOT_FWD, 0.0]), np.array([A[0], A[1], 0.30 + 0.25 * float(ha.mean()) - 0.02]))
    out["foot"] = (V_, F_); out["foot_fk"] = fk_
    out["ankle_crown"] = {"teeth": ANK_TEETH[s], "rim_z_m": [round(0.30 + float(ha.min()), 3), round(0.30 + float(ha.max()), 3)],
                          "inner_clearance_to_shin_m_min": round(float(((r_ob - CR_T / an - r_sh) * an).min()), 4),
                          "wall_m": CR_T, "outer_radius_vs_v2_collar": round(float((r_ob / rtop).mean()), 3)}
    out["joints"] = {"hip": H, "knee": Kn, "ankle": A, "toe": A + np.array([0.0, -0.30, 0.0])}
    out["foot_extent"] = {"sole_half_x": float((rings[0][:, 0].max() - rings[0][:, 0].min()) / 2),
                          "sole_half_y": float((rings[0][:, 1].max() - rings[0][:, 1].min()) / 2)}
    return out


LEGS_LO = {s: shell_leg(s, False) for s in "LR"}
LEGS_HI = {s: shell_leg(s, True) for s in "LR"}


def signed_volume(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


shell_rep = {}
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        for LEGS in (LEGS_LO, LEGS_HI):
            V, F = LEGS[s][part]
            if signed_volume(V, F) < 0:
                LEGS[s][part] = (V, F[:, ::-1].copy())
        V, F = LEGS_LO[s][part]
        shell_rep["%s.%s" % (part, s)] = {"tris_low": int(len(F)), "tris_high": int(len(LEGS_HI[s][part][1])),
                                          "volume_m3": round(signed_volume(V, F), 4)}
fx0 = report["column_profile"]["L"]["pad_half_extent_m"]
_band = (ZS > 0.10) & (ZS < 0.45)
col_half = {s: {"x": float(np.median((COL[s]["RS"][_band] * np.abs(np.cos(TH))[None, :]).max(1))),
                "y": float(np.median((COL[s]["RS"][_band] * np.abs(np.sin(TH))[None, :]).max(1)))} for s in "LR"}
sole_half = {s: LEGS_HI[s]["foot_extent"] for s in "LR"}
report["foot_widening_factors"] = {
    s: {"sculpted_column_half_extent_m_z0.10-0.45": {k: round(v, 4) for k, v in col_half[s].items()},
        "sculpted_pad_tip_half_extent_m": report["column_profile"][s]["pad_half_extent_m"],
        "new_sole_half_extent_m": {"x": round(sole_half[s]["sole_half_x"], 4), "y": round(sole_half[s]["sole_half_y"], 4)},
        "width_x_vs_sculpted_column": round(sole_half[s]["sole_half_x"] / col_half[s]["x"], 3),
        "depth_y_vs_sculpted_column": round(sole_half[s]["sole_half_y"] / col_half[s]["y"], 3),
        "width_x_vs_sculpted_pad_tip": round(sole_half[s]["sole_half_x"] / report["column_profile"][s]["pad_half_extent_m"]["x"], 3),
        "sole_area_vs_sculpted_column_section": round(sole_half[s]["sole_half_x"] * sole_half[s]["sole_half_y"] / (col_half[s]["x"] * col_half[s]["y"]), 3)}
    for s in "LR"}
report["legs"] = {"shells": shell_rep,
                  "shin_detail_stretch": round(LEGS_LO["L"]["shin_stretch"], 3),
                  "thigh_detail_scale_mirror_tiled_x2": round(LEGS_LO["L"]["thigh_detail_scale"], 3),
                  "foot_widening": {"sole_radius_factor": FOOT_W, "collar_factor": FOOT_COLLAR, "toe_lobes": 5, "toe_amplitude": TOE_A,
                                    "sculpted_pad_half_extent_m": fx0,
                                    "new_sole_half_extent_m": {k: round(v, 4) for k, v in LEGS_HI["L"]["foot_extent"].items()},
                                    "blend": "radius factor smoothstep(z, 0, 0.26 m) from %.1fx at the sole to %.1fx at the ankle collar; sole flat at z = 0" % (FOOT_W, FOOT_COLLAR),
                                    "toes_forward_m": FOOT_FWD}}

# ====================================================================== 7. assemble the standing low + high
parts_V, parts_F, fkind, fside, vlabel = [], [], [], [], []
voff = 0
TVr = TV + T_TRUNK
parts_V.append(TVr); parts_F.append(TF); fkind += ["trunk"] * len(TF); fside += [""] * len(TF); vlabel += ["trunk"] * len(TVr)
voff = len(TVr)
patch_vert_ranges = []
for p in patches:
    ids = np.concatenate([np.array(p["loop"]), voff + np.arange(len(p["V"]) - p["nb"])])
    parts_V.append(p["V"][p["nb"]:] + T_TRUNK); parts_F.append(ids[p["T"]])
    fkind += ["membrane"] * len(p["T"]); fside += [p["side"]] * len(p["T"]); vlabel += ["membrane." + p["side"]] * (len(p["V"]) - p["nb"])
    patch_vert_ranges.append((voff, voff + len(p["V"]) - p["nb"], np.array(p["loop"])))
    voff += len(p["V"]) - p["nb"]
ftag = [""] * len(fkind)
CTAG = {"cout": "collar", "rim": "collar_rim", "cin": "collar_in"}
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_LO[s][part]
        parts_V.append(V); parts_F.append(F + voff)
        fkind += [part] * len(F); fside += [s] * len(F); vlabel += ["%s.%s" % (part, s)] * len(V)
        fk_ = LEGS_LO[s].get(part + "_fk")
        ftag += [CTAG.get(k, "") for k in fk_] if fk_ is not None else [""] * len(F)
        voff += len(V)
VL = np.vstack(parts_V); FL = np.vstack(parts_F)
fkind = np.array(fkind); fside = np.array(fside); vlabel = np.array(vlabel); ftag = np.array(ftag, dtype="<U12")
lo_b, hi_b = VL.min(0), VL.max(0)
S_RC = np.array([-(lo_b[0] + hi_b[0]) / 2, -(lo_b[1] + hi_b[1]) / 2, -lo_b[2]])
VL = VL + S_RC
report["recentre_shift_m"] = S_RC.round(5).tolist()
T_REST = T_TRUNK + S_RC                                  # trunk coords -> rest frame
# rigidity proof on the trunk: edge lengths + face normals vs the seated sculpt
e_old = np.linalg.norm(TV[TF[:, 0]] - TV[TF[:, 1]], axis=1); e_new = np.linalg.norm(VL[TF[:, 0]] - VL[TF[:, 1]], axis=1)
n_old = np.cross(TV[TF[:, 1]] - TV[TF[:, 0]], TV[TF[:, 2]] - TV[TF[:, 0]])
n_new = np.cross(VL[TF[:, 1]] - VL[TF[:, 0]], VL[TF[:, 2]] - VL[TF[:, 0]])
report["rest_rebuild"] = {"trunk_faces_kept": int(len(TF)), "trunk_edge_stretch_max_abs": float(np.abs(e_new / e_old - 1).max()),
                          "trunk_flipped_faces": int(((n_old * n_new).sum(1) < 0).sum()),
                          "trunk_transform": "rigid translation %s" % T_REST.round(4).tolist()}
# leg shells: inverted faces (normal against the outward direction from the shell axis)
inv_ = 0
for s in "LR":
    J = LEGS_LO[s]["joints"]
    for part, (a0, a1) in (("thigh", ("knee", "hip")), ("shin", ("ankle", "knee")), ("foot", ("ankle", "ankle"))):
        # v3 crowns are excluded: their inner walls face the axis by design (orientation: closure + volume below)
        m = (vlabel[FL[:, 0]] == "%s.%s" % (part, s)) & (ftag == "")
        Fp = FL[m]
        cen = VL[Fp].mean(1)
        nrm = np.cross(VL[Fp[:, 1]] - VL[Fp[:, 0]], VL[Fp[:, 2]] - VL[Fp[:, 0]])
        pa, pb = J[a0] + S_RC, J[a1] + S_RC
        if part == "foot":
            # the foot's rings are centred FOOT_FWD ahead of the ankle at the sole (toes forward): the outward
            # reference is that ring-centre line, clamped inside the shell (z 0.10-0.30)
            zc = np.clip(cen[:, 2], 0.10, 0.30)
            ctr = np.stack([np.full(len(cen), pa[0]), pa[1] - FOOT_FWD * (1.0 - smoothstep(0.0, 0.26, zc)), zc], 1)
            out_ = cen - ctr
        else:
            ab = pb - pa
            tpar = np.clip(((cen - pa) @ ab) / max(ab @ ab, 1e-12), 0, 1)
            out_ = cen - (pa + tpar[:, None] * ab)
        inv_ += int(((nrm * out_).sum(1) < -1e-12).sum())
report["rest_rebuild"]["leg_shell_inverted_faces"] = inv_
report["rest_rebuild"]["tris_total"] = int(len(FL))
# closure: every edge of trunk+membranes and of each shell used exactly twice, once per direction
def edge_audit(Fs):
    cnt = defaultdict(int)
    for f in Fs:
        for k in range(3):
            cnt[(int(f[k]), int(f[(k + 1) % 3]))] += 1
    bad_dir = sum(1 for e, c in cnt.items() if c > 1)
    open_ = sum(1 for e in cnt if (e[1], e[0]) not in cnt)
    return {"directed_edge_duplicates": bad_dir, "open_edges": open_}
report["rest_rebuild"]["trunk_plus_membranes_closure"] = edge_audit(FL[np.isin(fkind, ("trunk", "membrane"))])
report["rest_rebuild"]["shell_closure"] = {f"{p}.{s}": edge_audit(FL[vlabel[FL[:, 0]] == f"{p}.{s}"]) for s in "LR" for p in ("thigh", "shin", "foot")}
report["crowns"] = {"knee." + s: LEGS_LO[s]["knee_crown"] for s in "LR"}
report["crowns"].update({"ankle." + s: LEGS_LO[s]["ankle_crown"] for s in "LR"})
report["crowns"]["knots"] = CROWN_KNOTS
report["crowns"]["faces_low"] = {k: int((ftag == k).sum()) for k in ("collar", "collar_rim", "collar_in")}
report["crowns"]["rule"] = ("lower piece's top rim rises as a hollow jagged crown around the upper piece (traced M points up: "
                            "tips 0.20-0.41 m above the knee joint); teeth on the 28-azimuth game grid, irregular gaps >= 3 "
                            "samples; knee-pit teeth x0.45, ankle front/back teeth x0.70 (fold/tilt planes); the lower piece flares into its crown (shin over its top 0.45 m, foot from z 0.14)")

# ====================================================================== 7b. v3 TREE-SKIRT (rest frame)
t7b = time.time()
ntv_r = len(TV)
Z_BASE = float(VL[:ntv_r, 2].min())
arm_old = np.array([nm.startswith(("upperarm", "forearm")) for nm in old_names])
arm_v = np.zeros(len(VL), bool)
arm_v[:ntv_r] = arm_old[np.argmax(Wold[TOI], 1)]
tb_mask = np.isin(fkind, ("trunk", "membrane")) & ~arm_v[FL].any(1)
bvh_tb = BVHTree.FromPolygons(VL.tolist(), FL[tb_mask].tolist())
_bandv = (~arm_v[:ntv_r]) & (VL[:ntv_r, 2] < Z_BASE + SK_ATT + 0.1)
YC = float((VL[:ntv_r][_bandv, 1].min() + VL[:ntv_r][_bandv, 1].max()) / 2)
Z_ATT = Z_BASE + SK_ATT                                       # traced attach height (front-view side lines)


def r_trunk(phis, z):
    """Outermost trunk-body radius from the skirt axis (0, YC) along each azimuth at height z (scalar or per
    azimuth; 0 = no trunk)."""
    out = np.zeros(len(phis))
    zz = np.broadcast_to(np.asarray(z, float), (len(phis),))
    for i, p in enumerate(phis):
        d = Vector((math.cos(p), math.sin(p), 0.0))
        hit = bvh_tb.ray_cast(Vector((0.0, YC, float(zz[i]))) + d * 3.0, -d, 3.0)
        out[i] = 3.0 - hit[3] if hit[0] is not None else 0.0
    return out


dphi = TAU / SK_N
PHS = -math.pi / 2 + np.arange(SK_N) * dphi                  # sample 0 = front centre (-Y)
# ---- hem: the traced W, symmetric in the drawing's own frame (centre -0.08 m, half-width 0.64 m on the plane y -0.90)
xn = np.abs(np.cos(PHS)); side = (np.cos(PHS) > 0).astype(int)   # 1 = +x (L)
pk = np.array(SK_PEAK)[side]; cn = np.array(SK_CORNER)[side]
x_pk = math.sin(dphi)                                         # the notch samples = the tip's neighbours
dep = np.where(xn < 0.5 * x_pk, SK_TIP, pk + (cn - pk) * np.clip((xn - x_pk) / (1 - x_pk), 0, 1))
back = np.sin(PHS) > 1e-9
dep = np.where(back, dep * (SK_BACK + (1 - SK_BACK) * xn ** 2), dep)
jit = 0.018 * np.sin(3.1 * np.arange(SK_N) + 0.7) * (xn > 1.5 * x_pk)
HEM = Z_BASE - (dep + jit)                                   # initial guess; calibrated below in the drawing's view
Z_LO = float(HEM.min())
# attach line: the traced height at front/back, raised SK_ATT_SIDE at the sides, where the thigh's hip dome breaks
# the trunk surface up to ~Z_BASE + 0.52 (a plate emerging at the traced 0.58 would need a flange to clear it)
ZAJ = Z_ATT + SK_ATT_SIDE * np.cos(PHS) ** 2
# ---- required outer radius: trunk + SK_TR, and the walk/idle-swept thighs + SK_CL + SK_T (pelvis space)
DZ_S = 0.02
ZG = np.arange(Z_BASE - 0.95, float(ZAJ.max()) + 0.02, DZ_S)
RTR = np.stack([r_trunk(PHS, z) for z in ZG], 1)               # (SK_N, nz)
sw_pts = []
for s in "LR":
    Hj = LEGS_LO[s]["joints"]["hip"] + S_RC
    P0 = VL[vlabel == "thigh." + s] - Hj
    for a in np.linspace(SK_SWEEP_PITCH[0], SK_SWEEP_PITCH[1], 12):
        for rl in (-SK_SWEEP_ROLL, 0.0, SK_SWEEP_ROLL):
            Rm_ = np.array(Euler((0.0, math.radians(rl), 0.0)).to_matrix()) @ np.array(Euler((math.radians(a), 0.0, 0.0)).to_matrix())
            sw_pts.append(P0 @ Rm_.T + Hj)
sw_pts = np.vstack(sw_pts)
# thigh samples inside the trunk (pelvis-rigid, so a static test) are hidden: the skirt only has to clear the rest
_bvh_tc = BVHTree.FromPolygons(VL.tolist(), FL[np.isin(fkind, ("trunk", "membrane"))].tolist())


def _outside(p):
    loc, nrm, _, d = _bvh_tc.find_nearest(Vector(p))
    return loc is None or (Vector(p) - loc).dot(nrm) > 0


_n_sw = len(sw_pts)
sw_pts = sw_pts[np.array([_outside(p) for p in sw_pts])]
rr_ = np.hypot(sw_pts[:, 0], sw_pts[:, 1] - YC)
jb = np.round((np.arctan2(sw_pts[:, 1] - YC, sw_pts[:, 0]) - PHS[0]) / dphi).astype(int) % SK_N
zb = np.floor((sw_pts[:, 2] - ZG[0]) / DZ_S).astype(int)
okz = (zb >= 0) & (zb < len(ZG))
RTH = np.zeros((SK_N, len(ZG)))
np.maximum.at(RTH, (jb[okz], zb[okz]), rr_[okz])
for _ in range(1):                                             # dilate one bin in azimuth and height
    RTH = np.maximum.reduce([RTH, np.roll(RTH, 1, 0), np.roll(RTH, -1, 0),
                             np.pad(RTH, ((0, 0), (1, 0)))[:, :-1], np.pad(RTH, ((0, 0), (0, 1)))[:, 1:]])
RREQ = np.maximum(np.where(RTR > 0, RTR + SK_TR, 0.0), np.where(RTH > 0, RTH + SK_CL + SK_T, 0.0))
# ---- flare profile R(z) = R_top + (R_bot - R_top) g(t), g = 1 - (1 - min(t/T_FL, 1))^2.5: flares out from the
# trunk over the top T_FL of the drop, then hangs (the traced side lines are near-vertical below the flare)
T_FL = 0.40


def g_fl(z, za):
    t = np.clip((za - np.asarray(z)) / (za - Z_LO), 0, 1)
    return 1 - (1 - np.minimum(t / T_FL, 1)) ** 2.5


def circ_smooth(a, n=2):
    for _ in range(n):
        a = 0.25 * np.roll(a, 1) + 0.5 * a + 0.25 * np.roll(a, -1)
    return a


# attach radius: the trunk's outermost over the 12 cm under the attach line (a belly bulge just below it must not
# poke through the plate), smoothed max-preserving
_rt = np.max([r_trunk(PHS, ZAJ - dz) for dz in (0.0, 0.03, 0.06, 0.09, 0.12)], axis=0) + SK_TR
R_TOP = np.maximum(circ_smooth(np.maximum.reduce([_rt, np.roll(_rt, 1), np.roll(_rt, -1)]), 2), _rt)
def solve_rbot(hem):
    """Smallest hem radius per azimuth whose flare clears every requirement between the hem and the attach line."""
    rb, bind = np.zeros(SK_N), []
    for j in range(SK_N):
        col = (ZG >= hem[j] - 1e-9) & (ZG <= ZAJ[j] - 0.02)
        gz = np.maximum(g_fl(ZG[col], ZAJ[j]), 0.08)
        need = R_TOP[j] + (RREQ[j, col] - R_TOP[j]) / gz
        rb[j] = max(R_TOP[j] + SK_FLARE_MIN[0] + (SK_FLARE_MIN[1] - SK_FLARE_MIN[0]) * math.cos(PHS[j]) ** 2, float(np.max(need)))
        kb = int(np.argmax(need))
        bind.append([round(float(ZG[col][kb]), 3), round(float(RTR[j, col][kb]), 3), round(float(RTH[j, col][kb]), 3), round(float(gz[kb]), 3)])
    return np.maximum(circ_smooth(np.maximum.reduce([rb, np.roll(rb, 1), np.roll(rb, -1)]), 2), rb), bind


# ---- hem calibration IN THE DRAWING'S VIEW: the artist drew the W on the v2 front render; the trace carries that
# render's exact camera. Knots from the stroke (outer corners = lowest ends, tip = lowest point between them,
# notches = highest points either side of the tip) are symmetrised onto our samples: corner <-> the side sample,
# tip <-> the front-centre sample, notch <-> the tip's neighbours, piecewise-linear in normalised x between; each
# front hem vertex is then placed at the height whose projection lands on the stroke's pixel row. The back half
# (never drawn) mirrors the front scaled by SK_BACK toward the centre.
TRACE = json.load(open(os.path.join(PRJ, "design", "refs", "eldroot-v3-trace.json")))
_cam = TRACE["camera"]
CAM_LOC = Vector(_cam["loc"]); CAM_RM = (Vector(_cam["centre"]) - CAM_LOC).to_track_quat("-Z", "Y").to_matrix()
TANF = 18.0 / 50.0


def proj_v2front(p):
    v = CAM_RM.transposed() @ (Vector(p) - CAM_LOC)
    return ((v.x / -v.z) / TANF + 1) / 2 * 1024, (1 - (v.y / -v.z) / TANF) / 2 * 1024


# v4: the ANNOTATION camera rides with the trunk. The artist drew on the v2 front render (trunk at T_REST_V2); v4's
# shorter legs lower the trunk, so the drawing's camera is translated by the same rigid offset - the stroke keeps its
# pixels relative to the body (the uniform cell refit at the end scales camera and body together: pixels invariant).
T_REST_V2 = np.array([0.0, -0.21, 2.7108])            # v2/v3 receipts: trunk rigid translation (rest_rebuild)
DT_ANN = T_REST - T_REST_V2


def proj_ann(p):
    return proj_v2front(np.asarray(p, float) - DT_ANN)


_st = np.array([proj_v2front((x, -0.90, z)) for x, z in TRACE["eldroot-v3-skirt-silhouette.png"]["world_xz_on_plane_y-0.90"]])
_cols = np.unique(np.round(_st[:, 0]).astype(int))
_prof = np.array([_st[np.round(_st[:, 0]).astype(int) == c_, 1].max() for c_ in _cols])      # lowest stroke px per column
_um, _hw = (_cols.min() + _cols.max()) / 2, (_cols.max() - _cols.min()) / 2
_mid = np.abs(_cols - _um) < 0.35 * _hw
_iT = int(np.nonzero(_mid)[0][np.argmax(_prof[_mid])])
_lft, _rgt = np.arange(len(_cols)) < _iT, np.arange(len(_cols)) > _iT
_iL = int(np.nonzero(_lft & (_cols < _um - 0.5 * _hw))[0][np.argmax(_prof[_lft & (_cols < _um - 0.5 * _hw)])])
_iR = int(np.nonzero(_rgt & (_cols > _um + 0.5 * _hw))[0][np.argmax(_prof[_rgt & (_cols > _um + 0.5 * _hw)])])
_iPL = _iL + int(np.argmin(_prof[_iL:_iT])); _iPR = _iT + int(np.argmin(_prof[_iT:_iR + 1]))
KN_U = np.array([_cols[i] for i in (_iL, _iPL, _iT, _iPR, _iR)], float)
KN_V = np.array([_prof[i] for i in (_iL, _iPL, _iT, _iPR, _iR)], float)


def hem_z_for_row(x, y, v_t):
    lo_, hi_ = Z_BASE - 1.2, Z_BASE + 0.4                      # image v grows downward as z falls
    for _ in range(40):
        m_ = 0.5 * (lo_ + hi_)
        if proj_ann((x, y, m_))[1] > v_t:
            lo_ = m_
        else:
            hi_ = m_
    return 0.5 * (lo_ + hi_)


front_j = np.nonzero(np.sin(PHS) < 1e-9)[0]                   # front half incl. both side samples
# the back half was never drawn: the mirrored W alone reads as a smooth bowl from the side (first v3 render), so it
# carries irregular downward bark points like the crowns (sharp V between grid-aligned knots, faded out at the sides)
_bt = np.array(SK_BACK_TEETH)
back_teeth = np.interp(np.degrees(PHS) % 360.0, np.concatenate([[0.0], _bt[:, 0], [180.0]]), np.concatenate([[0.0], _bt[:, 1], [0.0]]))
back_teeth = np.where(np.sin(PHS) > 1e-9, back_teeth * smoothstep(0.15, 0.5, np.sin(PHS)), 0.0)
calib = []
for it in range(3):
    R_BOT, BIND = solve_rbot(HEM)
    xs_ = R_BOT * np.cos(PHS); ys_ = YC + R_BOT * np.sin(PHS)
    rs_side = {1: R_BOT[SK_N // 4], 0: R_BOT[3 * SK_N // 4]}
    xi = np.array([xs_[j] / rs_side[int(np.cos(PHS[j]) > 0)] for j in range(SK_N)])
    kn_xi = np.array([-1.0, xi[-1], 0.0, xi[1], 1.0])
    newd = np.zeros(SK_N)
    for j in front_j:
        u_d = np.interp(xi[j], kn_xi, KN_U)
        v_t = float(np.interp(u_d, _cols, _prof)) if j not in (0, 1, SK_N - 1, SK_N // 4, 3 * SK_N // 4) else \
            {0: KN_V[2], 1: KN_V[3], SK_N - 1: KN_V[1], SK_N // 4: KN_V[4], 3 * SK_N // 4: KN_V[0]}[j]
        newd[j] = Z_BASE - hem_z_for_row(xs_[j], ys_[j], v_t)
    for j in range(SK_N):
        if j not in front_j:
            jm = (SK_N // 2 - j) % SK_N
            newd[j] = newd[jm] * (SK_BACK + (1 - SK_BACK) * abs(math.cos(PHS[j])) ** 2) + jit[j] + back_teeth[j]
    calib.append(round(float(np.abs(newd - (Z_BASE - HEM)).max()), 4))       # hem change this pass (m)
    HEM = Z_BASE - newd
    Z_LO = float(HEM.min())
R_BOT, BIND = solve_rbot(HEM)
_res = []
for j in front_j:
    xs_j, ys_j = R_BOT[j] * math.cos(PHS[j]), YC + R_BOT[j] * math.sin(PHS[j])
    _res.append(proj_ann((xs_j, ys_j, HEM[j]))[1])
SK_CALIB = {"stroke_knots_px_u": KN_U.round(1).tolist(), "stroke_knots_px_v": KN_V.round(1).tolist(),
            "knot_roles": ["corner -x (image left)", "notch -x", "tip", "notch +x", "corner +x"],
            "calibrated_depth_below_trunk_base_m": {"tip": round(float(Z_BASE - HEM[0]), 3),
                                                    "notches_-x_+x": [round(float(Z_BASE - HEM[-1]), 3), round(float(Z_BASE - HEM[1]), 3)],
                                                    "corners_-x_+x": [round(float(Z_BASE - HEM[3 * SK_N // 4]), 3), round(float(Z_BASE - HEM[SK_N // 4]), 3)]},
            "hem_change_per_pass_m": calib,
            "knot_row_residual_px": [round(float(_res[list(front_j).index(j)] - {0: KN_V[2], 1: KN_V[3], SK_N - 1: KN_V[1], SK_N // 4: KN_V[4], 3 * SK_N // 4: KN_V[0]}[j]), 2)
                                     for j in (3 * SK_N // 4, SK_N - 1, 0, 1, SK_N // 4)]}


def sk_R(za, z, rt, rb):
    return rt + (rb - rt) * g_fl(z, za)


def skirt_rings(phs, hem, rt, rb, za, w_out, w_in, relief=False):
    """Closed torus shell: outer rings hem -> attach, two buried rings (up and into the trunk), inner rings
    attach -> hem. Returns V, F (outward), ring kind per face-strip ('out', 'bury', 'in', 'hem')."""
    nphi = len(phs)
    cph, sph = np.cos(phs), np.sin(phs)
    rings, kinds = [], []
    for w in w_out:
        z = hem + (za - hem) * w
        R = sk_R(za, z, rt, rb)
        if relief and 0.03 < w < 0.97:
            u_ = phs * R
            R = R + 0.007 * (0.6 * np.sin(44.0 * u_ + 2.5 * np.sin(9.0 * z)) + 0.3 * np.sin(97.0 * u_ - 13.0 * z)
                             + 0.25 * np.sin(23.0 * z + 11.0 * u_)) * smoothstep(0.0, 0.08, w) * smoothstep(1.0, 0.9, w)
        rings.append(np.stack([R * cph, YC + R * sph, z], 1)); kinds.append("out")
    for dz, dr in ((0.05, 0.07), (0.09, 0.14)):
        R = r_trunk(phs, za + dz) - dr
        R = np.where(R > 0.2, R, 0.2)
        rings.append(np.stack([R * cph, YC + R * sph, za + dz], 1)); kinds.append("bury")
    for w in w_in:
        z = hem + (za - hem) * w + (0.004 if w == 0.0 else 0.0)
        R = sk_R(za, z, rt, rb) - SK_T
        rings.append(np.stack([R * cph, YC + R * sph, z], 1)); kinds.append("in")
    M, NR = nphi, len(rings)
    V = np.vstack(rings)
    F, fk = [], []
    for i in range(NR):
        n_ = (i + 1) % NR
        for j in range(M):
            a = i * M + j; b = i * M + (j + 1) % M; c = n_ * M + (j + 1) % M; d = n_ * M + j
            F += [[a, b, c], [a, c, d]]
        fk += [("hem" if n_ == 0 else kinds[n_])] * (2 * M)
    F = np.array(F)
    if signed_volume(V, F) < 0:
        F = F[:, ::-1].copy()
    return V, F, np.array(fk), NR


W_OUT = (0.0, 0.12, 0.28, 0.45, 0.62, 0.78, 0.90, 1.0)
W_IN = (0.90, 0.55, 0.20, 0.0)
# high: 6x azimuths, outline interpolated from the low samples (periodic), dense rings + bark relief on the outer face
PHH = -math.pi / 2 + np.arange(SK_NHI) * TAU / SK_NHI


def per_interp(vals):
    x = np.concatenate([PHS - TAU, PHS, PHS + TAU])
    return np.interp(PHH, x, np.tile(vals, 3))


def pint(vals, phis):
    """Periodic linear interpolation of a per-sample skirt field at arbitrary azimuths (PHS convention)."""
    x = np.concatenate([PHS - TAU, PHS, PHS + TAU])
    ph = (np.asarray(phis, float) - PHS[0]) % TAU + PHS[0]
    return np.interp(ph, x, np.tile(vals, 3))


# ---- v4 TASSETS: the skirt is re-cut into 6 front plates + the back shell. Azimuth rho = phi + pi/2 (0 = front
# centre, + toward +x / L). Seams are bark splits: seeded zigzag (knots on the low ring heights), a V opening wider at
# the hem (TS_GAP). Every piece is a closed shell with the v3 ring profile (outer hem->attach, two buried rings,
# inner attach->hem) + two split walls (the ring cross-section, tessellated in (r, z)).
_zig_rng = np.random.default_rng(4711)
ZIG_W = np.array([0.0, 0.28, 0.62, 0.90, 1.0])
SEAMS = {}
for sd_, lst_ in (("L", TS_SEAMS["L"]), ("R", TS_SEAMS["R"])):
    for d_ in lst_:
        key_ = round(d_ if sd_ == "L" else -d_, 3)
        if key_ in SEAMS:
            continue
        sg_ = 1.0 if _zig_rng.uniform() < 0.5 else -1.0
        SEAMS[key_] = np.array([0.0, sg_ * _zig_rng.uniform(0.7, 1.0), -sg_ * _zig_rng.uniform(0.5, 0.9),
                                sg_ * _zig_rng.uniform(0.3, 0.6), 0.0]) * TS_ZIG


def seam_rho(key, w, side):
    """Edge azimuth (rad, rho) of the piece lying on side (+1: toward +rho, -1: toward -rho) of seam `key` (deg)."""
    w = np.clip(np.asarray(w, float), 0.0, 1.0)
    ph0 = math.radians(key) - math.pi / 2
    rref = float(pint(R_BOT, [ph0])[0])
    gp = TS_GAP_BACK if abs(abs(key) - TS_SEAMS["L" if key > 0 else "R"][-1]) < 1e-6 else TS_GAP
    off = np.interp(w, ZIG_W, SEAMS[key]) + side * 0.5 * (gp[0] * (1 - w) + gp[1] * w)
    return math.radians(key) + off / rref


def build_piece(ka, kb, hi_res):
    """Closed skirt piece between seams ka < kb (deg rho; the back shell runs 90 -> 270 through the back)."""
    span = math.radians(kb - ka)
    M = max(3, int(round(span / (TAU / (SK_NHI if hi_res else SK_N))))) + 1
    w_out = np.linspace(0.0, 1.0, 64) if hi_res else np.array(W_OUT)
    w_in = np.linspace(1.0, 0.0, 14)[1:] if hi_res else np.array(W_IN)
    kb_key = kb if kb <= 180 else round(kb - 360, 3)   # the back shell's far seam is the R side's last seam
    hem_a = float(pint(HEM, [math.radians(ka) - math.pi / 2])[0])
    hem_b = float(pint(HEM, [math.radians(kb) - math.pi / 2])[0])
    rings, kinds, vw = [], [], []

    def ring_phis(w):
        ra = seam_rho(ka, w, +1.0)
        rb = seam_rho(kb_key, w, -1.0) + (TAU if kb > 180 else 0.0)
        return np.linspace(ra, rb, M) - math.pi / 2

    def fields(ph):
        hem = pint(HEM, ph); hem[0], hem[-1] = hem_a, hem_b        # the seams keep the traced knot depths exactly
        return hem, pint(R_TOP, ph), pint(R_BOT, ph), pint(ZAJ, ph)
    for w in w_out:
        ph = ring_phis(w); hem, rt, rb, za = fields(ph)
        z = hem + (za - hem) * w
        R = sk_R(za, z, rt, rb)
        if hi_res and 0.03 < w < 0.97:
            u_ = ph * R
            R = R + 0.007 * (0.6 * np.sin(44.0 * u_ + 2.5 * np.sin(9.0 * z)) + 0.3 * np.sin(97.0 * u_ - 13.0 * z)
                             + 0.25 * np.sin(23.0 * z + 11.0 * u_)) * smoothstep(0.0, 0.08, w) * smoothstep(1.0, 0.9, w)
        rings.append(np.stack([R * np.cos(ph), YC + R * np.sin(ph), z], 1)); kinds.append("out"); vw.append(np.full(M, w))
    ph = ring_phis(1.0); hem, rt, rb, za = fields(ph)
    for dz, dr in ((0.05, 0.07), (0.09, 0.14)):
        R = r_trunk(ph, za + dz) - dr
        R = np.where(R > 0.2, R, 0.2)
        rings.append(np.stack([R * np.cos(ph), YC + R * np.sin(ph), za + dz], 1)); kinds.append("bury"); vw.append(np.full(M, 1.05))
    for w in w_in:
        ph = ring_phis(w); hem, rt, rb, za = fields(ph)
        z = hem + (za - hem) * w + (0.004 if w == 0.0 else 0.0)
        R = sk_R(za, z, rt, rb) - SK_T
        rings.append(np.stack([R * np.cos(ph), YC + R * np.sin(ph), z], 1)); kinds.append("in"); vw.append(np.full(M, w))
    NR = len(rings)
    V = np.vstack(rings)
    F, fk = [], []
    for i in range(NR):
        n_ = (i + 1) % NR
        for j in range(M - 1):
            a = i * M + j; b = i * M + j + 1; c = n_ * M + j + 1; d = n_ * M + j
            F += [[a, b, c], [a, c, d]]
            fk += [("hem" if n_ == 0 else kinds[n_])] * 2
    # split walls: the ring cross-section at each end, tessellated in (r, z); the strip runs ring i+1 -> i along the
    # j = 0 edge, so that wall takes the loop direction i -> i+1 (and the j = M-1 wall the reverse)
    for j, sgn in ((0, 1.0), (M - 1, -1.0)):
        ids = [i * M + j for i in range(NR)]
        Q = [(math.hypot(V[k, 0], V[k, 1] - YC), V[k, 2]) for k in ids]
        area = 0.5 * sum(Q[i][0] * Q[(i + 1) % NR][1] - Q[(i + 1) % NR][0] * Q[i][1] for i in range(NR))
        tris = mgeo.tessellate_polygon([[Vector((q[0], q[1], 0.0)) for q in Q]])
        for t in tris:
            p0, p1, p2 = (Q[t[0]], Q[t[1]], Q[t[2]])
            ta = 0.5 * ((p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1]))
            tri = [ids[t[0]], ids[t[1]], ids[t[2]]]
            if (ta > 0) != ((area > 0) == (sgn > 0)):
                tri = tri[::-1]
            F.append(tri); fk.append("split")
    F = np.array(F)
    if signed_volume(V, F) < 0:
        F = F[:, ::-1].copy()
    return V, F, np.array(fk), np.concatenate(vw), NR, M


PIECES = []                                            # (name, side, rho_a, rho_b)
for sd_ in "LR":
    sg_ = TS_SEAMS[sd_]
    for k_ in range(len(sg_) - 1):
        lo_k, hi_k = (sg_[k_], sg_[k_ + 1]) if sd_ == "L" else (-sg_[k_ + 1], -sg_[k_])
        PIECES.append(("tasset.%s.%d" % (sd_, k_), sd_, lo_k, hi_k))
PIECES.append(("skirt", "", TS_SEAMS["L"][-1], 360.0 - TS_SEAMS["R"][-1]))
sk_off = len(VL)
PIECE_LO, PIECE_HI, PIECE_IDX = {}, {}, {}
for nm_, sd_, ka_, kb_ in PIECES:
    V_, F_, fk_, vw_, NR_, M_ = build_piece(ka_, kb_, False)
    Vh_, Fh_, _, _, _, Mh_ = build_piece(ka_, kb_, True)
    PIECE_LO[nm_] = {"V": V_, "F": F_, "fk": fk_, "vw": vw_, "NR": NR_, "M": M_, "side": sd_, "rho": (ka_, kb_)}
    PIECE_HI[nm_] = (Vh_, Fh_)
    o_ = len(VL)
    PIECE_IDX[nm_] = np.arange(o_, o_ + len(V_))
    VL = np.vstack([VL, V_]); FL = np.vstack([FL, F_ + o_])
    kind_ = "skirt" if nm_ == "skirt" else "tasset"
    fkind = np.concatenate([fkind, np.full(len(F_), kind_)]); fside = np.concatenate([fside, np.full(len(F_), sd_)])
    vlabel = np.concatenate([vlabel, np.full(len(V_), nm_)])
    arm_v = np.concatenate([arm_v, np.zeros(len(V_), bool)])
    tg_ = np.where(fk_ == "split", "split", np.where(fk_ == "in", "skirt_in" if nm_ == "skirt" else "tasset_in", "skirt"))
    ftag = np.concatenate([ftag, tg_])
VW_SK = np.full(len(VL), -1.0)
for nm_ in PIECE_IDX:
    VW_SK[PIECE_IDX[nm_]] = PIECE_LO[nm_]["vw"]
TASSETS = [nm_ for nm_, _, _, _ in PIECES if nm_ != "skirt"]
SKV_lo = VL[PIECE_IDX["skirt"]]; SKF_lo = PIECE_LO["skirt"]["F"]
_Mb = PIECE_LO["skirt"]["M"]
SK_TOP_IDX = PIECE_IDX["skirt"][(len(W_OUT) + 1) * _Mb + np.arange(_Mb)]
TS_TOP_IDX = {nm_: PIECE_IDX[nm_][(len(W_OUT) + 1) * PIECE_LO[nm_]["M"] + np.arange(PIECE_LO[nm_]["M"])] for nm_ in TASSETS}
SKIRT_ALL = np.isin(fkind, ("skirt", "tasset"))
# ---- v4: the traced W re-verified on the SPLIT hem, in the annotation camera (the v2 front camera riding with the
# trunk). Every outer hem vertex of the front half (plates + the back shell's side ends, |rho| <= 90) is projected; per
# stroke pixel column inside the projected span, the hem row (linear between the projected hem vertices, gaps between
# plates bridged) is compared with the stroke's lowest pixel row. The calibration fixes the 5 knots by construction;
# this is the whole-profile check, and the corner columns show the width match.
_hem_pts = []
for nm_, d_ in PIECE_LO.items():
    ring0 = PIECE_IDX[nm_][:d_["M"]]
    rho_ = np.degrees(np.arctan2(VL[ring0, 0], -(VL[ring0, 1] - YC)))
    _hem_pts.append(VL[ring0][np.abs(rho_) <= 90.0])
_hem_pts = np.vstack(_hem_pts)
_hp = np.array([proj_ann(p_) for p_ in _hem_pts])
# shape error in the calibration's own frame: each hem vertex's x normalised by the skirt's side radius -> the stroke
# column the calibration maps it to (the W symmetrised about the body and stretched to the skirt, as in v3) -> row error
_xi_h = np.array([p_[0] / rs_side[int(p_[0] > 0)] for p_ in _hem_pts])
_ud_h = np.interp(_xi_h, kn_xi, KN_U)
_err_map = _hp[:, 1] - np.interp(_ud_h, _cols, _prof)
_hp = _hp[np.argsort(_hp[:, 0])]
_inspan = (_cols >= _hp[0, 0]) & (_cols <= _hp[-1, 0])
_hv = np.interp(_cols[_inspan], _hp[:, 0], _hp[:, 1])
_err = _hv - _prof[_inspan]
SK_W_CHECK = {"stroke_columns": int(len(_cols)), "columns_inside_hem_span": int(_inspan.sum()),
              "hem_vertices_front_half": int(len(_hem_pts)),
              "shape_row_error_px_calibration_frame": {"mean_abs": round(float(np.abs(_err_map).mean()), 2),
                                                       "p95_abs": round(float(np.percentile(np.abs(_err_map), 95)), 2),
                                                       "max_abs": round(float(np.abs(_err_map).max()), 2)},
              "raw_same_column_note": "the W is symmetrised about the body (the stroke's tip sits 23 px left of centre) and "
                                      "stretched to the skirt's clearance width (the drawn side lines are narrower), as v3 "
                                      "calibrated it; the raw same-pixel-column error below measures that, not the split",
              "row_error_px": {"mean_abs": round(float(np.abs(_err).mean()), 2), "p95_abs": round(float(np.percentile(np.abs(_err), 95)), 2),
                               "max_abs": round(float(np.abs(_err).max()), 2), "mean_signed(+ = hem lower)": round(float(_err.mean()), 2)},
              "hem_span_px_u": [round(float(_hp[0, 0]), 1), round(float(_hp[-1, 0]), 1)],
              "stroke_span_px_u": [int(_cols.min()), int(_cols.max())],
              "knot_rows_px_stroke": KN_V.round(1).tolist(),
              "knot_rows_px_hem": [round(float(np.interp(u_, _hp[:, 0], _hp[:, 1])), 1) for u_ in KN_U],
              "camera": "v2 sheet front camera (trace json) translated by the trunk offset %s m" % DT_ANN.round(4).tolist()}

# hinge of each plate: its outer surface at the attach line, mid-plate azimuth; axis = horizontal, -tangent (a
# positive swing carries the hem OUTWARD)
HINGE = {}
for nm_ in TASSETS:
    ka_, kb_ = PIECE_LO[nm_]["rho"]
    phm = math.radians(0.5 * (ka_ + kb_)) - math.pi / 2
    rt_, za_ = float(pint(R_TOP, [phm])[0]), float(pint(ZAJ, [phm])[0])
    hem_c = VL[PIECE_IDX[nm_]][PIECE_LO[nm_]["vw"] == 0.0].mean(0)
    HINGE[nm_] = {"p": np.array([rt_ * math.cos(phm), YC + rt_ * math.sin(phm), za_]),
                  "axis": np.array([math.sin(phm), -math.cos(phm), 0.0]), "hem_centre": hem_c,
                  "phi_mid_deg": round(0.5 * (ka_ + kb_), 2)}
# rest clearance: every swept thigh sample vs the skirt's inner surface (radial, same azimuth bin/height)
col_gap = []
for j in range(SK_N):
    for k_, z in enumerate(ZG):
        if HEM[j] <= z <= ZAJ[j] - 0.02 and RTH[j, k_] > 0:
            col_gap.append(float(sk_R(ZAJ[j], z, R_TOP[j], R_BOT[j]) - SK_T - RTH[j, k_]))
thv = np.isin(vlabel, ["thigh.L", "thigh.R"])
bvh_sk_rest = BVHTree.FromPolygons(VL.tolist(), FL[SKIRT_ALL].tolist())
gap_rest = [bvh_sk_rest.find_nearest(Vector(p))[3] for p in VL[thv] if HEM.min() - 0.05 < p[2] < ZAJ.max() + 0.1]
report["skirt"] = {
    "axis_y": round(YC, 4), "trunk_base_z": round(Z_BASE, 4),
    "attach_z_front_back_sides": [round(Z_ATT, 4), round(float(ZAJ.max()), 4)],
    "hem_z": {"front_tip": round(float(HEM[0]), 4), "front_notches": [round(float(HEM[1]), 4), round(float(HEM[-1]), 4)],
              "sides_+x_-x": [round(float(HEM[SK_N // 4]), 4), round(float(HEM[3 * SK_N // 4]), 4)],
              "back_tip": round(float(HEM[SK_N // 2]), 4), "lowest": round(Z_LO, 4),
              "initial_guess_depth_below_trunk_base_m": {"tip": SK_TIP, "notches_-x_+x": list(SK_PEAK), "corners_-x_+x": list(SK_CORNER), "back_scale": SK_BACK}},
    "radius_m": {"attach_min_max": [round(float(R_TOP.min()), 3), round(float(R_TOP.max()), 3)],
                 "hem_min_max": [round(float(R_BOT.min()), 3), round(float(R_BOT.max()), 3)],
                 "half_width_x_at_hem": round(float(max(R_BOT[SK_N // 4], R_BOT[3 * SK_N // 4])), 3),
                 "front_y_at_hem": round(float(YC - R_BOT[0]), 3)},
    "per_azimuth": {"phi_deg": np.degrees(PHS).round(1).tolist(), "hem_z": HEM.round(3).tolist(),
                    "r_attach": R_TOP.round(3).tolist(), "r_hem": R_BOT.round(3).tolist(),
                    "binding_z_rtrunk_rthigh_g": BIND},
    "plate_m": SK_T, "air_gap_to_swept_thigh_m": SK_CL,
    "sweep": {"pitch_deg": list(SK_SWEEP_PITCH), "roll_deg": SK_SWEEP_ROLL, "samples": _n_sw, "outside_trunk": int(len(sw_pts))},
    "rest_clearance": {"swept_column_gap_min_m": round(min(col_gap), 4) if col_gap else None,
                       "rest_thigh_vertex_to_skirt_min_m": round(float(min(gap_rest)), 4) if gap_rest else None},
    "pieces_low": {nm_: {"verts": int(len(d_["V"])), "tris": int(len(d_["F"])), "samples_per_ring": d_["M"], "rings": d_["NR"],
                         "seams_rho_deg": list(d_["rho"]), "closure": edge_audit(d_["F"]), "volume_m3": round(signed_volume(d_["V"], d_["F"]), 4),
                         "split_wall_tris": int((d_["fk"] == "split").sum())} for nm_, d_ in PIECE_LO.items()},
    "tris_low_total": int(sum(len(d_["F"]) for d_ in PIECE_LO.values())),
    "high": {nm_: int(len(v_[1])) for nm_, v_ in PIECE_HI.items()},
    "seams": {"zigzag_knots_w": ZIG_W.tolist(), "zigzag_offsets_m": {str(k_): v_.round(4).tolist() for k_, v_ in SEAMS.items()},
              "gap_hem_belt_m": list(TS_GAP), "seams_deg": TS_SEAMS},
    "hinges": {nm_: {"p": h_["p"].round(4).tolist(), "axis": h_["axis"].round(4).tolist(), "mid_rho_deg": h_["phi_mid_deg"]} for nm_, h_ in HINGE.items()},
    "trace": "design/refs/eldroot-v3-trace.json (eldroot3_trace_refs.py: crops registered on renders/eldroot-standing2/standing_front.png, "
             "strokes back-projected through the v2 sheet camera)",
    "hem_calibration_in_drawing_view": SK_CALIB,
    "w_hem_recheck_split_plates": SK_W_CHECK,
    "annotation_camera": {"loc_v2": list(CAM_LOC), "centre_v2": list(_cam["centre"]), "trunk_offset_m": DT_ANN.round(5).tolist()},
    "seconds": round(time.time() - t7b, 1)}

# high master (rest frame): trunk high minus the legs + membrane highs + shell highs
_, nnear = None, None
removed_low = np.ones(n0, bool); removed_low[TOI] = False      # every low vertex the cut (+ hole growth) took
hi_leg = np.array([removed_low[kd0.find(p)[1]] for p in WH])
keepF = ~hi_leg[FH].any(1)
HV = [WH + T_REST]; HF = [FH[keepF]]; hoff = len(WH)
HGRP = ["trunk"]                                                  # v3: bake group of each high piece
for p in patches:
    HV.append(p["Vh"] + T_REST); HF.append(p["Th"] + hoff); hoff += len(p["Vh"]); HGRP.append("trunk")
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_HI[s][part]
        HV.append(V + S_RC); HF.append(F + hoff); hoff += len(V); HGRP.append(part)
for nm_, (Vh_, Fh_) in PIECE_HI.items():                           # v4 plates + back shell highs (rest frame)
    HV.append(Vh_); HF.append(Fh_ + hoff); hoff += len(Vh_); HGRP.append("skirt")
HVa = np.vstack(HV); HFa = np.vstack(HF)
HF_GRP = np.concatenate([np.full(len(f_), g_) for f_, g_ in zip(HF, HGRP)])
report["high_master"]["standing_tris"] = int(len(HFa))
report["high_master"]["trunk_high_faces_removed_as_leg"] = int((~keepF).sum())

# ---- cavity per new low face (shell highs: grid Laplacian on the high shells, scaled like the approved look)
def shell_cavity(V, F):
    ev = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    ev = np.unique(np.sort(ev, 1), axis=0)
    deg = np.bincount(ev.ravel(), minlength=len(V)).astype(float)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, F[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    el = np.linalg.norm(V[ev[:, 0]] - V[ev[:, 1]], axis=1).mean()
    cav = ((neighbour_mean(V, ev, deg) - V) * vn).sum(1) / el
    for _ in range(6):
        cav = neighbour_mean(cav[:, None], ev, deg)[:, 0] * 0.5 + cav * 0.5
    return np.clip(cav / CAV_SCALE, -1, 1)


cav_pts, cav_val = [], []
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_HI[s][part]
        cav_pts.append(V + S_RC); cav_val.append(shell_cavity(V, F))
for Vh_, Fh_ in PIECE_HI.values():
    cav_pts.append(Vh_); cav_val.append(shell_cavity(Vh_, Fh_))
cav_pts = np.vstack(cav_pts); cav_val = np.concatenate(cav_val)
kdc = KDTree(len(cav_pts))
for i, p in enumerate(cav_pts):
    kdc.insert(p, i)
kdc.balance()


def srgb(r, g, b):
    def c(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return np.array([c(r), c(g), c(b)])


# ---- colours: trunk corners verbatim; new faces by improve_unit's eldroot region rules
nf = len(FL)
FC = VL[FL].mean(1)
FNr = np.cross(VL[FL[:, 1]] - VL[FL[:, 0]], VL[FL[:, 2]] - VL[FL[:, 0]])
FNr /= np.maximum(np.linalg.norm(FNr, axis=1), 1e-12)[:, None]
newf = fkind != "trunk"
# membranes are judged in trunk (seated) coordinates like their neighbours; legs in the rest frame
FCq = FC.copy()
FCq[np.isin(fkind, ("membrane", "trunk"))] -= T_REST
fh_ = FCq[:, 2] / H_SEAT
fcav = np.zeros(nf)
for i in np.nonzero(newf & (fkind != "membrane"))[0]:
    fcav[i] = float(np.mean([cav_val[j] for (_, j, _) in kdc.find_n(FC[i], 6)]))
colf = np.tile(srgb(94, 78, 64), (nf, 1))
t_ = 1 - smoothstep(0.22, 0.34, fh_); colf = colf * (1 - t_[:, None]) + srgb(66, 52, 44) * t_[:, None]
t_ = smoothstep(0.50, 0.75, FNr[:, 2]) * smoothstep(0.15, 0.30, fh_) * (fh_ < 0.93) * (fcav < 0.2) * ((fh_ < 0.55) | (FCq[:, 1] > 0))
colf = colf * (1 - t_[:, None]) + srgb(92, 118, 60) * t_[:, None]
t_ = np.where((fcav > 0.35) & (fh_ > 0.45), smoothstep(0.35, 0.7, fcav), 0.0)
colf = colf * (1 - t_[:, None]) + srgb(30, 22, 18) * t_[:, None]
shade = 1.0 - 0.45 * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FCq @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
colf = np.clip(colf * shade[:, None], 0, 1)
COLC = np.zeros((nf, 3, 4)); GLOWC = np.zeros((nf, 3, 4))
ntr = len(TF)
COLC[:ntr] = TCOL; GLOWC[:ntr] = TGLOW
COLC[ntr:, :, :3] = colf[ntr:, None, :]; COLC[ntr:, :, 3] = 1.0
GLOWC[ntr:, :, 3] = 1.0
# the leg/trunk crease no longer exists: trunk faces near the cut lose its cavity darkening (blend to the plain
# region rule over 5-15 cm from the cut), everything else keeps its approved colour verbatim
loopv = np.concatenate([np.array(lp) for lp in loops_])
Ftc = TV[TF].mean(1)
dband = np.min(np.stack([np.linalg.norm(Ftc - TV[v], axis=1) for v in loopv]), axis=0)
wb = 1 - smoothstep(0.05, 0.15, dband)
COLC[:ntr, :, :3] = COLC[:ntr, :, :3] * (1 - wb[:, None, None]) + colf[:ntr, None, :] * wb[:, None, None]
# v3: hollow insides of the skirt plate and the crowns read as dark cavities; the crowns' broken rims as paler wood
hol = np.isin(ftag, ("skirt_in", "collar_in"))
COLC[hol, :, :3] = COLC[hol, :, :3] * 0.25 + srgb(30, 22, 18)[None, None, :] * 0.75
rimf = np.isin(ftag, ("collar_rim", "split"))                   # v4: the plates' split walls = the same broken wood
COLC[rimf, :, :3] = COLC[rimf, :, :3] * 0.45 + srgb(132, 104, 76)[None, None, :] * 0.55
tin = ftag == "tasset_in"                                        # v4: a plate's underside shows when it swings out
COLC[tin, :, :3] = COLC[tin, :, :3] * 0.5 + srgb(58, 44, 36)[None, None, :] * 0.5
report["look"] = {"trunk_corner_colours": "verbatim (approved palette) except the old crease band",
                  "trunk_faces_recoloured_crease_band": {"any": int((wb > 1e-3).sum()), "full": int((wb > 0.999).sum())},
                  "new_faces": int(newf.sum()),
                  "new_faces_by_kind": {k: int((fkind == k).sum()) for k in ("membrane", "thigh", "shin", "foot", "skirt", "tasset")},
                  "v4_plate_underside_faces": int(tin.sum()), "v4_split_wall_faces": int((ftag == "split").sum()),
                  "v3_hollow_dark_faces": int(hol.sum()), "v3_crown_rim_faces": int(rimf.sum()),
                  "rule": "improve_unit eldroot regions (old_bark / root_feet by height / moss up-facing / hollow_dark cavity) + cavity shade k 0.45 + jitter"}

# ---- build the mesh
me = bpy.data.meshes.new("eldroot4_mesh")
me.from_pydata(VL.tolist(), [], FL.tolist())
me.update()
me.shade_flat()
ca = me.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
ga = me.color_attributes.new("Glow", "FLOAT_COLOR", "CORNER")
ca.data.foreach_set("color", COLC.reshape(-1).astype(np.float32))
ga.data.foreach_set("color", GLOWC.reshape(-1).astype(np.float32))
me.color_attributes.active_color = ca
me.color_attributes.render_color_index = me.color_attributes.find("Col")
mat = me0.materials[0]
me.materials.append(mat)
low.modifiers.remove(low.modifiers["Armature"])
low.parent = None
bpy.data.objects.remove(old_rig, do_unlink=True)
low.data = me
low.vertex_groups.clear()
bpy.data.meshes.remove(me0)
bpy.data.meshes.remove(me_hi)

if "--geo-only" in argv:
    hm = bpy.data.meshes.new("eldroot4_high"); hm.from_pydata(HVa.tolist(), [], HFa.tolist()); hm.update()
    ho = bpy.data.objects.new("eldroot4_high", hm); scene.collection.objects.link(ho); ho.hide_render = True
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, copy=True, compress=True)
    json.dump(report, open(OUT_JSON, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("GEO_DONE", json.dumps({k: report[k] for k in ("measure", "leg_solve", "trunk_mass")}, default=str))
    sys.stdout.flush(); os._exit(0)

# ====================================================================== 8. UVs + bake (whole standing low from the standing high)
t8 = time.time()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.object.mode_set(mode="OBJECT")
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn); UVn = UVn.reshape(-1, 3, 2)
uva = 0.5 * np.abs((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 1, 1] - UVn[:, 0, 1]) * (UVn[:, 2, 0] - UVn[:, 0, 0]))
a3 = 0.5 * np.linalg.norm(np.cross(VL[FL[:, 1]] - VL[FL[:, 0]], VL[FL[:, 2]] - VL[FL[:, 0]]), axis=1)
dens = np.sqrt(uva / np.maximum(a3, 1e-12))
legf = np.isin(fkind, ("thigh", "shin", "foot"))
report["uv"] = {"uv_sha": sha(UVn), "method": "fresh Smart UV (improve_unit params: 66 deg, margin 0.004) on the whole standing mesh",
                "linear_texel_density_uv_per_m": {"trunk_median": round(float(np.median(dens[fkind == 'trunk'])), 4),
                                                  "legs_median": round(float(np.median(dens[legf])), 4),
                                                  "legs_p05_p95": [round(float(np.percentile(dens[legf], 5)), 4), round(float(np.percentile(dens[legf], 95)), 4)],
                                                  "trunk_p05_p95": [round(float(np.percentile(dens[fkind == 'trunk'], 5)), 4), round(float(np.percentile(dens[fkind == 'trunk'], 95)), 4)]},
                "approved_seated_density_ref": round(math.sqrt(0.458 / 26.459), 4)}
# v3: the standing high is split per bake group (trunk+membranes / thighs / shins / feet / skirt). Every group object
# stays in the scene (AO still sees every occluder), but each group's low faces are re-baked with ONLY their own high
# selected: with one merged high, cage rays from the skirt's inside, the crowns' inner walls and the trunk under the
# skirt landed on the neighbouring piece 3-9 cm away (first v3 bake: trunk 98.66 % vs v2 99.99 %, crown inner walls
# normal deviation 0.38).
HOBJ = {}
for g_ in ("trunk", "thigh", "shin", "foot", "skirt"):
    Fg = HFa[HF_GRP == g_]
    vid_ = np.unique(Fg); rm_ = -np.ones(len(HVa), dtype=np.int64); rm_[vid_] = np.arange(len(vid_))
    hm_ = bpy.data.meshes.new("eldroot4_high_" + g_); hm_.from_pydata(HVa[vid_].tolist(), [], rm_[Fg].tolist()); hm_.update()
    if hi_smooth_frac > 0.5:
        hm_.shade_smooth()
    HOBJ[g_] = bpy.data.objects.new("eldroot4_high_" + g_, hm_); scene.collection.objects.link(HOBJ[g_])
FGRP = np.where(np.isin(fkind, ("trunk", "membrane")), "trunk", np.where(fkind == "tasset", "skirt", fkind))
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
EXT = 0.076                                              # the approved bake's cage (0.02 x 3.8 m footprint)
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = EXT; bk.max_ray_distance = EXT * 2.0; bk.margin = 16; bk.use_clear = False
nt_ = mat.node_tree
img_nodes = [n for n in nt_.nodes if n.bl_idname == "ShaderNodeTexImage"]
old_imgs = [n.image for n in img_nodes if n.image]
img_n = bpy.data.images.new("eldroot4_normal", 2048, 2048, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new("eldroot4_ao", 1024, 1024, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
tn = ta = None
for n_ in img_nodes:
    if n_.image and "normal" in n_.image.name:
        n_.image = img_n; tn = n_
    elif n_.image and "ao" in n_.image.name:
        n_.image = img_ao; ta = n_
for im in old_imgs:
    if im.users == 0:
        bpy.data.images.remove(im)
me.shade_smooth()
bpy.context.view_layer.objects.active = low
bstats = {}


def bake_pass(sel_objs, tag):
    """NORMAL + AO of the whole low against the selected high objects, into fresh images; returns pixel arrays."""
    for o in scene.objects:
        o.select_set(o in sel_objs or o is low)
    bpy.context.view_layer.objects.active = low
    im_n = bpy.data.images.new("bk_n_" + tag, 2048, 2048, alpha=False)
    im_n.colorspace_settings.name = "Non-Color"; im_n.generated_color = (0.0, 0.0, 0.0, 1.0)
    im_a = bpy.data.images.new("bk_a_" + tag, 1024, 1024, alpha=False)
    im_a.colorspace_settings.name = "Non-Color"; im_a.generated_color = (1.0, 0.0, 1.0, 1.0)
    tn.image, ta.image = im_n, im_a
    st = {}
    for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
        nt_.nodes.active = node
        scene.cycles.samples = samples
        t = time.time()
        r = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=EXT, margin=16, use_clear=False)
        st[typ] = {"result": sorted(r), "seconds": round(time.time() - t, 1)}
    pn = np.empty(2048 * 2048 * 4, dtype=np.float32); im_n.pixels.foreach_get(pn)
    pa_ = np.empty(1024 * 1024 * 4, dtype=np.float32); im_a.pixels.foreach_get(pa_)
    bpy.data.images.remove(im_n); bpy.data.images.remove(im_a)
    return pn.reshape(-1, 4), pa_.reshape(-1, 4), st


px, pa, bstats["pass_all"] = bake_pass(list(HOBJ.values()), "all")       # base (margins, as v2)
tn.image, ta.image = img_n, img_ao
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn); UVn = UVn.reshape(-1, 3, 2)


def texels_of(mask_faces, res):
    """Texel mask (res x res) covered by the given faces' UV triangles (centre sampling)."""
    m = np.zeros((res, res), bool)
    for t in UVn[mask_faces]:
        p = t * res
        x0, y0 = np.floor(p.min(0)).astype(int); x1, y1 = np.ceil(p.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, res)), np.arange(max(y0, 0), min(y1, res)))
        if xs.size == 0:
            continue
        q = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1)
        a, b, c = p

        def ed(u_, v_):
            return (v_[0] - u_[0]) * (q[:, 1] - u_[1]) - (v_[1] - u_[1]) * (q[:, 0] - u_[0])
        d1, d2, d3 = ed(a, b), ed(b, c), ed(c, a)
        ins = ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))
        m[ys.ravel()[ins], xs.ravel()[ins]] = True
    return m.ravel()


iso = {}
for g_ in ("trunk", "thigh", "shin", "foot", "skirt"):
    pn_g, pa_g, st_g = bake_pass([HOBJ[g_]], g_)
    fm_g = FGRP == g_
    mn = texels_of(fm_g, 2048) & (pn_g[:, 2] > 0.25)
    ma = texels_of(fm_g, 1024) & (np.abs(pa_g[:, 0] - pa_g[:, 1]) < 0.02)
    px[mn] = pn_g[mn]; pa[ma] = pa_g[ma]
    iso[g_] = {"faces": int(fm_g.sum()), "normal_texels_from_isolated_pass": int(mn.sum()), "ao_texels_from_isolated_pass": int(ma.sum()),
               "seconds": round(st_g["NORMAL"]["seconds"] + st_g["AO"]["seconds"], 1)}
tn.image, ta.image = img_n, img_ao
bstats["isolated_passes"] = iso
bstats["rule"] = ("base pass: whole low vs every high (margins); then each group's texels re-baked against its own high only "
                  "(normal projection isolated, AO occluded by the whole scene)")
me.shade_flat()
dev = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
legtex = texels_of(legf, 2048)
trunktex = texels_of(fkind == "trunk", 2048)
membtex = texels_of(fkind == "membrane", 2048)
bstats.update({
    "normal_covered_texels": round(float(cov_n.mean()), 4),
    "normal_detail_fraction_dev_gt_0.05": round(float((dev[cov_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(dev[cov_n].mean()), 4),
    "legs": {"texels": int(legtex.sum()), "baked_pct": round(100 * float(cov_n[legtex].mean()), 2),
             "detail_fraction_dev_gt_0.05": round(float((dev[legtex & cov_n] > 0.05).mean()), 4), "normal_dev_mean": round(float(dev[legtex & cov_n].mean()), 4)},
    "trunk": {"texels": int(trunktex.sum()), "baked_pct": round(100 * float(cov_n[trunktex].mean()), 2),
              "detail_fraction_dev_gt_0.05": round(float((dev[trunktex & cov_n] > 0.05).mean()), 4), "normal_dev_mean": round(float(dev[trunktex & cov_n].mean()), 4)},
    "membranes": {"texels": int(membtex.sum()), "baked_pct": round(100 * float(cov_n[membtex].mean()), 2) if membtex.any() else None},
    **{("v3_" + nm_): {"faces": int(fm_.sum()), "texels": int(tx_.sum()), "baked_pct": round(100 * float(cov_n[tx_].mean()), 2),
                       "detail_fraction_dev_gt_0.05": round(float((dev[tx_ & cov_n] > 0.05).mean()), 4),
                       "normal_dev_mean": round(float(dev[tx_ & cov_n].mean()), 4),
                       "ao_baked_pct": round(100 * float(cov_a[texels_of(fm_, 1024)].mean()), 2),
                       "ao_mean": round(float(pa[texels_of(fm_, 1024) & cov_a, 0].mean()), 4)}
       for nm_, fm_, tx_ in [(nm_, fm_, texels_of(fm_, 2048)) for nm_, fm_ in
                             (("skirt_outer_incl_plates", ftag == "skirt"), ("skirt_inner", ftag == "skirt_in"),
                              ("plates_underside", ftag == "tasset_in"), ("plate_split_walls", ftag == "split"),
                              ("collars_outer_and_rim", np.isin(ftag, ("collar", "collar_rim"))), ("collars_inner", ftag == "collar_in"))]},
    "ao_covered_texels": round(float(cov_a.mean()), 4), "ao_mean": round(float(pa[cov_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4),
    "approved_seated_reference": {"normal_covered_texels": 0.7129, "normal_detail_fraction_dev_gt_0.05": 0.1992, "normal_dev_mean": 0.0369,
                                  "ao_covered_texels": 0.7864, "ao_mean": 0.7891}})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, "eldroot4_normal.png"), (img_ao, "eldroot4_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
    img.filepath = "//../improved/textures/" + nm
bstats["pixel_sha"] = {"normal": sha(px), "ao": sha(pa)}
bstats["cage_extrusion"] = EXT
bstats["seconds_uv_and_bake"] = round(time.time() - t8, 1)
report["bake"] = bstats
for o_ in HOBJ.values():
    m_ = o_.data
    bpy.data.objects.remove(o_, do_unlink=True); bpy.data.meshes.remove(m_)
scene.render.engine = "BLENDER_EEVEE"

# ====================================================================== 9. rig + weights
arm_data = bpy.data.armatures.new("eldroot_rig")
rig = bpy.data.objects.new("eldroot_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
spec = [("root", old_bones["root"][0], old_bones["root"][1], None)]
for nm in ("pelvis", "chest", "crown", "upperarm.L", "forearm.L", "upperarm.R", "forearm.R"):
    hd, tl, par = old_bones[nm]
    spec.append((nm, hd + T_REST, tl + T_REST, par))
for s in "LR":
    J = LEGS_LO[s]["joints"]
    spec += [("thigh." + s, J["hip"] + S_RC, J["knee"] + S_RC, "pelvis"), ("shin." + s, J["knee"] + S_RC, J["ankle"] + S_RC, "thigh." + s),
             ("foot." + s, J["ankle"] + S_RC, J["toe"] + S_RC, "shin." + s)]
SK_PIV_np = (LEGS_LO["L"]["joints"]["hip"] + LEGS_LO["R"]["joints"]["hip"]) / 2 + S_RC
spec.append(("skirt", SK_PIV_np, SK_PIV_np + np.array([0.0, 0.0, 0.5]), "pelvis"))      # v3/v4: the back shell
for nm_ in TASSETS:                                   # v4: one bone per plate, head on its belt hinge, tail toward its hem,
    h_ = HINGE[nm_]                                   # child of its thigh
    dv_ = h_["hem_centre"] - h_["p"]
    spec.append((nm_, h_["p"], h_["p"] + 0.6 * dv_ / np.linalg.norm(dv_), "thigh." + PIECE_LO[nm_]["side"]))
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
nV = len(VL)
Wn = np.zeros((nV, len(DEFORM)))
dc = {nm: i for i, nm in enumerate(DEFORM)}
nonleg = [nm for nm in old_names if not nm.startswith(("thigh", "shin"))]
ntv = len(TV)
Wk = Wold[TOI]
folded = 0
for nm in nonleg:
    Wn[:ntv, dc[nm]] = Wk[:, col_[nm]]
legshare = Wk[:, [col_[x] for x in old_names if x.startswith(("thigh", "shin"))]].sum(1)
Wn[:ntv, dc["pelvis"]] += legshare
folded = int((legshare > 1e-6).sum())
for (a0, a1, loopids) in patch_vert_ranges:
    Pm = VL[a0:a1]; B = VL[loopids]
    d = np.linalg.norm(Pm[:, None, :] - B[None, :, :], axis=2)
    w = 1.0 / np.maximum(d, 1e-4) ** 2
    Wn[a0:a1] = (w @ Wn[loopids]) / w.sum(1)[:, None]
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        Wn[vlabel == "%s.%s" % (part, s), dc["%s.%s" % (part, s)]] = 1.0
Wn[vlabel == "skirt", dc["skirt"]] = 1.0                                          # v3: rigid plate
for nm_ in TASSETS:
    Wn[vlabel == nm_, dc[nm_]] = 1.0                                             # v4: rigid plates
# cap influences at 4 (glTF), renormalise
for i in np.nonzero((Wn > 1e-5).sum(1) > 4)[0]:
    keep = np.argsort(Wn[i])[-4:]
    row = np.zeros_like(Wn[i]); row[keep] = Wn[i, keep]; Wn[i] = row
Wn /= np.maximum(Wn.sum(1), 1e-30)[:, None]
for j, nm in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=nm)
    for i in np.nonzero(Wn[:, j] > 1e-5)[0]:
        vg.add([int(i)], float(Wn[i, j]), "REPLACE")
report["reweight"] = {"trunk_verts_verbatim": int(ntv - folded), "trunk_verts_seated_leg_share_folded_to_pelvis": folded,
                      "membrane_verts_inverse_distance_from_boundary": int(sum(a1 - a0 for a0, a1, _ in patch_vert_ranges)),
                      "leg_shell_verts_rigid": int(np.isin(vlabel, [f"{p}.{s}" for p in ("thigh", "shin", "foot") for s in "LR"]).sum()),
                      "skirt_verts_rigid": int((vlabel == "skirt").sum()),
                      "tasset_verts_rigid": {nm_: int((vlabel == nm_).sum()) for nm_ in TASSETS},
                      "max_influences": int((Wn > 1e-5).sum(1).max()),
                      "per_bone_dominant": {nm: int((np.argmax(Wn, 1) == j).sum()) for j, nm in enumerate(DEFORM)}}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
mod = low.modifiers.new("Armature", "ARMATURE")
mod.object = rig

# ====================================================================== 10. pose machinery
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
LL1 = {s: (RT["thigh." + s] - RH["thigh." + s]).length for s in "LR"}
LL2 = {s: (RT["shin." + s] - RH["shin." + s]).length for s in "LR"}
A1 = {s: (RT["upperarm." + s] - RH["upperarm." + s]).length for s in "LR"}
A2 = {s: (RT["forearm." + s] - RH["forearm." + s]).length for s in "LR"}


def perp_pole(hd, kn, tl):
    dv = (tl - hd).normalized()
    p = (kn - hd) - dv * (kn - hd).dot(dv)
    return p.normalized()


POLE_ARM = {s: perp_pole(RH["upperarm." + s], RH["forearm." + s], RT["forearm." + s]) for s in "LR"}
DSIT = Vector((0.0, DY_SIT, -Z_B))
PIVOT = Vector((0.0, -0.35, 0.05)) + Vector(T_REST)     # trunk front-bottom edge (rest)
SEAT_PAD = {s: RT["forearm." + s] + DSIT for s in "LR"}
ANKLE = {s: RH["foot." + s].copy() for s in "LR"}


def Tm(v):
    return Matrix.Translation(Vector(v))


def G_about(head_new, R3, head_rest):
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
    hh_ = math.sqrt(max(l1 * l1 - a_ * a_, 0.0))
    return H + dv * a_ + pp * hh_, H + dv * Dc, D / (l1 + l2)


def aim_R(Rcur, rest_dir, target_dir):
    cur = (Rcur @ rest_dir).normalized()
    q = cur.rotation_difference(target_dir.normalized())
    return q.to_matrix() @ Rcur


reach_log = {}
skirt_log = []
SK_PIV = Vector(SK_PIV_np)
SK_SIT = {"pitch": 0.0, "back": 0.0}          # sit-state skirt pose (deg about the hip line, m rearward); searched below


def skirt_local(beta_deg, back_m):
    """Skirt bone's pose in the pelvis's rest frame: pitch about the hip line (negative = front up), then a rearward
    shift."""
    return Tm(SK_PIV) @ Euler((math.radians(beta_deg), 0.0, 0.0)).to_matrix().to_4x4() @ Tm(-SK_PIV) @ Tm((0.0, back_m, 0.0))


TS_SIT = {nm_: Quaternion() for nm_ in TASSETS}      # sit-state plate rotations about their hinges (searched below)
fold_log = []
tasset_log = []
RHM_REST = (RH["thigh.L"] + RH["thigh.R"]) / 2           # the hip line's midpoint (rest)
TS_CARRY = {"cap_deg": 180.0, "tuck_from_deg": opt("--tuck-from", 100.0)}   # searched on the sitting pose (thigh mode)
TS_TUCK = {nm_: (0.0, 0.0, 0.0) for nm_ in TASSETS}       # per-plate tuck into the trunk (m, searched; pelvis axes)


TS_LIFT = {nm_: 0.0 for nm_ in TASSETS}                # sit-state belt ride-up of each plate (m, searched below)


def tasset_local(nm, u):
    """Plate pose in the pelvis rest frame: fraction u of the rest -> sit motion = the belt riding up the trunk by
    u x TS_LIFT (like the back shell's floor ride) + the rotation about the plate's belt hinge."""
    u = min(max(u, 0.0), 1.0)
    H_ = Vector(HINGE[nm]["p"])
    q_ = Quaternion().slerp(TS_SIT[nm], u)
    return Tm((0.0, 0.0, u * TS_LIFT[nm])) @ Tm(H_) @ q_.to_matrix().to_4x4() @ Tm(-H_)


def solve(P):
    G = {"root": Matrix.Identity(4)}
    thigh_dir = {}
    d = Vector((P.get("px", 0.0), P.get("py", 0.0), P.get("lift", 0.0)))
    Rp = eul(P.get("pelvis", (0, 0, 0)))
    G["pelvis"] = Tm(PIVOT + d) @ Rp.to_4x4() @ Tm(-PIVOT)
    hc = G["pelvis"] @ RH["chest"]
    G["chest"] = G_about(hc, eul(P.get("chest", (0, 0, 0))) @ Rp, RH["chest"])
    hk = G["chest"] @ RH["crown"]
    G["crown"] = G_about(hk, eul(P.get("crown", (0, 0, 0))) @ rot_of(G["chest"]), RH["crown"])
    for s in "LR":
        H = G["pelvis"] @ RH["thigh." + s]
        A_ = Vector(P.get("feet", {}).get(s, ANKLE[s]))
        # knee pole: the in-sagittal-plane perpendicular of hip->ankle, bent the forward/up way: forward while
        # the leg hangs straight, UP when folded (seated: hip behind the ankle -> the knee rises over it).
        # Never parallel to the leg (the old forward->up blend went degenerate mid stand_up with the hip over
        # the ankle, and the knee flipped out sideways: a shin 99.5 mm under the floor at stand_up frame 65).
        pole = (A_ - H).normalized().cross(Rp @ Vector((1.0, 0.0, 0.0)))
        Kp, Tp, reach = ik2(H, A_, LL1[s], LL2[s], pole)
        thigh_dir[s] = (Kp - H).normalized()
        reach_log.setdefault("leg." + s, []).append(reach)
        Rt = aim_R(Rp, RT["thigh." + s] - RH["thigh." + s], Kp - H)
        G["thigh." + s] = G_about(H, Rt, RH["thigh." + s])
        Rs = aim_R(Rt, RT["shin." + s] - RH["shin." + s], Tp - Kp)
        G["shin." + s] = G_about(Kp, Rs, RH["shin." + s])
        yaw = P.get("foot_yaw", {}).get(s, 0.0)
        G["foot." + s] = G_about(A_, eul((0, 0, yaw)), RH["foot." + s])
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
        if plant > 0.5:
            reach_log.setdefault("arm." + s, []).append(rch)
        Ru_ik = aim_R(Rp, RT["upperarm." + s] - RH["upperarm." + s], Ke - Hs)
        Rf_ik = aim_R(Ru_ik, RT["forearm." + s] - RH["forearm." + s], Te - Ke)
        qu = Ru_fk.to_quaternion().slerp(Ru_ik.to_quaternion(), plant)
        Gu = G_about(Hs, qu.to_matrix(), RH["upperarm." + s])
        he = Gu @ RH["forearm." + s]
        qf = Rf_fk.to_quaternion().slerp(Rf_ik.to_quaternion(), plant)
        G["upperarm." + s], G["forearm." + s] = Gu, G_about(he, qf.to_matrix(), RH["forearm." + s])
    # v3 skirt: rigid with the pelvis while the thighs hang (idle/walk: thigh <= 23 deg off the pelvis axis); as the
    # thighs fold (40 -> 120 deg) it blends into the searched sit pose (SK_SIT: pitch about the hip line + rearward shift) and rides the floor:
    # lift = exactly what keeps its lowest vertex SK_FLOOR above the ground (0 whenever it is clear).
    dn = Rp @ Vector((0.0, 0.0, -1.0))
    th_ang = float(np.mean([math.degrees(thigh_dir[s].angle(dn)) for s in "LR"]))
    wf = float(smoothstep(40.0, 120.0, th_ang))
    beta = SK_SIT["pitch"] * wf
    G0 = G["pelvis"] @ skirt_local(beta, SK_SIT["back"] * wf)
    M_ = np.array(G0)
    zmin = float((SKV_lo @ M_[2, :3] + M_[2, 3]).min())
    lift = max(0.0, SK_FLOOR - zmin)
    G["skirt"] = Tm((0.0, 0.0, lift)) @ G0
    skirt_log.append((th_ang, beta, lift))
    # v4 plates: each swings about its belt hinge along its rest -> sit path; u from the caller (clearance-driven
    # keying) or, by default, from its own thigh's fold
    th_side = {s: math.degrees(thigh_dir[s].angle(dn)) for s in "LR"}
    tu = P.get("tasset_u")
    if TS_MODE == "thigh":
        # the thighs' sagittal swing relative to the pelvis (0 hanging, 90 forward, >90 forward-up), both sides averaged:
        # every plate turns about the hip line by the same angle, so neighbours (and the two centre plates, carried by
        # different thighs) never rub - first v4 run with each plate rigid on its own thigh: 23 mm visible centre overlap
        pit_ = []
        for s in "LR":
            dl_ = Rp.transposed() @ thigh_dir[s]
            pit_.append(math.atan2(-dl_.y, -dl_.z))
        pitch_avg = 0.5 * (pit_[0] + pit_[1])
        u_all = float(smoothstep(TS_FOLD[0], TS_FOLD[1], 0.5 * (th_side["L"] + th_side["R"])))
        th_u = u_all * pitch_avg                        # the swing the fold carries ...
        th_c = min(th_u, math.radians(TS_CARRY["cap_deg"]))   # ... up to the cap that keeps the folded plates in
        v_t = float(smoothstep(TS_CARRY["tuck_from_deg"], TS_CARRY["cap_deg"], math.degrees(th_u)))
        G_car = G["pelvis"] @ Tm(RHM_REST) @ Matrix.Rotation(-th_c, 4, "X") @ Tm(-RHM_REST)
        tasset_log.append((math.degrees(pitch_avg), u_all, math.degrees(th_c), v_t))
    for nm_ in TASSETS:
        sd_ = PIECE_LO[nm_]["side"]
        u_ = tu[nm_] if tu is not None else float(smoothstep(TS_FOLD[0], TS_FOLD[1], th_side[sd_]))
        if TS_MODE == "thigh":                              # hinged on the hip line, carried by the thighs' fold,
            G[nm_] = Tm(Vector(TS_TUCK[nm_]) * v_t) @ G_car  # settling into the trunk once folded (world tuck, sit frame)
        else:
            G[nm_] = G["pelvis"] @ tasset_local(nm_, u_)
    fold_log.append(th_side)
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


apply({})
Cr = mesh_coords()
report["rest_equals_standing_mesh_max_mm"] = round(float(np.linalg.norm(Cr - VL, axis=1).max()) * 1000, 4)


# ====================================================================== 11. motion
def sn(t, k=1.0, ph=0.0):
    return math.sin(TAU * k * t + ph)


def cs(t, k=1.0, ph=0.0):
    return math.cos(TAU * k * t + ph)


def s01(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (6 * x - 15) + 10)


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


LTOT = min(LL1[s] + LL2[s] for s in "LR")
clamp_log = {"frames": 0}


def reach_clamp(P, cap=0.9985):
    """Keep every planted leg inside cap x full reach by lowering the hips (never lifts)."""
    Rp = eul(P.get("pelvis", (0, 0, 0)))
    for _ in range(3):
        d = Vector((P.get("px", 0.0), P.get("py", 0.0), P.get("lift", 0.0)))
        worst = 0.0
        for s in "LR":
            H = Tm(PIVOT + d) @ Rp.to_4x4() @ Tm(-PIVOT) @ RH["thigh." + s]
            A_ = Vector(P.get("feet", {}).get(s, ANKLE[s]))
            D = (H - A_).length
            lim = cap * (LL1[s] + LL2[s])
            if D > lim:
                v = H - A_
                # lower along z until |v| = lim
                hz = math.sqrt(max(lim * lim - v.x * v.x - v.y * v.y, 0.0))
                worst = max(worst, v.z - hz)
        if worst <= 1e-7:
            return P
        P["lift"] = P.get("lift", 0.0) - worst - 1e-5
        clamp_log["frames"] += 1
    return P


def idle_P(t):
    """Standing idle: the seated wave's creak-sway ported; knees ~10 deg off straight (the columns stay columns)."""
    return {"lift": -0.026 + 0.005 * sn(t),
            "pelvis": (0.5 * sn(t, 2), 1.0 * sn(t), 0.5 * sn(t, 1, 0.9)),
            "chest": (1.0 * sn(t, 2, -0.5), 1.5 * sn(t, 1, -0.5), 1.0 * sn(t, 1, -0.3)),
            "crown": (1.5 * sn(t, 1, -1.4), 3.0 * sn(t, 1, -1.2), 2.0 * sn(t, 1, -0.4)),
            "arms": {"L": {"rot": (4.0 + 1.5 * sn(t, 1, -0.9), 0.0, 1.0 * sn(t, 1, -0.4)), "fore": (-6.0 + 2.0 * sn(t, 1, -1.5), 0, 0)},
                     "R": {"rot": (4.0 + 1.5 * sn(t, 1, -0.9), 0.0, -1.0 * sn(t, 1, -0.4)), "fore": (-6.0 + 2.0 * sn(t, 1, -1.5), 0, 0)}}}


def sit_P(t):
    """Sitting idle: dormant, rooted; the trunk rests exactly on its sculpted base, knuckles planted."""
    return {"lift": -Z_B, "py": DY_SIT,
            "chest": (0.6 * sn(t), 0.0, 0.3 * sn(t, 1, 1.1)),
            "crown": (0.9 * sn(t, 1, -0.7), 0.6 * sn(t, 1, -1.3), 0.0),
            "arms": {s: {"plant": 1.0} for s in "LR"}}


def blendP(Pa, Pb, x):
    out = {}
    for k in ("lift", "px", "py"):
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
STAND_S, SIT_S = 5.0, 4.5

# ---- the rise path (hips), shared by stand_up and sit_down. The first v2 clips moved the hips forward over the
# feet while still low: a 2.9 m leg compressed to ~1 m folds the knee 1.2 m forward and DOWN (shin 80 deg over,
# 195 mm under the floor). Rule: the path is driven by the KNEE - it stays over the ankle, tipping at most
# PSI_MAX forward mid-rise - and the thigh swings from its seated angle (back-down) to vertical around it. The hips
# rise behind the feet first (knuckles still planted), then arc forward over the pads.
PSI_MAX = math.radians(24.0)
A_M = (ANKLE["L"] + ANKLE["R"]) / 2
RHM = (RH["thigh.L"] + RH["thigh.R"]) / 2
L1M = (LL1["L"] + LL1["R"]) / 2
L2M = (LL2["L"] + LL2["R"]) / 2
H_SEAT_W = RHM + DSIT
H_STAND_W = PIVOT + eul(I0["pelvis"]) @ (RHM - PIVOT) + Vector((0.0, 0.0, I0["lift"]))   # idle frame 1 hips, exactly
K_SEAT_W = A_M + Vector((0.0, 0.0, L2M))
_v = H_SEAT_W - K_SEAT_W
A_SEAT = math.atan2(_v.z, _v.y)                         # thigh elevation in the sagittal plane, from rearward (+Y)
L1YZ = math.hypot(_v.y, _v.z)


def _path_raw(g):
    psi = PSI_MAX * math.sin(math.pi * g)
    a = A_SEAT + (math.pi / 2 - A_SEAT) * g
    K = A_M + L2M * Vector((0.0, -math.sin(psi), math.cos(psi)))
    return K + L1YZ * Vector((0.0, math.cos(a), math.sin(a)))


_E0 = H_SEAT_W - _path_raw(0.0)
_E1 = H_STAND_W - _path_raw(1.0)


def hip_path(g):
    return _path_raw(g) + _E0 * (1.0 - g) + _E1 * g


def hips_to(P, H):
    """Pelvis translation that puts the mid-hip at H under the pose's pelvis rotation (about PIVOT)."""
    Rp = eul(P.get("pelvis", (0, 0, 0)))
    d = H - PIVOT - Rp @ (RHM - PIVOT)
    # floor guard: a forward pitch about the hips would sink the trunk front edge; while seated-low the trunk
    # instead rocks on that edge (PIVOT never below its seated height)
    d.z = max(d.z, DSIT.z)
    P["py"], P["lift"] = d.y, d.z
    return P


report["rise_path"] = {"knee_tip_max_deg": math.degrees(PSI_MAX), "thigh_seated_elevation_deg": round(math.degrees(A_SEAT), 2),
                       "endpoint_corrections_mm": {"seat": round(_E0.length * 1000, 2), "stand": round(_E1.length * 1000, 2)},
                       "rule": "knee = ankle + L2 tipped psi(g) = PSI_MAX sin(pi g) forward; hip = knee + L1 at elevation "
                               "lerp(seated, 90 deg, g); endpoint residuals blended out linearly"}


def stand_up_P(ts):
    """Seated 2.7 m -> standing 5.4 m, 5.0 s, feet planted the whole way.
    0.0-1.0 stir: crown lifts, chest swells, knuckles press.   0.7-2.6 load: the trunk pitches 16 deg onto the
    knuckles.   1.3-2.15 STRAIN: the sculpted base is rooted - it lifts 0.10 m with a 7 Hz tremble, then TEARS
    FREE (a 0.06 m jerk at 2.1 s).   1.6-4.3 the big rise along the knee-driven path: the hips climb behind the
    feet, then arc forward over the pads as the legs lock straight; knuckles push until 2.3 s, peel 2.1-2.7, swing
    to hang.   4.2-5.0 settle: the mass lands on the columns - knees give 5 cm and recover, the lean rocks back
    2 deg, crown nods - into idle frame 1."""
    g = s01((ts - 1.6) / 2.7)
    P = blendP(S0, I0, g)
    strain = win(ts, 1.3, 1.9) * (1 - g)
    trem = bump(ts, 1.75, 0.45)
    tear = win(ts, 2.02, 2.16) * (1 - g)
    lean = 16.0 * win(ts, 0.7, 2.0) * (1 - win(ts, 2.6, 4.0)) - 2.0 * bump(ts, 4.4, 0.4)
    P["pelvis"] = v3add(P["pelvis"], (lean, 1.2 * math.sin(TAU * 6.0 * ts) * trem, 0.0))
    hips_to(P, hip_path(g))
    P["lift"] += (0.10 * strain + 0.06 * tear) * (1 - g) + 0.008 * math.sin(TAU * 7.0 * ts) * trem - 0.05 * bump(ts, 4.45, 0.35)
    P["chest"] = v3add(P["chest"], (3.5 * bump(ts, 0.5, 0.5) + 5.0 * bump(ts, 1.8, 0.5) - 3.5 * bump(ts, 3.9, 0.5), 0.0, 0.0))
    P["crown"] = v3add(P["crown"], (-4.0 * bump(ts, 0.45, 0.45) - 7.0 * bump(ts, 4.2, 0.55) + 1.5 * math.sin(TAU * 6.5 * ts) * trem + 3.0 * bump(ts, 2.1, 0.12), 0.0, 0.0))
    peel = s01((ts - 2.1) / 0.6)
    for s in "LR":
        P["arms"][s]["plant"] = 1.0 - s01((ts - 2.3) / 0.5)
        P["arms"][s]["peel"] = peel
        P["arms"][s]["rot"] = lerp((0, 0, 0), I0["arms"][s]["rot"], s01((ts - 2.6) / 1.5))
        P["arms"][s]["fore"] = lerp((0, 0, 0), I0["arms"][s]["fore"], s01((ts - 2.6) / 1.5))
    return reach_clamp(P)


def sit_down_P(ts):
    """Standing -> seated, 4.5 s, feet planted. 0-0.7 the crown dips, weight settles. 0.5-3.5 the hips run the
    rise path backwards: they arc back off the pads and sink behind the feet (knees tip forward <= 24 deg); the
    trunk pitches 10 deg forward to counter the mass, upright again before touch-down. 2.6-3.5 knuckles reach down
    and plant. 3.5 touch-down: the base lands with a thud (chest + crown shudder), 3.7-4.5 settles into
    sitting_idle frame 1."""
    g = 1.0 - s01((ts - 0.5) / 3.0)
    P = blendP(I0, S0, 1.0 - g)
    lean = 10.0 * win(ts, 0.5, 1.4) * (1 - win(ts, 2.4, 3.3))
    P["pelvis"] = v3add(P["pelvis"], (lean, 0.0, 0.0))
    hips_to(P, hip_path(g))
    thud = bump(ts, 3.62, 0.22)
    P["lift"] += -0.008 * thud                        # the seated base clears the floor by 10.6 mm
    P["chest"] = v3add(P["chest"], (2.5 * bump(ts, 2.0, 0.7) - 2.5 * thud, 0.0, 0.0))
    P["crown"] = v3add(P["crown"], (3.0 * bump(ts, 0.4, 0.4) + 4.0 * thud + 2.0 * bump(ts, 4.0, 0.35), 0.0, 0.0))
    reach = s01((ts - 2.6) / 0.9)
    for s in "LR":
        P["arms"][s]["plant"] = s01((ts - 2.3) / 0.5)
        P["arms"][s]["peel"] = 1.0 - reach
        P["arms"][s]["rot"] = lerp(I0["arms"][s]["rot"], (0, 0, 0), s01((ts - 1.5) / 1.5))
        P["arms"][s]["fore"] = lerp(I0["arms"][s]["fore"], (0, 0, 0), s01((ts - 1.5) / 1.5))
    return reach_clamp(P)


WALK = dict(S=0.70, duty=0.75, lift=0.18, offsets={"L": 0.75, "R": 0.25}, arm_swing=9.0, v=0.14)
WALK["N"] = int(round(WALK["S"] * FPS / (WALK["duty"] * WALK["v"])))


def gait_target(F0_, phase, duty, S, lift):
    if phase < duty:
        u_ = phase / duty
        return (F0_[0], F0_[1] - S / 2 + S * u_, F0_[2])
    u_ = (phase - duty) / (1.0 - duty)
    m = S * (1.0 - duty) / duty
    h00, h10 = 2 * u_ ** 3 - 3 * u_ ** 2 + 1, u_ ** 3 - 2 * u_ ** 2 + u_
    h01, h11 = -2 * u_ ** 3 + 3 * u_ ** 2, u_ ** 3 - u_ ** 2
    y = h00 * (S / 2) + h10 * m + h01 * (-S / 2) + h11 * m
    return (F0_[0], F0_[1] + y, F0_[2] + lift * math.sin(math.pi * u_))


def walk_P(t):
    """Two-beat lumber at full height: L swing [0,.25), double support, R swing [.5,.75), double support.
    Hips ride 7 cm low (knees ~17 deg), dip as each foot lands, roll + shift over the stance foot; arms
    swing contralaterally, forearms lag."""
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
    P = {"lift": -0.07 - 0.03 * dip + 0.012 * effort, "px": 0.045 * lean(t - 0.03),
         "pelvis": (2.0 + 1.5 * dip, 2.2 * lw, 1.5 * lw),
         "chest": (1.5 * pulse(t, 0.29) + 1.5 * pulse(t, 0.79), 1.5 * lean(t - 0.06), -0.8 * lw),
         "crown": (2.0 * pulse(t, 0.35) + 2.0 * pulse(t, 0.85), 1.6 * lean(t - 0.12), 0.0),
         "feet": {}, "arms": {}}
    wk = WALK
    for s, off in wk["offsets"].items():
        P["feet"][s] = gait_target(tuple(ANKLE[s]), (t + off) % 1.0, wk["duty"], wk["S"], wk["lift"])
    A_ = wk["arm_swing"]
    P["arms"]["R"] = {"rot": (4.0 - A_ * cs(t, 1, -TAU * 0.125), 0.0, 0.0), "fore": (-6.0 - 0.45 * A_ * cs(t, 1, -TAU * 0.20), 0, 0)}
    P["arms"]["L"] = {"rot": (4.0 + A_ * cs(t, 1, -TAU * 0.125), 0.0, 0.0), "fore": (-6.0 + 0.45 * A_ * cs(t, 1, -TAU * 0.20), 0, 0)}
    return P


# proof: sitting = the sculpt's trunk exactly (rigid), measured on the evaluated mesh (no breathing)
_Gs = solve({"lift": -Z_B, "py": DY_SIT, "arms": {s: {"plant": 1.0} for s in "LR"}})
# ---- v3: search the sit-state skirt pose on the sitting pose itself (legs rigid -> posed by their bone matrices):
# minimise the plate's worst penetration into the folded thighs (+ half-weight the shins) + 2x any top-rim exposure
# outside the trunk; the floor-ride lift is recomputed per candidate. Transitions blend it by the thigh fold.
t_ss = time.time()


def local_set_v(face_mask):
    Fm = FL[face_mask]
    vid = np.unique(Fm)
    remap = -np.ones(len(VL), dtype=np.int64); remap[vid] = np.arange(len(vid))
    return vid, remap[Fm]


def _posed(idx, M4):
    M_ = np.array(M4)
    return VL[idx] @ M_[:3, :3].T + M_[:3, 3]


_legsets = {}
for s in "LR":
    for part in ("thigh", "shin"):
        ls_ = local_set_v((fkind == part) & (fside == s))
        P_ = _posed(ls_[0], _Gs[part + "." + s])
        _legsets[part + "." + s] = (BVHTree.FromPolygons(P_.tolist(), ls_[1].tolist()), np.vstack([P_, P_[ls_[1]].mean(1)]))
_sk_ls = local_set_v(fkind == "skirt")
_Mp_s = np.array(_Gs["pelvis"])
_rest_trunk_bvh = BVHTree.FromPolygons(VL.tolist(), FL[np.isin(fkind, ("trunk", "membrane"))].tolist())
apply(_Gs)
_btr_s = BVHTree.FromPolygons(mesh_coords().tolist(), FL[np.isin(fkind, ("trunk", "membrane"))].tolist())   # posed trunk


def _hide_sit(p):
    return inside_parity(_btr_s, p)


_RAYD = Vector((0.0123, 0.0171, 0.99977)).normalized()        # skewed: never parallel to a ring or a strip


def _parity(bvh, p, d):
    cnt, o = 0, Vector(p)
    for _ in range(64):
        hit = bvh.ray_cast(o, d, 50.0)
        if hit[0] is None:
            break
        cnt += 1; o = hit[0] + d * 1e-5
    return cnt % 2 == 1


_RAYS3 = [_RAYD, Vector((0.61, -0.43, 0.664)).normalized(), Vector((-0.52, 0.37, -0.77)).normalized()]


def inside_parity(bvh, p):
    """Robust point-in-closed-shell test: odd number of crossings along a skewed ray (the nearest-face normal test
    alone misfires near teeth tips, poles and fold rims - it reported 1.3 m 'depths' on the first sit search).
    v4: majority of three skewed rays - the posed trunk self-intersects where a swinging arm root folds into the
    chest, and a single ray through that fold flips (first v4 walk: a plate 'inside the trunk' 546 mm deep)."""
    a_ = _parity(bvh, p, _RAYS3[0]); b_ = _parity(bvh, p, _RAYS3[1])
    if a_ == b_:
        return a_
    return _parity(bvh, p, _RAYS3[2])


def pen_depth(points, bvh, hide=None):
    """(worst depth of points inside the shell, worst depth among those NOT hidden inside the trunk). hide(p) -> True
    when p lies inside the trunk body (an overlap there cannot be seen)."""
    w_all, w_vis = 0.0, 0.0
    for p in points:
        loc, nrm, _, d = bvh.find_nearest(Vector(p))
        if loc is None or d <= w_vis and d <= w_all:
            continue
        if (Vector(p) - loc).dot(nrm) < 0 or d < 0.05:
            if inside_parity(bvh, p):
                w_all = max(w_all, d)
                if hide is None or not hide(p):
                    w_vis = max(w_vis, d)
    return w_all, w_vis


def _depth(points, bvh):
    return pen_depth(points, bvh)[0]


def _sit_cost(beta, back):
    Lm = np.array(skirt_local(beta, back))
    Ps = VL[_sk_ls[0]] @ Lm[:3, :3].T + Lm[:3, 3]                 # pelvis-rest frame
    Pw = Ps @ _Mp_s[:3, :3].T + _Mp_s[:3, 3]
    lift_ = max(0.0, SK_FLOOR - float(Pw[:, 2].min()))
    Pw = Pw + np.array([0.0, 0.0, lift_])
    bsk = BVHTree.FromPolygons(Pw.tolist(), _sk_ls[1].tolist())
    probes_ = np.vstack([Pw, Pw[_sk_ls[1]].mean(1)])
    pa, pv = [], []
    for s in "LR":
        for a_, b_ in (pen_depth(probes_, _legsets["thigh." + s][0], _hide_sit), pen_depth(_legsets["thigh." + s][1], bsk, _hide_sit)):
            pa.append(a_); pv.append(b_)
    pth_all, pth = max(pa), max(pv)
    psh = max(pen_depth(probes_, _legsets["shin." + s][0], _hide_sit)[1] for s in "LR")
    rim = (VL[SK_TOP_IDX] @ Lm[:3, :3].T + Lm[:3, 3]) @ _Mp_s[:3, :3].T + _Mp_s[:3, 3] + np.array([0.0, 0.0, lift_])
    ex_ = 0.0
    for q in rim:
        if not inside_parity(_btr_s, q):
            ex_ = max(ex_, _btr_s.find_nearest(Vector(q))[3])
    return pth + 0.5 * psh + 2.0 * ex_, {"pitch_deg": beta, "back_m": back, "lift_m": round(lift_, 4), "top_z_m": round(float(Pw[:, 2].max()), 3),
                                          "thigh_pen_visible_mm": round(pth * 1000, 1), "thigh_pen_incl_inside_trunk_mm": round(pth_all * 1000, 1),
                                          "shin_pen_visible_mm": round(psh * 1000, 1), "top_rim_exposure_mm": round(ex_ * 1000, 1)}


_grid = []
for beta_ in (-50.0, -40.0, -30.0, -25.0, -20.0, -15.0, -10.0, -5.0, 0.0, 10.0, 20.0, 30.0):
    for back_ in (0.0, 0.1, 0.2):         # >= 0.3 rearward pushes the plate out behind the trunk (a 'backpack' on the first sit render)
        _grid.append(_sit_cost(beta_, back_))
# the plate may only tip its FRONT up (a lap over the folded thighs) and stay on the hip line: front-down / rearward
# candidates lift its back half out behind the trunk like a shell (second sit render) - searched for the report only
# ... and its top must stay under the seated mouth (standing_front: mouth bottom z 4.34 -> seated 1.63; -50 deg put a
# bark bib over the mouth on the third sit render), 0.18 m margin
SK_SIT_TOPZ = 1.45
_ok = [g for g in _grid if g[1]["pitch_deg"] <= 0.0 and g[1]["back_m"] == 0.0 and g[1]["top_z_m"] <= SK_SIT_TOPZ]
_best = min(_ok, key=lambda x: x[0])     # ties -> grid order (the most front-up pitch under the mouth cap: v4 measured
                                         # pitch 0 / -10 / -15 / -20: back shell into the thighs 22 / 0 / 0 / 0 mm, worst
                                         # visible plate contact in stand_up 27 / 27 / 19 / 18 mm)
if "--sit-pitch" in argv:
    _best = _sit_cost(opt("--sit-pitch", 0.0), 0.0)
SK_SIT.update({"pitch": _best[1]["pitch_deg"], "back": _best[1]["back_m"]})
report["skirt"]["sit_state_search"] = {"chosen": _best[1], "cost": round(_best[0], 4),
                                       "rigid_with_pelvis_lifted_only": [g[1] for g in _grid if g[1]["pitch_deg"] == 0 and g[1]["back_m"] == 0][0],
                                       "grid": [g[1] for g in _grid], "seconds": round(time.time() - t_ss, 1),
                                       "rule": "cost = thigh penetration + 0.5 shin penetration + 2 x top-rim exposure (m), "
                                               "on sitting_idle frame 1; transitions blend by smoothstep(40, 120 deg thigh fold)"}
# ---- v4 TASSET plate sit targets. Fast clearance model (the exact BVH measurement runs on every keyed frame in
# section 12): each rigid part is a radial table r_max(t, theta) about an axis in its REST frame (thigh/shin: the
# bone; foot: the vertical through the pad; upperarm/forearm: the bone, over the trunk verts they dominate), dilated
# one bin each way (conservative); the trunk body is an outermost-radius table about the skirt axis in the pelvis
# frame. A plate point is 'in' a part when its radius is under the table's; points already inside the trunk body do
# not count for legs/arms (hidden). Floor: z >= SK_FLOOR. The plate top (w > 0.85: belt + buried rings) is exempt from
# the trunk test (it enters the trunk at the belt by design).
t_ts = time.time()


def radial_table(P, head, tail, dt=0.03, nt=36):
    e = (tail - head) / np.linalg.norm(tail - head)
    e1 = np.cross(e, [0.0, 0.0, 1.0]) if abs(e[2]) < 0.9 else np.cross(e, [1.0, 0.0, 0.0])
    e1 /= np.linalg.norm(e1); e2 = np.cross(e, e1)
    q = P - head; t = q @ e; rad = q - t[:, None] * e
    rho = np.linalg.norm(rad, axis=1); th = np.arctan2(rad @ e2, rad @ e1) % TAU
    t0 = float(t.min()) - dt; nb = int(math.ceil((float(t.max()) + dt - t0) / dt)) + 1
    tab = np.zeros((nb, nt))
    np.maximum.at(tab, (np.floor((t - t0) / dt).astype(int), np.floor(th / (TAU / nt)).astype(int) % nt), rho)
    tab = np.maximum.reduce([tab, np.roll(tab, 1, 1), np.roll(tab, -1, 1)])
    tab = np.maximum.reduce([tab, np.pad(tab, ((1, 0), (0, 0)))[:-1], np.pad(tab, ((0, 1), (0, 0)))[1:]])
    return {"h": head, "e": e, "e1": e1, "e2": e2, "t0": t0, "dt": dt, "nt": nt, "tab": tab}


def table_depth(tb, Q):
    q = Q - tb["h"]; t = q @ tb["e"]; rad = q - t[:, None] * tb["e"]
    rho = np.linalg.norm(rad, axis=1); th = np.arctan2(rad @ tb["e2"], rad @ tb["e1"]) % TAU
    ti = np.floor((t - tb["t0"]) / tb["dt"]).astype(int); thi = np.floor(th / (TAU / tb["nt"])).astype(int) % tb["nt"]
    ok = (ti >= 0) & (ti < len(tb["tab"]))
    r = np.where(ok, tb["tab"][np.clip(ti, 0, len(tb["tab"]) - 1), thi], 0.0)
    return np.where(r > 0, r - rho, -1.0)


PART_TB = {}
_dom = np.argmax(Wn, 1)
for s in "LR":
    for part in ("thigh", "shin"):
        nmp = part + "." + s
        PART_TB[nmp] = radial_table(VL[vlabel == nmp], np.array(RH[nmp]), np.array(RT[nmp]))
    A_ = np.array(RH["foot." + s])
    PART_TB["foot." + s] = radial_table(VL[vlabel == "foot." + s], np.array([A_[0], A_[1] - 0.075, 0.0]), np.array([A_[0], A_[1] - 0.075, 0.8]))
    for nmp in ("upperarm." + s, "forearm." + s):
        m_ = (np.arange(len(VL)) < ntv) & (_dom == dc[nmp])
        PART_TB[nmp] = radial_table(VL[m_], np.array(RH[nmp]), np.array(RT[nmp]))
_zt = np.arange(Z_BASE - 0.4, Z_BASE + 2.75, 0.02); _pt = -math.pi / 2 + np.arange(72) * TAU / 72
TRUNK_TB = np.stack([r_trunk(_pt, z) for z in _zt])          # (nz, 72) outermost trunk-body radius, pelvis rest frame


def trunk_depth(Qp):
    """Qp: points in the pelvis REST frame -> radial depth inside the trunk body's outer envelope (<0 outside)."""
    zi = np.clip(np.round((Qp[:, 2] - _zt[0]) / 0.02).astype(int), 0, len(_zt) - 1)
    ph = np.arctan2(Qp[:, 1] - YC, Qp[:, 0])
    pj = np.round((ph - _pt[0]) / (TAU / 72)).astype(int) % 72
    r = TRUNK_TB[zi, pj]
    inz = (Qp[:, 2] >= _zt[0]) & (Qp[:, 2] <= _zt[-1])
    return np.where(inz & (r > 0), r - np.hypot(Qp[:, 0], Qp[:, 1] - YC), -1.0)


def plate_probes(nm):
    idx = PIECE_IDX[nm]
    Fm = FL[np.isin(FL[:, 0], idx) & (vlabel[FL[:, 0]] == nm)]
    P_ = np.vstack([VL[idx], VL[Fm].mean(1)])
    w_ = np.concatenate([VW_SK[idx], VW_SK[Fm].mean(1)])
    return P_, w_


PROBES = {nm_: plate_probes(nm_) for nm_ in TASSETS}
LEG_ARM_PARTS = [p_ + "." + s for s in "LR" for p_ in ("thigh", "shin", "foot", "upperarm", "forearm")]


TS_TOPZ_P = None                                           # plate ceiling in the pelvis rest frame (set below)


def plate_cost(nm, Rs, G, detail=False, lifts=None):
    """Rs: (k, 3, 3) candidate rotations about the plate's hinge (pelvis rest frame), lifts: (k,) belt ride-up (m);
    G: the frame's bone matrices. Returns per-candidate cost (m; 0 = clear by TS_MARGIN) [+ the worst part]."""
    P_, w_ = PROBES[nm]
    H_ = HINGE[nm]["p"]
    k = len(Rs)
    Qp = np.einsum("kij,nj->kni", Rs, P_ - H_) + H_
    if lifts is not None:
        Qp[:, :, 2] += np.asarray(lifts)[:, None]
    Qp = Qp.reshape(-1, 3)                                  # pelvis rest frame
    Mp = np.array(G["pelvis"])
    Qw = Qp @ Mp[:3, :3].T + Mp[:3, 3]
    td = trunk_depth(Qp)
    hidden = td > 0.02
    top = np.tile(w_ > 0.85, k)
    bury = np.tile(w_ > 1.0, k)
    worst = np.maximum(SK_FLOOR - Qw[:, 2], np.where(top, -1.0, td - 0.02))
    wpart = np.where(SK_FLOOR - Qw[:, 2] >= np.where(top, -1.0, td - 0.02), "floor", "trunk").astype("<U12")
    ex_ = np.where(bury, -td, -1.0)                         # buried belt rings must stay inside the trunk body
    wpart = np.where(ex_ > worst, "belt_rim", wpart); worst = np.maximum(worst, ex_)
    if TS_TOPZ_P is not None:                               # never up over the mouth (v3 sit render: a bark bib)
        cz = Qp[:, 2] - TS_TOPZ_P
        wpart = np.where(cz > worst, "mouth_cap", wpart); worst = np.maximum(worst, cz)
    for pn in LEG_ARM_PARTS:
        Gi = np.array(G[pn].inverted())
        d_ = table_depth(PART_TB[pn], Qw @ Gi[:3, :3].T + Gi[:3, 3]) + TS_MARGIN
        d_ = np.where(hidden, -1.0, d_)
        wpart = np.where(d_ > worst, pn, wpart); worst = np.maximum(worst, d_)
    worst = worst.reshape(k, -1); wpart = wpart.reshape(k, -1)
    cost = np.maximum(worst.max(1), 0.0)
    if detail:
        return cost, [wpart[i, int(np.argmax(worst[i]))] for i in range(k)]
    return cost


def rot_axis(axis, ang):
    return np.array(Matrix.Rotation(ang, 3, Vector(axis)))


_ALPHA = np.radians(np.arange(0.0, 176.0, 5.0)); _PSI = np.radians(np.arange(-60.0, 61.0, 10.0))
_LIFTS = np.array([0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9])
TS_TOPZ_P = SK_SIT_TOPZ + Z_B                                 # the seated mouth line (1.45 m sitting), pelvis rest frame
_Gsit = solve({"lift": -Z_B, "py": DY_SIT, "arms": {s: {"plant": 1.0} for s in "LR"}})
ts_search = {}
_TS_ORDER = ["tasset.L.0", "tasset.R.0", "tasset.L.1", "tasset.R.1", "tasset.L.2", "tasset.R.2"] if TS_MODE == "belt" else []
if TS_MODE == "thigh":
    # There is no visible seated place for the front arc: in the sitting pose the shins stand 5-12 cm in front of the
    # belly, the thighs leave the trunk ~0.3 m under the knee crowns, the feet fill the floor between the shins and the
    # knuckles the sides (belt-hinge search, 3276 candidates per plate incl. a 0-0.9 m belt ride-up: 0 clear, best
    # 54-272 mm into legs/floor/knuckles). So the fold carries the plates up about the hip line INTO the trunk, which
    # closes over them - the seated silhouette is the approved seated sculpt. Searched here: the carry cap and a
    # tuck translation (pelvis frame, blended in from 40 deg of swing) that leave the fewest plate points outside the
    # posed seated trunk body (exact: 3-ray parity; the outer-envelope table picked a tuck that pushed plates out
    # behind the head), then the worst outside distance, then the smallest motion.
    t_cs = time.time()
    _Pall = np.vstack([PROBES[nm_][0] for nm_ in TASSETS])
    _wall = np.concatenate([PROBES[nm_][1] for nm_ in TASSETS])
    _Mps = np.array(_Gsit["pelvis"])
    _pit = []
    for s in "LR":
        _d = np.array(_Gsit["thigh." + s].to_3x3() @ (RT["thigh." + s] - RH["thigh." + s]).normalized())
        _dl = _Mps[:3, :3].T @ _d
        _pit.append(math.degrees(math.atan2(-_dl[1], -_dl[2])))
    _pit_sit = float(np.mean(_pit))
    _grid_c = []
    _rhm = np.array(RHM_REST)
    _sub = np.arange(0, len(_Pall), 2)                     # every 2nd probe (verts + face centres interleave)
    for cap_ in np.arange(85.0, min(_pit_sit, 180.0) + 0.1, 5.0):
        R_ = np.array(Matrix.Rotation(-math.radians(cap_), 3, "X"))
        Q0 = (_Pall[_sub] - _rhm) @ R_.T + _rhm
        for dy_ in (0.0,):
            for dz_ in (0.0,):
                Qp_ = Q0 + np.array([0.0, dy_, dz_])
                Qw_ = Qp_ @ _Mps[:3, :3].T + _Mps[:3, 3]
                nbad, wbad = 0, 0.0
                for q_ in Qw_:
                    if q_[2] < SK_FLOOR or not inside_parity(_btr_s, q_):
                        nbad += 1
                        wbad = max(wbad, max(SK_FLOOR - q_[2], _btr_s.find_nearest(Vector(q_))[3]))
                _grid_c.append((nbad, wbad, cap_, dy_, dz_, abs(math.radians(cap_)) + 1.5 * math.hypot(dy_, dz_)))
    _grid_c.sort(key=lambda g_: (g_[0], round(g_[1], 3), g_[5]))
    _bc = _grid_c[0]
    TS_CARRY.update({"cap_deg": float(_bc[2])})
    # tucks at the chosen cap, solved JOINTLY along the plate chain L.2-L.1-L.0-R.0-R.1-R.2 (dynamic programming):
    # minimise the plate points left outside the posed seated trunk (exact parity) + TS_TUCK_LAMBDA x the relative
    # translation of neighbours (the tuck blends in while the plates are still partly outside; independent per-plate
    # tucks rubbed neighbours 18 mm visibly mid-transition, one tuck per side crossed the two centre plates 27 mm)
    _Rc = np.array(Matrix.Rotation(-math.radians(TS_CARRY["cap_deg"]), 3, "X"))
    CHAIN = ["tasset.L.2", "tasset.L.1", "tasset.L.0", "tasset.R.0", "tasset.R.1", "tasset.R.2"]
    _TG = np.array([(dx_, dy_, dz_) for dx_ in (-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3) for dy_ in (-0.1, 0.0, 0.1, 0.2)
                    for dz_ in (0.0, 0.1, 0.2, 0.3)])
    _cnt = {}
    for nm_ in CHAIN:
        P_ = PROBES[nm_][0][::2]
        Q0 = ((P_ - _rhm) @ _Rc.T + _rhm) @ _Mps[:3, :3].T + _Mps[:3, 3]
        row = np.zeros(len(_TG))
        for ti_, T_ in enumerate(_TG):
            nb_ = 0
            for q_ in Q0 + T_:
                if q_[2] < SK_FLOOR or not inside_parity(_btr_s, q_):
                    nb_ += 1
            row[ti_] = nb_
        _cnt[nm_] = row
    DT_ = np.linalg.norm(_TG[:, None, :] - _TG[None, :, :], axis=2)
    cost_ = _cnt[CHAIN[0]] + 1e-3 * np.linalg.norm(_TG, axis=1)
    back_ = []
    for nm_ in CHAIN[1:]:
        tot_ = cost_[:, None] + TS_TUCK_LAMBDA * DT_                  # prev state x this state
        arg_ = np.argmin(tot_, axis=0)
        cost_ = tot_[arg_, np.arange(len(_TG))] + _cnt[nm_] + 1e-3 * np.linalg.norm(_TG, axis=1)
        back_.append(arg_)
    st_ = [int(np.argmin(cost_))]
    for arg_ in reversed(back_):
        st_.append(int(arg_[st_[-1]]))
    st_ = st_[::-1]
    tuck_rep = {}
    for nm_, k_ in zip(CHAIN, st_):
        TS_TUCK[nm_] = tuple(float(x_) for x_ in _TG[k_])
        tuck_rep[nm_] = {"tuck_m": [round(float(x_), 3) for x_ in _TG[k_]], "points_outside": int(_cnt[nm_][k_]),
                         "of": int(len(PROBES[nm_][0][::2])), "points_outside_untucked": int(_cnt[nm_][int(np.argmin(np.linalg.norm(_TG, axis=1)))])}
    ts_search["carry"] = {"sit_swing_deg": round(_pit_sit, 2), "cap_deg": _bc[2], "tuck_chain_dp": tuck_rep, "tuck_lambda_per_m": TS_TUCK_LAMBDA,
                          "tuck_from_swing_deg": TS_CARRY["tuck_from_deg"],
                          "plate_probe_points_tested": int(len(_sub)), "points_outside_trunk_or_under_floor": _bc[0],
                          "worst_outside_m": round(_bc[1], 4),
                          "full_swing_untucked": [g_ for g_ in _grid_c if g_[2] == max(x_[2] for x_ in _grid_c) and g_[3] == 0.0 and g_[4] == 0.0][0][:2],
                          "best5": [list(g_[:5]) for g_ in _grid_c[:5]], "candidates": len(_grid_c), "seconds": round(time.time() - t_cs, 1)}
placed = []                                                  # posed BVHs of the plates already placed (+ back shell)
apply(_Gsit); _Csit = mesh_coords()
_bs = local_set_v(fkind == "skirt")
placed.append(("skirt", BVHTree.FromPolygons(_Csit[_bs[0]].tolist(), _bs[1].tolist())))
for nm_ in _TS_ORDER:
    ax_ = HINGE[nm_]["axis"]
    cands = [(a_, p_, np.array(Matrix.Rotation(p_, 3, "Z")) @ rot_axis(ax_, a_), l_) for l_ in _LIFTS for p_ in _PSI for a_ in _ALPHA]
    Rs = np.array([c_[2] for c_ in cands])
    cost, wp = plate_cost(nm_, Rs, _Gsit, detail=True, lifts=np.array([c_[3] for c_ in cands]))
    ang = np.array([Quaternion(Matrix(c_[2].tolist()).to_quaternion()).angle for c_ in cands])
    pref = ang + 1.5 * np.array([c_[3] for c_ in cands])          # smallest motion: rotation (rad) + 1.5 x lift (m)
    order = np.lexsort((pref, np.round(cost, 4)))
    ls_ = local_set_v(fkind == "tasset")
    pid = PIECE_IDX[nm_]
    Fp = FL[(fkind == "tasset") & (vlabel[FL[:, 0]] == nm_)]
    remap_ = -np.ones(len(VL), dtype=np.int64); remap_[pid] = np.arange(len(pid))
    Mp_ = np.array(_Gsit["pelvis"]); H_ = HINGE[nm_]["p"]
    chosen, tried = None, 0
    for oi_ in order[:80]:
        tried += 1
        Pw_ = ((VL[pid] - H_) @ cands[oi_][2].T + H_ + np.array([0.0, 0.0, cands[oi_][3]])) @ Mp_[:3, :3].T + Mp_[:3, 3]
        b_ = BVHTree.FromPolygons(Pw_.tolist(), remap_[Fp].tolist())
        hits = [n_ for n_, bb in placed if b_.overlap(bb)]
        if not hits:
            chosen = oi_; break
    if chosen is None:
        chosen = order[0]
    a_, p_, R_, l_ = cands[chosen]
    TS_SIT[nm_] = Matrix(R_.tolist()).to_quaternion(); TS_LIFT[nm_] = float(l_)
    Pw_ = ((VL[pid] - H_) @ R_.T + H_ + np.array([0.0, 0.0, l_])) @ Mp_[:3, :3].T + Mp_[:3, 3]
    placed.append((nm_, BVHTree.FromPolygons(Pw_.tolist(), remap_[Fp].tolist())))
    ts_search[nm_] = {"alpha_deg": round(math.degrees(a_), 1), "psi_deg": round(math.degrees(p_), 1), "belt_lift_m": round(float(l_), 3),
                      "rotation_deg": round(math.degrees(ang[chosen]), 1), "cost_mm": round(float(cost[chosen]) * 1000, 1),
                      "binding_part": wp[chosen], "clear_candidates": int((cost <= 1e-9).sum()), "candidates": len(cands),
                      "overlap_rejections": tried - 1, "hem_centre_sit_z_m": None,
                      "min_cost_mm_by_lift": {str(l_v): round(float(cost[np.array([c_[3] for c_ in cands]) == l_v].min()) * 1000, 1) for l_v in _LIFTS},
                      "binding_parts_of_best_20": sorted(set(wp[i_] for i_ in order[:20]))}
    hc_ = ((HINGE[nm_]["hem_centre"] - H_) @ R_.T + H_ + np.array([0.0, 0.0, l_])) @ Mp_[:3, :3].T + Mp_[:3, 3]
    ts_search[nm_]["hem_centre_sit_z_m"] = round(float(hc_[2]), 3)
report["tassets"] = {"mode": TS_MODE, "sit_targets": ts_search, "seconds_search": round(time.time() - t_ts, 1),
                     "rule": "per plate, on sitting_idle frame 1: belt ride-up 0-0.9 m (0.15) + rotation Rz(psi) R_hinge(alpha) about its belt hinge, alpha 0-175 "
                             "(5 deg), psi -60..60 (10 deg); ceiling = the seated mouth line; buried belt rings stay inside the trunk; cost = worst radial-table depth + %.0f mm margin into thighs, shins, "
                             "feet, upper/fore arms (visible points only), the trunk body (below the belt), the floor; the "
                             "clear candidate with the smallest motion (rotation rad + 1.5 x lift m) wins; exact BVH overlap vs plates already placed "
                             "(and the lifted back shell) rejects candidates" % (TS_MARGIN * 1000),
                     "fold_window_deg": list(TS_FOLD)}
# precomputed rest -> sit path rotations for the clearance-driven keying
U_GRID = np.linspace(0.0, 1.0, 41)
U_ROTS = {nm_: np.array([np.array(Quaternion().slerp(TS_SIT[nm_], u_).to_matrix()) for u_ in U_GRID]) for nm_ in TASSETS}
U_LIFTS = {nm_: U_GRID * TS_LIFT[nm_] for nm_ in TASSETS}

_Gs = solve({"lift": -Z_B, "py": DY_SIT, "arms": {s: {"plant": 1.0} for s in "LR"}})
apply(_Gs)
Cs = mesh_coords()
_Mp = np.array(_Gs["pelvis"])
report["skirt"]["sit_if_rigid_with_pelvis"] = {
    "hem_min_z_m": round(float((SKV_lo @ _Mp[2, :3] + _Mp[2, 3]).min()), 4),
    "note": "why the skirt has its own bone: glued to the pelvis the plate would sit this far under the floor in sitting_idle"}
trunk_orig_rest = W0[TOI] + np.array([0.0, DY_SIT, 0.0]) + S_RC
report["sit_pose"] = {"trunk_vs_sculpt_max_mm": round(float(np.linalg.norm(Cs[:ntv] - trunk_orig_rest, axis=1).max()) * 1000, 4)}
# legs: seated shells vs the sculpted columns (both directions, nearest-surface)
legF0 = F0[legdom[F0].all(1)]
sc_off = np.array([0.0, DY_SIT, 0.0]) + S_RC
bvh_orig_leg = BVHTree.FromPolygons((W0 + sc_off).tolist(), legF0.tolist())
shellv = np.isin(vlabel, [f"{p}.{s}" for p in ("thigh", "shin", "foot") for s in "LR"])
shellF = FL[legf]
bvh_new_leg = BVHTree.FromPolygons(Cs.tolist(), shellF.tolist())
d_on = np.array([(Vector(p) - bvh_new_leg.find_nearest(Vector(p))[0]).length for p in (W0[legdom] + sc_off)])
# only the shell surface OUTSIDE the trunk is visible; measure the new->sculpt direction on visible shell verts
bvh_trunk_sit = BVHTree.FromPolygons(Cs.tolist(), FL[np.isin(fkind, ("trunk", "membrane"))].tolist())
vis = []
for i in np.nonzero(shellv)[0]:
    p = Vector(Cs[i])
    hit = bvh_trunk_sit.ray_cast(p, Vector((0.0, 0.0, 1.0)), 10.0)
    cnt = 0; q = p
    while hit[0] is not None and cnt < 20:
        cnt += 1; q = hit[0] + Vector((0, 0, 1e-5)); hit = bvh_trunk_sit.ray_cast(q, Vector((0.0, 0.0, 1.0)), 10.0)
    vis.append(cnt % 2 == 0)
vis = np.array(vis)
sv_idx = np.nonzero(shellv)[0][vis]
d_no = np.array([(Vector(Cs[i]) - bvh_orig_leg.find_nearest(Vector(Cs[i]))[0]).length for i in sv_idx])
knee_top = {}
for s in "LR":
    ms = vlabel == "shin." + s
    knee_top[s] = round(float(Cs[ms, 2].max()), 4)
report["sit_pose"].update({
    "sculpt_leg_surface_to_new_legs_mm": {"mean": round(float(d_on.mean()) * 1000, 1), "p95": round(float(np.percentile(d_on, 95)) * 1000, 1), "max": round(float(d_on.max()) * 1000, 1)},
    "visible_new_leg_surface_to_sculpt_mm": {"verts": int(len(sv_idx)), "mean": round(float(d_no.mean()) * 1000, 1), "p95": round(float(np.percentile(d_no, 95)) * 1000, 1)},
    "knee_cap_top_z_m": knee_top, "sculpt_knee_cap_top_z_m": round(ZTOP, 4),
    "seated_height_m": round(float(Cs[:, 2].max() - Cs[:, 2].min()), 4)})

CLIPS = [
    ("sitting_idle", "loop", 144, lambda f, N: sit_P((f - 1) / N % 1.0)),
    ("stand_up", "oneshot", int(round(STAND_S * FPS)), lambda f, N: stand_up_P((f - 1) / FPS)),
    ("idle", "loop", 96, lambda f, N: idle_P((f - 1) / N % 1.0)),
    ("sit_down", "oneshot", int(round(SIT_S * FPS)), lambda f, N: sit_down_P((f - 1) / FPS)),
    ("walk", "loop", WALK["N"], lambda f, N: walk_P((f - 1) / N % 1.0)),
]
clips = {}
TS_KEY = {}
for name, kind, N, fn in CLIPS:
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    # v4 pass 1 (transitions only): per frame and plate, u = the first path fraction >= its fold-driven value whose
    # plate clears legs / knuckles / trunk / floor (fast tables); then a +-3-frame max filter + gaussian (sigma 1.5)
    # smoothing (never below the raw need), ends pinned to the neighbouring states (sit: 1, stand: 0) so the joins hold
    # exactly. Loops are locked: sitting_idle at the sit target, idle/walk at rest (the v3 skirt clearance was solved
    # for their thigh sweep; the exact measurement below checks it).
    U = {nm_: np.zeros(N + 1) for nm_ in TASSETS}
    ts_info = {}
    if TS_MODE == "thigh":
        U = None
    elif name == "sitting_idle":
        U = {nm_: np.ones(N + 1) for nm_ in TASSETS}
    elif kind == "oneshot":
        raw = {nm_: np.zeros(N + 1) for nm_ in TASSETS}
        pushed = {nm_: 0 for nm_ in TASSETS}
        uncl = {nm_: 0.0 for nm_ in TASSETS}
        for f in range(1, N + 2):
            fold_log.clear()
            G_ = solve(fn(f, N))
            th_ = fold_log[-1]
            for nm_ in TASSETS:
                uf = float(smoothstep(TS_FOLD[0], TS_FOLD[1], th_[PIECE_LO[nm_]["side"]]))
                c_ = plate_cost(nm_, U_ROTS[nm_], G_, lifts=U_LIFTS[nm_])
                ok_ = np.nonzero((U_GRID >= uf - 1e-9) & (c_ <= 1e-9))[0]
                if len(ok_) and U_GRID[ok_[0]] - uf < 1.0 / 40:
                    u_ = uf                                   # the fold-driven pose (or its grid neighbour) is clear
                elif len(ok_):
                    u_ = float(U_GRID[ok_[0]]); pushed[nm_] += 1
                else:
                    sel = np.nonzero(U_GRID >= uf - 1e-9)[0]
                    u_ = float(U_GRID[sel[int(np.argmin(c_[sel]))]]); pushed[nm_] += 1
                    uncl[nm_] = max(uncl[nm_], float(c_[sel].min()))
                raw[nm_][f - 1] = u_
        k_ = np.exp(-0.5 * (np.arange(-4, 5) / 1.5) ** 2); k_ /= k_.sum()
        first_u, last_u = (1.0, 0.0) if name == "stand_up" else (0.0, 1.0)
        for nm_ in TASSETS:
            r_ = raw[nm_].copy(); r_[0], r_[-1] = first_u, last_u
            mx = np.array([r_[max(0, i - 3):i + 4].max() for i in range(len(r_))])
            sm = np.convolve(np.pad(mx, 4, mode="edge"), k_, mode="valid")
            u_ = np.clip(np.maximum(sm, r_), 0.0, 1.0)
            u_[0], u_[-1] = first_u, last_u
            U[nm_] = u_
            ts_info[nm_] = {"frames_pushed_past_fold": pushed[nm_], "unclearable_fast_cost_mm": round(uncl[nm_] * 1000, 1),
                            "u_min_max": [round(float(u_.min()), 3), round(float(u_.max()), 3)]}
    TS_KEY[name] = U
    reach_log.clear(); clamp_log["frames"] = 0; skirt_log.clear(); fold_log.clear(); tasset_log.clear()
    prevq = {}
    for f in range(1, N + 2):
        P_ = fn(f, N)
        if U is not None:
            P_["tasset_u"] = {nm_: float(U[nm_][f - 1]) for nm_ in TASSETS}
        apply(solve(P_))
        key_all(f, prevq)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = kind == "loop"
    clips[name] = {"action": name, "kind": kind, "frames": N + 1,
                   ("cycle_frames" if kind == "loop" else "duration_frames"): N, "seconds": round(N / FPS, 4),
                   "ik_reach_max": {k: round(max(v), 4) for k, v in reach_log.items()},
                   "reach_clamped_frames": clamp_log["frames"],
                   "tasset_keying": ("thigh-carried, fold window %s deg" % list(TS_FOLD)) if TS_MODE == "thigh" else
                   (ts_info if ts_info else ("locked at the sit target" if name == "sitting_idle" else "locked at rest")),
                   "thigh_fold_deg_max_LR": [round(max(x["L"] for x in fold_log), 2), round(max(x["R"] for x in fold_log), 2)],
                   "tasset_swing_deg_max": round(max(x_[2] for x_ in tasset_log), 2) if tasset_log else None,
                   "tasset_tuck_max": round(max(x_[3] for x_ in tasset_log), 3) if tasset_log else None,
                   "skirt_bone": {"thigh_fold_deg_max": round(max(x[0] for x in skirt_log), 2),
                                  "sit_pitch_deg_max_abs": round(max(abs(x[1]) for x in skirt_log), 2),
                                  "floor_ride_lift_m_max": round(max(x[2] for x in skirt_log), 4),
                                  "frames_moved": int(sum(1 for x in skirt_log if x[2] > 0 or x[1] < 0))}}
    if name == "walk":
        v = WALK["S"] / (WALK["duty"] * N) * FPS
        clips[name].update({"stride_m": WALK["S"], "duty": WALK["duty"], "swing_lift_m": WALK["lift"], "speed_m_per_s": round(v, 4),
                            "belt_m_per_frame": WALK["S"] / (WALK["duty"] * N)})

# ====================================================================== 11b. v4 cell refit
# The approved sculpt's scale was set by the 3.8 m 4-cell footprint (improve_unit cell budget, width binding). The
# wider shoulders / bigger knuckles break it; the same rule is applied again as a uniform refit of the finished asset:
# mesh, bone rest positions and every location key x S_FIT (rotations are scale-free), so every clip replays exactly,
# scaled. Everything measured below is the refit asset; sections 1-11 report the sculpt-scale build.
apply({})
_Cr = mesh_coords()
FP_PRE = float(max(np.ptp(_Cr[:, 0]), np.ptp(_Cr[:, 1])))
H_PRE = float(np.ptp(_Cr[:, 2]))
VL_PRE = VL.copy()
S_FIT = CELL_FP / FP_PRE if (CELL_REFIT and FP_PRE > CELL_FP) else 1.0
# ... and the XY recentre: v3's recentre (section 7) ran before the skirt existed; v4's plates + bigger knuckles moved
# the rest bbox centre 22 mm off in y (contract feet_origin allows 10 mm). Every non-root bone rest moves with the mesh,
# so the pelvis-down animation shifts rigidly and no key changes; the root stays at the origin.
_lo2, _hi2 = (VL * S_FIT).min(0), (VL * S_FIT).max(0)
SHIFT_XY = np.array([-(_lo2[0] + _hi2[0]) / 2, -(_lo2[1] + _hi2[1]) / 2, 0.0])
if S_FIT != 1.0 or np.abs(SHIFT_XY).max() > 1e-6:
    VL = VL * S_FIT + SHIFT_XY
    me.vertices.foreach_set("co", VL.ravel()); me.update()
    bpy.context.view_layer.objects.active = rig
    for o in scene.objects:
        o.select_set(o is rig)
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in arm_data.edit_bones:
        sh_ = Vector((0.0, 0.0, 0.0)) if eb.name == "root" else Vector(SHIFT_XY)
        eb.head = eb.head * S_FIT + sh_; eb.tail = eb.tail * S_FIT + sh_
    bpy.ops.object.mode_set(mode="OBJECT")
    n_keys = 0
    for act in bpy.data.actions:
        for fc in K.action_fcurves(act):
            if fc.data_path.endswith("location"):
                for kp in fc.keyframe_points:
                    kp.co.y *= S_FIT; kp.handle_left.y *= S_FIT; kp.handle_right.y *= S_FIT; n_keys += 1
                fc.update()
    for k_ in ("stride_m", "swing_lift_m", "speed_m_per_s", "belt_m_per_frame"):
        clips["walk"][k_] = clips["walk"][k_] * S_FIT
    for o in scene.objects:
        o.select_set(o is low)
    bpy.context.view_layer.objects.active = low
apply({})
_Cr2 = mesh_coords()
report["cell_refit"] = {"enabled": CELL_REFIT, "footprint_before_m": round(FP_PRE, 4), "standing_height_before_m": round(H_PRE, 4),
                        "cell_footprint_m": CELL_FP, "scale": round(S_FIT, 6),
                        "footprint_after_m": round(float(max(np.ptp(_Cr2[:, 0]), np.ptp(_Cr2[:, 1]))), 4),
                        "rest_mesh_vs_scaled_build_max_mm": round(float(np.linalg.norm(_Cr2 - VL, axis=1).max()) * 1000, 4),
                        "recentre_xy_after_skirt_m": SHIFT_XY[:2].round(5).tolist(),
                        "rule": "improve_unit cell budget kf = 3.8 / footprint, applied uniformly to mesh, bone rests and location keys"}

# ====================================================================== 12. measure
sole = {s: (vlabel == "foot." + s) & (VL[:, 2] < 0.001) for s in "LR"}
report["pad_sole_verts"] = {s: int(sole[s].sum()) for s in "LR"}


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


# ---- v3 skirt / crown contact measurement (evaluated mesh, every frame of every clip)
def local_set(face_mask):
    Fm = FL[face_mask]
    vid = np.unique(Fm)
    remap = -np.ones(len(VL), dtype=np.int64); remap[vid] = np.arange(len(vid))
    return vid, remap[Fm]


SKS = local_set(fkind == "skirt")
THS = {s: local_set((fkind == "thigh") & (fside == s)) for s in "LR"}
SHS = {s: local_set((fkind == "shin") & (fside == s)) for s in "LR"}
ARMS = local_set(np.isin(fkind, ("trunk",)) & arm_v[FL].any(1))
REST_TRUNK_BVH = BVHTree.FromPolygons(VL.tolist(), FL[np.isin(fkind, ("trunk", "membrane"))].tolist())
_band_trunk = np.nonzero((np.arange(len(VL)) < ntv_r) & ~arm_v & (VL[:, 2] > Z_BASE) & (VL[:, 2] < Z_ATT + 0.8))[0]


def bvh_of(C, ls):
    return BVHTree.FromPolygons(C[ls[0]].tolist(), ls[1].tolist())


def depth_inside(points, bvh):
    """Max depth of points inside a closed outward-wound shell (nearest-face normal test), metres."""
    worst = 0.0
    for p in points:
        loc, nrm, _, d = bvh.find_nearest(Vector(p))
        if loc is not None and (Vector(p) - loc).dot(nrm) < 0:
            worst = max(worst, d)
    return worst


def probes(C, ls):
    Fc = C[ls[0]][ls[1]].mean(1)
    return np.vstack([C[ls[0]], Fc])


TRUNK_F = FL[np.isin(fkind, ("trunk", "membrane"))]


def skirt_frame(C, Gp, want_gap, btr=None):
    out = {}
    bsk = bvh_of(C, SKS)
    pen, pen_vis, pairs = 0.0, 0.0, 0
    if btr is None:
        btr = BVHTree.FromPolygons(C.tolist(), TRUNK_F.tolist())      # the POSED trunk body (closed)

    def hide(p):                                  # inside the trunk body -> an overlap there cannot be seen
        return inside_parity(btr, p)
    for s in "LR":
        bth = bvh_of(C, THS[s])
        ov = bsk.overlap(bth)
        pairs += len(ov)
        if ov:
            th_f = np.unique([b for _, b in ov])
            ring = lambda ls, fs: np.vstack([C[ls[0]][np.unique(ls[1][fs])], C[ls[0]][ls[1][fs]].mean(1)])
            # the deepest skirt point inside the thigh can sit away from the crossing faces: probe the whole skirt
            for a_ in (pen_depth(probes(C, SKS), bth, hide), pen_depth(ring(THS[s], th_f), bsk, hide)):
                pen = max(pen, a_[0]); pen_vis = max(pen_vis, a_[1])
    out["thigh_pen"] = pen; out["thigh_pen_vis"] = pen_vis; out["thigh_pairs"] = pairs
    if want_gap:
        g = 1e9
        for s in "LR":
            P = C[THS[s][0]]
            P = P[P[:, 2] > C[SKS[0], 2].min() - 0.05]
            for p in P:
                g = min(g, bsk.find_nearest(Vector(p))[3])
        out["thigh_gap"] = g
        barm = bvh_of(C, ARMS)
        out["arm_pairs"] = len(bsk.overlap(barm))
        out["arm_gap"] = min(barm.find_nearest(Vector(p))[3] for p in C[SKS[0]][::2])
    out["floor"] = float(C[SKS[0], 2].min())
    # top rim buried inside the posed trunk?
    ex_ = 0.0
    for q in C[SK_TOP_IDX]:
        if not inside_parity(btr, q):
            ex_ = max(ex_, btr.find_nearest(Vector(q))[3])
    out["top_exposed"] = ex_
    out["map_err"] = 0.0
    bshin = [bvh_of(C, SHS[s]) for s in "LR"]
    out["shin_pairs"] = sum(len(bsk.overlap(b)) for b in bshin)
    return out


# ---- v4 plates: exact per-frame contact (BVH, closed shells, parity), visible = not inside the posed trunk body
TSS = local_set(fkind == "tasset")
TS_SETS = {nm_: local_set((fkind == "tasset") & (vlabel[FL[:, 0]] == nm_)) for nm_ in TASSETS}
FOS = {s: local_set((fkind == "foot") & (fside == s)) for s in "LR"}
TS_TOP_ALL = np.concatenate([TS_TOP_IDX[nm_] for nm_ in TASSETS])
TS_BODY_IDX = np.concatenate([PIECE_IDX[nm_][PIECE_LO[nm_]["vw"] <= 0.85] for nm_ in TASSETS])


def plates_frame(C, btr, want_gap, Gp=None):
    out = {}
    bts = bvh_of(C, TSS)

    def hide(p):
        return inside_parity(btr, p)
    pr = probes(C, TSS)
    ring = lambda ls, fs: np.vstack([C[ls[0]][np.unique(ls[1][fs])], C[ls[0]][ls[1][fs]].mean(1)])
    for part, SETS in (("thigh", THS), ("shin", SHS), ("foot", FOS)):
        pen, vis, pairs = 0.0, 0.0, 0
        for s in "LR":
            bp = bvh_of(C, SETS[s])
            ov = bts.overlap(bp)
            pairs += len(ov)
            if ov:
                fs = np.unique([b for _, b in ov])
                for a_ in (pen_depth(pr, bp, hide), pen_depth(ring(SETS[s], fs), bts, hide)):
                    pen = max(pen, a_[0]); vis = max(vis, a_[1])
        out[part] = (pen, vis, pairs)
    # plate body (below the belt) inside the trunk body or the knuckles (the trunk mesh carries the arms)
    w_ = 0.0
    into_by = {}
    ins_ = np.zeros(len(TS_BODY_IDX), bool)
    for k_q, (i_q, q) in enumerate(zip(TS_BODY_IDX, C[TS_BODY_IDX])):
        if inside_parity(btr, q):
            ins_[k_q] = True
            d_ = btr.find_nearest(Vector(q))[3]
            w_ = max(w_, d_); into_by[str(vlabel[i_q])] = max(into_by.get(str(vlabel[i_q]), 0.0), d_)
    out["into_trunk"] = w_; out["into_by"] = into_by
    # poke-through: plate points OUTSIDE the trunk body but higher (pelvis frame) than the belt + 0.25 m - a plate
    # carried up with the fold showing through the chest / shoulders instead of staying folded in
    out["poke"] = 0.0
    if Gp is not None:
        Gi = np.array(Gp.inverted())
        Qp = C[TS_BODY_IDX] @ Gi[:3, :3].T + Gi[:3, 3]
        hi_ = (~ins_) & (Qp[:, 2] > ZAJ.max() + 0.25)
        out["poke"] = float((Qp[hi_, 2] - ZAJ.max() - 0.25).max()) if hi_.any() else 0.0
        out["poke_n"] = int(hi_.sum())
    # plate <-> plate and plate <-> back shell
    bl = {nm_: bvh_of(C, TS_SETS[nm_]) for nm_ in TASSETS}
    bl["skirt"] = bvh_of(C, SKS)
    names = TASSETS + ["skirt"]
    pp_pairs, pp_pen, pp_vis, pp_by = 0, 0.0, 0.0, {}
    for i_ in range(len(names)):
        for j_ in range(i_ + 1, len(names)):
            if bl[names[i_]].overlap(bl[names[j_]]):
                pp_pairs += 1
                a_set = TS_SETS[names[i_]]; b_set = TS_SETS[names[j_]] if names[j_] != "skirt" else SKS
                d1 = pen_depth(probes(C, a_set), bl[names[j_]], hide); d2 = pen_depth(probes(C, b_set), bl[names[i_]], hide)
                pp_pen = max(pp_pen, d1[0], d2[0]); pp_vis = max(pp_vis, d1[1], d2[1])
                pp_by[names[i_] + "|" + names[j_]] = (max(d1[0], d2[0]), max(d1[1], d2[1]))
    out["pp_pairs"] = pp_pairs; out["pp_pen"] = pp_pen; out["pp_vis"] = pp_vis; out["pp_by"] = pp_by
    out["floor"] = float(C[TSS[0], 2].min())
    ex_, ex_by = 0.0, {}
    for nm_ in TASSETS:
        for q in C[TS_TOP_IDX[nm_]]:
            if not inside_parity(btr, q):
                d_ = btr.find_nearest(Vector(q))[3]
                ex_ = max(ex_, d_); ex_by[nm_] = max(ex_by.get(nm_, 0.0), d_)
    out["top_exposed"] = ex_; out["top_by"] = ex_by
    # the visible part of a plate's crossing into the trunk: plate body points inside the trunk are hidden, but the
    # crossing line is where the plate enters the surface - count the frames it happens and the plates involved
    if want_gap:
        g = 1e9
        for s in "LR":
            P = C[THS[s][0]]
            P = P[P[:, 2] > C[TSS[0], 2].min() - 0.05]
            for p in P:
                g = min(g, bts.find_nearest(Vector(p))[3])
        out["thigh_gap"] = g
    return out


REST_PELVIS = REST["pelvis"]
frame_cache = {}
trunk_min_z = {}
for name, kind, N, fn in CLIPS:
    info = clips[name]
    sk_acc = {"thigh_pen": (0.0, 0), "thigh_pen_vis": (0.0, 0), "thigh_pairs_frames": 0, "thigh_gap": (1e9, 0), "arm_pairs_frames": 0, "arm_gap": (1e9, 0),
              "floor": (1e9, 0), "top_exposed": (0.0, 0), "map_err": 0.0, "shin_pairs_frames": 0}
    ts_acc = {k_: (0.0, 0) for k_ in ("thigh_all", "thigh_vis", "shin_all", "shin_vis", "foot_all", "foot_vis", "into_trunk", "pp_pen", "top_exposed", "poke")}
    ts_acc["poke_frames"] = 0
    ts_acc.update({"floor": (1e9, 0), "thigh_gap": (1e9, 0), "thigh_pairs_frames": 0, "shin_pairs_frames": 0, "foot_pairs_frames": 0, "pp_frames": 0,
                   "pp_vis": (0.0, 0), "pp_by": {}, "top_by": {}, "into_by": {}, "into_frames": 0})
    K.assign_action(rig, bpy.data.actions[name])
    feet = {s: [] for s in "LR"}
    hands = {s: [] for s in "LR"}
    stretch, gap, root_dev = 0.0, 0.0, 0.0
    lo_, hi_ = np.full(3, 1e9), np.full(3, -1e9)
    sole_z = {s: [] for s in "LR"}
    tmin = 1e9
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
        btr_ = BVHTree.FromPolygons(C.tolist(), TRUNK_F.tolist())
        m_ = skirt_frame(C, pose["pelvis"].matrix @ REST_PELVIS.inverted(), name in ("idle", "walk"), btr_)
        t_ = plates_frame(C, btr_, name in ("idle", "walk"), pose["pelvis"].matrix @ REST_PELVIS.inverted())
        for part in ("thigh", "shin", "foot"):
            for k_, v_ in ((part + "_all", t_[part][0]), (part + "_vis", t_[part][1])):
                if v_ > ts_acc[k_][0]:
                    ts_acc[k_] = (v_, f)
            ts_acc[part + "_pairs_frames"] += int(t_[part][2] > 0)
        for k_ in ("into_trunk", "pp_pen", "top_exposed", "poke"):
            if t_[k_] > ts_acc[k_][0]:
                ts_acc[k_] = (t_[k_], f)
        if t_["floor"] < ts_acc["floor"][0]:
            ts_acc["floor"] = (t_["floor"], f)
        if "thigh_gap" in t_ and t_["thigh_gap"] < ts_acc["thigh_gap"][0]:
            ts_acc["thigh_gap"] = (t_["thigh_gap"], f)
        ts_acc["pp_frames"] += int(t_["pp_pairs"] > 0)
        ts_acc["into_frames"] += int(t_["into_trunk"] > 0.01)
        ts_acc["poke_frames"] += int(t_.get("poke_n", 0) > 0)
        if t_["pp_vis"] > ts_acc["pp_vis"][0]:
            ts_acc["pp_vis"] = (t_["pp_vis"], f)
        for k_, v_ in t_["pp_by"].items():
            o_ = ts_acc["pp_by"].get(k_, (0.0, 0.0, 0))
            ts_acc["pp_by"][k_] = (max(o_[0], v_[0]), max(o_[1], v_[1]), o_[2] + 1)
        for k_, v_ in t_["top_by"].items():
            ts_acc["top_by"][k_] = max(ts_acc["top_by"].get(k_, 0.0), v_)
        for k_, v_ in t_["into_by"].items():
            ts_acc["into_by"][k_] = max(ts_acc["into_by"].get(k_, 0.0), v_)
        for k_, better in (("thigh_pen", max), ("thigh_pen_vis", max), ("top_exposed", max), ("floor", min), ("thigh_gap", min), ("arm_gap", min)):
            if k_ in m_ and better(m_[k_], sk_acc[k_][0]) == m_[k_] and m_[k_] != sk_acc[k_][0]:
                sk_acc[k_] = (m_[k_], f)
        sk_acc["thigh_pairs_frames"] += int(m_["thigh_pairs"] > 0)
        sk_acc["arm_pairs_frames"] += int(m_.get("arm_pairs", 0) > 0)
        sk_acc["shin_pairs_frames"] += int(m_["shin_pairs"] > 0)
        sk_acc["map_err"] = max(sk_acc["map_err"], m_["map_err"])
        if C[:, 2].min() < lo_[2]:
            iz = int(C[:, 2].argmin())
            info["lowest_vertex"] = {"z_mm": round(float(C[iz, 2]) * 1000, 2), "frame": f, "part": str(vlabel[iz]),
                                     "vertex": iz, "co": C[iz].round(4).tolist(), "rest_co": VL[iz].round(4).tolist(),
                                     "bone_matrix": [list(map(lambda x: round(x, 4), r)) for r in pose[str(vlabel[iz])].matrix]
                                     if str(vlabel[iz]) in pose else None}
        lo_ = np.minimum(lo_, C.min(0)); hi_ = np.maximum(hi_, C.max(0))
        tmin = min(tmin, float(C[:ntv, 2].min()))
        for s in "LR":
            sole_z[s].append(C[sole[s], 2])
        if f == 1:
            frame_cache[(name, "first")] = C
        last = C
    frame_cache[(name, "last")] = last
    if kind == "loop":
        info["seam_residual_mm"] = round(float(np.linalg.norm(frame_cache[(name, "first")] - last, axis=1).max()) * 1000, 4)
    info["skirt_contact"] = {
        "skirt_thigh_worst_penetration_mm": round(sk_acc["thigh_pen"][0] * 1000, 1), "at_frame": sk_acc["thigh_pen"][1],
        "skirt_thigh_worst_VISIBLE_penetration_mm": round(sk_acc["thigh_pen_vis"][0] * 1000, 1), "visible_at_frame": sk_acc["thigh_pen_vis"][1],
        "frames_with_skirt_thigh_triangle_overlap": sk_acc["thigh_pairs_frames"], "frames": N + 1,
        "skirt_min_z_mm": round(sk_acc["floor"][0] * 1000, 2), "skirt_min_z_frame": sk_acc["floor"][1],
        "top_rim_worst_exposure_outside_trunk_mm": round(sk_acc["top_exposed"][0] * 1000, 1), "top_rim_exposure_frame": sk_acc["top_exposed"][1],
        "frames_with_skirt_shin_overlap_info": sk_acc["shin_pairs_frames"]}
    info["tasset_contact"] = {
        "plate_thigh_worst_penetration_mm": round(ts_acc["thigh_all"][0] * 1000, 1), "at_frame": ts_acc["thigh_all"][1],
        "plate_thigh_worst_VISIBLE_penetration_mm": round(ts_acc["thigh_vis"][0] * 1000, 1), "visible_at_frame": ts_acc["thigh_vis"][1],
        "frames_with_plate_thigh_triangle_overlap": ts_acc["thigh_pairs_frames"],
        "plate_shin_worst_penetration_mm": round(ts_acc["shin_all"][0] * 1000, 1), "plate_shin_worst_VISIBLE_mm": round(ts_acc["shin_vis"][0] * 1000, 1),
        "frames_with_plate_shin_overlap": ts_acc["shin_pairs_frames"],
        "plate_foot_worst_penetration_mm": round(ts_acc["foot_all"][0] * 1000, 1), "plate_foot_worst_VISIBLE_mm": round(ts_acc["foot_vis"][0] * 1000, 1),
        "frames_with_plate_foot_overlap": ts_acc["foot_pairs_frames"],
        "plate_body_into_trunk_or_knuckles_mm": round(ts_acc["into_trunk"][0] * 1000, 1), "into_trunk_frame": ts_acc["into_trunk"][1],
        "plate_plate_or_backshell_overlap_frames": ts_acc["pp_frames"], "plate_plate_worst_penetration_mm": round(ts_acc["pp_pen"][0] * 1000, 1),
        "plate_plate_worst_VISIBLE_mm": round(ts_acc["pp_vis"][0] * 1000, 1), "plate_plate_visible_frame": ts_acc["pp_vis"][1],
        "plate_pairs_all_vis_mm_frames": {k_: [round(v_[0] * 1000, 1), round(v_[1] * 1000, 1), v_[2]] for k_, v_ in ts_acc["pp_by"].items()},
        "frames_with_plate_body_inside_trunk_gt_10mm": ts_acc["into_frames"],
        "poke_through_above_belly_mm": round(ts_acc["poke"][0] * 1000, 1), "poke_frame": ts_acc["poke"][1], "poke_frames": ts_acc["poke_frames"],
        "into_trunk_by_plate_mm": {k_: round(v_ * 1000, 1) for k_, v_ in ts_acc["into_by"].items()},
        "top_rim_exposure_by_plate_mm": {k_: round(v_ * 1000, 1) for k_, v_ in ts_acc["top_by"].items()},
        "plate_min_z_mm": round(ts_acc["floor"][0] * 1000, 2), "plate_min_z_frame": ts_acc["floor"][1],
        "plate_top_rim_worst_exposure_mm": round(ts_acc["top_exposed"][0] * 1000, 1), "top_rim_exposure_frame": ts_acc["top_exposed"][1],
        "frames": N + 1}
    if name in ("idle", "walk"):
        info["tasset_contact"]["thigh_vertex_to_plate_min_gap_mm"] = round(ts_acc["thigh_gap"][0] * 1000, 1)
        info["skirt_contact"].update({"thigh_vertex_to_skirt_min_gap_mm": round(sk_acc["thigh_gap"][0] * 1000, 1), "gap_frame": sk_acc["thigh_gap"][1],
                                      "frames_with_skirt_arm_overlap": sk_acc["arm_pairs_frames"],
                                      "skirt_vertex_to_arm_min_gap_mm": round(sk_acc["arm_gap"][0] * 1000, 1), "arm_gap_frame": sk_acc["arm_gap"][1]})
    info["root_max_offset_mm"] = round(root_dev * 1000, 4)
    info["bone_rigid_stretch_max_pct"] = round(stretch * 100, 5)
    info["joint_gap_max_mm"] = round(gap * 1000, 4)
    info["trunk_min_z_mm"] = round(tmin * 1000, 2)
    info["extent"] = {"footprint": round(float(max(hi_[0] - lo_[0], hi_[1] - lo_[1])), 4),
                      "width": round(float(hi_[0] - lo_[0]), 4), "depth": round(float(hi_[1] - lo_[1]), 4),
                      "height": round(float(hi_[2] - lo_[2]), 4), "top_z": round(float(hi_[2]), 4), "min_z": round(float(lo_[2]), 4)}
    belt = info.get("belt_m_per_frame", 0.0)
    slides = {}
    tracks = [("foot." + s, feet[s]) for s in "LR"]
    if name == "sitting_idle":
        tracks += [("forearm.%s:tail" % s, hands[s]) for s in "LR"]
    pad_contact = {}
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
        if lab_.startswith("foot."):
            s = lab_[-1]
            zz = [sole_z[s][i] for i in range(len(flags)) if flags[i]]
            pad_contact[s] = {"sole_z_min_mm": round(float(min(z.min() for z in zz)) * 1000, 3),
                              "sole_z_max_mm": round(float(max(z.max() for z in zz)) * 1000, 3)}
    info["contact_slide"] = slides
    info["pad_sole_during_contact"] = pad_contact


def join(a_, b_):
    return round(float(np.linalg.norm(frame_cache[a_] - frame_cache[b_], axis=1).max()) * 1000, 4)


clips["stand_up"]["join_mm"] = {"first_vs_sitting_idle_f1": join(("stand_up", "first"), ("sitting_idle", "first")),
                                "last_vs_idle_f1": join(("stand_up", "last"), ("idle", "first"))}
clips["sit_down"]["join_mm"] = {"first_vs_idle_f1": join(("sit_down", "first"), ("idle", "first")),
                                "last_vs_sitting_idle_f1": join(("sit_down", "last"), ("sitting_idle", "first"))}
_mr = skirt_frame(VL, Matrix.Identity(4), True)
_btr_rest = BVHTree.FromPolygons(VL.tolist(), TRUNK_F.tolist())
_tr = plates_frame(VL, _btr_rest, True)
report["tassets"]["rest_contact"] = {"plate_thigh_penetration_mm": round(_tr["thigh"][0] * 1000, 2), "thigh_vertex_to_plate_min_gap_mm": round(_tr["thigh_gap"] * 1000, 1),
                                     "plate_plate_overlap_pairs": _tr["pp_pairs"], "plate_body_into_trunk_mm": round(_tr["into_trunk"] * 1000, 1),
                                     "plate_top_rim_exposure_mm": round(_tr["top_exposed"] * 1000, 1)}
report["skirt"]["rest_contact"] = {"skirt_thigh_triangle_overlap_pairs": _mr["thigh_pairs"],
                                   "skirt_thigh_penetration_mm": round(_mr["thigh_pen"] * 1000, 2),
                                   "thigh_vertex_to_skirt_min_gap_mm": round(_mr["thigh_gap"] * 1000, 1),
                                   "skirt_arm_overlap_pairs": _mr["arm_pairs"], "skirt_vertex_to_arm_min_gap_mm": round(_mr["arm_gap"] * 1000, 1),
                                   "top_rim_exposure_mm": round(_mr["top_exposed"] * 1000, 2)}

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

# ---- standing balance: COM (trunk + shells, rest) vs the support polygon of the two soles
def hull2(pts):
    pts = sorted(set(map(tuple, np.round(pts, 6))))
    if len(pts) < 3:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


vt = [(VOL, COM + T_REST)]
for s in "LR":
    for part in ("thigh", "shin", "foot"):
        V, F = LEGS_LO[s][part]
        a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        vv = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
        vt.append((float(vv.sum()), (vv[:, None] * (a + b + c) / 4.0).sum(0) / vv.sum() + S_RC))
for d_ in PIECE_LO.values():                                  # v4 back shell + plates (rest frame)
    _a, _b, _c = d_["V"][d_["F"][:, 0]], d_["V"][d_["F"][:, 1]], d_["V"][d_["F"][:, 2]]
    _vv = np.einsum("ij,ij->i", _a, np.cross(_b, _c)) / 6.0
    vt.append((float(_vv.sum()), (_vv[:, None] * (_a + _b + _c) / 4.0).sum(0) / _vv.sum()))
Mtot = sum(v for v, _ in vt)
COM_S = sum(v * c for v, c in vt) / Mtot * S_FIT + SHIFT_XY     # v4: sculpt-scale build -> refit asset
soles = VL[sole["L"] | sole["R"]][:, :2]
hl = hull2(soles)                                        # CCW (monotone chain)
sd = []
for i in range(len(hl)):
    a, b = hl[i], hl[(i + 1) % len(hl)]
    e = b - a
    sd.append(float((e[0] * (COM_S[1] - a[1]) - e[1] * (COM_S[0] - a[0])) / np.linalg.norm(e)))   # >0 = inside side
inside = min(sd) >= 0
dmin = min(abs(x) for x in sd)
per_foot = {s: VL[sole[s]][:, :2].mean(0).round(4).tolist() for s in "LR"}
report["balance"] = {"com_rest_xyz": COM_S.round(4).tolist(), "sole_centres_xy": per_foot,
                     "com_y_minus_mean_sole_y_m": round(float(COM_S[1] - np.mean([per_foot[s][1] for s in "LR"])), 4),
                     "com_inside_support_polygon": bool(inside), "com_margin_to_support_edge_m": round(dmin, 4),
                     "support_polygon_extent_m": {"x": round(float(np.ptp(hl[:, 0])), 3), "y": round(float(np.ptp(hl[:, 1])), 3)},
                     "rule": "uniform density over trunk (closed) + leg shells, rest pose; support = convex hull of both flat soles"}

# ---- finish
rig.animation_data.action = None
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
bpy.context.view_layer.update()
Crest = mesh_coords()
H_STAND = float(Crest[:, 2].max() - Crest[:, 2].min())
H_SIT_POST = report["sit_pose"]["seated_height_m"] * S_FIT
report["heights"] = {"seated_m": round(H_SIT_POST, 4), "standing_m": round(H_STAND, 4), "ratio": round(H_STAND / H_SIT_POST, 4),
                     "pre_refit": {"sculpt_seated_m": round(H_SEAT, 4), "sitting_pose_m": report["sit_pose"]["seated_height_m"],
                                   "standing_m": round(H_PRE, 4), "ratio": round(H_PRE / report["sit_pose"]["seated_height_m"], 4)},
                     "cell_refit_scale": round(S_FIT, 6),
                     "v3": {"seated_m": 2.7108, "standing_m": 5.4216, "ratio": 2.0, "leg_chain_m": 2.9156},
                     "leg_chain_m": {"pre_refit": round(float(np.mean([LL1[s_] + LL2[s_] for s_ in "LR"])), 4),
                                     "post_refit": round(float(np.mean([LL1[s_] + LL2[s_] for s_ in "LR"])) * S_FIT, 4)}}
report["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                    "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]}
                   for b in arm_data.bones]
report["bone_count"] = len(arm_data.bones)
report["leg_chain_lengths_m"] = {s: {"thigh": round(LL1[s] * S_FIT, 4), "shin": round(LL2[s] * S_FIT, 4)} for s in "LR"}
report["clips"] = clips
rig["conquest_rig"] = "archetype-minimal v4 standing-rest, tree-skirt back shell bone + 6 thigh-parented tasset plate bones (eldroot)"
low["conquest_clips"] = [c[0] for c in CLIPS]
low["conquest_state_clips"] = ["sitting_idle", "stand_up", "sit_down"]
low["conquest_rest_stance"] = "standing (full extension)"
low["conquest_max_height"] = 5.6
low["conquest_tri_budget"] = [20000, 30000]
for k in ("conquest_front_anchor", "conquest_front_landmark"):
    if k in low.keys():
        low[k] = ((np.array(low[k]) + T_REST) * S_FIT + SHIFT_XY).tolist()
report["mesh_final"] = {"verts": int(len(VL)), "tris": int(len(FL)), "coords_sha": sha(VL), "faces_sha": sha(FL),
                        "bbox": [Crest.min(0).round(4).tolist(), Crest.max(0).round(4).tolist()]}
report["seconds"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_BLEND), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, copy=True, compress=True)
json.dump(report, open(OUT_JSON, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("STAND4_DONE", json.dumps({"heights": report["heights"], "seconds": report["seconds"]}))
sys.stdout.flush()
