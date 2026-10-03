"""Vampito (the MOSQUITOPIRE sculpt under the VAMPITO name, artist 2026-09-25) through the shared pipeline, one headless run.

    blender --background source-copies/newunit-mosquitopire.blend --factory-startup --python improve/vampito_build.py -- \
        [--preview <out.blend>]          (geometry + UV + regions + palette only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the two-run determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Reads (never writes): source-copies/newunit-mosquitopire.blend (opened; 8 render meshes, no materials, no rig).
Outputs:
    improved/vampito.blend + .json          crowd-tier retopo, UVs, baked normal/AO, region palette (no rig)
    improved/textures/vampito_{normal,ao}.png
    rigged/vampito.blend + .json            + hover rig (contract root + body/abdomen/head + 2-bone wings), clips idle + walk
    rigged/vampito.glb                      identity-scale export (natural scale)

Artist (design/review-log.md, verbatim): "mosquitopire is facing the wrong way"; "vampito is a worse mosquitopire (but maybe
vampito is the better name to keep)"; "[vampito name] yes confirmed ... [it] fly/hover". Scale policy 2026-09-25: natural
proportions, cell fit report-only until ship. No attack/hit/death clips (idle + locomotion only).

Pipeline:
  1. every render mesh to world space (the membrane object has a NEGATIVE x scale: its winding is flipped back), sculpt
     crumbs (< CRUMB_FRAC of a part) dropped, then YAW_FIX_DEG = 180 applied to the DATA (x, y) -> (-x, -y): the head and
     proboscis sat at +Y. Front = -Y afterwards (renders/vampito/facing_candidates.png).
  2. LOW: the six body parts (head, body+wing arms, collar, 2 eyes, membranes) joined -> voxel remesh (one manifold shell,
     interpenetrating sculpt parts fused) -> collapse decimation (deterministic). The two antennae are 0.04-unit rods a
     voxel remesh erases: they are collapse-decimated on their own (ANT_TRIS each) and kept as separate islands.
     Then XY bbox centre + floor (the abdomen tip) to the origin.
  3. region FIELDS on the low (per vertex): distance to each source part group (part Voronoi: the union surface's owner is
     the nearest part), height, |x|, distance from the head primitive's origin (proboscis length).
  4. ISO-CONTOUR CUTS (duskmaw's cutter): every region boundary is cut into the mesh along its field's iso-line, so colour
     edges are smooth lines. Regions are then read per face.
  5. regions -> palettes.store_regions; paint from palettes/vampito/default.json.
  6. Smart UV + pack; bake normal + AO from the 187k sculpt parts.
  7. RIG (hover archetype, local): 'root' (contract, origin, never keyed) -> 'body' (thorax pivot) -> 'abdomen', 'head',
     'wing.L/R' -> 'wing_tip.L/R'. Analytic weights. Clips keyed every frame from closed-form periodic curves (integer
     cycles per clip -> the seam is exact): idle = hover (lift + wingbeat bob, 2 Hz beats), walk = hover-forward in place
     (nose-down pitch, 4 Hz beats, abdomen trails, head counter-pitches to keep the gaze level).
  8. identity-scale glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402  (read-only use)
import palettes as PAL  # noqa: E402  (read-only use)

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun). Lengths are sculpt units, final
# frame: front -Y, floor (the abdomen tip at rest) z = 0. The sculpt is ~12.1 units tall, ~18.7 wingspan; natural scale.
UNIT = "vampito"
CHAR_ID = "vampito"                   # roster id: not yet in Conquest (ship path deferred by the artist)
YAW_FIX_DEG = 180.0                   # "facing fix": applied to the mesh DATA (proboscis + face were at +Y)
VOXEL = 0.05                          # "retopo resolution" (voxel remesh size before decimation)
LOW_TRIS = 9500                       # "mesh detail" (decimation target before the colour-boundary cuts + antennae)
                                      #   artist 2026-09-25: "smooth some of the model so it doesnt look as low poly" --
                                      #   raised from 4300; the budget probe showed 4k-14k all within 0.03 p99 of the sculpt
ANT_TRIS = 60                         # "antenna detail" (each antenna, decimated on its own)
TRI_BUDGET = [3000, 12000]            # declared tier (widened with the smoothing request, review-log 09-25)
CRUMB_FRAC = 0.01                     # sculpt crumbs smaller than this fraction of their part are dropped
ABD_Z = 4.4                           # "abdomen colour line": the blood-red abdomen is below this height ...
ABD_R = 1.6                           # ... within this |x| of the midline
WING_X = 1.5                          # "wing arm line": body surface beyond this |x| is wing arm
HAND_X = 7.9                          # "talon line": the hanging wing hands outside this |x| ...
TALON_Z = 5.4                         # ... and below this height are talons
# wing enlargement (artist 2026-09-25: "scale the wings to be larger ... a little longer in width but the height of the
# wings (mainly at the end as well at the tips from top to bottom)"). Tip-weighted: smoothstep 0 at WING_GROW_X0 -> 1 at
# the tip, applied to the SOURCE parts before remesh/bake so regions, weights and bakes stay consistent.
WING_GROW_X0 = 3.0                    # "where the wing growth starts" (|x|; shoulder bands 1.3-2.3 stay untouched)
WING_SPAN_GROW = 0.18                 # "wing width growth" at the tip (fraction of the distance beyond the start)
WING_TALL_GROW = 0.50                 # "wing height growth" at the tip (top-to-bottom, about the wing mid-height)
PROB_R_K = 1.12                       # "proboscis base": head surface farther than this x (median head radius) from the head origin
PROB_TIP_LEN = 1.0                    # "blood tip length": the outer part of the proboscis that is blood red
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
BAKE_CAGE = 0.06                      # bake cage extrusion (low->sculpt p99 is ~0.03)
BAKE_RES = (1024, 512)                # normal, AO texture sizes (crowd tier)
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-unit ceilings -- REPORT ONLY (scale policy 2026-09-25)
# rig + weights
NECK_BAND = 0.25                      # "neck softness": head/body blend half-width across the head-part boundary
WING_X0, WING_X1 = 1.3, 2.3           # "shoulder softness": the wing takes over from the body across this |x| band
WING_MID_FRAC = 0.55                  # "wing elbow": wing -> wing_tip joint at this fraction of the half-span ...
TIP_BAND = 0.8                        # ... blended over +- this |x|
ABD_BAND = 0.5                        # "waist softness": abdomen/body blend half-width about ABD_Z + ABD_JOINT_UP
ABD_JOINT_UP = 0.6                    # the abdomen joint sits this far above the colour line
COLLAR_BAND = 0.2                     # collar stays on the body (its wing weight fades out over this distance)
# clips (24 fps; every periodic term has an integer number of cycles per clip, so the loop seam is exact)
HOVER_FRAC = 0.15                     # "hover height": the body is lifted this fraction of its height off the floor
IDLE_N, IDLE_BEATS = 48, 4            # idle: 2 s loop, 4 wingbeats (2 Hz)
IDLE_FLAP_DEG, IDLE_FLAP_MEAN = 20.0, 6.0     # wing stroke amplitude, stroke centre (+ = raised)
IDLE_TIP_DEG, TIP_LAG = 10.0, 0.18            # wing-tip extra stroke, its phase lag (cycles) -- the tip follows through
IDLE_BOB_FRAC = 0.018                 # body bob amplitude (x height), one bob per wingbeat
IDLE_PITCH_DEG, IDLE_SWAY_DEG = 3.0, 2.0      # mean nose-down pitch, slow sway (1 cycle per clip)
IDLE_HEAD_DEG, IDLE_ABD_DEG = 3.0, 4.0        # head nod (2 cycles), abdomen swing (lagging the bob)
WALK_N, WALK_BEATS = 24, 4            # hover-forward: 1 s loop, 4 wingbeats (4 Hz: twice the idle rhythm)
WALK_FLAP_DEG, WALK_FLAP_MEAN = 26.0, 4.0
WALK_TIP_DEG = 12.0
WALK_BOB_FRAC = 0.012
WALK_PITCH_DEG = 14.0                 # "lean into the flight": nose-down pitch
WALK_PITCH_OSC = 1.5                  # pitch wobble per beat
WALK_HEAD_COUNTER = 0.6               # the head undoes this share of the pitch (gaze stays level)
WALK_ABD_TRAIL_DEG, WALK_ABD_DEG = 6.0, 3.0   # the abdomen trails back, and swings per beat
STROUHAL = (0.2, 0.3, 0.4)            # cruising flyers: St = f A / U in 0.2-0.4 (Taylor, Nudds & Thomas 2003) -> implied speed

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
DIGEST_ONLY = argv[argv.index("--digest-only") + 1] if "--digest-only" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(ROOT, "improved", "textures")
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": bpy.data.filepath, "tier": "crowd",
          "tri_budget": TRI_BUDGET, "yaw_fix_deg": YAW_FIX_DEG, "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}                                # the determinism digest: every array a consumer receives


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
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count_F(F):
    return int(sum(len(f) - 2 for f in F))


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], F)
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
    roots = np.array([find(i) for i in range(n)])
    return np.unique(roots, return_inverse=True, return_counts=True)


def keep_islands(V, F, min_frac):
    """Drop connected pieces smaller than min_frac of the vertices (sculpt crumbs / remesh specks)."""
    lab, inv, cnt = islands(V, F)
    keep_lab = cnt >= min_frac * len(V)
    keep_v = keep_lab[inv]
    remap = -np.ones(len(V), dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum())}


def decimate(V, F, target):
    tmp = new_obj("dec_src", V, F)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / tri_count_F(F))
    V2, F2 = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    return V2, F2


# =========================================================================== 1. sculpt parts -> world, crumbs, yaw fix
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
PART_OF = {"Sphere": "head", "Sphere.001": "body", "Sphere.002": "collar", "Sphere.003": "eyes", "Sphere.004": "eyes",
           "Sphere.005": "membrane", "Cylinder": "antennae", "Cylinder.001": "antennae"}
GROUPS = ["body", "head", "eyes", "collar", "membrane"]           # the fused (remeshed) parts; antennae stay separate
RY = np.diag([-1.0, -1.0, 1.0]) if abs(YAW_FIX_DEG - 180.0) < 1e-9 else \
    np.array(Matrix.Rotation(math.radians(YAW_FIX_DEG), 3, "Z"))
src_objs = sorted([o for o in scene.objects if o.type == "MESH" and not o.hide_render], key=lambda o: o.name)
assert sorted(o.name for o in src_objs) == sorted(PART_OF), [o.name for o in src_objs]
PARTS = {}
report["source_parts"] = {}
tris_source = 0
head_origin_src = np.array(bpy.data.objects["Sphere"].matrix_world.translation)
for o in src_objs:
    M = np.array(o.matrix_world)
    V, F = mesh_arrays(o.data, M)
    det = float(np.linalg.det(M[:3, :3]))
    if det < 0:                                  # negative scale: world-space winding is inverted -> flip back
        F = [f[::-1] for f in F]
    tris_source += tri_count_F(F)
    V, F, cr = keep_islands(V, F, CRUMB_FRAC)
    V = V @ RY.T
    PARTS[o.name] = (V, F)
    report["source_parts"][o.name] = {"group": PART_OF[o.name], "tris": tri_count_F(F), "det": round(det, 4),
                                      "winding_flipped": det < 0, "crumbs": cr}
report["tris_source"] = tris_source
head_origin = RY @ head_origin_src
# ---- wing enlargement (see the WING_GROW_* knobs): tip-weighted span + height growth on the source parts
_wing_all = np.vstack([V for nm, (V, _) in PARTS.items() if PART_OF[nm] in ("body", "membrane")])
_wx_max = float(np.abs(_wing_all[:, 0]).max())
_wz_piv = float(_wing_all[np.abs(_wing_all[:, 0]) > WING_GROW_X0, 2].mean())


def _wing_t(ax):
    u = np.clip((ax - WING_GROW_X0) / max(_wx_max - WING_GROW_X0, 1e-9), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def wing_grow(V):
    V = V.copy()
    ax = np.abs(V[:, 0])
    t = _wing_t(ax)
    V[:, 0] = np.sign(V[:, 0]) * (ax + np.clip(ax - WING_GROW_X0, 0.0, None) * WING_SPAN_GROW * t)
    V[:, 2] = _wz_piv + (V[:, 2] - _wz_piv) * (1.0 + WING_TALL_GROW * t)
    return V


def wing_grow_x(x):
    t = float(_wing_t(np.array([abs(x)]))[0])
    return abs(x) + max(abs(x) - WING_GROW_X0, 0.0) * WING_SPAN_GROW * t


PARTS = {k: (wing_grow(v), f) for k, (v, f) in PARTS.items()}
HAND_X_D = wing_grow_x(HAND_X)               # the talon line, carried out with the growth
report["wing_grow"] = {"x0": WING_GROW_X0, "span_grow": WING_SPAN_GROW, "tall_grow": WING_TALL_GROW,
                       "z_pivot": round(_wz_piv, 4), "half_span_before": round(_wx_max, 4),
                       "half_span_after": round(wing_grow_x(_wx_max), 4), "hand_x_deformed": round(HAND_X_D, 4)}
for o in list(bpy.data.objects):             # the source objects (8 meshes, camera, light) never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
# the source's own hover: the lowest sculpt point sat this far above z = 0
src_min_z = min(float(V[:, 2].min()) for V, _ in PARTS.values())
report["source_floor_gap"] = round(src_min_z, 4)

# =========================================================================== 2. low: union voxel remesh + collapse decimation
t = time.time()
UV_, UF_, ulab = [], [], []
off = 0
for nm in sorted(PARTS):
    if PART_OF[nm] == "antennae":
        continue
    V, F = PARTS[nm]
    UV_.append(V); UF_ += [[i + off for i in f] for f in F]; ulab += [PART_OF[nm]] * len(V); off += len(V)
UV_ = np.vstack(UV_)
tmp = new_obj("remesh_src", UV_, UF_)
rm = tmp.modifiers.new("vox", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = VOXEL; rm.use_smooth_shade = False
RV, RF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF = decimate(RV, RF, LOW_TRIS)
LV, LF, specks = keep_islands(LV, LF, 0.01)
ANT = []
for nm in ("Cylinder", "Cylinder.001"):
    AV, AF = decimate(*PARTS[nm], ANT_TRIS)
    ANT.append((AV, AF))
# centre on the delivered mesh's own bounds (contract feet_origin: bbox centre XY + floor at the origin)
allV = np.vstack([LV] + [a[0] for a in ANT])
lo2, hi2 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
LV = LV - SHIFT
ANT = [(a - SHIFT, f) for a, f in ANT]
PARTS = {k: (v - SHIFT, f) for k, (v, f) in PARTS.items()}
UV_ = UV_ - SHIFT
head_origin = head_origin - SHIFT
H = float(hi2[2] - lo2[2])
size = hi2 - lo2
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
GAME_M_PER_UNIT = k_fit
report["natural"] = {"height": round(H, 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                     "footprint": round(fp, 4), "units": "sculpt units (natural proportions, no refit)",
                     "origin_shift_sculpt_units_after_yaw": SHIFT.round(5).tolist(),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint (wingspan)",
                                                     "ceilings": [CELL_MAX_H, CELL_MAX_FP]}}
report["retopo"] = {"method": "6 parts joined -> voxel remesh (%.3f) -> collapse decimation (deterministic) -> main shell; "
                              "antennae decimated on their own (%d tris each target)" % (VOXEL, ANT_TRIS),
                    "remesh_tris": tri_count_F(RF), "decimated_tris": tri_count_F(LF),
                    "antenna_tris": [tri_count_F(a[1]) for a in ANT], "specks_dropped": specks,
                    "seconds": round(time.time() - t, 1)}
# retopo fidelity. low -> sculpt: every low vertex to the nearest sculpt surface (the duskmaw metric).
bvh_union = BVHTree.FromPolygons(UV_.tolist(), UF_)
dev = np.array([bvh_union.find_nearest(Vector(p))[3] for p in LV])
# sculpt -> low, VISIBLE sculpt only: a sculpt vertex buried inside another part (the collar's root inside the thorax,
# the membranes' top edge inside the arms) is not surface the low should reach; buried = inside another part's closed
# shell by the nearest-face normal test.
bvh_low = BVHTree.FromPolygons(LV.tolist(), LF)
part_bvh = {nm: BVHTree.FromPolygons(PARTS[nm][0].tolist(), PARTS[nm][1]) for nm in PARTS if PART_OF[nm] != "antennae"}


def inside(bvh, p):
    loc, nrm, _, d = bvh.find_nearest(Vector(p))
    return loc is not None and (Vector(p) - loc).dot(nrm) < 0


vis_rows = {}
all_vis = []
for nm in sorted(part_bvh):
    V = PARTS[nm][0]
    idx = np.arange(0, len(V), max(1, len(V) // 4000))
    vis = [i for i in idx if not any(inside(part_bvh[o], V[i]) for o in part_bvh if o != nm)]
    d_ = np.array([bvh_low.find_nearest(Vector(V[i]))[3] for i in vis])
    all_vis.append(d_)
    vis_rows[nm] = {"group": PART_OF[nm], "sampled": int(len(idx)), "visible": len(vis),
                    "p99": round(float(np.percentile(d_, 99)), 4), "max": round(float(d_.max()), 4)}
all_vis = np.concatenate(all_vis)
report["retopo"]["low_to_sculpt_distance"] = {"mean": round(float(dev.mean()), 4), "p99": round(float(np.percentile(dev, 99)), 4),
                                              "max": round(float(dev.max()), 4), "pct_of_height_p99": round(100 * float(np.percentile(dev, 99)) / H, 3)}
report["retopo"]["visible_sculpt_to_low_distance"] = {"mean": round(float(all_vis.mean()), 4), "p99": round(float(np.percentile(all_vis, 99)), 4),
                                                      "max": round(float(all_vis.max()), 4), "per_part": vis_rows,
                                                      "rule": "sculpt vertices (subsampled ~4k per part) not inside another part -> nearest low surface"}

# =========================================================================== 3. region fields (per low vertex)
G_BVH = {}
for g in GROUPS:
    Vg, Fg, o_ = [], [], 0
    for nm in sorted(PARTS):
        if PART_OF[nm] == g:
            V, F = PARTS[nm]
            Vg.append(V); Fg += [[i + o_ for i in f] for f in F]; o_ += len(V)
    G_BVH[g] = BVHTree.FromPolygons(np.vstack(Vg).tolist(), Fg)
DG = np.array([[G_BVH[g].find_nearest(Vector(p))[3] for g in GROUPS] for p in LV])      # unsigned: the union surface is outside every part


def voronoi_field(D, g):
    j = GROUPS.index(g)
    others = np.delete(D, j, axis=1).min(1)
    return others - D[:, j]                    # > 0 where part group g owns the surface


HV = PARTS["Sphere"][0]
hr = np.linalg.norm(HV - head_origin, axis=1)
# the proboscis axis: head primitive origin -> the head part's farthest vertex (also the facing landmark, below)
TIP_I = int(np.argmax(hr))
PROB_DIR = (HV[TIP_I] - head_origin) / hr[TIP_I]
PROB_R = PROB_R_K * float(np.median(hr))
dh = (LV - head_origin) @ PROB_DIR                  # position along the proboscis axis (a plane cut, so the neck below the ball is never proboscis)
head_low = voronoi_field(DG, "head") > 0
DH_MAX = float(dh[head_low].max())
DH_TIP = DH_MAX - PROB_TIP_LEN
FIELDS = {"fe": voronoi_field(DG, "eyes"), "fc": voronoi_field(DG, "collar"), "fm": voronoi_field(DG, "membrane"),
          "fh": voronoi_field(DG, "head"), "fb": voronoi_field(DG, "body"),
          "z": LV[:, 2], "ax": np.abs(LV[:, 0]), "dh": dh}
report["head_probe"] = {"head_origin": head_origin.round(4).tolist(), "median_head_radius": round(float(np.median(hr)), 4),
                        "proboscis_dir": PROB_DIR.round(4).tolist(), "proboscis_base_s": round(PROB_R, 4),
                        "proboscis_s_max": round(DH_MAX, 4), "tip_from_s": round(DH_TIP, 4)}

# =========================================================================== 4. iso-contour cuts (duskmaw's cutter)
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k) for k in FIELDS}
for v in bm.verts:
    for k, arr in FIELDS.items():
        v[LAY[k]] = float(arr[v.index])


def iso_cut(key, tau, gate=None):
    L = LAY[key]
    eps = 1e-5 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < CUT_SNAP:
                a[L] = tau
            elif tt > 1 - CUT_SNAP:
                b[L] = tau
    cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
    nsplit = 0
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
        nsplit += 1
    pairs = []
    for f in bm.faces:
        vs = list(f.verts)
        sides = [side(v) for v in vs]
        if not (1 in sides and -1 in sides):
            continue
        cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
        if len(cv) == 2:
            pairs.append(cv)
    for cv in pairs:
        bmesh.ops.connect_verts(bm, verts=cv)
    big = [f for f in bm.faces if len(f.verts) > 3]
    if big:
        bmesh.ops.triangulate(bm, faces=big)
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": nsplit, "face_connects": len(pairs)}


TOL = 0.02
in_body = lambda v: v[LAY["fb"]] > -TOL
in_head = lambda v: v[LAY["fh"]] > -TOL
abd_gate = lambda a, b: a[LAY["ax"]] < ABD_R and b[LAY["ax"]] < ABD_R and in_body(a) and in_body(b)
arm_gate = lambda a, b: all(in_body(v) and v[LAY["z"]] > ABD_Z - 0.3 for v in (a, b))
hand_gate = lambda a, b: all(in_body(v) and v[LAY["z"]] < TALON_Z + 0.3 for v in (a, b))
talon_gate = lambda a, b: all(in_body(v) and v[LAY["ax"]] > HAND_X_D - 0.3 for v in (a, b))
head_gate = lambda a, b: in_head(a) and in_head(b)
CUTS = [("fe", 0.0, None), ("fc", 0.0, None), ("fm", 0.0, None), ("fh", 0.0, None),
        ("z", ABD_Z, abd_gate), ("ax", WING_X, arm_gate), ("ax", HAND_X_D, hand_gate), ("z", TALON_Z, talon_gate),
        ("dh", PROB_R, head_gate), ("dh", DH_TIP, head_gate)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
straddle = {}
for key, tau, gate in CUTS:
    L = LAY[key]; eps = 1e-5 * max(1.0, abs(tau)); n_ = 0
    for f in bm.faces:
        vs = list(f.verts)
        if gate is not None and not all(gate(vs[i], vs[(i + 1) % len(vs)]) for i in range(len(vs))):
            continue
        vals = [v[L] for v in vs]
        if min(vals) < tau - eps and max(vals) > tau + eps:
            n_ += 1
    straddle["%s=%.3f" % (key, tau)] = n_
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1),
                      "rule": "edge split at the field's iso-value (snap within CUT_SNAP of a vertex) + face connects; "
                              "fields interpolate linearly along split edges; no face straddles any boundary afterwards",
                      "straddling_faces_after_cut": straddle}
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
VFIELD = {k: np.array([v[LAY[k]] for v in bm.verts]) for k in LAY}
bm.free()

# =========================================================================== regions
REG = ["body", "abdomen", "wing_arm", "talon", "membrane", "collar", "head", "eyes", "proboscis", "proboscis_tip", "antennae"]
R_ = {n: i for i, n in enumerate(REG)}
nf_c = len(CF)
owner = np.argmax(np.stack([FVAL["fb"], FVAL["fh"], FVAL["fe"], FVAL["fc"], FVAL["fm"]], 1), 1)   # GROUPS order
rid = np.full(nf_c, R_["body"], dtype=np.int32)
g_body = owner == 0
rid[owner == 1] = R_["head"]; rid[owner == 2] = R_["eyes"]; rid[owner == 3] = R_["collar"]; rid[owner == 4] = R_["membrane"]
rid[g_body & (FVAL["z"] < ABD_Z) & (FVAL["ax"] < ABD_R)] = R_["abdomen"]
arm = g_body & (FVAL["ax"] > WING_X) & (FVAL["z"] > ABD_Z - 0.3)
rid[arm] = R_["wing_arm"]
rid[g_body & (FVAL["ax"] > HAND_X_D) & (FVAL["z"] < TALON_Z)] = R_["talon"]
rid[(owner == 1) & (FVAL["dh"] > PROB_R)] = R_["proboscis"]
rid[(owner == 1) & (FVAL["dh"] > DH_TIP)] = R_["proboscis_tip"]
# final mesh = cut shell + the two antennae islands
FV = [CV] + [a[0] for a in ANT]
FF = list(CF)
o_ = len(CV)
for AV, AF in ANT:
    FF += [[i + o_ for i in f] for f in AF]; o_ += len(AV)
FV = np.vstack(FV)
rid = np.concatenate([rid, np.full(sum(len(a[1]) for a in ANT), R_["antennae"], dtype=np.int32)])
# per-vertex part group of the final mesh (for weights): antennae verts are head
VGROUP = np.concatenate([np.argmax(np.stack([VFIELD["fb"], VFIELD["fh"], VFIELD["fe"], VFIELD["fc"], VFIELD["fm"]], 1), 1),
                         np.full(len(FV) - len(CV), 1)])
VF = {k: np.concatenate([VFIELD[k], np.full(len(FV) - len(CV), 1.0 if k == "fh" else -1.0)]) for k in ("fb", "fh", "fe", "fc", "fm")}
low_me = bpy.data.meshes.new(UNIT)
low_me.from_pydata(FV.tolist(), [], FF)
low_me.update()
low = bpy.data.objects.new(UNIT, low_me)
scene.collection.objects.link(low)
me = low.data
nf = len(me.polygons)
assert nf == len(rid)
report["tris_final"] = int(sum(len(f) - 2 for f in FF))
report["region_rule"] = {
    "part_voronoi": "each low vertex's owner = the nearest source part group (body+arms / head+proboscis / eyes / collar / "
                    "membranes); boundaries cut along (nearest-other distance - own distance) = 0",
    "abdomen": "body surface below ABD_Z within ABD_R of the midline", "wing_arm": "body surface beyond |x| WING_X (above the abdomen)",
    "talon": "wing arm beyond |x| HAND_X below TALON_Z (the hanging hands)",
    "proboscis": "head surface beyond the plane PROB_R_K x median head radius out along the proboscis axis (head origin -> tip)",
    "proboscis_tip": "the outer PROB_TIP_LEN of the proboscis", "antennae": "the two decimated antenna rods"}

# cavity shade + deterministic per-face value jitter (improve_unit.py rule), cavity from the high sculpt
t = time.time()
HV_all, HF_all, o_ = [], [], 0
for nm in sorted(PARTS):
    V, F = PARTS[nm]
    HV_all.append(V); HF_all += [[i + o_ for i in f] for f in F]; o_ += len(V)
HV_all = np.vstack(HV_all)
HIGH = new_obj(UNIT + "_high", HV_all, HF_all)
kd_h = KDTree(len(HV_all))
for i, p in enumerate(HV_all):
    kd_h.insert(p, i)
kd_h.balance()
hme = HIGH.data
ev_h = np.empty(len(hme.edges) * 2, dtype=np.int64); hme.edges.foreach_get("vertices", ev_h); ev_h = ev_h.reshape(-1, 2)
nv_h = np.empty(len(hme.vertices) * 3); hme.vertex_normals.foreach_get("vector", nv_h); nv_h = nv_h.reshape(-1, 3)
deg = np.bincount(ev_h.ravel(), minlength=len(HV_all)).astype(float)


def nmean(X):
    s_ = np.zeros_like(X)
    for k in range(X.shape[1]):
        s_[:, k] = np.bincount(ev_h[:, 0], X[ev_h[:, 1], k], minlength=len(HV_all)) + np.bincount(ev_h[:, 1], X[ev_h[:, 0], k], minlength=len(HV_all))
    return s_ / np.maximum(deg, 1)[:, None]


el = np.linalg.norm(HV_all[ev_h[:, 0]] - HV_all[ev_h[:, 1]], axis=1).mean()
cav = ((nmean(HV_all) - HV_all) * nv_h).sum(1) / el
for _ in range(6):
    cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
cav = np.clip(cav / (np.percentile(np.abs(cav), 95) + 1e-9), -1, 1)
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
FN = np.empty(nf * 3); me.polygon_normals.foreach_get("vector", FN); FN = FN.reshape(-1, 3)
fcav = np.array([np.mean([cav[j] for (_, j, _) in kd_h.find_n(p, 8)]) for p in FC])
cav_k = 0.40
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["cavity_shade_k"] = cav_k
report["cavity_seconds"] = round(time.time() - t, 1)
fa = np.empty(nf); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == R_[n]].sum() / fa.sum()), 4) for n in REG}

# facing landmark (direction-free): the head primitive's origin (the sculpt's head-ball centre) -> the proboscis tip
# (the head part's vertex farthest from that origin). Evaluated on the SOURCE data before the yaw fix and after it.
# The proboscis is TWO fang prongs (visible in the front render): the landmark is the centroid of the head-part vertices
# within PROB_TIP_LEN of the farthest distance, so both prong tips average (one tip vertex alone reads ~10 deg off).
hv_final = PARTS["Sphere"][0]
tip_m = hr > hr.max() - PROB_TIP_LEN
anchor = head_origin.copy(); landmark = hv_final[tip_m].mean(0)
dvec = landmark - anchor
src_d = RY.T @ dvec
report["facing"] = {"rule": "head primitive origin (the sculpt's head-ball centre) -> centroid of the proboscis prong tips "
                            "(head-part vertices within PROB_TIP_LEN of the farthest from that origin)",
                    "tip_vertices": int(tip_m.sum()),
                    "prong_tips_x": [round(float(hv_final[tip_m, 0].min()), 3), round(float(hv_final[tip_m, 0].max()), 3)],
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "source_angle_from_minusY_deg": round(math.degrees(math.atan2(src_d[0], -src_d[1])), 2),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2),
                    "proboscis_droop_deg": round(math.degrees(math.atan2(-dvec[2], math.hypot(dvec[0], dvec[1]))), 2),
                    "eyes_centroid_rel_head": (np.vstack([PARTS["Sphere.003"][0], PARTS["Sphere.004"][0]]).mean(0) - head_origin).round(4).tolist()}

# =========================================================================== flat + UV
me.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

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
low["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "crowd"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = YAW_FIX_DEG
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = os.path.basename(bpy.data.filepath)
low["conquest_scale_policy"] = "natural proportions, sculpt units; game scales at import (cell fit report-only)"
low["conquest_locomotion"] = "hover (flyer): rest pose on the floor per contract; both clips lift the body HOVER_FRAC x height"

if PREVIEW:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("tris_final", "retopo", "regions_faces", "regions_area_share", "facing",
                                                             "iso_cuts", "head_probe", "natural", "source_floor_gap")}))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake (normal + AO from the sculpt parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
scene.cycles.seed = 0
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
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
assert (lt_ == 3).all(), "low must be all triangles"
UVn = UVn.reshape(-1, 3, 2)


def texels_of(mask_faces, res):
    m = np.zeros((res, res), bool)
    for tri in UVn[mask_faces]:
        p = tri * res
        x0_, y0_ = np.floor(p.min(0)).astype(int); x1_, y1_ = np.ceil(p.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0_, 0), min(x1_, res)), np.arange(max(y0_, 0), min(y1_, res)))
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


tb = time.time()
for o in scene.objects:
    o.select_set(o is HIGH or o is low)
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
    nt.nodes.active = node
    scene.cycles.samples = samples
    t = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
me.shade_flat()
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
bstats.update({
    "uv_texels_normal": int(alltex_n.sum()), "uv_coverage": round(float(alltex_n.mean()), 4),
    "normal_baked_pct_of_uv_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(devn[cov_n & alltex_n].mean()), 4),
    "ao_baked_pct_of_uv_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
per_region = {}
for n_ in REG:
    fm_ = rid == R_[n_]
    if fm_.any():
        tx = texels_of(fm_, RN)
        per_region[n_] = {"texels": int(tx.sum()), "baked_pct": round(100 * float(cov_n[tx].mean()), 2),
                          "normal_dev_mean": round(float(devn[tx & cov_n].mean()), 4) if (tx & cov_n).any() else None}
bstats["per_region_normal"] = per_region
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


# hash the 8-bit quantization (the shipped artifact is the 8-bit PNG), and dump the float buffer for the runner's
# bake tolerance gate: the NORMAL bake is not byte-deterministic on this mesh - a per-process Cycles tie-break on the
# thin membrane flips a couple of texels by one 8-bit step (measured 2026-09-25: 2 of 1,048,576 texels, max delta
# 0.0039, across 13 probe runs; AO and every geometry digest were always identical). vampito_run.ps1 compares the two
# builds' buffers against limits pinned from that measurement instead of demanding an impossible exact hash.
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
import tempfile  # noqa: E402
np.save(os.path.join(tempfile.gettempdir(), "vampito_normal_twin.npy" if DIGEST_ONLY else "vampito_normal_main.npy"),
        px[:, :3])
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["high_tris"] = report["tris_source"]
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(vc.outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
hm_ = HIGH.data
bpy.data.objects.remove(HIGH, do_unlink=True); bpy.data.meshes.remove(hm_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True

uva = 0.5 * ((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 2, 0] - UVn[:, 0, 0]) * (UVn[:, 1, 1] - UVn[:, 0, 1]))
report["uv"] = {"method": "Smart UV after the iso cuts (66 deg, margin 0.004), repacked", "faces": nf,
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()),
                "uv_sha": sha(UVn)}
LVf = np.array([v.co[:] for v in me.vertices])
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
DIG["geometry_colour"] = report["digest_geometry_colour"]; DIG["uv"] = report["uv"]["uv_sha"]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "no shipped colours exist (both source blends are material-less, no Conquest glb): authored"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig (hover archetype, local)
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
W_ = LVf.copy()
reg_v = np.zeros(len(W_), dtype=np.int32)                     # per-vertex region (any face's; used for landmarks only)
for fi, poly in enumerate(me.polygons):
    for vi in poly.vertices:
        reg_v[vi] = rid[fi]
Hh = float(W_[:, 2].max())
body_m = reg_v == R_["body"]
P_BODY = np.array([0.0, float(W_[body_m, 1].mean()), float(W_[body_m, 2].mean())])
abd_m = reg_v == R_["abdomen"]
ABD_TOP = np.array([0.0, float(W_[abd_m & (W_[:, 2] > ABD_Z - 0.6), 1].mean()), ABD_Z + ABD_JOINT_UP])
ABD_TIP = W_[abd_m][np.argmin(W_[abd_m, 2])].copy(); ABD_TIP[0] = 0.0
NECK = np.array([0.0, head_origin[1], head_origin[2] - float(np.median(hr))])   # bottom of the head ball
arm_m = np.isin(reg_v, [R_["body"], R_["wing_arm"], R_["talon"]]) & (W_[:, 2] > max(ABD_Z, TALON_Z))
half_span = float(np.abs(W_[:, 0]).max())
X_MID = WING_MID_FRAC * half_span


def arm_point(xabs, s):
    m = arm_m & (np.abs(np.abs(W_[:, 0]) - xabs) < 0.3) & (np.sign(W_[:, 0]) == s)
    assert m.any(), "no arm surface at |x| %.2f" % xabs
    q = W_[m].mean(0)
    return np.array([s * xabs, q[1], q[2]])


BONES = [("body", P_BODY, P_BODY + np.array([0, 0, 0.12 * Hh]), "root"),
         ("abdomen", ABD_TOP, ABD_TIP, "body"),
         ("head", NECK, head_origin, "body")]
for s, sg in (("L", 1.0), ("R", -1.0)):
    sh_ = arm_point(WING_X0 + 0.3, sg); mid_ = arm_point(X_MID, sg); tip_ = arm_point(half_span - 0.5, sg)
    BONES += [("wing." + s, sh_, mid_, "body"), ("wing_tip." + s, mid_, tip_, "wing." + s)]
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.06 * Hh); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_); e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = nm.startswith("wing_tip")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- analytic weights (<= 4 influences)
x, y, z = W_[:, 0], W_[:, 1], W_[:, 2]
ax_ = np.abs(x)
headness = smoothstep(-NECK_BAND, NECK_BAND, np.maximum(VF["fh"], VF["fe"]))       # head + eyes (+ antennae = 1)
collarness = smoothstep(-COLLAR_BAND, COLLAR_BAND, VF["fc"])
wingness = smoothstep(WING_X0, WING_X1, ax_) * (1 - collarness) * (1 - headness)
tipness = smoothstep(X_MID - TIP_BAND, X_MID + TIP_BAND, ax_)
abdness = smoothstep(ABD_Z + ABD_JOINT_UP + ABD_BAND, ABD_Z + ABD_JOINT_UP - ABD_BAND, z) * (1 - wingness) * (1 - headness)
Wt = np.zeros((len(W_), len(DEFORM)))
Wt[:, J["head"]] = headness
Wt[:, J["abdomen"]] = abdness
L_ = x > 0
for m_, s in ((L_, "L"), (~L_, "R")):
    Wt[m_, J["wing." + s]] = (wingness * (1 - tipness))[m_]
    Wt[m_, J["wing_tip." + s]] = (wingness * tipness)[m_]
Wt[:, J["body"]] = np.clip(1 - Wt.sum(1), 0, None)
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx = np.argsort(-Wt, 1)[:, 4:]
    np.put_along_axis(Wt, idx, 0.0, 1)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "rule": "head = head/eyes part ownership blended +-NECK_BAND across its boundary (antennae 1.0); wings = "
                          "|x| over WING_X0..WING_X1 (never the collar or head), wing -> wing_tip over X_MID +- TIP_BAND; "
                          "abdomen below the waist joint +- ABD_BAND; body = the rest"}
rep["wings_separable"] = ("no: the wing ARMS are fused into the main sculpt shell (Sphere.001 carries thorax + abdomen + both arms "
                          "and hands); the membranes are separate sculpt parts but interpenetrate the arms and fuse in the remesh. "
                          "The wings still flap: they are lateral appendages joined only at the shoulder, so a 2-bone chain per "
                          "side skins them cleanly (shoulder band WING_X0..WING_X1).")
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig

# ---- clips: closed-form periodic curves, keyed every frame (quaternions + locations), linear between keys
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AX = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0))}


def local_quat(bone, rots):
    """World-axis rotations (applied in order, about the bone head, relative to the parent) -> the bone's basis quaternion."""
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, AX[axis]) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    return q


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def pose_at(clip, f):
    """Pose of frame f (0-based within the period) -> {bone: (loc_world, [(axis, deg), ...])}."""
    if clip == "idle":
        N, beats, flap, mean, tipd, bob, pitch0 = IDLE_N, IDLE_BEATS, IDLE_FLAP_DEG, IDLE_FLAP_MEAN, IDLE_TIP_DEG, IDLE_BOB_FRAC, IDLE_PITCH_DEG
    else:
        N, beats, flap, mean, tipd, bob, pitch0 = WALK_N, WALK_BEATS, WALK_FLAP_DEG, WALK_FLAP_MEAN, WALK_TIP_DEG, WALK_BOB_FRAC, WALK_PITCH_DEG
    u = f / N                                  # 0..1 over the loop
    pb_ = 2 * math.pi * beats * u              # wingbeat phase: wings UP at 0, downstroke 0..pi
    wing = mean + flap * math.cos(pb_)
    tip = tipd * math.cos(pb_ - 2 * math.pi * TIP_LAG)
    lift = HOVER_FRAC * Hh - bob * Hh * math.cos(pb_)          # lowest with the wings up, rises through the downstroke
    if clip == "idle":
        pitch = pitch0 + IDLE_SWAY_DEG * math.sin(2 * math.pi * u)
        head = -pitch0 + IDLE_HEAD_DEG * math.sin(2 * math.pi * 2 * u)
        abd = IDLE_ABD_DEG * math.sin(pb_ - 0.5 * math.pi)
    else:
        pitch = pitch0 + WALK_PITCH_OSC * math.sin(pb_)
        head = -WALK_HEAD_COUNTER * pitch0 + 1.5 * math.sin(pb_ - 0.25 * math.pi)
        abd = WALK_ABD_TRAIL_DEG + WALK_ABD_DEG * math.sin(pb_ - 0.5 * math.pi)
    P = {"body": ((0.0, 0.0, lift), [("X", pitch)]),
         "abdomen": ((0, 0, 0), [("X", abd)]),
         "head": ((0, 0, 0), [("X", head)]),
         "wing.L": ((0, 0, 0), [("Y", -wing)]), "wing.R": ((0, 0, 0), [("Y", wing)]),
         "wing_tip.L": ((0, 0, 0), [("Y", -tip)]), "wing_tip.R": ((0, 0, 0), [("Y", tip)])}
    return P, {"wing_deg": wing, "tip_deg": tip, "lift": lift, "pitch_deg": pitch}


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
NEW_ACTS = {}
clip_rep = {}
key_rows = []
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1 (integer cycles -> exact seam)
        P, _ = pose_at(cn, f % N)
        for bn, (dw, rots) in P.items():
            pb = rig.pose.bones[bn]
            pb.location = local_loc(bn, dw)
            pb.rotation_quaternion = local_quat(bn, rots)
            pb.keyframe_insert("location", frame=f + 1)
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["wingbeats"] = IDLE_BEATS if cn == "idle" else WALK_BEATS     # the contact sheet samples one beat
    NEW_ACTS[cn] = act
DIG["keys"] = sha(np.array(key_rows))


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


# wing hand landmark: the outermost talon vertex per side (the stroke amplitude is measured there)
tal_L = int(np.argmax(np.where(L_, ax_, -1)))
tal_R = int(np.argmax(np.where(~L_, ax_, -1)))
samples = []
for cn, N in CLIPS.items():
    act = NEW_ACTS[cn]
    K.assign_action(rig, act)
    first = last = None
    minz, root_off, tipz = 1e9, 0.0, []
    lifts = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        minz = min(minz, float(C[:, 2].min()))
        root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        tipz.append([C[tal_L, 2], C[tal_R, 2]])
        lifts.append(float(C[:, 2].min()))
    tipz = np.array(tipz)
    seam = float(np.linalg.norm(first - last, axis=1).max())
    _, info0 = pose_at(cn, 0)
    beats = IDLE_BEATS if cn == "idle" else WALK_BEATS
    f_hz = beats * K.FPS / N
    A = float((tipz[:, 0].max() - tipz[:, 0].min()))
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "wingbeat_hz": round(f_hz, 3), "seam_units": round(seam, 8),
                    "clearance_min_z": round(minz, 4), "clearance_min_z_pct_H": round(100 * minz / Hh, 2),
                    "lowest_point_range": [round(min(lifts), 4), round(max(lifts), 4)],
                    "root_offset_max": round(root_off, 8),
                    "hand_tip_stroke_peak_to_peak": round(A, 4), "hand_tip_stroke_LR_diff": round(float(np.abs((tipz[:, 0] - tipz[:, 1])).max()), 6),
                    "pitch_deg_mean": IDLE_PITCH_DEG if cn == "idle" else WALK_PITCH_DEG}
    if cn == "walk":
        U = {str(st): round(f_hz * A / st, 3) for st in STROUHAL}
        clip_rep[cn]["implied_forward_speed"] = {
            "rule": "Strouhal: U = f A / St (f = wingbeat Hz, A = hand-tip vertical stroke peak-to-peak); cruising flyers St 0.2-0.4",
            "units_per_s_by_St": U,
            "m_per_s_at_cell_fit_by_St": {k: round(v * GAME_M_PER_UNIT, 3) for k, v in U.items()},
            "body_lengths_per_s_at_St_0.3": round(f_hz * A / 0.3 / float(W_[:, 1].max() - W_[:, 1].min()), 3),
            "game_m_per_unit_report_only": round(GAME_M_PER_UNIT, 5)}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["hover"] = {"hover_height": round(HOVER_FRAC * Hh, 4), "hover_frac_H": HOVER_FRAC, "units": "sculpt units",
                "hover_height_m_at_cell_fit": round(HOVER_FRAC * Hh * GAME_M_PER_UNIT, 4),
                "source_sculpt_floor_gap": report["source_floor_gap"],
                "rule": "rest pose on the floor (contract feet_origin); both clips key body.location z = HOVER_FRAC x H "
                        "-/+ the wingbeat bob; the contract root never moves"}
rep["clips"] = clip_rep
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "vampito v1: hover rig -- root (contract) > body > abdomen, head, wing.L/R > wing_tip.L/R"
low["conquest_clips"] = list(CLIPS)
low["conquest_clip_status"] = "idle = hover, walk = hover-forward (in place); no attack/hit/death (artist: idle + locomotion only)"
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
for a in list(bpy.data.actions):
    if a.name not in CLIPS:
        bpy.data.actions.remove(a)
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
if DIGEST_ONLY:
    json.dump({"digest": rep["digest"], "tris_final": report["tris_final"], "seconds": round(time.time() - T0, 1)}, open(DIGEST_ONLY, "w"), indent=1)
    print("DIGEST", DIG_ALL, json.dumps(DIG))
    sys.stdout.flush(); os._exit(0)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 7. identity-scale glb
for o in scene.objects:
    o.select_set(o is rig or o is low)
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, NEW_ACTS["idle"])
t = time.time()
import export_glb as _EG; _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False, export_attributes=True)
rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "structure": "armature object identity (no scale), mesh child identity, natural scale; game model_scale "
                           "%.5f reaches the regular cell (report-only)" % k_fit}
rig.animation_data.action = None
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "hover", "digest", "seconds")}))
sys.stdout.flush()
os._exit(0)
