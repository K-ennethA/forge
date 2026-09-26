"""Vineweave (hero) build: retopo + UV + bake + palette regions + vine rig + PROPOSED idle, one run.

    blender --background source-copies/hero-green_hero.blend --factory-startup --python improve/vineweave_build.py -- \
        [--skins default,emberroot] [--stage geo <preview.blend>]

Outputs (the opened source copy is never saved over; save_as_mainfile copy=True):
    improved/vineweave.blend + .json      hero-tier mesh, UVs, baked normal/AO, default palette (no rig)
    improved/textures/vineweave_{normal,ao}.png
    rigged/vineweave.blend + .json        + vine-chain rig, weights, PROPOSED 'idle' (no walk: the
                                          artist has not given the gait; no attacks)
    rigged/vineweave__<skin>.blend        one per extra --skins entry: the rigged file repainted
                                          from palettes/vineweave/<skin>.json (palette swap only)

Artist context (design/review-log.md 2026-09-25): "vineweave arms are vines should be flexible to
move"; author at NATURAL proportions (cell fit is report-only, the game scales at import); skins
are palette-swap variants. Source survey: 6 meshes, 204k tris, the 150k belt/braid mesh has no
UVs, no materials; facing -Y is already correct (yaw 0).

Pipeline:
  1. source parts to world space (object transforms applied), tiny belt specks (< 64 verts) dropped.
  2. VINE RE-POSE: the T-spanned vine arms of the body sculpt are re-posed by a rigkit.VineCurve
     rotation field (droop from the shoulder, hang, twist, curl at the tip) -- the SAME field
     later places the chain bones, so mesh and rig agree by construction.
  3. per-part collapse decimation (deterministic) to the hero budget, joined, flat-shaded, Smart UV.
  4. colour regions (face level) -> palettes.store_regions; paint from palettes/vineweave/default.json.
  5. bake normal (vs the smooth low) + AO from the re-posed 204k sculpt: base pass + one isolated
     pass per bake group (per part / per leaf shell), eldroot_stand4 pattern.
  6. rig: root, pelvis, chest, head, thigh/shin/foot per leg, VINE_BONES-bone vine chain per arm;
     weights: body = rig_unit proximity bands (1/d^4, gated), vines = rigkit.vine_weights; 3
     smoothing passes, top-3, normalized.
  7. PROPOSED idle: travelling curl/sway waves down each vine + slow chest breath; pelvis and legs
     are never keyed (feet cannot slide). Seam-closed (integer harmonics, last frame == first).
"""
import bpy, bmesh, sys, os, math, json, time, hashlib
import numpy as np
from mathutils import Matrix, Vector, Euler, Quaternion
from mathutils.kdtree import KDTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402
import palettes as PAL  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun)
UNIT = "vineweave"
TRI_TARGETS = {                      # "detail budget" per part, triangles (hero tier 20-35k total)
    "body": 13000, "belt": 9000, "leaf_front": 2600, "leaf_back": 2600, "leaf_side_L": 1400, "leaf_side_R": 1400}
TRI_BUDGET = [20000, 35000]          # declared hero-tier window (contract tri_budget)
VINE_ROOT_X = 0.85                   # where the vine leaves the shoulder (|x|, sculpt units)
VINE_DROOP_DEG = 86.0                # "vine drop" from the shoulder (90 = hangs straight down)
VINE_DROOP_S = (0.04, 0.78)          # length of vine over which the drop happens
VINE_TWIST_DEG = 90.0                # "vine twist" while hanging (about its own length): turns the
VINE_TWIST_S = (0.9, 1.9)            #   strand fan from front-back to side-by-side, rear strand outward
VINE_CURL_DEG = 110.0                # "tip curl" (how far the vine tip curls forward and up)
VINE_CURL_S0 = 1.6                   # where along the vine the curl starts
VINE_CURL_OUT_DEG = 0.0              # curl direction: 0 = curls forward, 90 = curls out to the side
VINE_CURL_TRIM = 0.45                # the curl stops this far before the tip (the strand hooks ride rigidly)
# (explored 2026-09-25, geo stage: an outward 150 deg curl folded the strand fan -- arc stretch
#  -0.16 at s 3.2 -- and kept the fit footprint-bound at 6.3 wide; this set: no fold, min 0.25)
VINE_BONES = 7                       # bones per vine chain (5-8)
Z_NECK = 3.72                        # head / torso split (sculpt z)
Z_HIP = 1.35                         # leg / torso split, thigh heads
Z_BELT_TOP = 2.50                    # belt / chest-braid split inside the belt mesh
VINE_TIP_FRAC = 0.70                 # vine_tip colour starts at this fraction of the vine length
IDLE_FRAMES = 120                    # idle loop length in frames (24 fps -> 5 s)
IDLE_CURL = (0.8, 3.2)               # vine curl wave amplitude, deg per bone (root, tip)
IDLE_SWAY = (0.6, 2.2)               # vine side-sway amplitude, deg per bone (root, tip)
IDLE_LAG = 0.55                      # wave travel: phase lag per bone (rad)
IDLE_BREATH_DEG = 1.1                # chest breath (pitch, deg)
IDLE_HEAD_DEG = (0.8, 1.6)           # head (pitch lag, slow look yaw), deg
BAKE_CAGE = 0.035                    # bake cage extrusion (sculpt units)
BAKE_RES = (2048, 1024)              # normal, AO texture sizes
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9   # Conquest hero ceilings -- REPORT ONLY (scale policy 2026-09-25)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SKINS = ["default"]
if "--skins" in argv:
    SKINS = argv[argv.index("--skins") + 1].split(",")
STAGE_GEO = argv[argv.index("--stage") + 2] if "--stage" in argv else None
# exploration override of any constant above: --set VINE_CURL_DEG=120 (the committed build uses none)
import ast  # noqa: E402
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", UNIT + ".blend")
TEX_DIR = os.path.join(ROOT, "improved", "textures")
report = {"unit": UNIT, "source": bpy.data.filepath, "tier": "hero", "tri_budget": TRI_BUDGET, "yaw_fix_deg": 0.0,
          "overrides": OVERRIDES}
geo = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def mesh_arrays(me, M=None):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    if M is not None:
        M = np.array(M); co = co @ M[:3, :3].T + M[:3, 3]
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    faces = [lv[a:a + b].tolist() for a, b in zip(ls, lt)]
    return co, faces


def islands(n, faces):
    par = np.arange(n)

    def find(a):
        r = a
        while par[r] != r:
            r = par[r]
        while par[a] != r:
            par[a], a = r, par[a]
        return r
    for f in faces:
        r0 = find(f[0])
        for v in f[1:]:
            r = find(v)
            if r != r0:
                par[r] = r0
    return np.array([find(i) for i in range(n)])


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


# =========================================================================== 1. source parts
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
SRC = {"Sphere": "body", "Sphere.001": "leaf_front", "Sphere.002": "leaf_back", "Sphere.003": "leaf_side_L",
       "Sphere.005": "leaf_side_R", "Sphere.004": "belt"}
PART_NAMES = list(TRI_TARGETS.keys())
parts = {}
for on, pn in SRC.items():
    o = bpy.data.objects[on]
    V, F = mesh_arrays(o.data, o.matrix_world)
    parts[pn] = {"V": V, "F": F, "src": on, "tris_src": tri_count(o.data)}
report["tris_source"] = int(sum(p["tris_src"] for p in parts.values()))
# belt specks: loose shells < 64 verts (0.02-0.04 wide flecks on the belt side) collapse to degenerate
# slivers under decimation; drop them from high and low alike.
bp = parts["belt"]
lab = islands(len(bp["V"]), bp["F"])
u, cnt = np.unique(lab, return_counts=True)
keepv = np.isin(lab, u[cnt >= 64])
remap = -np.ones(len(bp["V"]), dtype=np.int64); remap[keepv] = np.arange(keepv.sum())
bp["F"] = [[int(remap[v]) for v in f] for f in bp["F"] if keepv[f[0]]]
bp["V"] = bp["V"][keepv]
report["belt_specks_dropped"] = {"shells": int((cnt < 64).sum()), "verts": int((~keepv).sum())}

# =========================================================================== 2. vine re-pose
B = parts["body"]["V"]
curves = {}
vine_s = np.full(len(B), -1.0)
vine_side = np.zeros(len(B), dtype=np.int8)
geo["vines"] = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    # the vine tips curl up to z 3.78 (above the neck line); the head never reaches |x| > 0.81
    arm = (sg * B[:, 0] > VINE_ROOT_X - 0.05) & (B[:, 2] < 3.95) & (B[:, 2] > 2.5)
    slab = arm & (sg * B[:, 0] > 0.95) & (sg * B[:, 0] < 1.15)
    y0, z0 = B[slab, 1].mean(), B[slab, 2].mean()
    s = sg * B[:, 0] - VINE_ROOT_X
    L = float(s[arm].max())
    D = np.array([sg * math.sin(math.radians(VINE_CURL_OUT_DEG)), -math.cos(math.radians(VINE_CURL_OUT_DEG)), 0.0])
    curl_axis = np.cross([0.0, 0.0, -1.0], D)
    ops = [{"kind": "bend", "s0": VINE_DROOP_S[0], "s1": VINE_DROOP_S[1], "deg": VINE_DROOP_DEG, "axis": (0.0, sg, 0.0), "ramp": 0.3},
           {"kind": "twist", "s0": VINE_TWIST_S[0], "s1": VINE_TWIST_S[1], "deg": sg * VINE_TWIST_DEG, "ramp": 0.3},
           {"kind": "bend", "s0": VINE_CURL_S0, "s1": L - VINE_CURL_TRIM, "deg": VINE_CURL_DEG, "axis": tuple(curl_axis), "ramp": 0.25}]
    cv = K.VineCurve((sg * VINE_ROOT_X, y0, z0), (sg, 0.0, 0.0), L, ops)
    curves[side] = {"curve": cv, "curl_axis": curl_axis, "sg": sg}
    idx = np.nonzero(arm)[0]
    stretch_min, stretch_s, pinched = cv.arc_stretch_min(B[idx], s[idx], where=True)
    B[idx] = cv.deform(B[idx], s[idx])
    vine_s[idx] = s[idx]; vine_side[idx] = int(sg)
    geo["vines"][side] = {"root": [round(sg * VINE_ROOT_X, 4), round(float(y0), 4), round(float(z0), 4)], "length": round(L, 4),
                          "verts": int(len(idx)), "arc_stretch_min": round(stretch_min, 4),
                          "arc_stretch_min_at_s": round(stretch_s, 3), "pinched_fraction_lt_0.15": round(pinched, 5),
                          "tip_rest_to_posed": [round(float(v), 4) for v in cv.frame(L)[0]]}
parts["body"]["V"] = B
geo["vine_ops"] = {"droop_deg": VINE_DROOP_DEG, "droop_s": VINE_DROOP_S, "twist_deg": VINE_TWIST_DEG, "twist_s": VINE_TWIST_S,
                   "curl_deg": VINE_CURL_DEG, "curl_s0": VINE_CURL_S0, "curl_out_deg": VINE_CURL_OUT_DEG,
                   "curl_trim": VINE_CURL_TRIM}

# clearance: vine verts past the droop vs everything that is not vine (all parts)
others = np.vstack([B[vine_s <= 0]] + [parts[p]["V"] for p in PART_NAMES if p != "body"])
kd_o = KDTree(len(others))
for i, p in enumerate(others):
    kd_o.insert(p, i)
kd_o.balance()
far = np.nonzero(vine_s > 0.9)[0]
dmin = np.array([kd_o.find(B[i])[2] for i in far])
geo["vine_clearance_rest"] = {"min_m": round(float(dmin.min()), 4), "p01_m": round(float(np.percentile(dmin, 1)), 4),
                              "verts_within_2cm": int((dmin < 0.02).sum()), "rule": "vine verts s > 0.9 vs all non-vine verts (all parts)"}
geo["vine_min_z"] = round(float(B[vine_s > 0, 2].min()), 4)

allV = np.vstack([parts[p]["V"] for p in PART_NAMES])
lo, hi = allV.min(0), allV.max(0)
SHIFT = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
for p in PART_NAMES:
    parts[p]["V"] = parts[p]["V"] - SHIFT
size = hi - lo
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / size[2], CELL_MAX_FP / fp)
geo["natural"] = {"height": round(float(size[2]), 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                  "footprint": round(fp, 4), "units": "sculpt units (authored at natural proportions, no refit)",
                  "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(float(size[2] * k_fit), 4),
                                                  "bound_by": "height" if CELL_MAX_H / size[2] <= CELL_MAX_FP / fp else "footprint",
                                                  "ceilings": [CELL_MAX_H, CELL_MAX_FP]},
                  "shipped_glb_height_m": 1.137, "source_tpose": {"width": 8.823, "height": 5.273}}
report["geometry"] = geo
print("GEO", json.dumps(geo))

# --------------------------------------------------------------------------- high objects (per bake group)
scene = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], F)
    me.update()
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    return ob


HIGH = {p: new_obj(UNIT + "_high_" + p, parts[p]["V"], parts[p]["F"]) for p in PART_NAMES}
if STAGE_GEO:
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=STAGE_GEO, copy=True)
    print("STAGE_GEO_DONE", STAGE_GEO)
    sys.stdout.flush(); os._exit(0)

# cavity field per high part (+ = concave furrow, - = convex ridge), improve_unit.py rule
CAVP, CAVV = [], []
for p in PART_NAMES:
    me = HIGH[p].data
    W = parts[p]["V"]
    ev = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev); ev = ev.reshape(-1, 2)
    nv = np.empty(len(me.vertices) * 3); me.vertex_normals.foreach_get("vector", nv); nv = nv.reshape(-1, 3)
    deg = np.bincount(ev.ravel(), minlength=len(W)).astype(float)

    def nmean(X):
        s_ = np.zeros_like(X)
        for k in range(X.shape[1]):
            s_[:, k] = np.bincount(ev[:, 0], X[ev[:, 1], k], minlength=len(W)) + np.bincount(ev[:, 1], X[ev[:, 0], k], minlength=len(W))
        return s_ / np.maximum(deg, 1)[:, None]
    el = np.linalg.norm(W[ev[:, 0]] - W[ev[:, 1]], axis=1).mean()
    cav = ((nmean(W) - W) * nv).sum(1) / el
    for _ in range(6):
        cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
    cs_ = np.percentile(np.abs(cav), 95) + 1e-9
    CAVP.append(W); CAVV.append(np.clip(cav / cs_, -1, 1))
CAVP = np.vstack(CAVP); CAVV = np.concatenate(CAVV)

# =========================================================================== 3. decimate + join
low_parts = []
for p in PART_NAMES:
    hob = HIGH[p]
    mod = hob.modifiers.new("dec", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = min(1.0, TRI_TARGETS[p] / tri_count(hob.data))
    mod.use_collapse_triangulate = True
    dg = bpy.context.evaluated_depsgraph_get()
    me_l = bpy.data.meshes.new_from_object(hob.evaluated_get(dg))
    hob.modifiers.remove(mod)
    V, F = mesh_arrays(me_l)
    bpy.data.meshes.remove(me_l)
    low_parts.append((p, V, F))
LV, LF, FPART, off = [], [], [], 0
for pi, (p, V, F) in enumerate(low_parts):
    LV.append(V); LF += [[v + off for v in f] for f in F]; FPART += [pi] * len(F); off += len(V)
LV = np.vstack(LV); FPART = np.array(FPART)
# feet exactly at the origin after decimation (collapse can lift the lowest tip a hair)
lo2, hi2 = LV.min(0), LV.max(0)
S2 = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
LV = LV - S2
for p in PART_NAMES:
    parts[p]["V"] = parts[p]["V"] - S2
    Vh = np.empty(len(HIGH[p].data.vertices) * 3); HIGH[p].data.vertices.foreach_get("co", Vh)
    HIGH[p].data.vertices.foreach_set("co", (Vh.reshape(-1, 3) - S2).ravel()); HIGH[p].data.update()
CAVP = CAVP - S2
SHIFT = SHIFT + S2
report["origin_shift_sculpt_units"] = SHIFT.round(5).tolist()
low = new_obj(UNIT, LV, LF)
me = low.data
report["tris_final"] = tri_count(me)
report["tris_per_part"] = {p: int(sum(len(f) - 2 for f, fp_ in zip(LF, FPART) if fp_ == i)) for i, p in enumerate(PART_NAMES)}

# =========================================================================== flat + UV
me.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
# Smart UV alone left 69% of the atlas empty on 30k faces (coverage 0.312); repack the same
# islands tighter (rotation allowed, same margin) so the bake gets the texels.
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

# =========================================================================== 4. regions
nf = len(me.polygons)
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
FN = np.empty(nf * 3); me.polygon_normals.foreach_get("vector", FN); FN = FN.reshape(-1, 3)
kd = KDTree(len(CAVP))
for i, p in enumerate(CAVP):
    kd.insert(p, i)
kd.balance()
fcav = np.array([np.mean([CAVV[j] for (_, j, _) in kd.find_n(p, 6)]) for p in FC])
Zn, Zh = Z_NECK - SHIFT[2], Z_HIP - SHIFT[2]
# vine arc parameter transfer: every high body vertex knows its exact s (it was re-posed with it);
# low faces / verts take the s + side of the nearest high body vertex.
HB = parts["body"]["V"]
kd_hb = KDTree(len(HB))
for i, p in enumerate(HB):
    kd_hb.insert(p, i)
kd_hb.balance()


def vine_param(P):
    idx = np.array([kd_hb.find(p)[1] for p in P], dtype=np.int64)
    return vine_s[idx], vine_side[idx]


body_f = FPART == PART_NAMES.index("body")
fs = np.full(nf, -1.0)
fs[body_f] = vine_param(FC[body_f])[0]
REG = ["head_mask", "eye_socket", "eye_glow", "torso", "braid", "belt", "vine", "vine_tip",
       "leaf_front", "leaf_back", "leaf_side", "leaf_edge", "leg", "foot"]
R_ = {n: i for i, n in enumerate(REG)}
rid = np.full(nf, R_["torso"], dtype=np.int32)
rid[FPART == PART_NAMES.index("leaf_front")] = R_["leaf_front"]
rid[FPART == PART_NAMES.index("leaf_back")] = R_["leaf_back"]
side_leaf = np.isin(FPART, [PART_NAMES.index("leaf_side_L"), PART_NAMES.index("leaf_side_R")])
rid[side_leaf] = R_["leaf_side"]
beltf = FPART == PART_NAMES.index("belt")
rid[beltf] = np.where(FC[beltf, 2] < Z_BELT_TOP - SHIFT[2], R_["belt"], R_["braid"])
# leaf rims: faces whose normal lies in the leaf plane (thin axis y for front/back, x for sides)
fb = np.isin(FPART, [PART_NAMES.index("leaf_front"), PART_NAMES.index("leaf_back")])
rim = (fb & (np.abs(FN[:, 1]) < 0.28)) | (side_leaf & (np.abs(FN[:, 0]) < 0.28))
rid[rim] = R_["leaf_edge"]
vinef = body_f & (fs > 0.12)
rid[vinef] = R_["vine"]
Lmax = {s_: curves[s_]["curve"].L for s_ in curves}
tipf = vinef & (fs > VINE_TIP_FRAC * min(Lmax.values()))
rid[tipf] = R_["vine_tip"]
legf = body_f & (FC[:, 2] < Zh) & ~vinef
rid[legf] = R_["leg"]
rid[legf & (FC[:, 2] < 0.16)] = R_["foot"]
headf = body_f & (FC[:, 2] > Zn) & ~vinef
rid[headf] = R_["head_mask"]
# eye holes: depth below the head's CONVEX HULL (direction-free: the mask surface lies on its hull,
# the carved holes sink inside it), limited to the front half of the mask (the head/back-lobe
# junction and the chin underside are also inside the hull but face sideways / down).
from mathutils.bvhtree import BVHTree  # noqa: E402
Wb = parts["body"]["V"]
hv = (Wb[:, 2] > Zn) & (vine_s <= 0)
bmh = bmesh.new()
for p in Wb[hv]:
    bmh.verts.new(p)
hull = bmesh.ops.convex_hull(bmh, input=list(bmh.verts))
_inner = {id(g): g for g in hull["geom_interior"] + hull["geom_unused"] if isinstance(g, bmesh.types.BMVert)}
bmesh.ops.delete(bmh, geom=list(_inner.values()), context="VERTS")
bvh_h = BVHTree.FromBMesh(bmh)
hd = np.zeros(nf)
hi_ = np.nonzero(headf)[0]
for i in hi_:
    hd[i] = bvh_h.find_nearest(Vector(FC[i]))[3]
bmh.free()
head_cy = float(FC[headf, 1].mean())
Hz = FC[headf, 2]
front_mask = headf & (FC[:, 1] < head_cy) & (np.abs(FC[:, 0]) < 0.55) & (FC[:, 2] > Hz.min() + 0.35 * (Hz.max() - Hz.min()))
EYE_DEPTH = 0.06
socket = front_mask & (hd > EYE_DEPTH)
rid[socket] = R_["eye_socket"]
dmax = float(hd[socket].max()) if socket.any() else 0.0
deep = socket & (hd > 0.45 * dmax)
rid[deep] = R_["eye_glow"]
report["eye_rule"] = {"rule": "head faces > EYE_DEPTH inside the head's convex hull, front half of the mask, above 35% of the head "
                              "height; glow = sockets deeper than 45% of the deepest", "eye_depth": EYE_DEPTH,
                      "max_depth": round(dmax, 4), "socket_faces": int(socket.sum()), "glow_faces": int(deep.sum()),
                      "socket_x_split": [int((socket & (FC[:, 0] < 0)).sum()), int((socket & (FC[:, 0] >= 0)).sum())],
                      "hull_depth_front_p95": round(float(np.percentile(hd[front_mask], 95)), 4)}
# cavity shade + deterministic per-face value jitter (improve_unit.py rule)
cav_k = 0.40
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["cavity_shade_k"] = cav_k

# facing landmark: head centroid -> eye-socket centroid (direction-free identity feature)
anchor = FC[headf].mean(0)
eyes_all = np.isin(rid, [R_["eye_socket"], R_["eye_glow"]])
landmark = FC[eyes_all].mean(0) if eyes_all.any() else anchor
dvec = landmark - anchor
report["facing"] = {"rule": "head faces centroid -> eye-hole faces centroid", "anchor": anchor.round(4).tolist(),
                    "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2)}

# =========================================================================== material
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
PAL.apply_material(mat, pal_default)
me.materials.append(mat)

# =========================================================================== 5. bake
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
bk.margin = 16; bk.use_clear = False
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
# bake groups: body, belt, and each leaf SHELL on its own (the front/back leaves are two
# overlapping shells each; a merged projection would land on the neighbour leaf)
BG_NAMES, BG_HIGH = [], {}
for p in PART_NAMES:
    Vp, Fp = parts[p]["V"], parts[p]["F"]
    if p.startswith("leaf"):
        labp = islands(len(Vp), Fp)
        for k_, r in enumerate(np.unique(labp)):
            fm_ = [f for f in Fp if labp[f[0]] == r]
            vid = np.unique(np.concatenate([np.array(f) for f in fm_])); rm = -np.ones(len(Vp), dtype=np.int64); rm[vid] = np.arange(len(vid))
            nm = "%s.%d" % (p, k_)
            BG_HIGH[nm] = new_obj(UNIT + "_bk_" + nm, Vp[vid], [[int(rm[v]) for v in f] for f in fm_])
            BG_NAMES.append(nm)
    else:
        BG_HIGH[p] = HIGH[p]; BG_NAMES.append(p)
for p in PART_NAMES:
    if p.startswith("leaf"):
        HIGH[p].hide_render = True          # replaced by its shell objects (AO still sees them)
# low faces -> bake group (leaf faces: nearest high shell centroid of the same part)
LI = islands(len(LV), LF)
FISL = LI[np.array([f[0] for f in LF])]
FGRP = np.empty(nf, dtype=object)
for i, p in enumerate(PART_NAMES):
    fm_ = FPART == i
    if not p.startswith("leaf"):
        FGRP[fm_] = p
        continue
    shells = [n for n in BG_NAMES if n.startswith(p + ".")]
    cents = {n: np.array([v.co[:] for v in BG_HIGH[n].data.vertices]).mean(0) for n in shells}
    for isl in np.unique(FISL[fm_]):
        fj = np.nonzero(fm_ & (FISL == isl))[0]
        c = FC[fj].mean(0)
        FGRP[fj] = min(shells, key=lambda n: np.linalg.norm(cents[n] - c))
report["bake_groups"] = {g: int((FGRP == g).sum()) for g in BG_NAMES}
# the low is hidden from every bake ray (AO occluders = the high groups only)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
assert (lt_ == 3).all(), "low must be all triangles"
UVn = UVn.reshape(-1, 3, 2)


def texels_of(mask_faces, res):
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


def bake_pass(sel_objs, tag):
    for o in scene.objects:
        o.select_set(o in sel_objs or o is low)
    bpy.context.view_layer.objects.active = low
    im_n = bpy.data.images.new("bk_n_" + tag, RN, RN, alpha=False)
    im_n.colorspace_settings.name = "Non-Color"; im_n.generated_color = (0.0, 0.0, 0.0, 1.0)
    im_a = bpy.data.images.new("bk_a_" + tag, RA, RA, alpha=False)
    im_a.colorspace_settings.name = "Non-Color"; im_a.generated_color = (1.0, 0.0, 1.0, 1.0)
    tn.image, ta.image = im_n, im_a
    st = {}
    for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
        nt.nodes.active = node
        scene.cycles.samples = samples
        t = time.time()
        r = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
        st[typ] = {"result": sorted(r), "seconds": round(time.time() - t, 1)}
    pn = np.empty(RN * RN * 4, dtype=np.float32); im_n.pixels.foreach_get(pn)
    pa_ = np.empty(RA * RA * 4, dtype=np.float32); im_a.pixels.foreach_get(pa_)
    bpy.data.images.remove(im_n); bpy.data.images.remove(im_a)
    return pn.reshape(-1, 4), pa_.reshape(-1, 4), st


tb = time.time()
bstats = {}
px, pa, bstats["pass_all"] = bake_pass([BG_HIGH[n] for n in BG_NAMES], "all")
iso = {}
for g in BG_NAMES:
    fm_ = FGRP == g
    if not fm_.any():
        iso[g] = {"faces": 0}
        continue
    pn_g, pa_g, st_g = bake_pass([BG_HIGH[g]], g.replace(".", "_"))
    mn = texels_of(fm_, RN) & (pn_g[:, 2] > 0.25)
    ma = texels_of(fm_, RA) & (np.abs(pa_g[:, 0] - pa_g[:, 1]) < 0.02)
    px[mn] = pn_g[mn]; pa[ma] = pa_g[ma]
    iso[g] = {"faces": int(fm_.sum()), "normal_texels": int(mn.sum()), "ao_texels": int(ma.sum()),
              "seconds": round(st_g["NORMAL"]["seconds"] + st_g["AO"]["seconds"], 1)}
bstats["isolated_passes"] = iso
bstats["rule"] = ("base pass: whole low vs every high group (margins); then each group's texels re-baked against its own "
                  "high only (body / belt+braid / each leaf shell), AO occluded by the whole scene; low hidden from rays")
tn.image, ta.image = img_n, img_ao
me.shade_flat()
dev = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
bstats.update({
    "uv_texels_normal": int(alltex_n.sum()), "uv_coverage": round(float(alltex_n.mean()), 4),
    "normal_baked_pct_of_uv_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((dev[cov_n & alltex_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(dev[cov_n & alltex_n].mean()), 4),
    "ao_baked_pct_of_uv_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
per_region = {}
for n_ in REG:
    fm_ = rid == R_[n_]
    if fm_.any():
        tx = texels_of(fm_, RN)
        per_region[n_] = {"texels": int(tx.sum()), "baked_pct": round(100 * float(cov_n[tx].mean()), 2),
                          "normal_dev_mean": round(float(dev[tx & cov_n].mean()), 4) if (tx & cov_n).any() else None}
bstats["per_region_normal"] = per_region
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    """Packed images; the path is informational, written relative to the file being saved."""
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm
bstats["pixel_sha"] = {"normal": sha(px[:, :3]), "ao": sha(pa[:, :1])}
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
# wire: base = Col x AO ; normal = NormalMap(normal tex)
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(vc.outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
for o in list(scene.objects):
    if o is not low:
        m_ = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if m_.users == 0:
            bpy.data.meshes.remove(m_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True

# UV health (contract rule: zero-area UV faces, flipped)
uva = 0.5 * ((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 2, 0] - UVn[:, 0, 0]) * (UVn[:, 1, 1] - UVn[:, 0, 1]))
report["uv"] = {"method": "Smart UV after decimation (66 deg, margin 0.004), whole joined low", "faces": nf,
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()),
                "uv_sha": sha(UVn)}

# props for the contract checker (cell fit is REPORT-ONLY during authoring: ceilings recorded, not enforced)
LVf = np.array([v.co[:] for v in me.vertices])
low["conquest_unit"] = UNIT
low["conquest_tier"] = "hero"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = os.path.basename(bpy.data.filepath)
low["conquest_scale_policy"] = "natural proportions, sculpt units; game scales at import (cell fit report-only)"
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
bpy.context.preferences.filepaths.save_version = 0
set_tex_paths("//textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
M = K.MeshData(low)
W = M.W
Zh_, Zn_ = Z_HIP - SHIFT[2], Z_NECK - SHIFT[2]
# per-vertex part + vine parameter (vertex level: nearest posed-curve sample of its side)
vpart = np.zeros(M.n, dtype=np.int64)
for f, pi in zip(LF, FPART):
    vpart[f] = pi
vs = np.full(M.n, -1.0)
vside = np.zeros(M.n, dtype=np.int8)
bv = np.nonzero(vpart == 0)[0]
vs[bv], vside[bv] = vine_param(W[bv])


def leg_axis(sg):
    m = (vpart == 0) & (W[:, 2] < Zh_) & (sg * W[:, 0] > 0.02) & (vs < 0)
    pts = []
    for z in (Zh_, 0.5 * Zh_, 0.16):
        b = m & (np.abs(W[:, 2] - z) < 0.08)
        pts.append(np.array([W[b, 0].mean(), W[b, 1].mean(), z]))
    sole = m & (W[:, 2] < 0.06)
    toe = W[sole][int(np.argmin(W[sole, 1]))]
    return pts, np.array([pts[2][0], toe[1] + 0.05, 0.08])


body_c = W[(vpart == 0) & (W[:, 2] > Zh_) & (W[:, 2] < Zn_) & (np.abs(W[:, 0]) < 0.5)].mean(0)
head_c = W[(vpart == 0) & (W[:, 2] > Zn_) & (vs < 0)].mean(0)
H_top = float(W[:, 2].max())
Z_CHEST = 0.5 * (Zh_ + Zn_) + 0.25
BONES = [("pelvis", (0, body_c[1], Zh_ - 0.1), (0, body_c[1], Z_CHEST), "root"),
         ("chest", (0, body_c[1], Z_CHEST), (0, body_c[1], Zn_), "pelvis"),
         ("head", (0, head_c[1], Zn_), (0, head_c[1], H_top - 0.1), "chest")]
for side, sg in (("L", 1.0), ("R", -1.0)):
    pts, toe = leg_axis(sg)
    BONES += [("thigh." + side, tuple(pts[0]), tuple(pts[1]), "pelvis"),
              ("shin." + side, tuple(pts[1]), tuple(pts[2]), "thigh." + side),
              ("foot." + side, tuple(pts[2]), tuple(toe), "shin." + side)]
VINE_B = {}
for side, c in curves.items():
    VINE_B[side] = []
    for k, (h, t, s0, s1) in enumerate(c["curve"].bones(VINE_BONES)):
        nm = "vine.%s.%02d" % (side, k + 1)
        BONES.append((nm, tuple(h - SHIFT), tuple(t - SHIFT), "chest" if k == 0 else "vine.%s.%02d" % (side, k)))
        VINE_B[side].append(nm)
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.15 * H_top); eb.use_deform = False
for (n, h, t, p) in BONES:
    e = arm_data.edit_bones.new(n)
    e.head = Vector(h); e.tail = Vector(t)
    e.parent = arm_data.edit_bones[p]
    e.use_deform = True
    e.use_connect = n.startswith(("shin", "foot")) or (n.startswith("vine") and not n.endswith(".01"))
    if n.startswith("vine"):
        side = n.split(".")[1]
        a = curves[side]["curl_axis"]
        d = np.array(t) - np.array(h); d /= np.linalg.norm(d)
        e.align_roll(Vector(np.cross(a, d)))
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [n for (n, h, t, p) in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights
# labels: head / torso / leg.L / leg.R / belt / braid / leaf ; vines by arc parameter
lab = np.full(M.n, "torso", dtype=object)
lab[(vpart == 0) & (W[:, 2] > Zn_)] = "head"
legm = (vpart == 0) & (W[:, 2] < Zh_)
lab[legm & (W[:, 0] > 0)] = "leg.L"; lab[legm & (W[:, 0] <= 0)] = "leg.R"
lab[vpart == PART_NAMES.index("belt")] = "belt"
lab[np.isin(vpart, [PART_NAMES.index(p) for p in PART_NAMES if p.startswith("leaf")])] = "leaf"
GATE = {"head": ["head", "chest"], "torso": ["pelvis", "chest", "head"],
        "leg.L": ["pelvis", "thigh.L", "shin.L", "foot.L"], "leg.R": ["pelvis", "thigh.R", "shin.R", "foot.R"],
        "belt": ["pelvis", "chest"], "leaf": ["pelvis", "chest"]}
Wt = np.zeros((M.n, len(DEFORM)))
body_bones = [n for n in DEFORM if not n.startswith("vine")]
for n in body_bones:
    b = arm_data.bones[n]
    d = np.maximum(K.seg_dist(W, np.array(b.head_local), np.array(b.tail_local)), 0.003)
    gate = np.array([n in GATE[l_] for l_ in lab])
    Wt[:, J[n]] = (1.0 / d ** 4) * gate
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
for side in ("L", "R"):
    m = vs > 0
    m &= vside == (1 if side == "L" else -1)
    if not m.any():
        continue
    cv = curves[side]["curve"]
    VW = K.vine_weights(vs[m], cv.L, VINE_BONES, parent_s0=0.0)
    rows = np.nonzero(m)[0]
    Wt[rows] = 0.0
    Wt[rows, J["chest"]] = VW[:, 0]
    for k, nm in enumerate(VINE_B[side]):
        Wt[rows, J[nm]] = VW[:, k + 1]
for _ in range(3):
    Wt = 0.5 * Wt + 0.5 * M.neighbour_mean(Wt)
order = np.argsort(-Wt, axis=1)
keep = np.zeros_like(Wt, bool)
np.put_along_axis(keep, order[:, :3], True, axis=1)
Wt = np.where(keep & (Wt > 1e-4), Wt, 0.0)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "rule": ("body: proximity bands (label-gated bones, w = 1/d^4 to the bone segment, rig_unit.py); vines: "
                           "rigkit.vine_weights arc-length hats (<= 2 chain bones, root blends into chest); 3 neighbour-mean "
                           "passes; top-3; normalized")}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig

# =========================================================================== 7. PROPOSED idle
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
N = IDLE_FRAMES
TAU = 2 * math.pi


def idle_pose(t):
    q = {}
    q["chest"] = Euler((math.radians(IDLE_BREATH_DEG * math.sin(TAU * t)), 0.0,
                        math.radians(0.4 * math.sin(TAU * t + 1.1))), "XYZ").to_quaternion()
    q["head"] = Euler((math.radians(-IDLE_HEAD_DEG[0] * math.sin(TAU * t - 0.6)), 0.0,
                       math.radians(IDLE_HEAD_DEG[1] * math.sin(TAU * t + 0.4))), "XYZ").to_quaternion()
    for side, ph in (("L", 0.0), ("R", 2.1)):
        for k, nm in enumerate(VINE_B[side]):
            curl = K.vine_wave(t, k, VINE_BONES, IDLE_CURL[0], IDLE_CURL[1], IDLE_LAG, 1, ph) + \
                K.vine_wave(t, k, VINE_BONES, 0.25 * IDLE_CURL[0], 0.25 * IDLE_CURL[1], IDLE_LAG * 1.6, 2, ph + 0.7)
            sway = K.vine_wave(t, k, VINE_BONES, IDLE_SWAY[0], IDLE_SWAY[1], IDLE_LAG * 0.8, 1, ph + 1.6)
            q[nm] = Euler((math.radians(curl), 0.0, math.radians(sway)), "XYZ").to_quaternion()
    return q


act = bpy.data.actions.new("idle")
act.use_fake_user = True
K.assign_action(rig, act)
prevq = {}
KEYED = ["chest", "head"] + VINE_B["L"] + VINE_B["R"]
for f in range(1, N + 2):
    t = ((f - 1) / N) % 1.0
    q = idle_pose(t)
    for n in KEYED:
        qq = q[n]
        if n in prevq and prevq[n].dot(qq) < 0:
            qq.negate()
        prevq[n] = qq.copy()
        pose[n].rotation_quaternion = qq
        pose[n].keyframe_insert("rotation_quaternion", frame=f, group=n)
act.use_frame_range = True
act.frame_start, act.frame_end = 1, N + 1
act.use_cyclic = True

# ---- measure
feet_v = np.nonzero(np.isin(lab, ["leg.L", "leg.R"]) & (W[:, 2] < 0.10))[0]
vine_far = np.nonzero(vs > 0.9)[0]
not_vine = np.nonzero(vs <= 0)[0]
tip_v = {side: np.nonzero((vside == (1 if side == "L" else -1)) & (vs > curves[side]["curve"].L - 0.12))[0] for side in ("L", "R")}


def coords():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = low.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


rest = W.copy()
first = last = None
foot_slide = 0.0
min_z = 1e9
clear_min = 1e9
clear_frames = {}
ext_lo, ext_hi = np.full(3, 1e9), np.full(3, -1e9)
tip_path = {s_: [] for s_ in tip_v}
stretch = 0.0
for f in range(1, N + 2):
    scene.frame_set(f)
    C = coords()
    if f == 1:
        first = C
    if f == N + 1:
        last = C
    foot_slide = max(foot_slide, float(np.linalg.norm(C[feet_v] - rest[feet_v], axis=1).max()))
    min_z = min(min_z, float(C[:, 2].min()))
    ext_lo = np.minimum(ext_lo, C.min(0)); ext_hi = np.maximum(ext_hi, C.max(0))
    for s_, iv in tip_v.items():
        tip_path[s_].append(C[iv].mean(0))
    for pb in pose:
        stretch = max(stretch, abs(pb.length - pb.bone.length) / pb.bone.length)
    if (f - 1) % 6 == 0:
        kd_f = KDTree(len(not_vine))
        for i, v in enumerate(not_vine):
            kd_f.insert(C[v], i)
        kd_f.balance()
        dd = min(kd_f.find(C[v])[2] for v in vine_far)
        clear_frames[f] = round(dd, 4)
        clear_min = min(clear_min, dd)
tip_travel = {s_: round(float(max(np.linalg.norm(np.array(P) - np.array(P)[0], axis=1))), 4) for s_, P in tip_path.items()}
rep["idle"] = {"status": "PROPOSED - the artist judges", "action": "idle", "frames": N + 1, "cycle_frames": N,
               "cycle_s": round(N / K.FPS, 3), "keyed_bones": KEYED, "never_keyed": ["root", "pelvis", "thigh.*", "shin.*", "foot.*"],
               "seam_residual_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
               "feet_slide_mm": round(foot_slide * 1000, 6), "feet_verts": int(len(feet_v)),
               "min_z_m": round(min_z, 4), "bone_stretch_max_pct": round(stretch * 100, 6),
               "vine_tip_travel_m": tip_travel,
               "vine_clearance_min_m": round(clear_min, 4), "vine_clearance_by_frame": clear_frames,
               "vine_clearance_rule": "min distance, vine verts past s=0.9 vs every non-vine vert (body, head, legs, leaves, belt), every 6th frame",
               "extent": {"width": round(float(ext_hi[0] - ext_lo[0]), 4), "depth": round(float(ext_hi[1] - ext_lo[1]), 4),
                          "height": round(float(ext_hi[2] - ext_lo[2]), 4)},
               "motion": {"curl_deg_root_tip": IDLE_CURL, "sway_deg_root_tip": IDLE_SWAY, "lag_rad_per_bone": IDLE_LAG,
                          "breath_deg": IDLE_BREATH_DEG, "head_deg": IDLE_HEAD_DEG, "harmonics": [1, 2]}}
rig.animation_data.action = None
for pb in pose:
    pb.rotation_quaternion = (1, 0, 0, 0)
K.assign_action(rig, act)
scene.frame_set(1)
rig.animation_data.action = None
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_start, scene.frame_end = 1, N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local],
                 "length": round(b.length, 4)} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["vine_chains"] = {s_: {"bones": VINE_B[s_], "length": round(curves[s_]["curve"].L, 4),
                           "bone_length": round(curves[s_]["curve"].L / VINE_BONES, 4),
                           "local_x_axis": "the curl axis (idle rotates about local X = curl, local Z = sway)"} for s_ in VINE_B}
rig["conquest_rig"] = "archetype-minimal v1 + vine-chain (rigkit.VineCurve)"
low["conquest_clips"] = ["idle"]
low["conquest_clip_status"] = "idle PROPOSED; walk NOT AUTHORED (gait pending artist); no attacks"
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== skins (palette swap)
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "palette_files": pal_default["files"]}}
for skin in SKINS:
    if skin == "default":
        continue
    pal = PAL.load(UNIT, skin)
    counts = PAL.paint(me, pal)
    PAL.apply_material(mat, pal)
    out = OUT_RIGGED[:-6] + "__" + skin + ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True, relative_remap=False)
    _cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
    rep["skins"][skin] = {"file": out, "palette": PAL.table(pal), "palette_files": pal["files"],
                          "col_sha": hashlib.sha256(np.round(_cd, 5).tobytes()).hexdigest()[:16], "regions_faces": counts}
PAL.paint(me, pal_default); PAL.apply_material(mat, pal_default)
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "seconds")}))
print("IDLE", json.dumps(rep["idle"]))
sys.stdout.flush()
os._exit(0)
