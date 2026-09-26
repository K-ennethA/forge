"""Magmoo (lava serpent, 3 disjointed blob segments) -- fresh build of the two missing segments + assembly + chain rig.

    blender --background source-copies/newunit-magmoo.blend --factory-startup --python improve/magmoo_build.py -- \
        [--stop improved]                (geometry + regions + palette only: no rig -- fast look loop)
        [--outroot <dir>]                (write every output under <dir> instead of the project: the determinism re-run)
        [--skins default,obsidian]       (extra palette skins -> rigged/magmoo__<skin>.blend)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Artist (design/review-log.md, verbatim, binding): "magmoo missing pieces were never done, its essentially a serpent no
arms or legs and should be disjointed lava blobs that can form together to create one serpent creature or bounce around
separting itself into 3 parts head portion and upper, main body, and long tail, it can have its body on the floor and
head and tail in motion". Policies: natural scale (cell_fit report-only), palette skins, idle + walk only.

Source (read-only byte copy): ONE mesh 'Icosphere' (17,200 verts / 34,396 tris, no material -- the blend's only
material is Blender's unused default grey, so the lava colours are authored here, not derived). Its sculpt language:
a smooth blobby teardrop, the rounded end closed, the other end frayed into curling flame licks with two dorsal flame
fins -- a comet read. The rounded end is the FRONT (toward the head), the flame fringe trails toward the tail.

Pipeline:
  1. MAIN BODY = the source blob in its own sculpt frame (local +Z long axis = rounded end, local -Y = the dorsal fins,
     local X = the mirror axis) mapped to game axes: rounded end -> -Y (front), fins -> +Z, belly on the floor z = 0.
  2. HEAD+UPPER and LONG TAIL modelled fresh as SDFs (magmoo_sdf.py: smooth unions = blob fillets; round cones + flame
     licks in the body's own fin language), polygonised by marching tetrahedra.
  3. ONE FINISH for all three: voxel remesh (VOXEL) -> collapse decimation to the same triangle density per area.
  4. assembly: the upper segment in front of the body, the tail behind it; each placed along Y so the mesh-to-mesh gap
     equals GAP (measured, BVH). Main body on the floor, head raised, tail lying out long with a lifted flame tip.
  5. regions per segment (iso-contour cuts, the duskmaw rule: colour edges are cut lines, not per-face sawtooth):
     basalt CRUST plates / warm crust rim / glowing CRACKS (3D Voronoi cell borders F2 - F1, additively weighted +
     domain-warped so plates vary and cracks wobble) / hot FLAME tips (outward displacement off a Laplacian-smoothed
     copy, smoothing reach fixed in units -- the same rule on all three segments) / molten gap-facing ENDS (a flame-tier
     ring round a white-hot CORE) / EYES + MAW (upper only). Cracks are then recessed and plates domed (crust lumps),
     except at the floor contact, which stays flat on z = 0.
  6. molten BRIDGES: per gap BRIDGE_STRANDS tapered, sagging lava strands buried in both segments (own mesh object).
  7. chain rig: root (contract) -> three separable sub-chains parented straight to root: neck.0-3 + head (+ tongue),
     body.0-3, tail.0-(TAIL_BONES-1). Each segment is weighted ONLY to its own chain; the bridges blend across.
  8. clips (procedural, discrete FK on the rest chain; rotations about world Z keep every height = grounded body):
       idle  body at rest, cobra head sway + nod, lazy tail-tip wave, flame-tongue flick, magma glow pulse
       walk  in-place lateral undulation travelling head -> tail, amplitude growing down the tail (whip), head counter-
             yawed to stay forward, body re-fitted in place each frame (2D Procrustes on the body joints).
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K        # noqa: E402  (read-only use)
import palettes as PAL    # noqa: E402  (read-only use)
import magmoo_sdf as SD   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; lengths in source units = the main blob's own sculpt units, natural scale)
UNIT = "magmoo"
TRI_BUDGET = [30000, 50000]           # declared hero-tier window (review-log: "30-50k hero tier stands")
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
VOXEL = 0.03                          # "surface finish": the ONE voxel remesh size all three segments go through
SDF_STEP = 0.03                       # SDF sampling step for the two new segments
TRI_DENSITY = 300.0                   # "mesh detail": triangles per square unit of surface, same for every segment
GAP_UPPER = 0.30                      # "neck gap": mesh-to-mesh gap between the body and the head+upper segment
GAP_TAIL = 0.30                       # "tail gap": mesh-to-mesh gap between the body and the tail
# ---- head + upper segment (neck base on the floor in front of the body, S-neck rising, head facing -Y)
NECK_PTS = [(0, 0.0, 0.64), (0, -0.75, 0.74), (0, -1.35, 1.30), (0, -1.48, 2.15), (0, -1.18, 2.82),
            (0, -1.30, 3.22)]                            # "neck curve": floor run forward, then an S rising to the occiput
NECK_R = [0.78, 0.70, 0.62, 0.55, 0.50, 0.49]            # "neck thickness" at those points
BASE_PUDDLE = ((0, 0.0, 0.36), (0.80, 0.86, 0.38))       # lava puddle the neck rises from (centre, radii)
HEAD_PITCH_DEG = 14.0                 # "head tilt": snout pitched down from level
HEAD_SCALE = 1.25                     # "head size"
NECK_LICKS = [(0.36, 0.75), (0.55, 0.85), (0.74, 0.8)]   # "neck flame licks": (arc fraction up the neck, size)
HEAD_CREST = [(0.0, 0.15, 0.30, 1.0, 0.0), (0.24, 0.10, 0.24, 0.8, 0.35), (-0.24, 0.10, 0.24, 0.8, -0.35)]
#                                     "head crest": (head-local x, f, u root, size, outward splay) sweeping back
EYE_R = 0.13                          # "eye size" (glow zone radius around each eye socket)
MAW_T = 0.035                         # "lip glow": surface within this of the carved mouth slot burns
TONGUE = True                         # "flame tongue" (separate piece on its own bone; flicks in idle)
# ---- tail (lies on the floor, long, tapering, blob beads, dorsal flame licks, flame flick at the tip)
TAIL_LEN = 8.6                        # "tail length" (spine arc length)
TAIL_R0, TAIL_RTIP = 0.60, 0.07       # "tail thickness" at the base / at the tip
TAIL_TAPER = 0.85                     # taper curve exponent (1 = linear)
TAIL_FLAT = 0.82                      # tail cross-section height / width (lies flat on the floor)
TAIL_BEND_DEG = 16.0                  # "rest S-curve": lateral heading swing of the resting tail
TAIL_CURL_DEG = 26.0                  # "tail tip lift": the last stretch pitches up to this
TAIL_CURL_FROM = 0.74                 # ... starting at this fraction of the tail
BEAD_AMP, BEAD_LEN = 0.07, 0.95       # "blob beads": radius ripple along the tail (lava blobs) and its spacing
TAIL_LICKS = [0.9, 2.2, 3.5, 4.8, 6.1]  # "tail flame licks": arc positions of the dorsal licks
TAIL_LICK_SIZE = 1.25                 # "tail lick size" (x the local tail thickness)
# ---- palette regions
CRACK_CELL = 0.60                     # "crust plate size" (Voronoi seed spacing)
CRACK_W = 0.070                       # "crack width" (F2 - F1 below this = glowing crack)
HOT_W = 0.150                         # "warm rim": crust this close to a crack is warm-tinted
CRACK_DEPTH = 0.022                   # "crack depth": cracks recess inward ...
PLATE_DOME = 0.018                    # ... and crust plates dome outward (crust lumps)
FLOOR_KEEP = 0.06                     # no crust displacement below this height (the floor contact stays flat at z = 0)
TIP_SMOOTH_R = 0.42                   # flame-tip field: smoothing reach of the reference copy (units; passes = (R / mean edge)^2)
TIP_T = 0.075                         # "hot tip size": outward displacement off the smoothed copy above this glows
END_W = 0.62                          # "molten end size": zone radius round each gap-facing end
END_T = 0.30                          # molten end: outer ring (hot flame tier) from this facing x closeness ...
END_T2 = 0.58                         # ... white-hot core inside this
CRACK_WARP = 0.08                     # "crack wobble": sinusoidal domain warp of the Voronoi (units)
CRACK_VARY = 0.16                     # "plate size variety": additive Voronoi weights up to this (units)
LICK_SHARP = 1.5                      # "flame point": lick taper exponent (higher = thinner sooner, pointier)
# ---- molten bridges across each gap
BRIDGE_STRANDS = [(0.0, -0.10, 0.13), (0.26, 0.16, 0.10), (-0.24, 0.10, 0.09)]   # (lateral, vertical, end radius)
BRIDGE_PINCH = 0.42                   # "strand pinch": middle radius / end radius (stretched taffy)
BRIDGE_SAG = 0.07                     # "drip sag" at mid-gap
BRIDGE_SINK = 0.16                    # how deep each strand end buries into its segment
BRIDGE_SIDES, BRIDGE_RINGS = 8, 14
# ---- rig
NECK_BONES, BODY_BONES, TAIL_BONES = 4, 4, 12
# ---- clips
IDLE_FRAMES = 96                      # idle loop (24 fps -> 4 s)
IDLE_NECK_SWAY_DEG = 11.0             # "cobra sway": upper segment yaw about the neck base
IDLE_NOD_DEG = 5.0                    # "head nod"
IDLE_TAIL_DEG = 16.0                  # "lazy tail tip": heading wave amplitude at the tail tip
IDLE_GLOW_PULSE = 0.35                # magma glow flare (emission strength x (1 + this))
IDLE_TONGUE = (1.0, 1.9)              # tongue length scale: rest -> flicked out (two flicks per loop)
WALK_FRAMES = 48                      # slither cycle (24 fps -> 2 s)
WALK_WAVELENGTH = 8.0                 # "wave length" of the lateral undulation (units of arc)
WALK_AMP_BODY_DEG = 11.0              # heading swing through the body
WALK_AMP_TAIL_DEG = 40.0              # heading swing at the tail tip ("tail whipping wider")
WALK_AMP_NECK_DEG = 6.0               # heading swing through the neck
WALK_HEAD_FOLLOW = 0.25               # head heading follows this fraction of the neck (rest = locked forward)
WALK_GLOW_PULSE = 0.18

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STOP = argv[argv.index("--stop") + 1] if "--stop" in argv else None
OUTROOT = argv[argv.index("--outroot") + 1] if "--outroot" in argv else ROOT
SKINS = argv[argv.index("--skins") + 1].split(",") if "--skins" in argv else ["default"]
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(OUTROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(OUTROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(OUTROOT, "rigged", UNIT + ".glb")
for d_ in (os.path.dirname(OUT_IMPROVED), os.path.dirname(OUT_RIGGED)):
    os.makedirs(d_, exist_ok=True)
SEGS = ["upper", "body", "tail"]
report = {"unit": UNIT, "source": bpy.data.filepath, "tier": "hero", "tri_budget": TRI_BUDGET, "yaw_fix_deg": 0.0,
          "overrides": OVERRIDES, "segments": {}}
scene = bpy.context.scene
TAU = 2 * math.pi


def sha(*arrs):
    h = hashlib.sha256()
    for a in arrs:
        h.update(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


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
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V, float).tolist(), [], [list(map(int, f)) for f in F])
    me.update()
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    return ob


def evaluated_arrays(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me_l = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    V, F = mesh_arrays(me_l)
    bpy.data.meshes.remove(me_l)
    return V, F


def islands(V, F):
    n = len(V)
    par = np.arange(n)

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for f in F:
        r0 = find(f[0])
        for v in f[1:]:
            r1 = find(v)
            if r1 != r0:
                par[r1] = r0
    return np.array([find(i) for i in range(n)])


def keep_main_island(V, F, min_frac):
    roots = islands(V, F)
    lab, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_lab = cnt >= min_frac * len(V)
    keep_v = keep_lab[inv]
    remap = -np.ones(len(V), dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum())}


def area_of(V, F):
    V = np.asarray(V)
    a = 0.0
    for f in F:
        for j in range(1, len(f) - 1):
            a += 0.5 * np.linalg.norm(np.cross(V[f[j]] - V[f[0]], V[f[j + 1]] - V[f[0]]))
    return a


def finish(name, V, F, voxel=None, density=None, min_frac=0.02):
    """THE shared finish: voxel remesh -> collapse decimation to density x area -> main shell."""
    voxel = voxel or VOXEL
    density = density or TRI_DENSITY
    t = time.time()
    tmp = new_obj(name + "_rm", V, F)
    rm = tmp.modifiers.new("vox", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = voxel; rm.use_smooth_shade = False
    RV, RF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    RV, RF, specks = keep_main_island(RV, RF, min_frac)
    area = area_of(RV, RF)
    target = int(round(area * density))
    rtris = sum(len(f) - 2 for f in RF)
    tmp = new_obj(name + "_dec", RV, RF)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / rtris)
    LV, LF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV, LF, specks2 = keep_main_island(LV, LF, min_frac)
    return np.asarray(LV), [list(f) for f in LF], {"voxel": voxel, "remesh_tris": rtris, "area": round(area, 4),
                                                   "target_tris": target, "tris": sum(len(f) - 2 for f in LF),
                                                   "specks": [specks, specks2], "seconds": round(time.time() - t, 1)}


# =========================================================================== 1. main body = the source blob
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
src = bpy.data.objects["Icosphere"]
SV_local, SF = mesh_arrays(src.data)
report["source_mesh"] = {"object": src.name, "verts": len(SV_local), "tris": tri_count(src.data),
                         "object_rotation_deg": [round(math.degrees(a), 3) for a in src.rotation_euler],
                         "materials": [s.material.name if s.material else None for s in src.material_slots],
                         "blend_materials": [m.name for m in bpy.data.materials],
                         "colour_note": "no material on the mesh (the blend's only material is the unused default grey): "
                                        "crust/magma colours are authored in palettes/magmoo, not derived"}
# sculpt frame -> game frame: local (x, y, z) -> (-x, -z, -y): rounded end (+Z) to the front (-Y), dorsal fins (-Y) up
MAP = np.array([[-1.0, 0, 0], [0, 0, -1.0], [0, -1.0, 0]])
assert abs(np.linalg.det(MAP) - 1.0) < 1e-9
BV0 = SV_local @ MAP.T
lo, hi = BV0.min(0), BV0.max(0)
BV0 = BV0 - np.array([(lo[0] + hi[0]) / 2, 0.0, lo[2]])
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
report["source_mesh"]["frame_map"] = "local (x, y, z) -> game (-x, -z, -y); the artist's object rotation (%s deg) is " \
    "dropped: the sculpt's own axes are the symmetry frame (local x range %.3f..%.3f)" % (
        report["source_mesh"]["object_rotation_deg"], SV_local[:, 0].min(), SV_local[:, 0].max())
BODY_SRC = BV0.copy()                                  # the source blob, game-framed, floor at 0 (for the before/after)
bV, bF, bfin = finish("body", BV0, SF)
bV[:, 2] -= bV[:, 2].min()
report["segments"]["body"] = {"finish": bfin, "source_tris": report["source_mesh"]["tris"]}
# body spine: along Y through the core. Core height = mid-z of the mid-length slab without the fins.
by0, by1 = float(bV[:, 1].min()), float(bV[:, 1].max())
mid = (bV[:, 1] > by0 + 0.35 * (by1 - by0)) & (bV[:, 1] < by0 + 0.55 * (by1 - by0)) & (np.abs(bV[:, 0]) < 0.25)
BODY_ZC = float(np.percentile(bV[mid, 2], 5) + np.percentile(bV[mid, 2], 60)) / 2
front_m = bV[:, 1] < by0 + 0.6
BODY_ZF = float((bV[front_m, 2].min() + bV[front_m, 2].max()) / 2)
print("BODY", json.dumps({"y": [by0, by1], "zc": BODY_ZC, "z_front_mid": BODY_ZF, **bfin}))


# =========================================================================== 2a. head + upper segment (SDF)
def head_frame(pitch_deg):
    p = math.radians(pitch_deg)
    X = np.array([1.0, 0, 0]); Fw = np.array([0, -math.cos(p), -math.sin(p)]); U = np.array([0, -math.sin(p), math.cos(p)])
    return X, Fw, U


NP = np.array(NECK_PTS, float)
OCC = NP[-1]
HX, HF, HU = head_frame(HEAD_PITCH_DEG)
HFRAME = np.stack([HX, HF, HU])
hs = HEAD_SCALE


def hpt(x, f, u):
    return OCC + hs * (x * HX + f * HF + u * HU)


HEAD_ELLIPSOIDS = [  # (name, head-local centre (x, f, u), radii (lat, fwd, up))
    ("cranium", (0, 0.45, 0.02), (0.50, 0.60, 0.36)),
    ("snout", (0, 1.05, -0.06), (0.34, 0.48, 0.24)),
    ("jaw", (0, 0.80, -0.24), (0.40, 0.62, 0.20)),
    ("cheek.L", (0.32, 0.40, -0.12), (0.20, 0.30, 0.22)), ("cheek.R", (-0.32, 0.40, -0.12), (0.20, 0.30, 0.22)),
    ("brow.L", (0.26, 0.64, 0.25), (0.16, 0.32, 0.11)), ("brow.R", (-0.26, 0.64, 0.25), (0.16, 0.32, 0.11))]
MOUTH = ((0, 1.04, -0.13), (0.37, 0.64, 0.05))             # the mouth slot (subtracted): open glowing maw line
EYES = [(0.37, 0.73, 0.12), (-0.37, 0.73, 0.12)]            # eye sockets (subtracted spheres) = the glowing eyes
EYE_SOCKET = 0.085
SNOUT_TIP = (0, 1.55, -0.08)


def lick_cones(root, ctrl, tip, r0, n=10, rtip=0.014):
    pts = SD.bezier(root, ctrl, tip, n)
    u = np.linspace(0.0, 1.0, n)
    rr = rtip + (r0 - rtip) * (1 - u) ** LICK_SHARP
    return [(pts[i], pts[i + 1], rr[i], rr[i + 1]) for i in range(n - 1)]


def add_cones(G, cones, k, fmap=None):
    for a, b, r1, r2 in cones:
        lo_ = np.minimum(a, b) - max(r1, r2); hi_ = np.maximum(a, b) + max(r1, r2)
        if fmap is None:
            G.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), lo_, hi_, k)
        else:
            G.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(fmap(P), a, b, r1, r2), fmap(lo_[None], inv=True)[0],
                    fmap(hi_[None], inv=True)[0], k)


t_up = time.time()
neck_poly = SD.resample(SD.catmull(NP, 12), 40)
neck_s = SD.arclen(neck_poly)
knot_s = SD.arclen(NP)
neck_r = np.interp(neck_s / neck_s[-1], knot_s / knot_s[-1], NECK_R)
G = SD.Grid((-1.4, -3.8, -0.05), (1.4, 1.4, 4.6), SDF_STEP, band=0.09)
add_cones(G, [(neck_poly[i], neck_poly[i + 1], neck_r[i], neck_r[i + 1]) for i in range(len(neck_poly) - 1)], 0.03)
pc, pr = BASE_PUDDLE
G.apply(lambda P: SD.sd_ellipsoid(P, pc, pr), np.array(pc) - pr, np.array(pc) + pr, 0.22)
for nm, c, r in HEAD_ELLIPSOIDS:
    cw = hpt(*c); rr = np.array(r) * hs
    G.apply(lambda P, cw=cw, rr=rr: SD.sd_ellipsoid(P, cw, rr, HFRAME), cw - rr.max(), cw + rr.max(), 0.12)
UPPER_LICKS = []   # flame licks (root, control, tip, root radius) -- the body's dorsal-fin language, trailing backward
ntan = np.gradient(neck_poly, axis=0); ntan /= np.linalg.norm(ntan, axis=1)[:, None]
for frac, sz in NECK_LICKS:
    i = int(round(frac * (len(neck_poly) - 1)))
    t_ = ntan[i]; b_ = np.array([0.0, t_[2], -t_[1]])          # dorsal side of the neck (up on the floor run, back on the rise)
    b_ /= np.linalg.norm(b_)
    root = neck_poly[i] + b_ * 0.55 * neck_r[i]
    UPPER_LICKS.append((root, root + b_ * 0.50 * sz - t_ * 0.30 * sz, root + b_ * 0.42 * sz - t_ * 0.85 * sz, 0.16 * sz))
for x_, f_, u_, sz, spl in HEAD_CREST:
    root = hpt(x_, f_, u_)
    side = HX * spl
    UPPER_LICKS.append((root, root + hs * sz * (-0.55 * HF + 0.50 * HU + 0.4 * side), root + hs * sz * (-1.05 * HF + 0.30 * HU + 0.8 * side),
                        0.19 * sz * hs))
for root, ctrl, tip, r0 in UPPER_LICKS:
    add_cones(G, lick_cones(root, ctrl, tip, r0), 0.10)
G.floor(0.0)                                            # rests flat on the floor
mc, mr = MOUTH
mcw = hpt(*mc); mrr = np.array(mr) * hs
G.apply(lambda P: SD.sd_ellipsoid(P, mcw, mrr, HFRAME), mcw - mrr.max(), mcw + mrr.max(), 0.03, mode="subtract")
EYE_W = [hpt(*e) for e in EYES]
for ew in EYE_W:
    G.apply(lambda P, ew=ew: SD.sd_sphere(P, ew, EYE_SOCKET * hs), ew - 0.1, ew + 0.1, 0.02, mode="subtract")
uV0, uT0 = SD.polygonise(G)
def mt_audit(V, T, G_):
    e = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    _, c = np.unique(e, axis=0, return_counts=True)
    out = {"mt_verts": len(V), "mt_tris": len(T), "open_edges": int((c == 1).sum()), "nonmanifold_edges": int((c > 2).sum()),
           "grid": G_.n.tolist(), "nan": int(np.isnan(G_.F).sum())}
    assert out["open_edges"] == 0 and out["nan"] == 0, out
    return out


mt_up = mt_audit(uV0, uT0, G)
uV, uF, ufin = finish("upper", uV0, uT0)
uV[:, 2] = np.maximum(uV[:, 2], 0.0)                   # voxel rounding under the flat floor cut -> back onto the floor
report["segments"]["upper"] = {"finish": ufin, "sdf": {"step": SDF_STEP, **mt_up,
                                                      "seconds": round(time.time() - t_up, 1)}}
# flame tongue: its own island (own bone), finer voxel so a 0.1-wide tongue survives
TONGUE_PTS = None
if TONGUE:
    tr = hpt(0, 0.92, -0.13); tm = hpt(0, 1.62, -0.15)
    tips = [hpt(0.11, 1.98, -0.09), hpt(-0.11, 1.98, -0.09)]
    Gt = SD.Grid(np.minimum.reduce([tr, tm] + tips) - 0.15, np.maximum.reduce([tr, tm] + tips) + 0.15, 0.012, band=0.04)
    add_cones(Gt, [(tr, tm, 0.055, 0.04)], 0.0)
    for tp in tips:
        add_cones(Gt, lick_cones(tm, (tm + tp) / 2 + HU * 0.03, tp, 0.04, n=6, rtip=0.008), 0.02)
    tV0, tT0 = SD.polygonise(Gt)
    tV, tF, tfin = finish("tongue", tV0, tT0, voxel=0.012, density=TRI_DENSITY)
    TONGUE_PTS = (tr, tm, np.mean(tips, 0))
    report["segments"]["upper"]["tongue"] = tfin
print("UPPER", json.dumps(ufin))


# =========================================================================== 2b. long tail (SDF)
def tail_spine(n=200):
    s = np.linspace(0.0, TAIL_LEN, n)
    ds = s[1] - s[0]
    psi = np.radians(TAIL_BEND_DEG) * np.sin(TAU * s / TAIL_LEN * 0.8 + 0.3)
    th = np.radians(TAIL_CURL_DEG) * smoothstep(TAIL_CURL_FROM * TAIL_LEN, TAIL_LEN, s) ** 1.5
    d = np.stack([np.sin(psi) * np.cos(th), np.cos(psi) * np.cos(th), np.sin(th)], 1)
    P = np.vstack([[0, 0, 0], np.cumsum(d[:-1] * ds, 0)])
    r = TAIL_RTIP + (TAIL_R0 - TAIL_RTIP) * (1 - s / TAIL_LEN) ** TAIL_TAPER
    r = r * (1 + BEAD_AMP * np.sin(TAU * s / BEAD_LEN) * (1 - s / TAIL_LEN))
    return s, P, d, r


t_tl = time.time()
ts, tP, tD, tr_ = tail_spine()
tP[:, 2] += tr_[0] * TAIL_FLAT                         # the base sits on the floor
# body of the tail in a vertically UN-flattened space: z' = z / TAIL_FLAT (flat on the floor, round in section)
zc_ = tP[:, 2].copy()
TAIL_SPINE = tP.copy()


def unflat(P, inv=False):
    Q = np.array(P, float, copy=True)
    Q[:, 2] = Q[:, 2] * TAIL_FLAT if inv else Q[:, 2] / TAIL_FLAT
    return Q


# spine centre in unflattened space: the bottom (centre - r) stays on the floor until the curl lifts it
spine_u = tP.copy()
lift = tP[:, 2] - (tr_[0] * TAIL_FLAT)                  # curl lift above the resting height
spine_u[:, 2] = tr_ + lift / TAIL_FLAT
TAIL_SPINE[:, 2] = spine_u[:, 2] * TAIL_FLAT
idx = np.linspace(0, len(ts) - 1, 56).round().astype(int)
Gt2 = SD.Grid((-2.2, -0.9, -0.05), (2.2, TAIL_LEN + 1.2, 2.6), SDF_STEP, band=0.09)
add_cones(Gt2, [(spine_u[idx[i]], spine_u[idx[i + 1]], tr_[idx[i]], tr_[idx[i + 1]]) for i in range(len(idx) - 1)], 0.02,
          fmap=unflat)
end = TAIL_SPINE[-1]; tan = tD[-1]; up = np.array([0, 0, 1.0])
TAIL_TIP_FLAME = (end - tan * 0.05, end + tan * 0.45 + up * 0.18, end + tan * 0.62 + up * 0.52, tr_[-1] * 1.35)
add_cones(Gt2, lick_cones(*TAIL_TIP_FLAME), 0.05)
for sl in TAIL_LICKS:
    i = int(np.searchsorted(ts, sl))
    sc = TAIL_LICK_SIZE * tr_[i] / TAIL_R0
    top = TAIL_SPINE[i] + up * (tr_[i] * TAIL_FLAT * 0.78)
    side = np.cross(tD[i], up)
    add_cones(Gt2, lick_cones(top, top + tD[i] * 0.55 * sc + up * 0.42 * sc, top + tD[i] * 0.98 * sc + up * 0.30 * sc, 0.15 * sc), 0.08)
Gt2.floor(0.0)
tV0, tT0 = SD.polygonise(Gt2)
lV, lF, lfin = finish("tail", tV0, tT0)
lV[:, 2] = np.maximum(lV[:, 2], 0.0)                   # voxel rounding under the flat floor cut -> back onto the floor
report["segments"]["tail"] = {"finish": lfin, "sdf": {"step": SDF_STEP, **mt_audit(tV0, tT0, Gt2),
                                                     "seconds": round(time.time() - t_tl, 1)},
                              "spine_arc_length": TAIL_LEN}
print("TAIL", json.dumps(lfin))


# =========================================================================== 3. assembly: gaps measured, placed along Y
def min_gap(VA, FA, VB, FB):
    bA = BVHTree.FromPolygons(VA.tolist(), FA); bB = BVHTree.FromPolygons(VB.tolist(), FB)
    dA = min(bB.find_nearest(Vector(p))[3] for p in VA)
    dB = min(bA.find_nearest(Vector(p))[3] for p in VB)
    return float(min(dA, dB))


def place(V, F, sign, target, extra=None):
    """translate V along Y (sign -1 = in front of the body, +1 = behind) until the mesh gap to the body == target."""
    y0 = (by0 - V[:, 1].max()) if sign < 0 else (by1 - V[:, 1].min())
    shift = y0 + sign * target
    hist = []
    for _ in range(6):
        W = V + np.array([0, shift, 0])
        g = min_gap(W, F, bV, bF)
        hist.append(round(g, 5))
        if abs(g - target) < 0.002:
            break
        shift += sign * (target - g)
    return shift, hist


t_pl = time.time()
UP_SHIFT, up_hist = place(uV, uF, -1, GAP_UPPER)
TL_SHIFT, tl_hist = place(lV, lF, +1, GAP_TAIL)
S_UP = np.array([0, UP_SHIFT, 0]); S_TL = np.array([0, TL_SHIFT, 0])
uV = uV + S_UP; lV = lV + S_TL
if TONGUE:
    tV = tV + S_UP
    TONGUE_PTS = tuple(p + S_UP for p in TONGUE_PTS)
neck_poly = neck_poly + S_UP; OCC_W = OCC + S_UP; EYE_W = [e + S_UP for e in EYE_W]
MOUTH_W = (mcw + S_UP, mrr)
SNOUT_W = hpt(*SNOUT_TIP) + S_UP
TAIL_SPINE = TAIL_SPINE + S_TL
report["assembly"] = {"gap_iterations": {"upper": up_hist, "tail": tl_hist}, "seconds": round(time.time() - t_pl, 1)}

# =========================================================================== final centring (contract feet_origin)
ALLV = np.vstack([uV, bV, lV] + ([tV] if TONGUE else []))
lo_a, hi_a = ALLV.min(0), ALLV.max(0)
CENTER = np.array([(lo_a[0] + hi_a[0]) / 2, (lo_a[1] + hi_a[1]) / 2, lo_a[2]])
uV -= CENTER; bV -= CENTER; lV -= CENTER
BODY_SRC -= CENTER
if TONGUE:
    tV -= CENTER
    TONGUE_PTS = tuple(p - CENTER for p in TONGUE_PTS)
neck_poly -= CENTER; OCC_W = OCC_W - CENTER; EYE_W = [e - CENTER for e in EYE_W]
MOUTH_W = (MOUTH_W[0] - CENTER, MOUTH_W[1]); SNOUT_W = SNOUT_W - CENTER
TAIL_SPINE = TAIL_SPINE - CENTER
BODY_Y = (by0 - CENTER[1], by1 - CENTER[1])
BODY_ZC -= CENTER[2]; BODY_ZF -= CENTER[2]
BODY_X = -float(CENTER[0])                             # the body was built centred on x = 0
BODY_SPINE = np.array([[BODY_X, BODY_Y[0], BODY_ZF], [BODY_X, BODY_Y[1], BODY_ZC]])
report["assembly"]["centre_shift"] = CENTER.round(5).tolist()

# gap axes + the molten-end anchors (ray from the gap midpoint onto each segment's surface)
SEGV = {"upper": uV, "body": bV, "tail": lV}
SEGF = {"upper": uF, "body": bF, "tail": lF}
BVH = {k: BVHTree.FromPolygons(SEGV[k].tolist(), SEGF[k]) for k in SEGS}
GAPS = {}
for gname, a, b, pa, pb in (("upper|body", "upper", "body", neck_poly[0], np.array([BODY_X, BODY_Y[0], neck_poly[0][2]])),
                            ("body|tail", "body", "tail", np.array([BODY_X, BODY_Y[1], TAIL_SPINE[0][2]]), TAIL_SPINE[0])):
    # horizontal gap axes at the NEW segment's base-centre height (both new segments rest on the floor facing a body end)
    # spine end of A -> spine start of B; each surface hit is cast FROM the other segment's spine point (outside the
    # segment being hit), so a spine point buried inside its own blob never hits its own back faces
    d_ab = (pb - pa) / np.linalg.norm(pb - pa)
    far = 50.0                                         # cast from far outside on the OTHER side: first hit = the facing surface
    hitA = BVH[a].ray_cast(Vector(pa + d_ab * far), Vector(-d_ab))[0]
    hitB = BVH[b].ray_cast(Vector(pa - d_ab * far), Vector(d_ab))[0]
    assert hitA is not None and hitB is not None, "gap axis misses a segment: %s pa %s pb %s hits %s %s bboxA %s bboxB %s" % (
        gname, pa, pb, hitA, hitB, SEGV[a].min(0).round(3), SEGV[b].max(0).round(3))
    mid_ = (np.array(hitA) + np.array(hitB)) / 2
    GAPS[gname] = {"a": a, "b": b, "axis": d_ab, "mid": mid_, "hitA": np.array(hitA), "hitB": np.array(hitB),
                   "axial_gap": float(np.linalg.norm(np.array(hitB) - np.array(hitA)))}
report["assembly"]["gaps"] = {k: {"mesh_gap_min": round(min_gap(SEGV[v["a"]], SEGF[v["a"]], SEGV[v["b"]], SEGF[v["b"]]), 4),
                                  "axial_gap_on_spine": round(v["axial_gap"], 4), "axis": v["axis"].round(4).tolist()}
                              for k, v in GAPS.items()}
print("GAPS", json.dumps(report["assembly"]["gaps"]))


# =========================================================================== 5. regions (iso-contour cuts per segment)
REG = ["crust", "crust_hot", "crack", "flame", "core", "maw", "eye"]
R_ = {n: i for i, n in enumerate(REG)}
CUT_SNAP = 0.15


def vertex_normals(V, F):
    V = np.asarray(V); T = np.asarray([f for f in F if len(f) == 3])
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, T[:, k], n)
    return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


def edges_of(F):
    E = set()
    for f in F:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            E.add((min(a, b), max(a, b)))
    return np.array(sorted(E))


def smooth_copy(V, E, iters, lam=0.5):
    n = len(V)
    deg = np.bincount(E.ravel(), minlength=n).astype(float)
    X = V.copy()
    for _ in range(iters):
        S = np.zeros_like(X)
        for k in range(3):
            S[:, k] = np.bincount(E[:, 0], X[E[:, 1], k], minlength=n) + np.bincount(E[:, 1], X[E[:, 0], k], minlength=n)
        X = X + lam * (S / np.maximum(deg, 1)[:, None] - X)
    return X


def fps_seeds(V, spacing):
    """deterministic farthest-point seeds (start at the vertex with the largest y, ties -> lowest index)."""
    n_seed = 1
    d = np.full(len(V), np.inf)
    seeds = [int(np.argmax(V[:, 1]))]
    while True:
        d = np.minimum(d, np.linalg.norm(V - V[seeds[-1]], axis=1))
        j = int(np.argmax(d))
        if d[j] < spacing:
            break
        seeds.append(j)
        n_seed += 1
    return V[seeds]


def crack_warp(V):
    """deterministic sinusoidal domain warp (incommensurate frequencies ~ 1 / plate size): wobbly crack lines."""
    k = 2 * math.pi / CRACK_CELL
    W = np.stack([np.sin(k * 0.71 * V[:, 1] + 1.3) + np.sin(k * 1.13 * V[:, 2] + 0.2),
                  np.sin(k * 0.83 * V[:, 2] + 2.1) + np.sin(k * 1.29 * V[:, 0] + 0.7),
                  np.sin(k * 0.67 * V[:, 0] + 0.4) + np.sin(k * 1.07 * V[:, 1] + 2.9)], 1)
    return V + 0.5 * CRACK_WARP * W


def voronoi_f2f1(V, seeds):
    """additively weighted Voronoi (seed i's distance minus a hashed weight in [0, CRACK_VARY]): varied plate sizes."""
    Vw = crack_warp(V)
    best = np.full(len(V), np.inf); second = np.full(len(V), np.inf)
    for i, s in enumerate(crack_warp(seeds)):
        w = CRACK_VARY * ((math.sin((i + 1) * 12.9898) * 43758.5453) % 1.0)
        d = np.linalg.norm(Vw - s, axis=1) - w
        second = np.where(d < best, best, np.minimum(second, d))
        best = np.minimum(best, d)
    return second - best


def ellipsoid_field(P, c, r, frame):
    return SD.sd_ellipsoid(P, c, r, frame)


SEGDATA = {}
for seg in SEGS:
    t = time.time()
    V = SEGV[seg]; F = SEGF[seg]
    E = edges_of(F)
    N = vertex_normals(V, F)
    mean_edge = float(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1).mean())
    tip_iters = int(round((TIP_SMOOTH_R / mean_edge) ** 2))
    Vs = smooth_copy(V, E, tip_iters)
    tipf = np.einsum("ij,ij->i", V - Vs, N)
    seeds = fps_seeds(V, CRACK_CELL)
    crf = voronoi_f2f1(V, seeds)
    # no cracks inside the eye / maw zones (they glow on their own)
    eyed = np.min([np.linalg.norm(V - e, axis=1) for e in EYE_W], axis=0) if seg == "upper" else np.full(len(V), 9.0)
    mawf = ellipsoid_field(V, MOUTH_W[0], MOUTH_W[1] * np.array([1.0, 1.0, 1.0]), HFRAME) if seg == "upper" else np.full(len(V), 9.0)
    crf = crf + 1.0 * smoothstep(EYE_R * 2.0, EYE_R * 1.2, eyed) + 1.0 * smoothstep(0.16, 0.08, mawf)
    # molten end: facing the neighbour x closeness to the end anchor
    endf = np.full(len(V), -1.0)
    for gname, g in GAPS.items():
        if seg == g["a"]:
            anchor, dirv = g["hitA"], g["axis"]
        elif seg == g["b"]:
            anchor, dirv = g["hitB"], -g["axis"]
        else:
            continue
        fac = N @ dirv
        clo = 1.0 - np.linalg.norm(V - anchor, axis=1) / END_W
        endf = np.maximum(endf, np.minimum(fac, clo))      # > END_T only where BOTH facing and close
    SEGDATA[seg] = {"V": V, "F": F, "fields": {"tip": tipf, "cr": crf, "eye": eyed, "maw": mawf, "end": endf},
                    "seeds": len(seeds), "tip_iters": tip_iters, "mean_edge": round(mean_edge, 4)}
    print("FIELDS", seg, json.dumps({"tip_p50_p99_max": [round(float(np.percentile(tipf, q)), 4) for q in (50, 99)] + [round(float(tipf.max()), 4)],
                                     "seeds": len(seeds), "tip_iters": tip_iters, "sec": round(time.time() - t, 1)}))


def cut_segment(seg):
    D = SEGDATA[seg]
    V, F, FLD = D["V"], D["F"], D["fields"]
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    bm.verts.index_update()
    LAY = {k: bm.verts.layers.float.new(k) for k in FLD}
    for v in bm.verts:
        for k, arr in FLD.items():
            v[LAY[k]] = float(arr[v.index])

    def iso_cut(key, tau):
        L = LAY[key]
        eps = 1e-6 * max(1.0, abs(tau))
        side = lambda v: 0 if abs(v[L] - tau) <= eps else (1 if v[L] > tau else -1)
        for e in bm.edges:
            a, b = e.verts
            if side(a) * side(b) < 0:
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < CUT_SNAP:
                    a[L] = tau
                elif tt > 1 - CUT_SNAP:
                    b[L] = tau
        cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0]
        for e in cuts:
            a, b = e.verts
            tt = (tau - a[L]) / (b[L] - a[L])
            vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
            _, nv = bmesh.utils.edge_split(e, a, tt)
            for k in LAY:
                nv[LAY[k]] = vals[k]
            nv[L] = tau
        pairs = []
        for f in bm.faces:
            vs = list(f.verts)
            sides = [side(v) for v in vs]
            if 1 in sides and -1 in sides:
                cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
                if len(cv) == 2:
                    pairs.append(cv)
        for cv in pairs:
            bmesh.ops.connect_verts(bm, verts=cv)
        big = [f for f in bm.faces if len(f.verts) > 3]
        if big:
            bmesh.ops.triangulate(bm, faces=big)
        return {"field": key, "tau": tau, "edge_splits": len(cuts), "face_connects": len(pairs)}
    cuts = [("cr", CRACK_W), ("cr", HOT_W), ("tip", TIP_T), ("end", END_T), ("end", END_T2)]
    if seg == "upper":
        cuts += [("eye", EYE_R), ("maw", MAW_T)]
    log = [iso_cut(k, tau) for k, tau in cuts]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.faces.index_update()
    V2 = np.array([v.co[:] for v in bm.verts])
    F2 = [[v.index for v in f.verts] for f in bm.faces]
    VF = {k: np.array([v[LAY[k]] for v in bm.verts]) for k in LAY}
    bm.free()
    FV = {k: np.array([np.mean(VF[k][f]) for f in F2]) for k in VF}
    rid = np.full(len(F2), R_["crust"], dtype=np.int32)
    rid[FV["cr"] < HOT_W] = R_["crust_hot"]
    rid[FV["cr"] < CRACK_W] = R_["crack"]
    rid[FV["tip"] > TIP_T] = R_["flame"]
    rid[FV["end"] > END_T] = R_["flame"]
    rid[FV["end"] > END_T2] = R_["core"]
    if seg == "upper":
        rid[FV["maw"] < MAW_T] = R_["maw"]
        rid[FV["eye"] < EYE_R] = R_["eye"]
    # crust lumps: cracks recess, plates dome (0 at the crack line: continuous)
    Nn = vertex_normals(V2, F2)
    cr = VF["cr"]
    disp = -CRACK_DEPTH * np.clip(1 - cr / CRACK_W, 0, 1) + PLATE_DOME * smoothstep(CRACK_W, CRACK_CELL * 0.5, cr)
    disp = disp * smoothstep(0.0, FLOOR_KEEP, V2[:, 2])       # the floor contact stays flat on z = 0
    V2 = V2 + Nn * disp[:, None]
    straddle = {}
    for k, tau in cuts:
        vals = [VF[k][f] for f in F2]
        straddle["%s=%.3f" % (k, tau)] = int(sum(1 for v in vals if v.min() < tau - 1e-5 and v.max() > tau + 1e-5))
    return V2, F2, rid, {"cuts": log, "straddling_faces_after_cut": straddle}


t_cut = time.time()
SEGOBJ = {}
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
pal_default = PAL.load(UNIT, "default")
PAL.apply_material(mat, pal_default)


def glow_tiers(pal):
    """emission_scale ranks (the glow tiers painted into the Glow colour set = glTF COLOR_1). Gate: eye is the top tier,
    the molten core (gap ends + bridges) outshines the cracks, flame tips outshine the cracks."""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    rank = sorted(tier, key=lambda n: -tier[n])
    ok = bool(rank) and rank[0] == "eye" and tier.get("core", 0) > tier.get("crack", 0) and tier.get("flame", 0) > tier.get("crack", 0)
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "rank": rank, "pass": ok}


report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["pass"], report["glow_tiers"]["default"]


def finish_object(name, V, F, rid, shade=None):
    ob = new_obj(name, V, F)
    me = ob.data
    if shade is None:
        FC = np.array([np.mean(V[f], 0) for f in F])
        jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
        shade = 0.93 + 0.14 * jit
    PAL.store_regions(me, REG, rid, shade)
    counts = PAL.paint(me, pal_default)
    me.materials.append(mat)
    me.shade_flat()
    return ob, counts


for seg in SEGS:
    V2, F2, rid, cutrep = cut_segment(seg)
    if seg == "upper" and TONGUE:
        nb = len(V2)
        V2 = np.vstack([V2, tV]); F2 = F2 + [[i + nb for i in f] for f in tF]
        rid = np.concatenate([rid, np.full(len(tF), R_["flame"], dtype=np.int32)])
        SEGDATA[seg]["tongue_vidx"] = np.arange(nb, len(V2))
    ob, counts = finish_object(UNIT + "_" + seg, V2, F2, rid)
    SEGOBJ[seg] = ob
    SEGDATA[seg]["V2"] = V2; SEGDATA[seg]["F2"] = F2; SEGDATA[seg]["rid"] = rid
    report["segments"][seg].update({"regions_faces": counts, "iso_cuts": cutrep, "voronoi_seeds": SEGDATA[seg]["seeds"], "tip_field_passes": SEGDATA[seg]["tip_iters"], "mean_edge": SEGDATA[seg]["mean_edge"],
                                    "tris_final": tri_count(ob.data),
                                    "min_z": round(float(V2[:, 2].min()), 4),
                                    "bbox": [V2.min(0).round(4).tolist(), V2.max(0).round(4).tolist()]})
report["cut_seconds"] = round(time.time() - t_cut, 1)


# =========================================================================== 6. molten bridges
def strand(pa, pb, r_end, sides, rings):
    ax = pb - pa
    L = np.linalg.norm(ax); d = ax / L
    e1 = np.cross(d, [0, 0, 1.0]); e1 /= np.linalg.norm(e1); e2 = np.cross(e1, d)
    V, F, U = [], [], []
    for i in range(rings + 1):
        u = i / rings
        c = pa + ax * u - np.array([0, 0, BRIDGE_SAG]) * math.sin(math.pi * u)
        r = r_end * (BRIDGE_PINCH + (1 - BRIDGE_PINCH) * abs(2 * u - 1) ** 1.6)
        for j in range(sides):
            a = TAU * j / sides
            V.append(c + r * (math.cos(a) * e1 + math.sin(a) * e2)); U.append(u)
    for i in range(rings):
        for j in range(sides):
            a0 = i * sides + j; a1 = i * sides + (j + 1) % sides
            b0 = a0 + sides; b1 = a1 + sides
            F += [[a0, a1, b1], [a0, b1, b0]]
    c0 = len(V); V.append(pa); U.append(0.0)
    c1 = len(V); V.append(pb); U.append(1.0)
    for j in range(sides):
        F.append([c0, (j + 1) % sides, j])
        F.append([c1, rings * sides + j, rings * sides + (j + 1) % sides])
    return np.array(V), F, np.array(U)


BRV, BRF, BRU, BRGAP = [], [], [], []
bridge_rep = []
for gi, (gname, g) in enumerate(GAPS.items()):
    d = g["axis"]; e1 = np.cross(d, [0, 0, 1.0]); e1 /= np.linalg.norm(e1); e2 = np.cross(e1, d)
    for (lat, ver, rend) in BRIDGE_STRANDS:
        o = g["mid"] + lat * e1 + ver * e2
        ha = BVH[g["a"]].ray_cast(Vector(o + d * 50.0), Vector(-d))[0]
        hb = BVH[g["b"]].ray_cast(Vector(o - d * 50.0), Vector(d))[0]
        assert ha is not None and hb is not None, "bridge strand misses: %s %r" % (gname, (lat, ver))
        assert (np.array(hb) - np.array(ha)) @ d > 0.05, "bridge strand does not cross an open gap: %s %r" % (gname, (lat, ver))
        pa = np.array(ha) - d * BRIDGE_SINK; pb = np.array(hb) + d * BRIDGE_SINK
        V_, F_, U_ = strand(pa, pb, rend, BRIDGE_SIDES, BRIDGE_RINGS)
        nb = sum(len(v) for v in BRV)
        BRV.append(V_); BRF += [[i + nb for i in f] for f in F_]; BRU.append(U_); BRGAP.append(np.full(len(V_), gi))
        bridge_rep.append({"gap": gname, "offset": [lat, ver], "r_end": rend, "surface_span": round(float(np.linalg.norm(np.array(hb) - np.array(ha))), 4),
                           "strand_length": round(float(np.linalg.norm(pb - pa)), 4)})
BRV = np.vstack(BRV); BRU = np.concatenate(BRU); BRGAP = np.concatenate(BRGAP)
br_ob, br_counts = finish_object(UNIT + "_bridges", BRV, BRF, np.full(len(BRF), R_["core"], dtype=np.int32),
                                 shade=np.ones(len(BRF)))
report["bridges"] = {"strands": bridge_rep, "tris": tri_count(br_ob.data), "pinch": BRIDGE_PINCH, "sag": BRIDGE_SAG,
                     "sink": BRIDGE_SINK, "region": "core"}

# =========================================================================== facing landmark + props, UVs
anchor = (EYE_W[0] + EYE_W[1]) / 2
landmark = SNOUT_W
dvec = landmark - anchor
report["facing"] = {"rule": "midpoint of the two eye sockets -> the snout tip (head frame, built facing -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
ALL_OBS = [SEGOBJ[s] for s in SEGS] + [br_ob]
for ob in ALL_OBS:
    ob["conquest_unit"] = UNIT
    ob["conquest_tier"] = "hero"
    ob["conquest_tri_budget"] = TRI_BUDGET
    ob["conquest_max_height"] = CELL_MAX_H
    ob["conquest_max_footprint"] = CELL_MAX_FP
    ob["conquest_yaw_fix_deg"] = 0.0
    ob["conquest_front_anchor"] = anchor.tolist()
    ob["conquest_front_landmark"] = landmark.tolist()
    ob["conquest_facing_rule"] = report["facing"]["rule"]
    ob["conquest_source"] = "newunit-magmoo.blend (main body) + fresh SDF segments"
    ob["conquest_scale_policy"] = "natural proportions, source units; game scales at import (cell fit report-only)"
    ob["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
    ob["conquest_segment"] = ob.name[len(UNIT) + 1:]
t_uv = time.time()
for ob in ALL_OBS:
    bpy.context.view_layer.objects.active = ob
    for o in scene.objects:
        o.select_set(o is ob)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")
report["uv_seconds"] = round(time.time() - t_uv, 1)

# measurements: length / head height / tris
ALLV = np.vstack([np.array([v.co[:] for v in o.data.vertices]) for o in ALL_OBS])
lo_a, hi_a = ALLV.min(0), ALLV.max(0)
spine_len = float(SD.arclen(neck_poly)[-1] + np.linalg.norm(SNOUT_W - OCC_W) + (BODY_Y[1] - BODY_Y[0]) + TAIL_LEN
                  + sum(g["axial_gap"] for g in GAPS.values()))
UV_ = SEGDATA["upper"]["V2"]
head_m = np.linalg.norm(UV_ - OCC_W, axis=1) < 1.8
report["measure"] = {
    "bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()],
    "length_y": round(float(hi_a[1] - lo_a[1]), 4), "width_x": round(float(hi_a[0] - lo_a[0]), 4),
    "height_z": round(float(hi_a[2] - lo_a[2]), 4),
    "spine_arc_length_nose_to_tail_tip": round(spine_len, 4),
    "head_top_z": round(float(UV_[head_m, 2].max()), 4), "eye_z": round(float(anchor[2]), 4),
    "snout_z": round(float(SNOUT_W[2]), 4),
    "body_length": round(BODY_Y[1] - BODY_Y[0], 4), "body_core_height_top": round(float(np.percentile(bV[:, 2], 95)), 4),
    "tail_spine_length": TAIL_LEN,
    "units": "source units (the main blob's own sculpt scale, natural proportions)"}
fp = max(report["measure"]["length_y"], report["measure"]["width_x"])
k_fit = min(CELL_MAX_H / report["measure"]["height_z"], CELL_MAX_FP / fp)
report["measure"]["segment_gap_final"] = {
    g: round(min_gap(SEGDATA[v["a"]]["V2"], SEGDATA[v["a"]]["F2"], SEGDATA[v["b"]]["V2"], SEGDATA[v["b"]]["F2"]), 4)
    for g, v in GAPS.items()}
report["measure"]["segment_gap_note"] = "GAP_* is set on the finished shells; the crust domes (PLATE_DOME) then grow " \
                                        "both facing surfaces, so the delivered gap is GAP - up to 2 x PLATE_DOME"
report["measure"]["export_cell_fit_report_only"] ={"scale": round(k_fit, 5), "bound_by": "footprint" if CELL_MAX_FP / fp < CELL_MAX_H / report["measure"]["height_z"] else "height",
                                                    "length_m": round(fp * k_fit, 4), "height_m": round(report["measure"]["height_z"] * k_fit, 4)}
report["tris"] = {o.name: tri_count(o.data) for o in ALL_OBS}
report["tris"]["total"] = sum(report["tris"].values())
print("MEASURE", json.dumps(report["measure"]), json.dumps(report["tris"]))


def geometry_digest():
    h = hashlib.sha256()
    for o in sorted(ALL_OBS, key=lambda o_: o_.name):
        me = o.data
        co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
        lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
        h.update(np.round(co, 6).astype(np.float32).tobytes()); h.update(lv.tobytes())
        for nm in ("Col", "Glow"):
            cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes[nm].data.foreach_get("color", cd)
            h.update(np.round(cd, 5).tobytes())
        uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv)
        h.update(np.round(uv, 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


report["digest_geometry_colour_uv"] = geometry_digest()
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
report["seconds_to_improved"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True)
# the source blob (game-framed, same centring) for the before/after sheet: a separate small blend
src_ob = new_obj(UNIT + "_source_blob", BODY_SRC, SF)
src_ob.data.materials.append(mat)
for o in scene.objects:
    o.hide_render = o is not src_ob
    o.select_set(o is src_ob)
col_src = src_ob.data.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
col_src.data.foreach_set("color", np.tile([0.8, 0.8, 0.8, 1.0], len(src_ob.data.loops)).astype(np.float32))
glow_src = src_ob.data.color_attributes.new("Glow", "FLOAT_COLOR", "CORNER")
glow_src.data.foreach_set("color", np.tile([0.0, 0.0, 0.0, 1.0], len(src_ob.data.loops)).astype(np.float32))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUTROOT, "improved", UNIT + "_source_blob.blend"), copy=True, compress=True)
for o in scene.objects:
    o.hide_render = False
bpy.data.objects.remove(src_ob, do_unlink=True)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, report["digest_geometry_colour_uv"], round(time.time() - T0, 1))
sys.stdout.flush()
if STOP == "improved":
    os._exit(0)

# =========================================================================== 7. chain rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def chain_points(poly, n):
    s = SD.arclen(poly)
    t = np.linspace(0.0, s[-1], n + 1)
    return np.stack([np.interp(t, s, poly[:, k]) for k in range(3)], 1)


NECK_J = chain_points(np.vstack([neck_poly[:-1], OCC_W]), NECK_BONES)            # neck base -> occiput
BODY_J = chain_points(np.array([BODY_SPINE[0], BODY_SPINE[1]]), BODY_BONES)       # front -> rear
TAIL_POLY = TAIL_SPINE
TAIL_J = chain_points(TAIL_POLY, TAIL_BONES)                                      # base -> tip
# THE CHAIN, nose -> tail tip: joints and links (a bone = one link; gap links carry no bone)
JOINTS = [SNOUT_W, OCC_W] + [NECK_J[i] for i in range(NECK_BONES - 1, -1, -1)] + list(BODY_J) + list(TAIL_J)
JOINTS = np.array(JOINTS)
BONE_LINK = {"head": ("head", 0)}
names_chain = ["head"] + ["neck.%d" % i for i in range(NECK_BONES - 1, -1, -1)] + ["gap.upper"] + \
    ["body.%d" % i for i in range(BODY_BONES)] + ["gap.tail"] + ["tail.%d" % i for i in range(TAIL_BONES)]
assert len(names_chain) == len(JOINTS) - 1
LINK = {n: k for k, n in enumerate(names_chain)}       # link k joins JOINTS[k] -> JOINTS[k+1]
S_LINK = SD.arclen(JOINTS)                               # arc position of each joint from the nose
LINK_MID = 0.5 * (S_LINK[:-1] + S_LINK[1:])
SEG_OF = {"upper": ["neck.%d" % i for i in range(NECK_BONES)] + ["head"] + (["tongue"] if TONGUE else []),
          "body": ["body.%d" % i for i in range(BODY_BONES)], "tail": ["tail.%d" % i for i in range(TAIL_BONES)]}


def bone_ends(n):
    """(head, tail) of a chain bone: neck/tail/body bones run away from the body joint they grow from."""
    k = LINK[n]
    a, b = JOINTS[k], JOINTS[k + 1]
    if n == "head" or n.startswith("neck"):
        return b, a                                      # base -> head direction (the chain list runs nose -> tail)
    return a, b


arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.5); eb.use_deform = False
PARENT = {"neck.0": "root", "body.0": "root", "tail.0": "root", "head": "neck.%d" % (NECK_BONES - 1), "tongue": "head"}
for i in range(1, NECK_BONES):
    PARENT["neck.%d" % i] = "neck.%d" % (i - 1)
for i in range(1, BODY_BONES):
    PARENT["body.%d" % i] = "body.%d" % (i - 1)
for i in range(1, TAIL_BONES):
    PARENT["tail.%d" % i] = "tail.%d" % (i - 1)
DEFORM = ["neck.%d" % i for i in range(NECK_BONES)] + ["head"] + (["tongue"] if TONGUE else []) + \
    ["body.%d" % i for i in range(BODY_BONES)] + ["tail.%d" % i for i in range(TAIL_BONES)]
for n in DEFORM:
    e = arm_data.edit_bones.new(n)
    if n == "tongue":
        h_, t_ = TONGUE_PTS[0], TONGUE_PTS[2]
    else:
        h_, t_ = bone_ends(n)
    e.head = Vector(h_); e.tail = Vector(t_)
    e.align_roll(Vector((0, 0, 1.0)) if abs((Vector(t_) - Vector(h_)).normalized().z) < 0.9 else Vector((0, 1.0, 0)))
    e.use_deform = True
for n in DEFORM:
    e = arm_data.edit_bones[n]
    e.parent = arm_data.edit_bones[PARENT[n]]
    e.use_connect = PARENT[n] != "root" and n != "tongue" and (e.head - e.parent.tail).length < 1e-6
bpy.ops.object.mode_set(mode="OBJECT")
REST = {b.name: np.array(b.matrix_local) for b in arm_data.bones}
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "connected": b.use_connect, "head": [round(v, 4) for v in b.head_local],
                 "tail": [round(v, 4) for v in b.tail_local], "length": round(b.length, 4)} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["segment_chains"] = SEG_OF
rep["separability"] = "each segment's sub-chain root (neck.0 / body.0 / tail.0) is parented straight to 'root'; " \
                      "every segment mesh is weighted only to its own sub-chain (verified below)"


# ---- weights: arc-length hat weights along each segment's own bone polyline (smoothed over the mesh graph)
def chain_weights(V, E, bones):
    """bones: ordered list along the segment; vertex -> arc param by nearest point on the bone polyline (extended
    past both ends), 6 graph-smoothing passes, then hat weights between consecutive bone midpoints (<= 2)."""
    pts = [np.array(bone_ends(b)[0]) for b in bones] + [np.array(bone_ends(bones[-1])[1])]
    pts = np.array(pts)
    seg_len = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg_len)])
    best_d = np.full(len(V), np.inf); s = np.zeros(len(V))
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        ab = b - a
        tt = (V - a) @ ab / (ab @ ab)
        lo_t = -np.inf if i == 0 else 0.0
        hi_t = np.inf if i == len(pts) - 2 else 1.0
        tt = np.clip(tt, lo_t, hi_t)
        d = np.linalg.norm(V - (a + tt[:, None] * ab), axis=1)
        m = d < best_d
        best_d[m] = d[m]; s[m] = cum[i] + tt[m] * seg_len[i]
    n = len(V)
    deg = np.bincount(E.ravel(), minlength=n).astype(float)
    for _ in range(6):
        sm = (np.bincount(E[:, 0], s[E[:, 1]], minlength=n) + np.bincount(E[:, 1], s[E[:, 0]], minlength=n)) / np.maximum(deg, 1)
        s = 0.5 * s + 0.5 * sm
    mids = 0.5 * (cum[:-1] + cum[1:])
    W = np.zeros((n, len(bones)))
    if len(bones) == 1:
        W[:, 0] = 1.0
        return W
    k = np.clip(np.searchsorted(mids, s) - 1, 0, len(bones) - 2)
    u = np.clip((s - mids[k]) / (mids[k + 1] - mids[k]), 0.0, 1.0)
    W[np.arange(n), k] = 1 - u
    W[np.arange(n), k + 1] += u
    return W


WREP = {}
for seg in SEGS:
    ob = SEGOBJ[seg]
    V = np.array([v.co[:] for v in ob.data.vertices])
    E = np.array([e.vertices[:] for e in ob.data.edges])
    bones = SEG_OF[seg] if seg != "upper" else ["neck.%d" % i for i in range(NECK_BONES)] + ["head"]
    W = chain_weights(V, E, bones)
    groups = {b: ob.vertex_groups.new(name=b) for b in bones}
    tongue_v = SEGDATA[seg].get("tongue_vidx")
    if tongue_v is not None:
        W[tongue_v] = 0.0
        groups["tongue"] = ob.vertex_groups.new(name="tongue")
        groups["tongue"].add([int(i) for i in tongue_v], 1.0, "REPLACE")
    for j, b in enumerate(bones):
        for i in np.nonzero(W[:, j] > 1e-6)[0]:
            groups[b].add([int(i)], float(W[i, j]), "REPLACE")
    ob.parent = rig; ob.matrix_parent_inverse = Matrix.Identity(4)
    ob.modifiers.new("Armature", "ARMATURE").object = rig
    WREP[seg] = {"bones": bones + (["tongue"] if tongue_v is not None else []),
                 "per_bone_dominant": {b: int((np.argmax(W, 1) == j).sum()) for j, b in enumerate(bones)}}
# bridges: strand vertex at fraction u blends the end bone of A (1 - u) -> the start bone of B (u)
END_BONE = {("upper|body", "a"): "neck.0", ("upper|body", "b"): "body.0",
            ("body|tail", "a"): "body.%d" % (BODY_BONES - 1), ("body|tail", "b"): "tail.0"}
gnames = list(GAPS)
bgroups = {}
for i, (u, gi) in enumerate(zip(BRU, BRGAP)):
    ga, gb = END_BONE[(gnames[gi], "a")], END_BONE[(gnames[gi], "b")]
    for bn, w in ((ga, 1.0 - u), (gb, u)):
        if w > 0:
            if bn not in bgroups:
                bgroups[bn] = br_ob.vertex_groups.new(name=bn)
            bgroups[bn].add([i], float(w), "REPLACE")
br_ob.parent = rig; br_ob.matrix_parent_inverse = Matrix.Identity(4)
br_ob.modifiers.new("Armature", "ARMATURE").object = rig
WREP["bridges"] = {"bones": sorted(bgroups), "rule": "strand fraction u: (1-u) to segment A's end bone, u to B's start bone"}


def weight_audit(ob):
    deform = {b.name for b in arm_data.bones if b.use_deform}
    gi = {g.index: g.name for g in ob.vertex_groups}
    sums, infl, used = [], [], set()
    for v in ob.data.vertices:
        ws = [(gi[g.group], g.weight) for g in v.groups if gi[g.group] in deform and g.weight > 0]
        sums.append(sum(w for _, w in ws)); infl.append(len(ws)); used |= {n for n, _ in ws}
    sums = np.array(sums); infl = np.array(infl)
    return {"weight_sum_min": round(float(sums.min()), 6), "weight_sum_max": round(float(sums.max()), 6),
            "unweighted": int((infl == 0).sum()), "max_influences": int(infl.max()), "bones_used": sorted(used)}


for seg in SEGS:
    WREP[seg]["audit"] = weight_audit(SEGOBJ[seg])
    WREP[seg]["only_own_chain"] = set(WREP[seg]["audit"]["bones_used"]) <= set(SEG_OF[seg])
    assert WREP[seg]["only_own_chain"], (seg, WREP[seg]["audit"]["bones_used"])
WREP["bridges"]["audit"] = weight_audit(br_ob)
rep["weights"] = WREP

# =========================================================================== 8. clips (discrete FK on the rest chain)
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
LINKV = np.diff(JOINTS, axis=0)                        # rest link vectors (nose -> tail order)
J_BODY0 = 2 + NECK_BONES                                # joints: snout, occiput, neck joints, then the body's front
ANCHOR_J = J_BODY0 + BODY_BONES // 2                    # the body's middle joint stays put
assert np.allclose(JOINTS[ANCHOR_J], BODY_J[BODY_BONES // 2])
BODY_JIDX = list(range(J_BODY0, J_BODY0 + BODY_BONES + 1))
UPPER_LINKS = [LINK["head"]] + [LINK["neck.%d" % i] for i in range(NECK_BONES)]
TAIL_LINKS = [LINK["tail.%d" % i] for i in range(TAIL_BONES)]


def rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def raxis(axis, a):
    return np.array(Matrix.Rotation(a, 3, Vector(axis)))


def solve_chain(Q):
    """Q: per-link 3x3 rotation (absolute, relative to rest). Anchor joint fixed; -> joint positions."""
    P = np.zeros_like(JOINTS)
    P[ANCHOR_J] = JOINTS[ANCHOR_J]
    for k in range(ANCHOR_J, len(LINKV)):
        P[k + 1] = P[k] + Q[k] @ LINKV[k]
    for k in range(ANCHOR_J - 1, -1, -1):
        P[k] = P[k + 1] - Q[k] @ LINKV[k]
    return P


def procrustes_z(P, Q):
    """rigid 2D (about Z) fit of the posed body joints back onto their rest: the body stays in place."""
    A = P[BODY_JIDX, :2]; B = JOINTS[BODY_JIDX, :2]
    ca, cb = A.mean(0), B.mean(0)
    H = (A - ca).T @ (B - cb)
    ang = math.atan2(H[0, 1] - H[1, 0], H[0, 0] + H[1, 1])
    R = rz(ang)
    t = np.zeros(3); t[:2] = cb - R[:2, :2] @ ca
    return P @ R.T + t, [R @ q for q in Q], ang


def pose_chain(P, Q, tongue_scale=1.0):
    """armature-space bone matrices -> pose-bone basis (location, quaternion, scale) for every deform bone."""
    M = {"root": REST["root"]}                           # the contract root never moves
    out = {}
    for n in DEFORM:
        if n == "tongue":
            continue
        k = LINK[n]
        head_ = P[k + 1] if (n == "head" or n.startswith("neck")) else P[k]
        Mb = np.eye(4); Mb[:3, :3] = Q[k] @ REST[n][:3, :3]; Mb[:3, 3] = head_
        M[n] = Mb
    for n in DEFORM:
        if n == "tongue":
            out[n] = (Vector((0, 0, 0)), Quaternion(), (1.0, tongue_scale, 1.0))
            continue
        p = PARENT[n]
        rest_rel = np.linalg.inv(REST[p]) @ REST[n]
        basis = np.linalg.inv(rest_rel) @ np.linalg.inv(M[p]) @ M[n]
        mm = Matrix(basis.tolist())
        out[n] = (mm.to_translation(), mm.to_quaternion(), (1.0, 1.0, 1.0))
    return out


def lateral_axis(v):
    a = np.cross(v, [0, 0, 1.0])
    return a / max(np.linalg.norm(a), 1e-9)


def idle_pose(t):
    Q = [np.eye(3) for _ in LINKV]
    sway = math.radians(IDLE_NECK_SWAY_DEG) * math.sin(TAU * t)
    nod = math.radians(IDLE_NOD_DEG) * math.sin(TAU * 2 * t + 0.6)
    for i in range(NECK_BONES):                           # neck.0 (base) .. neck.3: sway grows toward the head
        w = 0.55 + 0.45 * i / max(NECK_BONES - 1, 1)
        Q[LINK["neck.%d" % i]] = rz(sway * w)
    hk = LINK["head"]
    Q[hk] = rz(sway * 1.25) @ raxis(lateral_axis(LINKV[hk]), nod)
    for i in range(TAIL_BONES):
        f = (i + 1) / TAIL_BONES
        a = math.radians(IDLE_TAIL_DEG) * f ** 2.2 * math.sin(TAU * t - 1.4 * f * math.pi)
        Q[LINK["tail.%d" % i]] = rz(a)
    P = solve_chain(Q)
    # tongue flick twice around t = 0.30 and t = 0.42 (rest 1 at the loop ends: seam-closed)
    fl = 0.0
    for c in (0.30, 0.42):
        x = (t - c) / 0.05
        fl += math.exp(-x * x)
    ts_ = 1.0 + (IDLE_TONGUE[1] - 1.0) * min(fl, 1.0)
    beat = 0.5 - 0.5 * math.cos(TAU * 2 * t)
    return P, Q, ts_, 1.0 + IDLE_GLOW_PULSE * beat, 0.0


def walk_amp(s):
    """heading amplitude (rad) along the arc from the nose: neck -> body -> tail tip (whip)."""
    s_b0, s_b1 = S_LINK[BODY_JIDX[0]], S_LINK[BODY_JIDX[-1]]
    if s < s_b0:
        return math.radians(WALK_AMP_NECK_DEG + (WALK_AMP_BODY_DEG - WALK_AMP_NECK_DEG) * max(0.0, 1 - (s_b0 - s) / 2.0))
    if s <= s_b1:
        return math.radians(WALK_AMP_BODY_DEG)
    f = (s - s_b1) / (S_LINK[-1] - s_b1)
    return math.radians(WALK_AMP_BODY_DEG + (WALK_AMP_TAIL_DEG - WALK_AMP_BODY_DEG) * f ** 1.3)


def walk_pose(t):
    Q = []
    for k in range(len(LINKV)):
        s = LINK_MID[k]
        Q.append(rz(walk_amp(s) * math.sin(TAU * (s / WALK_WAVELENGTH - t))))
    # head: keeps facing forward, follows a little of the neck
    neck_mean = np.mean([math.atan2(Q[LINK["neck.%d" % i]][1, 0], Q[LINK["neck.%d" % i]][0, 0]) for i in range(NECK_BONES)])
    Q[LINK["head"]] = rz(WALK_HEAD_FOLLOW * neck_mean)
    P = solve_chain(Q)
    P, Q, ang = procrustes_z(P, Q)
    beat = 0.5 - 0.5 * math.cos(TAU * t)
    return P, Q, 1.0, 1.0 + WALK_GLOW_PULSE * beat, ang


sock = bsdf.inputs["Emission Strength"]
base_es = sock.default_value


def author(name, frames, pose_fn):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    glow, fits = [], []
    for f in range(1, frames + 2):
        t = ((f - 1) / frames) % 1.0
        P, Q, tsc, g, ang = pose_fn(t)
        glow.append(g); fits.append(ang)
        B = pose_chain(P, Q, tsc)
        for n in DEFORM:
            loc, q, sc = B[n]
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pb = pose[n]
            pb.location = loc; pb.rotation_quaternion = q; pb.scale = sc
            pb.keyframe_insert("location", frame=f, group=n)
            pb.keyframe_insert("rotation_quaternion", frame=f, group=n)
            if n == "tongue":
                pb.keyframe_insert("scale", frame=f, group=n)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, frames + 1
    act.use_cyclic = True
    out = {"procrustes_yaw_deg_range": [round(math.degrees(min(fits)), 4), round(math.degrees(max(fits)), 4)]}
    try:
        K.assign_action(nt, act)
        for f, g in enumerate(glow, start=1):
            sock.default_value = base_es * g
            sock.keyframe_insert("default_value", frame=f)
        out["glow"] = {"socket": "Principled BSDF.Emission Strength", "base": base_es,
                       "range": [round(base_es * min(glow), 4), round(base_es * max(glow), 4)]}
    except Exception as exc:  # noqa: BLE001
        out["glow"] = {"error": repr(exc)}
    nt.animation_data.action = None
    sock.default_value = base_es
    out["slots"] = [s_.identifier for s_ in act.slots]
    return act, out


def eval_all():
    dg = bpy.context.evaluated_depsgraph_get()
    res = {}
    for ob in ALL_OBS:
        ev = ob.evaluated_get(dg)
        m_ = ev.to_mesh()
        co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
        ev.to_mesh_clear()
        res[ob.name] = co.reshape(-1, 3)
    return res


REST_CO = {ob.name: np.array([v.co[:] for v in ob.data.vertices]) for ob in ALL_OBS}
SEGF_T = {s: [list(p.vertices) for p in SEGOBJ[s].data.polygons] for s in SEGS}


def measure(act, frames):
    K.assign_action(rig, act)
    first = last = None
    minz = 1e9
    head_z, head_y, snout_x = [], [], []
    body_disp = 0.0
    gaps = {g: [1e9, -1e9] for g in GAPS}
    ext_lo, ext_hi = np.full(3, 1e9), np.full(3, -1e9)
    root_off = 0.0
    tail_tip = []
    hi_s = SEGOBJ["upper"].name
    for f in range(1, frames + 2):
        scene.frame_set(f)
        C = eval_all()
        if f == 1:
            first = C
        if f == frames + 1:
            last = C
        allc = np.vstack(list(C.values()))
        minz = min(minz, float(allc[:, 2].min()))
        ext_lo = np.minimum(ext_lo, allc.min(0)); ext_hi = np.maximum(ext_hi, allc.max(0))
        root_off = max(root_off, (rig.matrix_world @ pose["root"].head).length)
        hm = np.array(pose["head"].head); ht = np.array(pose["head"].tail)
        head_z.append(float(ht[2])); snout_x.append(float(ht[0])); head_y.append(float(ht[1]))
        tail_tip.append(np.array(pose["tail.%d" % (TAIL_BONES - 1)].tail))
        bc = C[SEGOBJ["body"].name]
        body_disp = max(body_disp, float(np.linalg.norm(bc - REST_CO[SEGOBJ["body"].name], axis=1).max()))
        if (f - 1) % 6 == 0:
            for gname, g in GAPS.items():
                A = C[SEGOBJ[g["a"]].name]; B_ = C[SEGOBJ[g["b"]].name]
                d_ = min_gap(A, SEGF_T[g["a"]], B_, SEGF_T[g["b"]])
                gaps[gname] = [min(gaps[gname][0], d_), max(gaps[gname][1], d_)]
    seams = {n: round(float(np.linalg.norm(first[n] - last[n], axis=1).max()) * 1000, 6) for n in first}
    tail_tip = np.array(tail_tip)
    return {"frames": frames + 1, "cycle_frames": frames, "cycle_s": round(frames / K.FPS, 4),
            "seam_first_last_max_per_object_x1000": seams,
            "min_z": round(minz, 6), "root_offset_max": round(root_off, 9),
            "head_tip_z_range": [round(min(head_z), 4), round(max(head_z), 4)],
            "head_tip_x_range": [round(min(snout_x), 4), round(max(snout_x), 4)],
            "body_vertex_max_displacement": round(body_disp, 4),
            "tail_tip_x_range": [round(float(tail_tip[:, 0].min()), 4), round(float(tail_tip[:, 0].max()), 4)],
            "segment_gap_min_max_every_6th_frame": {k: [round(v[0], 4), round(v[1], 4)] for k, v in gaps.items()},
            "extent": {"width": round(float(ext_hi[0] - ext_lo[0]), 4), "length": round(float(ext_hi[1] - ext_lo[1]), 4),
                       "height": round(float(ext_hi[2] - ext_lo[2]), 4)}}


t_anim = time.time()
act_idle, idle_x = author("idle", IDLE_FRAMES, idle_pose)
act_walk, walk_x = author("walk", WALK_FRAMES, walk_pose)
rig.animation_data.action = None
m_idle = measure(act_idle, IDLE_FRAMES)
m_walk = measure(act_walk, WALK_FRAMES)
wave_speed = WALK_WAVELENGTH / (WALK_FRAMES / K.FPS)
m_walk["implied_forward_speed"] = {
    "wave_speed_units_per_s": round(wave_speed, 4),
    "body_lengths_per_s": round(wave_speed / report["measure"]["spine_arc_length_nose_to_tail_tip"], 4),
    "m_per_s_at_report_only_cell_fit": round(wave_speed * report["measure"]["export_cell_fit_report_only"]["scale"], 4),
    "rule": "lateral undulation, zero-slip upper bound: forward speed = wave speed = wavelength / cycle time (the body "
            "follows its own path); real slip makes it slower -- the game glides the unit at its own speed"}
m_walk["motion"] = {"wavelength": WALK_WAVELENGTH, "amp_neck_deg": WALK_AMP_NECK_DEG, "amp_body_deg": WALK_AMP_BODY_DEG,
                    "amp_tail_tip_deg": WALK_AMP_TAIL_DEG, "head_follow": WALK_HEAD_FOLLOW, "glow_pulse": WALK_GLOW_PULSE,
                    "wave_direction": "head -> tail (posterior travelling wave = forward propulsion toward -Y)", **walk_x}
m_idle["motion"] = {"neck_sway_deg": IDLE_NECK_SWAY_DEG, "nod_deg": IDLE_NOD_DEG, "tail_tip_deg": IDLE_TAIL_DEG,
                    "glow_pulse": IDLE_GLOW_PULSE, "tongue_scale": IDLE_TONGUE, **idle_x}
rep["idle"] = {"status": "PROPOSED (artist 2026-09-25: body on the floor, head and tail in motion)", **m_idle}
rep["walk"] = {"status": "PROPOSED (in-place slither)", **m_walk}
rep["anim_seconds"] = round(time.time() - t_anim, 1)
K.assign_action(rig, None)
scene.frame_set(1)
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_start, scene.frame_end = 1, IDLE_FRAMES + 1
rig["conquest_rig"] = "magmoo chain: root + neck.0-%d/head/tongue | body.0-%d | tail.0-%d (three separable sub-chains)" % (
    NECK_BONES - 1, BODY_BONES - 1, TAIL_BONES - 1)
for ob in ALL_OBS:
    ob["conquest_clips"] = ["idle", "walk"]
    ob["conquest_clip_status"] = "idle + walk PROPOSED; no attack/hit/death; split-apart NOT animated (chains allow it)"


def full_digest():
    h = hashlib.sha256(geometry_digest().encode())
    for ob in sorted(ALL_OBS, key=lambda o_: o_.name):
        for v in ob.data.vertices:
            for g in v.groups:
                h.update(np.array([v.index, g.group, round(g.weight, 6)], np.float64).tobytes())
    for b in arm_data.bones:
        h.update(np.round(np.array(b.matrix_local), 6).astype(np.float32).tobytes())
    for act in (act_idle, act_walk):
        for fc in K.action_fcurves(act):
            h.update(fc.data_path.encode()); h.update(bytes([fc.array_index]))
            h.update(np.round(np.array([k.co[:] for k in fc.keyframe_points]), 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


rep["digest_full"] = full_digest()
rep["digest_geometry_colour_uv"] = report["digest_geometry_colour_uv"]
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True)

# =========================================================================== glb (identity scale, natural units)
for o in scene.objects:
    o.select_set(o is rig or o in ALL_OBS)
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, act_idle)
t = time.time()
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False)
rig.animation_data.action = None
rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "sha256_16": hashlib.sha256(open(OUT_GLB, "rb").read()).hexdigest()[:16],
              "seconds": round(time.time() - t, 1),
              "structure": "armature identity + 4 skinned mesh children (upper/body/tail/bridges), natural scale; "
                           "report-only cell fit %.5f" % k_fit}

# =========================================================================== skins (palette swap, geometry untouched)


def region_sample(pal_obs):
    out = {}
    for nm in ("Col", "Glow"):
        acc = {}
        for ob in pal_obs:
            me = ob.data
            names, rid, _ = PAL.read_regions(me)
            lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
            ls = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
            cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes[nm].data.foreach_get("color", cd)
            cd = cd.reshape(-1, 4)[ls, :3]
            for r in np.unique(rid):
                acc.setdefault(names[r], []).append(cd[rid == r])
        out[nm] = {k: [round(float(x), 4) for x in np.vstack(v).mean(0)] for k, v in acc.items()}
    return out


def region_counts():
    tot = {}
    for ob in ALL_OBS:
        names, rid, _ = PAL.read_regions(ob.data)
        for r, c in zip(*np.unique(rid, return_counts=True)):
            tot[names[r]] = tot.get(names[r], 0) + int(c)
    return tot


geo0 = geometry_digest()
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"],
                            "region_faces": region_counts(), "region_mean_linear": region_sample(ALL_OBS)}}
for skin in SKINS:
    if skin == "default":
        continue
    pal = PAL.load(UNIT, skin)
    gt = glow_tiers(pal)
    assert gt["pass"], (skin, gt)
    for ob in ALL_OBS:
        PAL.paint(ob.data, pal)
    PAL.apply_material(mat, pal)
    out = OUT_RIGGED[:-6] + "__" + skin + ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    geo_skin = geometry_digest()
    vpos = hashlib.sha256(b"".join(np.round(np.array([v.co[:] for v in o.data.vertices]), 6).astype(np.float32).tobytes() for o in ALL_OBS)).hexdigest()[:16]
    rep["skins"][skin] = {"file": out, "palette": PAL.table(pal), "palette_files": pal["files"], "glow_tiers": gt,
                          "region_faces": region_counts(), "region_mean_linear": region_sample(ALL_OBS),
                          "geometry_colour_uv_digest": geo_skin, "vertex_positions_sha": vpos}
for ob in ALL_OBS:
    PAL.paint(ob.data, pal_default)
PAL.apply_material(mat, pal_default)
rep["skins"]["repaint_proof"] = {
    "rule": "a skin is a pure palette swap: region face counts identical, vertex positions identical, colours differ only "
            "by the palette table; the default digest is restored after the swap",
    "default_digest_before": geo0, "default_digest_after_restore": geometry_digest()}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({"bones": rep["bone_count"], "digest_full": rep["digest_full"], "seconds": rep["seconds"]}))
print("IDLE", json.dumps(rep["idle"]))
print("WALK", json.dumps(rep["walk"]))
sys.stdout.flush()
os._exit(0)
